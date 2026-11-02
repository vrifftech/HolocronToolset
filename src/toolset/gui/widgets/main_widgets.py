from __future__ import annotations

from queue import Queue
from abc import abstractmethod
from typing import TYPE_CHECKING

from pykotor.resource.formats.tpc import TPCTextureFormat
from PyQt5 import QtCore
from PyQt5.QtCore import QModelIndex, QPoint, QSortFilterProxyModel, QThread, QTimer
from PyQt5.QtGui import QIcon, QImage, QPixmap, QResizeEvent, QStandardItem, QStandardItemModel, QTransform
from PyQt5.QtWidgets import QHeaderView, QMenu, QWidget

if TYPE_CHECKING:
    from pykotor.extract.file import FileResource
    from pykotor.resource.type import ResourceType
    from toolset.data.installation import HTInstallation


class MainWindowList(QWidget):
    requestOpenResource = QtCore.pyqtSignal(object, object)

    requestExtractResource = QtCore.pyqtSignal(object)
    requestResourceLocations = QtCore.pyqtSignal(object)

    sectionChanged = QtCore.pyqtSignal(object)

    @abstractmethod
    def selectedResources(self) -> list[FileResource]:
        ...


class ResourceList(MainWindowList):
    requestReload = QtCore.pyqtSignal(object)

    requestRefresh = QtCore.pyqtSignal()

    def __init__(self, parent: QWidget):
        """Initializes the ResourceList widget.

        Args:
        ----
            parent (QWidget): The parent widget

        Processing Logic:
        ----------------
            - Initializes the UI from the designer file
            - Sets up the signal connections
            - Creates a ResourceModel and sets it as the model for the tree view
            - Creates a QStandardItemModel for the section combo box
            - Sets the section model as the model for the combo box.
        """
        super().__init__(parent)

        from toolset.uic.widgets.resource_list import Ui_Form
        self.ui = Ui_Form()
        self.ui.setupUi(self)
        self.setupSignals()

        self.modulesModel = ResourceModel()
        self.modulesModel.proxyModel().setFilterCaseSensitivity(QtCore.Qt.CaseInsensitive)
        self.ui.resourceTree.setModel(self.modulesModel.proxyModel())
        self.ui.resourceTree.sortByColumn(0, QtCore.Qt.AscendingOrder)

        self.sectionModel = QStandardItemModel()
        self.ui.sectionCombo.setModel(self.sectionModel)

    def setupSignals(self):
        self.ui.searchEdit.textEdited.connect(self.onFilterStringUpdated)
        self.ui.sectionCombo.currentIndexChanged.connect(self.onSectionChanged)
        self.ui.reloadButton.clicked.connect(self.onReloadClicked)
        self.ui.refreshButton.clicked.connect(self.onRefreshClicked)
        self.ui.resourceTree.customContextMenuRequested.connect(self.onResourceContextMenu)
        self.ui.resourceTree.doubleClicked.connect(self.onResourceDoubleClicked)

    def hideReloadButton(self):
        self.ui.reloadButton.setVisible(False)

    def hideSection(self):
        self.ui.line.setVisible(False)
        self.ui.sectionCombo.setVisible(False)
        self.ui.refreshButton.setVisible(False)

    def currentSection(self) -> str:
        return self.ui.sectionCombo.currentData()

    def changeSection(self, section: str):
        for i in range(self.ui.sectionCombo.count()):
            if section in self.ui.sectionCombo.itemText(i):
                self.ui.sectionCombo.setCurrentIndex(i)

    def setResources(self, resources: list[FileResource]):
        # Do not deduplicate through FileResource.__eq__: equal keys can be
        # different physical records. The proxy sorts only their presentation.
        proxy = self.modulesModel.proxyModel()
        proxy.setDynamicSortFilter(False)
        self.modulesModel.clear()
        for resource in resources:
            self.modulesModel.addResource(resource)
        proxy.setDynamicSortFilter(True)
        proxy.sort(0, QtCore.Qt.AscendingOrder)

    def setSections(self, sections: list[QStandardItem]):
        self.sectionModel.clear()
        for section in sections:
            self.sectionModel.insertRow(self.sectionModel.rowCount(), section)

    def setResourceSelection(self, resource: FileResource):
        """Sets the selected resource in the resource tree.

        Args:
        ----
            resource (FileResource): The resource to select

        Processing Logic:
        ----------------
            - Loops through all resources in the model to find matching resource
            - Expands the parent item in the tree
            - Scrolls to and selects the matching child item.
        """
        model = self.ui.resourceTree.model().sourceModel()

        def select(parent, child):
            self.ui.resourceTree.expand(parent)
            self.ui.resourceTree.scrollTo(child)
            self.ui.resourceTree.setCurrentIndex(child)

        for item in model.allResourcesItems():
            resource_from_item: FileResource = item.resource
            if (
                resource_from_item.filepath() == resource.filepath()
                and resource_from_item.offset() == resource.offset()
                and resource_from_item.size() == resource.size()
                and resource_from_item.resname() == resource.resname()
                and resource_from_item.restype() == resource.restype()
            ):
                parentIndex = model.proxyModel().mapFromSource(item.parent().index())
                itemIndex = model.proxyModel().mapFromSource(item.index())
                QTimer.singleShot(1, lambda parent=parentIndex, index=itemIndex: select(parent, index))
                return

    def selectedResources(self) -> list[FileResource]:
        return self.modulesModel.resourceFromIndexes(self.ui.resourceTree.selectedIndexes())

    def onFilterStringUpdated(self):
        self.modulesModel.proxyModel().setFilterFixedString(self.ui.searchEdit.text())

    def onSectionChanged(self):
        self.sectionChanged.emit(self.ui.sectionCombo.currentData(QtCore.Qt.UserRole))

    def onReloadClicked(self):
        self.requestReload.emit(self.ui.sectionCombo.currentData(QtCore.Qt.UserRole))

    def onRefreshClicked(self):
        self.requestRefresh.emit()

    def onResourceContextMenu(self, point: QPoint):
        """Shows context menu for selected resources.

        Args:
        ----
            point: QPoint - Mouse position for context menu

        Processing Logic:
        ----------------
            - Create QMenu at mouse position
            - Get selected resources
            - If single resource and GFF type:
                - Add "Open" and "Open with GFF Editor" actions
                - Connect actions to emit signals to open resource.
        """
        index = self.ui.resourceTree.indexAt(point)
        if index.isValid() and not self.ui.resourceTree.selectionModel().isSelected(index):
            self.ui.resourceTree.setCurrentIndex(index)
        menu = QMenu(self)

        resources: list[FileResource] = self.selectedResources()
        if len(resources) == 1:
            resource: FileResource = resources[0]
            if resource.restype().contents == "gff":
                def open1():
                    return self.requestOpenResource.emit(resources, False)
                def open2():
                    return self.requestOpenResource.emit(resources, True)
                menu.addAction("Open").triggered.connect(open2)
                menu.addAction("Open with GFF Editor").triggered.connect(open1)

        if resources:
            menu.addSeparator()
            menu.addAction("Show resource locations…").triggered.connect(lambda: self.requestResourceLocations.emit(resources))
        menu.exec_(self.ui.resourceTree.viewport().mapToGlobal(point))

    def onResourceDoubleClicked(self):
        self.requestOpenResource.emit(self.selectedResources(), None)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.ui.resourceTree.setColumnWidth(1, 10)
        self.ui.resourceTree.setColumnWidth(0, self.ui.resourceTree.width() - 80)
        self.ui.resourceTree.header().setSectionResizeMode(QHeaderView.Fixed)


