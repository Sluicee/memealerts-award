"""Offline smoke check for native release bundles; never opens user data or a browser."""
import os
import tempfile
from pathlib import Path


def main():
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PySide6.QtWidgets import QApplication
    from .desktop import Worker, Window, STYLE
    from .storage import Store

    app = QApplication([])
    app.setStyleSheet(STYLE)
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory)
        store = Store(path / "smoke.sqlite3")
        store.set("smoke", True)
        if store.get("smoke") is not True:
            raise RuntimeError("SQLite smoke check failed")
        store.close()
        worker = Worker(path)
        window = Window(worker)
        window.show()
        app.processEvents()
        for page in range(3):
            window.change_page(page)
            app.processEvents()
        window.quitting = True
        window.close()
    print("MemeAlerts Award: native bundle smoke check passed", flush=True)
