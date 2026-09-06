"""Explicit and extensible workflow states."""

from __future__ import annotations

from enum import StrEnum


class StageStatus(StrEnum):
    """Status for an independent processing stage of a segment."""

    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    COMPLETE = "complete"
    REVIEW_REQUIRED = "review_required"


class SegmentStatus(StrEnum):
    """High-level segment status independent from individual stage states."""

    ACTIVE = "active"
    COMPLETE = "complete"
    REVIEW_REQUIRED = "review_required"


class CurveCalibrationReadiness(StrEnum):
    """Digitization readiness for one project curve in one scanned segment.

    Readiness is deliberately evaluated per ``segment + curve`` rather than by
    requiring every project curve to be calibrated before work may continue.
    """

    NOT_PRESENT = "not_present"
    DEPTH_NOT_CALIBRATED = "depth_not_calibrated"
    X_NOT_CALIBRATED = "x_not_calibrated"
    READY = "ready"
