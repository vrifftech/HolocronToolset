from __future__ import annotations

import os
import pathlib
import sys
import unittest
from unittest import TestCase

from pykotor.resource.formats.erf.erf_data import ERFType

try:
    from PyQt5.QtTest import QTest
    from PyQt5.QtWidgets import QApplication
except (ImportError, ModuleNotFoundError):
    QTest, QApplication = None, None  # type: ignore[misc, assignment]


TESTS_FILES_PATH = next(f for f in pathlib.Path(__file__).parents if f.name == "tests") / "files"

# Run these tests against the same separate backend as the application.
_FRONTEND_ROOT = next(parent for parent in pathlib.Path(__file__).resolve().parents if (parent / "src/toolset").is_dir())
sys.path.insert(0, str(_FRONTEND_ROOT / "src"))
from toolset.bootstrap import bootstrap_backend
bootstrap_backend()


K1_PATH = os.environ.get("K1_PATH")
K2_PATH = os.environ.get("K2_PATH")

from pykotor.common.stream import BinaryReader
from pykotor.extract.installation import Installation
from pykotor.resource.formats.erf.erf_auto import read_erf
from pykotor.resource.type import ResourceType


@unittest.skipIf(
    not K2_PATH or not pathlib.Path(K2_PATH).joinpath("chitin.key").exists(),
    "K2_PATH environment variable is not set or not found on disk.",
)
@unittest.skipIf(
    QTest is None or not QApplication,
    "PyQt5 is required, please run pip install -r requirements.txt before running this test.",
)
class ERFEditorTest(TestCase):
    @classmethod
    def setUpClass(cls):
        # Make sure to configure this environment path before testing!
        from toolset.gui.editors.erf import ERFEditor
        cls.ERFEditor = ERFEditor
        from toolset.data.installation import HTInstallation
        #cls.K1_INSTALLATION = HTInstallation(K1_PATH, "", tsl=False, mainWindow=None)
        cls.K2_INSTALLATION = HTInstallation(K2_PATH, "", tsl=True, mainWindow=None)

    def setUp(self):
        self.app = QApplication([])
        self.editor = self.ERFEditor(None, self.K2_INSTALLATION)
        self.log_messages: list[str] = [os.linesep]

    def tearDown(self):
        self.app.deleteLater()

    def log_func(self, *args):
        self.log_messages.append("\t".join(args))

    def test_save_and_load(self):
        filepath = TESTS_FILES_PATH / "001EBO_dlg.erf"

        data = BinaryReader.load_file(filepath)
        old = read_erf(data)
        self.editor.load(filepath, "001EBO_dlg", ResourceType.ERF, data)

        data, _ = self.editor.build()
        new = read_erf(data)

        self.assertDeepEqual(old, new)

    @unittest.skipIf(
        not K1_PATH or not pathlib.Path(K1_PATH).joinpath("chitin.key").exists(),
        "K1_PATH environment variable is not set or not found on disk.",
    )
    def test_gff_reconstruct_from_k1_installation(self):
        self.installation = Installation(K1_PATH)  # type: ignore[arg-type]
        for erf_resource in (resource for resource in self.installation if resource.restype() in {ERFType.__members__[erf_type] for erf_type in ERFType.__members__}):
            old = read_erf(erf_resource.data())
            self.editor.load(erf_resource.filepath(), erf_resource.resname(), erf_resource.restype(), erf_resource.data())

            data, _ = self.editor.build()
            new = read_erf(data)

            self.assertDeepEqual(old, new)

    @unittest.skipIf(
        not K2_PATH or not pathlib.Path(K2_PATH).joinpath("chitin.key").exists(),
        "K2_PATH environment variable is not set or not found on disk.",
    )
    def test_gff_reconstruct_from_k2_installation(self):
        self.installation = Installation(K2_PATH)  # type: ignore[arg-type]
        for erf_resource in (resource for resource in self.installation if resource.restype() in {ERFType.__members__[erf_type] for erf_type in ERFType.__members__}):
            old = read_erf(erf_resource.data())
            self.editor.load(erf_resource.filepath(), erf_resource.resname(), erf_resource.restype(), erf_resource.data())

            data, _ = self.editor.build()
            new = read_erf(data)

            self.assertDeepEqual(old, new)

    def assertDeepEqual(self, obj1, obj2, context=''):
        if isinstance(obj1, dict) and isinstance(obj2, dict):
            self.assertEqual(set(obj1.keys()), set(obj2.keys()), context)
            for key in obj1:
                new_context = f"{context}.{key}" if context else str(key)
                self.assertDeepEqual(obj1[key], obj2[key], new_context)

        elif isinstance(obj1, (list, tuple)) and isinstance(obj2, (list, tuple)):
            self.assertEqual(len(obj1), len(obj2), context)
            for index, (item1, item2) in enumerate(zip(obj1, obj2)):
                new_context = f"{context}[{index}]" if context else f"[{index}]"
                self.assertDeepEqual(item1, item2, new_context)

        elif hasattr(obj1, '__dict__') and hasattr(obj2, '__dict__'):
            self.assertDeepEqual(obj1.__dict__, obj2.__dict__, context)

        else:
            self.assertEqual(obj1, obj2, context)

    def test_placeholder(self):
        ...


if __name__ == "__main__":
    unittest.main()
