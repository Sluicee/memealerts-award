"""Run UI interaction tests in an isolated headless Qt process."""
import importlib.util
import os
import subprocess
import sys
import unittest


@unittest.skipIf(importlib.util.find_spec("PySide6") is None, "Install desktop dependencies")
class UIIntegrationTests(unittest.TestCase):
    def test_desktop_interactions(self):
        env = dict(os.environ, QT_QPA_PLATFORM="offscreen", PYTHON_KEYRING_BACKEND="keyring.backends.null.Keyring")
        result = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "ui_cases.py", "-v"],
                                env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
