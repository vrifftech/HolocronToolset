from __future__ import annotations

from PyQt5 import QtCore
from PyQt5.QtCore import QSignalBlocker, QStringListModel, Qt
from PyQt5.QtWidgets import QComboBox, QCompleter

from pykotor.resource.type import ResourceType
from toolset.data.resources import ResourceCatalog
from toolset.gui.dialogs.asyncloader import AsyncLoader
from toolset.gui.dialogs.resource_locations import ResourceLocationsDialog


class ResRefComboBox(QComboBox):
    """Editable suggestions. Loading/refreshing choices never changes the document value."""
    textEdited = QtCore.pyqtSignal(str)
    textChanged = QtCore.pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setEditable(True)
        self.setInsertPolicy(QComboBox.NoInsert)
        self.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.setMinimumContentsLength(12)
        self.setCompleter(None)
        self._suggestions = QStringListModel(self)
        self._completion = QCompleter(self._suggestions, self)
        self._completion.setCaseSensitivity(Qt.CaseInsensitive)
        self._completion.setFilterMode(Qt.MatchContains)
        self._completion.setCompletionMode(QCompleter.PopupCompletion)
        self.lineEdit().setCompleter(self._completion)
        self._completion.activated[str].connect(self._choose)
        self.lineEdit().textEdited.connect(self._edited)
        self.lineEdit().textChanged.connect(self.textChanged.emit)
        self.lineEdit().setContextMenuPolicy(Qt.CustomContextMenu)
        self.lineEdit().customContextMenuRequested.connect(self._contextMenu)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(lambda point: self._contextMenu(self.lineEdit().mapFrom(self, point)))
        self._installation = None
        self._filepath = lambda: None
        self._catalog = None
        self._failed = False
        self._source_catalog = None
        self._source_path = None
        self._types = ()
        self.setToolTip("Type any resource reference, or choose a filtered suggestion. Right-click for all physical locations. Custom, missing, and empty references remain editable.")

    def bindResources(self, installation, filepath):
        self._installation = installation
        self._filepath = filepath
        self._types = tuple(ResourceType.from_extension(value) for value in str(self.property("resourceTypes") or "").split())
        self.invalidateResources()

    def invalidateResources(self):
        self._catalog = None
        self._failed = False
        self._source_catalog = None
        self._source_path = None

    def text(self) -> str:
        return self.lineEdit().text()

    def setText(self, text: str):
        # No index lookup: an existing or case-varied reference is not replaced
        # by the first suggestion, and an empty field stays empty.
        self.lineEdit().setText(text)

    def _choose(self, text: str):
        self.setText(text)
        self.textEdited.emit(text)

    def _edited(self, text: str):
        self.textEdited.emit(text)
        if self._ensureResources():
            self._completion.setCompletionPrefix(text)
            self._completion.complete()

    def _ensureResources(self, refresh: bool = False) -> bool:
        installation = self._installation
        source = None if installation is None else installation.resource_catalog
        path = self._filepath()
        path_key = None if path is None else str(path)
        if not refresh and self._failed and self._source_catalog is source and self._source_path == path_key:
            return False
        if not refresh and self._catalog is not None and self._source_catalog is source and self._source_path == path_key:
            return True

        def collect(report):
            base = source
            if installation is not None and (base is None or refresh):
                base = ResourceCatalog.from_installation(installation, report, reload=True)
            if base is None:
                base = ResourceCatalog()
            return base, base.with_context(path, report)

        loader = AsyncLoader(self, "Indexing resource choices", collect, "Unable to list resource choices", with_progress=True)
        if not loader.exec_():
            self._failed = True
            self._source_catalog = source
            self._source_path = path_key
            return False
        self._failed = False
        base, self._catalog = loader.value
        if installation is not None:
            installation.resource_catalog = base
            self._source_catalog = base
        else:
            self._source_catalog = None
        self._source_path = path_key
        # The completer has a separate model. It cannot select an item merely
        # because its source list was rebuilt.
        text, cursor = self.text(), self.lineEdit().cursorPosition()
        blocker = QSignalBlocker(self.lineEdit())
        self._suggestions.setStringList(self._catalog.names(self._types))
        self.lineEdit().setText(text)
        self.lineEdit().setCursorPosition(cursor)
        del blocker
        return True

    def showPopup(self):
        if self._ensureResources():
            self._completion.setCompletionPrefix("")
            self._completion.complete()

    def hidePopup(self):
        self._completion.popup().hide()
        super().hidePopup()

    def wheelEvent(self, event):
        # Scrolling a form must not commit a different resource name.
        event.ignore()

    def _contextMenu(self, point):
        menu = self.lineEdit().createStandardContextMenu()
        menu.addSeparator()
        locations = menu.addAction("Show resource locations…")
        locations.setEnabled(bool(self.text()))
        locations.triggered.connect(self.showLocations)
        menu.addAction("Refresh resource choices").triggered.connect(lambda: self._ensureResources(True))
        menu.exec_(self.lineEdit().mapToGlobal(point))
        menu.deleteLater()

    def showLocations(self):
        if self._ensureResources():
            ResourceLocationsDialog(self, f"Locations: {self.text()}", self._catalog.find(self.text(), self._types)).exec_()
