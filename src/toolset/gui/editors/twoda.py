from __future__ import annotations

from enum import IntEnum
from typing import TYPE_CHECKING

import pyperclip
from pykotor.resource.formats.twoda import TwoDA, read_2da, write_2da
from pykotor.resource.type import ResourceType
from PyQt5.QtCore import QSortFilterProxyModel, Qt
from PyQt5.QtGui import QKeySequence, QStandardItem, QStandardItemModel
from PyQt5.QtWidgets import (
    QAction, QHeaderView, QInputDialog, QMenu, QMessageBox, QStyledItemDelegate,
    QUndoCommand, QUndoStack, QWidget,
)
from toolset.gui.dialogs.find_replace import FindReplaceDialog
from toolset.gui.editor import Editor

if TYPE_CHECKING:
    import os

    from toolset.data.installation import HTInstallation


class CellEdit(QUndoCommand):
    def __init__(self, editor: TwoDAEditor, values: dict[tuple[int, int], str], text: str):
        super().__init__(text)
        self.editor = editor
        self.before = {key: editor.model.item(*key).text() for key in values}
        self.after = values

    def undo(self):
        self.editor._applyCells(self.before)

    def redo(self):
        self.editor._applyCells(self.after)


class RowEdit(QUndoCommand):
    def __init__(self, editor: TwoDAEditor, rows: list[tuple[int, list[QStandardItem]]], insert: bool, text: str):
        super().__init__(text)
        self.editor = editor
        self.rows = rows
        self.insert = insert

    def undo(self):
        self.editor._applyRows(self.rows, not self.insert)

    def redo(self):
        self.editor._applyRows(self.rows, self.insert)


class ColumnEdit(QUndoCommand):
    def __init__(self, editor, columns, insert, text):
        super().__init__(text)
        self.editor = editor
        self.columns = columns
        self.insert = insert
        table = editor.ui.twodaTable
        self.before = [(table.columnWidth(column), table.isColumnHidden(column)) for column in range(editor.model.columnCount())]
        self.after = list(self.before)
        self.header_before = editor.verticalHeaderOption, editor.verticalHeaderColumn
        self.header_after = self.header_before
        if insert:
            for column, _header, _items in columns:
                self.after.insert(column, (table.horizontalHeader().defaultSectionSize(), False))
        else:
            for column, header, _items in reversed(columns):
                del self.after[column]
                if self.header_before == (VerticalHeaderOption.CELL_VALUE, header.text()):
                    self.header_after = VerticalHeaderOption.NONE, ""

    def undo(self):
        self.editor._applyColumns(self.columns, not self.insert, self.before, self.header_before)

    def redo(self):
        self.editor._applyColumns(self.columns, self.insert, self.after, self.header_after)


class RenameColumn(QUndoCommand):
    def __init__(self, editor, column, name):
        super().__init__("Rename column")
        self.editor, self.column = editor, column
        self.before = editor.model.horizontalHeaderItem(column).text()
        self.after = name

    def undo(self):
        self.editor._renameColumn(self.column, self.before)

    def redo(self):
        self.editor._renameColumn(self.column, self.after)


class CellDelegate(QStyledItemDelegate):
    def setModelData(self, widget, model, index):
        editor = self.parent()
        source = editor.proxyModel.mapToSource(index)
        editor._changeCells({(source.row(), source.column()): widget.text()}, "Edit cell")


