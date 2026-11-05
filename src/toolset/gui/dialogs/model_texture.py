from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QLabel, QLineEdit, QPlainTextEdit,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout,
)


class ModelTextureDialog(QDialog):
    def __init__(self, parent, catalog, current: str, suggestions: dict[str, set[str]]):
        super().__init__(parent)
        self.setWindowTitle("Assign preview texture")
        self.resize(800, 560)
        layout = QVBoxLayout(self)
        self.search = QLineEdit(self)
        self.search.setPlaceholderText("Filter textures, sources, or associations")
        layout.addWidget(self.search)
        self.relatedOnly = QCheckBox("Show only textures associated with this model", self)
        layout.addWidget(self.relatedOnly)
        self.textures = QTreeWidget(self)
        self.textures.setHeaderLabels(["Texture", "Association", "Source"])
        self.textures.setRootIsDecorated(False)
        self.textures.setUniformRowHeights(True)
        layout.addWidget(self.textures, 1)
        note = QLabel("Associations describe game data; choosing a texture applies it to this mesh's preview.", self)
        note.setWordWrap(True)
        layout.addWidget(note)
        self.details = QPlainTextEdit(self)
        self.details.setReadOnly(True)
        self.details.setMaximumHeight(120)
        layout.addWidget(self.details)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, self)
        self.buttons.button(QDialogButtonBox.Ok).setText("Use texture")
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(False)
        layout.addWidget(self.buttons)
        self._rows = []
        for name in sorted(catalog.textures, key=lambda name: (name not in suggestions, name)):
            resource = catalog.textures[name]
            reasons = sorted(suggestions.get(name, ()))
            association = reasons[0].split(":", 1)[0] if reasons else ""
            if len(reasons) > 1:
                association += f" (+{len(reasons) - 1})"
            source = str(resource.filepath())
            item = QTreeWidgetItem([resource.resname(), association, source])
            item.setData(0, Qt.UserRole, resource.resname())
            details = "\n".join(reasons)
            item.setToolTip(1, details)
            item.setToolTip(2, source)
            self.textures.addTopLevelItem(item)
            self._rows.append((item, f"{name} {source} {details}".lower(), bool(reasons)))
            if name == current.lower():
                self.textures.setCurrentItem(item)
        self.textures.setColumnWidth(0, 180)
        self.textures.setColumnWidth(1, 240)
        self.search.textChanged.connect(self._filter)
        self.relatedOnly.toggled.connect(self._filter)
        self.textures.itemSelectionChanged.connect(self._selectionChanged)
        self.textures.itemDoubleClicked.connect(lambda _item, _column: self.accept())
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        self.relatedOnly.setChecked(bool(suggestions))
        self._selectionChanged()

    def _filter(self):
        query = self.search.text().lower()
        related = self.relatedOnly.isChecked()
        for item, text, suggested in self._rows:
            item.setHidden(query not in text or related and not suggested)
        self._selectionChanged()

    def _selectionChanged(self):
        item = self.textures.currentItem()
        selected = item is not None and not item.isHidden()
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(selected)
        self.details.setPlainText(item.toolTip(1) if selected else "")

    def texture(self) -> str:
        return self.textures.currentItem().data(0, Qt.UserRole)
