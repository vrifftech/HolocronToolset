"""The main window opens Module Designer directly with its normal module picker."""
import ast
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QMainWindow
from toolset.gui.windows import module_designer
from toolset.uic.windows.main import Ui_MainWindow


class ModuleDesignerLaunchTests(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_main_window_opens_standalone_designer(self):
        installation = SimpleNamespace(name="Test installation")
        # Exercise this callback without importing unrelated multimedia editors.
        source = Path(__file__).resolve().parents[2] / "src/toolset/gui/windows/main.py"
        window_class = next(node for node in ast.parse(source.read_text()).body
                            if isinstance(node, ast.ClassDef) and node.name == "ToolWindow")
        callback = next(node for node in window_class.body
                        if isinstance(node, ast.FunctionDef) and node.name == "openModuleDesigner")
        designer, add_window = Mock(), Mock()
        namespace = {"ModuleDesigner": designer, "addWindow": add_window}
        exec(compile(ast.Module(body=[callback], type_ignores=[]), str(source), "exec"), namespace)
        namespace["openModuleDesigner"](SimpleNamespace(active=installation))
        designer.assert_called_once_with(None, installation)
        add_window.assert_called_once_with(designer.return_value)

    def test_designer_opens_its_module_picker_without_a_workspace_wrapper(self):
        with patch.object(module_designer.ModuleDesigner, "openModule") as open_module:
            window = module_designer.ModuleDesigner(None, SimpleNamespace(name="Test installation"))
            try:
                QTest.qWait(60)
                open_module.assert_called_once_with()
                self.assertIn("Module Designer", window.windowTitle())
                self.assertTrue(window.ui.mainRenderer is not None)
                self.assertTrue(window.ui.flatRenderer is not None)
            finally:
                window.close()

    def test_main_tools_menu_retains_designer_without_builder(self):
        window = QMainWindow()
        ui = Ui_MainWindow()
        try:
            ui.setupUi(window)
            self.assertIn(ui.actionModuleDesigner, ui.menuTools.actions())
            self.assertFalse(hasattr(ui, "actionIndoorMapBuilder"))
        finally:
            if hasattr(ui, "texturesWidget"):
                ui.texturesWidget.doTerminations()
            window.close()
