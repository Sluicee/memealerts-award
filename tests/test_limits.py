import unittest
from unittest.mock import Mock

from memeaward.api import Twitch
from memeaward.rewards import normalize_limits, twitch_limits


class LimitTests(unittest.TestCase):
    def test_disabled_limits_send_flags_without_invalid_zero_counts(self):
        self.assertEqual(twitch_limits({}), {
            "is_global_cooldown_enabled": False,
            "is_max_per_user_per_stream_enabled": False,
            "is_max_per_stream_enabled": False})

    def test_create_sends_all_limits(self):
        twitch = Twitch("client", "token")
        twitch.call = Mock(return_value={"data": [{"id": "reward"}]})
        self.assertEqual(twitch.create_reward("Title", 1000, {
            "cooldown_seconds": 60, "max_per_user_per_stream": 3, "max_per_stream": 50}), "reward")
        body = twitch.call.call_args.args[2]
        self.assertTrue(body["is_global_cooldown_enabled"])
        self.assertTrue(body["is_max_per_user_per_stream_enabled"])
        self.assertTrue(body["is_max_per_stream_enabled"])
        self.assertEqual(body["global_cooldown_seconds"], 60)
        self.assertEqual(body["max_per_user_per_stream"], 3)
        self.assertEqual(body["max_per_stream"], 50)
        self.assertFalse(body["is_enabled"])

    def test_edit_can_enable_some_limits_and_disable_others(self):
        twitch = Twitch("client", "token")
        twitch.call = Mock()
        twitch.update_reward("reward", "Title", 1000, {"max_per_user_per_stream": 2})
        body = twitch.call.call_args.args[2]
        self.assertTrue(body["is_max_per_user_per_stream_enabled"])
        self.assertEqual(body["max_per_user_per_stream"], 2)
        self.assertFalse(body["is_global_cooldown_enabled"])
        self.assertFalse(body["is_max_per_stream_enabled"])
        self.assertNotIn("global_cooldown_seconds", body)

    def test_short_and_maximum_cooldown_are_allowed(self):
        for value in (1, 59, 60, 604800):
            self.assertEqual(normalize_limits({"cooldown_seconds": value})["cooldown_seconds"], value)

    def test_invalid_configuration_is_rejected(self):
        for limits in ([], "60", {"max_per_stream": -1}, {"max_per_user_per_stream": True},
                       {"max_per_stream": 1000001}, {"cooldown_seconds": 604801}):
            with self.subTest(limits=limits), self.assertRaises(ValueError):
                normalize_limits(limits)

    def test_invalid_adapter_input_does_not_call_twitch(self):
        twitch = Twitch("client", "token")
        twitch.call = Mock()
        with self.assertRaises(ValueError):
            twitch.update_reward("reward", "Title", 1000, {"cooldown_seconds": -1})
        twitch.call.assert_not_called()
