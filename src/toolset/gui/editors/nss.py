from __future__ import annotations

import math
import re
from operator import attrgetter
from typing import TYPE_CHECKING, ClassVar

from pykotor.common.scriptdefs import KOTOR_CONSTANTS, KOTOR_FUNCTIONS, TSL_CONSTANTS, TSL_FUNCTIONS
from pykotor.common.stream import BinaryWriter
from pykotor.resource.formats.erf import ERF, read_erf, write_erf
from pykotor.resource.formats.ncs.compiler.source import CompileError
from pykotor.resource.formats.rim import RIM, read_rim, write_rim
from pykotor.resource.type import ResourceType
from pykotor.tools.misc import is_any_erf_type_file, is_bif_file, is_rim_file
from PyQt5 import QtCore
from PyQt5.QtCore import QPoint, QPointF, QRect, QRectF, QRegExp, QSettings, QSize, QStringListModel, QTimer
from PyQt5.QtGui import (
    QColor,
    QFont,
    QFontMetricsF,
    QKeySequence,
    QPainter,
    QPaintEvent,
    QPixmap,
    QPolygonF,
    QResizeEvent,
    QSyntaxHighlighter,
    QTextBlock,
    QTextBlockUserData,
    QTextCharFormat,
    QTextCursor,
    QTextDocument,
    QTextFormat,
    QTextLayout,
)
from PyQt5.QtWidgets import QAction, QCompleter, QDialog, QDialogButtonBox, QFileDialog, QInputDialog, QListWidgetItem, QMessageBox, QPlainTextEdit, QShortcut, QTextEdit, QVBoxLayout, QWidget
from toolset.gui.dialogs.find_replace import FindReplaceDialog
from toolset.gui.editor import Editor
from toolset.gui.widgets.settings.installations import GlobalSettings, NoConfigurationSetError
from toolset.utils.script import compileScript, decompileScript
from utility.error_handling import universal_simplify_exception
from utility.system.path import Path

if TYPE_CHECKING:
    import os

    from pykotor.common.script import ScriptConstant, ScriptFunction
    from toolset.data.installation import HTInstallation


EDITOR_UPDATE_DELAY_MS = 150