class ResourceModel(QStandardItemModel):
    """A data model used by the different trees (Core, Modules, Override).

    This class provides an easy way to add resources while sorting them into categories.
    """

    def __init__(self):
        super().__init__()
        self._categoryItems: dict[str, QStandardItem] = {}
        self._proxyModel = QSortFilterProxyModel(self)
        self._proxyModel.setSourceModel(self)
        self._proxyModel.setRecursiveFilteringEnabled(True)
        self.setColumnCount(2)
        self.setHorizontalHeaderLabels(["ResRef", "Type"])

    def proxyModel(self) -> QSortFilterProxyModel:
        return self._proxyModel

    def clear(self):
        super().clear()
        self._categoryItems = {}
        self.setColumnCount(2)
        self.setHorizontalHeaderLabels(["ResRef", "Type"])

    def _getCategoryItem(self, resourceType: ResourceType) -> QStandardItem:
        if resourceType.category not in self._categoryItems:
            categoryItem = QStandardItem(resourceType.category)
            categoryItem.setSelectable(False)
            unusedItem = QStandardItem("")
            unusedItem.setSelectable(False)
            self._categoryItems[resourceType.category] = categoryItem
            self.appendRow([categoryItem, unusedItem])
        return self._categoryItems[resourceType.category]

    def addResource(self, resource: FileResource):
        item1 = QStandardItem(resource.resname())
        item1.resource = resource
        item2 = QStandardItem(resource.restype().extension.upper())
        self._getCategoryItem(resource.restype()).appendRow([item1, item2])

    def resourceFromIndexes(self, indexes: list[QModelIndex], proxy: bool = True) -> list[FileResource]:
        items = []
        for index in indexes:
            sourceIndex = self._proxyModel.mapToSource(index) if proxy else index
            items.append(self.itemFromIndex(sourceIndex))
        return self.resourceFromItems(items)

    def resourceFromItems(self, items: list[QStandardItem]) -> list[FileResource]:
        return [item.resource for item in items if hasattr(item, "resource")]

    def allResourcesItems(self) -> list[QStandardItem]:
        """Returns a list of all QStandardItem objects in the model that represent resource files."""
        resources = [
            category.child(i, 0)
            for category in self._categoryItems.values()
            for i in range(category.rowCount())
        ]
        return [item for item in resources if item is not None]



