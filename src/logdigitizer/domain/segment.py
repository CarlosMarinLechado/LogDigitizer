"""Domain model for one scanned log segment."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from .digitization import DigitizedCurve, DigitizationError
from .states import SegmentStatus, StageStatus


@dataclass(slots=True)
class Segment:
    """A single scanned image belonging to a log project.

    ``source_path`` is stored as a project-relative POSIX path whenever possible.
    Future scientific results can be added through explicit fields without changing
    the identity, ordering or original source of the segment.
    """

    segment_id: str
    order: int
    name: str
    source_path: str
    checksum: str | None = None
    status: SegmentStatus = SegmentStatus.ACTIVE
    preprocess_status: StageStatus = StageStatus.NOT_STARTED
    calibration_status: StageStatus = StageStatus.NOT_STARTED
    digitization_status: StageStatus = StageStatus.NOT_STARTED
    digitizations: list[DigitizedCurve] = field(default_factory=list)
    source_missing: bool = False
    extensions: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        curve_ids = [item.curve_id for item in self.digitizations]
        if len(curve_ids) != len(set(curve_ids)):
            raise DigitizationError(
                "Segment cannot contain duplicate digitized curve IDs."
            )

    @classmethod
    def create(
        cls,
        *,
        order: int,
        name: str,
        source_path: str,
        checksum: str | None = None,
    ) -> "Segment":
        """Create a segment with a stable UUID and initial workflow states."""
        return cls(
            segment_id=str(uuid4()),
            order=order,
            name=name,
            source_path=source_path,
            checksum=checksum,
        )


    def get_digitized_curve(self, curve_id: str) -> DigitizedCurve:
        """Return one curve digitization by project-level curve ID."""
        for digitized in self.digitizations:
            if digitized.curve_id == curve_id:
                return digitized
        raise KeyError(curve_id)

    def set_digitized_curve(self, digitized: DigitizedCurve) -> None:
        """Add or replace one curve digitization for this scanned segment."""
        for index, existing in enumerate(self.digitizations):
            if existing.curve_id == digitized.curve_id:
                self.digitizations[index] = digitized
                return
        self.digitizations.append(digitized)

    def remove_digitized_curve(self, curve_id: str) -> DigitizedCurve:
        """Remove and return one curve digitization from this segment."""
        for index, digitized in enumerate(self.digitizations):
            if digitized.curve_id == curve_id:
                return self.digitizations.pop(index)
        raise KeyError(curve_id)

    def mark_downstream_review_required(self, changed_stage: str) -> None:
        """Prepare dependency invalidation without encoding scientific algorithms.

        A preprocessing change invalidates calibration and digitization; a
        calibration change invalidates digitization. This lightweight rule is
        intentionally domain-level and can later be expanded with dependency graphs.
        """
        normalized = changed_stage.strip().lower()
        if normalized == "preprocess":
            if self.calibration_status is not StageStatus.NOT_STARTED:
                self.calibration_status = StageStatus.REVIEW_REQUIRED
            if self.digitization_status is not StageStatus.NOT_STARTED:
                self.digitization_status = StageStatus.REVIEW_REQUIRED
        elif normalized == "calibration":
            if self.digitization_status is not StageStatus.NOT_STARTED:
                self.digitization_status = StageStatus.REVIEW_REQUIRED
        else:
            raise ValueError(f"Unknown stage: {changed_stage}")
