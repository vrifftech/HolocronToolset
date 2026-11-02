import os
import pathlib
import sys
import unittest
from unittest import TestCase

try:
    from PyQt5.QtTest import QTest
    from PyQt5.QtWidgets import QApplication
except (ImportError, ModuleNotFoundError):
    QTest, QApplication = None, None  # type: ignore[misc, assignment]


# Run these tests against the same separate backend as the application.
_FRONTEND_ROOT = next(parent for parent in pathlib.Path(__file__).resolve().parents if (parent / "src/toolset").is_dir())
sys.path.insert(0, str(_FRONTEND_ROOT / "src"))
from toolset.bootstrap import bootstrap_backend
bootstrap_backend()

K1_PATH = os.environ.get("K1_PATH")


@unittest.skipIf(
    not K1_PATH or not pathlib.Path(K1_PATH).joinpath("chitin.key").exists(),
    "K1_PATH environment variable is not set or not found on disk.",
)
@unittest.skipIf(
    QTest is None or not QApplication,
    "PyQt5 is required, please run pip install -r requirements.txt before running this test.",
)
class TXTEditorTest(TestCase):
    @classmethod
    def setUpClass(cls):
        # Make sure to configure this environment path before testing!
        from toolset.data.installation import HTInstallation
        cls.INSTALLATION = HTInstallation(K1_PATH, "", False, None)

    def setUp(self):
        from toolset.gui.editors.txt import TXTEditor
        self.app = QApplication([])
        self.ui = TXTEditor(None, self.INSTALLATION)

    def tearDown(self):
        self.app.deleteLater()

    def test_placeholder(self):
        ...


if __name__ == "__main__":
    unittest.main()
