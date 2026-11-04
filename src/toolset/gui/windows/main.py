from __future__ import annotations

from contextlib import suppress
from tempfile import mkdtemp
from typing import TYPE_CHECKING, Callable, ClassVar

from pykotor.common.module import Module
from pykotor.common.stream import BinaryReader
from pykotor.extract.file import FileResource, ResourceIdentifier, ResourceResult
from pykotor.extract.installation import SearchLocation
from pykotor.resource.formats.mdl import read_mdl, write_mdl
from pykotor.resource.formats.tpc import read_tpc, write_tpc
from pykotor.resource.type import ResourceType
from pykotor.tools import model
from pykotor.tools.misc import is_bif_file, is_capsule_file, is_rim_file
from PyQt5 import QtCore, QtGui
from PyQt5.QtGui import QCloseEvent, QIcon, QPixmap, QStandardItem
from PyQt5.QtWidgets import QFileDialog, QMainWindow, QMessageBox, QTreeView
from toolset.data.installation import HTInstallation
from toolset.gui.dialogs.about import About
from toolset.gui.dialogs.asyncloader import AsyncBatchLoader, AsyncLoader
from toolset.gui.dialogs.clone_module import CloneModuleDialog
from toolset.gui.dialogs.search import FileResults, FileSearcher
from toolset.gui.dialogs.settings import SettingsDialog
from toolset.gui.editors.dlg import DLGEditor
from toolset.gui.editors.erf import ERFEditor
from toolset.gui.editors.gff import GFFEditor
from toolset.gui.editors.ifo import IFOEditor
from toolset.gui.editors.nss import NSSEditor
from toolset.gui.editors.ssf import SSFEditor
from toolset.gui.editors.txt import TXTEditor
from toolset.gui.editors.utc import UTCEditor
from toolset.gui.editors.utd import UTDEditor
from toolset.gui.editors.ute import UTEEditor
from toolset.gui.editors.uti import UTIEditor
from toolset.gui.editors.utm import UTMEditor
from toolset.gui.editors.utp import UTPEditor
from toolset.gui.editors.uts import UTSEditor
from toolset.gui.editors.utt import UTTEditor
from toolset.gui.editors.utw import UTWEditor
from toolset.gui.widgets.settings.misc import GlobalSettings
from toolset.gui.windows.help import HelpWindow
from toolset.gui.windows.module_designer import ModuleDesigner
from toolset.utils.misc import openLink
from toolset.utils.window import addWindow, openResourceEditor
from utility.error_handling import (
    assert_with_variable_trace,
    enforce_instance_cast,
    format_exception_with_variables,
    universal_simplify_exception,
)
from utility.system.path import Path, PurePath
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

if TYPE_CHECKING:
    import os

    from pykotor.common.misc import CaseInsensitiveDict
    from pykotor.resource.formats.mdl.mdl_data import MDL
    from pykotor.resource.formats.tpc import TPC
    from pykotor.resource.type import SOURCE_TYPES
    from pykotor.tools.path import CaseAwarePath
    from toolset.gui.widgets.main_widgets import ResourceList, TextureList
    from typing_extensions import Literal


