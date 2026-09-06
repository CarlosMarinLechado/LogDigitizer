"""Derived, non-persistent workflow summaries for the project tree."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .calibration import DepthUnit


class WorkflowState(StrEnum):
    """Presentation-oriented completion state calculated from persisted data."""

    NOT_STARTED = "not_started"
    INCOMPLETE = "incomplete"
    COMPLETE = "complete"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class CurveWorkflowSummary:
    """Derived workflow state for one present curve in one segment."""

    curve_id: str
    mnemonic: str
    name: str
    unit: str
    display_color: str
    x_state: WorkflowState
    x_point_count: int
    digitization_state: WorkflowState
    section_count: int
    state: WorkflowState


@dataclass(frozen=True, slots=True)
class SegmentWorkflowSummary:
    """Derived workflow state for one scanned segment.

    These values are intentionally not serialized. Recomputing them from the
    scientific/project data prevents a stale "completed" flag after editing.
    """

    segment_id: str
    state: WorkflowState
    preprocessing_state: WorkflowState
    depth_state: WorkflowState
    depth_point_count: int
    depth_unit: DepthUnit
    curves: tuple[CurveWorkflowSummary, ...]
    source_missing: bool = False
