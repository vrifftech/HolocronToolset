from __future__ import annotations

from typing import TYPE_CHECKING

from pykotor.common.geometry import SurfaceMaterial, Vector2, Vector3
from pykotor.common.misc import Color
from pykotor.extract.installation import SearchLocation
from pykotor.resource.formats.bwm import read_bwm
from pykotor.resource.formats.lyt import LYT, read_lyt
from pykotor.resource.generics.pth import PTH, bytes_pth, read_pth
from pykotor.resource.type import ResourceType
from PyQt5.QtGui import QColor, QKeyEvent
from PyQt5.QtWidgets import QMenu, QWidget, QMessageBox
from toolset.data.settings import settings_signals
from toolset.data.misc import ControlItem
from toolset.gui.editor import Editor
from toolset.gui.widgets.settings.git import GITSettings
from toolset.gui.widgets.settings.module_designer import ModuleDesignerSettings

if TYPE_CHECKING:
    import os

    from pykotor.extract.file import ResourceResult
    from pykotor.resource.formats.bwm.bwm_data import BWM
    from pykotor.resource.generics.git import GITInstance
    from PyQt5.QtCore import QPoint
    from toolset.data.installation import HTInstallation


class PTHEditor(Editor):
    def __init__(self, parent: QWidget | None, installation: HTInstallation | None = None):
        supported: list[ResourceType] = [ResourceType.PTH]
        super().__init__(parent, "PTH Editor", "pth", supported, supported, installation)

        from toolset.uic.editors.pth import Ui_MainWindow
        self.ui = Ui_MainWindow()
        self.ui.setupUi(self)
        self._setupMenus()
        self._setupSignals()

        self._pth: PTH = PTH()
        self._controls: PTHControlScheme = PTHControlScheme(self)

        self._loadSettings()
        settings_signals.changed.connect(self.reloadSettings)

        self.ui.renderArea.materialColors = self.materialColors  # FIXME: wrong type Color v int
        self.ui.renderArea.hideWalkmeshEdges = True
        self.ui.renderArea.highlightBoundaries = False

        self.new()

    def _setupSignals(self):
        self.ui.renderArea.mousePressed.connect(self.onMousePressed)
        self.ui.renderArea.mouseMoved.connect(self.onMouseMoved)
        self.ui.renderArea.mouseScrolled.connect(self.onMouseScrolled)
        self.ui.renderArea.mouseReleased.connect(self.onMouseReleased)
        self.ui.renderArea.customContextMenuRequested.connect(self.onContextMenu)
        self.ui.renderArea.keyPressed.connect(self.onKeyPressed)

    def load(self, filepath, resref, restype, data):
        pth = read_pth(data)
        super().load(filepath, resref, restype, data)
        self.ui.renderArea.setWalkmeshes([])
        self._loadPTH(pth)
        if self._installation is None:
            QMessageBox.warning(self, "Layout unavailable", "The path document is open, but an installation is required to locate its layout.")
            return
        order = [SearchLocation.OVERRIDE, SearchLocation.CHITIN, SearchLocation.MODULES]
        result = self._installation.resource(resref, ResourceType.LYT, order)
        if result is None:
            QMessageBox.warning(self, "Layout unavailable", f"Cannot locate {resref}.lyt. The path document is retained; walkmesh context is unavailable.")
            return
        try:
            self.loadLayout(read_lyt(result.data))
        except Exception as error:
            QMessageBox.warning(self, "Layout unavailable", f"The path document is retained, but its layout could not be loaded:\n{error}")

    def _loadPTH(self, pth: PTH):
        self.ui.renderArea.pathSelection.clear()
        self._pth = pth
        self.ui.renderArea.centerCamera()
        self.ui.renderArea.setPth(pth)

    def build(self) -> tuple[bytes, bytes]:
        return bytes_pth(self._pth), b""

    def new(self):
        super().new()
        self._loadPTH(PTH())

    def pth(self) -> PTH:
        return self._pth

    def loadLayout(self, layout: LYT):
        walkmeshes: list[BWM] = []
        for room in layout.rooms:
            order: list[SearchLocation] = [SearchLocation.OVERRIDE, SearchLocation.CHITIN, SearchLocation.MODULES]
            findBWM: ResourceResult | None = self._installation.resource(room.model, ResourceType.WOK, order)
            if findBWM is not None:
                walkmeshes.append(read_bwm(findBWM.data))

        self.ui.renderArea.setWalkmeshes(walkmeshes)

    def moveCameraToSelection(self):
        point = self.ui.renderArea.pathSelection.last()
        if point is not None:
            self.ui.renderArea.camera.setPosition(point.x, point.y)

    def moveCamera(self, x: float, y: float):
        self.ui.renderArea.camera.nudgePosition(x, y)

    def zoomCamera(self, amount: float):
        self.ui.renderArea.camera.nudgeZoom(amount)

    def rotateCamera(self, angle: float):
        self.ui.renderArea.camera.nudgeRotation(angle)

    def moveSelected(self, x: float, y: float):
        for point in self.ui.renderArea.pathSelection.all():
            point.x += x
            point.y += y

    def selectNodeUnderMouse(self):
        if self.ui.renderArea.pathNodesUnderMouse():
            toSelect: list[Vector2] = [self.ui.renderArea.pathNodesUnderMouse()[0]]
            self.ui.renderArea.pathSelection.select(toSelect)
        else:
            self.ui.renderArea.pathSelection.clear()

    def addNode(self, x: float, y: float):
        self._pth.add(x, y)

    def removeNode(self, index: int):
        self._pth.remove(index)
        self.ui.renderArea.pathSelection.clear()

    def removeEdge(self, source: int, target: int):
        if not (0 <= source < len(self._pth) and 0 <= target < len(self._pth)):
            raise IndexError("Path point is outside the document.")
        self._pth._connections[:] = [
            edge for edge in self._pth._connections
            if (edge.source, edge.target) not in {(source, target), (target, source)}
        ]
        self.ui.renderArea.update()

    def addEdge(self, source: int, target: int):
        if not (0 <= source < len(self._pth) and 0 <= target < len(self._pth)):
            raise IndexError("Path point is outside the document.")
        if source == target:
            return
        if not self._pth.is_connected(source, target):
            self._pth.connect(source, target)
        if not self._pth.is_connected(target, source):
            self._pth.connect(target, source)
        self.ui.renderArea.update()

    def pointsUnderMouse(self) -> list[Vector2]:
        return self.ui.renderArea.pathNodesUnderMouse()

    def selectedNodes(self) -> list[Vector2]:
        return self.ui.renderArea.pathSelection.all()

    # region Signal Callbacks
    def onContextMenu(self, point: QPoint):
        globalPoint: QPoint = self.ui.renderArea.mapToGlobal(point)
        world: Vector3 = self.ui.renderArea.toWorldCoords(point.x(), point.y())
        self._controls.onRenderContextMenu(world, globalPoint)

    def onMouseMoved(self, screen: Vector2, delta: Vector2, buttons: set[int], keys: set[int]):
        worldDelta: Vector2 = self.ui.renderArea.toWorldDelta(delta.x, delta.y)
        world: Vector3 = self.ui.renderArea.toWorldCoords(screen.x, screen.y)
        self._controls.onMouseMoved(screen, delta, world, worldDelta, buttons, keys)  # FIXME: world is Vector3, arg is Vector2

    def onMouseScrolled(self, delta: Vector2, buttons: set[int], keys: set[int]):
        self._controls.onMouseScrolled(delta, buttons, keys)

    def onMousePressed(self, screen: Vector2, buttons: set[int], keys: set[int]):
        self._controls.onMousePressed(screen, buttons, keys)

    def onMouseReleased(self, screen: Vector2, buttons: set[int], keys: set[int]):
        self._controls.onMouseReleased(screen, buttons, keys)

    def onKeyPressed(self, buttons: set[int], keys: set[int]):
        self._controls.onKeyboardPressed(buttons, keys)

    def keyPressEvent(self, e: QKeyEvent | None):
        self.ui.renderArea.keyPressEvent(e)

    def keyReleaseEvent(self, e: QKeyEvent | None):
        self.ui.renderArea.keyReleaseEvent(e)

    def pointIndex(self, point):
        # Equal coordinates do not identify an occurrence.
        return next((index for index, candidate in enumerate(self._pth) if candidate is point), None)

    def deleteSelected(self):
        indices = {self.pointIndex(point) for point in self.selectedNodes()}
        for index in sorted((i for i in indices if i is not None), reverse=True):
            self._pth.remove(index)
        self.ui.renderArea.pathSelection.clear()
        self.ui.renderArea.update()

    def _loadSettings(self):
        self.settings = GITSettings()

        def intColorToQColor(intvalue):
            color = Color.from_rgba_integer(intvalue)
            return QColor(int(color.r*255), int(color.g*255), int(color.b*255), int(color.a*255))
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
        self._controls = PTHControlScheme(self)
        self.ui.renderArea.resetInput()
        self.ui.renderArea.materialColors = self.materialColors
        self.ui.renderArea.update()
    # endregion


