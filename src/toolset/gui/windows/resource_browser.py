from __future__ import annotations

from pathlib import Path

from PyQt5.QtCore import QDir, Qt
from PyQt5.QtWidgets import (QAbstractItemView, QFileSystemModel, QHBoxLayout, QLabel,
                             QMainWindow, QPushButton, QSplitter,
                             QTreeView, QVBoxLayout, QWidget)

from toolset.data.resources import ResourceCatalog, inspect_path
from toolset.gui.dialogs.asyncloader import AsyncLoader
from toolset.gui.dialogs.resource_locations import ResourceTable


class ResourceBrowser(QMainWindow):
    """Optional physical view. It is not an editor or an engine resource resolver."""

    def __init__(self, parent, installation):
        super().__init__(parent)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setWindowTitle(f"Physical resource browser — {installation.name} (read-only)")
        self.resize(1050, 650)
        self.installation = installation
        self._path: Path | None = None
        self._loading = False
        central = QWidget(self)
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        note = QLabel("Files are shown independently of engine search precedence. Select a file to list its physical records; duplicate archive entries remain separate. SAV parsing and nested archive editing are not supported.", self)
        note.setWordWrap(True)
        layout.addWidget(note)
        split = QSplitter(self)
        self.files = QFileSystemModel(self)
        self.files.setReadOnly(True)
        self.files.setFilter(QDir.AllEntries | QDir.NoDotAndDotDot | QDir.Hidden | QDir.System)
        self.tree = QTreeView(split)
        self.tree.setModel(self.files)
        self.tree.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tree.setDragEnabled(False)
        self.tree.setAcceptDrops(False)
        root = str(installation.path())
        self.tree.setRootIndex(self.files.setRootPath(root))
        self.table = ResourceTable(split)
        split.addWidget(self.tree)
        split.addWidget(self.table)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 2)
        layout.addWidget(split)
        controls = QHBoxLayout()
        self.refreshButton = QPushButton("Refresh selected file", self)
        self.refreshButton.setEnabled(False)
        self.refreshButton.clicked.connect(self.refresh)
        controls.addWidget(self.refreshButton)
        layout.addLayout(controls)
        self.tree.selectionModel().currentChanged.connect(self._selected)

    def _selected(self, current, _previous):
        if self._loading:
            return
        self.table.setRecords(())
        path = Path(self.files.filePath(current)) if current.isValid() else None
        self._path = path
        self.refreshButton.setEnabled(path is not None and path.is_file())
        if path is not None and path.is_file():
            self.refresh()

    def refresh(self):
        if self._path is None or self._loading:
            return
        path = self._path
        installation = self.installation
        self._loading = True
        self.tree.setEnabled(False)
        self.refreshButton.setEnabled(False)
        try:
            def task(report):
                catalog = installation.resource_catalog
                if path.suffix.lower() == ".bif":
                    catalog = ResourceCatalog.from_installation(installation, report, reload=True)
                return catalog, inspect_path(path, catalog, report)
            loader = AsyncLoader(self, f"Reading {path.name}", task, "Unable to inspect resource", with_progress=True)
            self.table.setRecords(())
            if loader.exec_():
                catalog, records = loader.value
                if catalog is not None:
                    installation.resource_catalog = catalog
                self.table.setRecords(records)
        finally:
            self._loading = False
            self.tree.setEnabled(True)
            self.refreshButton.setEnabled(True)

    def closeEvent(self, event):
        if self._loading:
            event.ignore()
        else:
            super().closeEvent(event)
