from __future__ import annotations

from toolset.data.misc import update_modifiers

import math
from copy import copy
from time import monotonic
from typing import TYPE_CHECKING

from pykotor.common.geometry import Vector2, Vector3
from pykotor.gl.scene import Scene
from pykotor.resource.formats.bwm.bwm_data import BWM
from pykotor.resource.generics.git import GITInstance
from pykotor.resource.type import ResourceType
from PyQt5 import QtCore
from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import QAction, QOpenGLWidget, QWidget, QMessageBox
from utility.error_handling import assert_with_variable_trace

if TYPE_CHECKING:
    from glm import vec3
    from pykotor.common.module import Module
    from pykotor.resource.formats.bwm import BWMFace
    from PyQt5.QtGui import QKeyEvent, QMouseEvent, QWheelEvent
    from toolset.data.installation import HTInstallation


class ModuleRenderer(QOpenGLWidget):
    inputReset = QtCore.pyqtSignal()
    renderFailed = QtCore.pyqtSignal(str)
    sceneInitalized = QtCore.pyqtSignal()
    """Signal emitted when scene has been initialized."""

    mouseMoved = QtCore.pyqtSignal(object, object, object, object, object)  # screen coords, screen delta, world/mouse pos, mouse, keys
    """Signal emitted when mouse is moved over the widget."""

    mouseScrolled = QtCore.pyqtSignal(object, object, object)  # screen delta, mouse, keys
    """Signal emitted when mouse is scrolled over the widget."""

    mouseReleased = QtCore.pyqtSignal(object, object, object)  # screen coords, mouse, keys
    """Signal emitted when a mouse button is released after being pressed on the widget."""

    mousePressed = QtCore.pyqtSignal(object, object, object)  # screen coords, mouse, keys
    """Signal emitted when a mouse button is pressed on the widget."""

    keyboardPressed = QtCore.pyqtSignal(object, object)  # mouse, keys

    keyboardReleased = QtCore.pyqtSignal(object, object)  # mouse, keys

    tick = QtCore.pyqtSignal(float)

    objectSelected = QtCore.pyqtSignal(object)
    """Signal emitted when an object has been selected through the renderer."""

    def __init__(self, parent: QWidget):
        """Initializes the ModuleDesigner widget.

        Args:
        ----
            parent: QWidget: The parent widget.

        Processing Logic:
        ----------------
            - Calls super().__init__() to initialize the base QWidget class
            - Initializes scene, settings and other member variables
            - Sets initial values for mouse, key states and camera properties
        """
        super().__init__(parent)

        from toolset.gui.windows.module_designer import ModuleDesignerSettings

        self.scene: Scene | None = None
        self.settings: ModuleDesignerSettings = ModuleDesignerSettings()
        self._module: Module | None = None
        self._installation: HTInstallation | None = None
        self._init = False
        self._camera = None

        self._keysDown: set[int] = set()
        self._mouseDown: set[int] = set()
        self._mousePrev: Vector2 = Vector2(0, 0)
        self._lastTick = None

        self.doSelect: bool = False  # Set to true to select object at mouse pointer
        self.freeCam: bool = False  # Changes how screenDelta is calculated in mouseMoveEvent
        self.delta: float = 0.0333
        self._renderError: str | None = None
        self._renderTimer = QTimer(self)
        self._renderTimer.setInterval(33)
        self._renderTimer.timeout.connect(self.loop)

    def init(self, installation, module):
        self.clearScene()
        self._installation = installation
        self._module = module
        self._renderTimer.start()
        self.update()

    def loop(self):
        if self._module is None or self._renderError is not None:
            return
        now = monotonic()
        elapsed = 0.0 if self._lastTick is None else now - self._lastTick
        self._lastTick = now
        self.delta = elapsed
        self.update()
        if self.scene is not None and self.hasFocus():
            self.tick.emit(elapsed)

    def walkmeshPoint(self, x: float, y: float, default_z: float = 0.0) -> Vector3:
        """Finds the face and z-height at a point on the walkmesh.

        Args:
        ----
            x: float - The x coordinate of the point
            y: float - The y coordinate of the point
            default_z: float = 0.0 - The default z height if no face is found

        Returns:
        -------
            Vector3 - The (x, y, z) position on the walkmesh

        Processing Logic:
        ----------------
            - Iterates through walkmesh resources to find the face at the given (x,y) coordinates
            - Checks if the found face is walkable, and overrides any previous less walkable face
            - Returns a Vector3 with the input x,y coords and either the face z height or default z if no face.
        """
        face: BWMFace | None = None
        for module_resource in self._module.resources.values():
            if module_resource.restype() != ResourceType.WOK:
                continue
            walkmesh_resource = module_resource.resource()
            if walkmesh_resource is None:
                continue
            assert isinstance(walkmesh_resource, BWM), assert_with_variable_trace(isinstance(walkmesh_resource, BWM))
            over: BWMFace | None = walkmesh_resource.faceAt(x, y)
            if over is None:
                continue
            if face is None:  # noqa: SIM114
                face = over
            elif not face.material.walkable() and over.material.walkable():
                face = over

        z: float = default_z if face is None else face.determine_z(x, y)
        return Vector3(x, y, z)

    def initializeGL(self):
        # GL objects belong to the old context; retain only the camera.
        if self.scene is not None:
            self._camera = self.scene.camera
            self.scene.executor.shutdown(wait=False)
        self.scene = None
        self._init = False

    def _paintScene(self):
        """Use Qt's physical framebuffer for rendering and logical event positions."""
        if self._module is None or self.width() <= 0 or self.height() <= 0:
            return
        if not self._init:
            self._initialize_paintGL()
        if not self.scene.set_viewport(self.width(), self.height()):
            return

        if self.doSelect:
            self.doSelect = False
            if 0 <= self._mousePrev.x < self.width() and 0 <= self._mousePrev.y < self.height():
                obj = self.scene.pick(self._mousePrev.x, self._mousePrev.y)
                if obj is not None and isinstance(obj.data, GITInstance):
                    self.objectSelected.emit(obj.data)
                else:
                    self.scene.selection.clear()
                    self.objectSelected.emit(None)

        screenCursor = self.mapFromGlobal(self.cursor().pos())
        worldCursor = self.scene.screenToWorld(screenCursor.x(), screenCursor.y())
        if worldCursor is not None:
            self.scene.cursor.set_position(worldCursor.x, worldCursor.y, worldCursor.z)

        self.scene.render()

    def _initialize_paintGL(self):
        self.scene = Scene(installation=self._installation, module=self._module)
        if self._camera is not None:
            self.scene.camera = self._camera
            self._camera = None
        self.scene.camera.fov = self.settings.fieldOfView
        self.scene.set_viewport(self.width(), self.height())
        self._init = True
        self.scene.buildCache()
        self.sceneInitalized.emit()

    # region Accessors
    def keysDown(self) -> set[int]:
        return copy(self._keysDown)

    def mouseDown(self) -> set[int]:
        return copy(self._mouseDown)
    # endregion

    # region Camera Transformations
    def snapCameraToPoint(self, point: Vector3, distance: float = 6.0):
        if self.scene is None or self._renderError is not None:
            return
        camera = self.scene.camera
        camera.x, camera.y, camera.z = point.x, point.y, point.z+1.0
        camera.distance = distance

    def panCamera(self, forward: float, right: float, up: float):
        """Moves the camera by the specified amount.

        The movement takes into account both the rotation and zoom of the
        camera on the x/y plane.

        Args:
        ----
            forward: Units to move forwards.
            right: Units to move to the right.
            up: Units to move upwards.
        """
        forward_vec: vec3 = forward * self.scene.camera.forward()
        sideward = right * self.scene.camera.sideward()

        self.scene.camera.x += (forward_vec.x + sideward.x)
        self.scene.camera.y += (forward_vec.y + sideward.y)
        self.scene.camera.z += up

    def moveCamera(self, forward: float, right: float, up: float):
        forward_vec: vec3 = forward * self.scene.camera.forward(ignore_z=False)
        sideward = right * self.scene.camera.sideward(ignore_z=False)
        upward = -up * self.scene.camera.upward(ignore_xy=False)

        self.scene.camera.x += upward.x + sideward.x + forward_vec.x
        self.scene.camera.y += upward.y + sideward.y + forward_vec.y
        self.scene.camera.z += upward.z + sideward.z + forward_vec.z

    def rotateCamera(self, yaw: float, pitch: float, snapRotations: bool = True):
        """Rotates the camera by the angles (radians) specified.

        Args:
        ----
            yaw:
            pitch:
            snapRotations:
        """
        self.scene.camera.rotate(yaw, pitch)
        if self.scene.camera.pitch < math.pi/2 and snapRotations:
            self.scene.camera.pitch = math.pi/2
        if self.scene.camera.pitch > math.pi and snapRotations:
            self.scene.camera.pitch = math.pi

    def zoomCamera(self, distance: float):
        self.scene.camera.distance -= distance
        self.scene.camera.distance = max(self.scene.camera.distance, 0)
    # endregion

    # region Events
    def wheelEvent(self, e: QWheelEvent):
        update_modifiers(self._keysDown, e.modifiers())
        if self.scene is None or self._renderError is not None:
            return
        self.mouseScrolled.emit(Vector2(e.angleDelta().x(), e.angleDelta().y()), set(self._mouseDown), set(self._keysDown))

    def mouseMoveEvent(self, e):
        update_modifiers(self._keysDown, e.modifiers())
        if self.scene is None or self._renderError is not None:
            return
        screen = Vector2(e.localPos().x(), e.localPos().y())
        if self.freeCam:
            delta = Vector2(screen.x - self.width() / 2, screen.y - self.height() / 2)
        else:
            delta = screen - self._mousePrev
        self._mousePrev = screen
        self.mouseMoved.emit(screen, delta, self.scene.cursor.position(), set(self._mouseDown), set(self._keysDown))

    def mousePressEvent(self, e: QMouseEvent):
        update_modifiers(self._keysDown, e.modifiers())
        if self.scene is None or self._renderError is not None:
            return
        self.setFocus(QtCore.Qt.MouseFocusReason)
        self._mouseDown.add(e.button())
        coords = Vector2(e.localPos().x(), e.localPos().y())
        self._mousePrev = coords
        self.mousePressed.emit(coords, set(self._mouseDown), set(self._keysDown))

    def mouseReleaseEvent(self, e: QMouseEvent):
        update_modifiers(self._keysDown, e.modifiers())
        self._mouseDown.discard(e.button())

        coords = Vector2(e.localPos().x(), e.localPos().y())
        self._mousePrev = coords
        self.mouseReleased.emit(coords, set(self._mouseDown), set(self._keysDown))

    def keyPressEvent(self, e: QKeyEvent, bubble: bool = True):
        if e.isAutoRepeat():
            return
        self._keysDown.add(e.key())
        if self.hasFocus() and self.scene is not None and self._renderError is None:
            self.keyboardPressed.emit(set(self._mouseDown), set(self._keysDown))

    def keyReleaseEvent(self, e: QKeyEvent, bubble: bool = True):
        if e.isAutoRepeat():
            return
        self._keysDown.discard(e.key())
        if self.hasFocus() and self.scene is not None and self._renderError is None:
            self.keyboardReleased.emit(set(self._mouseDown), set(self._keysDown))

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
        self._renderError = str(error) or type(error).__name__
        self._renderErrorReported = False
        self._renderTimer.stop()
        self.resetInput()
        self.renderFailed.emit(self._renderError)
        QTimer.singleShot(0, self._showRenderError)

    def _showRenderError(self):
        if self._renderError is not None and not self._renderErrorReported:
            self._renderErrorReported = True
            QMessageBox.critical(self, "Preview paused", self._renderError + "\n\nCorrect the resource and explicitly reload the preview.")

    def resetInput(self):
        self._keysDown.clear()
        self._mouseDown.clear()
        self._lastTick = None
        self.doSelect = False
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
        if self._renderError is None and self._module is not None:
            self._renderTimer.start()

    def closeEvent(self, event):
        self.resetInput()
        self._renderTimer.stop()
        if self.scene is not None:
            self.scene.executor.shutdown(wait=False)
        super().closeEvent(event)

    def clearScene(self):
        self._renderTimer.stop()
        self.resetInput()
        if self.scene is not None:
            self.scene.executor.shutdown(wait=False)
        self.scene = None
        self._module = None
        self._camera = None
        self._init = False
        self._renderError = None

    def reloadScene(self):
        if self._module is None:
            return
        self.resetInput()
        if self.scene is not None:
            self._camera = self.scene.camera
            self.scene.executor.shutdown(wait=False)
        self.scene = None
        self._init = False
        self._renderError = None
        self._renderTimer.start()
        self.update()
    # endregion
