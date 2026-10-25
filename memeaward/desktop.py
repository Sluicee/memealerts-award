import os
import json
import queue
import sys
import time
from pathlib import Path

from PySide6.QtCore import QThread, Signal, QStandardPaths, QLockFile
from PySide6.QtWidgets import QApplication, QMessageBox

from .api import ApiError, MemeAlerts, Twitch
from .engine import Engine
from .storage import Store
from .rewards import normalize_limits
from .ui import Window, STYLE

SERVICE = "memealerts-award"
TWITCH_CLIENT_ID = "7ezpvpkkgksgf0zvysmb2hc4a03ji4"


class Worker(QThread):
    snapshot = Signal(dict)
    notice = Signal(str)
    open_url = Signal(str)
    operation_finished = Signal()

    def __init__(self, path):
        super().__init__()
        self.path = path
        self.commands = queue.Queue()
        self.active = False
        self.exiting = False
        self.twitch = self.meme = None
        self.reward_id = ""
        self.device = None
        self.twitch_session = {}
        self.remember = False
        self.next_tick = self.validated = 0
        self.status = "Подключите Twitch и MemeAlerts"
        self.identities = {}

    def submit(self, name, data=None):
        self.commands.put((name, data))

    def emit_state(self):
        self.snapshot.emit({"active": self.active, "connected": bool(self.twitch and self.meme),
                            "reward_id": self.reward_id, "status": self.status,
                            "settings": self.store.get("settings", {}),
                            "identities": dict(self.identities),
                            "rows": [dict(r) for r in self.store.recent()]})

    def save_token(self, name, token):
        try:
            import keyring
            keyring.set_password(SERVICE, name, token)
        except Exception:
            self.notice.emit("Хранилище ключей ОС недоступно. Токен действует до выхода и не сохранён на диск.")

    def update_twitch_session(self, token, refresh_token):
        self.twitch_session = {"access_token": token, "refresh_token": refresh_token}
        if self.remember:
            self.save_token("twitch_session", json.dumps(self.twitch_session))
        self.snapshot.emit({"twitch_token": token})

    def clear_tokens(self):
        try:
            import keyring
            for name in ("twitch", "twitch_session", "memealerts"):
                if keyring.get_password(SERVICE, name):
                    keyring.delete_password(SERVICE, name)
        except Exception:
            self.notice.emit("Не удалось удалить токены из хранилища ОС. Удалите записи memealerts-award вручную.")

    def pause(self):
        self.active = False
        if self.twitch and self.reward_id:
            self.twitch.enable(self.reward_id, False)

    @staticmethod
    def reward_settings(data):
        title = data.get("title", "").strip()
        if not 1 <= len(title) <= 45:
            raise ValueError("Название награды должно содержать от 1 до 45 символов.")
        for key, maximum in (("cost", 1_000_000_000), ("amount", 1_000_000)):
            if type(data.get(key)) is not int or not 1 <= data[key] <= maximum:
                raise ValueError("Цена и количество мемкоинов должны быть положительными целыми числами в пределах полей настройки.")
        return {"title": title, "cost": data["cost"], "amount": data["amount"],
                "limits": normalize_limits(data.get("limits"))}

    def connect_accounts(self, data):
        if self.active:
            raise ValueError("Сначала остановите сбор.")
        if self.twitch and self.reward_id:
            self.pause()
        self.twitch = self.meme = None
        self.identities = {}
        self.reward_id = ""
        client = TWITCH_CLIENT_ID
        token = data["twitch_token"].strip().removeprefix("oauth:")
        meme_token = data["meme_token"].strip().removeprefix("Bearer ")
        if not all((client, token, meme_token)):
            raise ValueError("Нужны токены обоих сервисов.")
        self.remember = bool(data.get("remember"))
        refresh_token = (self.twitch_session.get("refresh_token", "")
                         if self.twitch_session.get("access_token") == token else "")
        twitch = Twitch(client, token, refresh_token, self.update_twitch_session)
        meme = MemeAlerts(meme_token)
        login = twitch.validate()
        reward_key = f"reward:{client}:{twitch.user_id}"
        reward_id = self.store.get(reward_key, "")
        if reward_id:
            twitch.enable(reward_id, False)
        name = meme.validate()
        owner = self.store.get(reward_key + ":meme_owner")
        if owner and owner != meme.user_id:
            raise ValueError("Эта награда привязана к другому аккаунту MemeAlerts. Подключите исходный аккаунт.")
        self.twitch, self.meme = twitch, meme
        self.identities = {"twitch": login, "memealerts": name}
        self.reward_key, self.reward_id = reward_key, reward_id
        self.store.set("settings", self.store.get(reward_key + ":settings", {}))
        self.validated = time.monotonic()
        if data.get("remember"):
            self.update_twitch_session(twitch.token, twitch.refresh_token)
            self.save_token("memealerts", meme_token)
        else:
            self.clear_tokens()
        self.status = f"Twitch: {login}   /   MemeAlerts: {name}"

    def handle(self, command, data):
        if command == "connect":
            self.connect_accounts(data)
        elif command == "device":
            if self.active:
                raise ValueError("Сначала остановите сбор.")
            client = TWITCH_CLIENT_ID
            self.device = Twitch.device_start(client)
            self.device.update(client_id=client, deadline=time.monotonic() + self.device["expires_in"],
                               next_poll=time.monotonic() + self.device["interval"])
            self.open_url.emit(self.device["verification_uri"])
            self.status = "Подтвердите Twitch в браузере. Код: " + self.device["user_code"]
        elif command == "create":
            if self.active:
                raise ValueError("Сначала остановите сбор.")
            if not self.twitch or not self.meme:
                raise ValueError("Сначала подключите оба аккаунта.")
            if self.reward_id:
                raise ValueError("Награда уже создана этим приложением.")
            data = self.reward_settings(data)
            # Save immediately after Twitch responds; never create a new reward on each launch.
            self.reward_id = self.twitch.create_reward(data["title"], data["cost"], limits=data["limits"])
            self.store.set(self.reward_key, self.reward_id)
            self.store.set(self.reward_key + ":meme_owner", self.meme.user_id)
            self.store.set("settings", data)
            self.store.set(self.reward_key + ":settings", data)
            self.status = "Награда создана и выключена. Можно запустить сбор."
        elif command == "edit":
            if self.active:
                raise ValueError("Остановите сбор перед редактированием награды.")
            if not self.twitch or not self.meme or not self.reward_id:
                raise ValueError("Подключите аккаунты и создайте награду.")
            data = self.reward_settings(data)
            self.pause()
            old_amount = self.store.get("settings", {}).get("amount", 10)
            Engine(self.store, self.twitch, self.meme, self.reward_id, old_amount).capture_queue()
            self.twitch.update_reward(self.reward_id, data["title"], data["cost"], limits=data["limits"])
            self.store.set(self.reward_key + ":settings", data)
            self.store.set("settings", data)
            self.status = "Изменения сохранены. Старые заявки сохраняют прежнее количество мемкоинов."
        elif command == "start":
            if not self.twitch or not self.meme or not self.reward_id:
                raise ValueError("Подключите аккаунты и создайте награду.")
            if self.store.uncertain():
                raise ValueError("Сначала разберите неизвестные начисления в журнале.")
            self.twitch.validate()
            self.meme.validate()
            self.validated = time.monotonic()
            self.active = True
            try:
                # Drain old requests while the reward is disabled, then enable new claims.
                self.engine = Engine(self.store, self.twitch, self.meme, self.reward_id,
                                     self.store.get("settings", {}).get("amount", 10))
                self.engine.tick(lambda: not self.commands.empty())
                self.twitch.enable(self.reward_id, True)
                self.next_tick = 0
                self.status = "Сбор включён. Награда доступна зрителям."
            except Exception:
                self.active = False
                raise
        elif command == "stop":
            self.pause()
            self.status = "Сбор остановлен. Награда выключена."
        elif command == "resolve":
            rid, credited = data
            row = self.store.row(rid)
            if not row or row["state"] != "uncertain":
                raise ValueError("Выберите начисление с неизвестным результатом.")
            if not self.twitch or row["reward_id"] != self.reward_id:
                raise ValueError("Подключите Twitch-аккаунт, которому принадлежит эта награда.")
            state = "credited" if credited else "cancel_pending"
            self.store.update(rid, state, "Результат проверен пользователем вручную.")
            Engine(self.store, self.twitch, self.meme, self.reward_id, row["amount"]).process({"id": rid})
            self.status = "Результат ручной проверки сохранён."
        elif command == "exit":
            self.device = None
            try:
                self.pause()
            except Exception:
                self.notice.emit("Не удалось выключить награду. Выключите её в панели Twitch вручную.")
            self.exiting = True

    def poll_device(self):
        device = self.device
        if not device or time.monotonic() < device["next_poll"]:
            return
        if time.monotonic() >= device["deadline"]:
            self.device = None
            raise ValueError("Код Twitch истёк. Запустите вход заново.")
        device["next_poll"] = time.monotonic() + device["interval"]
        try:
            result = Twitch.device_poll(device["client_id"], device["device_code"])
        except ApiError as exc:
            if exc.code == "authorization_pending":
                return
            if exc.code == "slow_down":
                device["interval"] += 5
                return
            self.device = None
            raise
        self.device = None
        self.twitch_session = {"access_token": result["access_token"],
                               "refresh_token": result.get("refresh_token", "")}
        self.snapshot.emit({"twitch_token": result["access_token"]})
        self.status = "Вход Twitch выполнен. Добавьте токен MemeAlerts и нажмите «Подключить аккаунты»."
        self.emit_state()

    def run(self):
        self.store = Store(self.path / "journal.sqlite3")
        initial = {}
        try:
            import keyring
            saved = keyring.get_password(SERVICE, "twitch_session")
            self.twitch_session = json.loads(saved) if saved else {}
            initial["twitch_token"] = (self.twitch_session.get("access_token")
                                       or keyring.get_password(SERVICE, "twitch") or "")
            initial["meme_token"] = keyring.get_password(SERVICE, "memealerts") or ""
            initial["remember"] = bool(initial["twitch_token"] and initial["meme_token"])
        except Exception:
            pass
        self.snapshot.emit(initial)
        self.emit_state()
        while not self.exiting:
            try:
                try:
                    command, data = self.commands.get(timeout=0.5)
                except queue.Empty:
                    command = None
                if command:
                    try:
                        self.handle(command, data)
                        self.emit_state()
                    finally:
                        self.operation_finished.emit()
                self.poll_device()
                if self.active and time.monotonic() >= self.next_tick:
                    if time.monotonic() - self.validated >= 3600:
                        self.twitch.validate()
                        self.meme.validate()
                        self.validated = time.monotonic()
                    self.engine.tick(lambda: not self.commands.empty())
                    self.next_tick = time.monotonic() + 5
                    self.emit_state()
            except Exception as exc:
                self.active = False
                message = str(exc) if isinstance(exc, (ApiError, ValueError)) else "Операция не завершена. Проверьте журнал и подключение."
                from .engine import UncertainCredit
                if isinstance(exc, UncertainCredit):
                    message = str(exc)
                try:
                    self.pause()
                except Exception:
                    message += " Не удалось выключить награду: выключите её вручную в Twitch."
                self.status = message
                self.notice.emit(message)
                self.emit_state()
        self.store.close()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("MemeAlerts Award")
    app.setOrganizationName("MemeAlertsAward")
    app.setQuitOnLastWindowClosed(False)
    app.setStyleSheet(STYLE)
    path = Path(os.environ.get("MEMEAWARD_DATA_DIR") or QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation))
    path.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(path / "app.lock"))
    lock.setStaleLockTime(0)
    if not lock.tryLock(100):
        QMessageBox.information(None, "MemeAlerts Award", "Приложение уже запущено. Откройте его из трея.")
        return
    worker = Worker(path)
    window = Window(worker)
    window.show()
    worker.start()
    app.exec()
    worker.wait()
    lock.unlock()


if __name__ == "__main__":
    main()
