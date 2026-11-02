from __future__ import annotations

from PyQt5.QtCore import QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt, QUrl
from PyQt5.QtGui import QDesktopServices
from PyQt5.QtWidgets import (QAbstractItemView, QApplication, QDialog, QDialogButtonBox,
                             QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPlainTextEdit,
                             QPushButton, QTableView, QVBoxLayout, QWidget)

from toolset.data.resources import ResourceLocation
from toolset.gui.dialogs.asyncloader import AsyncLoader


class ResourceLocationModel(QAbstractTableModel):
    HEADERS = ("Resource", "Type", "Physical source", "Offset", "Bytes", "Record")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.records: tuple[ResourceLocation, ...] = ()

    def setRecords(self, records):
        self.beginResetModel()
        self.records = tuple(records)
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.records)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.HEADERS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal and 0 <= section < len(self.HEADERS):
            return self.HEADERS[section]
        return None

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self.records):
            return None
        row = self.records[index.row()]
        if role == Qt.UserRole:
            return row
        if role == Qt.ToolTipRole:
            return row.display
        if role == Qt.DisplayRole:
            return (row.name, row.restype.extension, str(row.path), row.offset, row.size, row.record)[index.column()]
        return None


class ResourceTable(QWidget):
    """Physical locations only: no edit/save actions and no precedence claims."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        self.filter = QLineEdit(self)
        self.filter.setPlaceholderText("Filter resource names, types, or physical paths")
        self.model = ResourceLocationModel(self)
        self.proxy = QSortFilterProxyModel(self)
        self.proxy.setSourceModel(self.model)
        self.proxy.setFilterKeyColumn(-1)
        self.proxy.setFilterCaseSensitivity(Qt.CaseInsensitive)
        self.filter.textChanged.connect(self.proxy.setFilterFixedString)
        self.view = QTableView(self)
        self.view.setModel(self.proxy)
        self.view.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.view.setSelectionMode(QAbstractItemView.SingleSelection)
        self.view.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.view.setSortingEnabled(True)
        self.view.horizontalHeader().setStretchLastSection(True)
        self.count = QLabel(self)
        layout.addWidget(self.filter)
        layout.addWidget(self.view)
        layout.addWidget(self.count)
        buttons = QHBoxLayout()
        self.copyButton = QPushButton("Copy location", self)
        self.folderButton = QPushButton("Open containing folder", self)
        self.previewButton = QPushButton("View bytes (read-only)", self)
        for button in (self.copyButton, self.folderButton, self.previewButton):
            buttons.addWidget(button)
        layout.addLayout(buttons)
        self.copyButton.clicked.connect(self.copyLocation)
        self.folderButton.clicked.connect(self.openFolder)
        self.previewButton.clicked.connect(self.preview)
        self.view.doubleClicked.connect(lambda _index: self.preview())
        self.view.selectionModel().selectionChanged.connect(self._selectionChanged)
        self.filter.textChanged.connect(self._updateCount)
        self._selectionChanged()
        self._updateCount()

    def setRecords(self, records):
        self.model.setRecords(records)
        self._selectionChanged()
        self._updateCount()

    def _updateCount(self, *_):
        self.count.setText(f"{self.proxy.rowCount()} shown / {len(self.model.records)} physical records. No engine search order is implied.")

    def selected(self):
        rows = self.view.selectionModel().selectedRows()
        return None if not rows else self.proxy.data(rows[0], Qt.UserRole)

    def _selectionChanged(self, *_):
        enabled = self.selected() is not None
        for button in (self.copyButton, self.folderButton, self.previewButton):
            button.setEnabled(enabled)

    def copyLocation(self):
        row = self.selected()
        if row is not None:
            QApplication.clipboard().setText(row.display)

    def openFolder(self):
        row = self.selected()
        if row is not None and not QDesktopServices.openUrl(QUrl.fromLocalFile(str(row.path.parent))):
            QMessageBox.warning(self, "Unable to open folder", str(row.path.parent))

    def preview(self):
        row = self.selected()
        if row is not None:
            ResourceByteView(self, row).exec_()


class ResourceByteView(QDialog):
    """Paged view reads exact byte ranges, including otherwise hidden duplicates."""
    PAGE_SIZE = 4096

    def __init__(self, parent, record: ResourceLocation):
        super().__init__(parent)
        self.record = record
        self.position = 0
        self.setWindowTitle(f"Read-only: {record.filename}")
        self.resize(800, 550)
        layout = QVBoxLayout(self)
        self.info = QLabel(record.display, self)
        self.info.setWordWrap(True)
        self.text = QPlainTextEdit(self)
        self.text.setReadOnly(True)
        layout.addWidget(self.info)
        layout.addWidget(self.text)
        controls = QHBoxLayout()
        self.previous = QPushButton("Previous page", self)
        self.next = QPushButton("Next page", self)
        self.previous.clicked.connect(lambda: self.loadPage(max(0, self.position - self.PAGE_SIZE)))
        self.next.clicked.connect(lambda: self.loadPage(self.position + self.PAGE_SIZE))
        controls.addWidget(self.previous)
        controls.addWidget(self.next)
        layout.addLayout(controls)
        close = QDialogButtonBox(QDialogButtonBox.Close, self)
        close.rejected.connect(self.reject)
        layout.addWidget(close)
        self.loadPage(0)

    def loadPage(self, position):
        loader = AsyncLoader(self, "Reading resource bytes", lambda: self.record.read(position, self.PAGE_SIZE), "Unable to read resource")
        if not loader.exec_():
            self.text.clear()
            self.previous.setEnabled(False)
            self.next.setEnabled(False)
            return
        data = loader.value
        lines = []
        for offset in range(0, len(data), 16):
            block = data[offset:offset + 16]
            printable = "".join(chr(value) if 32 <= value < 127 else "." for value in block)
            lines.append(f"{position + offset:08X}  {block.hex(' '):47}  {printable}")
        self.position = position
        self.text.setPlainText("\n".join(lines) if data else "(Empty resource)")
        self.info.setText(f"{self.record.display}\nBytes {position}–{position + len(data)} of {self.record.size}; offsets below are relative to this resource.")
        self.previous.setEnabled(position > 0)
        self.next.setEnabled(position + len(data) < self.record.size)


class ResourceLocationsDialog(QDialog):
    def __init__(self, parent, title: str, records):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(960, 500)
        layout = QVBoxLayout(self)
        self.table = ResourceTable(self)
        self.table.setRecords(records)
        layout.addWidget(self.table)
        note = QLabel("All indexed locations are shown, including other modules and duplicate archive records. A missing result does not make a custom ResRef invalid.", self)
        note.setWordWrap(True)
        layout.addWidget(note)
        close = QDialogButtonBox(QDialogButtonBox.Close, self)
        close.rejected.connect(self.reject)
        layout.addWidget(close)
