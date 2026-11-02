from __future__ import annotations

from PyQt5.QtWidgets import QCheckBox, QDockWidget, QHBoxLayout, QMessageBox, QPushButton, QVBoxLayout, QWidget

from toolset.data.resources import ResourceCatalog
from toolset.gui.dialogs.asyncloader import AsyncLoader
from toolset.gui.dialogs.resource_locations import ResourceTable


class ResourceBrowser(QDockWidget):
    def __init__(self, editor):
        super().__init__("Resources", editor)
        self.setObjectName("resourceBrowser")
        self._editor = editor
        self._catalog = None
        self._sourceCatalog = None
        body = QWidget(self)
        self.setWidget(body)
        layout = QVBoxLayout(body)
        controls = QHBoxLayout()
        self.compatible = QCheckBox("Current editor's formats only", body)
        self.refreshButton = QPushButton("Refresh", body)
        controls.addWidget(self.compatible)
        controls.addWidget(self.refreshButton)
        layout.addLayout(controls)
        self.table = ResourceTable(body)
        self.table.view.setMinimumWidth(360)
        self.table.count.setWordWrap(True)
        layout.addWidget(self.table)
        self.openButton = QPushButton("Open in new editor", body)
        self.openButton.setEnabled(False)
        layout.addWidget(self.openButton)
        self.refreshButton.clicked.connect(lambda: self.refresh(True))
        self.compatible.toggled.connect(self._setRecords)
        self.openButton.clicked.connect(self.openResource)
        self.table.view.doubleClicked.disconnect()
        self.table.view.doubleClicked.connect(lambda _index: self.openResource())
        self.table.view.selectionModel().selectionChanged.connect(
            lambda *_: self.openButton.setEnabled(self.table.selected() is not None))
        editor.loadedFile.connect(self.invalidate)
        editor.newFile.connect(self.invalidate)
        editor.savedFile.connect(self.invalidate)
        editor.childSavedFile.connect(self.invalidate)

    def invalidate(self, *_):
        self._catalog = None
        self.table.setRecords(())
        self.openButton.setEnabled(False)
        self.table.count.setText("Click Refresh to list resources for this document.")

    def ensureResources(self):
        installation = self._editor._installation
        source = None if installation is None else installation.resource_catalog
        if self._catalog is None or source is not self._sourceCatalog:
            self.refresh()
        self.table.filter.setFocus()

    def refresh(self, reload=False):
        installation, path = self._editor._installation, self._editor._filepath
        source = None if installation is None else installation.resource_catalog

        def collect(report):
            if installation is None:
                base = ResourceCatalog()
            elif source is None or reload:
                base = ResourceCatalog.from_installation(installation, report, reload=True)
            else:
                base = source
            return base, base.with_context(path, report)

        self.invalidate()
        loader = AsyncLoader(self, "Indexing resources", collect, "Unable to list resources", with_progress=True)
        if not loader.exec_():
            return
        base, self._catalog = loader.value
        if installation is not None:
            installation.resource_catalog = base
            self._sourceCatalog = base
        else:
            self._sourceCatalog = None
        self._setRecords()

    def _setRecords(self, *_):
        if self._catalog is not None:
            records = self._catalog.locations
            if self.compatible.isChecked():
                records = (record for record in records if record.restype in self._editor._readSupported)
            self.table.setRecords(records)
        self.openButton.setEnabled(False)

    def openResource(self):
        from toolset.gui.editor import Editor
        from toolset.utils.window import openResourceEditor

        record = self.table.selected()
        if record is None:
            return
        loader = AsyncLoader(self, "Reading resource", record.read_for_editing, "Unable to open resource")
        if not loader.exec_():
            return
        try:
            _path, window = openResourceEditor(record.path, record.name, record.restype, loader.value,
                                               self._editor._installation, self._editor)
        except Exception as error:
            QMessageBox.critical(self, "Unable to open resource", f"{record.display}\n{error}")
            return
        if isinstance(window, Editor):
            window.savedFile.connect(self._editor.resourceSaved)
            window.childSavedFile.connect(self._editor.resourceSaved)
