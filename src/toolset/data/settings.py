from copy import deepcopy
from typing import Any

from PyQt5.QtCore import QObject, QSettings, pyqtSignal


class _SettingsSignals(QObject):
    changed = pyqtSignal()


settings_signals = _SettingsSignals()


class Settings:
    def __init__(self, scope: str):
        self.settings = QSettings("HolocronToolset", scope)

    def default(self, name):
        return deepcopy(getattr(type(self), name).fget.default)

    @staticmethod
    def _addSetting(name: str, default: Any):
        def read(this):
            return this.settings.value(name, deepcopy(default), type(default))
        read.default = default
        return property(read, lambda this, value: this.settings.setValue(name, value))
