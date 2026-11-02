from __future__ import annotations

from copy import deepcopy
from typing import TYPE_CHECKING

from pykotor.common.misc import ResRef
from pykotor.common.stream import BinaryReader
from pykotor.extract.file import ResourceIdentifier
from pykotor.resource.formats.erf import ERF, ERFResource, ERFType, read_erf, write_erf
from pykotor.resource.formats.rim import RIM, RIMResource, read_rim, write_rim
from pykotor.resource.type import ResourceType
from PyQt5 import QtCore, QtGui
from PyQt5.QtCore import QMimeData
from PyQt5.QtGui import QDragEnterEvent, QDragMoveEvent, QDropEvent, QStandardItem, QStandardItemModel
from PyQt5.QtWidgets import QFileDialog, QMessageBox, QShortcut, QTableView, QWidget
from toolset.gui.editor import Editor
from toolset.gui.widgets.settings.installations import GlobalSettings
from toolset.utils.window import openResourceEditor
from utility.error_handling import universal_simplify_exception
from utility.system.path import Path

if TYPE_CHECKING:
    import os

    from toolset.data.installation import HTInstallation


class ERFEditor(Editor):
    def __init__(self, parent: QWidget | None, installation: HTInstallation | None = None):
        """Initialize ERF Editor window."""
        supported: list[ResourceType] = [ResourceType.ERF, ResourceType.MOD, ResourceType.RIM]
        super().__init__(parent, "ERF Editor", "none", supported, supported, installation)
        self.resize(400, 250)

        from toolset.uic.editors.erf import Ui_MainWindow

        self.ui = Ui_MainWindow()
        self.ui.setupUi(self)
        self._setupMenus()
        self._setupSignals()

        self.model = QStandardItemModel(self)
        self.ui.tableView.setModel(self.model)
        self.ui.tableView.selectionModel().selectionChanged.connect(self.selectionChanged)

        # Disable saving file into module
        capsule_types = " ".join(f"*.{e.name.lower()}" for e in ERFType) + " *.rim"
        self._saveFilter = self._saveFilter.replace(f";;Save into module ({capsule_types})", "")
        self._openFilter = self._openFilter.replace(f";;Load from module ({capsule_types})", "")

        self.new()

    def _setupSignals(self):
        """Setup signal connections for UI elements."""
        self.ui.extractButton.clicked.connect(self.extractSelected)
        self.ui.loadButton.clicked.connect(self.selectFilesToAdd)
        self.ui.unloadButton.clicked.connect(self.removeSelected)
        self.ui.openButton.clicked.connect(self.openSelected)
        self.ui.refreshButton.clicked.connect(self.refresh)
        self.ui.tableView.resourceDropped.connect(self.addResources)
        self.ui.tableView.doubleClicked.connect(self.openSelected)

        QShortcut("Del", self).activated.connect(self.removeSelected)

    def load(self, filepath: os.PathLike | str, resref: str, restype: ResourceType, data: bytes):
        """Load resource file.

        Args:
        ----
            filepath: Path to resource file
            resref: Resource reference
            restype: Resource type
            data: File data

        Load resource file:
            - Clear existing model data
            - Set model column count to 3 and header labels
            - Enable refresh button
            - If ERF-type:
                - Parse file with erf reader
                - Add each resource as row to model
            - If RIM:
                - Parse file with rim reader
                - Add each resource as row to model
            - Else show error message file type not supported.
        """
        super().load(filepath, resref, restype, data)

        self.model.clear()
        self.model.setColumnCount(3)
        self.model.setHorizontalHeaderLabels(["ResRef", "Type", "Size"])
        self.ui.refreshButton.setEnabled(True)

        if restype in (ResourceType.ERF, ResourceType.MOD):
            erf: ERF = read_erf(data)
            self._archive = erf
            for resource in erf:
                resrefItem = QStandardItem(str(resource.resref))
                resrefItem.setData(resource)
                restypeItem = QStandardItem(resource.restype.extension.upper())
                sizeItem = QStandardItem(str(len(resource.data)))
                self.model.appendRow([resrefItem, restypeItem, sizeItem])

        elif restype == ResourceType.RIM:
            rim: RIM = read_rim(data)
            self._archive = rim
            for resource in rim:
                resrefItem = QStandardItem(str(resource.resref))
                resrefItem.setData(resource)
                restypeItem = QStandardItem(resource.restype.extension.upper())
                sizeItem = QStandardItem(str(len(resource.data)))
                self.model.appendRow([resrefItem, restypeItem, sizeItem])

        else:
            QMessageBox(
                QMessageBox.Critical,
                "Unable to load file",
                "The file specified is not a MOD/ERF type file.",
                self,
            ).show()

    def build(self) -> tuple[bytes, bytes]:
        """Builds resource data from the model."""
        data = bytearray()
        resource: ERFResource | RIMResource

        if self._restype == ResourceType.RIM:
            rim = deepcopy(self._archive) if isinstance(self._archive, RIM) else RIM()
            for previous in list(rim):
                rim.remove(str(previous.resref), previous.restype)
            for i in range(self.model.rowCount()):
                item = self.model.item(i, 0)
                resource = item.data()
                rim.append(RIMResource(ResRef.from_bytes(resource.resref.to_bytes()), resource.restype, resource.data))
            write_rim(rim, data)

        elif self._restype in (None, ResourceType.ERF, ResourceType.MOD):
            erf_type = ERFType.ERF if self._restype in (None, ResourceType.ERF) else ERFType.MOD
            erf = deepcopy(self._archive) if isinstance(self._archive, ERF) else ERF(erf_type)
            erf.erf_type = erf_type
            for previous in list(erf):
                erf.remove(str(previous.resref), previous.restype)
            for i in range(self.model.rowCount()):
                item = self.model.item(i, 0)
                resource = item.data()
                erf.append(ERFResource(ResRef.from_bytes(resource.resref.to_bytes()), resource.restype, resource.data))
            write_erf(erf, data)

        else:
            raise ValueError(f"Unsupported archive format: {self._restype}.")

        return data, b""

    def new(self):
        super().new()
        self._archive = None
        self.model.clear()
        self.model.setColumnCount(3)
        self.model.setHorizontalHeaderLabels(["ResRef", "Type", "Size"])
        self.ui.refreshButton.setEnabled(False)

    # This editor writes complete archives, rather than a resource inside one.
    _saveEndsWithErf = Editor._saveEndsWithOther
    _saveEndsWithRim = Editor._saveEndsWithOther

    def _resolveSaveDestination(self) -> bool:
        """Whole-archive editing writes directly to its selected container."""
        return True

    def save(self) -> bool:
        """Use the shared save result and enable Refresh only after a successful write."""
        saved = super().save()
        if saved:
            self.ui.refreshButton.setEnabled(True)
        return saved

    def extractSelected(self):
        """Extract selected resources to a folder."""
        folderpath_str = QFileDialog.getExistingDirectory(self, "Extract to folder")
        if not folderpath_str:
            return

        self.ui.tableView.selectionModel().selectedRows()
        for index in self.ui.tableView.selectionModel().selectedRows(0):
            item = self.model.itemFromIndex(index)
            resource: ERFResource = item.data()
            file_path = Path(folderpath_str, f"{resource.resref}.{resource.restype.extension}")
            with file_path.open("wb") as file:
                file.write(resource.data)

    def removeSelected(self):
        """Removes selected rows from table view.

        Removes selected rows from table view by:
            - Getting selected row indexes from table view
            - Reversing the list to remove from last to first
            - Removing the row from model using row number.
        """
        for index in reversed([index for index in self.ui.tableView.selectedIndexes() if not index.column()]):
            item: QStandardItem | None = self.model.itemFromIndex(index)
            self.model.removeRow(item.row())

    def addResources(self, filepaths: list[str]):
        """Adds resource files to the project."""
        for filepath in filepaths:
            c_filepath = Path(filepath)
            try:
                resref, restype = ResourceIdentifier.from_path(c_filepath).validate()
                data = BinaryReader.load_file(c_filepath)
                resource = ERFResource(ResRef(resref), restype, data)

                for row in range(self.model.rowCount()):
                    existing = self.model.item(row, 0).data()
                    if existing.identifier() == resource.identifier():
                        existing.data = data
                        self.model.item(row, 0).setData(existing)
                        self.model.item(row, 2).setText(str(len(data)))
                        break
                else:
                    row = None
                if row is not None:
                    continue

                resrefItem = QStandardItem(str(resource.resref))
                resrefItem.setData(resource)
                restypeItem = QStandardItem(resource.restype.extension.upper())
                sizeItem = QStandardItem(str(len(resource.data)))
                self.model.appendRow([resrefItem, restypeItem, sizeItem])
            except Exception as e:
                QMessageBox(
                    QMessageBox.Critical,
                    "Failed to add resource",
                    f"Could not add resource at {c_filepath.absolute()}:\n{universal_simplify_exception(e)}",
                ).exec_()

    def selectFilesToAdd(self):
        filepaths: list[str] = QFileDialog.getOpenFileNames(self, "Load files into module")[:-1][0]
        self.addResources(filepaths)

    def openSelected(self):
        """Opens the selected resource in the editor."""
        if self._filepath is None:
            QMessageBox(QMessageBox.Critical, "Cannot edit resource", "Save the ERF and try again.", QMessageBox.Ok, self).exec_()
            return

        for index in self.ui.tableView.selectionModel().selectedRows(0):
            item = self.model.itemFromIndex(index)
            resource: ERFResource = item.data()

            if resource.restype.name in ERFType.__members__ or resource.restype == ResourceType.RIM:
                QMessageBox(
                    QMessageBox.Warning,
                    "Cannot open nested ERF files",
                    "Editing ERF or RIM files nested within each other is not supported.",
                    QMessageBox.Ok,
                    self,
                ).exec_()
                continue

            tempPath, editor = openResourceEditor(
                self._filepath,
                str(resource.resref),
                resource.restype,
                resource.data,
                self._installation,
                self,
            )
            if resource.restype not in (ResourceType.WAV, ResourceType.MP3):
                editor.savedFile.connect(self.resourceSaved)
                editor.childSavedFile.connect(self.resourceSaved)

    def refresh(self):
        data: bytes = BinaryReader.load_file(self._filepath)
        self.load(self._filepath, self._resname, self._restype, data)

    def selectionChanged(self):
        """Updates UI controls based on table selection."""
        if len(self.ui.tableView.selectedIndexes()) == 0:
            self._set_ui_controls_state(False)
        else:
            self._set_ui_controls_state(True)

    def _set_ui_controls_state(self, state: bool):
        self.ui.extractButton.setEnabled(state)
        self.ui.openButton.setEnabled(state)
        self.ui.unloadButton.setEnabled(state)

    def resourceSaved(self, filepath: str, resname: str, restype: ResourceType, data: bytes):
        """Saves resource data to the UI table."""
        super().resourceSaved(filepath, resname, restype, data)
        if filepath != self._filepath:
            return

        if restype in self._readSupported:
            self.load(filepath, resname, restype, data)
            return

        for row in range(self.model.rowCount()):
            item = self.model.item(row, 0)
            resource: ERFResource | RIMResource = item.data()
            if resource.resref == resname and resource.restype == restype:
                resource.data = data
                item.setData(resource)
                self.model.item(row, 2).setText(str(len(data)))
                return

        resource_type = RIMResource if self._restype == ResourceType.RIM else ERFResource
        resource = resource_type(ResRef(resname), restype, data)
        item = QStandardItem(str(resource.resref))
        item.setData(resource)
        self.model.appendRow([item, QStandardItem(restype.extension.upper()), QStandardItem(str(len(data)))])


