"""Undo/redo for KotOR Game Instance Table records in the editors."""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from enum import Enum
from typing import Callable, Iterable
from uuid import uuid4

from PyQt5.QtGui import QKeySequence
from PyQt5.QtWidgets import QAction, QUndoCommand, QUndoStack

from pykotor.resource.generics.git import GIT, GITInstance


_COLLECTIONS = (
    "cameras", "creatures", "doors", "encounters", "placeables",
    "sounds", "stores", "triggers", "waypoints",
)


def _values(instance):
    return {key: value for key, value in vars(instance).items() if not key.startswith("_gff_")}


def _comparable(value):
    """Compare model values, not object addresses or retained source documents."""
    if isinstance(value, (str, bytes, int, float, bool, Enum, type(None))):
        return value
    if isinstance(value, dict):
        return tuple((key, _comparable(item)) for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return tuple(_comparable(item) for item in value)
    return type(value), _comparable(_values(value))


def clone_instance(instance: GITInstance) -> GITInstance:
    """Duplicate a record's contents without sharing its source occurrence ID."""
    result = deepcopy(instance)
    if hasattr(result, "_gff_key"):
        result._gff_key = uuid4().hex
        result._gff_record._source_key = result._gff_key
    return result


class _InstanceEdit(QUndoCommand):
    def __init__(self, text, before, after, before_lists, after_lists, refresh):
        super().__init__(text)
        self.before, self.after = before, after
        self.before_lists, self.after_lists = before_lists, after_lists
        self.refresh = refresh
        # QUndoStack.push() calls redo. The live gesture has already been applied.
        self._first_redo = True

    def _restore(self, records, collections):
        for items, contents in collections:
            items[:] = contents
        for instance, values in records:
            for key in tuple(vars(instance)):
                if not key.startswith("_gff_"):
                    delattr(instance, key)
            vars(instance).update(deepcopy(values))
        self.refresh()

    def undo(self):
        self._restore(self.before, self.before_lists)

    def redo(self):
        if self._first_redo:
            self._first_redo = False
            self.refresh()
        else:
            self._restore(self.after, self.after_lists)


class InstanceEditHistory(QUndoStack):
    """Capture only affected records; list snapshots retain actual occurrences.

    Each gesture starts on its first mutation and ends on release/focus loss.
    File/resource installation is deliberately outside this document history.
    """
    def __init__(self, parent, refresh: Callable[[], None]):
        super().__init__(parent)
        self.git: GIT | None = None
        self.refresh = refresh
        self._pending = None

    def reset(self, git: GIT | None):
        self._pending = None
        self.clear()
        self.git = git

    def add_actions(self, menu):
        undo = QAction("Undo", self.parent())
        redo = QAction("Redo", self.parent())
        undo.setShortcuts(QKeySequence.Undo)
        redo.setShortcuts(QKeySequence.Redo)
        undo.setEnabled(False)
        redo.setEnabled(False)
        undo.triggered.connect(self.undo)
        redo.triggered.connect(self.redo)
        self.canUndoChanged.connect(undo.setEnabled)
        self.canRedoChanged.connect(redo.setEnabled)
        self.undoTextChanged.connect(lambda text: undo.setText("Undo " + text if text else "Undo"))
        self.redoTextChanged.connect(lambda text: redo.setText("Redo " + text if text else "Redo"))
        menu.addAction(undo)
        menu.addAction(redo)

    def begin(self, text: str, instances: Iterable[GITInstance] = (), *, membership: bool = False):
        if self.git is None:
            raise RuntimeError("No GIT document is open.")
        if self._pending is not None:
            return
        records = [(obj, deepcopy(_values(obj))) for obj in dict.fromkeys(instances)]
        collections = [(getattr(self.git, key), list(getattr(self.git, key))) for key in _COLLECTIONS] if membership else []
        self._pending = text, records, collections

    def finish(self):
        if self._pending is None:
            return
        text, before, before_lists = self._pending
        self._pending = None
        after_lists = [(items, list(items)) for items, _ in before_lists]
        objects = [instance for instance, _ in before]
        old_ids = {id(obj) for _, entries in before_lists for obj in entries}
        for _, entries in after_lists:
            objects.extend(obj for obj in entries if id(obj) not in old_ids and obj not in objects)
        after = [(obj, deepcopy(_values(obj))) for obj in objects]
        membership_changed = any(
            [id(obj) for obj in a] != [id(obj) for obj in b]
            for (_, a), (_, b) in zip(before_lists, after_lists)
        )
        values_changed = len(before) != len(after) or any(
            _comparable(a) != _comparable(b)
            for (_, a), (_, b) in zip(before, after)
        )
        if membership_changed or values_changed:
            self.push(_InstanceEdit(text, before, after, before_lists, after_lists, self.refresh))

    def cancel(self):
        if self._pending is None:
            return
        _, before, collections = self._pending
        self._pending = None
        for items, contents in collections:
            items[:] = contents
        for instance, values in before:
            for key in tuple(vars(instance)):
                if not key.startswith("_gff_"):
                    delattr(instance, key)
            vars(instance).update(deepcopy(values))
        self.refresh()

    @contextmanager
    def edit(self, text: str, instances: Iterable[GITInstance] = (), *, membership: bool = False):
        self.finish()
        self.begin(text, instances, membership=membership)
        try:
            yield
        except BaseException:
            self.cancel()
            raise
        else:
            self.finish()

    def undo(self):
        self.finish()
        super().undo()

    def redo(self):
        self.finish()
        super().redo()
