"""Domain model for a persistent Log Project."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from logdigitizer import PROJECT_FORMAT_VERSION, __version__

from .calibration import CurveDefinition
from .segment import Segment


def utc_now_iso() -> str:
    """Return a timezone-aware UTC timestamp in ISO 8601 format."""
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass(slots=True)
class LogProject:
    """Persistent scientific project representing one coherent log."""

    format_version: int
    project_id: str
    name: str
    created_at: str
    modified_at: str
    software_version: str
    metadata: dict[str, Any] = field(default_factory=dict)
    segments: list[Segment] = field(default_factory=list)
    curves: list[CurveDefinition] = field(default_factory=list)

    @classmethod
    def create(
        cls,
        *,
        name: str,
        well_name: str = "",
        location: str = "",
        description: str = "",
    ) -> "LogProject":
        """Create a new project with stable identity and basic metadata."""
        now = utc_now_iso()
        return cls(
            format_version=PROJECT_FORMAT_VERSION,
            project_id=str(uuid4()),
            name=name.strip(),
            created_at=now,
            modified_at=now,
            software_version=__version__,
            metadata={
                "well_name": well_name.strip(),
                "location": location.strip(),
                "description": description.strip(),
            },
        )

    def touch(self) -> None:
        """Update modification timestamp and writer software version."""
        self.modified_at = utc_now_iso()
        self.software_version = __version__

    def get_segment(self, segment_id: str) -> Segment:
        """Return a segment by stable ID or raise ``KeyError``."""
        for segment in self.segments:
            if segment.segment_id == segment_id:
                return segment
        raise KeyError(segment_id)

    def add_segment(self, segment: Segment) -> None:
        """Append a segment and normalize explicit one-based ordering."""
        if any(
            existing.segment_id == segment.segment_id
            for existing in self.segments
        ):
            raise ValueError(f"Duplicate segment id: {segment.segment_id}")
        self.segments.append(segment)
        self._normalize_order()
        self.touch()

    def remove_segment(self, segment_id: str) -> Segment:
        """Remove and return a segment while preserving sequential order."""
        for index, segment in enumerate(self.segments):
            if segment.segment_id == segment_id:
                removed = self.segments.pop(index)
                self._normalize_order()
                self.touch()
                return removed
        raise KeyError(segment_id)

    def move_segment(self, segment_id: str, offset: int) -> bool:
        """Move a segment one or more positions. Return whether order changed."""
        if offset == 0:
            return False
        current = next(
            (
                index
                for index, item in enumerate(self.segments)
                if item.segment_id == segment_id
            ),
            None,
        )
        if current is None:
            raise KeyError(segment_id)
        target = max(0, min(len(self.segments) - 1, current + offset))
        if target == current:
            return False
        item = self.segments.pop(current)
        self.segments.insert(target, item)
        self._normalize_order()
        self.touch()
        return True

    def get_curve(self, curve_id: str) -> CurveDefinition:
        """Return a project-level curve by stable ID or raise ``KeyError``."""
        for curve in self.curves:
            if curve.curve_id == curve_id:
                return curve
        raise KeyError(curve_id)

    def add_curve(self, curve: CurveDefinition) -> None:
        """Add one global curve definition with a unique mnemonic."""
        if any(existing.curve_id == curve.curve_id for existing in self.curves):
            raise ValueError(f"Duplicate curve id: {curve.curve_id}")
        if any(
            existing.mnemonic.casefold() == curve.mnemonic.casefold()
            for existing in self.curves
        ):
            raise ValueError(f"Duplicate curve mnemonic: {curve.mnemonic}")
        self.curves.append(curve)
        self.touch()

    def replace_curve(self, curve: CurveDefinition) -> CurveDefinition:
        """Replace one global curve definition while preserving its ID."""
        for index, existing in enumerate(self.curves):
            if existing.curve_id != curve.curve_id:
                continue
            if any(
                other.curve_id != curve.curve_id
                and other.mnemonic.casefold() == curve.mnemonic.casefold()
                for other in self.curves
            ):
                raise ValueError(f"Duplicate curve mnemonic: {curve.mnemonic}")
            self.curves[index] = curve
            self.touch()
            return existing
        raise KeyError(curve.curve_id)

    def remove_curve(self, curve_id: str) -> CurveDefinition:
        """Remove and return a project-level curve definition."""
        for index, curve in enumerate(self.curves):
            if curve.curve_id == curve_id:
                removed = self.curves.pop(index)
                self.touch()
                return removed
        raise KeyError(curve_id)

    def _normalize_order(self) -> None:
        for order, segment in enumerate(self.segments, start=1):
            segment.order = order
