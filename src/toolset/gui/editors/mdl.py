from __future__ import annotations

from typing import TYPE_CHECKING

from pykotor.common.stream import BinaryReader
from pykotor.extract.installation import SearchLocation
from pykotor.resource.formats.erf import read_erf
from pykotor.resource.formats.mdl import MDL, read_mdl, write_mdl
from pykotor.resource.formats.rim import read_rim
from pykotor.resource.type import ResourceType
from pykotor.tools.misc import is_any_erf_type_file, is_bif_file, is_rim_file
from pykotor.tools.path import CaseAwarePath
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QBrush, QColor
from PyQt5.QtWidgets import QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QSplitter, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget
from toolset.data.model_textures import ModelTextureCatalog
from toolset.gui.dialogs.model_texture import ModelTextureDialog
from toolset.gui.editor import Editor

if TYPE_CHECKING:
    import os

    from toolset.data.installation import HTInstallation


class MDLEditor(Editor):
    def __init__(self, parent: QWidget | None, installation: HTInstallation | None = None):
        """Initialize the Model Viewer window.

        Args:
        ----
            parent: {QWidget}: The parent widget of this window
            installation: {HTInstallation}: The installation context

        Processing Logic:
        ----------------
            - Initialize the base class with the given parameters
            - Create an MDL model object
            - Load the UI from the designer file
            - Set up menus and connect signals
            - Set the installation on the model renderer
            - Call new() to start with a blank state.
        """
        read_supported = [ResourceType.MDL, ResourceType.MDX]
        write_supported = [ResourceType.MDL]
        super().__init__(parent, "Model Viewer", "none", read_supported, write_supported, installation)

        self._mdl: MDL = MDL()
        self._installation = installation

        from toolset.uic.editors.mdl import Ui_MainWindow

        self.ui = Ui_MainWindow()
        self.ui.setupUi(self)
        self._setupTextureAssignments()
        self._setupMenus()
        self._setupSignals()

        self.ui.modelRenderer.installation = installation

        self.new()

    def _setupSignals(self):
        self.assignTextureButton.clicked.connect(self._assignTexture)
        self.resetTextureButton.clicked.connect(self._resetTexture)
        self.reloadPreviewButton.clicked.connect(self._reloadPreview)
        self.textureTree.itemSelectionChanged.connect(self._textureSelectionChanged)
        self.textureTree.itemDoubleClicked.connect(lambda _item, _column: self._assignTexture() if self.assignTextureButton.isEnabled() else None)
        self.ui.modelRenderer.renderFailed.connect(self._previewFailed)
        self.ui.modelRenderer.renderReset.connect(self.previewError.clear)

    def _setupTextureAssignments(self):
        self._textureCatalog = None
        self._textureRows = {}
        self.ui.modelRenderer.showRenderErrors = False
        self.ui.gridLayout.removeWidget(self.ui.modelRenderer)
        splitter = QSplitter(Qt.Horizontal, self.ui.centralwidget)
        splitter.addWidget(self.ui.modelRenderer)
        panel = QWidget(splitter)
        layout = QVBoxLayout(panel)
        heading = QLabel("Mesh texture assignments", panel)
        layout.addWidget(heading)
        hint = QLabel("Assignments affect only this preview. Saved model data keeps its original texture references.", panel)
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.textureTree = QTreeWidget(panel)
        self.textureTree.setHeaderLabels(["Mesh / slot", "Requested", "Preview"])
        self.textureTree.setAlternatingRowColors(True)
        self.textureTree.setUniformRowHeights(True)
        for column, width in enumerate((160, 155, 155)):
            self.textureTree.setColumnWidth(column, width)
        layout.addWidget(self.textureTree, 1)
        self.textureStatus = QLabel(panel)
        self.textureStatus.setWordWrap(True)
        layout.addWidget(self.textureStatus)
        buttons = QHBoxLayout()
        self.assignTextureButton = QPushButton("Assign texture…", panel)
        self.resetTextureButton = QPushButton("Reset assignment", panel)
        self.assignTextureButton.setEnabled(False)
        self.resetTextureButton.setEnabled(False)
        buttons.addWidget(self.assignTextureButton)
        buttons.addWidget(self.resetTextureButton)
        layout.addLayout(buttons)
        self.previewError = QPlainTextEdit(panel)
        self.previewError.setReadOnly(True)
        self.previewError.setMaximumHeight(110)
        self.previewError.hide()
        self.previewError.textChanged.connect(lambda: self.previewError.setVisible(bool(self.previewError.toPlainText())))
        layout.addWidget(self.previewError)
        self.reloadPreviewButton = QPushButton("Reload preview", panel)
        layout.addWidget(self.reloadPreviewButton)
        splitter.addWidget(panel)
        splitter.setStretchFactor(0, 1)
        splitter.setSizes([650, 500])
        self.ui.gridLayout.addWidget(splitter, 0, 0)
        self.resize(1150, 680)

    def _createTextureCatalog(self):
        renderer = self.ui.modelRenderer
        for capsule in renderer.capsules:
            capsule.reload()
        requested = {node.mesh.texture_1 for node in self._mdl.all_nodes() if node.mesh is not None}
        self._textureCatalog = ModelTextureCatalog(
            self._installation, renderer.capsules, renderer.folders,
            {self._mdl.name, self._resname or ""}, requested, self._mdl.game,
        )

    def _populateTextureAssignments(self):
        selected = self.textureTree.currentItem()
        selected_key = None if selected is None else selected.data(0, Qt.UserRole)
        self.textureTree.clear()
        self._textureRows.clear()
        pending = [(self._mdl.root, ())]
        while pending:
            node, path = pending.pop()
            pending.extend((child, (*path, index)) for index, child in reversed(list(enumerate(node.children))))
            mesh = node.mesh
            if mesh is None:
                continue
            parent = QTreeWidgetItem([node.name])
            parent.setToolTip(0, node.name)
            self.textureTree.addTopLevelItem(parent)
            rendered = mesh.render and node.aabb is None and any(mesh.indices)
            for label, slot, requested, bit, offset in (
                ("Diffuse", "texture", mesh.texture_1, 2, mesh.mdx_offsets[3]),
                ("Lightmap", "lightmap", mesh.texture_2, 4, mesh.mdx_offsets[4]),
            ):
                key = (path, slot)
                item = QTreeWidgetItem([label, requested or "NULL"])
                item.setData(0, Qt.UserRole, key)
                used = rendered and bool(mesh.mdx_bitmap & bit) and offset != 0xFFFFFFFF
                item.setData(0, Qt.UserRole + 1, used)
                parent.addChild(item)
                self._textureRows[key] = item
                self._updateTextureRow(key, "No UV channel" if rendered else "Not rendered")
        self.textureTree.expandAll()
        selectable = [item for item in self._textureRows.values() if item.data(0, Qt.UserRole + 1)]
        if selected_key in self._textureRows:
            self.textureTree.setCurrentItem(self._textureRows[selected_key])
        elif selectable:
            self.textureTree.setCurrentItem(next((item for item in selectable if item.data(0, Qt.UserRole + 2) == "Missing"), selectable[0]))
        self._textureSelectionChanged()

    def _updateTextureRow(self, key, unused="No UV channel"):
        item = self._textureRows[key]
        texture = self.ui.modelRenderer.textureAssignments.get(key, item.text(1))
        item.setText(2, texture)
        used = item.data(0, Qt.UserRole + 1)
        missing = used and texture.upper() != "NULL" and texture.lower() not in self._textureCatalog.textures
        item.setData(0, Qt.UserRole + 2, ("Missing" if missing else "Found" if texture.upper() != "NULL" else "Untextured") if used else unused)
        requested_missing = used and item.text(1).upper() != "NULL" and item.text(1).lower() not in self._textureCatalog.textures
        item.setForeground(1, QBrush(QColor("#c0392b")) if requested_missing else QBrush())
        item.setForeground(2, QBrush(QColor("#c0392b")) if missing else QBrush())
        for column in (1, 2):
            item.setToolTip(column, item.text(column))

    def _textureSelectionChanged(self):
        item = self.textureTree.currentItem()
        key = None if item is None else item.data(0, Qt.UserRole)
        self.assignTextureButton.setEnabled(key is not None and bool(item.data(0, Qt.UserRole + 1)))
        self.resetTextureButton.setEnabled(key in self.ui.modelRenderer.textureAssignments)
        self.textureStatus.setText("Select a diffuse or lightmap slot." if key is None
                                   else f"{item.data(0, Qt.UserRole + 2)}: {item.text(2)}")

    def _assignTexture(self):
        item = self.textureTree.currentItem()
        key = item.data(0, Qt.UserRole)
        if key[1] == "lightmap":
            suggestions = {node.mesh.texture_2.lower(): {"Requested as a lightmap by this model"}
                           for node in self._mdl.all_nodes() if node.mesh is not None
                           and node.mesh.texture_2.lower() in self._textureCatalog.textures}
        else:
            suggestions = self._textureCatalog.suggestions
        dialog = ModelTextureDialog(self, self._textureCatalog, item.text(2), suggestions)
        if dialog.exec_():
            self.previewError.clear()
            self.ui.modelRenderer.setTextureAssignment(*key, dialog.texture())
            self._updateTextureRow(key)
            self._textureSelectionChanged()

    def _resetTexture(self):
        key = self.textureTree.currentItem().data(0, Qt.UserRole)
        self.previewError.clear()
        self.ui.modelRenderer.setTextureAssignment(*key, None)
        self._updateTextureRow(key)
        self._textureSelectionChanged()

    def _reloadPreview(self):
        self.previewError.clear()
        self._createTextureCatalog()
        self._populateTextureAssignments()
        self.ui.modelRenderer.reloadScene()

    def _previewFailed(self, message: str):
        self.previewError.setPlainText(f"Preview paused: {message}\nAssign a texture above, then reload the preview.")

    def load(self, filepath: os.PathLike | str, resref: str, restype: ResourceType, data: bytes):
        """Loads a model resource and its associated data.

        Args:
        ----
            filepath: {Path to the resource file}
            resref: {Resource reference string}
            restype: {Resource type (MDL or MDX)}
            data: {Binary data of the resource}

        Loads associated MDL/MDX data:
            - Checks file extension and loads associated data from file
            - Loads associated data from Erf, Rim or Bif files if present
            - Sets model data on renderer if both MDL and MDX found
            - Displays error if unable to find associated data.
        """
        c_filepath = CaseAwarePath.pathify(filepath)
        if restype not in (ResourceType.MDL, ResourceType.MDX):
            raise ValueError("The model viewer requires an MDL or MDX resource")
        companion_type = ResourceType.MDX if restype == ResourceType.MDL else ResourceType.MDL
        companion_data = None
        if c_filepath.suffix.lower() in (".mdl", ".mdx"):
            expected = c_filepath.with_suffix("." + companion_type.extension).name.lower()
            matches = [p for p in c_filepath.parent.iterdir()
                       if p.name.lower() == expected and p.is_file()]
            if len(matches) != 1:
                raise FileNotFoundError(f"Expected one companion '{expected}' beside '{c_filepath}'")
            companion_data = BinaryReader.load_file(matches[0])
            if restype == ResourceType.MDX:
                c_filepath = CaseAwarePath(matches[0])
        elif is_any_erf_type_file(c_filepath.name):
            companion_data = read_erf(c_filepath).get(resref, companion_type)
        elif is_rim_file(c_filepath.name):
            companion_data = read_rim(c_filepath).get(resref, companion_type)
        elif is_bif_file(c_filepath.name) and self._installation is not None:
            resource = self._installation.resource(resref, companion_type, [SearchLocation.CHITIN])
            if resource is not None:
                companion_data = resource.data
        if companion_data is None:
            raise FileNotFoundError(f"Missing '{resref}.{companion_type.extension}' for the model")
        mdl_data, mdx_data = (data, companion_data) if restype == ResourceType.MDL else (companion_data, data)
        model = read_mdl(mdl_data, source_ext=mdx_data)
        self._mdl = model
        self.ui.modelRenderer.setModel(mdl_data, mdx_data, source=c_filepath)
        # Opening the companion still edits/saves the pair under its MDL identity.
        super().load(c_filepath, resref, ResourceType.MDL, mdl_data)
        self.previewError.clear()
        self._createTextureCatalog()
        self._populateTextureAssignments()
        self.ui.modelRenderer.reloadScene()

    def _loadMDL(self, mdl: MDL):
        self._mdl = mdl

    def build(self) -> tuple[bytes, bytes]:
        data = bytearray()
        data_ext = bytearray()
        write_mdl(self._mdl, data, ResourceType.MDL, data_ext)
        return data, data_ext

    def new(self):
        super().new()
        self._mdl = MDL()
        self.ui.modelRenderer.clearModel()
        self.textureTree.clear()
        self._textureRows.clear()
        self._textureCatalog = None
        self.previewError.clear()
        self._textureSelectionChanged()
        self.ui.modelRenderer.reloadScene()