class NSSEditor(Editor):
    TAB_SIZE = 4
    TAB_AS_SPACE = True

    def __init__(self, parent: QWidget | None, installation: HTInstallation | None):
        """Initialize the script editor window."""
        supported: list[ResourceType] = [ResourceType.NSS, ResourceType.NCS]
        super().__init__(parent, "Script Editor", "script", supported, supported, installation)

        from toolset.uic.editors.nss import Ui_MainWindow

        self.ui = Ui_MainWindow()
        self.ui.setupUi(self)
        self._setupMenus()
        self._setupSignals()

        self._global_settings: GlobalSettings = GlobalSettings()
        self._highlighter: SyntaxHighlighter = SyntaxHighlighter(self.ui.codeEdit.document(), installation)
        self.setInstallation(self._installation)

        self.ui.codeEdit.setTabStopDistance(QFontMetricsF(self.ui.codeEdit.font()).horizontalAdvance(" ") * NSSEditor.TAB_SIZE)

        self._setupEditing()
        self.new()

    def _setupSignals(self):
        """Sets up signals and slots for the GUI."""
        self.ui.actionCompile.triggered.connect(self.compileCurrentScript)
        self.savedFile.connect(self._sourceSaved)
        self.ui.tabWidget.currentChanged.connect(self.changeDescription)
        self.ui.constantList.itemSelectionChanged.connect(self.changeDescription)
        self.ui.functionList.itemSelectionChanged.connect(self.changeDescription)
        self.ui.constantList.doubleClicked.connect(self.insertSelectedConstant)
        self.ui.functionList.doubleClicked.connect(self.insertSelectedFunction)

        self.ui.functionSearchEdit.textChanged.connect(self.onFunctionSearch)
        self.ui.constantSearchEdit.textChanged.connect(self.onConstantSearch)

        self.ui.actionCompile.setShortcut("F5")
        QShortcut("Ctrl+I", self).activated.connect(self.onInsertShortcut)

    def _textAction(self, menu, text, shortcut, slot, *, code_only=False):
        action = QAction(text, self)
        action.setShortcut(QKeySequence(shortcut))
        action.triggered.connect(slot)
        if code_only:
            action.setShortcutContext(QtCore.Qt.WidgetWithChildrenShortcut)
            self.ui.codeEdit.addAction(action)
        menu.addAction(action)
        return action

    def _setupEditing(self):
        code = self.ui.codeEdit
        self.findDialog = FindReplaceDialog(self)
        self.findDialog.findRequested.connect(self.findNext)
        self.findDialog.replaceRequested.connect(self.replaceCurrent)
        self.findDialog.replaceAllRequested.connect(self.replaceAll)
        self.snippetSettings = QSettings("HolocronToolset", "ScriptEditor")
        menu = self.menuBar().addMenu("Edit")
        undo = self._textAction(menu, "Undo", QKeySequence.Undo, code.undo, code_only=True)
        redo = self._textAction(menu, "Redo", QKeySequence.Redo, code.redo, code_only=True)
        redo.setShortcuts(QKeySequence.Redo)
        undo.setEnabled(False)
        redo.setEnabled(False)
        code.undoAvailable.connect(undo.setEnabled)
        code.redoAvailable.connect(redo.setEnabled)
        menu.addSeparator()
        cut = self._textAction(menu, "Cut", QKeySequence.Cut, code.cut, code_only=True)
        copy = self._textAction(menu, "Copy", QKeySequence.Copy, code.copy, code_only=True)
        cut.setEnabled(False)
        copy.setEnabled(False)
        code.copyAvailable.connect(cut.setEnabled)
        code.copyAvailable.connect(copy.setEnabled)
        self._textAction(menu, "Paste", QKeySequence.Paste, code.paste, code_only=True)
        self._textAction(menu, "Select All", QKeySequence.SelectAll, code.selectAll, code_only=True)
        menu.addSeparator()
        self._textAction(menu, "Find…", "Ctrl+F", self.showFind)
        self._textAction(menu, "Replace…", "Ctrl+H", lambda: self.showFind(replace=True))
        self._textAction(menu, "Find Next", "F3", lambda: self.findNext(False))
        self._textAction(menu, "Find Previous", "Shift+F3", lambda: self.findNext(True))
        self._textAction(menu, "Go to Line…", "Ctrl+G", self.goToLine)
        self._textAction(menu, "Complete Function / Constant", "Ctrl+Space", lambda: code.showCompletions(), code_only=True)
        menu.addSeparator()
        self._textAction(menu, "Comment / Uncomment Lines", "Ctrl+/", code.toggleComment, code_only=True)
        self._textAction(menu, "Indent Lines", "Ctrl+]", lambda: code.indentLines(False), code_only=True)
        self._textAction(menu, "Unindent Lines", "Ctrl+[", lambda: code.indentLines(True), code_only=True)
        self._textAction(menu, "Duplicate Lines", "Ctrl+D", code.duplicateLines, code_only=True)
        self._textAction(menu, "Move Lines Up", "Alt+Up", lambda: code.moveLines(-1), code_only=True)
        self._textAction(menu, "Move Lines Down", "Alt+Down", lambda: code.moveLines(1), code_only=True)
        bookmarks = menu.addMenu("Bookmarks")
        self._textAction(bookmarks, "Toggle Bookmark", "Ctrl+F2", lambda: code.toggleBookmark())
        self._textAction(bookmarks, "Next Bookmark", "F2", lambda: code.nextBookmark(False))
        self._textAction(bookmarks, "Previous Bookmark", "Shift+F2", lambda: code.nextBookmark(True))
        self._textAction(bookmarks, "Clear Bookmarks", "", code.clearBookmarks)
        self.snippetsMenu = menu.addMenu("Snippets")
        self.snippetsMenu.aboutToShow.connect(self._snippetMenu)
        view = self.menuBar().addMenu("View")
        self._textAction(view, "Toggle Fold", "Ctrl+Shift+[", lambda: code.toggleFold())
        self._textAction(view, "Fold All", "Ctrl+K, Ctrl+0", code.foldAll)
        self._textAction(view, "Unfold All", "Ctrl+K, Ctrl+J", code.unfoldAll)
        minimap = self._textAction(view, "Minimap", "Ctrl+Shift+M", code.setMinimapVisible)
        minimap.setCheckable(True)
        minimap.setChecked(True)
        self._analysisDock = None
        script = self.menuBar().addMenu("Script")
        self._textAction(script, "Check Syntax and Semantics", "Ctrl+F5", lambda: self.analyzeScript())
        self._textAction(script, "Find Top-Level Definition…", "F12", lambda: self.analyzeScript(definition=True))

    def analyzeScript(self, *, definition=False):
        from toolset.data.script_analysis import analyze_script
        from toolset.gui.dialogs.asyncloader import AsyncLoader
        from toolset.gui.widgets.script_analysis import ScriptAnalysisDock

        source = self.ui.codeEdit.toPlainText()
        if self._filepath is None:
            name = "<untitled.nss>"
        elif self._restype == ResourceType.NSS and not is_any_erf_type_file(self._filepath.name) and not is_rim_file(self._filepath.name) and not is_bif_file(self._filepath.name):
            name = str(self._filepath)
        else:
            name = f"{self._filepath} :: {self._resname}.nss"
        cursor = self.ui.codeEdit.textCursor()
        cursor.select(QTextCursor.WordUnderCursor)
        symbol = cursor.selectedText() if definition else None
        lookup = [] if self._source_dir is None else [str(self._source_dir)]
        if self._global_settings.extractPath:
            lookup.append(self._global_settings.extractPath)
        tsl = self._installation.tsl
        loader = AsyncLoader(self, "Analyzing script",
                             lambda report: analyze_script(source, name, tsl, lookup, report),
                             "Unable to analyze script", with_progress=True)
        if not loader.exec_():
            return
        if self._analysisDock is None:
            self._analysisDock = ScriptAnalysisDock(self)
            self._analysisDock.navigate.connect(self._openAnalysisLocation)
            self.addDockWidget(QtCore.Qt.BottomDockWidgetArea, self._analysisDock)
        self._analysisDock.setAnalysis(loader.value, symbol)
        self._analysisDock.show()
        self._analysisDock.raise_()

    def _openAnalysisLocation(self, location, source):
        code = self.ui.codeEdit
        if source.filename == self._analysisDock._result.root_name and code.toPlainText() == source.text:
            target = code
            dialog = None
        else:
            dialog = QDialog(self)
            dialog.setWindowTitle(f"Read-only source snapshot — {location}")
            dialog.resize(850, 600)
            layout = QVBoxLayout(dialog)
            target = QPlainTextEdit(dialog)
            target.setFont(code.font())
            target.setLineWrapMode(QPlainTextEdit.NoWrap)
            target.setReadOnly(True)
            target.setPlainText(source.text)
            highlighter = SyntaxHighlighter(target.document(), self._installation)
            layout.addWidget(target)
            close = QDialogButtonBox(QDialogButtonBox.Close, dialog)
            close.rejected.connect(dialog.reject)
            layout.addWidget(close)
        block = target.document().findBlockByNumber(location.line - 1)
        cursor = QTextCursor(block)
        column = len(location.line_text[:location.column - 1].encode("utf-16-le")) // 2
        cursor.setPosition(block.position() + column)
        target.setTextCursor(cursor)
        target.centerCursor()
        target.setFocus()
        if dialog is not None:
            QtCore.QTimer.singleShot(0, target.centerCursor)
            dialog.exec_()
            dialog.deleteLater()

    def showFind(self, *, replace=False):
        self.findDialog.openFor(replace=replace, selection=self.ui.codeEdit.textCursor().selectedText())

    def _findFlags(self):
        flags = QTextDocument.FindFlags()
        if self.findDialog.caseSensitive.isChecked():
            flags |= QTextDocument.FindCaseSensitively
        if self.findDialog.wholeWords.isChecked():
            flags |= QTextDocument.FindWholeWords
        return flags

    def findNext(self, backwards=False):
        term = self.findDialog.findEdit.text()
        if not term:
            self.showFind()
            return
        code = self.ui.codeEdit
        flags = self._findFlags()
        if backwards:
            flags |= QTextDocument.FindBackward
        match = code.document().find(term, code.textCursor(), flags)
        wrapped = match.isNull()
        if wrapped:
            cursor = QTextCursor(code.document())
            cursor.movePosition(QTextCursor.End if backwards else QTextCursor.Start)
            match = code.document().find(term, cursor, flags)
        if match.isNull():
            self.findDialog.status.setText("No matches.")
        else:
            code.setTextCursor(match)
            code.centerCursor()
            self.findDialog.status.setText("Search wrapped." if wrapped else "Match found.")

    def replaceCurrent(self):
        code = self.ui.codeEdit
        cursor = code.textCursor()
        match = code.document().find(self.findDialog.findEdit.text(), cursor.selectionStart(), self._findFlags())
        if match.isNull() or (match.selectionStart(), match.selectionEnd()) != (cursor.selectionStart(), cursor.selectionEnd()):
            self.findNext()
            return
        cursor.beginEditBlock()
        cursor.insertText(self.findDialog.replaceEdit.text())
        cursor.endEditBlock()
        code.setTextCursor(cursor)
        self.findNext()

    def replaceAll(self):
        code = self.ui.codeEdit
        document = code.document()
        flags = self._findFlags()
        term = self.findDialog.findEdit.text()
        matches = []
        match = document.find(term, 0, flags)
        while not match.isNull():
            matches.append((match.selectionStart(), match.selectionEnd()))
            match = document.find(term, match.selectionEnd(), flags)
        original = code.textCursor()
        cursor = QTextCursor(document)
        cursor.beginEditBlock()
        for start, end in reversed(matches):
            cursor.setPosition(start)
            cursor.setPosition(end, QTextCursor.KeepAnchor)
            cursor.insertText(self.findDialog.replaceEdit.text())
        cursor.endEditBlock()
        code.setTextCursor(original)
        self.findDialog.status.setText(f"Replaced {len(matches)} occurrence(s).")

    def goToLine(self):
        code = self.ui.codeEdit
        line, accepted = QInputDialog.getInt(self, "Go to Line", "Line number:", code.textCursor().blockNumber() + 1, 1, code.blockCount())
        if accepted:
            code.setTextCursor(QTextCursor(code.document().findBlockByNumber(line - 1)))
            code.centerCursor()
            code.setFocus()

    def _snippetMenu(self):
        self.snippetsMenu.clear()
        save = self.snippetsMenu.addAction("Save Selection as Snippet…", self.saveSnippet)
        save.setEnabled(self.ui.codeEdit.textCursor().hasSelection())
        snippets = self.snippetSettings.value("snippets", {}, type=dict)
        self.snippetsMenu.addSeparator()
        for name in sorted(snippets):
            action = self.snippetsMenu.addAction(name.replace("&", "&&"))
            action.triggered.connect(lambda _, name=name: self.insertSnippet(name))
        self.snippetsMenu.addSeparator()
        delete = self.snippetsMenu.addAction("Delete Snippet…", self.deleteSnippet)
        delete.setEnabled(bool(snippets))

    def saveSnippet(self):
        text = self.ui.codeEdit.textCursor().selectedText().replace("\u2029", "\n")
        name, accepted = QInputDialog.getText(self, "Save Snippet", "Name (reuse a name to replace it):")
        if accepted and name.strip():
            snippets = self.snippetSettings.value("snippets", {}, type=dict)
            snippets[name.strip()] = text
            self.snippetSettings.setValue("snippets", snippets)

    def insertSnippet(self, name):
        snippets = self.snippetSettings.value("snippets", {}, type=dict)
        cursor = self.ui.codeEdit.textCursor()
        cursor.beginEditBlock()
        cursor.insertText(snippets[name])
        cursor.endEditBlock()
        self.ui.codeEdit.setTextCursor(cursor)
        self.ui.codeEdit.setFocus()

    def deleteSnippet(self):
        snippets = self.snippetSettings.value("snippets", {}, type=dict)
        name, accepted = QInputDialog.getItem(self, "Delete Snippet", "Snippet:", sorted(snippets), 0, False)
        if accepted:
            snippets = self.snippetSettings.value("snippets", {}, type=dict)
            del snippets[name]
            self.snippetSettings.setValue("snippets", snippets)

    def setInstallation(self, installation: HTInstallation):
        """Sets the installation for the editor."""
        self._installation = installation

        # sort them alphabetically
        constants: list[ScriptConstant] = sorted(TSL_CONSTANTS if self._installation.tsl else KOTOR_CONSTANTS, key=attrgetter("name"))
        functions: list[ScriptFunction] = sorted(TSL_FUNCTIONS if self._installation.tsl else KOTOR_FUNCTIONS, key=attrgetter("name"))

        self.ui.functionList.clear()
        self.ui.constantList.clear()
        for function in functions:
            item = QListWidgetItem(function.name)
            item.setData(QtCore.Qt.UserRole, function)
            self.ui.functionList.addItem(item)

        for constant in constants:
            item = QListWidgetItem(constant.name)
            item.setData(QtCore.Qt.UserRole, constant)
            self.ui.constantList.addItem(item)

        self.ui.codeEdit.setCompletions([definition.name for definition in (*functions, *constants)])

    def load(self, filepath: os.PathLike | str, resref: str, restype: ResourceType, data: bytes):
        """Loads a resource into the editor."""
        super().load(filepath, resref, restype, data)
        self._source_dir = self._filepath.parent

        if restype == ResourceType.NSS:
            self.ui.codeEdit.setPlainText(data.decode("windows-1252", errors="ignore"))
        elif restype == ResourceType.NCS:
            try:
                source = decompileScript(data, self._installation.tsl)
                self.ui.codeEdit.setPlainText(source)
            except ValueError as e:
                QMessageBox(QMessageBox.Critical, "Decompilation Failed", str(universal_simplify_exception(e))).exec_()
                self.new()
            except NoConfigurationSetError as e:
                QMessageBox(QMessageBox.Critical, "Filepath is not set", str(universal_simplify_exception(e))).exec_()
                self.new()

    def build(self) -> tuple[bytes | None, bytes]:
        if self._restype != ResourceType.NCS:
            return self.ui.codeEdit.toPlainText().encode("windows-1252"), b""

        compiled_bytes: bytes | None = compileScript(
            self.ui.codeEdit.toPlainText(), self._installation.tsl,
            source_dir=self._source_dir,
        )
        print("compiling script from nsseditor")
        if compiled_bytes is None:
            print("user cancelled the compilation")
            return None, b""

        return compiled_bytes, b""

    def new(self):
        super().new()
        self._source_dir: Path | None = None

        self.ui.codeEdit.setPlainText("\n\nvoid main()\n{\n    \n}\n")

    def _sourceSaved(self, filepath, resname, restype, data):
        if restype == ResourceType.NSS:
            self._source_dir = Path(filepath).parent

    def compileCurrentScript(self) -> bool:
        """Compile to a chosen file, the source's sibling NCS, or its containing archive."""
        try:
            filepath = self._filepath
            if filepath is None:
                chosen, _filter = QFileDialog.getSaveFileName(self, "Save Compiled Script", "untitled_script.ncs", "Compiled Script (*.ncs)")
                if not chosen:
                    return False
                savePath = Path(chosen)
                if savePath.suffix.lower() != ".ncs":
                    savePath = savePath.with_suffix(".ncs")
                    if savePath.exists() and QMessageBox.question(
                        self, "Replace compiled script?", f"{savePath} already exists. Replace it?",
                        QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
                    ) != QMessageBox.Yes:
                        return False
            elif is_bif_file(filepath.name):
                savePath = self._installation.override_path() / f"{self._resname}.ncs"
            else:
                savePath = filepath.with_suffix(".ncs")

            data = compileScript(self.ui.codeEdit.toPlainText(), self._installation.tsl, source_dir=self._source_dir)
            if data is None:
                return False
            if filepath is not None and is_any_erf_type_file(filepath.name):
                erf = read_erf(filepath)
                erf.set_data(self._resname, ResourceType.NCS, data)
                write_erf(erf, filepath)
                savePath = filepath / f"{self._resname}.ncs"
                self.savedFile.emit(str(filepath), self._resname, ResourceType.NCS, data)
            elif filepath is not None and is_rim_file(filepath.name):
                rim = read_rim(filepath)
                rim.set_data(self._resname, ResourceType.NCS, data)
                write_rim(rim, filepath)
                savePath = filepath / f"{self._resname}.ncs"
                self.savedFile.emit(str(filepath), self._resname, ResourceType.NCS, data)
            else:
                BinaryWriter.dump(savePath, data)
                self.savedFile.emit(str(savePath), savePath.stem, ResourceType.NCS, data)
            QMessageBox(QMessageBox.Information, "Success", f"Compiled script successfully saved to:\n {savePath}.").exec_()
            return True
        except (CompileError, ValueError) as error:
            QMessageBox(QMessageBox.Critical, "Failed to compile", str(universal_simplify_exception(error))).exec_()
        except OSError as error:
            QMessageBox(QMessageBox.Critical, "Failed to save file", str(universal_simplify_exception(error))).exec_()
        return False

    def changeDescription(self):
        """Change the description textbox to whatever function or constant the user has selected."""
        self.ui.descriptionEdit.setPlainText("")

        if self.ui.tabWidget.currentIndex() == 0 and self.ui.functionList.selectedItems():  # Functions tab
            item = self.ui.functionList.selectedItems()[0]
            function = item.data(QtCore.Qt.UserRole)
            text = function.description + "\n" + str(function)
            self.ui.descriptionEdit.setPlainText(text)
        elif self.ui.tabWidget.currentIndex() == 1 and self.ui.constantList.selectedItems():  # Constants tab
            item = self.ui.constantList.selectedItems()[0]
            constant = item.data(QtCore.Qt.UserRole)
            self.ui.descriptionEdit.setPlainText(str(constant))

    def insertSelectedConstant(self):
        """Inserts the selected constant on the constant list into the code textbox at the cursors location. The cursor is
        then shifted to the end of the newly inserted constant.
        """  # noqa: D205
        if self.ui.constantList.selectedItems():
            constant = self.ui.constantList.selectedItems()[0].data(QtCore.Qt.UserRole)
            insert = constant.name
            self.insertTextAtCursor(insert)

    def insertSelectedFunction(self):
        """Inserts the selected function on the function list into the code textbox at the cursors location. The cursor is
        then shifted to the start of the first parameter of the inserted function.
        """  # noqa: D205
        if self.ui.functionList.selectedItems():
            function: ScriptFunction = self.ui.functionList.selectedItems()[0].data(QtCore.Qt.UserRole)
            insert = f"{function.name}()"
            self.insertTextAtCursor(insert, insert.index("(") + 1)

    def insertTextAtCursor(self, insert: str, offset: int | None = None):
        """Inserts the given text at the cursors location and then shifts the cursor position by the offset specified. If
        no offset is specified then the cursor is moved to the end of the inserted text.

        Args:
        ----
            insert: Text to insert.
            offset: Amount of characters to shift the cursor.
        """  # noqa: D205
        cursor = self.ui.codeEdit.textCursor()
        position = cursor.selectionStart()
        cursor.beginEditBlock()
        cursor.insertText(insert)
        cursor.endEditBlock()
        if offset is not None:
            cursor.setPosition(position + len(insert[:offset].encode("utf-16-le")) // 2)
        self.ui.codeEdit.setTextCursor(cursor)

    def onInsertShortcut(self):
        """Inserts selected shortcut based on active tab."""
        if self.ui.tabWidget.currentIndex() == 0:
            self.insertSelectedFunction()
        elif self.ui.tabWidget.currentIndex() == 1:
            self.insertSelectedConstant()

    def onFunctionSearch(self):
        string = self.ui.functionSearchEdit.text()
        for i in range(self.ui.functionList.count()):
            item = self.ui.functionList.item(i)
            item.setHidden(string not in item.text())

    def onConstantSearch(self):
        string = self.ui.constantSearchEdit.text()
        for i in range(self.ui.constantList.count()):
            item = self.ui.constantList.item(i)
            item.setHidden(string not in item.text())


class LineNumberArea(QWidget):
    def __init__(self, editor: CodeEditor):
        super().__init__(editor)
        self._editor: CodeEditor = editor

    def sizeHint(self) -> QSize:
        return QSize(self._editor.lineNumberAreaWidth(), 0)

    def paintEvent(self, event):
        self._editor.lineNumberAreaPaintEvent(event)

    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton:
            block = self._editor.firstVisibleBlock()
            top = self._editor.blockBoundingGeometry(block).translated(self._editor.contentOffset()).top()
            while block.isValid():
                bottom = top + self._editor.blockBoundingRect(block).height()
                if block.isVisible() and top <= event.y() < bottom:
                    if event.x() >= self.width() - 16:
                        if block.blockNumber() in self._editor._folds:
                            self._editor.toggleFold(block)
                    else:
                        self._editor.toggleBookmark(block)
                    return
                block = block.next()
                top = bottom


class Bookmark(QTextBlockUserData):
    pass


class ScriptMinimap(QWidget):
    def __init__(self, editor):
        super().__init__(editor)
        self._editor = editor
        self._pixmap = QPixmap()
        self._lineHeight = 2.0
        self._frame = None
        self._lines = {}
        self._dirtyFirst = None
        self._dirtyLast = -1
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(EDITOR_UPDATE_DELAY_MS)
        self._timer.timeout.connect(self._render)
        editor.document().contentsChange.connect(self.invalidate)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setToolTip("Click or drag to jump to a line.")

    def rebuild(self):
        """Invalidate geometry/font-dependent rendering, including edits made while hidden."""
        self._frame = None
        if self.isVisible():
            self._timer.start()

    def invalidate(self, position, removed, added):
        document = self._editor.document()
        limit = document.characterCount() - 1
        first = document.findBlock(min(position, limit)).blockNumber()
        last = document.findBlock(min(position + added, limit)).blockNumber()
        self._dirtyFirst = first if self._dirtyFirst is None else min(first, self._dirtyFirst)
        self._dirtyLast = max(last, self._dirtyLast)
        if self.isVisible():
            self._timer.start()

    def _lineLayout(self, block, font):
        formats = block.layout().formats()
        signature = (block.text(), block.userState(), tuple((part.start, part.length, part.format) for part in formats))
        number = block.blockNumber()
        cached = self._lines.get(number)
        if cached is not None and cached[0] == signature:
            return cached[1], False
        layout = QTextLayout(block.text(), font)
        layout.setTextOption(self._editor.document().defaultTextOption())
        layout.setFormats(formats)
        layout.beginLayout()
        line = layout.createLine()
        if line.isValid():
            line.setLineWidth(self.width() / 0.15)
        layout.endLayout()
        self._lines[number] = signature, layout
        return layout, True

    def _render(self):
        if not self.isVisible() or self.width() <= 0 or self.height() <= 0:
            return
        document = self._editor.document()
        count = document.blockCount()
        font = self._editor.font()
        palette = self._editor.palette()
        ratio = self.devicePixelRatioF()
        frame = (count, self.width(), self.height(), ratio, font.toString(),
                 palette.base().color().rgba(), palette.text().color().rgba())
        full = self._frame != frame
        if not full and self._dirtyFirst is None:
            self.update()
            return
        if full:
            self._lineHeight = min(3.0, self.height() / max(1, count))
            self._pixmap = QPixmap(round(self.width() * ratio), round(self.height() * ratio))
            self._pixmap.setDevicePixelRatio(ratio)
            self._pixmap.fill(palette.base().color())
            self._lines.clear()
        first = 0 if full else self._dirtyFirst
        last = count - 1 if full else self._dirtyLast
        end = first
        block = document.findBlockByNumber(first)
        while block.isValid():
            number = block.blockNumber()
            _layout, changed = self._lineLayout(block, font)
            # Highlighting can propagate beyond the edit through a multiline comment.
            # Once a following unchanged block/state is reached, the remaining cache is valid.
            if not full and number > last and not changed:
                break
            end = number
            block = block.next()
        top = max(0, math.floor(first * self._lineHeight))
        bottom = min(self.height(), math.ceil((end + 1) * self._lineHeight))
        first_draw = max(0, math.floor(top / self._lineHeight) - 1)
        last_draw = min(count - 1, math.ceil(bottom / self._lineHeight))
        painter = QPainter(self._pixmap)
        area = QRectF(0, top, self.width(), bottom - top)
        painter.setClipRect(area)
        painter.fillRect(area, palette.base().color())
        painter.setPen(palette.text().color())
        for number in range(first_draw, last_draw + 1):
            painter.save()
            painter.translate(0, number * self._lineHeight)
            painter.scale(0.15, self._lineHeight / max(1, self._editor.fontMetrics().height()))
            self._lines[number][1].draw(painter, QPointF(0, 0))
            painter.restore()
        painter.end()
        self._frame = frame
        self._dirtyFirst, self._dirtyLast = None, -1
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), self._editor.palette().base())
        painter.drawPixmap(0, 0, self._pixmap)
        first = self._editor.firstVisibleBlock().blockNumber()
        last = self._editor.cursorForPosition(QPoint(0, self._editor.viewport().height() - 1)).blockNumber()
        area = QRectF(0, first * self._lineHeight, self.width() - 1,
                      max(2, (last - first + 1) * self._lineHeight))
        painter.setPen(QColor(80, 120, 180, 160))
        painter.setBrush(QColor(80, 120, 180, 40))
        painter.drawRect(area)

    def resizeEvent(self, event):
        self.rebuild()

    def showEvent(self, event):
        self.rebuild()

    def _jump(self, event):
        number = max(0, min(self._editor.blockCount() - 1, int(event.y() / self._lineHeight)))
        self._editor.setTextCursor(QTextCursor(self._editor.document().findBlockByNumber(number)))
        self._editor.centerCursor()
        self._editor.setFocus()

    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton:
            self._jump(event)

    def mouseMoveEvent(self, event):
        if event.buttons() & QtCore.Qt.LeftButton:
            self._jump(event)

    def wheelEvent(self, event):
        self._editor.wheelEvent(event)


