import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

try:
    from memeaward.desktop import Worker
except ImportError:
    Worker = None

from memeaward.storage import Store
from memeaward.rewards import normalize_limits


@unittest.skipIf(Worker is None, "Install project dependencies for desktop tests")
class DesktopTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.worker = Worker(Path(self.tmp.name))
        self.worker.store = Store(Path(self.tmp.name) / "test.sqlite3")
        self.worker.twitch = Mock()
        self.worker.twitch.redemptions.return_value = iter([])
        self.worker.meme = Mock()
        self.worker.meme.user_id = "meme-streamer"
        self.worker.reward_id = "reward"
        self.worker.reward_key = "reward:client:streamer"
        self.worker.store.set("settings", {"title": "Old", "cost": 1000, "amount": 10})

    def tearDown(self):
        self.worker.store.close()
        self.tmp.cleanup()

    def test_stop_disables_remote_reward(self):
        self.worker.active = True
        self.worker.handle("stop", None)
        self.assertFalse(self.worker.active)
        self.worker.twitch.enable.assert_called_once_with("reward", False)

    def test_start_validates_then_enables_reward(self):
        self.worker.handle("start", None)
        self.assertTrue(self.worker.active)
        self.worker.twitch.validate.assert_called_once()
        self.worker.meme.validate.assert_called_once()
        self.worker.twitch.enable.assert_called_once_with("reward", True)

    def test_unknown_operation_blocks_start(self):
        self.worker.store.record("id", "reward", "viewer", 10, "uncertain")
        with self.assertRaises(ValueError):
            self.worker.handle("start", None)
        self.assertFalse(self.worker.active)
        self.worker.twitch.enable.assert_not_called()

    def test_exit_disables_reward_even_when_not_collecting(self):
        self.worker.handle("exit", None)
        self.assertTrue(self.worker.exiting)
        self.worker.twitch.enable.assert_called_once_with("reward", False)

    def test_edit_preserves_old_amount_for_queued_requests(self):
        self.worker.twitch.redemptions.return_value = iter([{"id": "old", "user_input": "viewer"}])
        settings = {"title": "  New  ", "cost": 2000, "amount": 20}
        self.worker.handle("edit", settings)
        self.assertEqual(self.worker.store.row("old")["amount"], 10)
        self.assertEqual(self.worker.store.get("settings"), {"title": "New", "cost": 2000, "amount": 20,
                                                             "limits": normalize_limits()})
        self.assertEqual(self.worker.store.get(self.worker.reward_key + ":settings")["amount"], 20)
        self.worker.twitch.update_reward.assert_called_once_with("reward", "New", 2000, limits=normalize_limits())
        self.worker.twitch.enable.assert_called_once_with("reward", False)
        self.worker.meme.credit.assert_not_called()

    def test_edit_while_running_is_rejected(self):
        self.worker.active = True
        with self.assertRaises(ValueError):
            self.worker.handle("edit", {"title": "New", "cost": 2000, "amount": 20})
        self.worker.twitch.update_reward.assert_not_called()

    def test_failed_remote_edit_does_not_change_saved_rate(self):
        self.worker.twitch.update_reward.side_effect = ValueError("failed")
        with self.assertRaises(ValueError):
            self.worker.handle("edit", {"title": "New", "cost": 2000, "amount": 20})
        self.assertEqual(self.worker.store.get("settings")["amount"], 10)

    def test_invalid_reward_fields_are_rejected_before_network_calls(self):
        for changes in ({"title": " "}, {"cost": 0}, {"amount": -1}, {"amount": True}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.worker.handle("edit", {"title": "New", "cost": 2000, "amount": 20} | changes)
        self.worker.twitch.update_reward.assert_not_called()

    def test_limits_are_saved_and_passed_to_twitch(self):
        limits = {"cooldown_seconds": 60, "max_per_user_per_stream": 3, "max_per_stream": 50}
        self.worker.handle("edit", {"title": "Old", "cost": 1000, "amount": 10, "limits": limits})
        self.worker.twitch.update_reward.assert_called_once_with("reward", "Old", 1000, limits=limits)
        self.assertEqual(self.worker.store.get(self.worker.reward_key + ":settings")["limits"], limits)

    def test_invalid_limits_do_not_disable_or_modify_reward(self):
        for value in (-1, 604801, True, 1.5, "60"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.worker.handle("edit", {"title": "Old", "cost": 1000, "amount": 10,
                                            "limits": {"cooldown_seconds": value}})
        self.worker.twitch.enable.assert_not_called()
        self.worker.twitch.update_reward.assert_not_called()

    def test_creation_includes_limits(self):
        self.worker.reward_id = ""
        self.worker.twitch.create_reward.return_value = "new"
        limits = {"cooldown_seconds": 120, "max_per_user_per_stream": 2, "max_per_stream": 0}
        self.worker.handle("create", {"title": "New", "cost": 1000, "amount": 10, "limits": limits})
        self.worker.twitch.create_reward.assert_called_once_with("New", 1000, limits=limits)
        self.assertEqual(self.worker.store.get("settings")["limits"], limits)

    def test_window_and_worker_exit_without_hanging(self):
        code = '''
import tempfile
from pathlib import Path
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from memeaward.desktop import Worker, Window
app = QApplication([])
app.setQuitOnLastWindowClosed(False)
with tempfile.TemporaryDirectory() as directory:
    worker = Worker(Path(directory))
    window = Window(worker)
    window.show()
    state = {"active": False, "connected": True, "reward_id": "reward", "status": "Ready",
             "settings": {"title": "Reward", "cost": 1000, "amount": 10}, "rows": []}
    window.update_state(state)
    assert window.amount.isEnabled() and window.toggle.isEnabled()
    assert window.create_btn.text() == "Сохранить изменения"
    window.amount.setValue(20)
    assert not window.toggle.isEnabled() and not window.tray_toggle.isEnabled()
    window.update_state(state)
    assert window.amount.value() == 20  # Background updates must preserve unsaved edits.
    state["settings"] = {"title": "Reward", "cost": 1000, "amount": 20}
    window.update_state(state)
    assert window.toggle.isEnabled()
    window.limit_fields["cooldown_seconds"].setValue(60)
    assert not window.toggle.isEnabled()
    window.update_state(state)
    assert window.limit_fields["cooldown_seconds"].value() == 60
    state["settings"]["limits"] = {"cooldown_seconds": 60, "max_per_user_per_stream": 3, "max_per_stream": 50}
    window.update_state(state)
    assert window.limit_fields["max_per_user_per_stream"].value() == 3
    assert window.limit_fields["max_per_stream"].value() == 50
    assert window.toggle.isEnabled()
    state["active"] = True
    window.update_state(state)
    assert not window.amount.isEnabled() and not window.create_btn.isEnabled()
    assert not window.limit_fields["cooldown_seconds"].isEnabled()
    worker.start()
    QTimer.singleShot(200, window.quit)
    app.exec()
    assert worker.wait(1000)
'''
        env = dict(os.environ, QT_QPA_PLATFORM="offscreen", PYTHON_KEYRING_BACKEND="keyring.backends.null.Keyring")
        result = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
