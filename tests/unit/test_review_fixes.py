"""Save/search/cache regressions and Qt editor update behavior; no game data required."""
from pathlib import Path
from tempfile import TemporaryDirectory
from types import MethodType, SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

from PyQt5.QtCore import QEventLoop
from PyQt5.QtGui import QTextCursor
from PyQt5.QtTest import QSignalSpy, QTest
from PyQt5.QtWidgets import QApplication
from pykotor.resource.type import ResourceType
from toolset.data.installation import HTInstallation
from toolset.gui.dialogs.search import FileSearcher
from toolset.gui.editor import Editor
from toolset.gui.editors import nss
from toolset.gui.editors.erf import ERFEditor


class SaveTests(TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)
        self.revert = Mock()
        self.revert.text.return_value = "Revert"
        menu = Mock()
        menu.actions.return_value = [self.revert]
        bar = Mock()
        bar.actions.return_value = [SimpleNamespace(menu=lambda: menu)]
        self.editor = SimpleNamespace(
            _filepath=self.folder / "old.2da", _resname="old", _restype=ResourceType.TwoDA,
            _revert=b"original", _installation=None, _saveFilter="2DA files",
            _writeSupported=[ResourceType.TwoDA], build=Mock(return_value=(b"updated", b"")),
            refreshWindowTitle=Mock(), savedFile=Mock(), menuBar=lambda: bar,
        )
        for name in ("save", "saveAs", "_saveEndsWithOther", "_resolveSaveDestination",
                     "_saveEndsWithRim", "_saveEndsWithErf", "_reloadSavedModule"):
            setattr(self.editor, name, MethodType(getattr(Editor, name), self.editor))

    def choose(self, path):
        selected = patch("toolset.gui.editor.QFileDialog.getSaveFileName", return_value=(str(path), ""))
        self.addCleanup(selected.stop)
        selected.start()

    def test_failed_save_as_keeps_identity_and_revert(self):
        self.choose(self.folder / "new.2da")
        self.editor._saveEndsWithOther = Mock(side_effect=OSError("disk full"))
        previous = self.editor._filepath, self.editor._resname, self.editor._restype, self.editor._revert
        with patch("toolset.gui.editor.QMessageBox"), patch("toolset.gui.editor.Path.open", side_effect=OSError("read-only")):
            self.assertFalse(self.editor.saveAs())
        self.assertEqual(previous, (self.editor._filepath, self.editor._resname, self.editor._restype, self.editor._revert))
        self.revert.setEnabled.assert_not_called()
        self.editor.savedFile.emit.assert_not_called()

    def test_cancelled_save_as_does_not_build(self):
        self.choose("")
        self.assertFalse(self.editor.saveAs())
        self.editor.build.assert_not_called()

    def test_successful_save_as_commits_new_identity(self):
        target = self.folder / "new.2da"
        self.choose(target)
        self.assertTrue(self.editor.saveAs())
        self.assertEqual(target.read_bytes(), b"updated")
        self.assertEqual(str(self.editor._filepath), str(target))
        self.assertEqual(self.editor._resname, "new")
        self.assertEqual(self.editor._revert, b"updated")
        self.revert.setEnabled.assert_called_with(True)
        self.editor.savedFile.emit.assert_called_once()

    def test_cancelled_build_preserves_document(self):
        self.editor.build.return_value = None, b""
        self.assertFalse(self.editor.save())
        self.assertEqual(self.editor._revert, b"original")
        self.editor.savedFile.emit.assert_not_called()

    def test_cancelled_redirect_restores_original_identity(self):
        self.editor._filepath = self.folder / "game.bif"
        previous = self.editor._filepath
        def cancel():
            self.editor._filepath = self.folder / "cancelled.mod"
            return False
        self.editor._resolveSaveDestination = cancel
        self.assertFalse(self.editor.save())
        self.assertEqual(self.editor._filepath, previous)


class SearchAndCacheTests(TestCase):
    def test_search_only_returns_selected_types(self):
        script = SimpleNamespace(resname=lambda: "match", restype=lambda: ResourceType.NSS)
        item = SimpleNamespace(resname=lambda: "match", restype=lambda: ResourceType.UTI)
        installation = SimpleNamespace(chitin_resources=lambda: [script, item])
        def worker(parent, title, task, error, **kwargs):
            loader = SimpleNamespace(value=None)
            def run():
                loader.value = task(lambda *args: None)
                return True
            loader.exec_ = run
            return loader
        for selected, expected in (([ResourceType.NSS], [script]), ([], [])):
            searcher = SimpleNamespace()
            with patch("toolset.gui.dialogs.search.AsyncLoader", side_effect=worker):
                self.assertTrue(FileSearcher.search(searcher, installation, False, True, "match", True, False, False, selected))
            self.assertEqual(searcher.results, expected)

    def test_cached_2da_is_only_reloaded_when_requested(self):
        for reload, queried, expected in ((False, False, "old"), (True, True, "new")):
            def resources(identifiers, locations):
                return {identifier: SimpleNamespace(data=b"new") for identifier in identifiers}
            installation = SimpleNamespace(_cache2da={"known": "old"}, resources=Mock(side_effect=resources))
            with patch("toolset.data.installation.read_2da", return_value="new"):
                HTInstallation.htBatchCache2DA(installation, ["known"], reload=reload)
            self.assertEqual(installation.resources.called, queried)
            self.assertEqual(installation._cache2da["known"], expected)


