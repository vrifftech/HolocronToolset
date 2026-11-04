from __future__ import annotations

import math
from typing import TYPE_CHECKING

from pykotor.common.geometry import SurfaceMaterial, Vector2, Vector3, Vector4
from pykotor.common.misc import Color, ResRef
from pykotor.common.module import Module, ModuleResource
from pykotor.common.stream import BinaryWriter
from pykotor.extract.file import ResourceIdentifier
from pykotor.resource.generics.git import (
    GIT,
    GITCamera,
    GITCreature,
    GITDoor,
    GITEncounter,
    GITInstance,
    GITPlaceable,
    GITSound,
    GITStore,
    GITTrigger,
    GITWaypoint,
)
from pykotor.resource.generics.utd import read_utd
from pykotor.resource.generics.utt import read_utt
from pykotor.resource.generics.utw import read_utw
from pykotor.resource.type import ResourceType
from pykotor.tools import module
from PyQt5 import QtCore
from PyQt5.QtCore import QPoint, QTimer
from PyQt5.QtGui import QColor, QFont, QIcon, QKeyEvent, QPixmap
from PyQt5.QtWidgets import QAction, QCheckBox, QListWidgetItem, QMainWindow, QMenu, QMessageBox, QTreeWidgetItem, QWidget
from toolset.data.settings import settings_signals
from toolset.data.misc import ControlItem
from toolset.data.instance_history import InstanceEditHistory, clone_instance
from toolset.gui.dialogs.insert_instance import InsertInstanceDialog
from toolset.gui.dialogs.select_module import SelectModuleDialog
from toolset.gui.editors.git import openInstanceDialog
from toolset.gui.widgets.settings.installations import GlobalSettings
from toolset.gui.widgets.settings.module_designer import ModuleDesignerSettings
from toolset.gui.windows.help import HelpWindow
from toolset.utils.misc import QtMouse
from toolset.utils.window import openResourceEditor
from utility.logger_util import RobustRootLogger

if TYPE_CHECKING:
    from glm import vec3
    from pykotor.gl.scene import Camera
    from pykotor.resource.generics.are import ARE
    from pykotor.resource.generics.ifo import IFO
    from pykotor.tools.path import CaseAwarePath
    from toolset.data.installation import HTInstallation
    from toolset.gui.editor import Editor
    from toolset.gui.widgets.renderer.module import ModuleRenderer
    from toolset.gui.widgets.renderer.walkmesh import WalkmeshRenderer


