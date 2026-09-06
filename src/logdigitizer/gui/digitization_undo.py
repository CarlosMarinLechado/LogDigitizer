"""Undo/redo commands for persisted digitization edits.

The command stores serialized before/after snapshots of one ``DigitizedCurve``.
This keeps the undo layer independent from mutable domain-object identity and
preserves automatic raw samples and processing metadata exactly.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Callable

from PySide6.QtGui import QUndoCommand


@dataclass(frozen=True, slots=True)
class EditSelection:
    """Optional saved-trace selection to restore after undo/redo."""

    section_id: str | None = None
    node_index: int | None = None


SnapshotPayload = dict[str, Any] | None
ApplySnapshot = Callable[[str, str, SnapshotPayload, EditSelection], None]


class AppliedDigitizationEditCommand(QUndoCommand):
    """Undo one digitization edit that was already applied before ``push``.

    ``QUndoStack.push()`` immediately calls ``redo()``.  Existing digitization
    editing code applies and validates the operation first, so the first redo is
    deliberately skipped.  Subsequent undo/redo calls restore serialized
    snapshots through the supplied application-layer callback.
    """

    def __init__(
        self,
        *,
        text: str,
        segment_id: str,
        curve_id: str,
        before_payload: SnapshotPayload,
        after_payload: SnapshotPayload,
        before_selection: EditSelection,
        after_selection: EditSelection,
        apply_callback: ApplySnapshot,
    ) -> None:
        super().__init__(str(text))
        self.segment_id = str(segment_id)
        self.curve_id = str(curve_id)
        self._before_payload = deepcopy(before_payload)
        self._after_payload = deepcopy(after_payload)
        self._before_selection = before_selection
        self._after_selection = after_selection
        self._apply_callback = apply_callback
        self._skip_initial_redo = True

    def undo(self) -> None:
        self._apply_callback(
            self.segment_id,
            self.curve_id,
            deepcopy(self._before_payload),
            self._before_selection,
        )

    def redo(self) -> None:
        if self._skip_initial_redo:
            self._skip_initial_redo = False
            return
        self._apply_callback(
            self.segment_id,
            self.curve_id,
            deepcopy(self._after_payload),
            self._after_selection,
        )
