from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

from PySide6.QtCore import Qt, QPoint, QPointF
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication, QLineEdit

from memeaward.desktop import Window, Worker, STYLE


class UIInteractions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)
        cls.app.setStyleSheet(STYLE)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.worker = Worker(Path(self.tmp.name))
        self.window = Window(self.worker)
        self.window.show()
        self.state = dict(active=False, connected=True, reward_id="reward", status="Сбор выключен.",
                          settings=dict(title="Мемкоины", cost=1000, amount=10), rows=[],
                          identities=dict(twitch="streamer", memealerts="meme_streamer"))
        self.window.update_state(self.state)
        self.app.processEvents()

    def tearDown(self):
        self.window.quitting = True
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()
        self.tmp.cleanup()

    def rows(self):
        now = datetime.now(timezone.utc).isoformat()
        return [dict(id="ok", nickname="котик", state="done", amount=10, note="Коины начислены", updated=now),
                dict(id="bad", nickname="viewer", state="uncertain", amount=10,
                     note="Нужно проверить баланс <вручную>", updated=now)]

    def test_dirty_settings_can_be_reset_without_changing_saved_summary(self):
        window = self.window
        window.change_page(1)
        window.amount.setValue(50)
        self.assertFalse(window.toggle.isEnabled())
        self.assertTrue(window.reset_btn.isEnabled())
        self.assertEqual(window.coin_value.text(), "10")
        window.update_state(self.state)
        self.assertEqual(window.amount.value(), 50)
        window.reset_btn.click()
        self.assertEqual(window.amount.value(), 10)
        self.assertTrue(window.toggle.isEnabled())
        self.assertFalse(window.create_btn.isEnabled())

    def test_search_filter_selection_and_details_survive_refresh(self):
        window = self.window
        self.state["rows"] = self.rows()
        window.update_state(self.state)
        self.assertEqual(window.table.rowCount(), 2)
        self.assertFalse(window.toggle.isEnabled())
        window.search.setText("КОТИК")
        self.assertEqual(window.table.rowCount(), 1)
        self.assertEqual(window.table.item(0, 1).text(), "котик")
        window.search.clear()
        window.log_filter.setCurrentIndex(window.log_filter.findData("uncertain"))
        self.assertEqual(window.table.rowCount(), 1)
        window.table.selectRow(0)
        self.assertEqual(window.selected_id(), "bad")
        self.assertTrue(window.resolve_yes.isEnabled())
        self.assertIn("<вручную>", window.detail_text.text())
        self.assertEqual(window.detail_text.textFormat(), Qt.TextFormat.PlainText)
        window.update_state(self.state)
        self.assertEqual(window.selected_id(), "bad")
        window.search.setText("нет такого ника")
        self.assertEqual(window.table.rowCount(), 0)
        self.assertFalse(window.resolve_yes.isEnabled())

    def test_busy_controls_prevent_duplicate_network_commands(self):
        window = self.window
        window.send_command("start")
        window.send_command("start")
        self.assertEqual(self.worker.commands.qsize(), 1)
        self.assertFalse(window.toggle.isEnabled())
        self.assertFalse(window.amount.isEnabled())
        self.assertFalse(window.accounts.isEnabled())
        self.worker.operation_finished.emit()
        self.assertTrue(window.toggle.isEnabled())

    def test_tokens_are_hidden_on_navigation_and_connections_have_identity(self):
        window = self.window
        window.change_page(2)
        window.ma_token.setText("private-token")
        window.show_token.setChecked(True)
        self.assertEqual(window.ma_token.echoMode(), QLineEdit.EchoMode.Normal)
        window.change_page(0)
        self.assertEqual(window.ma_token.echoMode(), QLineEdit.EchoMode.Password)
        self.assertEqual(window.twitch_status.text(), "streamer")
        self.assertEqual(window.meme_status.text(), "meme_streamer")

    def test_warning_remains_visible_after_background_status_updates(self):
        window = self.window
        window.notify("Не удалось сохранить токен")
        window.update_state(self.state)
        self.assertEqual(window.status.text(), "Не удалось сохранить токен")
        window.clear_warning()
        self.assertEqual(window.status.text(), "Сбор выключен.")

    def test_scrolling_unfocused_price_does_not_change_it(self):
        window = self.window
        window.change_page(1)
        window.reward_title.setFocus()
        self.app.processEvents()
        self.assertFalse(window.cost.hasFocus())
        value = window.cost.value()
        event = QWheelEvent(QPointF(5, 5), QPointF(5, 5), QPoint(), QPoint(0, 120),
                            Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                            Qt.ScrollPhase.NoScrollPhase, False)
        QApplication.sendEvent(window.cost, event)
        self.assertEqual(window.cost.value(), value)

    def test_small_window_keeps_navigation_and_collection_control_visible(self):
        window = self.window
        window.resize(880, 600)
        for page in range(3):
            window.change_page(page)
            self.app.processEvents()
            self.assertTrue(window.toggle.isVisible())
            self.assertEqual(window.pages.currentIndex(), page)
            self.assertLessEqual(window.width(), 880)
            self.assertLessEqual(window.height(), 600)


if __name__ == "__main__":
    unittest.main()