class ToolWindow(QMainWindow):
    moduleFilesUpdated = QtCore.pyqtSignal(object, object)

    overrideFilesUpdate = QtCore.pyqtSignal(object, object)

    GFF_TYPES: ClassVar[list[ResourceType]] = [
        ResourceType.GFF,
        ResourceType.UTC,
        ResourceType.UTP,
        ResourceType.UTD,
        ResourceType.UTI,
        ResourceType.UTM,
        ResourceType.UTE,
        ResourceType.UTT,
        ResourceType.UTW,
        ResourceType.UTS,
        ResourceType.DLG,
        ResourceType.GUI,
        ResourceType.ARE,
        ResourceType.IFO,
        ResourceType.GIT,
        ResourceType.JRL,
        ResourceType.ITP,
    ]

    def __init__(self):
        """Initializes the main window.

        Args:
        ----
            self: The object instance

        Processing Logic:
        ----------------
            - Sets up superclass initialization
            - Initializes observer and handler objects
            - Hides unnecessary UI sections on startup
            - Loads settings from file
        """
        super().__init__()

        self._loading_installation = False
        self.dogObserver = None
        self.dogHandler = FolderObserver(self)
        self.active: HTInstallation | None = None
        self.settings: GlobalSettings = GlobalSettings()
        self.installations: dict[str, HTInstallation] = {}

        from toolset.uic.windows.main import Ui_MainWindow

        self.ui = Ui_MainWindow()
        self.ui.setupUi(self)
        self._setupSignals()

        self.ui.coreWidget.hideSection()
        self.ui.coreWidget.hideReloadButton()
        self.setWindowIcon(QIcon(QPixmap(":/images/icons/sith.png")))
        self.reloadSettings()

        firstTime = self.settings.firstTime
        if firstTime:
            self.settings.firstTime = False

            # Create a directory used for dumping temp files
            try:
                self.settings.extractPath = mkdtemp(prefix="HolocronToolset-")
            except Exception as e:
                print(f"Could not create temp directory: {universal_simplify_exception(e)}")

    def _setupSignals(self):
        """Connects signals to slots for UI interactions.

        Args:
        ----
            self: {The class instance}: Sets up connections for UI signals.

        Processing Logic:
        ----------------
            - Connects game combo box index changed to change active installation
            - Connects module/override file updated signals to update handlers
            - Connects various widget signals like section changed to handler methods
            - Connects button clicks to extract/open resource methods
            - Connects menu actions to various editor, dialog and tool openings.
        """
        self.ui.gameCombo.currentIndexChanged.connect(self.changeActiveInstallation)
        self._physicalBrowserAction = self.ui.menuTools.addAction("Physical resource browser (read-only)…")
        self._physicalBrowserAction.triggered.connect(self.openResourceBrowser)
        self._referenceSearchAction = self.ui.menuTools.addAction("Find references…")
        self._referenceSearchAction.triggered.connect(self.openReferenceSearch)
        for listing in (self.ui.coreWidget, self.ui.modulesWidget, self.ui.overrideWidget, self.ui.texturesWidget):
            listing.requestResourceLocations.connect(self.showResourceLocations)

        self.moduleFilesUpdated.connect(self.onModuleFileUpdated)
        self.overrideFilesUpdate.connect(self.onOverrideFileUpdated)

        self.ui.coreWidget.requestExtractResource.connect(self.onExtractResources)
        self.ui.coreWidget.requestOpenResource.connect(self.onOpenResources)

        self.ui.modulesWidget.sectionChanged.connect(self.onModuleChanged)
        self.ui.modulesWidget.sectionChanged.connect(self.updateModule3dLink)
        self.ui.modulesWidget.sectionModel.modelReset.connect(self.updateModule3dLink)
        self.ui.resourceTabs.currentChanged.connect(self.updateModule3dLink)
        self.ui.openModule3dLink.linkActivated.connect(self.openCurrentModuleDesigner)
        self.ui.modulesWidget.requestReload.connect(self.onModuleReload)
        self.ui.modulesWidget.requestRefresh.connect(self.onModuleRefresh)
        self.ui.modulesWidget.requestExtractResource.connect(self.onExtractResources)
        self.ui.modulesWidget.requestOpenResource.connect(self.onOpenResources)

        self.ui.overrideWidget.sectionChanged.connect(self.onOverrideChanged)
        self.ui.overrideWidget.requestReload.connect(self.onOverrideReload)
        self.ui.overrideWidget.requestRefresh.connect(self.onOverrideRefresh)
        self.ui.overrideWidget.requestExtractResource.connect(self.onExtractResources)
        self.ui.overrideWidget.requestOpenResource.connect(self.onOpenResources)

        self.ui.texturesWidget.sectionChanged.connect(self.onTexturesChanged)
        self.ui.texturesWidget.requestOpenResource.connect(self.onOpenResources)

        self.ui.extractButton.clicked.connect(lambda: self.onExtractResources(self.getActiveResourceWidget().selectedResources()))
        self.ui.extractAllButton.clicked.connect(lambda: self.onExtractResources(self.getActiveResourceWidget().allResources()))
        self.ui.openButton.clicked.connect(lambda: self.onOpenResources(self.getActiveResourceWidget().selectedResources()))

        self.ui.openAction.triggered.connect(self.openFromFile)
        self.ui.actionSettings.triggered.connect(self.openSettingsDialog)
        self.ui.actionExit.triggered.connect(self.close)
        self.ui.actionNewDLG.triggered.connect(lambda: DLGEditor(self, self.active).show())
        self.ui.actionNewNSS.triggered.connect(lambda: NSSEditor(self, self.active).show())
        self.ui.actionNewUTC.triggered.connect(lambda: UTCEditor(self, self.active).show())
        self.ui.actionNewUTP.triggered.connect(lambda: UTPEditor(self, self.active).show())
        self.ui.actionNewUTD.triggered.connect(lambda: UTDEditor(self, self.active).show())
        self.ui.actionNewUTI.triggered.connect(lambda: UTIEditor(self, self.active).show())
        self.ui.actionNewUTT.triggered.connect(lambda: UTTEditor(self, self.active).show())
        self.ui.actionNewUTM.triggered.connect(lambda: UTMEditor(self, self.active).show())
        self.ui.actionNewUTW.triggered.connect(lambda: UTWEditor(self, self.active).show())
        self.ui.actionNewUTE.triggered.connect(lambda: UTEEditor(self, self.active).show())
        self.ui.actionNewUTS.triggered.connect(lambda: UTSEditor(self, self.active).show())
        self.ui.actionNewGFF.triggered.connect(lambda: GFFEditor(self, self.active).show())
        self.ui.actionNewERF.triggered.connect(lambda: ERFEditor(self, self.active).show())
        self.ui.actionNewTXT.triggered.connect(lambda: TXTEditor(self, self.active).show())
        self.ui.actionNewSSF.triggered.connect(lambda: SSFEditor(self, self.active).show())
        self._newIFOAction = self.ui.menuNew.addAction("Module Information (IFO)")
        self._newIFOAction.setEnabled(self.active is not None)
        self._newIFOAction.triggered.connect(lambda: IFOEditor(self, self.active).show())
        self.ui.actionCloneModule.triggered.connect(lambda: CloneModuleDialog(self, self.active, self.installations).exec_())

        self.ui.actionModuleDesigner.triggered.connect(self.openModuleDesigner)
        self.ui.actionEditTLK.triggered.connect(self.openActiveTalktable)
        self.ui.actionEditJRL.triggered.connect(self.openActiveJournal)
        self.ui.actionFileSearch.triggered.connect(self.openFileSearchDialog)

        self.ui.actionInstructions.triggered.connect(self.openInstructionsWindow)
        self.ui.actionHelpAbout.triggered.connect(self.openAboutDialog)
        self.ui.actionDiscordDeadlyStream.triggered.connect(lambda: openLink("https://discord.com/invite/bRWyshn"))
        self.ui.actionDiscordKotOR.triggered.connect(lambda: openLink("http://discord.gg/kotor"))
        self.ui.actionDiscordOpenKotOR.triggered.connect(lambda: openLink("https://discord.gg/openkotor"))

    # region Signal callbacks
    def onModuleFileUpdated(self, changedFile: str, eventType: str):
        if self._loading_installation or self.active is None:
            return
        self.active.resource_catalog = None
        if eventType in ("created", "deleted"):
            self.onModuleRefresh()
        else:
            # If the current module opened is the file which was updated, then we
            # should refresh the ui.
            if self.ui.modulesWidget.currentSection() == changedFile:
                self.onModuleReload(changedFile)
            else:
                self.active.reload_module(changedFile)

    def onModuleChanged(self, newModuleFile: str):
        self.onModuleReload(newModuleFile)

    def onModuleReload(self, moduleFile: str):
        if self.active is None or not moduleFile:
            return
        if not self._loading_installation:
            self.active.reload_module(moduleFile)
            self.active.resource_catalog = None
        resources = self.active.module_resources(moduleFile)
        if self.settings.joinRIMsTogether and is_rim_file(moduleFile):
            companion = f"{PurePath(moduleFile).stem}_s.rim".casefold()
            actual = next((name for name in self.active.modules_list() if name.casefold() == companion), None)
            if actual is not None:
                resources += self.active.module_resources(actual)
        self.ui.modulesWidget.setResources(resources)

    def onModuleRefresh(self):
        self.ui.modulesWidget.setResources([])
        self.refreshModuleList()

    def onOverrideFileUpdated(self, changedFile: str, eventType: str):
        if self._loading_installation or self.active is None:
            return
        self.active.resource_catalog = None
        if eventType in ("created", "deleted"):
            self.onOverrideRefresh()
        else:
            self.onOverrideReload(changedFile)

    def onOverrideChanged(self, newDirectory: str):
        if self.active is None or newDirectory is None:
            return
        self.ui.overrideWidget.setResources(self.active.override_resources(newDirectory))

    def onOverrideReload(self, file: str):
        file_path = Path(file)
        if not file_path.name:
            print(f"Cannot reload '{file}': no file loaded")
            return
        if not file_path.is_relative_to(self.active.override_path()):
            print(f"{file_path} is not relative to the override folder, cannot reload")
            return
        if file_path.safe_isfile():
            self.active.reload_override_file(file_path)
        self.onOverrideChanged(self.ui.overrideWidget.currentSection())

    def onOverrideRefresh(self):
        self.refreshOverrideList()

    def onTexturesChanged(self, newTexturepack: str):
        if self.active is None or not newTexturepack:
            return
        self.ui.texturesWidget.setResources(self.active.texturepack_resources(newTexturepack))

    def onExtractResources(self, resources: list[FileResource]):
        """Extracts the resources selected in the main UI window.

        Args:
        ----
            resources: list[FileResource]: List of selected resources to extract

        Processing Logic:
        ----------------
            - If single resource selected, prompt user to save with default or custom name
            - If multiple resources selected, prompt user for extract directory and extract each with original name.
        """
        options = (self.ui.mdlDecompileCheckbox.isChecked(), self.ui.tpcTxiCheckbox.isChecked(),
                   self.ui.tpcDecompileCheckbox.isChecked(), self.ui.mdlTexturesCheckbox.isChecked())
        if len(resources) == 1:
            # Player saves resource with a specific name
            default: str = f"{resources[0].resname()}.{resources[0].restype().extension}"
            filepath: str = QFileDialog.getSaveFileName(self, "Save resource", default)[0]

            if filepath:
                loader = AsyncBatchLoader(self, "Extracting Resources", [], "Failed to Extract Resources")
                loader.addTask(lambda: self._extractResource(resources[0], filepath, loader, options))
                loader.exec_()

        elif len(resources) >= 1:
            # Player saves resources with original name to a specific directory
            folderpath: str = QFileDialog.getExistingDirectory(self, "Select directory to extract to")
            if folderpath:
                loader = AsyncBatchLoader(self, "Extracting Resources", [], "Failed to Extract Resources")

                for resource in resources:
                    filename = f"{resource.resname()}.{resource.restype().extension}"
                    filepath = str(Path(folderpath, filename))
                    loader.addTask(lambda a=resource, b=filepath: self._extractResource(a, b, loader, options))

                loader.exec_()

    def onOpenResources(self, resources: list[FileResource], useSpecializedEditor: bool | None = None):
        from toolset.data.resources import locations_from_resources

        resources = tuple(resources)
        if not resources:
            return
        installation = self.active

        def task(report):
            locations = locations_from_resources(resources, report)
            report(len(locations), "set_maximum")
            result = []
            for index, location in enumerate(locations):
                report(location.filename, "update_subtask_text")
                result.append((location, location.read_for_editing()))
                report(index + 1, "set_value")
            return result

        loader = AsyncLoader(self, "Reading resources", task, "Unable to open resources", with_progress=True)
        if not loader.exec_():
            return
        for location, data in loader.value:
            try:
                openResourceEditor(location.path, location.name, location.restype, data,
                                   installation, self, gff_specialized=useSpecializedEditor)
            except Exception as error:
                QMessageBox.critical(self, "Unable to open resource", f"{location.display}\n{error}")

    # endregion

    # region Events
    def closeEvent(self, e: QCloseEvent | None):
        if self._loading_installation:
            if e is not None:
                e.ignore()
            return
        if self.dogObserver is not None:
            self.dogObserver.stop()
            self.dogObserver.join()
            self.dogObserver = None
        self.ui.texturesWidget.doTerminations()
        super().closeEvent(e)

    def dropEvent(self, e: QtGui.QDropEvent | None):
        if e is None:
            return
        if e.mimeData().hasUrls():
            for url in e.mimeData().urls():
                filepath: str = url.toLocalFile()
                data = BinaryReader.load_file(filepath)
                resref, restype = ResourceIdentifier.from_path(filepath)
                openResourceEditor(filepath, resref, restype, data, self.active, self)

    def dragEnterEvent(self, e: QtGui.QDragEnterEvent | None):
        if e is None:
            return
        if e.mimeData().hasUrls():
            for url in e.mimeData().urls():
                with suppress(Exception):
                    _resref, _restype = ResourceIdentifier.from_path(url.toLocalFile()).validate()
                    e.accept()

    # endregion

    # region Menu Bar
    def updateMenus(self):
        self._physicalBrowserAction.setEnabled(self.active is not None)
        self._newIFOAction.setEnabled(self.active is not None)
        version: Literal['x', '2', '1'] = "x" if self.active is None else "2" if self.active.tsl else "1"

        dialogIconPath = f":/images/icons/k{version}/dialog.png"
        self.ui.actionNewDLG.setIcon(QIcon(QPixmap(dialogIconPath)))
        self.ui.actionNewDLG.setEnabled(self.active is not None)

        scriptIconPath = f":/images/icons/k{version}/script.png"
        self.ui.actionNewNSS.setIcon(QIcon(QPixmap(scriptIconPath)))
        self.ui.actionNewNSS.setEnabled(self.active is not None)

        creatureIconPath = f":/images/icons/k{version}/creature.png"
        self.ui.actionNewUTC.setIcon(QIcon(QPixmap(creatureIconPath)))
        self.ui.actionNewUTC.setEnabled(self.active is not None)

        placeableIconPath = f":/images/icons/k{version}/placeable.png"
        self.ui.actionNewUTP.setIcon(QIcon(QPixmap(placeableIconPath)))
        self.ui.actionNewUTP.setEnabled(self.active is not None)

        doorIconPath = f":/images/icons/k{version}/door.png"
        self.ui.actionNewUTD.setIcon(QIcon(QPixmap(doorIconPath)))
        self.ui.actionNewUTD.setEnabled(self.active is not None)

        itemIconPath = f":/images/icons/k{version}/item.png"
        self.ui.actionNewUTI.setIcon(QIcon(QPixmap(itemIconPath)))
        self.ui.actionNewUTI.setEnabled(self.active is not None)

        soundIconPath = f":/images/icons/k{version}/sound.png"
        self.ui.actionNewUTS.setIcon(QIcon(QPixmap(soundIconPath)))
        self.ui.actionNewUTS.setEnabled(self.active is not None)

        triggerIconPath = f":/images/icons/k{version}/trigger.png"
        self.ui.actionNewUTT.setIcon(QIcon(QPixmap(triggerIconPath)))
        self.ui.actionNewUTT.setEnabled(self.active is not None)

        merchantIconPath = f":/images/icons/k{version}/merchant.png"
        self.ui.actionNewUTM.setIcon(QIcon(QPixmap(merchantIconPath)))
        self.ui.actionNewUTM.setEnabled(self.active is not None)

        waypointIconPath = f":/images/icons/k{version}/waypoint.png"
        self.ui.actionNewUTW.setIcon(QIcon(QPixmap(waypointIconPath)))
        self.ui.actionNewUTW.setEnabled(self.active is not None)

        encounterIconPath = f":/images/icons/k{version}/encounter.png"
        self.ui.actionNewUTE.setIcon(QIcon(QPixmap(encounterIconPath)))
        self.ui.actionNewUTE.setEnabled(self.active is not None)

        self.ui.actionEditTLK.setEnabled(self.active is not None)
        self.ui.actionEditJRL.setEnabled(self.active is not None)
        self.ui.actionFileSearch.setEnabled(self.active is not None)
        self._referenceSearchAction.setEnabled(self.active is not None)
        self.ui.actionModuleDesigner.setEnabled(self.active is not None)

        self.ui.actionCloneModule.setEnabled(self.active is not None)

    def openModuleDesigner(self):
        assert self.active is not None, assert_with_variable_trace(self.active is not None)
        designer = ModuleDesigner(None, self.active)
        addWindow(designer)

    def updateModule3dLink(self):
        self.ui.openModule3dLink.setVisible(
            self.ui.resourceTabs.currentWidget() is self.ui.modulesTab
            and bool(self.ui.modulesWidget.currentSection())
        )

    def openCurrentModuleDesigner(self):
        root = Module.find_root(self.ui.modulesWidget.currentSection())
        designer = ModuleDesigner(None, self.active, module_root=root)
        addWindow(designer)

    def openSettingsDialog(self):
        """Opens the Settings dialog and refresh installation combo list if changes."""
        dialog = SettingsDialog(self)
        if dialog.exec_() and dialog.installationEdited:
            self.reloadSettings()

    def openActiveTalktable(self):
        """Opens the talktable for the active (currently selected) installation.

        If there is no active information, show a message box instead.
        """
        filepath: CaseAwarePath = self.active.path() / "dialog.tlk"
        data: bytes = BinaryReader.load_file(filepath)
        openResourceEditor(filepath, "dialog", ResourceType.TLK, data, self.active, self)

    def openActiveJournal(self):
        self.active.load_override(".")
        res: ResourceResult | None = self.active.resource(
            "global",
            ResourceType.JRL,
            [SearchLocation.OVERRIDE, SearchLocation.CHITIN],
        )
        openResourceEditor(
            res.filepath,
            resref="global",
            restype=ResourceType.JRL,
            data=res.data,
            installation=self.active,
            parentwindow=self,
        )

    def openReferenceSearch(self):
        from toolset.gui.dialogs.reference_search import ReferenceSearchDialog

        addWindow(ReferenceSearchDialog(self, self.active))

    def openFileSearchDialog(self):
        """Opens the FileSearcher dialog.

        If a search is conducted then a FileResults dialog displays the results
        where the user can then select a resource and the selected resouce will then be shown in the main window.
        """
        searchDialog = FileSearcher(self, self.installations)
        if searchDialog.exec_():
            assert searchDialog.installation is not None, assert_with_variable_trace(searchDialog.installation is not None)
            resultsDialog = FileResults(self, searchDialog.results, searchDialog.installation)
            if resultsDialog.exec_() and resultsDialog.selection:
                selection: FileResource = resultsDialog.selection

                # Open relevant tab then select resource in the tree
                if selection.filepath().is_relative_to(self.active.module_path()):
                    self.ui.resourceTabs.setCurrentIndex(1)
                    self.selectResource(self.ui.modulesWidget, selection)  # FIXME: ResourceList vs QTreeView?
                elif selection.filepath().is_relative_to(self.active.override_path()):
                    self.ui.resourceTabs.setCurrentIndex(2)
                    self.selectResource(self.ui.overrideWidget, selection)  # FIXME: ResourceList vs QTreeView?
                elif is_bif_file(selection.filepath().name):
                    self.selectResource(self.ui.coreWidget, selection)  # FIXME: ResourceList vs QTreeView?

    def openInstructionsWindow(self):
        """Opens the instructions window."""
        window = HelpWindow(None)
        addWindow(window)

    def openAboutDialog(self):
        """Opens the about dialog."""
        About(self).exec_()

    # endregion

    # region Other
    def reloadSettings(self):
        self.reloadInstallations()

    def getActiveResourceWidget(self) -> TextureList | ResourceList | None:
        if self.ui.resourceTabs.currentWidget() is self.ui.coreTab:
            return self.ui.coreWidget
        if self.ui.resourceTabs.currentWidget() is self.ui.modulesTab:
            return self.ui.modulesWidget
        if self.ui.resourceTabs.currentWidget() is self.ui.overrideTab:
            return self.ui.overrideWidget
        if self.ui.resourceTabs.currentWidget() is self.ui.texturesTab:
            return self.ui.texturesWidget
        return None

    def refreshModuleList(self, reload: bool = True, module_names=None):
        """Refreshes the list of modules in the modulesCombo combobox."""
        # Do nothing if no installation is currently loaded
        if self.active is None:
            return

        # If specified the user can forcibly reload the resource list for every module
        if reload:
            self.active.load_modules()
            self.active.resource_catalog = None

        areaNames: CaseInsensitiveDict[str] = self.active.module_names() if module_names is None else module_names
        sortedKeys: list[str] = sorted(areaNames, key=lambda key: areaNames.get(key))

        modules: list[QStandardItem] = []
        for module in sortedKeys:
            # Some users may choose to have their RIM files for the same module merged into a single option for the
            # dropdown menu.
            lower_module_name = module.lower()
            if self.settings.joinRIMsTogether and lower_module_name.endswith("_s.rim"):
                continue

            item = QStandardItem(f"{areaNames[module]} [{module}]")
            item.setData(module, QtCore.Qt.UserRole)

            # Some users may choose to have items representing RIM files to have grey text.
            if self.settings.greyRIMText and lower_module_name.endswith(".rim"):
                item.setForeground(self.palette().shadow())

            modules.append(item)

        self.ui.modulesWidget.setSections(modules)

    def refreshOverrideList(self, reload=True):
        """Refreshes the list of override directories in the overrideFolderCombo combobox."""
        if reload:
            self.active.load_override()
            self.active.resource_catalog = None

        sections: list[QStandardItem] = []
        for directory in self.active.override_list():
            section = QStandardItem(directory if directory.strip() else "[Root]")
            section.setData(directory, QtCore.Qt.UserRole)
            sections.append(section)
        self.ui.overrideWidget.setSections(sections)

    def refreshTexturePackList(self, reload=True):
        if reload:
            self.active.load_textures()
            self.active.resource_catalog = None

        sections: list[QStandardItem] = []
        for texturepack in self.active.texturepacks_list():
            section = QStandardItem(texturepack)
            section.setData(texturepack, QtCore.Qt.UserRole)
            sections.append(section)

        self.ui.texturesWidget.setSections(sections)

    def changeModule(self, module: str):
        # Some users may choose to merge their RIM files under one option in the Modules tab; if this is the case we
        # need to account for this.
        if self.settings.joinRIMsTogether and module.casefold().endswith("_s.rim"):
            module = f"{module[:-6]}.rim"

        self.ui.modulesWidget.changeSection(module)

    def selectResource(self, tree: QTreeView, resource: FileResource):
        if tree == self.ui.coreWidget:
            self.ui.resourceTabs.setCurrentWidget(self.ui.coreTab)
            self.ui.coreWidget.setResourceSelection(resource)

        elif tree == self.ui.modulesWidget:
            self.ui.resourceTabs.setCurrentWidget(self.ui.modulesTab)
            filename: str = resource.filepath().name
            self.changeModule(filename)
            self.ui.modulesWidget.setResourceSelection(resource)

        elif tree == self.ui.overrideWidget:
            self.ui.resourceTabs.setCurrentWidget(self.ui.overrideTab)
            self.ui.overrideWidget.setResourceSelection(resource)
            subfolder: str = "."
            for folder_name in self.active.override_list():
                folder_path: CaseAwarePath = self.active.override_path() / folder_name
                if resource.filepath().is_relative_to(folder_path) and len(subfolder) < len(folder_path.name):
                    subfolder = folder_name
            self.changeOverrideFolder(subfolder)

    def changeOverrideFolder(self, subfolder: str):
        self.ui.overrideWidget.changeSection(subfolder)

    def reloadInstallations(self):
        """Refresh the list of installations available in the combobox."""
        self.ui.gameCombo.clear()
        self.ui.gameCombo.addItem("[None]")

        for installation in self.settings.installations().values():
            self.ui.gameCombo.addItem(installation.name)

    def changeActiveInstallation(self, index: int):
        """Build a candidate in the worker; commit data and widgets on this thread."""
        if self._loading_installation:
            return
        old_active = self.active
        old_index = 0 if old_active is None else self.ui.gameCombo.findText(old_active.name)
        if index <= 0:
            if self.dogObserver is not None:
                self.dogObserver.stop()
                self.dogObserver.join()
                self.dogObserver = None
            self.active = None
            self.ui.coreWidget.setResources([])
            self.ui.modulesWidget.setSections([])
            self.ui.modulesWidget.setResources([])
            self.ui.overrideWidget.setSections([])
            self.ui.overrideWidget.setResources([])
            self.ui.texturesWidget.setResources([])
            self.ui.texturesWidget.setSections([])
            self.ui.texturesWidget.setInstallation(None)
            self.ui.resourceTabs.setEnabled(False)
            self.ui.sidebar.setEnabled(False)
            self.updateMenus()
            return

        name = self.ui.gameCombo.itemText(index)
        selected = self.settings.installations()[name]
        path = selected.path.strip()
        if not path:
            path = QFileDialog.getExistingDirectory(self, f"Select the game directory for {name}")
        if not path:
            blocker = QtCore.QSignalBlocker(self.ui.gameCombo)
            self.ui.gameCombo.setCurrentIndex(max(0, old_index))
            del blocker
            return
        tsl = selected.tsl
        self._loading_installation = True
        try:
            from toolset.data.resources import ResourceCatalog

            def task(report):
                # HTInstallation.__init__ already loads once. Never mutate the
                # active installation or create Qt models inside this worker.
                candidate = HTInstallation(path, name, tsl, None, progress_callback=report)
                candidate.progress_callback = None
                report("Reading module display names", "update_maintask_text")
                module_names = candidate.module_names()
                candidate.resource_catalog = ResourceCatalog.from_installation(candidate, report)
                report("Ready to display installation", "update_maintask_text")
                return candidate, module_names

            loader = AsyncLoader(self, "Loading installation", task, "Failed to load installation", with_progress=True)
            if not loader.exec_():
                blocker = QtCore.QSignalBlocker(self.ui.gameCombo)
                self.ui.gameCombo.setCurrentIndex(max(0, old_index))
                del blocker
                return
            candidate, module_names = loader.value
            if self.dogObserver is not None:
                self.dogObserver.stop()
                self.dogObserver.join()
                self.dogObserver = None
            candidate.mainWindow = self
            self.active = candidate
            self.installations[name] = candidate
            selected.path = path
            self.ui.resourceTabs.setEnabled(True)
            self.ui.sidebar.setEnabled(True)
            self.ui.coreWidget.setResources(candidate.core_resources())
            self.ui.texturesWidget.setInstallation(candidate)
            self.ui.modulesWidget.setResources([])
            self.ui.overrideWidget.setResources([])
            self.ui.texturesWidget.setResources([])
            self.refreshModuleList(reload=False, module_names=module_names)
            self.refreshOverrideList(reload=False)
            self.refreshTexturePackList(reload=False)
            self.updateMenus()
            self.dogObserver = Observer()
            self.dogObserver.schedule(self.dogHandler, str(candidate.path()), recursive=True)
            self.dogObserver.start()
        finally:
            self._loading_installation = False

    def openResourceBrowser(self):
        if self.active is None:
            return
        from toolset.gui.windows.resource_browser import ResourceBrowser

        browser = ResourceBrowser(self, self.active)
        browser.show()
        addWindow(browser)

    def showResourceLocations(self, resources):
        if self.active is None or not resources:
            return
        from toolset.data.resources import ResourceCatalog
        from toolset.gui.dialogs.resource_locations import ResourceLocationsDialog

        installation = self.active
        # Refresh is explicit here; don't query a guessed 'winning' location.
        loader = AsyncLoader(self, "Locating physical resources",
                             lambda report: ResourceCatalog.from_installation(installation, report, reload=True),
                             "Unable to list resource locations", with_progress=True)
        if not loader.exec_():
            return
        installation.resource_catalog = loader.value
        keys = {(resource.resname().casefold(), resource.restype()) for resource in resources}
        records = [row for row in loader.value.locations if (row.name.casefold(), row.restype) in keys]
        ResourceLocationsDialog(self, "All indexed resource locations", records).exec_()

    def _extractResource(self, resource: FileResource, filepath: os.PathLike | str, loader: AsyncBatchLoader, options):
        """Extracts a resource file from a FileResource object.

        Args:
        ----
            resource (FileResource): The FileResource object
            filepath (os.PathLike | str): Path to save the extracted file
            loader (AsyncBatchLoader): Loader for async operations

        Processing Logic:
        ----------------
            - Extracts Txi data from TPC files
            - Decompiles TPC and MDL files
            - Extracts textures from MDL files
            - Writes extracted data to the file path
        """
        decompile_mdl, extract_txi, decompile_tpc, extract_textures = options
        r_filepath: Path = Path.pathify(filepath)
        folderpath: Path = r_filepath.parent

        try:
            data: bytes = resource.data()

            if resource.restype() == ResourceType.MDX and decompile_mdl:
                # Ignore extracting MDX files if decompiling MDLs
                return

            if resource.restype() == ResourceType.TPC:
                tpc: TPC = read_tpc(data, file_format=ResourceType.TPC)

                if extract_txi:
                    self._extractTxi(tpc, r_filepath)

                if decompile_tpc:
                    if tpc.image_count() > 1:
                        # Batch extraction runs on a worker. Export every image;
                        # do not launch a selection dialog outside the Qt thread.
                        for image in range(tpc.image_count()):
                            output = r_filepath.with_name(f"{r_filepath.stem}_{image}.tga")
                            write_tpc(tpc, output, ResourceType.TGA, image=image)
                        return
                    data = self._decompileTpc(tpc)
                    r_filepath = r_filepath.with_suffix(".tga")

            if resource.restype() == ResourceType.MDL:
                if decompile_mdl:
                    data = self._decompileMdl(resource, data)
                    r_filepath = r_filepath.with_suffix(".ascii.mdl")

                if extract_textures:
                    self._extractMdlTextures(resource, folderpath, loader, data, extract_txi, decompile_tpc)

            with r_filepath.open("wb") as file:
                file.write(data)

        except Exception as e:
            print(format_exception_with_variables(e))
            msg = f"Failed to extract resource: {resource.resname()}.{resource.restype().extension}"
            raise RuntimeError(msg) from e

    def _extractTxi(self, tpc: TPC, filepath: Path):
        from pykotor.resource.formats.txi import write_txi
        write_txi(tpc.effective_txi, filepath.with_suffix(".txi"))

    def _decompileTpc(self, tpc: TPC):
        data = bytearray()
        write_tpc(tpc, data, ResourceType.TGA)
        return data

    def _decompileMdl(self, resource: FileResource, data: SOURCE_TYPES):
        mdxData: bytes = self.active.resource(resource.resname(), ResourceType.MDX).data
        mdl: MDL | None = read_mdl(data, 0, 0, mdxData, 0, 0)

        data = bytearray()
        write_mdl(mdl, data, ResourceType.MDL_ASCII)
        return data

    def _extractMdlTextures(self, resource: FileResource, folderpath: Path, loader: AsyncBatchLoader, data: bytes, extract_txi: bool, decompile_tpc: bool):
        try:
            for texture in model.iterate_textures(data):
                try:
                    tpc: TPC | None = self.active.texture(texture)
                    if extract_txi:
                        self._extractTxi(tpc, folderpath.joinpath(f"{texture}.tpc"))

                    file_format: Literal[ResourceType.TGA, ResourceType.TPC] = ResourceType.TGA if decompile_tpc else ResourceType.TPC
                    extension: Literal['.tga', '.tpc'] = ".tga" if file_format == ResourceType.TGA else ".tpc"
                    if file_format == ResourceType.TGA and tpc.image_count() > 1:
                        for image in range(tpc.image_count()):
                            write_tpc(tpc, folderpath.joinpath(f"{texture}_{image}{extension}"), file_format, image=image)
                    else:
                        write_tpc(tpc, folderpath.joinpath(f"{texture}{extension}"), file_format)

                except Exception as e:  # noqa: PERF203
                    etype, msg = universal_simplify_exception(e)
                    loader.errors.append(type(e)(f"Could not find or extract tpc: '{texture}'\nReason ({etype}): {msg}"))

        except Exception as e:
            etype, msg = universal_simplify_exception(e)
            loader.errors.append(type(e)(f"Could not determine textures used in model: '{resource.resname()}'\nReason ({etype}): {msg}"))

    def openFromFile(self):
        filepaths: list[str] = QFileDialog.getOpenFileNames(self, "Select files to open")[:-1][0]

        for filepath in filepaths:
            try:
                openResourceEditor(
                    filepath,
                    *ResourceIdentifier.from_path(filepath).validate(),
                    BinaryReader.load_file(filepath),
                    self.active,
                    self,
                )
            except ValueError as e:  # noqa: PERF203
                QMessageBox(QMessageBox.Critical, "Failed to open file", str(universal_simplify_exception(e))).exec_()

    # endregion


class FolderObserver(FileSystemEventHandler):
    def __init__(self, window: ToolWindow):
        self.window: ToolWindow = window

    def on_any_event(self, event):
        if event.event_type not in {"created", "modified", "deleted", "moved"}:
            return
        if event.is_directory and event.event_type == "modified":
            return

        changes = [(event.src_path, event.event_type)]
        if event.event_type == "moved":
            changes = [(event.src_path, "deleted"), (event.dest_path, "created")]

        module_path = Path(self.window.active.module_path())
        override_path = Path(self.window.active.override_path())
        for path, event_type in changes:
            modified_path = Path(path)
            if modified_path == module_path or (
                not event.is_directory
                and modified_path.parent == module_path
                and is_capsule_file(modified_path.name)
            ):
                self.window.moduleFilesUpdated.emit(modified_path.name, event_type)
            elif modified_path.is_relative_to(override_path):
                self.window.overrideFilesUpdate.emit(str(modified_path), event_type)
