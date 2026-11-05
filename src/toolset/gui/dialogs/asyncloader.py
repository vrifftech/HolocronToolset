from __future__ import annotations

from concurrent.futures import CancelledError
import logging
from typing import Any, Callable

from PyQt5 import QtCore
from PyQt5.QtCore import QThread
from PyQt5.QtWidgets import QDialog, QLabel, QMessageBox, QProgressBar, QPushButton, QVBoxLayout, QWidget


class AsyncWorker(QThread):
    progressed = QtCore.pyqtSignal(object, str)

    def __init__(self, parent: QWidget, task: Callable, with_progress: bool = False):
        super().__init__(parent)
        self._task = task
        self._with_progress = with_progress
        self.value: Any = None
        self.error: Exception | None = None
        self.cancelled = False

    def report(self, value: int | str, operation: str):
        if self.isInterruptionRequested():
            raise CancelledError()
        self.progressed.emit(value, operation)

    def run(self):
        try:
            self.report(0, "set_maximum")
            self.value = self._task(self.report) if self._with_progress else self._task()
            self.cancelled = self.isInterruptionRequested()
        except CancelledError:
            self.cancelled = True
        except Exception as error:
            logging.getLogger(__name__).exception("Background task failed: %r", self._task)
            self.error = error


class AsyncLoader(QDialog):
    """Run filesystem/CPU work off the GUI thread and keep its worker alive to completion."""

    def __init__(self, parent: QWidget | None, title: str, task: Callable,
                 errorTitle: str | None = None, *, with_progress: bool = False):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setWindowFlag(QtCore.Qt.WindowContextHelpButtonHint, False)
        self.setMinimumWidth(360)
        self.setLayout(QVBoxLayout())
        self._infoText = QLabel(self)
        self._infoText.setWordWrap(True)
        self._progressBar = QProgressBar(self)
        self._progressBar.setRange(0, 0)
        self._cancelButton = QPushButton("Cancel", self)
        self.layout().addWidget(self._infoText)
        self.layout().addWidget(self._progressBar)
        self.layout().addWidget(self._cancelButton)
        self._cancelButton.clicked.connect(self.reject)
        self.value: Any = None
        self.error: Exception | None = None
        self.cancelled = False
        self.errorTitle = errorTitle
        self._started = False
        self._worker = AsyncWorker(self, task, with_progress)
        self._worker.progressed.connect(self._updateProgress, QtCore.Qt.QueuedConnection)
        self._worker.finished.connect(self._finished, QtCore.Qt.QueuedConnection)

    def exec_(self):
        if not self._started:
            self._started = True
            # Start after the dialog enters its event loop, not in its constructor.
            QtCore.QTimer.singleShot(0, self._startWorker)
        return super().exec_()

    def _startWorker(self):
        if self.cancelled:
            super().reject()
        else:
            self._worker.start()

    def reject(self):
        self.cancelled = True
        if self._started and not self._worker.isFinished():
            self._worker.requestInterruption()
            self.cancelled = True
            self._cancelButton.setEnabled(False)
            self._infoText.setText("Canceling after the current operation completes…")
            return
        super().reject()

    def closeEvent(self, event):
        if self._started and not self._worker.isFinished():
            self.reject()
            event.ignore()
        else:
            super().closeEvent(event)

    def _updateProgress(self, value, operation: str):
        if self.cancelled:
            return
        if operation == "set_maximum":
            self._progressBar.setRange(0, max(0, int(value)))
            self._progressBar.setValue(0)
        elif operation == "set_value":
            self._progressBar.setValue(int(value))
        elif operation == "increment":
            self._progressBar.setValue(self._progressBar.value() + int(value))
        elif operation in ("update_maintask_text", "update_subtask_text"):
            self._infoText.setText(str(value))

    def _finished(self):
        # finished is emitted after run() exits; accepting cannot destroy a running QThread.
        self.value = self._worker.value
        self.error = self._worker.error
        self.cancelled = self.cancelled or self._worker.cancelled
        if self.error is not None:
            if self.errorTitle:
                QMessageBox.critical(self, self.errorTitle, str(self.error))
            super().reject()
        elif self.cancelled:
            super().reject()
        else:
            if self._progressBar.maximum() > 0:
                self._progressBar.setValue(self._progressBar.maximum())
            super().accept()


class AsyncBatchLoader(AsyncLoader):
    """Queue side-effect tasks before exec_; cancel between operations, never during a write."""

    def __init__(self, parent: QWidget | None, title: str, tasks: list[Callable],
                 errorTitle: str | None = None, *, cascade: bool = False):
        self._tasks = list(tasks)
        self._cascade = cascade
        self.errors: list[Exception] = []
        super().__init__(parent, title, self._runTasks, errorTitle, with_progress=True)

    def addTask(self, task: Callable):
        if self._started:
            raise RuntimeError("Queue all tasks before starting the loader.")
        self._tasks.append(task)

    def _runTasks(self, report):
        report(len(self._tasks), "set_maximum")
        for index, task in enumerate(self._tasks):
            report(f"Operation {index + 1} of {len(self._tasks)}", "update_maintask_text")
            try:
                task()
            except CancelledError:
                raise
            except Exception as error:
                logging.getLogger(__name__).exception("Batch task failed: %r", task)
                self.errors.append(error)
                if self._cascade:
                    break
            report(index + 1, "set_value")
        if self.errors:
            raise RuntimeError("\n".join(str(error) for error in self.errors))