class PTHControlScheme:
    def __init__(self, editor: PTHEditor):
        self.editor: PTHEditor = editor
        self.settings: GITSettings = GITSettings()
        self.cameraSettings = ModuleDesignerSettings()

        self.panCamera: ControlItem = ControlItem(self.settings.moveCameraBind)
        self.rotateCamera: ControlItem = ControlItem(self.settings.rotateCameraBind)
        self.zoomCamera: ControlItem = ControlItem(self.settings.zoomCameraBind)
        self.moveSelected: ControlItem = ControlItem(self.settings.moveSelectedBind)
        self.selectUnderneath: ControlItem = ControlItem(self.settings.selectUnderneathBind)
        self.deleteSelected: ControlItem = ControlItem(self.settings.deleteSelectedBind)

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

    def onMousePressed(self, screen: Vector2, buttons: set[int], keys: set[int]):
        if self.selectUnderneath.satisfied(buttons, keys):
            self.editor.selectNodeUnderMouse()

    def onMouseReleased(self, screen: Vector2, buttons: set[int], keys: set[int]):
        ...

    def onKeyboardPressed(self, buttons: set[int], keys: set[int]):
        if self.deleteSelected.satisfied(buttons, keys):
            self.editor.deleteSelected()  # FIXME: undefined

    def onKeyboardReleased(self, buttons: set[int], keys: set[int]):
        ...

    def onRenderContextMenu(self, world, screen):
        hovered = self.editor.pointsUnderMouse()
        selected = self.editor.selectedNodes()
        target = self.editor.pointIndex(hovered[0]) if hovered else None
        source = self.editor.pointIndex(selected[0]) if selected else None
        menu = QMenu(self.editor)
        menu.addAction("Add Node").triggered.connect(lambda _=False: self.editor.addNode(world.x, world.y))
        if target is not None:
            menu.addAction("Remove Node").triggered.connect(lambda _=False: self.editor.removeNode(target))
        if source is not None and target is not None and source != target:
            menu.addSeparator()
            menu.addAction("Add Edge").triggered.connect(lambda _=False: self.editor.addEdge(source, target))
            menu.addAction("Remove Edge").triggered.connect(lambda _=False: self.editor.removeEdge(source, target))
        menu.aboutToHide.connect(self.editor.ui.renderArea.resetInput)
        menu.popup(screen)
