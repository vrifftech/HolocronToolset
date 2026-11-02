from __future__ import annotations

from typing import TYPE_CHECKING

from pykotor.common.misc import Color
from PyQt5.QtWidgets import QWidget

if TYPE_CHECKING:
    from toolset.gui.widgets.edit.color import ColorEdit
    from toolset.gui.widgets.set_bind import SetBindWidget


class SettingsWidget(QWidget):
    def __init__(self, parent: QWidget):
        super().__init__(parent)

        self.binds: list = []
        self.colours: list = []
        self.settings = None

    def _registerBind(self, widget: SetBindWidget, bindName: str, defaults=False):
        widget.setBind(self.settings.default(bindName) if defaults else getattr(self.settings, bindName))
        if (widget, bindName) not in self.binds:
            self.binds.append((widget, bindName))

    def _registerColour(self, widget: ColorEdit, colourName: str, defaults=False):
        value = self.settings.default(colourName) if defaults else getattr(self.settings, colourName)
        widget.setColor(Color.from_rgba_integer(value))
        if (widget, colourName) not in self.colours:
            self.colours.append((widget, colourName))
