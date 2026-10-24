import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from memeaward.api import ApiError, MemeAlerts, NotSupporter, Twitch
from memeaward.engine import Engine, UncertainCredit
from memeaward.storage import Store


class FakeMeme:
    def __init__(self):
        self.credits = []
        self.missing = self.fail = False

    def find_supporter(self, nickname):
        if self.missing:
            raise NotSupporter("Саппортер не найден")
        return "supporter-id"

    def credit(self, supporter_id, amount):
        self.credits.append((supporter_id, amount))
        if self.fail:
            raise ApiError("MemeAlerts")


class FakeTwitch:
    def __init__(self):
        self.finished = []
        self.messages = []
        self.message_fail = False
        self.fail = False

    def finish(self, reward_id, redemption_id, status):
        if self.fail:
            raise ApiError("Twitch")
        self.finished.append((reward_id, redemption_id, status))

    def notify_not_supporter(self, login):
        self.messages.append(login)
        if self.message_fail:
            raise ApiError("Twitch")


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "test.sqlite3"
        self.store = Store(self.path)
        self.tw, self.ma = FakeTwitch(), FakeMeme()
        self.engine = Engine(self.store, self.tw, self.ma, "reward", 10)
        self.redemption = {"id": "redemption", "user_input": " viewer ", "user_login": "twitch_viewer"}

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_duplicate_does_not_credit_twice(self):
        self.engine.process(self.redemption)
        self.engine.process(self.redemption)
        self.assertEqual(self.ma.credits, [("supporter-id", 10)])
        self.assertEqual(self.store.row("redemption")["nickname"], "viewer")
        self.assertEqual(self.store.row("redemption")["state"], "done")

    def test_twitch_failure_after_credit_is_safe_to_retry_after_restart(self):
        self.tw.fail = True
        with self.assertRaises(ApiError):
            self.engine.process(self.redemption)
        self.assertEqual(self.store.row("redemption")["state"], "credited")
        self.store.close()
        self.store = Store(self.path)
        self.engine.store = self.store
        self.tw.fail = False
        self.engine.process(self.redemption)
        self.assertEqual(len(self.ma.credits), 1)
        self.assertEqual(self.tw.finished[-1][-1], "FULFILLED")

    def test_uncertain_credit_never_automatically_retries(self):
        self.ma.fail = True
        with self.assertRaises(UncertainCredit):
            self.engine.process(self.redemption)
        with self.assertRaises(UncertainCredit):
            self.engine.process(self.redemption)
        self.assertEqual(len(self.ma.credits), 1)
        self.assertEqual(self.tw.finished, [])
        self.assertEqual(self.store.row("redemption")["state"], "uncertain")

    def test_crash_in_sending_becomes_uncertain(self):
        self.store.record("redemption", "reward", "viewer", 10, "sending")
        self.store.close()
        self.store = Store(self.path)
        self.assertEqual(self.store.row("redemption")["state"], "uncertain")

    def test_missing_supporter_refunds_points(self):
        self.ma.missing = True
        self.engine.process(self.redemption)
        self.assertEqual(self.ma.credits, [])
        self.assertEqual(self.tw.finished[-1][-1], "CANCELED")
        self.assertEqual(self.tw.messages, ["twitch_viewer"])
        self.assertIn("отправлено", self.store.row("redemption")["note"])

    def test_notification_failure_does_not_prevent_refund_or_retry_credit(self):
        self.ma.missing, self.tw.message_fail = True, True
        self.engine.process(self.redemption)
        self.engine.process(self.redemption)
        self.engine.deliver_notifications()
        self.assertEqual(self.ma.credits, [])
        self.assertEqual(self.store.row("redemption")["state"], "canceled")
        self.assertEqual(self.tw.messages, ["twitch_viewer"])
        self.assertIn("не подтверждено", self.store.row("redemption")["note"])

    def test_crash_after_refund_before_chat_is_recovered(self):
        self.store.record("redemption", "reward", "viewer", 10, "canceled")
        self.store.queue_notification("redemption", "twitch_viewer")
        self.tw.redemptions = lambda _: iter([])
        self.store.close()
        self.store = Store(self.path)
        self.engine.store = self.store
        self.engine.tick()
        self.engine.tick()
        self.assertEqual(self.tw.messages, ["twitch_viewer"])

    def test_crash_during_chat_does_not_duplicate_message(self):
        self.store.record("redemption", "reward", "viewer", 10, "canceled")
        self.store.queue_notification("redemption", "twitch_viewer")
        self.store.notification_state("redemption", "sending")
        self.store.close()
        self.store = Store(self.path)
        self.engine.store = self.store
        self.engine.deliver_notifications()
        self.assertEqual(self.tw.messages, [])
        self.assertIn("неизвестна", self.store.row("redemption")["note"])

    def test_refund_failure_retries_without_credit(self):
        self.ma.missing, self.tw.fail = True, True
        with self.assertRaises(ApiError):
            self.engine.process(self.redemption)
        self.assertEqual(self.tw.messages, [])
        self.ma.missing, self.tw.fail = False, False
        self.engine.process(self.redemption)
        self.assertEqual(self.ma.credits, [])
        self.assertEqual(self.tw.finished[-1][-1], "CANCELED")
        self.assertEqual(self.tw.messages, ["twitch_viewer"])

    def test_amount_is_snapshotted_before_processing(self):
        self.store.record("redemption", "reward", "viewer", 7, "pending")
        self.engine.process(self.redemption)
        self.assertEqual(self.ma.credits, [("supporter-id", 7)])

    def test_stop_interrupts_between_redemptions(self):
        self.tw.redemptions = lambda _: iter([self.redemption])
        self.engine.tick(lambda: True)
        self.assertEqual(self.ma.credits, [])

    def test_unknown_operation_remains_visible_beyond_recent_200(self):
        self.store.record("old-unknown", "reward", "viewer", 10, "uncertain")
        for index in range(201):
            self.store.record(str(index), "reward", "viewer", 10, "done")
        rows = self.store.recent()
        self.assertIn("old-unknown", [row["id"] for row in rows])
        self.assertEqual(len(rows), 201)