class CompileTests(TestCase):
    def test_untitled_scripts_use_separately_chosen_destinations(self):
        with TemporaryDirectory() as folder:
            targets = [Path(folder) / "first.ncs", Path(folder) / "second.ncs"]
            for target in targets:
                editor = SimpleNamespace(_filepath=None, _resname=None, _source_dir=None,
                    _installation=SimpleNamespace(tsl=False), savedFile=Mock(),
                    ui=SimpleNamespace(codeEdit=SimpleNamespace(toPlainText=lambda: "void main() {}")))
                with patch.object(nss.QFileDialog, "getSaveFileName", return_value=(str(target), "")), \
                     patch.object(nss, "compileScript", return_value=b"compiled"), patch.object(nss, "QMessageBox"):
                    self.assertTrue(nss.NSSEditor.compileCurrentScript(editor))
                self.assertEqual(target.read_bytes(), b"compiled")
                self.assertIsNone(editor._filepath)
                editor.savedFile.emit.assert_called_once()

    def test_cancelled_destination_does_not_compile(self):
        editor = SimpleNamespace(_filepath=None)
        with patch.object(nss.QFileDialog, "getSaveFileName", return_value=("", "")), \
             patch.object(nss, "compileScript") as compile_script:
            self.assertFalse(nss.NSSEditor.compileCurrentScript(editor))
        compile_script.assert_not_called()


    def test_normalized_output_requires_overwrite_confirmation(self):
        with TemporaryDirectory() as folder:
            target = Path(folder) / "existing.ncs"
            target.write_bytes(b"keep")
            editor = SimpleNamespace(_filepath=None)
            with patch.object(nss.QFileDialog, "getSaveFileName", return_value=(str(target.with_suffix(".txt")), "")), \
                 patch.object(nss.QMessageBox, "question", return_value=nss.QMessageBox.No), \
                 patch.object(nss, "compileScript") as compile_script:
                self.assertFalse(nss.NSSEditor.compileCurrentScript(editor))
            compile_script.assert_not_called()
            self.assertEqual(target.read_bytes(), b"keep")


class QtEditorTests(TestCase):
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

    def setUp(self):
        self.code = nss.CodeEditor(None)
        self.code.resize(900, 500)
        self.code.show()
        self.addCleanup(self.code.close)
        self.app.processEvents(QEventLoop.AllEvents)

    def test_minimap_reuses_unchanged_line_layouts(self):
        self.code.setPlainText("\n".join(f"int variable_{i};" for i in range(2000)))
        minimap = self.code._minimap
        minimap._timer.stop()
        minimap._render()
        cursor = QTextCursor(self.code.document().findBlockByNumber(1000))
        cursor.movePosition(QTextCursor.EndOfBlock)
        with patch.object(nss, "QTextLayout", wraps=nss.QTextLayout) as layouts:
            cursor.insertText(" // edited")
            minimap._timer.stop()
            minimap._render()
        self.assertEqual(layouts.call_count, 1)

    def test_folding_updates_are_debounced(self):
        self.code._structureTimer.stop()
        spy = QSignalSpy(self.code._structureTimer.timeout)
        for letter in "burst":
            self.code.insertPlainText(letter)
        self.assertEqual(len(spy), 0)
        QTest.qWait(nss.EDITOR_UPDATE_DELAY_MS + 100)
        self.assertEqual(len(spy), 1)

    def test_unfold_restores_hidden_blocks(self):
        self.code.setPlainText("void main()\n{\n    int value;\n}\n")
        self.code.foldAll()
        self.assertFalse(self.code.document().findBlockByNumber(2).isVisible())
        self.code.unfoldAll()
        self.assertTrue(self.code.document().findBlockByNumber(2).isVisible())

    def test_archive_editor_uses_shared_save_contract(self):
        archive = ERFEditor(None)
        self.addCleanup(archive.close)
        with TemporaryDirectory() as folder:
            target = Path(folder) / "empty.erf"
            with patch("toolset.gui.editor.QFileDialog.getSaveFileName", return_value=(str(target), "")):
                self.assertTrue(archive.saveAs())
            self.assertTrue(target.is_file())
            self.assertTrue(archive.ui.refreshButton.isEnabled())