class ModuleDesigner(QMainWindow):
    def __init__(self, parent: QWidget | None, installation: HTInstallation, *, module_root: str | None = None):
        """Initializes the Module Designer window.

        Args:
        ----
            parent: QWidget | None: Parent widget
            installation: HTInstallation: Hometuck installation
            module_root: Module to open directly; omit to show the module picker.

        Processing Logic:
        ----------------
            - Initializes UI elements and connects signals
            - Sets up 3D and 2D renderer controls
            - Populates resource tree and instance list
            - Sets window title and loads module on next frame.
        """
        super().__init__(parent)

        self._installation: HTInstallation = installation
        self._module: Module | None = None

        self.selectedInstances: list[GITInstance] = []
        self.settings: ModuleDesignerSettings = ModuleDesignerSettings()

        self.hideCreatures: bool = False
        self.hidePlaceables: bool = False
        self.hideDoors: bool = False
        self.hideTriggers: bool = False
        self.hideEncounters: bool = False
        self.hideWaypoints: bool = False
        self.hideSounds: bool = False
        self.hideStores: bool = False
        self.hideCameras: bool = False
        self.lockInstances: bool = False

        from toolset.uic.windows.module_designer import Ui_MainWindow

        self.ui = Ui_MainWindow()
        self.ui.setupUi(self)
        self._setupSignals()
        self.history = InstanceEditHistory(self, self._refreshHistory)
        self.history.add_actions(self.menuBar().addMenu("Edit"))
        self._geometryInstance = None
        self._freeCamToggleHeld = False
        reload_action = QAction("Reload 3D preview", self)
        reload_action.triggered.connect(self.ui.mainRenderer.reloadScene)
        self.ui.menuFile.addAction(reload_action)

        self._loadMaterialColors()
        settings_signals.changed.connect(self.reloadSettings)

        self.ui.flatRenderer.materialColors = self.materialColors
        self.ui.flatRenderer.hideWalkmeshEdges = True
        self.ui.flatRenderer.highlightBoundaries = False

        self._controls3d: ModuleDesignerControls3d = ModuleDesignerControls3d(self, self.ui.mainRenderer)
        self._controls2d: ModuleDesignerControls2d = ModuleDesignerControls2d(self, self.ui.flatRenderer)

        self._refreshWindowTitle()
        self.rebuildResourceTree()
        self.rebuildInstanceList()

        QTimer.singleShot(33, self.openModule if module_root is None else lambda: self.loadModule(module_root))

    def _setupSignals(self):
        """Connect signals to slots.

        Args:
        ----
            self: {The class instance}: Connects signals from UI elements to methods

        Processing Logic:
        ----------------
            - Connect menu actions to methods like open, save
            - Connect toggles of instance visibility checks to update method
            - Connect double clicks on checks and instance list to methods
            - Connect 3D renderer signals to mouse, key methods
            - Connect 2D renderer signals to mouse, key methods.
        """
        self.ui.actionOpen.triggered.connect(self.openModule)
        self.ui.actionSave.triggered.connect(self.saveGit)
        self.ui.actionOpen.setShortcut("Ctrl+O")
        self.ui.actionSave.setShortcut("Ctrl+S")
        self.ui.actionInstructions.triggered.connect(self.showHelpWindow)

        self.ui.resourceTree.customContextMenuRequested.connect(self.onResourceTreeContextMenu)

        self.ui.viewCreatureCheck.toggled.connect(self.updateToggles)
        self.ui.viewPlaceableCheck.toggled.connect(self.updateToggles)
        self.ui.viewDoorCheck.toggled.connect(self.updateToggles)
        self.ui.viewSoundCheck.toggled.connect(self.updateToggles)
        self.ui.viewTriggerCheck.toggled.connect(self.updateToggles)
        self.ui.viewEncounterCheck.toggled.connect(self.updateToggles)
        self.ui.viewWaypointCheck.toggled.connect(self.updateToggles)
        self.ui.viewCameraCheck.toggled.connect(self.updateToggles)
        self.ui.viewStoreCheck.toggled.connect(self.updateToggles)
        self.ui.backfaceCheck.toggled.connect(self.updateToggles)
        self.ui.lightmapCheck.toggled.connect(self.updateToggles)
        self.ui.cursorCheck.toggled.connect(self.updateToggles)

        self.ui.viewCreatureCheck.mouseDoubleClickEvent = lambda a0: self.onInstanceVisibilityDoubleClick(self.ui.viewCreatureCheck)  # noqa: ARG005
        self.ui.viewPlaceableCheck.mouseDoubleClickEvent = lambda a0: self.onInstanceVisibilityDoubleClick(self.ui.viewPlaceableCheck)  # noqa: ARG005
        self.ui.viewDoorCheck.mouseDoubleClickEvent = lambda a0: self.onInstanceVisibilityDoubleClick(self.ui.viewDoorCheck)  # noqa: ARG005
        self.ui.viewSoundCheck.mouseDoubleClickEvent = lambda a0: self.onInstanceVisibilityDoubleClick(self.ui.viewSoundCheck)  # noqa: ARG005
        self.ui.viewTriggerCheck.mouseDoubleClickEvent = lambda a0: self.onInstanceVisibilityDoubleClick(self.ui.viewTriggerCheck)  # noqa: ARG005
        self.ui.viewEncounterCheck.mouseDoubleClickEvent = lambda a0: self.onInstanceVisibilityDoubleClick(self.ui.viewEncounterCheck)  # noqa: ARG005
        self.ui.viewWaypointCheck.mouseDoubleClickEvent = lambda a0: self.onInstanceVisibilityDoubleClick(self.ui.viewWaypointCheck)  # noqa: ARG005
        self.ui.viewCameraCheck.mouseDoubleClickEvent = lambda a0: self.onInstanceVisibilityDoubleClick(self.ui.viewCameraCheck)  # noqa: ARG005
        self.ui.viewStoreCheck.mouseDoubleClickEvent = lambda a0: self.onInstanceVisibilityDoubleClick(self.ui.viewStoreCheck)  # noqa: ARG005

        self.ui.instanceList.doubleClicked.connect(self.onInstanceListDoubleClicked)
        self.ui.instanceList.customContextMenuRequested.connect(self.onInstanceListContextMenu)

        self.ui.mainRenderer.sceneInitalized.connect(self.on3dSceneInitialized)
        self.ui.mainRenderer.mousePressed.connect(self.on3dMousePressed)
        self.ui.mainRenderer.mouseMoved.connect(self.on3dMouseMoved)
        self.ui.mainRenderer.mouseScrolled.connect(self.on3dMouseScrolled)
        self.ui.mainRenderer.keyboardPressed.connect(self.on3dKeyboardPressed)
        self.ui.mainRenderer.objectSelected.connect(self.on3dObjectSelected)
        self.ui.mainRenderer.mouseReleased.connect(self.onMouseReleased)
        self.ui.mainRenderer.keyboardReleased.connect(self.onKeyboardReleased)
        self.ui.mainRenderer.inputReset.connect(self.onInputReset)
        self.ui.mainRenderer.tick.connect(self.onInputTick)

        self.ui.flatRenderer.mousePressed.connect(self.on2dMousePressed)
        self.ui.flatRenderer.mouseMoved.connect(self.on2dMouseMoved)
        self.ui.flatRenderer.mouseScrolled.connect(self.on2dMouseScrolled)
        self.ui.flatRenderer.keyPressed.connect(self.on2dKeyboardPressed)
        self.ui.flatRenderer.mouseReleased.connect(self.onMouseReleased)
        self.ui.flatRenderer.keyReleased.connect(self.onKeyboardReleased)
        self.ui.flatRenderer.inputReset.connect(self.onInputReset)

    def _refreshWindowTitle(self):
        if self._module is None:
            title = f"No Module - {self._installation.name} - Module Designer"
        else:
            title = f"{self._module.root()} - {self._installation.name} - Module Designer"
        self.setWindowTitle(title)

    def openModule(self):
        dialog = SelectModuleDialog(self, self._installation)
        if not dialog.exec_():
            return
        self.loadModule(dialog.module)

    def loadModule(self, root: str):
        try:
            mod_filepath = self._installation.module_path() / f"{root}.mod"
            if GlobalSettings().disableRIMSaving and not mod_filepath.is_file():
                module.rim_to_mod(mod_filepath)
                self._installation.load_modules()
            candidate = Module(root, self._installation)
            required = [("GIT", candidate.git()), ("LYT", candidate.layout()), ("IFO", candidate.info())]
            for label, resource in required:
                if resource is None or resource.resource() is None:
                    raise ValueError(f"The selected module has no readable {label} resource.")
            git = candidate.git().resource()
            walkmeshes = []
            for resource in candidate.resources.values():
                if resource.restype() is ResourceType.WOK:
                    mesh = resource.resource()
                    if mesh is not None:
                        walkmeshes.append(mesh)
        except Exception as error:
            RobustRootLogger().exception("Unable to load module '%s'", root)
            QMessageBox.critical(self, "Cannot open module", str(error))
            return
        self.unloadModule()
        self._module = candidate
        self.history.reset(git)
        self.ui.flatRenderer.setGit(git)
        self.ui.flatRenderer.setWalkmeshes(walkmeshes)
        self.ui.flatRenderer.centerCamera()
        self.ui.mainRenderer.init(self._installation, candidate)
        self._refreshWindowTitle()

    def unloadModule(self):
        self.history.finish()
        self.history.reset(None)
        self._geometryInstance = None
        self.selectedInstances.clear()
        self.ui.mainRenderer.clearScene()
        self.ui.flatRenderer.setGit(None)
        self.ui.flatRenderer.setWalkmeshes([])
        self.ui.flatRenderer.instanceSelection.clear()
        self.ui.flatRenderer.geometrySelection.clear()
        self._module = None

    def showHelpWindow(self):
        window = HelpWindow(self, "./help/tools/1-moduleEditor.md")
        window.show()

    def git(self) -> GIT:
        return self._module.git().resource()

    def are(self) -> ARE:
        return self._module.are().resource()
    def ifo(self) -> IFO:
        return self._module.info().resource()

    def saveGit(self):
        self.history.finish()
        if self._module is not None:
            self._module.git().save()
            self.history.setClean()

    def rebuildResourceTree(self):
        """Rebuilds the resource tree widget.

        Args:
        ----
            self: The class instance

        Rebuilds the resource tree widget by:
            - Clearing existing items
            - Enabling the tree
            - Grouping resources by type into categories
            - Adding category items and resource items
            - Sorting items alphabetically.
        """
        self.ui.resourceTree.clear()
        self.ui.resourceTree.setEnabled(True)

        # Only build if module is loaded
        if self._module is None:
            self.ui.resourceTree.setEnabled(False)
            return

        categories = {
            ResourceType.UTC: QTreeWidgetItem(["Creatures"]),
            ResourceType.UTP: QTreeWidgetItem(["Placeables"]),
            ResourceType.UTD: QTreeWidgetItem(["Doors"]),
            ResourceType.UTI: QTreeWidgetItem(["Items"]),
            ResourceType.UTE: QTreeWidgetItem(["Encounters"]),
            ResourceType.UTT: QTreeWidgetItem(["Triggers"]),
            ResourceType.UTW: QTreeWidgetItem(["Waypoints"]),
            ResourceType.UTS: QTreeWidgetItem(["Sounds"]),
            ResourceType.UTM: QTreeWidgetItem(["Merchants"]),
            ResourceType.DLG: QTreeWidgetItem(["Dialogs"]),
            ResourceType.FAC: QTreeWidgetItem(["Factions"]),
            ResourceType.MDL: QTreeWidgetItem(["Models"]),
            ResourceType.TGA: QTreeWidgetItem(["Textures"]),
            ResourceType.NCS: QTreeWidgetItem(["Scripts"]),
            ResourceType.IFO: QTreeWidgetItem(["Module Data"]),
            ResourceType.INVALID: QTreeWidgetItem(["Other"]),
        }
        categories[ResourceType.MDX] = categories[ResourceType.MDL]
        categories[ResourceType.WOK] = categories[ResourceType.MDL]
        categories[ResourceType.TPC] = categories[ResourceType.TGA]
        categories[ResourceType.IFO] = categories[ResourceType.IFO]
        categories[ResourceType.ARE] = categories[ResourceType.IFO]
        categories[ResourceType.GIT] = categories[ResourceType.IFO]
        categories[ResourceType.LYT] = categories[ResourceType.IFO]
        categories[ResourceType.VIS] = categories[ResourceType.IFO]
        categories[ResourceType.PTH] = categories[ResourceType.IFO]
        categories[ResourceType.NSS] = categories[ResourceType.NCS]

        for value in categories.values():
            self.ui.resourceTree.addTopLevelItem(value)

        for resource in self._module.resources.values():
            item = QTreeWidgetItem([f"{resource.resname()}.{resource.restype().extension}"])
            item.setData(0, QtCore.Qt.UserRole, resource)
            category = categories.get(resource.restype(), categories[ResourceType.INVALID])
            category.addChild(item)

        self.ui.resourceTree.sortByColumn(0, QtCore.Qt.AscendingOrder)
        self.ui.resourceTree.setSortingEnabled(True)

    def openModuleResource(self, resource: ModuleResource):
        editor: Editor | None = openResourceEditor(resource.active(), resource.resname(), resource.restype(), resource.data(),
                                    self._installation, self)[1]

        if editor is None:
            QMessageBox(
                QMessageBox.Critical,
                "Failed to open editor",
                f"Failed to open editor for file: {resource.resname()}.{resource.restype().extension}",
            )
        else:
            editor.savedFile.connect(lambda: self._onSavedResource(resource))

    def copyResourceToOverride(self, resource: ModuleResource):
        location = self._installation.override_path() / f"{resource.resname()}.{resource.restype().extension}"
        BinaryWriter.dump(location, resource.data())
        resource.add_locations([location])
        resource.activate(location)
        self._onSavedResource(resource)

    def activateResourceFile(self, resource: ModuleResource, location: str):
        resource.activate(location)
        self._onSavedResource(resource)

    def selectResourceItem(self, instance: GITInstance, clearExisting: bool = True):
        """Select a resource item in the tree.

        Args:
        ----
            instance: {The GIT instance to select}
            clearExisting: {Clear existing selections if True}.

        Processing Logic:
        ----------------
            1. Clear selection if clearExisting is True
            2. Iterate through top level items
            3. Iterate through child items of each top level
            4. Check if instance matches item and select it.
        """
        if clearExisting:
            self.ui.resourceTree.clearSelection()

        for i in range(self.ui.resourceTree.topLevelItemCount()):
            parent: QTreeWidgetItem | None = self.ui.resourceTree.topLevelItem(i)
            for j in range(parent.childCount()):
                item = parent.child(j)
                res: ModuleResource = item.data(0, QtCore.Qt.UserRole)
                if (
                    instance.identifier() is not None
                    and res.resname() == instance.identifier().resname
                    and res.restype() == instance.identifier().restype
                ):
                    parent.setExpanded(True)
                    item.setSelected(True)
                    self.ui.resourceTree.scrollToItem(item)

    def rebuildInstanceList(self):
        """Rebuilds the instance list.

        Args:
        ----
            self: The class instance

        Rebuilding Logic:
            - Clear existing instance list
            - Only rebuild if module is loaded
            - Filter instances based on visible type mappings
            - Add each instance to the list with icon, text, tooltips from the instance data.
        """
        self.ui.instanceList.clear()
        self.ui.instanceList.setEnabled(True)

        # Only build if module is loaded
        if self._module is None:
            self.ui.instanceList.setEnabled(False)
            return

        visibleMapping = {
            GITCreature: self.hideCreatures,
            GITPlaceable: self.hidePlaceables,
            GITDoor: self.hideDoors,
            GITTrigger: self.hideTriggers,
            GITEncounter: self.hideEncounters,
            GITWaypoint: self.hideWaypoints,
            GITSound: self.hideSounds,
            GITStore: self.hideStores,
            GITCamera: self.hideCameras,
            GITInstance: False,
        }
        iconMapping = {
            GITCreature: QPixmap(":/images/icons/k1/creature.png"),
            GITPlaceable: QPixmap(":/images/icons/k1/placeable.png"),
            GITDoor: QPixmap(":/images/icons/k1/door.png"),
            GITSound: QPixmap(":/images/icons/k1/sound.png"),
            GITTrigger: QPixmap(":/images/icons/k1/trigger.png"),
            GITEncounter: QPixmap(":/images/icons/k1/encounter.png"),
            GITWaypoint: QPixmap(":/images/icons/k1/waypoint.png"),
            GITCamera: QPixmap(":/images/icons/k1/camera.png"),
            GITStore: QPixmap(":/images/icons/k1/merchant.png"),
            GITInstance: QPixmap(32, 32),
        }

        self.ui.instanceList.clear()
        items: list[QListWidgetItem] = []

        for instance in self._module.git().resource().instances():
            if visibleMapping[type(instance)]:
                continue

            struct_index: int = self._module.git().resource().index(instance)

            icon = QIcon(iconMapping[type(instance)])
            item = QListWidgetItem(icon, "")
            font: QFont = item.font()

            if isinstance(instance, GITCamera):
                item.setText(f"Camera #{instance.camera_id}")
                item.setToolTip(f"Struct Index: {struct_index}\nCamera ID: {instance.camera_id}\nFOV: {instance.fov}")
                item.setData(QtCore.Qt.UserRole + 1, "cam" + str(instance.camera_id).rjust(10, "0"))
            else:
                resource: ModuleResource[ARE] | None = self._module.resource(instance.identifier().resname, instance.identifier().restype)
                filename: str = instance.identifier().resname
                name: str = filename
                tag: str = ""

                resourceExists: bool = resource is not None and resource.resource() is not None
                if isinstance(instance, (GITDoor, GITTrigger)) and resourceExists:
                    # Tag is stored in the GIT
                    name = resource.localized_name() or filename
                    tag = instance.tag
                elif isinstance(instance, GITWaypoint):
                    # Name and tag are stored in the GIT
                    name = self._installation.string(instance.name)
                    tag = instance.tag
                elif resourceExists:
                    name = resource.localized_name() or filename
                    tag = resource.resource().tag

                if resource is None:
                    font.setItalic(True)

                item.setText(name)
                item.setToolTip(f"Struct Index: {struct_index}\nResRef: {filename}\nName: {name}\nTag: {tag}")
                item.setData(QtCore.Qt.UserRole + 1, instance.identifier().restype.extension + name)

            item.setFont(font)
            item.setData(QtCore.Qt.UserRole, instance)
            items.append(item)

        for item in sorted(items, key=lambda i: i.data(QtCore.Qt.UserRole + 1)):
            self.ui.instanceList.addItem(item)

    def selectInstanceItemOnList(self, instance: GITInstance):
        """Select an instance item on the instance list.

        Args:
        ----
            instance (GITInstance): The instance to select

        Processing Logic:
        ----------------
            - Clear any existing selection from the instance list
            - Iterate through each item in the instance list
            - Check if the item's stored data matches the passed in instance
            - If so, select the item and scroll it into view.
        """
        self.ui.instanceList.clearSelection()
        for i in range(self.ui.instanceList.count()):
            item: QListWidgetItem | None = self.ui.instanceList.item(i)
            data: GITInstance = item.data(QtCore.Qt.UserRole)
            if data is instance:
                item.setSelected(True)
                self.ui.instanceList.scrollToItem(item)

    def updateToggles(self):
        scene = self.ui.mainRenderer.scene
        controls = (
            ("Creatures", "creatures", "Creature"), ("Placeables", "placeables", "Placeable"),
            ("Doors", "doors", "Door"), ("Triggers", "triggers", "Trigger"),
            ("Encounters", "encounters", "Encounter"), ("Waypoints", "waypoints", "Waypoint"),
            ("Sounds", "sounds", "Sound"), ("Stores", "stores", "Store"),
            ("Cameras", "cameras", "Camera"),
        )
        for attr, scene_attr, control in controls:
            hidden = not getattr(self.ui, "view" + control + "Check").isChecked()
            setattr(self, "hide" + attr, hidden)
            setattr(self.ui.flatRenderer, "hide" + attr, hidden)
            if scene is not None:
                setattr(scene, "hide_" + scene_attr, hidden)
        if scene is not None:
            scene.backface_culling = self.ui.backfaceCheck.isChecked()
            scene.use_lightmap = self.ui.lightmapCheck.isChecked()
            scene.show_cursor = self.ui.cursorCheck.isChecked()
        self.rebuildInstanceList()

    def addInstance(self, instance, walkmeshSnap=True):
        if self._module is None:
            return
        if walkmeshSnap:
            instance.position.z = self.ui.mainRenderer.walkmeshPoint(instance.position.x, instance.position.y, instance.position.z).z
        if isinstance(instance, GITCamera):
            instance.camera_id = max((camera.camera_id for camera in self.git().cameras), default=-1) + 1
        else:
            dialog = InsertInstanceDialog(self, self._installation, self._module, instance.identifier().restype)
            if not dialog.exec_():
                return
            self.rebuildResourceTree()
            instance.resref = ResRef(dialog.resname)
            if isinstance(instance, GITWaypoint):
                template = read_utw(dialog.data)
                instance.tag, instance.name = template.tag, template.name
            elif isinstance(instance, GITTrigger):
                instance.tag = read_utt(dialog.data).tag
            elif isinstance(instance, GITDoor):
                instance.tag = read_utd(dialog.data).tag
        with self.history.edit("Insert instance", membership=True):
            self.git().add(instance)
        self.setSelection([instance])


    def editInstance(self, instance):
        self.history.finish()
        self.history.begin("Edit instance", [instance])
        try:
            accepted = openInstanceDialog(self, instance, self._installation)
        except BaseException:
            self.history.cancel()
            raise
        if accepted:
            self.history.finish()
        else:
            self.history.cancel()

    def snapCameraToView(self, instance):
        camera = self.ui.mainRenderer.scene.camera
        view = camera.true_position()
        with self.history.edit("Snap GIT camera to view", [instance]):
            instance.pitch = 0
            instance.height = 0
            instance.position = Vector3(view.x, view.y, view.z)
            instance.orientation = Vector4.from_euler(math.pi / 2 - camera.yaw, 0, math.pi - camera.pitch)

    def snapViewToCamera(self, instance: GITCamera):
        camera: Camera = self.ui.mainRenderer.scene.camera
        euler: Vector3 = instance.orientation.to_euler()
        camera.pitch = math.pi - euler.z - math.radians(instance.pitch)
        camera.yaw = math.pi / 2 - euler.x
        camera.x = instance.position.x
        camera.y = instance.position.y
        camera.z = instance.position.z + instance.height
        camera.distance = 0

    def snapCameraToEntryLocation(self):
        self.ui.mainRenderer.scene.camera.x = self.ifo().entry_position.x
        self.ui.mainRenderer.scene.camera.y = self.ifo().entry_position.y
        self.ui.mainRenderer.scene.camera.z = self.ifo().entry_position.z

    def toggleFreeCam(self):
        if self.ui.mainRenderer.scene is None or self.ui.mainRenderer._renderError is not None:
            return
        self.history.finish()
        if isinstance(self._controls3d, ModuleDesignerControls3d):
            self._controls3d = ModuleDesignerControlsFreeCam(self, self.ui.mainRenderer)
        else:
            self._controls3d = ModuleDesignerControls3d(self, self.ui.mainRenderer)
        self.ui.mainRenderer._lastTick = None

    # region Selection Manipulations
    def setSelection(self, instances):
        self.history.finish()
        if self._module is None:
            instances = []
        self.selectedInstances = list(instances)
        scene = self.ui.mainRenderer.scene
        if scene is not None:
            scene.selection.clear()
            for instance in instances:
                if instance in scene.objects:
                    scene.selection.append(scene.objects[instance])
        self.ui.flatRenderer.instanceSelection.select(instances)
        if instances:
            self.selectInstanceItemOnList(instances[0])
            self.selectResourceItem(instances[0])

    def deleteSelected(self):
        if self._module is None or self.ui.lockInstancesCheck.isChecked():
            return
        selection = list(self.selectedInstances)
        if not selection:
            return
        with self.history.edit("Delete instances", selection, membership=True):
            for instance in selection:
                self.git().remove(instance)

    def moveSelected(self, x: float, y: float, z: float | None = None):
        """Moves selected instances by the given offsets.

        Args:
        ----
            x: Float offset to move instances along the x-axis.
            y: Float offset to move instances along the y-axis.
            z: Float offset to move instances along the z-axis or None.

        Processing Logic:
        ----------------
            - Checks if instance locking is enabled and returns if True
            - Loops through selected instances
            - Increases x, y position by offsets
            - Increases z position by offset if provided, else sets to walkmesh height
            - No return, modifies instances in place.
        """
        if self.ui.lockInstancesCheck.isChecked():
            return

        self.history.begin("Move instances", self.selectedInstances)
        for instance in self.selectedInstances:
            instance.position.x += x
            instance.position.y += y
            if z is None:
                instance.position.z = self.ui.mainRenderer.walkmeshPoint(instance.position.x, instance.position.y).z
            else:
                instance.position.z += z

    def rotateSelected(self, x: float, y: float):
        if self.ui.lockInstancesCheck.isChecked():
            return

        self.history.begin("Rotate instances", self.selectedInstances)
        for instance in self.selectedInstances:
            instance.rotate(x / 60, y / 60, 0.0)

    # endregion

    # region Signal Callbacks
    def _onSavedResource(self, resource: ModuleResource):
        self.history.finish()
        resource.reload()
        if resource.restype() is ResourceType.GIT:
            # A separately saved/activated document starts a new undo history.
            git = self.git()
            self.history.reset(git)
            self.selectedInstances.clear()
            self._geometryInstance = None
            self.ui.flatRenderer.setGit(git)
            self.ui.flatRenderer.instanceSelection.clear()
            self.ui.flatRenderer.geometrySelection.clear()
            self.ui.flatRenderer.hideGeomPoints = True
            self.rebuildInstanceList()
        if self.ui.mainRenderer.scene is not None:
            self.ui.mainRenderer.scene.clearCacheBuffer.append(ResourceIdentifier(resource.resname(), resource.restype()))

    def onInstanceListDoubleClicked(self):
        if self.ui.instanceList.selectedItems():
            item: QListWidgetItem = self.ui.instanceList.selectedItems()[0]
            instance: GITInstance = item.data(QtCore.Qt.UserRole)
            self.setSelection([instance])
            self.ui.mainRenderer.snapCameraToPoint(instance.position)
            self.ui.flatRenderer.snapCameraToPoint(instance.position)

    def onInstanceVisibilityDoubleClick(self, checkbox: QCheckBox):
        """Toggles visibility of a single instance type on double click.

        This method should be called whenever one of the instance visibility checkboxes have been double clicked. The
        resulting affect should be that all checkboxes become unchecked except for the one that was pressed.

        Args:
        ----
            checkbox (QCheckBox): Checkbox that was double clicked.

        Processing Logic:
        ----------------
            - Unchecks all other instance type checkboxes
            - Checks the checkbox that was double clicked
            - This ensures only one instance type is visible at a time
        """
        self.ui.viewCreatureCheck.setChecked(False)
        self.ui.viewPlaceableCheck.setChecked(False)
        self.ui.viewDoorCheck.setChecked(False)
        self.ui.viewSoundCheck.setChecked(False)
        self.ui.viewTriggerCheck.setChecked(False)
        self.ui.viewEncounterCheck.setChecked(False)
        self.ui.viewWaypointCheck.setChecked(False)
        self.ui.viewCameraCheck.setChecked(False)
        self.ui.viewStoreCheck.setChecked(False)

        checkbox.setChecked(True)

    def onResourceTreeContextMenu(self, point: QPoint):
        menu = QMenu(self)

        data = self.ui.resourceTree.currentItem().data(0, QtCore.Qt.UserRole)
        if isinstance(data, ModuleResource):
            self._build_active_override_menu(data, menu)
        menu.exec_(self.ui.resourceTree.mapToGlobal(point))

    def _build_active_override_menu(self, data: ModuleResource, menu: QMenu):
        """Builds an active override menu for a module resource.

        Args:
        ----
            data: ModuleResource - The module resource data
            menu: QMenu - The menu to build actions on

        Processing Logic:
        ----------------
            - Adds actions to edit active file, reload active file, and copy to override
            - Loops through each location in the resource and adds an action
            - Disables the active location action
            - Disables copy to override if a location contains 'override'
            - Connects all actions to trigger appropriate functions.
        """
        copyToOverrideAction = QAction("Copy To Override", self)
        copyToOverrideAction.triggered.connect(lambda _, r=data: self.copyResourceToOverride(r))

        menu.addAction("Edit Active File").triggered.connect(lambda _, r=data: self.openModuleResource(r))
        menu.addAction("Reload Active File").triggered.connect(lambda _: data.reload())
        menu.addAction(copyToOverrideAction)
        menu.addSeparator()
        for location in data.locations():
            locationAction = QAction(str(location), self)
            locationAction.triggered.connect(lambda _, loc=location: self.activateResourceFile(data, loc))
            if location == data.active():
                locationAction.setEnabled(False)
            lowercase_parts: list[str] = [part.lower() for part in location.parts]
            if "override" in lowercase_parts:
                copyToOverrideAction.setEnabled(False)
            menu.addAction(locationAction)

    def on3dMouseMoved(self, screen: Vector2, screenDelta: Vector2, world: Vector3, buttons: set[int], keys: set[int]):
        if self.ui.mainRenderer.scene is None or self.ui.mainRenderer._renderError is not None:
            return
        self._controls3d.onMouseMoved(screen, screenDelta, world, buttons, keys)

    def on3dMouseScrolled(self, delta: Vector2, buttons: set[int], keys: set[int]):
        if self.ui.mainRenderer.scene is None or self.ui.mainRenderer._renderError is not None:
            return
        self._controls3d.onMouseScrolled(delta, buttons, keys)

    def on3dMousePressed(self, screen: Vector2, buttons: set[int], keys: set[int]):
        if self.ui.mainRenderer.scene is None or self.ui.mainRenderer._renderError is not None:
            return
        self._controls3d.onMousePressed(screen, buttons, keys)

    def on3dKeyboardPressed(self, buttons, keys):
        if self._module is None or self.ui.mainRenderer.scene is None or self.ui.mainRenderer._renderError is not None:
            return
        toggle = ControlItem(self.settings.toggleFreeCam3dBind).satisfied(buttons, keys)
        if toggle:
            if not self._freeCamToggleHeld:
                self._freeCamToggleHeld = True
                self.toggleFreeCam()
            return
        self._controls3d.onKeyboardPressed(buttons, keys)

    def on3dObjectSelected(self, instance: GITInstance):
        if instance is not None:
            self.setSelection([instance])
        else:
            self.setSelection([])

    def onContextMenu(self, world: Vector3, point: QPoint):
        if self._module is None:
            return

        if not self.selectedInstances:
            self.onContextMenuSelectionNone(world)
        else:
            self.onContextMenuSelectionExists()

    def onContextMenuSelectionNone(self, world: Vector3):
        """Displays a context menu for object insertion.

        Args:
        ----
            world: (Vector3): World position for context menu

        Processing Logic:
        ----------------
            - Creates a QMenu object
            - Adds actions to menu for inserting different object types at world position or view position
            - Connects actions to addInstance method
            - Pops up menu at mouse cursor position
            - Connects menu hide signal to reset mouse buttons
        """
        menu = QMenu(self)

        menu.addAction("Insert Camera").triggered.connect(lambda: self.addInstance(GITCamera(*world), False))
        scene = self.ui.mainRenderer.scene
        if scene is not None and self.ui.mainRenderer._renderError is None:
            view = scene.camera.true_position()
            rot = scene.camera
            menu.addAction("Insert Camera at View").triggered.connect(lambda: self.addInstance(GITCamera(view.x, view.y, view.z, rot.yaw, rot.pitch, 0, 0), False))
        menu.addSeparator()
        menu.addAction("Insert Creature").triggered.connect(lambda: self.addInstance(GITCreature(*world), True))
        menu.addAction("Insert Door").triggered.connect(lambda: self.addInstance(GITDoor(*world), False))
        menu.addAction("Insert Placeable").triggered.connect(lambda: self.addInstance(GITPlaceable(*world), False))
        menu.addAction("Insert Store").triggered.connect(lambda: self.addInstance(GITStore(*world), False))
        menu.addAction("Insert Sound").triggered.connect(lambda: self.addInstance(GITSound(*world), False))
        menu.addAction("Insert Waypoint").triggered.connect(lambda: self.addInstance(GITWaypoint(*world), False))
        menu.addAction("Insert Encounter").triggered.connect(lambda: self.addInstance(GITEncounter(*world), False))
        menu.addAction("Insert Trigger").triggered.connect(lambda: self.addInstance(GITTrigger(*world), False))

        menu.popup(self.cursor().pos())
        menu.aboutToHide.connect(self.ui.mainRenderer.resetInput)
        menu.aboutToHide.connect(self.ui.flatRenderer.resetInput)

    def onContextMenuSelectionExists(self):
        """Checks if a context menu selection exists.

        Args:
        ----
            self: The class instance

        Processing Logic:
        ----------------
            - Checks if any instances are selected
            - If a camera instance is selected, adds camera-view snapping actions
            - Always adds edit and remove actions
            - Pops up the context menu at the mouse cursor position
            - Resets mouse buttons when menu closes.
        """
        menu = QMenu(self)

        if self.selectedInstances:
            instance = self.selectedInstances[0]
            if isinstance(instance, GITCamera) and self.ui.mainRenderer.scene is not None and self.ui.mainRenderer._renderError is None:
                menu.addAction("Snap Camera to 3D View").triggered.connect(lambda: self.snapCameraToView(instance))
                menu.addAction("Snap 3D View to Camera").triggered.connect(lambda: self.snapViewToCamera(instance))
                menu.addSeparator()

            menu.addAction("Edit Instance").triggered.connect(lambda: self.editInstance(instance))
            if isinstance(instance, (GITTrigger, GITEncounter)):
                menu.addAction("Edit Geometry in 2D").triggered.connect(lambda: self.editGeometry(instance))
            menu.addAction("Remove").triggered.connect(self.deleteSelected)

        menu.popup(self.cursor().pos())
        menu.aboutToHide.connect(self.ui.mainRenderer.resetInput)
        menu.aboutToHide.connect(self.ui.flatRenderer.resetInput)

    def on3dSceneInitialized(self):
        self.rebuildResourceTree()
        self.rebuildInstanceList()
        self._refreshWindowTitle()
        self.updateToggles()

    def on2dMouseMoved(self, screen: Vector2, delta: Vector2, buttons: set[int], keys: set[int]):
        if self._module is None:
            return
        worldDelta: Vector2 = self.ui.flatRenderer.toWorldDelta(delta.x, delta.y)
        world: Vector3 = self.ui.flatRenderer.toWorldCoords(screen.x, screen.y)
        self._controls2d.onMouseMoved(screen, delta, Vector2.from_vector3(world), worldDelta, buttons, keys)

    def on2dMouseScrolled(self, delta: Vector2, buttons: set[int], keys: set[int]):
        self._controls2d.onMouseScrolled(delta, buttons, keys)

    def on2dMousePressed(self, screen, buttons, keys):
        if self._module is None:
            return
        if self._geometryInstance is not None:
            renderer = self.ui.flatRenderer
            if self._controls2d.openContextMenu.satisfied(buttons, keys):
                self.geometryContextMenu(renderer.toWorldCoords(screen.x, screen.y), renderer.mapToGlobal(QPoint(int(screen.x), int(screen.y))))
            elif self._controls2d.selectUnderneath.satisfied(buttons, keys):
                renderer.geometrySelection.select([point for point in renderer.geomPointsUnderMouse() if point.instance is self._geometryInstance])
            return
        self._controls2d.onMousePressed(screen, buttons, keys)

    def on2dKeyboardPressed(self, buttons, keys):
        if self._module is None:
            return
        if self._geometryInstance is not None and self._controls2d.deleteSelected.satisfied(buttons, keys):
            self.deleteGeometryPoints()
            return
        self._controls2d.onKeyboardPressed(buttons, keys)

    # endregion

    # region Events
    def keyPressEvent(self, event, bubble=True):
        if self.ui.mainRenderer.hasFocus():
            self.ui.mainRenderer.keyPressEvent(event)
        elif self.ui.flatRenderer.hasFocus():
            self.ui.flatRenderer.keyPressEvent(event)
        else:
            super().keyPressEvent(event)

    def keyReleaseEvent(self, event, bubble=True):
        if self.ui.mainRenderer.hasFocus():
            self.ui.mainRenderer.keyReleaseEvent(event)
        elif self.ui.flatRenderer.hasFocus():
            self.ui.flatRenderer.keyReleaseEvent(event)
        else:
            super().keyReleaseEvent(event)

    def _refreshHistory(self):
        if self._module is None:
            return
        alive = self.git().instances()
        self.selectedInstances = [obj for obj in self.selectedInstances if obj in alive]
        self.ui.flatRenderer.instanceSelection.select(self.selectedInstances)
        self.ui.flatRenderer.geometrySelection.clear()
        if self._geometryInstance not in alive:
            self._geometryInstance = None
            self.ui.flatRenderer.hideGeomPoints = True
        scene = self.ui.mainRenderer.scene
        if scene is not None:
            # Geometry/material views are caches, not the document itself.
            scene.objects = {key: value for key, value in scene.objects.items() if not isinstance(key, GITInstance)}
            scene.selection.clear()
        self.rebuildInstanceList()
        self.ui.flatRenderer.update()
        self.ui.mainRenderer.update()

    def duplicateSelected(self, position):
        if not self.selectedInstances or self.ui.lockInstancesCheck.isChecked():
            return
        offset = position - self.selectedInstances[-1].position
        copies = []
        with self.history.edit("Duplicate instances", membership=True):
            for original in self.selectedInstances:
                instance = clone_instance(original)
                instance.position = instance.position + offset
                if isinstance(instance, GITCamera):
                    instance.camera_id = max((camera.camera_id for camera in self.git().cameras), default=-1) + 1
                self.git().add(instance)
                copies.append(instance)
        self.setSelection(copies)

    def onMouseReleased(self, screen, buttons, keys):
        if not buttons:
            self.history.finish()

    def onKeyboardReleased(self, buttons, keys):
        if not ControlItem(self.settings.toggleFreeCam3dBind).satisfied(buttons, keys):
            self._freeCamToggleHeld = False
        if not buttons:
            self.history.finish()

    def onInputReset(self):
        self.history.finish()
        self._freeCamToggleHeld = False
        if self.ui.mainRenderer.freeCam:
            self._controls3d = ModuleDesignerControls3d(self, self.ui.mainRenderer)

    def onInputTick(self, elapsed):
        if self._module is None or self.ui.mainRenderer.scene is None:
            return
        scene = self.ui.mainRenderer.scene
        scene.selection = [scene.objects[item] for item in self.selectedInstances if item in scene.objects]
        self._controls3d.moveHeld(elapsed)

    def onInstanceListContextMenu(self, point):
        item = self.ui.instanceList.itemAt(point)
        if item is None:
            return
        self.setSelection([item.data(QtCore.Qt.UserRole)])
        self.onContextMenuSelectionExists()

    def editGeometry(self, instance):
        if not isinstance(instance, (GITTrigger, GITEncounter)):
            return
        self.history.finish()
        self.setSelection([instance])
        self._geometryInstance = instance
        self.ui.flatRenderer.geometrySelection.clear()
        self.ui.flatRenderer.hideGeomPoints = False
        self.ui.flatRenderer.snapCameraToPoint(instance.position)
        self.ui.flatRenderer.setFocus()

    def finishGeometry(self):
        self.history.finish()
        self._geometryInstance = None
        self.ui.flatRenderer.geometrySelection.clear()
        self.ui.flatRenderer.hideGeomPoints = True

    def insertGeometryPoint(self, world):
        instance = self._geometryInstance
        if instance is None or self.ui.lockInstancesCheck.isChecked():
            return
        with self.history.edit("Insert geometry point", [instance]):
            instance.geometry.append(world - instance.position)

    def deleteGeometryPoints(self):
        instance = self._geometryInstance
        if instance is None or self.ui.lockInstancesCheck.isChecked():
            return
        ids = {id(vertex.point) for vertex in self.ui.flatRenderer.geometrySelection.all() if vertex.instance is instance}
        with self.history.edit("Delete geometry points", [instance]):
            instance.geometry.points[:] = [point for point in instance.geometry.points if id(point) not in ids]

    def geometryContextMenu(self, world, point):
        menu = QMenu(self)
        if self.ui.flatRenderer.geometrySelection.all():
            menu.addAction("Remove Point").triggered.connect(self.deleteGeometryPoints)
        menu.addAction("Insert Point").triggered.connect(lambda _=False: self.insertGeometryPoint(world))
        menu.addAction("Finish Editing Geometry").triggered.connect(self.finishGeometry)
        menu.aboutToHide.connect(self.ui.flatRenderer.resetInput)
        menu.popup(point)

    def closeEvent(self, event):
        self.history.finish()
        self.ui.mainRenderer.clearScene()
        self.ui.flatRenderer.resetInput()
        super().closeEvent(event)

    def _loadMaterialColors(self):
        def intColorToQColor(intvalue) -> QColor:
            """Converts an integer color value to a QColor object.

            Args:
            ----
                intvalue: Integer color value.

            Returns:
            -------
                QColor: QColor object representing the color

            Processing Logic:
            ----------------
                - Extract RGBA components from integer color value using Color.from_rgba_integer()
                - Multiply each component by 255 to convert to QColor expected value range of 0-255
                - Pass converted values to QColor constructor to return QColor object.
            """
            color = Color.from_rgba_integer(intvalue)
            return QColor(int(color.r * 255), int(color.g * 255), int(color.b * 255), int(color.a * 255))

        self.materialColors: dict[SurfaceMaterial, QColor] = {
            SurfaceMaterial.UNDEFINED: intColorToQColor(self.settings.undefinedMaterialColour),
            SurfaceMaterial.OBSCURING: intColorToQColor(self.settings.obscuringMaterialColour),
            SurfaceMaterial.DIRT: intColorToQColor(self.settings.dirtMaterialColour),
            SurfaceMaterial.GRASS: intColorToQColor(self.settings.grassMaterialColour),
            SurfaceMaterial.STONE: intColorToQColor(self.settings.stoneMaterialColour),
            SurfaceMaterial.WOOD: intColorToQColor(self.settings.woodMaterialColour),
            SurfaceMaterial.WATER: intColorToQColor(self.settings.waterMaterialColour),
            SurfaceMaterial.NON_WALK: intColorToQColor(self.settings.nonWalkMaterialColour),
            SurfaceMaterial.TRANSPARENT: intColorToQColor(self.settings.transparentMaterialColour),
            SurfaceMaterial.CARPET: intColorToQColor(self.settings.carpetMaterialColour),
            SurfaceMaterial.METAL: intColorToQColor(self.settings.metalMaterialColour),
            SurfaceMaterial.PUDDLES: intColorToQColor(self.settings.puddlesMaterialColour),
            SurfaceMaterial.SWAMP: intColorToQColor(self.settings.swampMaterialColour),
            SurfaceMaterial.MUD: intColorToQColor(self.settings.mudMaterialColour),
            SurfaceMaterial.LEAVES: intColorToQColor(self.settings.leavesMaterialColour),
            SurfaceMaterial.LAVA: intColorToQColor(self.settings.lavaMaterialColour),
            SurfaceMaterial.BOTTOMLESS_PIT: intColorToQColor(self.settings.bottomlessPitMaterialColour),
            SurfaceMaterial.DEEP_WATER: intColorToQColor(self.settings.deepWaterMaterialColour),
            SurfaceMaterial.DOOR: intColorToQColor(self.settings.doorMaterialColour),
            SurfaceMaterial.NON_WALK_GRASS: intColorToQColor(self.settings.nonWalkGrassMaterialColour),
            SurfaceMaterial.TRIGGER: intColorToQColor(self.settings.nonWalkGrassMaterialColour),
        }

    def reloadSettings(self):
        self.history.finish()
        self.settings = ModuleDesignerSettings()
        self._loadMaterialColors()
        self.ui.flatRenderer.materialColors = self.materialColors
        self.ui.mainRenderer.resetInput()
        self.ui.flatRenderer.resetInput()
        self._controls3d = ModuleDesignerControls3d(self, self.ui.mainRenderer)
        self._controls2d = ModuleDesignerControls2d(self, self.ui.flatRenderer)
        self.ui.mainRenderer.settings = ModuleDesignerSettings()
        if self.ui.mainRenderer.scene is not None:
            self.ui.mainRenderer.scene.camera.fov = self.settings.fieldOfView
        self.ui.flatRenderer.update()
        self.ui.mainRenderer.update()

    # endregion


