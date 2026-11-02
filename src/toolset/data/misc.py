from __future__ import annotations

from typing import Optional, Set, Tuple

from PyQt5.QtCore import Qt

Bind = Tuple[Optional[Set[int]], Optional[Set[int]]]


class ControlItem:
    def __init__(self, bind: Bind):
        self.keys = None if bind[0] is None else set(bind[0])
        self.mouse = None if bind[1] is None else set(bind[1])

    def satisfied(self, buttons: set[int], keys: set[int], *, exactKeys=True) -> bool:
        mouse_matches = self.mouse is None or self.mouse == buttons
        if self.keys is None:
            keys_match = True
        elif exactKeys or not self.keys:
            keys_match = self.keys == keys
        else:
            keys_match = self.keys.issubset(keys)
        return mouse_matches and keys_match



def update_modifiers(keys: set[int], modifiers) -> None:
    """Mouse events carry modifier state even when the key was pressed elsewhere."""
    for flag, key in (
        (Qt.ShiftModifier, Qt.Key_Shift), (Qt.ControlModifier, Qt.Key_Control),
        (Qt.AltModifier, Qt.Key_Alt), (Qt.MetaModifier, Qt.Key_Meta),
    ):
        if modifiers & flag:
            keys.add(key)
        else:
            keys.discard(key)
