from .api import NotSupporter


class UncertainCredit(Exception):
    pass


class Engine:
    def __init__(self, store, twitch, meme, reward_id, amount):
        self.store, self.twitch, self.meme = store, twitch, meme
        self.reward_id, self.amount = reward_id, amount

    def process(self, redemption):
        rid = redemption["id"]
        row = self.store.row(rid)
        if row is None:
            nickname = redemption.get("user_input", "").strip()
            self.store.record(rid, self.reward_id, nickname, self.amount, "pending")
            row = self.store.row(rid)
        state = row["state"]
        if state in ("done", "canceled"):
            return
        if state in ("sending", "uncertain"):
            raise UncertainCredit("Есть начисление с неизвестным результатом. Проверьте журнал.")
        if state == "pending":
            try:
                supporter_id = self.meme.find_supporter(row["nickname"])
            except NotSupporter as exc:
                self.store.queue_notification(rid, redemption.get("user_login", ""))
                self.store.update(rid, "cancel_pending", str(exc))
            else:
                self.store.update(rid, "sending")
                try:
                    self.meme.credit(supporter_id, row["amount"])
                except Exception:
                    self.store.update(rid, "uncertain", "Ответ на начисление не подтверждён. Проверьте баланс на MemeAlerts вручную.")
                    raise UncertainCredit("Неизвестен результат начисления. Сбор остановлен; нужна ручная проверка.") from None
                self.store.update(rid, "credited", "Коины начислены; подтверждаем Twitch.")
        row = self.store.row(rid)
        if row["state"] == "credited":
            # Failure here is safe to retry: the credit itself is already journaled.
            self.twitch.finish(self.reward_id, rid, "FULFILLED")
            self.store.update(rid, "done", "Коины начислены")
        elif row["state"] == "cancel_pending":
            self.twitch.finish(self.reward_id, rid, "CANCELED")
            self.store.update(rid, "canceled", row["note"])
            self.deliver_notifications()

    def deliver_notifications(self):
        for notification in self.store.ready_notifications(self.reward_id):
            rid = notification["redemption_id"]
            # Persist the attempt first: an unknown response must not cause repeated chat messages.
            self.store.notification_state(rid, "sending")
            try:
                self.twitch.notify_not_supporter(notification["login"])
            except Exception:
                self.store.notification_state(rid, "failed")
                note = " Сообщение в чат не подтверждено; автоматического повтора не будет."
            else:
                self.store.notification_state(rid, "sent")
                note = " Сообщение о саппортерстве отправлено в чат Twitch."
            self.store.update(rid, "canceled", notification["note"] + note)

    def capture_queue(self):
        """Snapshot every queued amount before processing or changing the reward's rate."""
        redemptions = list(self.twitch.redemptions(self.reward_id))
        for redemption in redemptions:
            self.store.record(redemption["id"], self.reward_id,
                              redemption.get("user_input", "").strip(), self.amount, "pending")
        return redemptions

    def tick(self, should_stop=lambda: False):
        # Snapshot before mutations so pagination cannot skip records when the queue shrinks.
        redemptions = self.capture_queue()
        for redemption in redemptions:
            if should_stop():
                break
            self.process(redemption)
        if not should_stop():
            self.deliver_notifications()
