from __future__ import annotations

import io

from pathlib import Path
from typing import TYPE_CHECKING

from PIL import Image, ImageOps
from PyQt5.QtCore import QSize, Qt, QTimer
from PyQt5.QtGui import QImage, QPixmap, QTransform
from PyQt5.QtWidgets import QComboBox, QFileDialog, QGraphicsScene, QLabel, QPushButton, QHBoxLayout, QMessageBox, QSizePolicy

from pykotor.extract.capsule import Capsule
from pykotor.resource.formats.tpc import TPC, TPCTextureFormat, read_tpc, write_tpc
from pykotor.resource.formats.txi import TXI, read_txi, write_txi
from pykotor.resource.type import ResourceType
from pykotor.tools.misc import is_capsule_file
from pykotor.tools.path import CaseAwarePath
from toolset.gui.editor import Editor

if TYPE_CHECKING:
    import os
    from pykotor.extract.installation import Installation
    from PyQt5.QtWidgets import QWidget


class TPCEditor(Editor):
    def __init__(self, parent: QWidget | None, installation: Installation | None = None):
        supported = [ResourceType.TPC, ResourceType.TGA, ResourceType.JPG, ResourceType.PNG, ResourceType.BMP]
        super().__init__(parent, "Texture Viewer", "none", supported, supported, installation)
        from toolset.uic.editors.tpc import Ui_MainWindow
        self.ui = Ui_MainWindow()
        self.ui.setupUi(self)
        self._textureScene = QGraphicsScene(self.ui.textureImage)
        self.ui.textureImage.setScene(self._textureScene)
        self._setupMenus()
        self._tpc = TPC()
        self._image_index = 0
        self._mipmap_index = 0
        self._txi_source = "embedded"
        self._txi_dirty = False
        self._external_dirty = False
        self._setupSignals()
        self.new()
        self.resize(self.size().boundedTo(self.screen().availableGeometry().size() - QSize(40, 80)))

    def _setupSignals(self):
        # Keep the original viewer; add explicit selection rather than flattening
        # a compound texture when previewing or exporting it.
        selection = QHBoxLayout()
        selection.addWidget(QLabel("Image", self))
        self.imageSelect = QComboBox(self)
        selection.addWidget(self.imageSelect)
        selection.addWidget(QLabel("Mip", self))
        self.mipmapSelect = QComboBox(self)
        selection.addWidget(self.mipmapSelect)
        self.zoomSelect = QComboBox(self)
        self.zoomSelect.addItem("Fit to window", True)
        self.zoomSelect.addItem("100%", False)
        selection.addWidget(self.zoomSelect)
        self.zoomSelect.currentIndexChanged.connect(self._updateImageScale)
        self.ui.verticalLayout.insertLayout(0, selection)
        formats = QHBoxLayout()
        self.formatStatus = QLabel(self)
        self.formatSelect = QComboBox(self)
        self.convertButton = QPushButton("Convert whole texture", self)
        self.formatSelect.setToolTip("Converts the whole texture. Compression is lossy. RGB, Greyscale, DXT1 and PVRTC4_RGB remove alpha. PVRTC targets K1 and requires power-of-two dimensions. Other TPC formats end rectangular mip chains when the shorter side reaches 1 pixel.")
        formats.addWidget(self.formatStatus)
        formats.addWidget(self.formatSelect)
        formats.addWidget(self.convertButton)
        self.ui.verticalLayout.insertLayout(1, formats)
        documents = QHBoxLayout()
        self.txiSourceSelect = QComboBox(self)
        documents.addWidget(self.txiSourceSelect)
        self.saveTxiButton = QPushButton("Save TXI…", self)
        self.embedTxiButton = QPushButton("Embed external TXI", self)
        documents.addWidget(self.saveTxiButton)
        documents.addWidget(self.embedTxiButton)
        self.ui.verticalLayout.insertLayout(3, documents)
        self.imageSelect.currentIndexChanged.connect(self._selectImage)
        self.mipmapSelect.currentIndexChanged.connect(self._selectMipmap)
        self.txiSourceSelect.currentIndexChanged.connect(self._selectTxi)
        self.ui.txiEdit.textChanged.connect(self._markTxiChanged)
        self.saveTxiButton.clicked.connect(self._saveTxi)
        self.embedTxiButton.clicked.connect(self._embedExternalTxi)
        self.formatSelect.currentIndexChanged.connect(self._updateConversionState)
        self.convertButton.clicked.connect(self._convertTexture)
        self.formatStatus.setWordWrap(True)
        for combo in (self.imageSelect, self.mipmapSelect, self.zoomSelect, self.formatSelect, self.txiSourceSelect):
            combo.setMinimumContentsLength(1)
            combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
            combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def _updateConversionState(self):
        self.convertButton.setEnabled(bool(self._tpc.images()[0].mipmaps) and
                                      self.formatSelect.currentData() != self._tpc.format())

    def _convertTexture(self):
        from toolset.gui.dialogs.asyncloader import AsyncLoader
        from toolset.utils.texture import convert_texture

        try:
            self._flushTxi()
        except (ValueError, UnicodeError) as error:
            QMessageBox.warning(self, "Invalid TXI edit", str(error))
            return
        source = self._tpc
        target = TPCTextureFormat(self.formatSelect.currentData())
        loader = AsyncLoader(self, "Converting texture", lambda report: convert_texture(source, target, report),
                             "Unable to convert texture", with_progress=True)
        if loader.exec_():
            mipmap = self._mipmap_index
            self._tpc = loader.value
            self._refreshImages()
            self.mipmapSelect.setCurrentIndex(min(mipmap, self.mipmapSelect.count() - 1))

    def load(self, filepath: os.PathLike | str, resref: str, restype: ResourceType, data: bytes):
        super().load(filepath, resref, restype, data)
        if restype in (ResourceType.TPC, ResourceType.TGA):
            game = self._installation.game() if self._installation is not None else 1
            self._tpc = read_tpc(data, file_format=restype, game=game)
            path = Path(filepath)
            if path.suffix.lower() in (".tpc", ".tga"):
                sidecar = CaseAwarePath.get_case_sensitive_path(path.with_suffix(".txi"))
                if sidecar.is_file():
                    self._tpc.external_txi = read_txi(sidecar)
                    self._tpc.external_txi_source = str(sidecar)
            elif self._installation is not None:
                capsules = [Capsule(path)] if is_capsule_file(path) else []
                external = self._installation.resource(resref, ResourceType.TXI, capsules=capsules)
                if external is not None:
                    self._tpc.external_txi = read_txi(external.data)
                    self._tpc.external_txi_source = str(external.filepath)
        else:
            with Image.open(io.BytesIO(data)) as source:
                image = ImageOps.flip(source.convert("RGBA"))
                self._tpc = TPC()
                self._tpc.set_single(image.width, image.height, image.tobytes(), TPCTextureFormat.RGBA)
        self._refreshTexture()

    def new(self):
        super().new()
        self._tpc = TPC()
        self._tpc.set_single(256, 256, bytes(256 * 256 * 4), TPCTextureFormat.RGBA)
        self._refreshTexture()

    def _refreshTexture(self):
        self._image_index = self._mipmap_index = 0
        self._txi_dirty = self._external_dirty = False
        self.txiSourceSelect.blockSignals(True)
        self.txiSourceSelect.clear()
        self.txiSourceSelect.addItem("Embedded TXI", "embedded")
        if self._tpc.external_txi is not None:
            self.txiSourceSelect.addItem("External TXI (effective)", "external")
            self.txiSourceSelect.setCurrentIndex(1)
        self._txi_source = self.txiSourceSelect.currentData()
        self.txiSourceSelect.blockSignals(False)
        self.embedTxiButton.setEnabled(self._tpc.external_txi is not None)
        self._showTxi()
        self._refreshImages()

    def _refreshImages(self):
        images = self._tpc.images()
        self.formatStatus.setText(f"Stored: {self._tpc.format().name}")
        self.formatSelect.blockSignals(True)
        self.formatSelect.clear()
        settings = self._tpc.effective_txi.settings(game=self._tpc.header.game)
        formats = (TPCTextureFormat.Greyscale, TPCTextureFormat.RGB, TPCTextureFormat.RGBA,
                   TPCTextureFormat.DXT1, TPCTextureFormat.DXT5)
        if images[0].kind == "image" and settings.controller is None and not settings.texture["isbumpmap"]:
            formats += (TPCTextureFormat.PVRTC4_RGB, TPCTextureFormat.PVRTC4_RGBA)
        for texture_format in dict.fromkeys((self._tpc.format(), *formats)):
            if images[0].kind != "frame" or texture_format in (self._tpc.format(), TPCTextureFormat.DXT1, TPCTextureFormat.DXT5):
                name = texture_format.name + " (K1)" if texture_format in (TPCTextureFormat.PVRTC4_RGB, TPCTextureFormat.PVRTC4_RGBA) else texture_format.name
                self.formatSelect.addItem(name, texture_format)
        self.formatSelect.setCurrentIndex(self.formatSelect.findData(self._tpc.format()))
        self.formatSelect.blockSignals(False)
        self._updateConversionState()
        self.imageSelect.blockSignals(True)
        self.imageSelect.clear()
        faces = ("+X", "-X", "+Y", "-Y", "+Z", "-Z")
        for image in images:
            label = "Face " + faces[image.index] if image.kind == "face" else f"{image.kind.title()} {image.index}"
            self.imageSelect.addItem(label, image.index)
        self.imageSelect.setCurrentIndex(min(self._image_index, len(images) - 1))
        self.imageSelect.blockSignals(False)
        self._selectImage(self.imageSelect.currentIndex())

    def _selectImage(self, index: int):
        if index < 0:
            return
        self._image_index = index
        self.mipmapSelect.blockSignals(True)
        self.mipmapSelect.clear()
        self.mipmapSelect.addItems([str(i) for i in range(self._tpc.mipmap_count(index))])
        self.mipmapSelect.blockSignals(False)
        if self._tpc.mipmap_count(index):
            self._selectMipmap(0)
        else:
            self._textureScene.clear()
            self._textureScene.addText("The header declares no mipmaps to preview.")
            self._updateImageScale()

    def _selectMipmap(self, index: int):
        if index < 0:
            return
        self._mipmap_index = index
        try:
            width, height, pixels = self._tpc.convert(TPCTextureFormat.RGBA, index, image=self._image_index)
            image = QImage(bytes(pixels), width, height, width * 4, QImage.Format_RGBA8888).copy()
            pixmap = QPixmap.fromImage(image).transformed(QTransform().scale(1, -1))
            self._textureScene.clear()
            self._textureScene.addPixmap(pixmap)
            self._updateImageScale()
        except ValueError as error:
            # The source remains loaded and preservable; do not fabricate pixels
            # for an out-of-range native image view.
            self._textureScene.clear()
            self._textureScene.addText(str(error))
            self._updateImageScale()

    def _updateImageScale(self):
        view = self.ui.textureImage
        fit = self.zoomSelect.currentData()
        scrollbars = Qt.ScrollBarAlwaysOff if fit else Qt.ScrollBarAsNeeded
        view.setHorizontalScrollBarPolicy(scrollbars)
        view.setVerticalScrollBarPolicy(scrollbars)
        view.setSceneRect(self._textureScene.itemsBoundingRect())
        view.resetTransform()
        if fit:
            view.fitInView(view.sceneRect(), Qt.KeepAspectRatio)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        QTimer.singleShot(0, self._updateImageScale)

    def _document(self) -> TXI:
        return self._tpc.external_txi if self._txi_source == "external" else self._tpc.embedded_txi

    def _showTxi(self):
        document = self._document()
        self.ui.txiEdit.blockSignals(True)
        self.ui.txiEdit.setPlainText(document.text.rstrip("\0"))
        binary = document.has_binary_blocks
        self.ui.txiEdit.setReadOnly(binary)
        self.ui.txiEdit.setToolTip("Binary list blocks are preserved. Edit them through the structured TXI API." if binary else "")
        self.ui.txiEdit.blockSignals(False)
        self._txi_dirty = False

    def _markTxiChanged(self):
        self._txi_dirty = True

    def _flushTxi(self):
        if not self._txi_dirty:
            return
        # Validate a candidate before replacing either source document.
        candidate = TXI(bytes(self._document()))
        candidate.update_text(self.ui.txiEdit.toPlainText())
        candidate.settings(game=self._tpc.header.game)
        if self._txi_source == "external":
            self._tpc.external_txi = candidate
            self._external_dirty = True
        else:
            self._tpc.embedded_txi = candidate
        self._txi_dirty = False

    def _selectTxi(self, index: int):
        if index < 0:
            return
        try:
            self._flushTxi()
        except (ValueError, UnicodeError) as error:
            QMessageBox.warning(self, "Invalid TXI edit", str(error))
            self.txiSourceSelect.blockSignals(True)
            self.txiSourceSelect.setCurrentIndex(1 if self._txi_source == "external" else 0)
            self.txiSourceSelect.blockSignals(False)
            return
        self._txi_source = self.txiSourceSelect.itemData(index)
        self._showTxi()

    def _saveTxi(self):
        try:
            self._flushTxi()
            path, _ = QFileDialog.getSaveFileName(self, "Save TXI document", "", "TXI (*.txi)")
            if not path:
                return
            write_txi(self._document(), path)
            if self._txi_source == "external":
                self._tpc.external_txi_source = path
                self._external_dirty = False
            self._refreshImages()
        except (OSError, ValueError, UnicodeError) as error:
            QMessageBox.warning(self, "Unable to save TXI", str(error))

    def _embedExternalTxi(self):
        try:
            self._flushTxi()
            if self._tpc.external_txi is not None:
                self._tpc.embedded_txi = TXI(bytes(self._tpc.external_txi))
                self.txiSourceSelect.setCurrentIndex(0)
        except (ValueError, UnicodeError) as error:
            QMessageBox.warning(self, "Invalid TXI edit", str(error))

    def build(self) -> tuple[bytes, bytes]:
        self._flushTxi()
        if self._external_dirty:
            raise ValueError("External TXI has unsaved edits. Use Save TXI before saving the texture.")
        if self._restype in (ResourceType.TPC, ResourceType.TGA):
            data = bytearray()
            if self._restype == ResourceType.TPC:
                write_tpc(self._tpc, data, ResourceType.TPC)
            else:
                write_tpc(self._tpc, data, ResourceType.TGA, image=self._image_index, mipmap=self._mipmap_index)
            return bytes(data), b""
        if self._restype == ResourceType.JPG:
            return self.extract_tpc_jpeg_bytes(), b""
        return self.extract_png_bmp_bytes(), b""

    def extract_tpc_jpeg_bytes(self):
        width, height, pixels = self._tpc.convert(TPCTextureFormat.RGB, self._mipmap_index, image=self._image_index)
        image = ImageOps.flip(Image.frombytes("RGB", (width, height), bytes(pixels)))
        stream = io.BytesIO()
        image.save(stream, "JPEG", quality=80)
        return stream.getvalue()

    def extract_png_bmp_bytes(self):
        width, height, pixels = self._tpc.convert(TPCTextureFormat.RGBA, self._mipmap_index, image=self._image_index)
        image = ImageOps.flip(Image.frombytes("RGBA", (width, height), bytes(pixels)))
        stream = io.BytesIO()
        image.save(stream, "PNG" if self._restype == ResourceType.PNG else "BMP")
        return stream.getvalue()