class AdapterTests(unittest.TestCase):
    @patch("memeaward.api.request")
    def test_old_token_requests_new_authorization(self, request):
        request.return_value = {"client_id": "client", "scopes": ["channel:manage:redemptions"]}
        with self.assertRaisesRegex(ValueError, "заново"):
            Twitch("client", "token").validate()

    @patch("memeaward.api.request")
    def test_chat_message_targets_channel_and_real_twitch_login(self, request):
        request.return_value = {"data": [{"is_sent": True}]}
        twitch = Twitch("client", "token")
        twitch.user_id = "streamer"
        twitch.notify_not_supporter("twitch_viewer")
        args = request.call_args.args
        self.assertEqual(args[1], "https://api.twitch.tv/helix/chat/messages")
        self.assertEqual(args[3]["sender_id"], "streamer")
        self.assertEqual(args[3]["broadcaster_id"], "streamer")
        self.assertTrue(args[3]["message"].startswith("@twitch_viewer,"))
        self.assertIn("https://memealerts.com/", args[3]["message"])
        self.assertLessEqual(len(args[3]["message"]), 500)

    @patch("memeaward.api.request")
    def test_dropped_chat_message_is_not_success(self, request):
        request.return_value = {"data": [{"is_sent": False, "drop_reason": {"code": "automod"}}]}
        with self.assertRaises(ValueError):
            Twitch("client", "token").notify_not_supporter("viewer")

    @patch("memeaward.api.request")
    def test_invalid_mention_does_not_send(self, request):
        with self.assertRaises(ValueError):
            Twitch("client", "token").notify_not_supporter("viewer @someone")
        request.assert_not_called()

    def test_update_reward_preserves_required_nickname_and_queue(self):
        twitch = Twitch("client", "token")
        from unittest.mock import Mock
        twitch.call = Mock()
        twitch.update_reward("reward", "Новое название", 1200)
        twitch.call.assert_called_once_with("custom_rewards", "PATCH", {
            "title": "Новое название", "cost": 1200, "is_user_input_required": True,
            "should_redemptions_skip_request_queue": False}, id="reward")

    def test_supporter_search_requires_exact_name(self):
        meme = MemeAlerts("secret")
        meme.user_id = "streamer"
        meme.call = lambda *args: {"data": [{"supporterId": "wrong", "supporterName": "Viewer123"},
                                           {"supporterId": "correct", "supporterName": "Viewer"}]}
        self.assertEqual(meme.find_supporter(" viewer "), "correct")
        with self.assertRaises(NotSupporter):
            meme.find_supporter("view")

    def test_duplicate_names_are_ambiguous(self):
        meme = MemeAlerts("secret")
        meme.call = lambda *args: {"data": [{"supporterId": "a", "supporterName": "viewer"},
                                           {"supporterId": "b", "supporterName": "viewer"}]}
        with self.assertRaises(NotSupporter):
            meme.find_supporter("viewer")

    def test_supporter_search_pages(self):
        meme = MemeAlerts("secret")
        def call(path, method, body):
            if not body["skip"]:
                return {"data": [{"supporterId": str(i), "supporterName": str(i)} for i in range(100)], "total": 101}
            return {"data": [{"supporterId": "target", "supporterName": "viewer"}], "total": 101}
        meme.call = call
        self.assertEqual(meme.find_supporter("viewer"), "target")

    def test_redemption_pagination_is_fully_read_before_mutating(self):
        twitch = Twitch("id", "token")
        calls = []
        def call(path, **query):
            calls.append(query)
            if "after" not in query:
                return {"data": [{"id": "a"}], "pagination": {"cursor": "next"}}
            return {"data": [{"id": "b"}], "pagination": {}}
        twitch.call = call
        self.assertEqual(list(twitch.redemptions("reward")), [{"id": "a"}, {"id": "b"}])
        self.assertEqual(calls[1]["after"], "next")


if __name__ == "__main__":
    unittest.main()
