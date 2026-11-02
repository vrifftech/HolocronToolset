from __future__ import annotations

import math
from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING

from pykotor.common.geometry import SurfaceMaterial, Vector2, Vector3
from pykotor.common.misc import Color
from pykotor.common.module import Module
from pykotor.extract.installation import SearchLocation
from pykotor.resource.formats.bwm import read_bwm
from pykotor.resource.formats.bwm.bwm_data import BWM
from pykotor.resource.formats.lyt import LYT, read_lyt
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
    bytes_git,
    read_git,
)
from pykotor.resource.type import ResourceType
from pykotor.tools.misc import is_bif_file, is_capsule_file
from pykotor.tools.template import extract_name, extract_tag_from_gff
from PyQt5 import QtCore
from PyQt5.QtGui import QColor, QIcon, QKeyEvent, QKeySequence
from PyQt5.QtWidgets import QCheckBox, QDialog, QListWidgetItem, QMenu, QMessageBox, QWidget
from toolset.data.settings import settings_signals
from toolset.data.misc import ControlItem
from toolset.data.instance_history import InstanceEditHistory, clone_instance
from toolset.gui.dialogs.instance.camera import CameraDialog
from toolset.gui.dialogs.instance.creature import CreatureDialog
from toolset.gui.dialogs.instance.door import DoorDialog
from toolset.gui.dialogs.instance.encounter import EncounterDialog
from toolset.gui.dialogs.instance.placeable import PlaceableDialog
from toolset.gui.dialogs.instance.sound import SoundDialog
from toolset.gui.dialogs.instance.store import StoreDialog
from toolset.gui.dialogs.instance.trigger import TriggerDialog
from toolset.gui.dialogs.instance.waypoint import WaypointDialog
from toolset.gui.editor import Editor
from toolset.gui.widgets.renderer.walkmesh import GeomPoint
from toolset.gui.widgets.settings.git import GITSettings
from toolset.gui.widgets.settings.module_designer import ModuleDesignerSettings
from toolset.utils.misc import getResourceFromFile
from toolset.utils.window import openResourceEditor

if TYPE_CHECKING:
    import os

    from pykotor.extract.file import ResourceIdentifier, ResourceResult
    from PyQt5.QtCore import QPoint
    from toolset.data.installation import HTInstallation


def openInstanceDialog(parent: QWidget, instance: GITInstance, installation: HTInstallation):
    dialog = QDialog()

    if isinstance(instance, GITCreature):
        dialog = CreatureDialog(parent, instance)
    elif isinstance(instance, GITDoor):
        dialog = DoorDialog(parent, instance, installation)
    elif isinstance(instance, GITPlaceable):
        dialog = PlaceableDialog(parent, instance)
    elif isinstance(instance, GITTrigger):
        dialog = TriggerDialog(parent, instance, installation)
    elif isinstance(instance, GITCamera):
        dialog = CameraDialog(parent, instance)
    elif isinstance(instance, GITEncounter):
        dialog = EncounterDialog(parent, instance)
    elif isinstance(instance, GITSound):
        dialog = SoundDialog(parent, instance)
    elif isinstance(instance, GITWaypoint):
        dialog = WaypointDialog(parent, instance, installation)
    elif isinstance(instance, GITStore):
        dialog = StoreDialog(parent, instance)

    return dialog.exec_()