class TwoDAEditor(Editor):
    def __init__(self, parent: QWidget | None, installation: HTInstallation | None = None):
        supported = [ResourceType.TwoDA, ResourceType.TwoDA_CSV, ResourceType.TwoDA_JSON]
        super().__init__(parent, "2DA Editor", "none", supported, supported, installation)
        self.resize(635, 454)
        from toolset.uic.editors.twoda import Ui_MainWindow

        self.ui = Ui_MainWindow()
        self.ui.setupUi(self)
        self._setupMenus()
        self.history = QUndoStack(self)
        self.model = QStandardItemModel(self)
        self.proxyModel = SortFilterProxyModel(self)
        self.proxyModel.setSourceModel(self.model)
        self.ui.twodaTable.setModel(self.proxyModel)
        self.ui.twodaTable.setSortingEnabled(False)
        self.ui.twodaTable.setItemDelegate(CellDelegate(self))
        self.ui.filterBox.hide()
        self.findDialog = FindReplaceDialog(self)
        self.findDialog.findRequested.connect(self.findNext)
        self.findDialog.replaceRequested.connect(self.replaceCurrent)
        self.findDialog.replaceAllRequested.connect(self.replaceAll)
        self._setupSignals()
        self._setupActions()
        self.ui.twodaTable.selectionModel().selectionChanged.connect(self._updateActions)
        self.proxyModel.rowsInserted.connect(self._updateActions)
        self.proxyModel.rowsRemoved.connect(self._updateActions)
        self.new()

    def _setupSignals(self):
        self.ui.filterEdit.textEdited.connect(self.doFilter)
        self.ui.actionToggleFilter.triggered.connect(self.toggleFilter)
        self.ui.actionCopy.triggered.connect(self.copySelection)
        self.ui.actionPaste.triggered.connect(self.pasteSelection)
        self.ui.actionInsertRow.triggered.connect(self.insertRow)
        self.ui.actionDuplicateRow.triggered.connect(self.duplicateRow)
        self.ui.actionRemoveRows.triggered.connect(self.removeSelectedRows)
        self.ui.actionRedoRowLabels.triggered.connect(self.redoRowLabels)

    def _action(self, menu, text, shortcut, slot):
        action = QAction(text, self)
        action.setShortcut(shortcut)
        action.triggered.connect(slot)
        menu.addAction(action)
        return action

    def _setupActions(self):
        undo = self.history.createUndoAction(self, "Undo")
        undo.setShortcuts(QKeySequence.Undo)
        redo = self.history.createRedoAction(self, "Redo")
        redo.setShortcuts(QKeySequence.Redo)
        self.ui.menuEdit.insertActions(self.ui.actionCopy, [undo, redo])
        self.ui.menuEdit.insertSeparator(self.ui.actionCopy)
        self.ui.menuEdit.addSeparator()
        self._action(self.ui.menuEdit, "Find…", "Ctrl+F", self.showFind)
        self._action(self.ui.menuEdit, "Replace…", "Ctrl+H", lambda: self.showFind(replace=True))
        self._action(self.ui.menuEdit, "Find Next", "F3", lambda: self.findNext(False))
        self._action(self.ui.menuEdit, "Find Previous", "Shift+F3", lambda: self.findNext(True))
        self.ui.menuEdit.addSeparator()
        self.fillDownAction = self._action(self.ui.menuEdit, "Fill Down", "Ctrl+Shift+D", lambda: self.fillSelection(False))
        self.fillRightAction = self._action(self.ui.menuEdit, "Fill Right", "Ctrl+Shift+R", lambda: self.fillSelection(True))
        self.ui.menuEdit.addSeparator()
        self._action(self.ui.menuEdit, "Add Column…", "", self.addColumn)
        self.renameColumnAction = self._action(self.ui.menuEdit, "Rename Column…", "", self.renameSelectedColumn)
        self.removeColumnsAction = self._action(self.ui.menuEdit, "Remove Columns", "", self.removeSelectedColumns)
        self.ui.actionToggleFilter.setShortcut("Ctrl+Shift+F")
        self.columnsMenu = self.ui.menuView.addMenu("Columns")
        self.columnsMenu.aboutToShow.connect(self._columnMenu)
        self._action(self.ui.menuView, "Show All Columns", "", self.showAllColumns)
        self._action(self.ui.menuView, "Fit Columns to Contents", "", self.ui.twodaTable.resizeColumnsToContents)
        self.autoWidthsAction = self._action(self.ui.menuView, "Automatic Column Widths", "", self._automaticWidths)
        self.autoWidthsAction.setCheckable(True)
        self.ui.menuView.addSeparator()
        self.goRowAction = self._action(self.ui.menuView, "Go to Row…", "Ctrl+G", self.goToRow)
        self.goColumnAction = self._action(self.ui.menuView, "Go to Column…", "Ctrl+Shift+G", self.goToColumn)
        header = self.ui.twodaTable.horizontalHeader()
        header.setContextMenuPolicy(Qt.CustomContextMenu)
        header.customContextMenuRequested.connect(self._columnContextMenu)

    def _updateActions(self, *_):
        selected = bool(self._selection())
        for action in (self.ui.actionCopy, self.ui.actionPaste, self.ui.actionDuplicateRow,
                       self.ui.actionRemoveRows, self.fillDownAction, self.fillRightAction):
            action.setEnabled(selected)
        self.goRowAction.setEnabled(self.model.rowCount() > 0 and bool(self._visibleColumns()))
        self.goColumnAction.setEnabled(self.proxyModel.rowCount() > 0 and self.model.columnCount() > 0)
        self.ui.actionRedoRowLabels.setEnabled(self.model.rowCount() > 0)
        columns = self._selectedColumns()
        self.renameColumnAction.setEnabled(len(columns) == 1)
        self.removeColumnsAction.setEnabled(bool(columns))

    def _rowItems(self, values):
        items = [QStandardItem(value) for value in values]
        font = items[0].font()
        font.setBold(True)
        items[0].setFont(font)
        items[0].setBackground(self.palette().midlight())
        return items

    def load(self, filepath: os.PathLike | str, resref: str, restype: ResourceType, data: bytes):
        super().load(filepath, resref, restype, data)
        try:
            self._load_main(data)
        except ValueError:
            QMessageBox(QMessageBox.Critical, "Failed to load file", "Failed to open or load file data.").exec_()
            self.new()

    def _load_main(self, data: bytes):
        twoda = read_2da(data)
        self._twoda_version = twoda.version
        self._twoda_default = twoda.default
        headers = list(twoda.get_headers())
        self.history.clear()
        self.model.clear()
        self.model.setHorizontalHeaderLabels(["", *headers])
        for row in range(twoda.get_height()):
            self.model.appendRow(self._rowItems([str(twoda.get_label(row)), *[twoda.get_cell(row, header) for header in headers]]))
        self._resetView()

    def build(self) -> tuple[bytes, bytes]:
        twoda = TwoDA(version=self._twoda_version, default=self._twoda_default)
        headers = [self.model.horizontalHeaderItem(column).text() for column in range(1, self.model.columnCount())]
        for header in headers:
            twoda.add_column(header)
        for row in range(self.model.rowCount()):
            twoda.add_row()
            twoda.set_label(row, self.model.item(row, 0).text())
            for column, header in enumerate(headers, 1):
                twoda.set_cell(row, header, self.model.item(row, column).text())
        data = bytearray()
        write_2da(twoda, data, self._restype)
        return bytes(data), b""

    def new(self):
        super().new()
        self.history.clear()
        self._twoda_version = "V2.b"
        self._twoda_default = ""
        self.model.clear()
        self.model.setHorizontalHeaderLabels([""])
        self._resetView()

    def _resetView(self):
        self.ui.filterEdit.clear()
        self.doFilter("")
        self.verticalHeaderOption = VerticalHeaderOption.NONE
        self.verticalHeaderColumn = ""
        self._rowHeaderMenu()
        self.autoWidthsAction.setChecked(False)
        self._automaticWidths(False)
        self.showAllColumns()
        self.ui.twodaTable.resizeColumnsToContents()
        self.resetVerticalHeaders()
        self.findDialog.status.clear()
        self._updateActions()

    def _rowHeaderMenu(self):
        self.ui.menuSetRowHeader.clear()
        for text, option in (("None", VerticalHeaderOption.NONE), ("Row Index", VerticalHeaderOption.ROW_INDEX),
                             ("Row Label", VerticalHeaderOption.ROW_LABEL)):
            action = self.ui.menuSetRowHeader.addAction(text)
            action.triggered.connect(lambda _, option=option: self.setVerticalHeaderOption(option))
        self.ui.menuSetRowHeader.addSeparator()
        for column in range(1, self.model.columnCount()):
            text = self.model.horizontalHeaderItem(column).text()
            action = self.ui.menuSetRowHeader.addAction(text.replace("&", "&&"))
            action.triggered.connect(lambda _, text=text: self.setVerticalHeaderOption(VerticalHeaderOption.CELL_VALUE, text))

    def _changeCells(self, values: dict[tuple[int, int], str], text: str):
        changed = {key: value for key, value in values.items() if self.model.item(*key).text() != value}
        if changed:
            self.history.push(CellEdit(self, changed, text))

    def _applyCells(self, values):
        self.proxyModel.setDynamicSortFilter(False)
        for key, value in values.items():
            self.model.item(*key).setText(value)
        self._finishEdit()

    def _applyRows(self, rows, insert):
        self.proxyModel.setDynamicSortFilter(False)
        if insert:
            for row, items in rows:
                self.model.insertRow(row, [item.clone() for item in items])
        else:
            for row, _ in reversed(rows):
                self.model.removeRow(row)
        self._finishEdit()

    def _applyColumns(self, columns, insert, view_state, header_state):
        self.proxyModel.setDynamicSortFilter(False)
        if insert:
            for column, header, items in columns:
                self.model.insertColumn(column, [item.clone() for item in items])
                self.model.setHorizontalHeaderItem(column, header.clone())
        else:
            for column, _header, _items in reversed(columns):
                self.model.removeColumn(column)
        self.verticalHeaderOption, self.verticalHeaderColumn = header_state
        for column, (width, hidden) in enumerate(view_state):
            self.ui.twodaTable.setColumnHidden(column, hidden)
            self.ui.twodaTable.setColumnWidth(column, width)
        self._rowHeaderMenu()
        self._finishEdit()

    def _renameColumn(self, column, name):
        previous = self.model.horizontalHeaderItem(column).text()
        self.model.horizontalHeaderItem(column).setText(name)
        if self.verticalHeaderOption == VerticalHeaderOption.CELL_VALUE and self.verticalHeaderColumn == previous:
            self.verticalHeaderColumn = name
        self._rowHeaderMenu()
        self.resetVerticalHeaders()

    def _selectedColumns(self):
        return sorted({index.column() for index in self._selection() if index.column() > 0})

    def _columnName(self, title, current=None):
        name, accepted = QInputDialog.getText(self, title, "Column name:", text="" if current is None else current)
        if not accepted:
            return None
        headers = [self.model.horizontalHeaderItem(column).text() for column in range(1, self.model.columnCount())]
        if current is not None:
            headers.remove(current)
        if not name or any(character.isspace() or character == "\0" for character in name) or name in headers:
            QMessageBox.warning(self, "Invalid column name", "Enter a nonempty, unique column name without whitespace or NUL characters. Names are case-sensitive.")
            return None
        return name

    def addColumn(self):
        name = self._columnName("Add Column")
        if name is not None:
            column = self.model.columnCount()
            items = [QStandardItem("") for _ in range(self.model.rowCount())]
            self.history.push(ColumnEdit(self, [(column, QStandardItem(name), items)], True, "Add column"))

    def renameSelectedColumn(self):
        self.renameColumn(self._selectedColumns()[0])

    def renameColumn(self, column):
        previous = self.model.horizontalHeaderItem(column).text()
        name = self._columnName("Rename Column", previous)
        if name is not None and name != previous:
            self.history.push(RenameColumn(self, column, name))

    def removeSelectedColumns(self):
        self.removeColumns(self._selectedColumns())

    def removeColumns(self, columns):
        values = [(column, self.model.horizontalHeaderItem(column).clone(),
                   [self.model.item(row, column).clone() for row in range(self.model.rowCount())]) for column in columns]
        self.history.push(ColumnEdit(self, values, False, "Remove columns"))

    def _finishEdit(self):
        self.resetVerticalHeaders()
        self.proxyModel.setDynamicSortFilter(True)
        self.proxyModel.invalidateFilter()
        self._updateActions()

    def doFilter(self, text: str):
        self.proxyModel.setFilterFixedString(text)
        self._updateActions()

    def toggleFilter(self):
        visible = not self.ui.filterBox.isVisible()
        self.ui.filterBox.setVisible(visible)
        self.doFilter(self.ui.filterEdit.text() if visible else "")
        if visible:
            self.ui.filterEdit.setFocus()

    def _visibleColumns(self):
        return [column for column in range(self.model.columnCount()) if not self.ui.twodaTable.isColumnHidden(column)]

    def _selection(self):
        return sorted((index for index in self.ui.twodaTable.selectedIndexes()
                       if not self.ui.twodaTable.isColumnHidden(index.column())), key=lambda index: (index.row(), index.column()))

    def copySelection(self):
        indexes = self._selection()
        rows = range(min(index.row() for index in indexes), max(index.row() for index in indexes) + 1)
        left, right = min(index.column() for index in indexes), max(index.column() for index in indexes)
        columns = [column for column in self._visibleColumns() if left <= column <= right]
        pyperclip.copy("\n".join("\t".join(self.proxyModel.index(row, column).data() for column in columns) for row in rows))

    def pasteSelection(self):
        indexes = self._selection()
        top = min(index.row() for index in indexes)
        left = min(index.column() for index in indexes)
        columns = self._visibleColumns()
        columns = columns[columns.index(left):]
        values = {}
        for row, text in zip(range(top, self.proxyModel.rowCount()), pyperclip.paste().replace("\r\n", "\n").replace("\r", "\n").split("\n")):
            for column, value in zip(columns, text.split("\t")):
                source = self.proxyModel.mapToSource(self.proxyModel.index(row, column))
                values[source.row(), source.column()] = value
        self._changeCells(values, "Paste cells")

    def insertRow(self):
        row = self.model.rowCount()
        items = self._rowItems([str(row), *["" for _ in range(1, self.model.columnCount())]])
        self.history.push(RowEdit(self, [(row, items)], True, "Insert row"))

    def duplicateRow(self):
        source = self.proxyModel.mapToSource(self._selection()[0]).row()
        row = self.model.rowCount()
        items = [self.model.item(source, column).clone() for column in range(self.model.columnCount())]
        items[0].setText(str(row))
        self.history.push(RowEdit(self, [(row, items)], True, "Duplicate row"))

    def removeSelectedRows(self):
        selected = sorted({self.proxyModel.mapToSource(index).row() for index in self._selection()})
        rows = [(row, [self.model.item(row, column).clone() for column in range(self.model.columnCount())]) for row in selected]
        self.history.push(RowEdit(self, rows, False, "Remove rows"))

    def redoRowLabels(self):
        self._changeCells({(row, 0): str(row) for row in range(self.model.rowCount())}, "Renumber row labels")

    def fillSelection(self, right: bool):
        values, first = {}, {}
        for index in self._selection():
            group = index.row() if right else index.column()
            first.setdefault(group, index.data())
            source = self.proxyModel.mapToSource(index)
            values[source.row(), source.column()] = first[group]
        self._changeCells(values, "Fill right" if right else "Fill down")

    def showFind(self, *, replace: bool = False):
        self.findDialog.openFor(replace=replace)

    def findNext(self, backwards: bool = False):
        if not self.findDialog.findEdit.text():
            self.showFind()
            return
        columns = self._visibleColumns()
        current = self.ui.twodaTable.currentIndex()
        start = (current.row() * len(columns) + columns.index(current.column())
                 if current.isValid() and current.column() in columns else (0 if backwards else -1))
        cell_count = self.proxyModel.rowCount() * len(columns)
        expression = self.findDialog.expression()
        for offset in range(1, cell_count + 1):
            row, visible_column = divmod((start + (-offset if backwards else offset)) % cell_count, len(columns))
            column = columns[visible_column]
            index = self.proxyModel.index(row, column)
            if expression.search(index.data()):
                self.ui.twodaTable.setCurrentIndex(index)
                self.ui.twodaTable.scrollTo(index)
                self.findDialog.status.setText("Match found in visible cells.")
                return
        self.findDialog.status.setText("No matches in visible cells.")

    def replaceCurrent(self):
        index = self.ui.twodaTable.currentIndex()
        if not index.isValid() or self.ui.twodaTable.isColumnHidden(index.column()):
            self.findNext()
            return
        value, count = self.findDialog.expression().subn(lambda _: self.findDialog.replaceEdit.text(), index.data(), count=1)
        if count:
            source = self.proxyModel.mapToSource(index)
            self._changeCells({(source.row(), source.column()): value}, "Replace text")
        self.findNext()

    def replaceAll(self):
        expression = self.findDialog.expression()
        values, count = {}, 0
        columns = self._visibleColumns()
        for row in range(self.proxyModel.rowCount()):
            for column in columns:
                index = self.proxyModel.index(row, column)
                value, matches = expression.subn(lambda _: self.findDialog.replaceEdit.text(), index.data())
                if matches:
                    source = self.proxyModel.mapToSource(index)
                    values[source.row(), source.column()] = value
                    count += matches
        self._changeCells(values, "Replace all")
        self.findDialog.status.setText(f"Replaced {count} occurrence(s) in visible cells.")

    def _columnMenu(self):
        self.columnsMenu.clear()
        for column in range(self.model.columnCount()):
            name = "Row Label" if column == 0 else self.model.horizontalHeaderItem(column).text()
            action = self.columnsMenu.addAction(name.replace("&", "&&"))
            action.setCheckable(True)
            action.setChecked(not self.ui.twodaTable.isColumnHidden(column))
            action.toggled.connect(lambda visible, column=column: self._showColumn(column, visible))

    def _columnContextMenu(self, point):
        column = self.ui.twodaTable.horizontalHeader().logicalIndexAt(point)
        if column < 0:
            return
        menu = QMenu(self)
        menu.addAction("Hide Column", lambda: self._showColumn(column, False))
        menu.addAction("Fit Column to Contents", lambda: self.ui.twodaTable.resizeColumnToContents(column))
        menu.addAction("Show All Columns", self.showAllColumns)
        if column > 0:
            menu.addSeparator()
            menu.addAction("Rename Column…", lambda: self.renameColumn(column))
            menu.addAction("Remove Column", lambda: self.removeColumns([column]))
        menu.exec_(self.ui.twodaTable.horizontalHeader().mapToGlobal(point))
        menu.deleteLater()

    def _showColumn(self, column, visible):
        self.ui.twodaTable.setColumnHidden(column, not visible)
        self._updateActions()

    def showAllColumns(self):
        for column in range(self.model.columnCount()):
            self.ui.twodaTable.setColumnHidden(column, False)
        self._updateActions()

    def _automaticWidths(self, enabled):
        self.ui.twodaTable.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents if enabled else QHeaderView.Interactive)

    def goToRow(self):
        row, accepted = QInputDialog.getInt(self, "Go to Row", "Row index (starting at 0):", 0, 0, self.model.rowCount() - 1)
        if accepted:
            self.ui.filterEdit.clear()
            self.doFilter("")
            index = self.proxyModel.mapFromSource(self.model.index(row, self._visibleColumns()[0]))
            self.ui.twodaTable.setCurrentIndex(index)
            self.ui.twodaTable.scrollTo(index)
            self.ui.twodaTable.setFocus()

    def goToColumn(self):
        names = [f"{column}: {self.model.horizontalHeaderItem(column).text() if column else 'Row Label'}" for column in range(self.model.columnCount())]
        name, accepted = QInputDialog.getItem(self, "Go to Column", "Column:", names, 0, False)
        if accepted:
            column = names.index(name)
            self._showColumn(column, True)
            index = self.proxyModel.index(max(0, self.ui.twodaTable.currentIndex().row()), column)
            self.ui.twodaTable.setCurrentIndex(index)
            self.ui.twodaTable.scrollTo(index)
            self.ui.twodaTable.setFocus()

    def setVerticalHeaderOption(self, option: VerticalHeaderOption, column: str = ""):
        self.verticalHeaderOption = option
        self.verticalHeaderColumn = column
        self.resetVerticalHeaders()

    def resetVerticalHeaders(self):
        self.ui.twodaTable.verticalHeader().setStyleSheet("")
        if self.verticalHeaderOption == VerticalHeaderOption.ROW_INDEX:
            headers = [str(row) for row in range(self.model.rowCount())]
        elif self.verticalHeaderOption == VerticalHeaderOption.ROW_LABEL:
            headers = [self.model.item(row, 0).text() for row in range(self.model.rowCount())]
        elif self.verticalHeaderOption == VerticalHeaderOption.CELL_VALUE:
            column = [self.model.horizontalHeaderItem(i).text() for i in range(self.model.columnCount())].index(self.verticalHeaderColumn)
            headers = [self.model.item(row, column).text() for row in range(self.model.rowCount())]
        else:
            self.ui.twodaTable.verticalHeader().setStyleSheet("QHeaderView::section { color: transparent; } QHeaderView::section:checked { color: #000; }")
            headers = ["⯈" for _ in range(self.model.rowCount())]
        self.model.setVerticalHeaderLabels(headers)


class SortFilterProxyModel(QSortFilterProxyModel):
    def filterAcceptsRow(self, sourceRow, sourceParent) -> bool:
        pattern = self.filterRegExp().pattern().lower()
        return not pattern or any(pattern in self.sourceModel().index(sourceRow, column, sourceParent).data().lower()
                                  for column in range(self.sourceModel().columnCount()))


class VerticalHeaderOption(IntEnum):
    ROW_INDEX = 0
    ROW_LABEL = 1
    CELL_VALUE = 2
    NONE = 3
