from __future__ import annotations

from PyQt5.QtCore import QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt
from PyQt5.QtWidgets import (QAbstractItemView, QCheckBox, QDialog, QHBoxLayout, QLabel,
                             QLineEdit, QMessageBox, QPlainTextEdit, QPushButton,
                             QTableView, QTabWidget, QVBoxLayout)

from toolset.data.reference_search import search_references
from toolset.gui.dialogs.asyncloader import AsyncLoader
from toolset.gui.dialogs.resource_locations import ResourceByteView


class ReferenceModel(QAbstractTableModel):
    HEADERS = ("Resource", "Reference kind", "Field / cell", "Value", "Physical source", "Record")

    def __init__(self, parent):
        super().__init__(parent)
        self.hits = ()

    def setHits(self, hits):
        self.beginResetModel()
        self.hits = hits
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.hits)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.HEADERS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            return self.HEADERS[section]

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        hit = self.hits[index.row()]
        if role == Qt.UserRole:
            return hit
        if role == Qt.ToolTipRole:
            return f"{hit.resource.display}\n{hit.field}\n{hit.value}"
        if role == Qt.DisplayRole:
            return (hit.resource.filename, hit.kind, hit.field, hit.value,
                    str(hit.resource.path), hit.resource.record)[index.column()]


class ReferenceSearchDialog(QDialog):
    def __init__(self, parent, installation, filepath=None, query=""):
        super().__init__(parent)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setWindowTitle(f"Find references — {installation.name}")
        self.resize(1050, 600)
        self.installation, self.filepath = installation, filepath
        layout = QVBoxLayout(self)
        controls = QHBoxLayout()
        self.query = QLineEdit(query, self)
        self.query.setPlaceholderText("Resource name or tag, without a file extension")
        self.searchButton = QPushButton("Search", self)
        self.searchButton.setEnabled(bool(query))
        controls.addWidget(self.query)
        controls.addWidget(self.searchButton)
        layout.addLayout(controls)
        options = QHBoxLayout()
        self.caseSensitive = QCheckBox("Match case", self)
        self.partial = QCheckBox("Partial matches", self)
        self.models = QCheckBox("Include models and layouts (slower)", self)
        for option in (self.caseSensitive, self.partial, self.models):
            options.addWidget(option)
        layout.addLayout(options)
        note = QLabel("Searches saved GFF ResRefs/strings, 2DA cells, NSS string literals and NCS string constants. String matches are candidates, not proof of a runtime dependency; computed names are not inferred.", self)
        note.setWordWrap(True)
        layout.addWidget(note)
        self.tabs = QTabWidget(self)
        self.model = ReferenceModel(self)
        self.proxy = QSortFilterProxyModel(self)
        self.proxy.setSourceModel(self.model)
        self.view = QTableView(self)
        self.view.setModel(self.proxy)
        self.view.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.view.setSelectionMode(QAbstractItemView.SingleSelection)
        self.view.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.view.setSortingEnabled(True)
        self.errors = QPlainTextEdit(self)
        self.errors.setReadOnly(True)
        self.tabs.addTab(self.view, "Matches")
        self.tabs.addTab(self.errors, "Read / parse errors")
        layout.addWidget(self.tabs)
        self.status = QLabel("Search reads saved files, including separate physical archive records.", self)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        buttons = QHBoxLayout()
        self.openButton = QPushButton("Open in new editor", self)
        self.bytesButton = QPushButton("View bytes (read-only)", self)
        self.openButton.setEnabled(False)
        self.bytesButton.setEnabled(False)
        buttons.addWidget(self.openButton)
        buttons.addWidget(self.bytesButton)
        layout.addLayout(buttons)
        self.query.textChanged.connect(lambda text: self.searchButton.setEnabled(bool(text)))
        self.searchButton.clicked.connect(self.search)
        self.query.returnPressed.connect(lambda: self.search() if self.query.text() else None)
        self.view.selectionModel().selectionChanged.connect(self._selectionChanged)
        self.view.doubleClicked.connect(lambda _index: self.openResource())
        self.openButton.clicked.connect(self.openResource)
        self.bytesButton.clicked.connect(lambda: ResourceByteView(self, self.selected().resource).exec_())

    def selected(self):
        rows = self.view.selectionModel().selectedRows()
        return None if not rows else self.proxy.data(rows[0], Qt.UserRole)

    def _selectionChanged(self, *_):
        enabled = self.selected() is not None
        self.openButton.setEnabled(enabled)
        self.bytesButton.setEnabled(enabled)

    def search(self):
        query, case, partial, models = self.query.text(), self.caseSensitive.isChecked(), self.partial.isChecked(), self.models.isChecked()
        self.model.setHits(())
        self.errors.clear()
        self.tabs.setTabText(1, "Read / parse errors")
        loader = AsyncLoader(self, "Searching references",
                             lambda report: search_references(self.installation, self.filepath, query,
                                                               case_sensitive=case, partial=partial, models=models, report=report),
                             "Unable to search references", with_progress=True)
        if not loader.exec_():
            self.status.setText("Search did not complete.")
            return
        base, hits, errors, count = loader.value
        self.installation.resource_catalog = base
        self.model.setHits(hits)
        self.errors.setPlainText("\n\n".join(f"{record.display}\n{error}" for record, error in errors))
        self.tabs.setTabText(1, f"Read / parse errors ({len(errors)})")
        self.status.setText(f"{len(hits)} match(es) for {query!r}; {count} resources searched; {len(errors)} read / parse error(s).")

    def openResource(self):
        from toolset.gui.editor import Editor
        from toolset.utils.window import openResourceEditor

        hit = self.selected()
        if hit is None:
            return
        row = hit.resource
        loader = AsyncLoader(self, "Reading resource", row.read_for_editing, "Unable to open resource")
        if not loader.exec_():
            return
        try:
            _path, window = openResourceEditor(row.path, row.name, row.restype, loader.value, self.installation, self.parent())
        except Exception as error:
            QMessageBox.critical(self, "Unable to open resource", f"{row.display}\n{error}")
            return
        parent = self.parent()
        if isinstance(window, Editor) and isinstance(parent, Editor):
            window.savedFile.connect(parent.resourceSaved)
            window.childSavedFile.connect(parent.resourceSaved)
