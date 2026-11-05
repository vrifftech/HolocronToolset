"""Application logs live outside the executable, app bundle, and working directory."""
from __future__ import annotations

import atexit
import faulthandler
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import sys
import threading

from toolset.config import PROGRAM_VERSION_LABEL

_crash_stream = None


def log_directory() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local"
        return Path(base) / "HolocronToolset" / "logs"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Logs" / "HolocronToolset"
    base = os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state"
    return Path(base) / "holocrontoolset" / "logs"


class _LogStream:
    """Copy printed output to the current rotating file without re-entering logging."""

    def __init__(self, original, handler: RotatingFileHandler):
        self.original = original
        self.handler = handler

    @property
    def encoding(self):
        return "utf-8" if self.original is None else self.original.encoding

    def write(self, message: str):
        if self.original is not None:
            self.original.write(message)
        self.handler.acquire()
        try:
            record = logging.LogRecord("stdio", logging.INFO, __file__, 0, message, (), None)
            if self.handler.shouldRollover(record):
                self.handler.doRollover()
            self.handler.stream.write(message)
            self.handler.flush()
        finally:
            self.handler.release()
        return len(message)

    def flush(self):
        if self.original is not None:
            self.original.flush()
        self.handler.flush()

    def isatty(self):
        return self.original is not None and self.original.isatty()

    def __getattr__(self, name):
        return getattr(self.original, name)


def on_app_crash(etype, error, traceback):
    logging.getLogger("toolset.crash").critical("Unhandled exception", exc_info=(etype, error, traceback))


def _thread_crash(args):
    if args.exc_type is not SystemExit:
        logging.getLogger("toolset.crash").critical(
            "Unhandled thread exception", exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )


def _unraisable_error(args):
    logging.getLogger("toolset.crash").error(
        "Unraisable exception in %r: %s", args.object, args.err_msg,
        exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
    )


def configure_logging() -> Path:
    global _crash_stream
    directory = log_directory()
    directory.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s [%(process)d/%(threadName)s]: %(message)s")
    handler = RotatingFileHandler(directory / "HolocronToolset.log", maxBytes=5 * 1024 * 1024,
                                  backupCount=3, encoding="utf-8", errors="backslashreplace")
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(formatter)
    handlers = [handler]
    if sys.stderr is not None:
        console = logging.StreamHandler(sys.stderr)
        console.setLevel(logging.INFO)
        console.setFormatter(formatter)
        handlers.append(console)
    # Own the application configuration even if an import has logged already.
    logging.basicConfig(level=logging.DEBUG, handlers=handlers, force=True)
    root = logging.getLogger()

    _crash_stream = (directory / "crash.log").open("a", encoding="utf-8", errors="backslashreplace")
    crash_handler = logging.StreamHandler(_crash_stream)
    crash_handler.setFormatter(formatter)
    crash_logger = logging.getLogger("toolset.crash")
    crash_logger.setLevel(logging.ERROR)
    crash_logger.addHandler(crash_handler)
    faulthandler.enable(file=_crash_stream, all_threads=True)
    sys.excepthook = on_app_crash
    threading.excepthook = _thread_crash
    sys.unraisablehook = _unraisable_error
    logging.captureWarnings(True)

    sys.stdout = _LogStream(sys.stdout, handler)
    sys.stderr = _LogStream(sys.stderr, handler)
    atexit.register(sys.stdout.flush)
    atexit.register(sys.stderr.flush)
    root.info("HolocronToolset %s starting; Python %s; platform=%s; frozen=%s; logs=%s",
              PROGRAM_VERSION_LABEL, sys.version.split()[0], sys.platform, bool(getattr(sys, "frozen", False)), directory)
    return directory


def install_qt_message_handler():
    from PyQt5.QtCore import QtCriticalMsg, QtDebugMsg, QtFatalMsg, QtInfoMsg, QtWarningMsg, qInstallMessageHandler

    levels = {QtDebugMsg: logging.DEBUG, QtInfoMsg: logging.INFO, QtWarningMsg: logging.WARNING,
              QtCriticalMsg: logging.ERROR, QtFatalMsg: logging.CRITICAL}

    def qt_message(kind, context, message):
        logging.getLogger(f"qt.{context.category}").log(levels[kind], "%s", message)

    qInstallMessageHandler(qt_message)