class CodeEditor(QPlainTextEdit):
    """CodeEditor shows the line numbers on the side of the text editor and highlights the row the cursor is on.

    Ported from the C++ code at: https://doc.qt.io/qt-5/qtwidgets-widgets-codeeditor-example.html
    """

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self._lineNumberArea = LineNumberArea(self)
        self._minimap = ScriptMinimap(self)
        self._minimapEnabled = True
        self._folds = {}
        self._collapsed = []
        self._hasHiddenBlocks = False
        self._structureTimer = QTimer(self)
        self._structureTimer.setSingleShot(True)
        self._structureTimer.timeout.connect(self._refreshStructure)
        self._structureTimer.setInterval(EDITOR_UPDATE_DELAY_MS)
        self.textChanged.connect(self._structureTimer.start)
        self.cursorPositionChanged.connect(self._revealCursor)
        self._completionWords = QStringListModel(self)
        self._completion = QCompleter(self._completionWords, self)
        self._completion.setWidget(self)
        self._completion.setCaseSensitivity(QtCore.Qt.CaseInsensitive)
        self._completion.setCompletionMode(QCompleter.PopupCompletion)
        self._completion.activated[str].connect(self._insertCompletion)
        self.textChanged.connect(self._completion.popup().hide)
        self.cursorPositionChanged.connect(self._completion.popup().hide)

        self.blockCountChanged.connect(self._updateLineNumberAreaWidth)
        self.updateRequest.connect(self._updateLineNumberArea)
        self.cursorPositionChanged.connect(self._highlightCurrentLine)

        self._updateLineNumberAreaWidth(0)
        self._highlightCurrentLine()

    def _refreshStructure(self):
        self._structureTimer.stop()
        text = self.toPlainText()
        tokens = re.finditer(r'//[^\n]*|/\*.*?(?:\*/|\Z)|"(?:\\.|[^"\\\n])*(?:"|$)|\'(?:\\.|[^\'\\\n])*(?:\'|$)|[{}]', text, re.DOTALL | re.MULTILINE)
        stack = []
        folds = {}
        number, previous = 0, 0
        for token in tokens:
            number += text.count("\n", previous, token.start())
            value = token.group()
            if value == "{":
                stack.append(number)
            elif value == "}" and stack:
                start = stack.pop()
                if number > start + 1:
                    folds[start] = (self.document().findBlockByNumber(start), self.document().findBlockByNumber(number))
            number += value.count("\n")
            previous = token.end()
        self._folds = folds
        self._collapsed = [folds[block.blockNumber()] for block, _end in self._collapsed
                           if block.isValid() and block.blockNumber() in folds and folds[block.blockNumber()][0] == block]
        self._applyFolds()

    def _applyFolds(self):
        collapsed = {block.blockNumber(): end.blockNumber() for block, end in self._collapsed}
        if not collapsed and not self._hasHiddenBlocks:
            self._lineNumberArea.update()
            return
        self._hasHiddenBlocks = bool(collapsed)
        cursor = self.textCursor()
        first = self.document().findBlock(cursor.selectionStart()).blockNumber()
        last = self.document().findBlock(cursor.selectionEnd()).blockNumber()
        covering = [start for start, end in collapsed.items() if first < end and last > start]
        if covering:
            super().setTextCursor(QTextCursor(self.document().findBlockByNumber(min(covering))))
        block = self.document().begin()
        hidden_until = -1
        changed = False
        while block.isValid():
            number = block.blockNumber()
            visible = number >= hidden_until
            if block.isVisible() != visible:
                block.setVisible(visible)
                changed = True
            hidden_until = max(hidden_until, collapsed.get(number, -1))
            block = block.next()
        if changed:
            self.document().markContentsDirty(0, self.document().characterCount())
        self.viewport().update()
        self._lineNumberArea.update()
        self._minimap.update()

    def _revealRange(self, first, last):
        retained = [(block, end) for block, end in self._collapsed
                    if block.isValid() and end.isValid() and not (first < end.blockNumber() and last > block.blockNumber())]
        if len(retained) != len(self._collapsed):
            self._collapsed = retained
            self._applyFolds()

    def _revealCursor(self):
        cursor = self.textCursor()
        self._revealRange(self.document().findBlock(cursor.selectionStart()).blockNumber(),
                          self.document().findBlock(cursor.selectionEnd()).blockNumber())

    def setTextCursor(self, cursor):
        self._revealRange(self.document().findBlock(cursor.selectionStart()).blockNumber(),
                          self.document().findBlock(cursor.selectionEnd()).blockNumber())
        super().setTextCursor(cursor)

    def setPlainText(self, text):
        self.unfoldAll()
        self._folds.clear()
        super().setPlainText(text)

    def toggleFold(self, block=None):
        self._refreshStructure()
        number = (self.textCursor().block() if block is None else block).blockNumber()
        candidates = [(start, end) for start, end in self._folds.values()
                      if start.blockNumber() <= number < end.blockNumber()]
        if not candidates:
            return
        fold = max(candidates, key=lambda candidate: candidate[0].blockNumber())
        if fold in self._collapsed:
            self._collapsed.remove(fold)
        else:
            self._collapsed.append(fold)
        self._applyFolds()

    def foldAll(self):
        self._refreshStructure()
        self._collapsed = list(self._folds.values())
        self._applyFolds()

    def unfoldAll(self):
        self._collapsed.clear()
        self._applyFolds()

    def setMinimapVisible(self, visible):
        self._minimapEnabled = visible
        self._minimap.setVisible(visible)
        self._updateLineNumberAreaWidth(0)

    def setCompletions(self, names):
        self._completion.popup().hide()
        self._completionWords.setStringList(sorted(set(names), key=lambda name: (name.casefold(), name)))
        self._completion.popup().setFont(self.font())

    def _completionPrefix(self):
        cursor = self.textCursor()
        cursor.clearSelection()
        cursor.movePosition(QTextCursor.StartOfBlock, QTextCursor.KeepAnchor)
        match = re.search(r"\b[A-Za-z_][A-Za-z0-9_]*$", cursor.selectedText())
        return match.group() if match else ""

    def showCompletions(self, manual=True):
        prefix = self._completionPrefix()
        self._completion.setCompletionPrefix(prefix)
        if not manual and (len(prefix) < 3 or (
            self._completion.completionCount() == 1 and self._completion.currentCompletion() == prefix
        )):
            self._completion.popup().hide()
            return
        self._completion.popup().setCurrentIndex(self._completion.completionModel().index(0, 0))
        rect = self.cursorRect()
        rect.setWidth(self._completion.popup().sizeHintForColumn(0) + self._completion.popup().verticalScrollBar().sizeHint().width())
        self._completion.complete(rect)

    def _insertCompletion(self, name):
        cursor = self.textCursor()
        if self._completionPrefix():
            cursor.movePosition(QTextCursor.PreviousCharacter)
            cursor.select(QTextCursor.WordUnderCursor)
        cursor.beginEditBlock()
        cursor.insertText(name)
        cursor.endEditBlock()
        self.setTextCursor(cursor)

    def _selectedBlocks(self):
        cursor = self.textCursor()
        first = self.document().findBlock(cursor.selectionStart())
        last = self.document().findBlock(cursor.selectionEnd())
        if cursor.hasSelection() and last.position() == cursor.selectionEnd():
            last = last.previous()
        blocks = []
        block = first
        while block.isValid() and block.blockNumber() <= last.blockNumber():
            blocks.append(block)
            block = block.next()
        return blocks

    def indentLines(self, remove=False):
        original = self.textCursor()
        cursor = QTextCursor(self.document())
        cursor.beginEditBlock()
        for block in reversed(self._selectedBlocks()):
            cursor.setPosition(block.position())
            if remove:
                text = block.text()
                count = 1 if text.startswith("\t") else min(NSSEditor.TAB_SIZE, len(text) - len(text.lstrip(" ")))
                cursor.movePosition(QTextCursor.NextCharacter, QTextCursor.KeepAnchor, count)
                cursor.removeSelectedText()
            else:
                cursor.insertText(" " * NSSEditor.TAB_SIZE if NSSEditor.TAB_AS_SPACE else "\t")
        cursor.endEditBlock()
        self.setTextCursor(original)

    def toggleComment(self):
        blocks = [block for block in self._selectedBlocks() if block.text().strip()]
        uncomment = all(block.text().lstrip(" \t").startswith("//") for block in blocks)
        original = self.textCursor()
        cursor = QTextCursor(self.document())
        cursor.beginEditBlock()
        for block in reversed(blocks):
            text = block.text()
            indent = len(text) - len(text.lstrip(" \t"))
            cursor.setPosition(block.position() + indent)
            if uncomment:
                count = 3 if text[indent:].startswith("// ") else 2
                cursor.movePosition(QTextCursor.NextCharacter, QTextCursor.KeepAnchor, count)
                cursor.removeSelectedText()
            else:
                cursor.insertText("// ")
        cursor.endEditBlock()
        self.setTextCursor(original)

    def duplicateLines(self):
        original = self.textCursor()
        selected = original.hasSelection()
        column = original.positionInBlock()
        blocks = self._selectedBlocks()
        start = blocks[0].position()
        end = blocks[-1].position() + blocks[-1].length() - 1
        cursor = QTextCursor(self.document())
        cursor.setPosition(start)
        cursor.setPosition(end, QTextCursor.KeepAnchor)
        text = cursor.selectedText().replace("\u2029", "\n")
        cursor.setPosition(end)
        cursor.beginEditBlock()
        cursor.insertText("\n" + text)
        cursor.endEditBlock()
        if selected:
            cursor.setPosition(end + 1)
            cursor.setPosition(end + 1 + end - start, QTextCursor.KeepAnchor)
        else:
            cursor.setPosition(end + 1 + column)
        self.setTextCursor(cursor)

    def moveLines(self, direction):
        blocks = self._selectedBlocks()
        neighbor = blocks[0].previous() if direction < 0 else blocks[-1].next()
        if not neighbor.isValid():
            return
        original = self.textCursor()
        old_start = blocks[0].position()
        anchor, position = original.anchor() - old_start, original.position() - old_start
        selected = [(block.text(), block.userData() is not None) for block in blocks]
        other = neighbor.text(), neighbor.userData() is not None
        first = neighbor if direction < 0 else blocks[0]
        last = blocks[-1] if direction < 0 else neighbor
        start, end = first.position(), last.position() + last.length() - 1
        new_start = start if direction < 0 else start + neighbor.length()
        ordered = [*selected, other] if direction < 0 else [other, *selected]
        cursor = QTextCursor(self.document())
        cursor.setPosition(start)
        cursor.setPosition(end, QTextCursor.KeepAnchor)
        cursor.beginEditBlock()
        cursor.insertText("\n".join(text for text, _ in ordered))
        cursor.endEditBlock()
        block = self.document().findBlock(start)
        for _, marked in ordered:
            block.setUserData(Bookmark() if marked else None)
            block = block.next()
        limit = self.document().characterCount() - 1
        cursor.setPosition(min(new_start + anchor, limit))
        cursor.setPosition(min(new_start + position, limit), QTextCursor.KeepAnchor)
        self.setTextCursor(cursor)
        self.ensureCursorVisible()
        self._lineNumberArea.update()

    def keyPressEvent(self, event):
        if self._completion.popup().isVisible() and event.key() in (
            QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter, QtCore.Qt.Key_Tab,
            QtCore.Qt.Key_Backtab, QtCore.Qt.Key_Escape,
        ):
            event.ignore()
            return
        cursor = self.textCursor()
        if not cursor.hasSelection() and (
            (event.key() == QtCore.Qt.Key_Delete and cursor.atBlockEnd()) or
            (event.key() == QtCore.Qt.Key_Backspace and cursor.atBlockStart())
        ):
            self._refreshStructure()
            boundary = [(block, end) for block, end in self._collapsed
                        if (block == cursor.block() if event.key() == QtCore.Qt.Key_Delete
                            else end == cursor.block())]
            if boundary:
                self._collapsed = [fold for fold in self._collapsed if fold not in boundary]
                self._applyFolds()
                return
        if event.key() in (QtCore.Qt.Key_Tab, QtCore.Qt.Key_Backtab) and event.modifiers() in (QtCore.Qt.NoModifier, QtCore.Qt.ShiftModifier):
            if event.key() == QtCore.Qt.Key_Backtab or event.modifiers() == QtCore.Qt.ShiftModifier:
                self.indentLines(True)
            elif cursor.hasSelection():
                self.indentLines(False)
            else:
                cursor.insertText(" " * NSSEditor.TAB_SIZE if NSSEditor.TAB_AS_SPACE else "\t")
                self.setTextCursor(cursor)
            return
        if event.key() in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter) and event.modifiers() == QtCore.Qt.NoModifier:
            block = self.document().findBlock(cursor.selectionStart())
            context = QTextCursor(self.document())
            context.setPosition(block.position())
            context.setPosition(cursor.selectionStart(), QTextCursor.KeepAnchor)
            prefix = context.selectedText()
            indent = re.match(r"[ \t]*", prefix).group()
            if prefix.rstrip().endswith("{"):
                indent += " " * NSSEditor.TAB_SIZE if NSSEditor.TAB_AS_SPACE else "\t"
            cursor.beginEditBlock()
            cursor.insertText("\n" + indent)
            cursor.endEditBlock()
            self.setTextCursor(cursor)
            return
        super().keyPressEvent(event)
        if event.modifiers() in (QtCore.Qt.NoModifier, QtCore.Qt.ShiftModifier) and (
            event.text() or event.key() in (QtCore.Qt.Key_Backspace, QtCore.Qt.Key_Delete)
        ):
            self.showCompletions(False)

    def toggleBookmark(self, block=None):
        block = self.textCursor().block() if block is None else block
        block.setUserData(None if block.userData() is not None else Bookmark())
        self._lineNumberArea.update()

    def nextBookmark(self, backwards=False):
        blocks = []
        block = self.document().begin()
        while block.isValid():
            if block.userData() is not None:
                blocks.append(block)
            block = block.next()
        if not blocks:
            return
        current = self.textCursor().blockNumber()
        if backwards:
            blocks.reverse()
        following = [block for block in blocks if (block.blockNumber() < current if backwards else block.blockNumber() > current)]
        target = following[0] if following else blocks[0]
        self.setTextCursor(QTextCursor(target))
        self.centerCursor()
        self.setFocus()

    def clearBookmarks(self):
        block = self.document().begin()
        while block.isValid():
            block.setUserData(None)
            block = block.next()
        self._lineNumberArea.update()

    def lineNumberAreaPaintEvent(self, e: QPaintEvent):
        """Draws line numbers in the line number area."""
        painter = QPainter(self._lineNumberArea)
        painter.fillRect(e.rect(), QColor(230, 230, 230))

        block: QTextBlock = self.firstVisibleBlock()
        blockNumber: int = block.blockNumber()
        top: float = self.blockBoundingGeometry(block).translated(self.contentOffset()).top()
        bottom: float = top + self.blockBoundingRect(block).height()

        while block.isValid() and top <= e.rect().bottom():
            if block.isVisible() and bottom >= e.rect().top():
                number = str(blockNumber + 1)
                if block.userData() is not None:
                    painter.setPen(QtCore.Qt.NoPen)
                    painter.setBrush(QColor(40, 110, 200))
                    painter.drawEllipse(3, int(top) + self.fontMetrics().height() // 2 - 3, 6, 6)
                painter.setPen(QColor(140, 140, 140))
                painter.drawText(
                    12,
                    int(top),
                    self._lineNumberArea.width() - 28,
                    self.fontMetrics().height(),
                    QtCore.Qt.AlignCenter,
                    number,
                )
                if blockNumber in self._folds:
                    x = self._lineNumberArea.width() - 12
                    y = int(top) + self.fontMetrics().height() / 2
                    points = [(x, y - 4), (x, y + 4), (x + 5, y)] if any(start == block for start, _end in self._collapsed) else [(x - 2, y - 2), (x + 6, y - 2), (x + 2, y + 3)]
                    painter.setPen(QtCore.Qt.NoPen)
                    painter.setBrush(QColor(100, 100, 100))
                    painter.drawPolygon(QPolygonF([QPointF(px, py) for px, py in points]))

            block = block.next()
            top = bottom
            bottom = top + self.blockBoundingRect(block).height()
            blockNumber += 1

    def lineNumberAreaWidth(self) -> int:
        """Calculates the width needed to display line numbers."""
        digits = 1
        maximum: int = max(1, self.blockCount())
        while maximum >= 10:
            maximum /= 10  # type: ignore[assignment]
            digits += 1
        space = 34 + self.fontMetrics().horizontalAdvance("9") * digits
        minSpace = 34 + self.fontMetrics().horizontalAdvance("9") * 3
        return max(minSpace, space)

    def resizeEvent(self, e: QResizeEvent):
        super().resizeEvent(e)

        cr: QRect = self.contentsRect()
        self._lineNumberArea.setGeometry(QRect(cr.left(), cr.top(), self.lineNumberAreaWidth(), cr.height()))
        self._minimap.setGeometry(QRect(self.viewport().geometry().right() + 1, self.viewport().geometry().top(), 90, self.viewport().height()))

    def _updateLineNumberAreaWidth(self, newBlockCount: int):
        self.setViewportMargins(self.lineNumberAreaWidth(), 0, 90 if self._minimapEnabled else 0, 0)
        cr = self.contentsRect()
        self._lineNumberArea.setGeometry(QRect(cr.left(), cr.top(), self.lineNumberAreaWidth(), cr.height()))
        self._minimap.setGeometry(QRect(self.viewport().geometry().right() + 1, self.viewport().geometry().top(), 90, self.viewport().height()))

    def _highlightCurrentLine(self):
        """Highlights the current line in the text editor."""
        extraSelections: list[QTextEdit.ExtraSelection] = []

        if not self.isReadOnly():
            selection = QTextEdit.ExtraSelection()
            lineColor = QColor(255, 255, 220)
            selection.format.setBackground(lineColor)
            selection.format.setProperty(QTextFormat.FullWidthSelection, True)
            selection.cursor = self.textCursor()
            selection.cursor.clearSelection()
            extraSelections.append(selection)

        self.setExtraSelections(extraSelections)

    def _updateLineNumberArea(self, rect: QRect, dy: int):
        self._minimap.update()
        if dy:
            self._lineNumberArea.scroll(0, dy)
        else:
            self._lineNumberArea.update(0, rect.y(), self._lineNumberArea.width(), rect.height())

        if rect.contains(self.viewport().rect()):
            self._updateLineNumberAreaWidth(0)


class SyntaxHighlighter(QSyntaxHighlighter):
    KEYWORDS: ClassVar[list[str]] = [
        "return",
        "float",
        "int",
        "object",
        "location",
        "void",
        "effect",
        "action",
        "string",
        "vector",
        "talent",
        "if",
        "for",
        "while",
        "#include",
        "TRUE",
        "FALSE",
    ]

    OPERATORS: ClassVar[list[str]] = ["=", "==", "!=", "<", "<=", ">", ">=", "!", "\\+", "-", "/", "<<", ">>", "\\&", "\\|"]

    COMMENT_BLOCK_START = QRegExp("/\\*")
    COMMENT_BLOCK_END = QRegExp("\\*/")

    BRACES: ClassVar[list[str]] = ["\\{", "\\}", "\\(", "\\)", "\\[", "\\]"]

    def __init__(self, parent: QTextDocument, installation: HTInstallation):
        """Initializes the syntax highlighter.

        Args:
        ----
            parent: QTextDocument: The parent text document
            installation: HTInstallation: The installation object

        Initializes styles and rules:
            - Initializes style formats
            - Gets keywords, functions and constants from installation
            - Defines highlighting rules for keywords, functions, constants, operators, braces, numbers, strings and comments
            - Sets rules attribute with compiled rules.
        """
        super().__init__(parent)

        self.styles: dict[str, QTextCharFormat] = {
            "keyword": self.getCharFormat("blue"),
            "operator": self.getCharFormat("darkRed"),
            "numbers": self.getCharFormat("brown"),
            "comment": self.getCharFormat("gray", False, True),
            "string": self.getCharFormat("darkMagenta"),
            "brace": self.getCharFormat("darkRed"),
            "function": self.getCharFormat("darkGreen"),
            "constant": self.getCharFormat("darkBlue"),
        }

        functions: list[str] = [function.name for function in (TSL_FUNCTIONS if installation.tsl else KOTOR_FUNCTIONS)]
        constants: list[str] = [function.name for function in (TSL_CONSTANTS if installation.tsl else KOTOR_CONSTANTS)]

        rules: list[tuple[str, int, QTextCharFormat]] = []
        rules += [(r"\b%s\b" % w, 0, self.styles["keyword"]) for w in SyntaxHighlighter.KEYWORDS]
        rules += [(r"\b%s\b" % w, 0, self.styles["function"]) for w in functions]
        rules += [(r"\b%s\b" % w, 0, self.styles["constant"]) for w in constants]
        rules += [(o, 0, self.styles["operator"]) for o in SyntaxHighlighter.OPERATORS]
        rules += [(o, 0, self.styles["brace"]) for o in SyntaxHighlighter.BRACES]

        rules += [
            (r"\b[+-]?[0-9]+[lL]?\b", 0, self.styles["numbers"]),
            (r"\b[+-]?0[xX][0-9A-Fa-f]+[lL]?\b", 0, self.styles["numbers"]),
            (r"\b[+-]?[0-9]+(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?\b", 0, self.styles["numbers"]),
            (r"'[^'\\]*(\\.[^'\\]*)*'", 0, self.styles["string"]),
            (r'"[^"\\]*(\\.[^"\\]*)*"', 0, self.styles["string"]),
            (r"//[^\n]*", 0, self.styles["comment"]),
        ]

        self.rules: list[tuple[QtCore.QRegExp, int, QTextCharFormat]] = [(QtCore.QRegExp(pat), index, fmt) for (pat, index, fmt) in rules]

    def highlightBlock(self, text: str | None):
        """Highlights blocks of text."""
        if text is None:
            print("text cannot be None", "highlightBlock")
            return
        for expression, nth, format in self.rules:
            index: int = expression.indexIn(text, 0)

            while index >= 0:
                index = expression.pos(nth)
                length: int = len(expression.cap(nth))
                self.setFormat(index, length, format)
                index = expression.indexIn(text, index + length)

        self.setCurrentBlockState(0)

        startIndex = 0
        if self.previousBlockState() != 1:
            startIndex = SyntaxHighlighter.COMMENT_BLOCK_START.indexIn(text, startIndex)

        while startIndex >= 0:
            endIndex = SyntaxHighlighter.COMMENT_BLOCK_END.indexIn(text, startIndex)
            if endIndex == -1:
                self.setCurrentBlockState(1)
                commentLength = len(text) - startIndex
            else:
                commentLength = endIndex - startIndex + 2
            self.setFormat(startIndex, commentLength, self.styles["comment"])
            startIndex = SyntaxHighlighter.COMMENT_BLOCK_START.indexIn(text, startIndex + commentLength + 2)

    def getCharFormat(
        self,
        color: str | int,
        bold: bool = False,
        italic: bool = False,
    ) -> QTextCharFormat:
        # The docs on QColor are different from the stubs? Not sure why the static typing is erroring here.
        qcolor_obj = QColor(color)  # type: ignore[]
        textFormat = QTextCharFormat()
        textFormat.setForeground(qcolor_obj)
        textFormat.setFontWeight(QFont.Bold if bold else QFont.Normal)
        textFormat.setFontItalic(italic)
        return textFormat

