from __future__ import annotations

import math
from typing import TYPE_CHECKING

from pykotor.common.geometry import Vector2
from pykotor.common.misc import Game
from pykotor.common.module import Module
from pykotor.common.stream import BinaryReader
from pykotor.extract.capsule import Capsule
from pykotor.gl.models.read_mdl import gl_load_mdl
from pykotor.gl.scene import RenderObject, Scene
from pykotor.resource.generics.git import GIT, GITCreature
from pykotor.tools.misc import is_capsule_file, is_mod_file
from pykotor.tools.model import detect_version
from pykotor.tools.path import CaseAwarePath
from PyQt5 import QtCore
from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import QAction, QOpenGLWidget, QWidget, QMessageBox
from toolset.data.settings import settings_signals
from toolset.data.misc import ControlItem, update_modifiers
from toolset.gui.widgets.settings.module_designer import ModuleDesignerSettings
from utility.error_handling import assert_with_variable_trace
from utility.logger_util import RobustRootLogger

if TYPE_CHECKING:
    import os

    from glm import vec3
    from pykotor.extract.installation import Installation
    from pykotor.resource.generics.utc import UTC
    from PyQt5.QtGui import QKeyEvent, QMouseEvent, QWheelEvent


class ModelRenderer(QOpenGLWidget):
    inputReset = QtCore.pyqtSignal()
    renderFailed = QtCore.pyqtSignal(str)
    renderReset = QtCore.pyqtSignal()
    def __init__(self, parent: QWidget):
        super().__init__(parent)

        self.scene: Scene | None = None
        self.installation: Installation | None = None
        self.capsules: list[Capsule] = []
        self.folders: list[CaseAwarePath] = []
        self._source: CaseAwarePath | None = None
        self._sourceInstallation: Installation | None = None
        self._game = Game.K2
        self._modelToLoad: tuple[BinaryReader, BinaryReader] | None = None
        self._creatureToLoad: UTC | None = None
        self._modelData: tuple[bytes, bytes] | None = None
        self._creature: UTC | None = None
        self.textureAssignments: dict[tuple[tuple[int, ...], str], str] = {}
        self.showRenderErrors = True
        self._resetCameraOnLoad = False

        self._keysDown: set[int] = set()
        self._mouseDown: set[int] = set()
        self._mousePrev: Vector2 = Vector2(0, 0)

        self._renderError: str | None = None
        self._reloadSceneRequested = False
        self._renderTimer = QTimer(self)
        self._renderTimer.setInterval(33)
        self._renderTimer.timeout.connect(self.loop)

        self.reloadSettings()
        settings_signals.changed.connect(self.reloadSettings)
        reload_action = QAction("Reload preview", self)
        reload_action.triggered.connect(self.reloadScene)
        self.addAction(reload_action)
        self.setContextMenuPolicy(QtCore.Qt.ActionsContextMenu)

    def loop(self):
        if self._renderError is None:
            self.update()

    def _initializeScene(self):
        camera = self.scene.camera if self.scene is not None else None
        if self.scene is not None:
            self.scene.executor.shutdown(wait=False)
        self.scene = Scene(
            installation=self.installation, game=self._game,
            capsules=self.capsules, folders=self.folders,
        )
        if camera is not None:
            self.scene.camera = camera
        else:
            self.scene.camera.fov = self.settings.fieldOfView
            self.scene.camera.distance = 4
            self.scene.camera.z = 1.8
            self.scene.camera.yaw = math.pi/2
        self.scene.show_cursor = False
        self.scene.git = GIT()

        # Qt can replace a context when the widget is reparented. Reload CPU
        # source data into the new context without resetting the current view.
        if self._modelData is not None:
            self._modelToLoad = (
                BinaryReader.from_auto(self._modelData[0], 12),
                BinaryReader.from_auto(self._modelData[1]),
            )
        self._creatureToLoad = self._creature
        self._reloadSceneRequested = False
        self._renderTimer.start()

    def _paintScene(self):
        """Renders the scene.

        Args:
        ----
            self: The class instance

        Processing Logic:
        ----------------
            - Checks if scene is None and returns if so
            - Loads model if _modelToLoad is not None
            - Loads creature if _creatureToLoad is not None
            - Renders the scene.
        """
        if self.width() <= 0 or self.height() <= 0:
            return
        if self.scene is None or self._reloadSceneRequested:
            self._initializeScene()
        if not self.scene.set_viewport(self.width(), self.height()):
            return

        if self._modelToLoad is not None:
            model = gl_load_mdl(self.scene, *self._modelToLoad)
            for (path, slot), texture in self.textureAssignments.items():
                node = model.root
                for index in path:
                    node = node.children[index]
                setattr(node.mesh, slot, texture)
            self.scene.models["model"] = model
            self.scene.objects["model"] = RenderObject("model")
            if self._resetCameraOnLoad:
                self.resetCamera()
                self._resetCameraOnLoad = False
            self._modelToLoad = None

        if self._creatureToLoad is not None:
            instance = GITCreature()
            utc = self._creatureToLoad

            self.scene.objects["model"] = self.scene.getCreatureRenderObject(instance, utc)
            if self._resetCameraOnLoad:
                self.resetCamera()
                self._resetCameraOnLoad = False
            self._creatureToLoad = None

        self.scene.render()

    def clearModel(self):
        self._renderError = None
        self.textureAssignments.clear()
        self.resetInput()
        self._modelData = None
        self._creature = None
        self._modelToLoad = None
        self._creatureToLoad = None
        self._resetCameraOnLoad = False
        if self.scene is not None and "model" in self.scene.objects:
            del self.scene.objects["model"]

    def setSource(self, filepath: os.PathLike | str | None):
        source = None if filepath is None else CaseAwarePath.pathify(filepath)
        if source == self._source and self.installation is self._sourceInstallation:
            return
        self._source, self._sourceInstallation = source, self.installation
        self.folders = [] if source is None else [source.parent]
        self.capsules = []
        if source is not None and is_capsule_file(source):
            self.capsules.append(Capsule(source))
            if not is_mod_file(source):
                root = Module.find_root(source).lower()
                names = {root + suffix for suffix in (".rim", "_s.rim", "_dlg.erf")}
                self.capsules.extend(
                    Capsule(path) for path in sorted(source.parent.iterdir(), key=lambda path: path.name.lower())
                    if path != source and path.name.lower() in names and path.is_file()
                )
        self._reloadSceneRequested = True

    def setModel(self, data: bytes, data_ext: bytes, *, source: os.PathLike | str | None = None):
        self.setSource(source)
        self.textureAssignments.clear()
        game = detect_version(data)
        if game != self._game:
            self._game = game
            self._reloadSceneRequested = True
        self._renderError = None
        self._renderTimer.start()
        self._modelData = (bytes(data), bytes(data_ext))
        self._creature = None
        self._creatureToLoad = None
        self._resetCameraOnLoad = True
        mdl = BinaryReader.from_auto(data, 12)
        mdx = BinaryReader.from_auto(data_ext)
        self._modelToLoad = mdl, mdx

    def setCreature(self, utc: UTC, *, source: os.PathLike | str | None = None):
        self.setSource(source)
        self.textureAssignments.clear()
        self._renderError = None
        self._renderTimer.start()
        self._modelData = None
        self._modelToLoad = None
        self._creature = utc
        self._creatureToLoad = utc
        self._resetCameraOnLoad = True

    def setTextureAssignment(self, path: tuple[int, ...], slot: str, texture: str | None):
        key = (path, slot)
        if texture is None:
            self.textureAssignments.pop(key, None)
        else:
            self.textureAssignments[key] = texture
        self.reloadScene()

    def resetCamera(self):
        scene: Scene | None = self.scene
        assert scene is not None, assert_with_variable_trace(scene is not None)
        if "model" in scene.objects:
            model: RenderObject = scene.objects["model"]
            scene.camera.x = 0
            scene.camera.y = 0
            scene.camera.z = (model.cube(scene).max_point.z - model.cube(scene).min_point.z) / 2
            scene.camera.pitch = math.pi / 16 * 9
            scene.camera.yaw = math.pi / 16 * 7
            scene.camera.distance = model.radius(scene) + 2

    # region Events
    def wheelEvent(self, e: QWheelEvent):
        update_modifiers(self._keysDown, e.modifiers())
        if self.scene is None or self._renderError is not None:
            return
        if self.zoomCamera.satisfied(self._mouseDown, self._keysDown):
            strength: float = self.settings.zoomCameraSensitivity3d / 2000
            self.scene.camera.distance += -e.angleDelta().y() * strength

        if self.moveZCamera.satisfied(self._mouseDown, self._keysDown):
            strength: float = self.settings.moveCameraSensitivity3d / 10000
            self.scene.camera.z -= -e.angleDelta().y() * strength

    def mouseMoveEvent(self, e: QMouseEvent):
        update_modifiers(self._keysDown, e.modifiers())
        if self.scene is None or self._renderError is not None:
            return
        screen = Vector2(e.localPos().x(), e.localPos().y())
        screenDelta = Vector2(screen.x - self._mousePrev.x, screen.y - self._mousePrev.y)
        self._mousePrev = screen

        if self.moveXYCamera.satisfied(self._mouseDown, self._keysDown):
            forward: vec3 = -screenDelta.y * self.scene.camera.forward()
            sideward: vec3 = screenDelta.x * self.scene.camera.sideward()
            strength = self.settings.moveCameraSensitivity3d / 10000
            self.scene.camera.x -= (forward.x + sideward.x) * strength
            self.scene.camera.y -= (forward.y + sideward.y) * strength

        if self.rotateCamera.satisfied(self._mouseDown, self._keysDown):
            strength = self.settings.rotateCameraSensitivity3d / 10000
            self.scene.camera.rotate(-screenDelta.x * strength, screenDelta.y * strength)

    def mousePressEvent(self, e: QMouseEvent):
        update_modifiers(self._keysDown, e.modifiers())
        if self.scene is None or self._renderError is not None:
            return
        self.setFocus(QtCore.Qt.MouseFocusReason)
        self._mousePrev = Vector2(e.localPos().x(), e.localPos().y())
        self._mouseDown.add(e.button())

    def mouseReleaseEvent(self, e: QMouseEvent):
        update_modifiers(self._keysDown, e.modifiers())
        self._mouseDown.discard(e.button())

    def keyPressEvent(self, e: QKeyEvent, bubble: bool = True):
        if e.isAutoRepeat():
            return
        self._keysDown.add(e.key())
        if self.scene is None or self._renderError is not None:
            return

        if self.rotateCameraLeft.satisfied(self._mouseDown, self._keysDown):
            self.scene.camera.rotate(math.pi/4, 0)
        if self.rotateCameraRight.satisfied(self._mouseDown, self._keysDown):
            self.scene.camera.rotate(-math.pi/4, 0)
        if self.rotateCameraUp.satisfied(self._mouseDown, self._keysDown):
            self.scene.camera.rotate(0, math.pi/4)
        if self.rotateCameraDown.satisfied(self._mouseDown, self._keysDown):
            self.scene.camera.rotate(0, -math.pi/4)

        if self.moveCameraUp.satisfied(self._mouseDown, self._keysDown):
            self.scene.camera.z += 1
        if self.moveCameraDown.satisfied(self._mouseDown, self._keysDown):
            self.scene.camera.z -= 1
        if self.moveCameraLeft.satisfied(self._mouseDown, self._keysDown):
            self.scene.camera.y -= 1
        if self.moveCameraRight.satisfied(self._mouseDown, self._keysDown):
            self.scene.camera.y += 1
        if self.moveCameraForward.satisfied(self._mouseDown, self._keysDown):
            self.scene.camera.x += 1
        if self.moveCameraBackward.satisfied(self._mouseDown, self._keysDown):
            self.scene.camera.x -= 1

        if self.zoomCameraIn.satisfied(self._mouseDown, self._keysDown):
            self.scene.camera.distance += 1
        if self.zoomCameraOut.satisfied(self._mouseDown, self._keysDown):
            self.scene.camera.distance -= 1

    def keyReleaseEvent(self, e: QKeyEvent, bubble: bool = True):
        if e.isAutoRepeat():
            return
        self._keysDown.discard(e.key())

    def paintGL(self):
        if self._renderError is not None:
            return
        try:
            self._paintScene()
        except Exception as exc:
            self._failRender(exc)

    def _failRender(self, error):
        if self._renderError is not None:
            return
        RobustRootLogger().error("Model preview failed", exc_info=(type(error), error, error.__traceback__))
        self._renderError = str(error) or type(error).__name__
        self._renderErrorReported = False
        self._renderTimer.stop()
        self.resetInput()
        self.renderFailed.emit(self._renderError)
        if self.showRenderErrors:
            QTimer.singleShot(0, self._showRenderError)

    def _showRenderError(self):
        if self._renderError is not None and not self._renderErrorReported:
            self._renderErrorReported = True
            QMessageBox.critical(self, "Preview paused", self._renderError + "\n\nCorrect the resource and explicitly reload the preview.")

    def resetInput(self):
        self._keysDown.clear()
        self._mouseDown.clear()
        self.inputReset.emit()

    def focusOutEvent(self, event):
        self.resetInput()
        super().focusOutEvent(event)

    def hideEvent(self, event):
        self.resetInput()
        self._renderTimer.stop()
        super().hideEvent(event)

    def showEvent(self, event):
        super().showEvent(event)
        if self._renderError is None and self.scene is not None:
            self._renderTimer.start()

    def closeEvent(self, event):
        self.resetInput()
        self._renderTimer.stop()
        if self.scene is not None:
            self.scene.executor.shutdown(wait=False)
        super().closeEvent(event)

    def initializeGL(self):
        if self._renderError is not None:
            return
        try:
            self._initializeScene()
        except Exception as exc:
            self._failRender(exc)

    def reloadScene(self):
        self.resetInput()
        self._renderError = None
        self.renderReset.emit()
        # Recreate GPU caches on the next paint, when Qt owns the active context.
        self._reloadSceneRequested = True
        self._renderTimer.start()
        self.update()

    def reloadSettings(self):
        self.settings: ModuleDesignerSettings = ModuleDesignerSettings()
        self.moveXYCamera: ControlItem = ControlItem(self.settings.moveCameraXY3dBind)
        self.moveZCamera: ControlItem = ControlItem(self.settings.moveCameraZ3dBind)
        self.rotateCamera: ControlItem = ControlItem(self.settings.rotateCamera3dBind)
        self.zoomCamera: ControlItem = ControlItem(self.settings.zoomCamera3dBind)
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
        self.resetInput()
        if self.scene is not None:
            self.scene.camera.fov = self.settings.fieldOfView
        self.update()
    # endregion
