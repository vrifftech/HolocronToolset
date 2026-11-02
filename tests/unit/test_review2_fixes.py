"""Regressions for save routing, streamed search, GFF values, and integer editing."""
import struct
from pathlib import Path
from tempfile import TemporaryDirectory
from types import MethodType, SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

from PyQt5.QtCore import QPoint
from PyQt5.QtGui import QTextDocument
from PyQt5.QtTest import QSignalSpy, QTest
from PyQt5.QtWidgets import QApplication, QWidget
from pykotor.resource.formats.gff import GFFFieldType
from pykotor.resource.type import ResourceType
from toolset.gui import editor as editor_module
from toolset.gui.dialogs.search import FileResults, FileSearcher
from toolset.gui.editors import gff, nss
from toolset.gui.editors.erf import ERFEditor
from toolset.gui.widgets.long_spinbox import LongSpinBox


class SaveRedirectTests(TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        self.events = []
        self.editor = SimpleNamespace(
            _filepath=self.folder / "templates.bif", _resname="example", _restype=ResourceType.TwoDA,
            _revert=b"original", _writeSupported=[ResourceType.TwoDA, ResourceType.NSS],
            _installation=SimpleNamespace(override_path=lambda: self.folder),
            _global_settings=SimpleNamespace(disableRIMSaving=True), refreshWindowTitle=Mock(),
            menuBar=lambda: SimpleNamespace(actions=lambda: [SimpleNamespace(menu=lambda: SimpleNamespace(actions=lambda: []))]),
            _saveEndsWithOther=Mock(return_value=True), _saveEndsWithRim=Mock(return_value=True),
            _saveEndsWithErf=Mock(return_value=True),
        )
        def build():
            self.events.append(("build", self.editor._filepath, self.editor._restype))
            return b"updated", b""
        self.editor.build = Mock(side_effect=build)
        for name in ("save", "_resolveSaveDestination"):
            setattr(self.editor, name, MethodType(getattr(editor_module.Editor, name), self.editor))

    def dialog(self, option):
        return SimpleNamespace(option=option, exec_=lambda: self.events.append("choose destination"))

    def test_override_redirects_choose_destination_before_one_build(self):
        for suffix, name, options in ((".bif", "BifSaveDialog", editor_module.BifSaveOption),
                                      (".rim", "RimSaveDialog", editor_module.RimSaveOption)):
            with self.subTest(suffix=suffix):
                self.editor._filepath = self.folder / ("templates" + suffix)
                self.editor.build.reset_mock()
                self.events.clear()
                with patch.object(editor_module, name, return_value=self.dialog(options.Override)):
                    self.assertTrue(self.editor.save())
                self.editor.build.assert_called_once()
                self.assertEqual(self.events, ["choose destination", ("build", self.folder / "example.2da", ResourceType.TwoDA)])

    def test_module_redirect_builds_selected_resource_type(self):
        target = self.folder / "chosen.mod"
        resource = SimpleNamespace(exec_=lambda: True, resname=lambda: "renamed", restype=lambda: ResourceType.NSS)
        with patch.object(editor_module, "BifSaveDialog", return_value=self.dialog(editor_module.BifSaveOption.MOD)), \
             patch.object(editor_module.QFileDialog, "getSaveFileName", return_value=(str(target), "")), \
             patch.object(editor_module, "SaveToModuleDialog", return_value=resource):
            self.assertTrue(self.editor.save())
        self.assertEqual(self.events[-1], ("build", target, ResourceType.NSS))
        self.editor.build.assert_called_once()
        self.assertEqual(self.editor._resname, "renamed")
        self.editor._saveEndsWithErf.assert_called_once_with(b"updated", b"")

    def test_cancelled_redirects_do_not_build(self):
        for suffix, name, options in ((".bif", "BifSaveDialog", editor_module.BifSaveOption),
                                      (".rim", "RimSaveDialog", editor_module.RimSaveOption)):
            original = self.folder / ("templates" + suffix)
            self.editor._filepath = original
            with patch.object(editor_module, name, return_value=self.dialog(options.Nothing)):
                self.assertFalse(self.editor.save())
            self.assertEqual(self.editor._filepath, original)
        self.editor.build.assert_not_called()

    def test_cancelled_module_details_do_not_build(self):
        with patch.object(editor_module, "BifSaveDialog", return_value=self.dialog(editor_module.BifSaveOption.MOD)), \
             patch.object(editor_module.QFileDialog, "getSaveFileName", return_value=(str(self.folder / "chosen.mod"), "")), \
             patch.object(editor_module, "SaveToModuleDialog", return_value=SimpleNamespace(exec_=lambda: False)):
            self.assertFalse(self.editor.save())
        self.editor.build.assert_not_called()
        self.assertEqual(self.editor._filepath.name, "templates.bif")

    def test_disabled_rim_saving_redirects_to_mod_before_build(self):
        self.editor._filepath = self.folder / "module_s.rim"
        with patch.object(editor_module, "RimSaveDialog", return_value=self.dialog(editor_module.RimSaveOption.MOD)), \
             patch.object(editor_module.Module, "find_root", return_value="module"):
            self.assertTrue(self.editor.save())
        self.assertEqual(self.events[-1], ("build", self.folder / "module.mod", ResourceType.TwoDA))
        self.editor.build.assert_called_once()
        self.editor._saveEndsWithErf.assert_called_once()

    def test_enabled_rim_saving_and_whole_archive_editing_keep_destination(self):
        self.editor._filepath = self.folder / "module.rim"
        self.editor._global_settings.disableRIMSaving = False
        with patch.object(editor_module, "RimSaveDialog") as dialog:
            self.assertTrue(self.editor.save())
        dialog.assert_not_called()
        self.editor._saveEndsWithRim.assert_called_once()
        self.editor._global_settings.disableRIMSaving = True
        self.assertTrue(ERFEditor._resolveSaveDestination(self.editor))
        self.assertEqual(self.editor._filepath.name, "module.rim")


class StreamedSearchTests(TestCase):
    def run_search(self, installation, *, accepted=True, filenames_only=True, selected=None):
        searcher = SimpleNamespace(results=["stale"])
        def make_loader(parent, title, task, error, **kwargs):
            installation.chitin_resources.assert_not_called()
            loader = SimpleNamespace(value=None)
            def run():
                loader.value = task(lambda *args: None)
                return accepted
            loader.exec_ = run
            return loader
        with patch("toolset.gui.dialogs.search.AsyncLoader", side_effect=make_loader):
            succeeded = FileSearcher.search(searcher, installation, False, filenames_only, "match", True, False, False,
                                            [ResourceType.NSS] if selected is None else selected)
        return succeeded, searcher.results

    def test_enumeration_is_deferred_to_worker_and_types_are_filtered(self):
        script = SimpleNamespace(resname=lambda: "MATCH", restype=lambda: ResourceType.NSS)
        other = SimpleNamespace(resname=lambda: "match", restype=lambda: ResourceType.UTI, data=Mock())
        installation = SimpleNamespace(chitin_resources=Mock(return_value=iter([script, other])))
        self.assertEqual(self.run_search(installation), (True, [script]))
        other.data.assert_not_called()

    def test_content_matching_and_cancelled_results(self):
        resource = SimpleNamespace(resname=lambda: "unrelated", restype=lambda: ResourceType.NSS,
                                   data=lambda: b"Text with MATCH inside")
        for accepted in (True, False):
            installation = SimpleNamespace(chitin_resources=Mock(return_value=iter([resource])))
            self.assertEqual(self.run_search(installation, accepted=accepted, filenames_only=False),
                             (accepted, [resource] if accepted else []))

    def test_no_selected_types_does_not_enumerate(self):
        installation = SimpleNamespace(chitin_resources=Mock(side_effect=AssertionError("unnecessary enumeration")))
        self.assertEqual(self.run_search(installation, selected=[]), (True, []))


class GFFConversionTests(TestCase):
    def setUp(self):
        self.item = Mock()
        self.values = {gff._TYPE_NODE_ROLE: GFFFieldType.UInt16, gff._VALUE_NODE_ROLE: 1000,
                       gff._RAW_NODE_ROLE: b"retained", gff._ORIGINAL_NODE_ROLE: "original"}
        self.item.data.side_effect = self.values.get
        self.item.setData.side_effect = lambda value, role: self.values.__setitem__(role, value)
        self.item.rowCount.return_value = 0
        self.editor = SimpleNamespace(
            ui=SimpleNamespace(treeView=SimpleNamespace(selectedIndexes=lambda: [0])),
            proxyModel=SimpleNamespace(mapToSource=lambda index: index),
            model=SimpleNamespace(itemFromIndex=lambda index: self.item), loadItem=Mock(), refreshItemText=Mock(),
        )
        self.editor._convertedValue = MethodType(gff.GFFEditor._convertedValue, self.editor)

    def test_rejected_narrowing_preserves_the_complete_field(self):
        original = self.values.copy()
        with patch.object(gff, "QMessageBox") as message:
            gff.GFFEditor.typeChanged(self.editor, GFFFieldType.UInt8.value)
        message.warning.assert_called_once()
        self.assertEqual(self.values, original)
        self.item.removeRows.assert_not_called()
        self.editor.loadItem.assert_called_once_with(self.item)

    def test_accepted_type_and_value_commit_together(self):
        gff.GFFEditor.typeChanged(self.editor, GFFFieldType.UInt32.value)
        self.assertEqual(self.values[gff._TYPE_NODE_ROLE], GFFFieldType.UInt32)
        self.assertEqual(self.values[gff._VALUE_NODE_ROLE], 1000)
        self.assertIsNone(self.values[gff._RAW_NODE_ROLE])
        self.assertIsNone(self.values[gff._ORIGINAL_NODE_ROLE])

    def test_numeric_boundaries_and_float_conversion(self):
        convert = self.editor._convertedValue
        for kind, value in ((GFFFieldType.UInt8, -1), (GFFFieldType.Int8, 128),
                            (GFFFieldType.Int32, 1.5), (GFFFieldType.Int64, float("nan")),
                            (GFFFieldType.Single, 1e100)):
            with self.subTest(kind=kind, value=value), self.assertRaises(ValueError):
                convert(kind, value)
        self.assertEqual(convert(GFFFieldType.UInt64, 2**64 - 1), 2**64 - 1)
        self.assertEqual(convert(GFFFieldType.Int64, -2**63), -2**63)
        self.assertEqual(convert(GFFFieldType.Single, 0.1), struct.unpack("<f", struct.pack("<f", 0.1))[0])

    def test_container_values_match_tree_representation(self):
        self.assertEqual(self.editor._convertedValue(GFFFieldType.Struct, 1000), 0)
        self.assertIsNone(self.editor._convertedValue(GFFFieldType.List, 1000))

    def test_declined_child_removal_preserves_field(self):
        self.item.rowCount.return_value = 2
        original = self.values.copy()
        with patch.object(gff.QMessageBox, "question", return_value=gff.QMessageBox.No):
            gff.GFFEditor.typeChanged(self.editor, GFFFieldType.Struct.value)
        self.assertEqual(self.values, original)
        self.item.removeRows.assert_not_called()

    def test_confirmed_child_removal_creates_valid_struct_id(self):
        self.item.rowCount.return_value = 2
        with patch.object(gff.QMessageBox, "question", return_value=gff.QMessageBox.Yes):
            gff.GFFEditor.typeChanged(self.editor, GFFFieldType.Struct.value)
        self.item.removeRows.assert_called_once_with(0, 2)
        self.assertEqual(self.values[gff._VALUE_NODE_ROLE], 0)


class QtWidgetRegressionTests(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.owns_app = QApplication.instance() is None
        cls.app = QApplication.instance() or QApplication([])

    @classmethod
    def tearDownClass(cls):
        if cls.owns_app:
            cls.app.quit()
            cls.app.deleteLater()
        cls.app = None

    def test_search_dialogs_use_the_supplied_parent(self):
        parent = QWidget()
        self.addCleanup(parent.close)
        for dialog in (FileSearcher(parent, {}), FileResults(parent, [], None)):
            self.addCleanup(dialog.close)
            self.assertIs(dialog.parent(), parent)

    def test_integer_notifications_only_publish_committed_values(self):
        spin = LongSpinBox(None)
        self.addCleanup(spin.close)
        spin.setRange(0, 255)
        spin.setValue(12)
        spy = QSignalSpy(spin.valueChanged)
        spin.lineEdit().selectAll()
        QTest.keyClicks(spin.lineEdit(), "999")
        self.assertTrue(all(0 <= event[0] <= 255 for event in spy))
        spin.editingFinished.emit()
        self.assertEqual(spin.value(), 255)
        self.assertEqual(spin.text(), "255")
        self.assertEqual(spy[-1][0], 255)

    def test_incomplete_integer_text_retains_value_and_steps_stay_bounded(self):
        spin = LongSpinBox(None)
        self.addCleanup(spin.close)
        spin.setRange(-128, 127)
        spin.setValue(-12)
        spin.lineEdit().selectAll()
        QTest.keyClicks(spin.lineEdit(), "-")
        spin.editingFinished.emit()
        self.assertEqual(spin.value(), -12)
        spin.setRange(0, 2**64 - 1)
        spin.setValue(2**64 - 1)
        spin.stepUp()
        self.assertEqual(spin.value(), 2**64 - 1)
        self.assertFalse(spin.stepEnabled() & spin.StepUpEnabled)
        spin.setRange(0, 1)
        self.assertEqual(spin.value(), 1)

    def test_gff_context_menu_defaults_build_and_list_structs_can_be_added(self):
        editor = gff.GFFEditor(None)
        self.addCleanup(editor.close)
        editor._restype = ResourceType.GFF
        root = editor.model.item(0, 0)
        menu = gff.QMenu(editor)
        editor._build_context_menu_gff_struct(menu, root)
        for action in menu.actions():
            if not action.isSeparator():
                action.trigger()
        data, _ = editor.build()
        self.assertTrue(data)
        list_item = root.child(root.rowCount() - 1)
        list_menu = gff.QMenu(editor)
        index = editor.proxyModel.mapFromSource(list_item.index())
        with patch.object(editor.ui.treeView, "indexAt", return_value=index), \
             patch.object(gff, "QMenu", return_value=list_menu), patch.object(list_menu, "popup"):
            editor.requestContextMenu(QPoint(0, 0))
        next(action for action in list_menu.actions() if action.text() == "Add Struct").trigger()
        self.assertEqual(list_item.rowCount(), 1)
        self.assertEqual(list_item.child(0).data(gff._VALUE_NODE_ROLE), 0)
        data, _ = editor.build()
        self.assertTrue(data)

    def test_constant_highlighting_uses_the_selected_game(self):
        for tsl, table in ((False, "KOTOR_CONSTANTS"), (True, "TSL_CONSTANTS")):
            document = QTextDocument()
            with patch.object(nss, "KOTOR_CONSTANTS", [SimpleNamespace(name="K1_ONLY")]), \
                 patch.object(nss, "TSL_CONSTANTS", [SimpleNamespace(name="K2_ONLY")]):
                highlighter = nss.SyntaxHighlighter(document, SimpleNamespace(tsl=tsl))
            patterns = {expression.pattern() for expression, _, fmt in highlighter.rules if fmt == highlighter.styles["constant"]}
            self.assertEqual(patterns, {r"\bK2_ONLY\b" if table == "TSL_CONSTANTS" else r"\bK1_ONLY\b"})