class ModuleDesignerControls3d:
    def __init__(self, editor: ModuleDesigner, renderer: ModuleRenderer):
        """Initializes the 3D view controller.

        Args:
        ----
            editor: ModuleDesigner - The module designer instance
            renderer: ModuleRenderer - The 3D renderer instance

        Processing Logic:
        ----------------
            - Initializes control items from settings bindings
            - Sets initial scene and renderer properties
            - Hides cursor if setting is unchecked.
        """
        self.editor: ModuleDesigner = editor
        self.settings: ModuleDesignerSettings = ModuleDesignerSettings()
        self.renderer: ModuleRenderer = renderer

        self.moveXYCamera: ControlItem = ControlItem(self.settings.moveCameraXY3dBind)
        self.moveZCamera: ControlItem = ControlItem(self.settings.moveCameraZ3dBind)
        self.moveCameraPlane: ControlItem = ControlItem(self.settings.moveCameraPlane3dBind)
        self.rotateCamera: ControlItem = ControlItem(self.settings.rotateCamera3dBind)
        self.zoomCamera: ControlItem = ControlItem(self.settings.zoomCamera3dBind)
        self.zoomCameraMM: ControlItem = ControlItem(self.settings.zoomCameraMM3dBind)
        self.rotateSelected: ControlItem = ControlItem(self.settings.rotateSelected3dBind)
        self.moveXYSelected: ControlItem = ControlItem(self.settings.moveSelectedXY3dBind)
        self.moveZSelected: ControlItem = ControlItem(self.settings.moveSelectedZ3dBind)
        self.selectUnderneath: ControlItem = ControlItem(self.settings.selectObject3dBind)
        self.moveCameraToSelected: ControlItem = ControlItem(self.settings.moveCameraToSelected3dBind)
        self.moveCameraToCursor: ControlItem = ControlItem(self.settings.moveCameraToCursor3dBind)
        self.moveCameraToEntryPoint: ControlItem = ControlItem(self.settings.moveCameraToEntryPoint3dBind)
        self.toggleFreeCam: ControlItem = ControlItem(self.settings.toggleFreeCam3dBind)
        self.deleteSelected: ControlItem = ControlItem(self.settings.deleteObject3dBind)
        self.duplicateSelected: ControlItem = ControlItem(self.settings.duplicateObject3dBind)
        self.openContextMenu: ControlItem = ControlItem((set(), {QtMouse.RightButton}))
        self.rotateCameraLeft: ControlItem = ControlItem(self.settings.rotateCameraLeft3dBind)
        self.rotateCameraRight: ControlItem = ControlItem(self.settings.rotateCameraRight3dBind)
        self.rotateCameraUp: ControlItem = ControlItem(self.settings.rotateCameraUp3dBind)
        self.rotateCameraDown: ControlItem = ControlItem(self.settings.rotateCameraDown3dBind)
        self.moveCameraUp: ControlItem = ControlItem(self.settings.moveCameraUp3dBind)
        self.moveCameraDown: ControlItem = ControlItem(self.settings.moveCameraDown3dBind)
        self.moveCameraForward: ControlItem = ControlItem(self.settings.moveCameraForward3dBind)
        self.moveCameraBackward: ControlItem = ControlItem(self.settings.moveCameraBackward3dBind)
        self.moveCameraLeft: ControlItem = ControlItem(self.settings.moveCameraLeft3dBind)
        self.moveCameraRight: ControlItem = ControlItem(self.settings.moveCameraRight3dBind)
        self.zoomCameraIn: ControlItem = ControlItem(self.settings.zoomCameraIn3dBind)
        self.zoomCameraOut: ControlItem = ControlItem(self.settings.zoomCameraOut3dBind)
        self.toggleInstanceLock: ControlItem = ControlItem(self.settings.toggleLockInstancesBind)

        if self.renderer.scene is not None:
            self.renderer.scene.show_cursor = self.editor.ui.cursorCheck.isChecked()
        self.renderer.freeCam = False
        self.renderer.setCursor(QtCore.Qt.CursorShape.ArrowCursor)

    def onMouseScrolled(self, delta: Vector2, buttons: set[int], keys: set[int]):
        if self.zoomCamera.satisfied(buttons, keys):
            strength = self.settings.zoomCameraSensitivity3d / 2000
            self.renderer.scene.camera.distance += -delta.y * strength

        if self.moveZCamera.satisfied(buttons, keys):
            strength = self.settings.moveCameraSensitivity3d / 1000
            self.renderer.scene.camera.z -= -delta.y * strength

    def onMouseMoved(self, screen, screenDelta, world, buttons, keys):
        if self.moveXYCamera.satisfied(buttons, keys):
            forward = -screenDelta.y * self.renderer.scene.camera.forward()
            sideward = screenDelta.x * self.renderer.scene.camera.sideward()
            strength = self.settings.moveCameraSensitivity3d / 1000
            self.renderer.scene.camera.x -= (forward.x + sideward.x) * strength
            self.renderer.scene.camera.y -= (forward.y + sideward.y) * strength
            return
        if self.moveCameraPlane.satisfied(buttons, keys):
            upward = screenDelta.y * self.renderer.scene.camera.upward(ignore_xy=False)
            sideward = screenDelta.x * self.renderer.scene.camera.sideward()
            strength = self.settings.moveCameraSensitivity3d / 1000
            self.renderer.scene.camera.z -= (upward.z + sideward.z) * strength
            self.renderer.scene.camera.y -= (upward.y + sideward.y) * strength
            self.renderer.scene.camera.x -= (upward.x + sideward.x) * strength
            return
        if self.rotateCamera.satisfied(buttons, keys):
            strength = self.settings.rotateCameraSensitivity3d / 10000
            self.renderer.rotateCamera(-screenDelta.x * strength, screenDelta.y * strength)
            return
        if self.zoomCameraMM.satisfied(buttons, keys):
            self.renderer.scene.camera.distance -= screenDelta.y * self.settings.zoomCameraSensitivity3d / 5000
            return
        if self.editor.ui.lockInstancesCheck.isChecked() or not self.editor.selectedInstances:
            return
        if self.moveXYSelected.satisfied(buttons, keys):
            target = self.renderer.scene.cursor.position()
            anchor = self.editor.selectedInstances[-1].position
            self.editor.moveSelected(target.x - anchor.x, target.y - anchor.y)
        elif self.moveZSelected.satisfied(buttons, keys):
            self.editor.moveSelected(0, 0, -screenDelta.y / 40)
        elif self.rotateSelected.satisfied(buttons, keys):
            self.editor.rotateSelected(screenDelta.x, screenDelta.y)

    def onMousePressed(self, screen: Vector2, buttons: set[int], keys: set[int]):
        """Handle mouse press events in the editor.

        Args:
        ----
            screen: Vector2 - Mouse position on screen
            buttons: set[int] - Pressed mouse buttons
            keys: set[int] - Pressed keyboard keys

        Processing Logic:
        ----------------
            - Check if select button is pressed and set doSelect flag
            - Check if duplicate button is pressed, duplicate selected instance and add/select new instance
            - Check if context menu button is pressed and open context menu at cursor position.
        """
        if self.selectUnderneath.satisfied(buttons, keys):
            self.renderer.doSelect = True

        if self.duplicateSelected.satisfied(buttons, keys) and self.editor.selectedInstances:
            point = self.renderer.scene.cursor.position()
            self.editor.duplicateSelected(Vector3(point.x, point.y, point.z))

        if self.openContextMenu.satisfied(buttons, keys):
            world = Vector3(*self.renderer.scene.cursor.position())
            self.editor.onContextMenu(world, self.renderer.mapToGlobal(QPoint(int(screen.x), int(screen.y))))

    def onMouseReleased(self, screen: Vector2, buttons: set[int], keys: set[int]):
        ...

    def onKeyboardPressed(self, buttons: set[int], keys: set[int]):
        """Handles keyboard input in the editor.

        Args:
        ----
            buttons: set[int]: The pressed buttons
            keys: set[int]: The pressed keys

        Processes keyboard input:
            - Toggles free camera mode
            - Snaps camera to selected instance
            - Moves camera to cursor/entry point
            - Deletes selected instances
            - Rotates camera
            - Pans camera
            - Zooms camera
            - Toggles instance locking.
        """

        if self.moveCameraToSelected.satisfied(buttons, keys):
            for instance in self.editor.selectedInstances:
                self.renderer.snapCameraToPoint(instance.position)
                break
        if self.moveCameraToCursor.satisfied(buttons, keys):
            camera = self.renderer.scene.camera
            camera.x = self.renderer.scene.cursor.position().x
            camera.y = self.renderer.scene.cursor.position().y
            camera.z = self.renderer.scene.cursor.position().z
        if self.moveCameraToEntryPoint.satisfied(buttons, keys):
            self.editor.snapCameraToEntryLocation()

        if self.deleteSelected.satisfied(buttons, keys):
            self.editor.deleteSelected()

        if self.rotateCameraLeft.satisfied(buttons, keys):
            self.renderer.rotateCamera(math.pi / 4, 0)
        if self.rotateCameraRight.satisfied(buttons, keys):
            self.renderer.rotateCamera(-math.pi / 4, 0)
        if self.rotateCameraUp.satisfied(buttons, keys):
            self.renderer.rotateCamera(0, math.pi / 4)
        if self.rotateCameraDown.satisfied(buttons, keys):
            self.renderer.rotateCamera(0, -math.pi / 4)

        if self.zoomCameraIn.satisfied(buttons, keys):
            self.renderer.zoomCamera(1)
        if self.zoomCameraOut.satisfied(buttons, keys):
            self.renderer.zoomCamera(-1)

        if self.toggleInstanceLock.satisfied(buttons, keys):
            self.editor.ui.lockInstancesCheck.setChecked(not self.editor.ui.lockInstancesCheck.isChecked())

    def onKeyboardReleased(self, buttons: set[int], keys: set[int]):
        ...

    def moveHeld(self, elapsed):
        buttons, keys = self.renderer.mouseDown(), self.renderer.keysDown()
        up = int(self.moveCameraUp.satisfied(buttons, keys, exactKeys=False)) - int(self.moveCameraDown.satisfied(buttons, keys, exactKeys=False))
        side = int(self.moveCameraRight.satisfied(buttons, keys, exactKeys=False)) - int(self.moveCameraLeft.satisfied(buttons, keys, exactKeys=False))
        forward = int(self.moveCameraForward.satisfied(buttons, keys, exactKeys=False)) - int(self.moveCameraBackward.satisfied(buttons, keys, exactKeys=False))
        length = math.sqrt(up * up + side * side + forward * forward)
        if length:
            strength = self.settings.moveCameraSensitivity3d / 100 * 30 * elapsed / length
            self.renderer.panCamera(forward * strength, side * strength, up * strength)