class TextureList(MainWindowList):
    requestReload = QtCore.pyqtSignal(object)
    requestRefresh = QtCore.pyqtSignal()

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        from toolset.uic.widgets.texture_list import Ui_Form
        self.ui = Ui_Form()
        self.ui.setupUi(self)
        self._installation = None
        self._generation = 0
        self._closed = False
        self._submitted = set()
        self.texturesModel = QStandardItemModel(self)
        self.texturesProxyModel = QSortFilterProxyModel(self)
        self.texturesProxyModel.setSourceModel(self.texturesModel)
        self.texturesProxyModel.setFilterCaseSensitivity(QtCore.Qt.CaseInsensitive)
        self.ui.resourceList.setModel(self.texturesProxyModel)
        self.sectionModel = QStandardItemModel(self)
        self.ui.sectionCombo.setModel(self.sectionModel)
        self._worker = TextureListWorker(self)
        self._worker.ready.connect(self.onIconUpdate, QtCore.Qt.QueuedConnection)
        self._worker.start()
        self.ui.searchEdit.textEdited.connect(self.onFilterStringUpdated)
        self.ui.sectionCombo.currentIndexChanged.connect(self.onSectionChanged)
        self.ui.resourceList.doubleClicked.connect(self.onResourceDoubleClicked)
        self.ui.resourceList.verticalScrollBar().valueChanged.connect(self.onTextureListScrolled)
        self.ui.searchEdit.textChanged.connect(lambda _text: QTimer.singleShot(0, self.onTextureListScrolled))
        self.ui.resourceList.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.ui.resourceList.customContextMenuRequested.connect(self.onResourceContextMenu)

    def doTerminations(self):
        self._closed = True
        self._worker.requestInterruption()
        self._worker.requests.put(None)
        self._worker.wait()

    def setInstallation(self, installation):
        self._installation = installation
        self._generation += 1
        self._worker.generation = self._generation
        self._submitted.clear()

    def setResources(self, resources):
        self._generation += 1
        self._worker.generation = self._generation
        self._submitted.clear()
        self.texturesModel.clear()
        for resource in resources:
            item = QStandardItem(resource.resname())
            item.setToolTip(f"{resource.filepath()} :: {resource.filename()} @ {resource.offset()}")
            item.setData(resource, QtCore.Qt.UserRole + 1)
            self.texturesModel.appendRow(item)
        QTimer.singleShot(0, self.onTextureListScrolled)

    def setSections(self, sections):
        self.sectionModel.clear()
        for section in sections:
            self.sectionModel.appendRow(section)

    def selectedResources(self):
        return [self.texturesModel.item(self.texturesProxyModel.mapToSource(index).row()).data(QtCore.Qt.UserRole + 1)
                for index in self.ui.resourceList.selectedIndexes()]

    def visibleItems(self):
        view = self.ui.resourceList
        area = view.viewport().rect()
        indexes = []
        # Icon grids can have spacing at (0,0); find the first visible tile.
        first = QModelIndex()
        for y in range(2, min(area.height(), 94), 4):
            for x in range(2, min(area.width(), 94), 4):
                first = view.indexAt(QPoint(x, y))
                if first.isValid():
                    break
            if first.isValid():
                break
        if first.isValid():
            for row in range(first.row(), self.texturesProxyModel.rowCount()):
                index = self.texturesProxyModel.index(row, 0)
                rect = view.visualRect(index)
                if rect.top() > area.bottom():
                    break
                if rect.intersects(area):
                    indexes.append(index)
        return [self.texturesModel.item(self.texturesProxyModel.mapToSource(index).row()) for index in indexes]

    def onTextureListScrolled(self, *_):
        if self._closed or self._worker.isInterruptionRequested():
            return
        for item in self.visibleItems():
            row = item.row()
            if row not in self._submitted:
                self._submitted.add(row)
                self._worker.requests.put((self._generation, row, item.data(QtCore.Qt.UserRole + 1)))

    def onIconUpdate(self, generation, row, result, error):
        if self._closed or generation != self._generation:
            return
        item = self.texturesModel.item(row)
        if item is None:
            return
        if error is not None:
            item.setToolTip(f"{item.toolTip()}\nPreview unavailable: {error}")
            return
        width, height, data = result
        # QPixmap, QIcon and item access stay on the GUI thread.
        image = QImage(data, width, height, width * 4, QImage.Format_RGBA8888).copy()
        item.setIcon(QIcon(QPixmap.fromImage(image).transformed(QTransform().scale(1, -1))))

    def onFilterStringUpdated(self):
        self.texturesProxyModel.setFilterFixedString(self.ui.searchEdit.text())

    def onSectionChanged(self):
        self.sectionChanged.emit(self.ui.sectionCombo.currentData(QtCore.Qt.UserRole))

    def onResourceContextMenu(self, point):
        index = self.ui.resourceList.indexAt(point)
        if index.isValid() and not self.ui.resourceList.selectionModel().isSelected(index):
            self.ui.resourceList.setCurrentIndex(index)
        resources = self.selectedResources()
        if resources:
            menu = QMenu(self)
            menu.addAction("Show resource locations…").triggered.connect(lambda: self.requestResourceLocations.emit(resources))
            menu.exec_(self.ui.resourceList.viewport().mapToGlobal(point))

    def onResourceDoubleClicked(self):
        self.requestOpenResource.emit(self.selectedResources(), None)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        QTimer.singleShot(0, self.onTextureListScrolled)


class TextureListWorker(QThread):
    ready = QtCore.pyqtSignal(int, int, object, object)

    def __init__(self, parent):
        super().__init__(parent)
        self.requests = Queue()
        self.generation = 0

    def run(self):
        while not self.isInterruptionRequested():
            request = self.requests.get()
            if request is None:
                return
            generation, row, resource = request
            if generation != self.generation:
                continue
            try:
                result = self.decode(resource)
            except Exception as error:
                self.ready.emit(generation, row, None, str(error))
            else:
                self.ready.emit(generation, row, result, None)

    @staticmethod
    def decode(resource):
        from pykotor.resource.formats.tpc import read_tpc
        from toolset.data.resources import locations_from_resources

        location, = locations_from_resources((resource,))
        texture = read_tpc(location.read(), file_format=resource.restype())
        mip = next((level for level in range(texture.mipmap_count()) if max(texture.get(level).width, texture.get(level).height) <= 64), 0)
        width, height, data = texture.convert(TPCTextureFormat.RGBA, mip)
        return width, height, bytes(data)