class ERFEditorTable(QTableView):
    resourceDropped = QtCore.pyqtSignal(object)

    def __init__(self, parent: QWidget):
        super().__init__(parent)

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls:
            event.accept()
        else:
            event.ignore()

    def dragMoveEvent(self, event: QDragMoveEvent):
        if event.mimeData().hasUrls:
            event.setDropAction(QtCore.Qt.CopyAction)
            event.accept()
        else:
            event.ignore()

    def dropEvent(self, event: QDropEvent):
        if event.mimeData().hasUrls:
            event.setDropAction(QtCore.Qt.CopyAction)
            event.accept()
            links: list[str] = [str(url.toLocalFile()) for url in event.mimeData().urls()]
            self.resourceDropped.emit(links)
        else:
            event.ignore()

    def startDrag(self, actions: QtCore.Qt.DropActions | QtCore.Qt.DropAction):
        """Starts a drag operation with the selected items."""
        tempDir = Path(GlobalSettings().extractPath)

        if not tempDir or not tempDir.safe_isdir():
            print(f"Temp directory not valid: {tempDir}")
            return

        urls: list[QtCore.QUrl] = []
        for index in (index for index in self.selectedIndexes() if not index.column()):
            resource: ERFResource = self.model().itemData(index)[QtCore.Qt.UserRole + 1]
            file_stem, file_ext = str(resource.resref), resource.restype.extension
            filepath = Path(tempDir, f"{file_stem}.{file_ext}")
            with filepath.open("wb") as file:
                file.write(resource.data)
            urls.append(QtCore.QUrl.fromLocalFile(str(filepath)))

        mimeData = QMimeData()
        mimeData.setUrls(urls)
        drag = QtGui.QDrag(self)
        drag.setMimeData(mimeData)
        drag.exec_(QtCore.Qt.CopyAction, QtCore.Qt.CopyAction)