class ModuleDesignerControlsFreeCam:
    def __init__(self, editor: ModuleDesigner, renderer: ModuleRenderer):
        """Initializes the free camera controller.

        Args:
        ----
            editor: {ModuleDesigner}: The module designer instance.
            renderer: {ModuleRenderer}: The module renderer instance.

        Initializes control items for camera movement and sets up free camera mode in the renderer:
            - Sets editor and settings references
            - Initializes control items for camera movement bindings
            - Hides cursor and enables free camera mode in renderer
            - Clears any existing key presses and centers cursor in renderer view.
        """
        self.editor: ModuleDesigner = editor
        self.settings: ModuleDesignerSettings = ModuleDesignerSettings()
        self.renderer: ModuleRenderer = renderer

        self.toggleFreeCam: ControlItem = ControlItem(self.settings.toggleFreeCam3dBind)
        self.moveCameraUp: ControlItem = ControlItem(self.settings.moveCameraUpFcBind)
        self.moveCameraDown: ControlItem = ControlItem(self.settings.moveCameraDownFcBind)
        self.moveCameraForward: ControlItem = ControlItem(self.settings.moveCameraForwardFcBind)
        self.moveCameraBackward: ControlItem = ControlItem(self.settings.moveCameraBackwardFcBind)
        self.moveCameraLeft: ControlItem = ControlItem(self.settings.moveCameraLeftFcBind)
        self.moveCameraRight: ControlItem = ControlItem(self.settings.moveCameraRightFcBind)

        self.renderer.scene.show_cursor = False
        self.renderer.freeCam = True
        self.renderer.setCursor(QtCore.Qt.CursorShape.BlankCursor)
        self.renderer.setFocus(QtCore.Qt.OtherFocusReason)

        rendererPos = self.renderer.mapToGlobal(QPoint(0, 0))
        mouseX = rendererPos.x() + self.renderer.width() // 2
        mouseY = rendererPos.y() + self.renderer.height() // 2
        self.renderer.cursor().setPos(mouseX, mouseY)

    def onMouseScrolled(self, delta: Vector2, buttons: set[int], keys: set[int]):
        ...

    def onMouseMoved(self, screen: Vector2, screenDelta: Vector2, world: Vector3, buttons: set[int], keys: set[int]):
        rendererPos = self.renderer.mapToGlobal(QPoint(0, 0))
        mouseX: int = rendererPos.x() + self.renderer.width() // 2
        mouseY: int = rendererPos.y() + self.renderer.height() // 2
        strength: float = self.settings.rotateCameraSensitivityFC / 10000

        self.renderer.rotateCamera(-screenDelta.x * strength, screenDelta.y * strength, snapRotations=False)
        self.renderer.cursor().setPos(mouseX, mouseY)

    def onMousePressed(self, screen: Vector2, buttons: set[int], keys: set[int]):
        ...

    def onMouseReleased(self, screen: Vector2, buttons: set[int], keys: set[int]):
        ...

    def onKeyboardPressed(self, buttons, keys):
        # Discrete commands are handled by the window; held motion runs on its tick.
        pass

    def onKeyboardReleased(self, buttons: set[int], keys: set[int]):
        ...

    def moveHeld(self, elapsed):
        keys, buttons = self.renderer.keysDown(), self.renderer.mouseDown()
        up = int(self.moveCameraUp.satisfied(buttons, keys, exactKeys=False)) - int(self.moveCameraDown.satisfied(buttons, keys, exactKeys=False))
        side = int(self.moveCameraRight.satisfied(buttons, keys, exactKeys=False)) - int(self.moveCameraLeft.satisfied(buttons, keys, exactKeys=False))
        forward = int(self.moveCameraForward.satisfied(buttons, keys, exactKeys=False)) - int(self.moveCameraBackward.satisfied(buttons, keys, exactKeys=False))
        length = math.sqrt(up * up + side * side + forward * forward)
        if length:
            strength = self.settings.flyCameraSpeedFC / 100 * 30 * elapsed / length
            self.renderer.moveCamera(forward * strength, side * strength, up * strength)


