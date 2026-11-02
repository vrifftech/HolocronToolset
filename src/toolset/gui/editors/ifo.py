from __future__ import annotations

import math
from copy import deepcopy
from typing import TYPE_CHECKING

from pykotor.common.misc import ResRef
from pykotor.resource.generics.ifo import IFO, read_ifo, write_ifo
from pykotor.resource.type import ResourceType
from PyQt5.QtCore import QSignalBlocker
from PyQt5.QtWidgets import (
    QDoubleSpinBox, QFormLayout, QLineEdit, QTabWidget, QVBoxLayout, QWidget,
)
from toolset.gui.editor import Editor
from toolset.gui.widgets.edit.locstring import LocalizedStringLineEdit
from toolset.gui.widgets.edit.resref import ResRefComboBox

if TYPE_CHECKING:
    import os

    from toolset.data.installation import HTInstallation


class IFOEditor(Editor):
    def __init__(self, parent: QWidget | None, installation: HTInstallation):
        supported = [ResourceType.IFO, ResourceType.IFO_XML]
        super().__init__(parent, "Module Information Editor", "none", supported, supported, installation)
        self.resize(600, 620)
        self._ifo = IFO()
        body = QWidget(self)
        self.setCentralWidget(body)
        layout = QVBoxLayout(body)
        tabs = QTabWidget(body)
        layout.addWidget(tabs)
        basic = QWidget(tabs)
        form = QFormLayout(basic)
        tabs.addTab(basic, "Module")
        self.nameEdit = LocalizedStringLineEdit(basic)
        self.nameEdit.setInstallation(installation)
        self.descriptionEdit = LocalizedStringLineEdit(basic)
        self.descriptionEdit.setInstallation(installation)
        self.tagEdit = QLineEdit(basic)
        self.voEdit = QLineEdit(basic)
        form.addRow("Name:", self.nameEdit)
        form.addRow("Description:", self.descriptionEdit)
        form.addRow("Tag:", self.tagEdit)
        form.addRow("Voice-over ID:", self.voEdit)
        self.resrefEdits = {}
        for attribute, label in (("area_name", "First area:"), ("resref", "Entry area:")):
            edit = ResRefComboBox(basic)
            edit.setProperty("resourceTypes", "are")
            self.resrefEdits[attribute] = edit
            form.addRow(label, edit)
        self.positionEdits = {}
        for axis in "xyz":
            edit = QDoubleSpinBox(basic)
            edit.setDecimals(6)
            edit.setRange(-3.4028234663852886e38, 3.4028234663852886e38)
            edit.valueChanged.connect(lambda value, axis=axis: setattr(self._ifo.entry_position, axis, value))
            self.positionEdits[axis] = edit
            form.addRow(f"Entry {axis.upper()}:", edit)
        self.directionEdit = QDoubleSpinBox(basic)
        self.directionEdit.setDecimals(6)
        self.directionEdit.setRange(-360.0, 360.0)
        self.directionEdit.valueChanged.connect(lambda value: setattr(self._ifo, "entry_direction", math.radians(value)))
        form.addRow("Entry direction (degrees):", self.directionEdit)
        scripts = QWidget(tabs)
        script_form = QFormLayout(scripts)
        tabs.addTab(scripts, "Scripts")
        for attribute, label in (
            ("on_load", "On Module Load"), ("on_start", "On Module Start"),
            ("on_enter", "On Client Enter"), ("on_leave", "On Client Leave"),
            ("on_heartbeat", "On Heartbeat"), ("on_user_defined", "On User Defined"),
            ("on_acquire_item", "On Acquire Item"), ("on_activate_item", "On Activate Item"),
            ("on_unacquire_item", "On Unacquire Item"), ("on_player_death", "On Player Death"),
            ("on_player_dying", "On Player Dying"), ("on_player_levelup", "On Player Level Up"),
            ("on_player_respawn", "On Player Respawn"), ("on_player_rest", "On Player Rest"),
        ):
            edit = ResRefComboBox(scripts)
            edit.setProperty("resourceTypes", "ncs")
            self.resrefEdits[attribute] = edit
            script_form.addRow(label + ":", edit)
        menu = self.menuBar().addMenu("File")
        for text in ("New", "Open", "Save", "Save As", "Revert"):
            menu.addAction(text)
        menu.addSeparator()
        menu.addAction("Exit")
        self._setupMenus()
        self.new()

    def load(self, filepath: os.PathLike | str, resref: str, restype: ResourceType, data: bytes):
        super().load(filepath, resref, restype, data)
        self._loadIFO(read_ifo(data))

    def _loadIFO(self, ifo: IFO):
        self._ifo = ifo
        self.nameEdit.setLocstring(deepcopy(ifo.mod_name))
        self.descriptionEdit.setLocstring(deepcopy(ifo.description))
        self.tagEdit.setText(ifo.tag)
        self.voEdit.setText(ifo.vo_id)
        for attribute, edit in self.resrefEdits.items():
            edit.setText(str(getattr(ifo, attribute)))
        for axis, edit in self.positionEdits.items():
            blocker = QSignalBlocker(edit)
            edit.setValue(getattr(ifo.entry_position, axis))
            del blocker
        blocker = QSignalBlocker(self.directionEdit)
        self.directionEdit.setValue(math.degrees(ifo.entry_direction))
        del blocker

    def build(self) -> tuple[bytes, bytes]:
        ifo = deepcopy(self._ifo)
        ifo.mod_name = deepcopy(self.nameEdit.locstring())
        ifo.description = deepcopy(self.descriptionEdit.locstring())
        ifo.tag = self.tagEdit.text()
        ifo.vo_id = self.voEdit.text()
        for attribute, edit in self.resrefEdits.items():
            if str(getattr(ifo, attribute)) != edit.text():
                setattr(ifo, attribute, ResRef(edit.text()))
        data = bytearray()
        file_format = ResourceType.GFF_XML if self._restype == ResourceType.IFO_XML else ResourceType.GFF
        write_ifo(ifo, data, self._installation.game(), file_format)
        return bytes(data), b""

    def new(self):
        super().new()
        self._loadIFO(IFO())
