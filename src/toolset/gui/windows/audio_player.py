from __future__ import annotations

import time
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING

from pykotor.common.stream import BinaryReader
from pykotor.extract.file import ResourceIdentifier
from pykotor.tools import sound
from PyQt5 import QtCore
from PyQt5.QtCore import QUrl
from PyQt5.QtMultimedia import QMediaContent, QMediaPlayer
from PyQt5.QtWidgets import QFileDialog, QMainWindow, QWidget

if TYPE_CHECKING:
    import os

    from pykotor.resource.type import ResourceType
    from PyQt5.QtGui import QCloseEvent


class AudioPlayer(QMainWindow):
    def __init__(self, parent: QWidget):
        super().__init__(parent)

        from toolset.uic.windows.audio_player import Ui_MainWindow

        self.ui = Ui_MainWindow()
        self.ui.setupUi(self)

        self.player: QMediaPlayer = QMediaPlayer(self)
        self._mediaDirectory = TemporaryDirectory(prefix="HolocronToolset-audio-")

        self.ui.actionOpen.triggered.connect(self.open)
        self.ui.stopButton.clicked.connect(self.player.stop)
        self.ui.playButton.clicked.connect(self.player.play)
        self.ui.pauseButton.clicked.connect(self.player.pause)
        self.ui.timeSlider.sliderReleased.connect(self.changePosition)
        self.player.durationChanged.connect(self.durationChanged)
        self.player.positionChanged.connect(self.positionChanged)

    def load(self, filepath: os.PathLike | str, resname: str, restype: ResourceType, data: bytes):
        data = sound.deobfuscate_audio(data)

        self.player.stop()
        self.player.setMedia(QMediaContent())
        extension = "wav" if data.startswith(b"RIFF") else "mp3"
        media_path = Path(self._mediaDirectory.name) / f"preview.{extension}"
        media_path.write_bytes(data)
        self.setWindowTitle(f"{resname}.{restype.extension} - Audio Player")
        self.player.setMedia(QMediaContent(QUrl.fromLocalFile(str(media_path))))
        QtCore.QTimer.singleShot(0, self.player.play)

    def open(self):
        filepath: str = QFileDialog.getOpenFileName(self, "Select an audio file")[0]
        if filepath != "":
            resname, restype = ResourceIdentifier.from_path(filepath).validate()
            data: bytes = BinaryReader.load_file(filepath)
            self.load(filepath, resname, restype, data)

    def durationChanged(self, duration: int):
        totalTime: str = time.strftime("%H:%M:%S", time.gmtime(duration // 1000))
        self.ui.totalTimeLabel.setText(totalTime)
        self.ui.timeSlider.setMaximum(duration)

    def positionChanged(self, position: int):
        currentTime: str = time.strftime("%H:%M:%S", time.gmtime(position // 1000))
        self.ui.currentTimeLabel.setText(currentTime)

        # sometimes QMediaPlayer does not accurately calculate the duration of the audio
        if position > self.ui.timeSlider.maximum():
            self.durationChanged(position)

        self.ui.timeSlider.setValue(position)

    def changePosition(self):
        position: int = self.ui.timeSlider.value()
        self.player.setPosition(position)

    def closeEvent(self, e: QCloseEvent | None):
        self.player.stop()
        self.player.setMedia(QMediaContent())