class ModuleDesignerControls2d:
    def __init__(self, editor: ModuleDesigner, renderer: WalkmeshRenderer):
        """Initializes the 2D editor controller.

        Args:
        ----
            editor: {ModuleDesigner}: The editor module.
            renderer: {WalkmeshRenderer}: The renderer for the walkmesh.

        Processing Logic:
        ----------------
            - Sets editor and renderer references
            - Initializes control bindings from settings
            - Initializes ControlItem objects for each binding.
        """
        self.editor: ModuleDesigner = editor
        self.renderer: WalkmeshRenderer = renderer
        self.settings: ModuleDesignerSettings = ModuleDesignerSettings()

        self.moveCamera: ControlItem = ControlItem(self.settings.moveCamera2dBind)
        self.rotateCamera: ControlItem = ControlItem(self.settings.rotateCamera2dBind)
        self.zoomCamera: ControlItem = ControlItem(self.settings.zoomCamera2dBind)
        self.rotateSelected: ControlItem = ControlItem(self.settings.rotateObject2dBind)
        self.moveSelected: ControlItem = ControlItem(self.settings.moveObject2dBind)
        self.selectUnderneath: ControlItem = ControlItem(self.settings.selectObject2dBind)
        self.deleteSelected: ControlItem = ControlItem(self.settings.deleteObject2dBind)
        self.duplicateSelected: ControlItem = ControlItem(self.settings.duplicateObject2dBind)
        self.snapCameraToSelected: ControlItem = ControlItem(self.settings.moveCameraToSelected2dBind)
        self.openContextMenu: ControlItem = ControlItem((set(), {QtMouse.RightButton}))
        self.toggleInstanceLock: ControlItem = ControlItem(self.settings.toggleLockInstancesBind)

    def onMouseScrolled(self, delta, buttons, keys):
        if self.zoomCamera.satisfied(buttons, keys):
            strength = self.settings.zoomCameraSensitivity2d / 100
            self.renderer.camera.nudgeZoom(1.1 ** (delta.y / 120 * strength))

    def onMouseMoved(self, screen, screenDelta, world, worldDelta, buttons, keys):
        if self.moveCamera.satisfied(buttons, keys):
            strength = self.settings.moveCameraSensitivity2d / 100
            self.renderer.camera.nudgePosition(-worldDelta.x * strength, -worldDelta.y * strength)
            return
        if self.rotateCamera.satisfied(buttons, keys):
            strength = self.settings.rotateCameraSensitivity2d / 5000
            self.renderer.camera.nudgeRotation(screenDelta.x * strength)
            return
        if self.editor.ui.lockInstancesCheck.isChecked():
            return
        if self.editor._geometryInstance is not None:
            if self.moveSelected.satisfied(buttons, keys):
                points = self.renderer.geometrySelection.all()
                if points:
                    self.editor.history.begin("Move geometry points", [self.editor._geometryInstance])
                    for vertex in points:
                        vertex.point.x += worldDelta.x
                        vertex.point.y += worldDelta.y
            return
        if self.moveSelected.satisfied(buttons, keys):
            self.editor.moveSelected(worldDelta.x, worldDelta.y)
        elif self.rotateSelected.satisfied(buttons, keys) and self.editor.selectedInstances:
            self.editor.history.begin("Rotate instances", self.editor.selectedInstances)
            for instance in self.editor.selectedInstances:
                rotation = -math.atan2(world.x - instance.position.x, world.y - instance.position.y)
                if isinstance(instance, GITCamera):
                    instance.rotate(instance.yaw() - rotation, 0, 0)
                else:
                    instance.rotate(-instance.yaw() + rotation, 0, 0)

    def onMousePressed(self, screen: Vector2, buttons: set[int], keys: set[int]):
        """Handle mouse press events in the editor.

        Args:
        ----
            screen: Vector2 - Mouse position
            buttons: set[int] - Pressed buttons
            keys: set[int] - Pressed keys

        Processing Logic:
        ----------------
            - Check if select button is pressed and select instance under mouse
            - Check if duplicate button is pressed and duplicate selected instance
            - Check if context menu button is pressed and open context menu.
        """
        if self.selectUnderneath.satisfied(buttons, keys):
            if self.renderer.instancesUnderMouse():
                self.editor.setSelection([self.renderer.instancesUnderMouse()[-1]])
            else:
                self.editor.setSelection([])

        if self.duplicateSelected.satisfied(buttons, keys) and self.editor.selectedInstances:
            self._duplicate_instance()
        if self.openContextMenu.satisfied(buttons, keys):
            hovered = self.renderer.instancesUnderMouse()
            self.editor.setSelection([hovered[-1]] if hovered else [])
            world: Vector3 = self.renderer.toWorldCoords(screen.x, screen.y)
            self.editor.onContextMenu(world, self.renderer.mapToGlobal(QPoint(int(screen.x), int(screen.y))))

    # TODO Rename this here and in `onMousePressed`
    def _duplicate_instance(self):
        cursor = self.renderer.mapFromGlobal(self.renderer.cursor().pos())
        self.editor.duplicateSelected(self.renderer.toWorldCoords(cursor.x(), cursor.y()))

    def onMouseReleased(self, screen: Vector2, buttons: set[int], keys: set[int]):
        ...

    def onKeyboardPressed(self, buttons: set[int], keys: set[int]):
        """Handle keyboard input in the editor.

        Args:
        ----
            buttons: {Set of pressed button codes}
            keys: {Set of pressed key codes}.

        Processing Logic:
        ----------------
            - Check if delete selection shortcut satisfied and call editor delete selection
            - Check if snap camera to selection shortcut satisfied and snap camera to first selected instance
            - Check if toggle instance lock shortcut satisfied and toggle lock instances checkbox
        """
        if self.deleteSelected.satisfied(buttons, keys):
            self.editor.deleteSelected()

        if self.snapCameraToSelected.satisfied(buttons, keys):
            for instance in self.editor.selectedInstances:
                self.renderer.snapCameraToPoint(instance.position)
                break

        if self.toggleInstanceLock.satisfied(buttons, keys):
            self.editor.ui.lockInstancesCheck.setChecked(not self.editor.ui.lockInstancesCheck.isChecked())

    def onKeyboardReleased(self, buttons: set[int], keys: set[int]):
        ...