class GITEditor(Editor):
    settingsUpdated = QtCore.pyqtSignal(object)

    def __init__(self, parent: QWidget | None, installation: HTInstallation | None = None):
        """Initializes the GIT editor.

        Args:
        ----
            parent: QWidget | None: The parent widget
            installation: HTInstallation | None: The installation

        Initializes the editor UI and connects signals. Loads default settings. Initializes rendering area and mode. Clears any existing geometry.
        """
        supported = [ResourceType.GIT]
        super().__init__(parent, "GIT Editor", "git", supported, supported, installation)

        from toolset.uic.editors.git import Ui_MainWindow

        self.ui = Ui_MainWindow()
        self.ui.setupUi(self)
        self._setupMenus()
        self._setupSignals()
        self._setupHotkeys()
        self.history = InstanceEditHistory(self, self._refreshHistory)
        self.history.add_actions(self.menuBar().addMenu("Edit"))

        self._git: GIT = GIT()
        self._mode: _Mode = _InstanceMode(self, installation, self._git)
        self._controls: GITControlScheme = GITControlScheme(self)

        self._loadSettings()
        settings_signals.changed.connect(self.reloadSettings)

        self.nameBuffer: dict[ResourceIdentifier, str] = {}
        self.tagBuffer: dict[ResourceIdentifier, str] = {}

        self.ui.renderArea.materialColors = self.materialColors
        self.ui.renderArea.hideWalkmeshEdges = True
        self.ui.renderArea.highlightBoundaries = False

        self.new()

    def _setupHotkeys(self):
        self.ui.actionDeleteSelected.setShortcut(QKeySequence("Del"))
        self.ui.actionZoomIn.setShortcut(QKeySequence("+"))
        self.ui.actionZoomOut.setShortcut(QKeySequence("-"))

    def _setupSignals(self):
        """Connect signals to UI elements.

        Args:
        ----
            self: The class instance

        Processing Logic:
        ----------------
            - Connect mouse/key events to handlers
            - Connect checkbox toggles to visibility updater
            - Connect menu options to label settings changes.
        """
        self.childSavedFile.connect(self._onTemplateSaved)
        self.ui.renderArea.mousePressed.connect(self.onMousePressed)
        self.ui.renderArea.mouseMoved.connect(self.onMouseMoved)
        self.ui.renderArea.mouseScrolled.connect(self.onMouseScrolled)
        self.ui.renderArea.mouseReleased.connect(self.onMouseReleased)
        self.ui.renderArea.customContextMenuRequested.connect(self.onContextMenu)
        self.ui.renderArea.keyPressed.connect(self.onKeyPressed)
        self.ui.renderArea.inputReset.connect(self._finishGesture)
        self.ui.renderArea.keyReleased.connect(self._onKeyReleased)

        self.ui.filterEdit.textEdited.connect(self.onFilterEdited)
        self.ui.listWidget.doubleClicked.connect(self.moveCameraToSelection)
        self.ui.listWidget.itemSelectionChanged.connect(self.onItemSelectionChanged)
        self.ui.listWidget.customContextMenuRequested.connect(self.onItemContextMenu)

        self.ui.viewCreatureCheck.toggled.connect(self.updateVisibility)
        self.ui.viewPlaceableCheck.toggled.connect(self.updateVisibility)
        self.ui.viewDoorCheck.toggled.connect(self.updateVisibility)
        self.ui.viewSoundCheck.toggled.connect(self.updateVisibility)
        self.ui.viewTriggerCheck.toggled.connect(self.updateVisibility)
        self.ui.viewEncounterCheck.toggled.connect(self.updateVisibility)
        self.ui.viewWaypointCheck.toggled.connect(self.updateVisibility)
        self.ui.viewCameraCheck.toggled.connect(self.updateVisibility)
        self.ui.viewStoreCheck.toggled.connect(self.updateVisibility)

        self.ui.viewCreatureCheck.mouseDoubleClickEvent = lambda a0: self.onInstanceVisibilityDoubleClick( self.ui.viewCreatureCheck)  # noqa: ARG005
        self.ui.viewPlaceableCheck.mouseDoubleClickEvent = lambda a0: self.onInstanceVisibilityDoubleClick( self.ui.viewPlaceableCheck)  # noqa: ARG005
        self.ui.viewDoorCheck.mouseDoubleClickEvent = lambda a0: self.onInstanceVisibilityDoubleClick(self.ui.viewDoorCheck)  # noqa: ARG005
        self.ui.viewSoundCheck.mouseDoubleClickEvent = lambda a0: self.onInstanceVisibilityDoubleClick(self.ui.viewSoundCheck)  # noqa: ARG005
        self.ui.viewTriggerCheck.mouseDoubleClickEvent = lambda a0: self.onInstanceVisibilityDoubleClick(self.ui.viewTriggerCheck)  # noqa: ARG005
        self.ui.viewEncounterCheck.mouseDoubleClickEvent = lambda a0: self.onInstanceVisibilityDoubleClick( self.ui.viewEncounterCheck)  # noqa: ARG005
        self.ui.viewWaypointCheck.mouseDoubleClickEvent = lambda a0: self.onInstanceVisibilityDoubleClick( self.ui.viewWaypointCheck)  # noqa: ARG005
        self.ui.viewCameraCheck.mouseDoubleClickEvent = lambda a0: self.onInstanceVisibilityDoubleClick(self.ui.viewCameraCheck)  # noqa: ARG005
        self.ui.viewStoreCheck.mouseDoubleClickEvent = lambda a0: self.onInstanceVisibilityDoubleClick(self.ui.viewStoreCheck)  # noqa: ARG005

        # View
        self.ui.actionZoomIn.triggered.connect(lambda: self.ui.renderArea.camera.nudgeZoom(1))
        self.ui.actionZoomOut.triggered.connect(lambda: self.ui.renderArea.camera.nudgeZoom(-1))
        self.ui.actionRecentreCamera.triggered.connect(lambda: self.ui.renderArea.centerCamera())
        # View -> Creature Labels
        self.ui.actionUseCreatureResRef.triggered.connect(lambda: setattr(self.settings, "creatureLabel", "resref"))
        self.ui.actionUseCreatureResRef.triggered.connect(self.updateVisibility)
        self.ui.actionUseCreatureTag.triggered.connect(lambda: setattr(self.settings, "creatureLabel", "tag"))
        self.ui.actionUseCreatureTag.triggered.connect(self.updateVisibility)
        self.ui.actionUseCreatureName.triggered.connect(lambda: setattr(self.settings, "creatureLabel", "name"))
        self.ui.actionUseCreatureName.triggered.connect(self.updateVisibility)
        # View -> Door Labels
        self.ui.actionUseDoorResRef.triggered.connect(lambda: setattr(self.settings, "doorLabel", "resref"))
        self.ui.actionUseDoorResRef.triggered.connect(self.updateVisibility)
        self.ui.actionUseDoorTag.triggered.connect(lambda: setattr(self.settings, "doorLabel", "tag"))
        self.ui.actionUseDoorTag.triggered.connect(self.updateVisibility)
        self.ui.actionUseDoorName.triggered.connect(lambda: setattr(self.settings, "doorLabel", "name"))
        self.ui.actionUseDoorName.triggered.connect(self.updateVisibility)
        # View -> Placeable Labels
        self.ui.actionUsePlaceableResRef.triggered.connect(lambda: setattr(self.settings, "placeableLabel", "resref"))
        self.ui.actionUsePlaceableResRef.triggered.connect(self.updateVisibility)
        self.ui.actionUsePlaceableName.triggered.connect(lambda: setattr(self.settings, "placeableLabel", "name"))
        self.ui.actionUsePlaceableName.triggered.connect(self.updateVisibility)
        self.ui.actionUsePlaceableTag.triggered.connect(lambda: setattr(self.settings, "placeableLabel", "tag"))
        self.ui.actionUsePlaceableTag.triggered.connect(self.updateVisibility)
        # View -> Merchant Labels
        self.ui.actionUseMerchantResRef.triggered.connect(lambda: setattr(self.settings, "storeLabel", "resref"))
        self.ui.actionUseMerchantResRef.triggered.connect(self.updateVisibility)
        self.ui.actionUseMerchantName.triggered.connect(lambda: setattr(self.settings, "storeLabel", "name"))
        self.ui.actionUseMerchantName.triggered.connect(self.updateVisibility)
        self.ui.actionUseMerchantTag.triggered.connect(lambda: setattr(self.settings, "storeLabel", "tag"))
        self.ui.actionUseMerchantTag.triggered.connect(self.updateVisibility)
        # View -> Sound Labels
        self.ui.actionUseSoundResRef.triggered.connect(lambda: setattr(self.settings, "soundLabel", "resref"))
        self.ui.actionUseSoundResRef.triggered.connect(self.updateVisibility)
        self.ui.actionUseSoundName.triggered.connect(lambda: setattr(self.settings, "soundLabel", "name"))
        self.ui.actionUseSoundName.triggered.connect(self.updateVisibility)
        self.ui.actionUseSoundTag.triggered.connect(lambda: setattr(self.settings, "soundLabel", "tag"))
        self.ui.actionUseSoundTag.triggered.connect(self.updateVisibility)
        # View -> Waypoint Labels
        self.ui.actionUseWaypointResRef.triggered.connect(lambda: setattr(self.settings, "waypointLabel", "resref"))
        self.ui.actionUseWaypointResRef.triggered.connect(self.updateVisibility)
        self.ui.actionUseWaypointName.triggered.connect(lambda: setattr(self.settings, "waypointLabel", "name"))
        self.ui.actionUseWaypointName.triggered.connect(self.updateVisibility)
        self.ui.actionUseWaypointTag.triggered.connect(lambda: setattr(self.settings, "waypointLabel", "tag"))
        self.ui.actionUseWaypointTag.triggered.connect(self.updateVisibility)
        # View -> Encounter Labels
        self.ui.actionUseEncounterResRef.triggered.connect(lambda: setattr(self.settings, "encounterLabel", "resref"))
        self.ui.actionUseEncounterResRef.triggered.connect(self.updateVisibility)
        self.ui.actionUseEncounterName.triggered.connect(lambda: setattr(self.settings, "encounterLabel", "name"))
        self.ui.actionUseEncounterName.triggered.connect(self.updateVisibility)
        self.ui.actionUseEncounterTag.triggered.connect(lambda: setattr(self.settings, "encounterLabel", "tag"))
        self.ui.actionUseEncounterTag.triggered.connect(self.updateVisibility)
        # View -> Trigger Labels
        self.ui.actionUseTriggerResRef.triggered.connect(lambda: setattr(self.settings, "triggerLabel", "resref"))
        self.ui.actionUseTriggerResRef.triggered.connect(self.updateVisibility)
        self.ui.actionUseTriggerTag.triggered.connect(lambda: setattr(self.settings, "triggerLabel", "tag"))
        self.ui.actionUseTriggerTag.triggered.connect(self.updateVisibility)
        self.ui.actionUseTriggerName.triggered.connect(lambda: setattr(self.settings, "triggerLabel", "name"))
        self.ui.actionUseTriggerName.triggered.connect(self.updateVisibility)

    def load(self, filepath, resref, restype, data):
        git = read_git(data)
        super().load(filepath, resref, restype, data)
        self.ui.renderArea.setWalkmeshes([])
        self._loadGIT(git)
        if self._installation is None:
            QMessageBox.warning(self, "Layout unavailable", "The GIT document is open, but an installation is required to display its walkmeshes.")
            return
        try:
            order = [SearchLocation.OVERRIDE, SearchLocation.CHITIN, SearchLocation.MODULES]
            result = self._installation.resource(resref, ResourceType.LYT, order)
            if result is None:
                raise ValueError(f"No layout was found for '{resref}'.")
            self.loadLayout(read_lyt(result.data))
        except Exception as error:
            QMessageBox.warning(self, "Layout unavailable", str(error))

    def _loadGIT(self, git: GIT):
        """Load a GIT instance.

        Args:
        ----
            git: The GIT instance to load

        Processing Logic:
        ----------------
            - Load the provided GIT instance into the application
            - Set the GIT instance on the render area
            - Center the camera on the render area
            - Create an InstanceMode for interaction based on the loaded GIT and installation
            - Update the visibility of UI elements.
        """
        self.nameBuffer.clear()
        self.tagBuffer.clear()
        self.history.reset(git)
        self._git = git
        self.ui.renderArea.instanceSelection.clear()
        self.ui.renderArea.geometrySelection.clear()
        self.ui.renderArea.setGit(self._git)
        self.ui.renderArea.centerCamera()
        self._mode = _InstanceMode(self, self._installation, self._git)
        self.updateVisibility()

    def build(self) -> tuple[bytes, bytes]:
        self.history.finish()
        return bytes_git(self._git), b""

    def new(self):
        super().new()
        self._loadGIT(GIT())

    def loadLayout(self, layout: LYT):
        """Load layout walkmeshes into the UI renderer.

        Args:
        ----
            layout (LYT): Layout to load walkmeshes from

        Processing Logic:
        ----------------
            - Iterate through each room in the layout
            - Get the highest priority walkmesh asset for the room from the installation
            - If a walkmesh asset is found, read it and add it to a list
            - Set the list of walkmeshes on the UI renderer.
        """
        walkmeshes: list[BWM] = []
        for room in layout.rooms:
            order: list[SearchLocation] = [SearchLocation.OVERRIDE, SearchLocation.CHITIN, SearchLocation.MODULES]
            findBWM: ResourceResult | None = self._installation.resource(room.model, ResourceType.WOK, order)
            if findBWM is not None:
                walkmeshes.append(read_bwm(findBWM.data))

        self.ui.renderArea.setWalkmeshes(walkmeshes)

    def git(self) -> GIT:
        return self._git

    def onInstanceVisibilityDoubleClick(self, checkbox: QCheckBox):
        """Toggles visibility of the relevant UI data on double click.

        Args:
        ----
            checkbox (QCheckBox): Checkbox for instance type visibility

        Processing Logic:
        ----------------
            - Uncheck all other instance type checkboxes
            - Check the checkbox that was double clicked
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

    def getInstanceExternalName(self, instance: GITInstance) -> str | None:
        """Get external name of a GIT instance.

        Args:
        ----
            instance: The GIT instance object

        Returns:
        -------
            name: The external name of the instance or None

        Processing Logic:
        ----------------
            - Extract identifier from instance
            - Check if identifier is present in name buffer
            - If not present, get resource from installation using identifier
            - Extract name from resource data
            - Save name in buffer
            - Return name from buffer.
        """
        resid: ResourceIdentifier | None = instance.identifier()
        if resid is None or self._installation is None:
            return None
        if resid not in self.nameBuffer:
            res: ResourceResult | None = self._installation.resource(resid.resname, resid.restype)
            self.nameBuffer[resid] = None if res is None else self._installation.string(extract_name(res.data))
        return self.nameBuffer[resid]

    def getInstanceExternalTag(self, instance: GITInstance) -> str | None:
        """Gets external tag for the given instance.

        Args:
        ----
            instance: The instance to get tag for

        Returns:
        -------
            tag: The external tag associated with the instance or None

        Processing Logic:
        ----------------
            - Get resource identifier from instance
            - Check if tag is already cached for this identifier
            - If not cached, call installation to get resource and extract tag from resource data
            - Cache tag in buffer and return cached tag.
        """
        resid: ResourceIdentifier | None = instance.identifier()
        if resid is None or self._installation is None:
            return None
        if resid not in self.tagBuffer:
            res: ResourceResult | None = self._installation.resource(resid.resname, resid.restype)
            self.tagBuffer[resid] = None if res is None else extract_tag_from_gff(res.data)
        return self.tagBuffer[resid]

    def _onTemplateSaved(self, filepath, resname, restype, data):
        path = Path(filepath)
        if path.is_relative_to(self._installation.override_path()):
            self._installation.reload_override_file(path)
        self.nameBuffer.clear()
        self.tagBuffer.clear()
        # Use the saved bytes even if an installation index still has the old file size.
        for instance in self._git.instances():
            identifier = instance.identifier()
            if identifier is not None and identifier.resname.casefold() == resname.casefold() and identifier.restype == restype:
                self.nameBuffer[identifier] = self._installation.string(extract_name(data))
                self.tagBuffer[identifier] = extract_tag_from_gff(data)
                break
        if isinstance(self._mode, _InstanceMode):
            for row in range(self.ui.listWidget.count()):
                item = self.ui.listWidget.item(row)
                self._mode.setListItemLabel(item, item.data(QtCore.Qt.UserRole))

    def enterInstanceMode(self):
        self.history.finish()
        self._mode = _InstanceMode(self, self._installation, self._git)

    def enterGeometryMode(self):
        self.history.finish()
        self._mode = _GeometryMode(self, self._installation, self._git)


    def moveCameraToSelection(self):
        instance = self.ui.renderArea.instanceSelection.last()
        if instance:
            self.ui.renderArea.camera.setPosition(instance.position.x, instance.position.y)

    # region Mode Calls
    def openListContextMenu(self, item: QListWidgetItem, point: QPoint):
        ...

    def updateVisibility(self):
        self._mode.updateVisibility()

    def selectUnderneath(self):
        self._mode.selectUnderneath()

    def deleteSelected(self):
        self._mode.deleteSelected()

    def duplicateSelected(self, position: Vector3):
        self._mode.duplicateSelected(position)

    def moveSelected(self, x: float, y: float):
        if self.ui.lockInstancesCheck.isChecked():
            return
        if isinstance(self._mode, _GeometryMode):
            instances = list(dict.fromkeys(vertex.instance for vertex in self.ui.renderArea.geometrySelection.all()))
        else:
            instances = self.ui.renderArea.instanceSelection.all()
        if not instances:
            return
        self.history.begin("Move selection", instances)
        self._mode.moveSelected(x, y)

    def rotateSelected(self, angle: float):
        if self.ui.lockInstancesCheck.isChecked():
            return
        if isinstance(self._mode, _GeometryMode):
            instances = list(dict.fromkeys(vertex.instance for vertex in self.ui.renderArea.geometrySelection.all()))
        else:
            instances = self.ui.renderArea.instanceSelection.all()
        if not instances:
            return
        self.history.begin("Rotate selection", instances)
        self._mode.rotateSelected(angle)

    def rotateSelectedToPoint(self, x: float, y: float):
        if self.ui.lockInstancesCheck.isChecked():
            return
        if isinstance(self._mode, _GeometryMode):
            instances = list(dict.fromkeys(vertex.instance for vertex in self.ui.renderArea.geometrySelection.all()))
        else:
            instances = self.ui.renderArea.instanceSelection.all()
        if not instances:
            return
        self.history.begin("Rotate selection", instances)
        self._mode.rotateSelectedToPoint(x, y)

    def moveCamera(self, x: float, y: float):
        self._mode.moveCamera(x, y)

    def zoomCamera(self, amount: float):
        self._mode.zoomCamera(amount)

    def rotateCamera(self, angle: float):
        self._mode.rotateCamera(angle)

    # endregion

    # region Signal Callbacks
    def onContextMenu(self, point: QPoint):
        """Opens context menu on right click in render area.

        Args:
        ----
            point: Point of right click in local coordinates
        Returns:
            None
        Processes right click context menu:
            - Maps point from local to global coordinates
            - Converts point from local to world coordinates
        - Passes world point and global point to mode for context menu handling
        """
        globalPoint: QPoint = self.ui.renderArea.mapToGlobal(point)
        world: Vector3 = self.ui.renderArea.toWorldCoords(point.x(), point.y())
        self._mode.onRenderContextMenu(Vector2.from_vector3(world), globalPoint)

    def onFilterEdited(self):
        self._mode.onFilterEdited(self.ui.filterEdit.text())

    def onItemSelectionChanged(self):
        self._mode.onItemSelectionChanged(self.ui.listWidget.currentItem())

    def onItemContextMenu(self, point):
        item = self.ui.listWidget.itemAt(point)
        if item is None:
            return
        self.ui.listWidget.setCurrentItem(item)
        self._mode.openListContextMenu(item, self.ui.listWidget.mapToGlobal(point))

    def onMouseMoved(self, screen: Vector2, delta: Vector2, buttons: set[int], keys: set[int]):
        """Handle mouse movement event.

        Args:
        ----
            screen: Vector2: Mouse position on screen
            delta: Vector2: Mouse movement since last event
            buttons: set[int]: Currently pressed mouse buttons
            keys: set[int]: Currently pressed keyboard keys

        Processing Logic:
        ----------------
            - Convert mouse position and movement to world coordinates
            - Pass mouse event to controls handler
            - Update status bar with world mouse position.
        """
        worldDelta: Vector2 = self.ui.renderArea.toWorldDelta(delta.x, delta.y)
        world: Vector3 = self.ui.renderArea.toWorldCoords(screen.x, screen.y)
        self._controls.onMouseMoved(screen, delta, Vector2.from_vector3(world), worldDelta, buttons, keys)
        mode: _InstanceMode = self._mode
        mode.updateStatusBar(Vector2.from_vector3(world))

    def onMouseScrolled(self, delta: Vector2, buttons: set[int], keys: set[int]):
        self._controls.onMouseScrolled(delta, buttons, keys)

    def onMousePressed(self, screen: Vector2, buttons: set[int], keys: set[int]):
        self._controls.onMousePressed(screen, buttons, keys)

    def onMouseReleased(self, screen, buttons, keys):
        self._controls.onMouseReleased(screen, buttons, keys)
        if not buttons:
            self.history.finish()

    def onKeyPressed(self, buttons: set[int], keys: set[int]):
        self._controls.onKeyboardPressed(buttons, keys)

    def keyPressEvent(self, e):
        if self.ui.renderArea.hasFocus():
            self.ui.renderArea.keyPressEvent(e)
        else:
            super().keyPressEvent(e)

    def keyReleaseEvent(self, e):
        if self.ui.renderArea.hasFocus():
            self.ui.renderArea.keyReleaseEvent(e)
        else:
            super().keyReleaseEvent(e)

    def _finishGesture(self):
        self.history.finish()

    def _onKeyReleased(self, buttons, keys):
        if not buttons:
            self.history.finish()

    def _refreshHistory(self):
        alive = self._git.instances()
        selected = [item for item in self.ui.renderArea.instanceSelection.all() if item in alive]
        self.ui.renderArea.instanceSelection.select(selected)
        self.ui.renderArea.geometrySelection.clear()
        if isinstance(self._mode, _GeometryMode) and not selected:
            self.enterInstanceMode()
        if isinstance(self._mode, _InstanceMode):
            self.ui.listWidget.blockSignals(True)
            try:
                self._mode.buildList()
            finally:
                self.ui.listWidget.blockSignals(False)
            self._mode.setSelection(selected)
        self.ui.renderArea.update()

    def closeEvent(self, event):
        self.history.finish()
        super().closeEvent(event)

    def _loadSettings(self):
        self.settings = GITSettings()

        def intColorToQColor(intvalue):
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
        self._loadSettings()
        self.history.finish()
        self._controls = GITControlScheme(self)
        self.ui.renderArea.resetInput()
        self.ui.renderArea.materialColors = self.materialColors
        self.updateVisibility()
        self.ui.renderArea.update()

    # endregion


class _Mode(ABC):
    def __init__(self, editor: GITEditor, installation: HTInstallation, git: GIT):
        self._editor: GITEditor = editor
        self._installation: HTInstallation = installation
        self._git: GIT = git

        self._ui = editor.ui

    @abstractmethod
    def onItemSelectionChanged(self, item: QListWidgetItem):
        ...

    @abstractmethod
    def onFilterEdited(self, text: str):
        ...

    @abstractmethod
    def onRenderContextMenu(self, world: Vector2, screen: QPoint):
        ...

    @abstractmethod
    def openListContextMenu(self, item: QListWidgetItem, screen: QPoint):
        ...

    @abstractmethod
    def updateVisibility(self):
        ...

    @abstractmethod
    def selectUnderneath(self):
        ...

    @abstractmethod
    def deleteSelected(self):
        ...

    @abstractmethod
    def duplicateSelected(self, position: Vector3):
        ...

    @abstractmethod
    def moveSelected(self, x: float, y: float):
        ...

    @abstractmethod
    def rotateSelected(self, angle: float):
        ...

    @abstractmethod
    def rotateSelectedToPoint(self, x: float, y: float):
        ...

    @abstractmethod
    def moveCamera(self, x: float, y: float):
        ...

    @abstractmethod
    def zoomCamera(self, amount: float):
        ...

    @abstractmethod
    def rotateCamera(self, angle: float):
        ...

    # endregion


class _InstanceMode(_Mode):
    def __init__(self, editor: GITEditor, installation: HTInstallation, git: GIT):
        super().__init__(editor, installation, git)
        self._ui.renderArea.hideGeomPoints = True
        self._ui.renderArea.geometrySelection.clear()
        self.updateVisibility()

    def setSelection(self, instances: list[GITInstance]):
        # set the renderer widget selection
        """Sets the selection of instances in the renderer and list widgets.

        Args:
        ----
            instances: list[GITInstance]: List of instances to select

        Processing Logic:
        ----------------
            - Select instances in the renderer widget
            - Block list widget signals to prevent selection changed signal
            - Loop through list widget items and select matching instances
            - Unblock list widget signals.
        """
        self._editor.history.finish()
        self._ui.renderArea.instanceSelection.select(instances)

        # set the list widget selection
        self._ui.listWidget.blockSignals(True)
        for i in range(self._ui.listWidget.count()):
            item = self._ui.listWidget.item(i)
            instance = item.data(QtCore.Qt.UserRole)
            if instance in instances:
                self._ui.listWidget.setCurrentItem(item)
        self._ui.listWidget.blockSignals(False)

    def editSelectedInstance(self):
        selection = self._ui.renderArea.instanceSelection.all()
        if selection:
            instance = selection[-1]
            self._editor.history.finish()
            self._editor.history.begin("Edit instance", [instance])
            try:
                accepted = openInstanceDialog(self._editor, instance, self._installation)
            except BaseException:
                self._editor.history.cancel()
                raise
            if accepted:
                self._editor.history.finish()
            else:
                self._editor.history.cancel()

    def editSelectedInstanceResource(self):
        """Open the template from Override, the current module, or the core BIF."""
        selection = self._ui.renderArea.instanceSelection.all()
        if not selection or self._installation is None:
            return
        identifier = selection[-1].identifier()
        if identifier is None:
            return
        resname, restype = identifier
        current = self._editor.filepath()
        module_root = None if current is None else Module.find_root(current).casefold()
        locations = self._installation.location(resname, restype,
                                                [SearchLocation.OVERRIDE, SearchLocation.MODULES, SearchLocation.CHITIN])
        candidates = []
        for result in locations:
            path = result.filepath
            if path.is_relative_to(self._installation.override_path()):
                priority = 0
            elif module_root is not None and is_capsule_file(path) and Module.find_root(path).casefold() == module_root:
                priority = 1 if path.suffix.lower() == ".mod" else 2
            elif is_bif_file(path):
                priority = 3
            else:
                continue
            candidates.append((priority, path))
        if not candidates:
            QMessageBox.warning(self._editor, "Template unavailable", f"Cannot locate {resname}.{restype.extension} in Override, this module, or the core game files.")
            return
        filepath = min(candidates, key=lambda candidate: candidate[0])[1]
        if is_bif_file(filepath):
            resource = self._installation.resource(resname, restype, [SearchLocation.CHITIN])
            if resource is None:
                QMessageBox.warning(self._editor, "Template unavailable", f"Cannot read {resname}.{restype.extension} from the core game files.")
                return
            filepath, data = resource.filepath, resource.data
        else:
            data = getResourceFromFile(filepath, resname, restype)
        _path, window = openResourceEditor(filepath, resname, restype, data, self._installation, self._editor)
        if isinstance(window, Editor):
            window.savedFile.connect(self._editor.resourceSaved)
            window.childSavedFile.connect(self._editor.resourceSaved)

    def editSelectedInstanceGeometry(self):
        if self._ui.renderArea.instanceSelection.last():
            self._ui.renderArea.instanceSelection.last()
            self._editor.enterGeometryMode()


    def addInstance(self, instance):
        if isinstance(instance, GITCamera):
            instance.camera_id = max((camera.camera_id for camera in self._git.cameras), default=-1) + 1
        if openInstanceDialog(self._editor, instance, self._installation):
            with self._editor.history.edit("Insert instance", membership=True):
                self._git.add(instance)
            self.setSelection([instance])

    def addInstanceActionsToMenu(self, instance: GITInstance, menu: QMenu):
        """Adds instance actions to a context menu.

        Args:
        ----
            instance: {The selected GIT instance object}
            menu: {The QMenu to add actions to}.

        Returns:
        -------
            None: {Does not return anything, just adds actions to the provided menu}

        Processing Logic:
        ----------------
            - Adds basic "Remove" and "Edit Instance" actions
            - Conditionally adds "Edit Resource" action and disables for cameras
            - Adds additional geometry and spawn point editing for encounters and triggers
            - Connects each action to a method on the class to handle the trigger
        """
        menu.addAction("Remove").triggered.connect(self.deleteSelected)
        menu.addAction("Edit Instance").triggered.connect(self.editSelectedInstance)

        actionEditResource = menu.addAction("Edit Resource")
        actionEditResource.triggered.connect(self.editSelectedInstanceResource)
        actionEditResource.setEnabled(not isinstance(instance, GITCamera))
        menu.addAction(actionEditResource)

        if isinstance(instance, (GITEncounter, GITTrigger)):
            menu.addAction("Edit Geometry").triggered.connect(self.editSelectedInstanceGeometry)

    def setListItemLabel(self, item: QListWidgetItem, instance: GITInstance):
        """Sets the label text of a QListWidget item for a game instance.

        Args:
        ----
            item (QListWidgetItem): The list widget item
            instance (GITInstance): The game instance

        Sets the item data and tooltip, determines the label text based on instance type and editor settings, sets the item text and font if label not found.
        """
        item.setData(QtCore.Qt.UserRole, instance)
        item.setToolTip(self.getInstanceTooltip(instance))

        name: str | None = None

        if isinstance(instance, GITCamera):
            item.setText(str(instance.camera_id))
            return
        if isinstance(instance, GITCreature):
            if self._editor.settings.creatureLabel == "tag":
                name = self._editor.getInstanceExternalTag(instance)
            elif self._editor.settings.creatureLabel == "name":
                name = self._editor.getInstanceExternalName(instance)
            else:
                name = instance.identifier().resname
        elif isinstance(instance, GITPlaceable):
            if self._editor.settings.placeableLabel == "tag":
                name = self._editor.getInstanceExternalTag(instance)
            elif self._editor.settings.placeableLabel == "name":
                name = self._editor.getInstanceExternalName(instance)
            else:
                name = instance.identifier().resname
        elif isinstance(instance, GITDoor):
            if self._editor.settings.doorLabel == "tag":
                name = instance.tag
            elif self._editor.settings.doorLabel == "name":
                name = self._editor.getInstanceExternalName(instance)
            else:
                name = instance.identifier().resname
        elif isinstance(instance, GITStore):
            if self._editor.settings.storeLabel == "tag":
                name = self._editor.getInstanceExternalTag(instance)
            elif self._editor.settings.storeLabel == "name":
                name = self._editor.getInstanceExternalName(instance)
            else:
                name = instance.identifier().resname
        elif isinstance(instance, GITSound):
            if self._editor.settings.soundLabel == "tag":
                name = self._editor.getInstanceExternalTag(instance)
            elif self._editor.settings.soundLabel == "name":
                name = self._editor.getInstanceExternalName(instance)
            else:
                name = instance.identifier().resname
        elif isinstance(instance, GITWaypoint):
            if self._editor.settings.waypointLabel == "tag":
                name = instance.tag
            elif self._editor.settings.waypointLabel == "name":
                name = self._installation.string(instance.name, None)
            else:
                name = instance.identifier().resname
        elif isinstance(instance, GITEncounter):
            if self._editor.settings.encounterLabel == "tag":
                name = self._editor.getInstanceExternalTag(instance)
            elif self._editor.settings.encounterLabel == "name":
                name = self._editor.getInstanceExternalName(instance)
            else:
                name = instance.identifier().resname
        elif isinstance(instance, GITTrigger):
            if self._editor.settings.triggerLabel == "tag":
                name = instance.tag
            elif self._editor.settings.triggerLabel == "name":
                name = self._editor.getInstanceExternalName(instance)
            else:
                name = instance.identifier().resname

        text: str = instance.identifier().resname if name is None else name
        font = item.font()
        font.setItalic(name is None)
        item.setFont(font)

        item.setText(text)

    def getInstanceTooltip(self, instance: GITInstance) -> str:
        if isinstance(instance, GITCamera):
            return f"Struct Index: {self._git.index(instance)}\nCamera ID: {instance.camera_id}"
        return f"Struct Index: {self._git.index(instance)}\nResRef: {instance.identifier().resname}"

    # region Interface Methods
    def onFilterEdited(self, text: str):
        self._ui.renderArea.instanceFilter = text
        self.buildList()

    def onItemSelectionChanged(self, item: QListWidgetItem):
        if item is None:
            self.setSelection([])
        else:
            self.setSelection([item.data(QtCore.Qt.UserRole)])

    def updateStatusBar(self, world: Vector2):
        if self._ui.renderArea.instancesUnderMouse() and self._ui.renderArea.instancesUnderMouse()[-1] is not None:
            instance = self._ui.renderArea.instancesUnderMouse()[-1]
            resname = "" if isinstance(instance, GITCamera) else instance.identifier().resname
            self._editor.statusBar().showMessage(f"({world.x:.1f}, {world.y:.1f}) {resname}")
        else:
            self._editor.statusBar().showMessage(f"({world.x:.1f}, {world.y:.1f})")

    def openListContextMenu(self, item: QListWidgetItem, point: QPoint):
        if item is None:
            return

        instance = item.data(QtCore.Qt.UserRole)
        self.setSelection([instance])
        menu = QMenu(self._ui.listWidget)

        self.addInstanceActionsToMenu(instance, menu)

        menu.aboutToHide.connect(self._ui.renderArea.resetInput)
        menu.popup(point)

    def onRenderContextMenu(self, world: Vector2, point: QPoint):
        """Renders context menu on right click.

        Args:
        ----
            self: {The class instance}
            world: {The world coordinates clicked}
            point: {The screen coordinates clicked}.

        Renders context menu:
            - Adds instance creation actions if no selection
            - Adds instance actions to selected instance if single selection
            - Adds deselect action for instances under mouse
        """
        underMouse: list[GITInstance] = self._ui.renderArea.instancesUnderMouse()
        self.setSelection([underMouse[-1]] if underMouse else [])

        menu = QMenu(self._ui.listWidget)

        if not self._ui.renderArea.instanceSelection.isEmpty():
            self.addInstanceActionsToMenu(self._ui.renderArea.instanceSelection.last(), menu)
        else:
            self._add_submenu(menu, world)
        if underMouse:
            menu.addSeparator()
            for instance in underMouse:
                icon = QIcon(self._ui.renderArea.instancePixmap(instance))
                reference = "" if instance.identifier() is None else instance.identifier().resname
                index = self._editor.git().index(instance)

                instanceAction = menu.addAction(icon, f"[{index}] {reference}")
                instanceAction.triggered.connect(lambda _, inst=instance: self.setSelection([inst]))
                instanceAction.setEnabled(instance not in self._ui.renderArea.instanceSelection.all())
                menu.addAction(instanceAction)

        menu.aboutToHide.connect(self._ui.renderArea.resetInput)
        menu.popup(point)

    def _add_submenu(self, menu: QMenu, world: Vector2):
        menu.addAction("Insert Creature").triggered.connect(lambda: self.addInstance(GITCreature(world.x, world.y)))
        menu.addAction("Insert Door").triggered.connect(lambda: self.addInstance(GITDoor(world.x, world.y)))
        menu.addAction("Insert Placeable").triggered.connect(lambda: self.addInstance(GITPlaceable(world.x, world.y)))
        menu.addAction("Insert Store").triggered.connect(lambda: self.addInstance(GITStore(world.x, world.y)))
        menu.addAction("Insert Sound").triggered.connect(lambda: self.addInstance(GITSound(world.x, world.y)))
        menu.addAction("Insert Waypoint").triggered.connect(lambda: self.addInstance(GITWaypoint(world.x, world.y)))
        menu.addAction("Insert Camera").triggered.connect(lambda: self.addInstance(GITCamera(world.x, world.y)))
        menu.addAction("Insert Encounter").triggered.connect(lambda: self.addInstance(GITEncounter(world.x, world.y)))

        simpleTrigger = GITTrigger(world.x, world.y)
        simpleTrigger.geometry.extend(
            [Vector3(0.0, 0.0, 0.0), Vector3(3.0, 0.0, 0.0), Vector3(3.0, 3.0, 0.0), Vector3(0.0, 3.0, 0.0)],
        )
        menu.addAction("Insert Trigger").triggered.connect(lambda: self.addInstance(simpleTrigger))

    def buildList(self):
        self._ui.listWidget.clear()

        def instanceSort(inst: GITInstance):
            textToSort: str = str(inst.camera_id) if isinstance(inst, GITCamera) else inst.identifier().resname
            return textToSort.rjust(9, "0") if isinstance(inst, GITCamera) else inst.identifier().restype.extension + textToSort

        instances: list[GITInstance] = sorted(self._git.instances(), key=instanceSort)
        for instance in instances:
            filterSource: str = str(instance.camera_id) if isinstance(instance, GITCamera) else instance.identifier().resname
            isVisible: bool | None = self._ui.renderArea.isInstanceVisible(instance)
            isFiltered: bool = self._ui.filterEdit.text() in filterSource

            if isVisible and isFiltered:
                icon = QIcon(self._ui.renderArea.instancePixmap(instance))
                item = QListWidgetItem(icon, "")
                self.setListItemLabel(item, instance)
                self._ui.listWidget.addItem(item)

    def updateVisibility(self):
        self._ui.renderArea.hideCreatures = not self._ui.viewCreatureCheck.isChecked()
        self._ui.renderArea.hidePlaceables = not self._ui.viewPlaceableCheck.isChecked()
        self._ui.renderArea.hideDoors = not self._ui.viewDoorCheck.isChecked()
        self._ui.renderArea.hideTriggers = not self._ui.viewTriggerCheck.isChecked()
        self._ui.renderArea.hideEncounters = not self._ui.viewEncounterCheck.isChecked()
        self._ui.renderArea.hideWaypoints = not self._ui.viewWaypointCheck.isChecked()
        self._ui.renderArea.hideSounds = not self._ui.viewSoundCheck.isChecked()
        self._ui.renderArea.hideStores = not self._ui.viewStoreCheck.isChecked()
        self._ui.renderArea.hideCameras = not self._ui.viewCameraCheck.isChecked()
        self.buildList()

    def selectUnderneath(self):
        underMouse: list[GITInstance] = self._ui.renderArea.instancesUnderMouse()
        selection: list[GITInstance] = self._ui.renderArea.instanceSelection.all()

        # Do not change the selection if the selected instance if its still underneath the mouse
        if selection and selection[0] in underMouse:
            return

        if underMouse:
            self.setSelection([underMouse[-1]])
        else:
            self.setSelection([])

    def deleteSelected(self):
        selection = self._ui.renderArea.instanceSelection.all()
        if not selection or self._ui.lockInstancesCheck.isChecked():
            return
        with self._editor.history.edit("Delete instances", selection, membership=True):
            for instance in selection:
                self._git.remove(instance)

    def duplicateSelected(self, position):
        selected = self._ui.renderArea.instanceSelection.all()
        if not selected or self._ui.lockInstancesCheck.isChecked():
            return
        offset = position - selected[-1].position
        created = []
        with self._editor.history.edit("Duplicate instances", membership=True):
            for original in selected:
                instance = clone_instance(original)
                instance.position = instance.position + offset
                if isinstance(instance, GITCamera):
                    instance.camera_id = max((camera.camera_id for camera in self._git.cameras), default=-1) + 1
                self._git.add(instance)
                created.append(instance)
        self.setSelection(created)

    def moveSelected(self, x: float, y: float):
        if self._ui.lockInstancesCheck.isChecked():
            return

        for instance in self._ui.renderArea.instanceSelection.all():
            instance.move(x, y, 0)

    def rotateSelected(self, angle: float):
        for instance in self._ui.renderArea.instanceSelection.all():
            instance.rotate(angle, 0.0, 0.0)

    def rotateSelectedToPoint(self, x: float, y: float):
        for instance in self._ui.renderArea.instanceSelection.all():
            rotation: float = -math.atan2(x - instance.position.x, y - instance.position.y)
            if isinstance(instance, GITCamera):
                instance.rotate(instance.yaw() - rotation, 0, 0)
            else:
                instance.rotate(-instance.yaw() + rotation, 0, 0)

    def moveCamera(self, x: float, y: float):
        self._ui.renderArea.camera.nudgePosition(x, y)

    def zoomCamera(self, amount: float):
        self._ui.renderArea.camera.nudgeZoom(amount)

    def rotateCamera(self, angle: float):
        self._ui.renderArea.camera.nudgeRotation(angle)

    # endregion


class _GeometryMode(_Mode):
    def __init__(self, editor: GITEditor, installation: HTInstallation, git: GIT):
        super().__init__(editor, installation, git)

        self._ui.renderArea.hideCreatures = True
        self._ui.renderArea.hideDoors = True
        self._ui.renderArea.hidePlaceables = True
        self._ui.renderArea.hideSounds = True
        self._ui.renderArea.hideStores = True
        self._ui.renderArea.hideCameras = True
        self._ui.renderArea.hideTriggers = True
        self._ui.renderArea.hideEncounters = True
        self._ui.renderArea.hideWaypoints = True
        self._ui.renderArea.hideGeomPoints = False

    def insertPointAtMouse(self):
        instance = self._ui.renderArea.instanceSelection.last()
        if not isinstance(instance, (GITTrigger, GITEncounter)) or self._ui.lockInstancesCheck.isChecked():
            return
        screen = self._ui.renderArea.mapFromGlobal(self._editor.cursor().pos())
        world = self._ui.renderArea.toWorldCoords(screen.x(), screen.y())
        with self._editor.history.edit("Insert geometry point", [instance]):
            instance.geometry.append(world - instance.position)

    # region Interface Methods
    def onItemSelectionChanged(self, item: QListWidgetItem):
        pass

    def onFilterEdited(self, text: str):
        pass

    def updateStatusBar(self, world: Vector2):
        instance: GITInstance | None = self._ui.renderArea.instanceSelection.last()
        if instance:
            self._editor.statusBar().showMessage(
                f"({world.x:.1f}, {world.y:.1f}) Editing Geometry of {instance.identifier().resname}",
            )

    def onRenderContextMenu(self, world: Vector2, screen: QPoint):
        menu = QMenu(self._editor)

        if not self._ui.renderArea.geometrySelection.isEmpty():
            menu.addAction("Remove").triggered.connect(self.deleteSelected)

        if self._ui.renderArea.geometrySelection.count() == 0:
            menu.addAction("Insert").triggered.connect(self.insertPointAtMouse)

        menu.addSeparator()
        menu.addAction("Finish Editing").triggered.connect(self._editor.enterInstanceMode)

        menu.popup(screen)

    def openListContextMenu(self, item: QListWidgetItem, screen: QPoint):
        pass

    def updateVisibility(self):
        pass

    def selectUnderneath(self):
        underMouse: list[GeomPoint] = self._ui.renderArea.geomPointsUnderMouse()
        selection: list[GeomPoint] = self._ui.renderArea.geometrySelection.all()

        # Do not change the selection if the selected instance if its still underneath the mouse
        if selection and selection[0] in underMouse:
            return

        if underMouse:
            self._ui.renderArea.geometrySelection.select(underMouse)
        else:
            self._ui.renderArea.geometrySelection.select([])

    def deleteSelected(self):
        selected = self._ui.renderArea.geometrySelection.all()
        if not selected or self._ui.lockInstancesCheck.isChecked():
            return
        instances = list(dict.fromkeys(vertex.instance for vertex in selected))
        with self._editor.history.edit("Delete geometry points", instances):
            for instance in instances:
                point_ids = {id(vertex.point) for vertex in selected if vertex.instance is instance}
                instance.geometry.points[:] = [point for point in instance.geometry.points if id(point) not in point_ids]

    def duplicateSelected(self, position: Vector3):
        pass

    def moveSelected(self, x: float, y: float):
        for vertex in self._ui.renderArea.geometrySelection.all():
            vertex.point.x += x
            vertex.point.y += y

    def rotateSelected(self, angle: float):
        pass

    def rotateSelectedToPoint(self, x: float, y: float):
        pass

    def moveCamera(self, x: float, y: float):
        self._ui.renderArea.camera.nudgePosition(x, y)

    def zoomCamera(self, amount: float):
        self._ui.renderArea.camera.nudgeZoom(amount)

    def rotateCamera(self, angle: float):
        self._ui.renderArea.camera.nudgeRotation(angle)

    # endregion


class GITControlScheme:
    def __init__(self, editor: GITEditor):
        self.editor: GITEditor = editor
        self.settings: GITSettings = GITSettings()
        self.cameraSettings = ModuleDesignerSettings()

        self.panCamera: ControlItem = ControlItem(self.settings.moveCameraBind)
        self.rotateCamera: ControlItem = ControlItem(self.settings.rotateCameraBind)
        self.zoomCamera: ControlItem = ControlItem(self.settings.zoomCameraBind)
        self.rotateSelectedToPoint: ControlItem = ControlItem(self.settings.rotateSelectedToPointBind)
        self.moveSelected: ControlItem = ControlItem(self.settings.moveSelectedBind)
        self.selectUnderneath: ControlItem = ControlItem(self.settings.selectUnderneathBind)
        self.deleteSelected: ControlItem = ControlItem(self.settings.deleteSelectedBind)
        self.duplicateSelected: ControlItem = ControlItem(self.settings.duplicateSelectedBind)
        self.toggleInstanceLock: ControlItem = ControlItem(self.settings.toggleLockInstancesBind)

    def onMouseScrolled(self, delta, buttons, keys):
        if self.zoomCamera.satisfied(buttons, keys):
            self.editor.zoomCamera(1.1 ** (delta.y / 120 * self.cameraSettings.zoomCameraSensitivity2d / 100))

    def onMouseMoved(self, screen, screenDelta, world, worldDelta, buttons, keys):
        if self.panCamera.satisfied(buttons, keys):
            self.editor.moveCamera(-worldDelta.x * self.cameraSettings.moveCameraSensitivity2d / 100, -worldDelta.y * self.cameraSettings.moveCameraSensitivity2d / 100)
            return
        if self.rotateCamera.satisfied(buttons, keys):
            self.editor.rotateCamera(screenDelta.x * self.cameraSettings.rotateCameraSensitivity2d / 5000)
            return
        if self.moveSelected.satisfied(buttons, keys):
            self.editor.moveSelected(worldDelta.x, worldDelta.y)
        elif self.rotateSelectedToPoint.satisfied(buttons, keys):
            self.editor.rotateSelectedToPoint(world.x, world.y)

    def onMousePressed(self, screen: Vector2, buttons: set[int], keys: set[int]):
        if self.selectUnderneath.satisfied(buttons, keys):
            self.editor.selectUnderneath()
        if self.duplicateSelected.satisfied(buttons, keys):
            position = self.editor.ui.renderArea.toWorldCoords(screen.x, screen.y)
            self.editor.duplicateSelected(position)

    def onMouseReleased(self, screen: Vector2, buttons: set[int], keys: set[int]):
        ...

    def onKeyboardPressed(self, buttons: set[int], keys: set[int]):
        if self.deleteSelected.satisfied(buttons, keys):
            self.editor.deleteSelected()

        if self.toggleInstanceLock.satisfied(buttons, keys):
            self.editor.ui.lockInstancesCheck.setChecked(not self.editor.ui.lockInstancesCheck.isChecked())

    def onKeyboardReleased(self, buttons: set[int], keys: set[int]):
        ...
