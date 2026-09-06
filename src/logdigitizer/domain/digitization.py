"""Domain model for digitized well-log curves in rectified-image coordinates."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from math import isclose, isfinite
from typing import Any, Iterable
from uuid import uuid4

from .calibration import DEFAULT_HORIZONTAL_SCALE_ID


class DigitizationError(ValueError):
    """Raised when digitized-curve geometry or settings are invalid."""


class TraceSource(StrEnum):
    """Origin of one continuous digitized section."""

    MANUAL = "manual"
    AUTOMATIC = "automatic"


@dataclass(frozen=True, slots=True)
class TracePoint:
    """One point expressed in canonical pixels of the rectified image."""

    x_px: float
    y_px: float

    def __post_init__(self) -> None:
        x = float(self.x_px)
        y = float(self.y_px)
        if not isfinite(x) or not isfinite(y):
            raise DigitizationError("Trace point coordinates must be finite.")
        object.__setattr__(self, "x_px", x)
        object.__setattr__(self, "y_px", y)

    def to_dict(self) -> dict[str, float]:
        return {"x_px": self.x_px, "y_px": self.y_px}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TracePoint":
        try:
            return cls(
                x_px=float(data["x_px"]),
                y_px=float(data["y_px"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise DigitizationError(f"Invalid trace point: {exc}") from exc


def _normalize_points(
    points: Iterable[TracePoint],
    *,
    label: str,
) -> tuple[TracePoint, ...]:
    normalized = tuple(
        point if isinstance(point, TracePoint) else TracePoint(*point)
        for point in points
    )
    normalized = tuple(sorted(normalized, key=lambda point: point.y_px))

    y_values = [point.y_px for point in normalized]
    if len(y_values) != len(set(y_values)):
        raise DigitizationError(
            f"{label} cannot contain duplicate y_px values."
        )
    return normalized


def _normalize_scale_offset(value: object) -> int:
    """Return one whole linear-scale displacement.

    A backup/wrap-around section belongs to the same scientific curve as its
    neighbours.  The integer only records how many complete linear scale
    widths must be added (or subtracted) after horizontal calibration.
    """
    if isinstance(value, bool):
        raise DigitizationError("Scale offset must be an integer.")
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise DigitizationError("Scale offset must be an integer.") from exc
    if not isfinite(numeric) or not numeric.is_integer():
        raise DigitizationError("Scale offset must be an integer.")
    return int(numeric)


@dataclass(slots=True)
class TraceSection:
    """One continuous section of a digitized curve.

    ``control_points_px`` is always the editable geometry. Automatic sections
    may additionally preserve a dense ``raw_samples_px`` trajectory so later
    smoothing/simplification changes never destroy the original tracker result.
    """

    section_id: str
    source: TraceSource
    control_points_px: tuple[TracePoint, ...] = ()
    raw_samples_px: tuple[TracePoint, ...] = ()
    smoothing_strength: float = 0.0
    simplification_tolerance_px: float = 0.0
    scale_id: str = DEFAULT_HORIZONTAL_SCALE_ID
    scale_offset: int = 0

    def __post_init__(self) -> None:
        self.section_id = str(self.section_id).strip()
        if not self.section_id:
            raise DigitizationError("Trace section id cannot be empty.")

        if not isinstance(self.source, TraceSource):
            self.source = TraceSource(str(self.source))

        self.control_points_px = _normalize_points(
            self.control_points_px,
            label="Control points",
        )
        self.raw_samples_px = _normalize_points(
            self.raw_samples_px,
            label="Raw samples",
        )

        self.smoothing_strength = float(self.smoothing_strength)
        self.simplification_tolerance_px = float(
            self.simplification_tolerance_px
        )
        self.scale_id = str(self.scale_id).strip()
        if not self.scale_id:
            raise DigitizationError("Trace section scale id cannot be empty.")
        self.scale_offset = _normalize_scale_offset(self.scale_offset)
        if not isfinite(self.smoothing_strength) or not 0.0 <= self.smoothing_strength <= 1.0:
            raise DigitizationError(
                "Smoothing strength must be between 0 and 1."
            )
        if (
            not isfinite(self.simplification_tolerance_px)
            or self.simplification_tolerance_px < 0.0
        ):
            raise DigitizationError(
                "Simplification tolerance must be a non-negative pixel value."
            )

        if self.source is TraceSource.MANUAL:
            if self.raw_samples_px:
                raise DigitizationError(
                    "Manual sections cannot contain automatic raw samples."
                )
            if self.smoothing_strength != 0.0:
                raise DigitizationError(
                    "Manual sections cannot define automatic smoothing."
                )
            if self.simplification_tolerance_px != 0.0:
                raise DigitizationError(
                    "Manual sections cannot define automatic simplification."
                )

    @classmethod
    def create_manual(
        cls,
        points: Iterable[TracePoint] = (),
        *,
        scale_id: str = DEFAULT_HORIZONTAL_SCALE_ID,
        scale_offset: int = 0,
    ) -> "TraceSection":
        return cls(
            section_id=str(uuid4()),
            source=TraceSource.MANUAL,
            control_points_px=tuple(points),
            scale_id=scale_id,
            scale_offset=scale_offset,
        )

    @classmethod
    def create_automatic(
        cls,
        *,
        control_points: Iterable[TracePoint] = (),
        raw_samples: Iterable[TracePoint] = (),
        smoothing_strength: float = 0.0,
        simplification_tolerance_px: float = 0.0,
        scale_id: str = DEFAULT_HORIZONTAL_SCALE_ID,
        scale_offset: int = 0,
    ) -> "TraceSection":
        return cls(
            section_id=str(uuid4()),
            source=TraceSource.AUTOMATIC,
            control_points_px=tuple(control_points),
            raw_samples_px=tuple(raw_samples),
            smoothing_strength=smoothing_strength,
            simplification_tolerance_px=simplification_tolerance_px,
            scale_id=scale_id,
            scale_offset=scale_offset,
        )

    @property
    def start_y_px(self) -> float | None:
        if self.control_points_px:
            return self.control_points_px[0].y_px
        if self.raw_samples_px:
            return self.raw_samples_px[0].y_px
        return None

    @property
    def end_y_px(self) -> float | None:
        if self.control_points_px:
            return self.control_points_px[-1].y_px
        if self.raw_samples_px:
            return self.raw_samples_px[-1].y_px
        return None

    def replace_control_points(
        self,
        points: Iterable[TracePoint],
    ) -> tuple[TracePoint, ...]:
        self.control_points_px = _normalize_points(
            points,
            label="Control points",
        )
        return self.control_points_px

    def replace_raw_samples(
        self,
        points: Iterable[TracePoint],
    ) -> tuple[TracePoint, ...]:
        supplied = tuple(points)
        if self.source is TraceSource.MANUAL and supplied:
            raise DigitizationError(
                "Manual sections cannot contain automatic raw samples."
            )
        self.raw_samples_px = _normalize_points(
            supplied,
            label="Raw samples",
        )
        return self.raw_samples_px

    def set_processing(
        self,
        *,
        smoothing_strength: float,
        simplification_tolerance_px: float,
    ) -> None:
        if self.source is not TraceSource.AUTOMATIC:
            raise DigitizationError(
                "Processing parameters apply only to automatic sections."
            )
        smoothing = float(smoothing_strength)
        tolerance = float(simplification_tolerance_px)
        if not isfinite(smoothing) or not 0.0 <= smoothing <= 1.0:
            raise DigitizationError(
                "Smoothing strength must be between 0 and 1."
            )
        if not isfinite(tolerance) or tolerance < 0.0:
            raise DigitizationError(
                "Simplification tolerance must be a non-negative pixel value."
            )
        self.smoothing_strength = smoothing
        self.simplification_tolerance_px = tolerance

    def set_scale_offset(self, scale_offset: int) -> None:
        """Change section backup-scale metadata without moving image points."""
        self.scale_offset = _normalize_scale_offset(scale_offset)

    def set_scale_id(self, scale_id: str) -> None:
        """Change the horizontal calibration profile without moving points."""
        normalized = str(scale_id).strip()
        if not normalized:
            raise DigitizationError("Trace section scale id cannot be empty.")
        self.scale_id = normalized

    def to_dict(self) -> dict[str, Any]:
        return {
            "section_id": self.section_id,
            "source": self.source.value,
            "control_points_px": [
                point.to_dict() for point in self.control_points_px
            ],
            "raw_samples_px": [
                point.to_dict() for point in self.raw_samples_px
            ],
            "smoothing_strength": self.smoothing_strength,
            "simplification_tolerance_px": self.simplification_tolerance_px,
            "scale_id": self.scale_id,
            "scale_offset": self.scale_offset,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TraceSection":
        try:
            control_items = data.get("control_points_px", [])
            raw_items = data.get("raw_samples_px", [])
            if not isinstance(control_items, list) or not isinstance(raw_items, list):
                raise DigitizationError(
                    "Trace point collections must be lists."
                )
            return cls(
                section_id=str(data["section_id"]),
                source=TraceSource(str(data["source"])),
                control_points_px=tuple(
                    TracePoint.from_dict(item) for item in control_items
                ),
                raw_samples_px=tuple(
                    TracePoint.from_dict(item) for item in raw_items
                ),
                smoothing_strength=float(
                    data.get("smoothing_strength", 0.0)
                ),
                simplification_tolerance_px=float(
                    data.get("simplification_tolerance_px", 0.0)
                ),
                # Missing in projects saved before multi-scale support.
                scale_id=data.get(
                    "scale_id",
                    DEFAULT_HORIZONTAL_SCALE_ID,
                ),
                # Missing in projects saved before backup-scale support.
                scale_offset=data.get("scale_offset", 0),
            )
        except (KeyError, TypeError, ValueError) as exc:
            if isinstance(exc, DigitizationError):
                raise
            raise DigitizationError(f"Invalid trace section: {exc}") from exc


@dataclass(slots=True)
class DigitizedCurve:
    """Digitization of one project-level curve within one scanned segment."""

    curve_id: str
    sections: list[TraceSection] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.curve_id = str(self.curve_id).strip()
        if not self.curve_id:
            raise DigitizationError("Digitized curve id cannot be empty.")
        ids = [section.section_id for section in self.sections]
        if len(ids) != len(set(ids)):
            raise DigitizationError(
                "Digitized curve cannot contain duplicate section IDs."
            )
        self._sort_sections()

    def get_section(self, section_id: str) -> TraceSection:
        for section in self.sections:
            if section.section_id == section_id:
                return section
        raise KeyError(section_id)

    def scale_offset_at_y(self, y_px: float) -> int | None:
        """Return the saved section offset at one image Y coordinate.

        Sections with different scale transformations are not allowed to share
        a Y coordinate, so the lookup is unambiguous. ``None`` means that the
        cursor lies outside all saved geometric sections of this curve.
        """
        target_y = float(y_px)
        if not isfinite(target_y):
            raise DigitizationError("Cursor y_px must be finite.")

        section = self.section_at_y(y_px)
        return None if section is None else section.scale_offset

    def scale_settings_at_y(self, y_px: float) -> tuple[str, int] | None:
        """Return ``(scale_id, scale_offset)`` for saved geometry at image Y."""
        section = self.section_at_y(y_px)
        if section is None:
            return None
        return section.scale_id, section.scale_offset

    def section_at_y(self, y_px: float) -> TraceSection | None:
        """Return the unambiguous saved geometric section at one image Y."""
        target_y = float(y_px)
        if not isfinite(target_y):
            raise DigitizationError("Cursor y_px must be finite.")

        for section in self.sections:
            bounds = self._effective_y_bounds(section)
            if bounds is None:
                continue
            if (
                bounds[0] < target_y
                or isclose(
                    bounds[0],
                    target_y,
                    rel_tol=0.0,
                    abs_tol=1e-9,
                )
            ) and (
                target_y < bounds[1]
                or isclose(
                    target_y,
                    bounds[1],
                    rel_tol=0.0,
                    abs_tol=1e-9,
                )
            ):
                return section
        return None

    def add_section(self, section: TraceSection) -> None:
        if any(
            existing.section_id == section.section_id
            for existing in self.sections
        ):
            raise DigitizationError(
                f"Duplicate trace section id: {section.section_id}"
            )
        replacement = [*self.sections, section]
        self._validate_unique_y_geometry(replacement)
        self.sections = replacement
        self._sort_sections()

    def apply_automatic_override(self, section: TraceSection) -> int:
        """Insert an automatic trace without creating duplicate X values per Y.

        The newest automatic section replaces older automatic geometry inside
        its inclusive vertical range. Existing manual corrections always keep
        priority: the incoming automatic trajectory is split around every
        manual interval and joined to its boundary points.

        Existing automatic sections are normalized in their saved order before
        the new trace is applied. This also repairs automatic overlaps created
        by older application versions the next time the curve is tracked.

        Returns the number of existing automatic sections intersected by the
        incoming trace.
        """
        if section.source is not TraceSource.AUTOMATIC:
            raise DigitizationError(
                "Automatic override requires an automatic trace section."
            )
        if len(self._effective_points(section)) < 2:
            raise DigitizationError(
                "Automatic override requires at least two trace points."
            )
        if any(
            existing.section_id == section.section_id
            for existing in self.sections
        ):
            raise DigitizationError(
                f"Duplicate trace section id: {section.section_id}"
            )

        incoming_bounds = self._effective_y_bounds(section)
        assert incoming_bounds is not None
        automatic_sections_affected = sum(
            1
            for existing in self.sections
            if existing.source is TraceSource.AUTOMATIC
            and self._ranges_intersect(
                self._effective_y_bounds(existing),
                incoming_bounds,
            )
        )

        manual_sections = [
            existing
            for existing in self.sections
            if existing.source is TraceSource.MANUAL
        ]
        non_geometric_automatic = [
            existing
            for existing in self.sections
            if existing.source is TraceSource.AUTOMATIC
            and len(self._effective_points(existing)) < 2
        ]

        # Reapply saved automatic sections in their stable order so any legacy
        # automatic/automatic overlap becomes one unambiguous X=f(Y) path.
        automatic_sections: list[TraceSection] = []
        for existing in self.sections:
            if (
                existing.source is TraceSource.AUTOMATIC
                and len(self._effective_points(existing)) >= 2
            ):
                automatic_sections = self._overlay_automatic_section(
                    automatic_sections,
                    existing,
                )
        automatic_sections = self._overlay_automatic_section(
            automatic_sections,
            section,
        )

        # Manual corrections are immutable from the automatic tool. Clip all
        # automatic geometry around them and use their exact endpoints at the
        # joins so a shared boundary never contains two different X values.
        for manual in manual_sections:
            if len(self._effective_points(manual)) >= 2:
                automatic_sections = self._clip_automatic_around_manual(
                    automatic_sections,
                    manual,
                )

        replacement = [
            *automatic_sections,
            *non_geometric_automatic,
            *manual_sections,
        ]
        self._validate_unique_y_geometry(replacement)
        self.sections = replacement
        self._sort_sections()
        return automatic_sections_affected

    def apply_manual_override(self, section: TraceSection) -> int:
        """Insert one manual correction with priority over saved geometry.

        The manual section replaces every existing trace inside its inclusive
        vertical range.  Sections crossing either limit are split, and their
        surviving upper/lower fragments share the corresponding manual
        endpoint.  Consequently the visible curve remains continuous and the
        two junctions are straight segments for dense raw tracker sections.

        The newest manual correction also takes priority over an older manual
        section when their ranges overlap.  The method builds the replacement
        list before mutating ``self.sections`` so validation failures cannot
        leave a partially edited curve.

        Returns the number of automatic sections affected by the correction.
        """
        if section.source is not TraceSource.MANUAL:
            raise DigitizationError(
                "Manual override requires a manual trace section."
            )
        if len(section.control_points_px) < 2:
            raise DigitizationError(
                "Manual override requires at least two control points."
            )
        if any(
            existing.section_id == section.section_id
            for existing in self.sections
        ):
            raise DigitizationError(
                f"Duplicate trace section id: {section.section_id}"
            )

        start_point = section.control_points_px[0]
        end_point = section.control_points_px[-1]
        start_y = start_point.y_px
        end_y = end_point.y_px
        replacement: list[TraceSection] = []
        automatic_sections_affected = 0

        for existing in self.sections:
            bounds = self._section_y_bounds(existing)
            if bounds is None or bounds[1] < start_y or bounds[0] > end_y:
                replacement.append(existing)
                continue

            if existing.source is TraceSource.AUTOMATIC:
                automatic_sections_affected += 1

            upper = self._manual_override_fragment(
                existing,
                keep_above_y=start_y,
                boundary_point=start_point,
                boundary_scale_id=section.scale_id,
                boundary_scale_offset=section.scale_offset,
                section_id=existing.section_id,
            )
            lower_id = existing.section_id if upper is None else str(uuid4())
            lower = self._manual_override_fragment(
                existing,
                keep_below_y=end_y,
                boundary_point=end_point,
                boundary_scale_id=section.scale_id,
                boundary_scale_offset=section.scale_offset,
                section_id=lower_id,
            )
            if upper is not None:
                replacement.append(upper)
            if lower is not None:
                replacement.append(lower)

        replacement.append(section)
        self._validate_unique_y_geometry(replacement)
        self.sections = replacement
        self._sort_sections()
        return automatic_sections_affected

    def erase_automatic_range(self, start_y_px: float, end_y_px: float) -> int:
        """Delete automatic geometry in an inclusive vertical interval.

        Manual sections are never modified. Automatic sections crossing the
        interval are split into independent upper and lower fragments without
        inserting connector points, so the erased range remains an intentional
        visible gap. The method is transactional and returns the number of
        automatic sections affected.
        """
        first_y = float(start_y_px)
        second_y = float(end_y_px)
        if not isfinite(first_y) or not isfinite(second_y):
            raise DigitizationError(
                "Automatic erase limits must be finite pixel coordinates."
            )
        minimum_y, maximum_y = sorted((first_y, second_y))
        replacement: list[TraceSection] = []
        automatic_sections_affected = 0

        for existing in self.sections:
            if existing.source is not TraceSource.AUTOMATIC:
                replacement.append(existing)
                continue

            bounds = self._section_y_bounds(existing)
            if (
                bounds is None
                or bounds[1] < minimum_y
                or bounds[0] > maximum_y
            ):
                replacement.append(existing)
                continue

            automatic_sections_affected += 1
            upper = self._automatic_erase_fragment(
                existing,
                keep_above_y=minimum_y,
                section_id=existing.section_id,
            )
            lower_id = existing.section_id if upper is None else str(uuid4())
            lower = self._automatic_erase_fragment(
                existing,
                keep_below_y=maximum_y,
                section_id=lower_id,
            )
            if upper is not None:
                replacement.append(upper)
            if lower is not None:
                replacement.append(lower)

        self.sections = replacement
        self._sort_sections()
        return automatic_sections_affected

    @staticmethod
    def _section_y_bounds(
        section: TraceSection,
    ) -> tuple[float, float] | None:
        points = section.control_points_px + section.raw_samples_px
        if not points:
            return None
        y_values = tuple(point.y_px for point in points)
        return min(y_values), max(y_values)

    @staticmethod
    def _effective_points(
        section: TraceSection,
    ) -> tuple[TracePoint, ...]:
        """Return the geometry that represents the saved curve on screen."""
        if len(section.control_points_px) >= 2:
            return section.control_points_px
        if len(section.raw_samples_px) >= 2:
            return section.raw_samples_px
        return ()

    @classmethod
    def _effective_y_bounds(
        cls,
        section: TraceSection,
    ) -> tuple[float, float] | None:
        points = cls._effective_points(section)
        if not points:
            return None
        return points[0].y_px, points[-1].y_px

    @staticmethod
    def _ranges_intersect(
        first: tuple[float, float] | None,
        second: tuple[float, float] | None,
    ) -> bool:
        if first is None or second is None:
            return False
        return first[1] >= second[0] and second[1] >= first[0]

    @classmethod
    def _overlay_automatic_section(
        cls,
        saved: list[TraceSection],
        incoming: TraceSection,
    ) -> list[TraceSection]:
        """Overlay one automatic section on non-overlapping automatic data."""
        points = cls._effective_points(incoming)
        if incoming.source is not TraceSource.AUTOMATIC or len(points) < 2:
            raise DigitizationError(
                "Automatic overlay requires a valid automatic section."
            )

        start_point = points[0]
        end_point = points[-1]
        start_y = start_point.y_px
        end_y = end_point.y_px
        replacement: list[TraceSection] = []

        for existing in saved:
            bounds = cls._effective_y_bounds(existing)
            if bounds is None or bounds[1] < start_y or bounds[0] > end_y:
                replacement.append(existing)
                continue

            upper = cls._manual_override_fragment(
                existing,
                keep_above_y=start_y,
                boundary_point=start_point,
                boundary_scale_id=incoming.scale_id,
                boundary_scale_offset=incoming.scale_offset,
                section_id=existing.section_id,
            )
            lower_id = existing.section_id if upper is None else str(uuid4())
            lower = cls._manual_override_fragment(
                existing,
                keep_below_y=end_y,
                boundary_point=end_point,
                boundary_scale_id=incoming.scale_id,
                boundary_scale_offset=incoming.scale_offset,
                section_id=lower_id,
            )
            if upper is not None:
                replacement.append(upper)
            if lower is not None:
                replacement.append(lower)

        replacement.append(incoming)
        return replacement

    @classmethod
    def _clip_automatic_around_manual(
        cls,
        automatic_sections: list[TraceSection],
        manual: TraceSection,
    ) -> list[TraceSection]:
        """Remove automatic geometry covered by one manual correction."""
        manual_points = cls._effective_points(manual)
        if manual.source is not TraceSource.MANUAL or len(manual_points) < 2:
            raise DigitizationError(
                "Automatic clipping requires a valid manual section."
            )

        start_point = manual_points[0]
        end_point = manual_points[-1]
        start_y = start_point.y_px
        end_y = end_point.y_px
        replacement: list[TraceSection] = []

        for automatic in automatic_sections:
            bounds = cls._effective_y_bounds(automatic)
            if bounds is None or bounds[1] < start_y or bounds[0] > end_y:
                replacement.append(automatic)
                continue

            upper = cls._manual_override_fragment(
                automatic,
                keep_above_y=start_y,
                boundary_point=start_point,
                boundary_scale_id=manual.scale_id,
                boundary_scale_offset=manual.scale_offset,
                section_id=automatic.section_id,
            )
            lower_id = automatic.section_id if upper is None else str(uuid4())
            lower = cls._manual_override_fragment(
                automatic,
                keep_below_y=end_y,
                boundary_point=end_point,
                boundary_scale_id=manual.scale_id,
                boundary_scale_offset=manual.scale_offset,
                section_id=lower_id,
            )
            if upper is not None:
                replacement.append(upper)
            if lower is not None:
                replacement.append(lower)

        return replacement

    @classmethod
    def _validate_unique_y_geometry(
        cls,
        sections: Iterable[TraceSection],
    ) -> None:
        """Require at most one X value for every Y within one curve.

        Adjacent sections may share one endpoint when it is the same image
        point and both use the same scale profile and offset. Sections with a
        different X transformation must always have separate Y ranges.
        """
        geometric = [
            (section, cls._effective_points(section))
            for section in sections
            if len(cls._effective_points(section)) >= 2
        ]
        geometric.sort(
            key=lambda item: (
                item[1][0].y_px,
                item[1][-1].y_px,
                item[0].section_id,
            )
        )

        for index, (first_section, first_points) in enumerate(geometric):
            first_end = first_points[-1]
            for second_section, second_points in geometric[index + 1 :]:
                second_start = second_points[0]
                if first_end.y_px < second_start.y_px and not isclose(
                    first_end.y_px,
                    second_start.y_px,
                    rel_tol=0.0,
                    abs_tol=1e-9,
                ):
                    break

                shared_endpoint = bool(
                    isclose(
                        first_end.y_px,
                        second_start.y_px,
                        rel_tol=0.0,
                        abs_tol=1e-9,
                    )
                    and isclose(
                        first_end.x_px,
                        second_start.x_px,
                        rel_tol=0.0,
                        abs_tol=1e-9,
                    )
                    and first_section.scale_offset
                    == second_section.scale_offset
                    and first_section.scale_id == second_section.scale_id
                )
                if shared_endpoint:
                    continue

                raise DigitizationError(
                    "Digitized curve sections cannot overlap in Y, define "
                    "different X values at the same Y, or share the same Y "
                    "across different scale profiles or offsets: "
                    f"{first_section.section_id} / "
                    f"{second_section.section_id}."
                )

    @staticmethod
    def _manual_override_fragment(
        section: TraceSection,
        *,
        boundary_point: TracePoint,
        boundary_scale_id: str,
        boundary_scale_offset: int,
        section_id: str,
        keep_above_y: float | None = None,
        keep_below_y: float | None = None,
    ) -> TraceSection | None:
        """Return one surviving side of an overridden section.

        Sections with the same scale profile and offset share the overriding
        endpoint to preserve the physical trace. At a scale transition the old
        section instead ends strictly before (or starts strictly after) the new
        section, so different transforms never own the same image Y coordinate.
        """
        if (keep_above_y is None) == (keep_below_y is None):
            raise ValueError(
                "Exactly one manual-override fragment limit is required."
            )

        share_boundary = bool(
            section.scale_id == str(boundary_scale_id)
            and section.scale_offset == int(boundary_scale_offset)
        )

        def clipped(
            points: tuple[TracePoint, ...],
        ) -> tuple[TracePoint, ...]:
            if keep_above_y is not None:
                retained = tuple(
                    point for point in points if point.y_px < keep_above_y
                )
                if not retained:
                    return ()
                return (
                    retained + (boundary_point,)
                    if share_boundary
                    else retained
                )
            retained = tuple(
                point for point in points if point.y_px > keep_below_y
            )
            if not retained:
                return ()
            return (
                (boundary_point,) + retained
                if share_boundary
                else retained
            )

        control_points = clipped(section.control_points_px)
        raw_samples = clipped(section.raw_samples_px)
        if len(control_points) < 2 and len(raw_samples) < 2:
            return None

        return TraceSection(
            section_id=section_id,
            source=section.source,
            control_points_px=control_points,
            raw_samples_px=raw_samples,
            smoothing_strength=section.smoothing_strength,
            simplification_tolerance_px=(
                section.simplification_tolerance_px
            ),
            scale_id=section.scale_id,
            scale_offset=section.scale_offset,
        )

    @staticmethod
    def _automatic_erase_fragment(
        section: TraceSection,
        *,
        section_id: str,
        keep_above_y: float | None = None,
        keep_below_y: float | None = None,
    ) -> TraceSection | None:
        """Return one automatic side outside an erased vertical interval."""
        if section.source is not TraceSource.AUTOMATIC:
            raise ValueError("Only automatic sections can be erased by range.")
        if (keep_above_y is None) == (keep_below_y is None):
            raise ValueError(
                "Exactly one automatic-erase fragment limit is required."
            )

        def clipped(
            points: tuple[TracePoint, ...],
        ) -> tuple[TracePoint, ...]:
            if keep_above_y is not None:
                return tuple(
                    point for point in points if point.y_px < keep_above_y
                )
            return tuple(
                point for point in points if point.y_px > keep_below_y
            )

        control_points = clipped(section.control_points_px)
        raw_samples = clipped(section.raw_samples_px)
        # A lone point cannot represent X=f(Y) or be rendered as a trace. If a
        # dense raw fragment survives, let it remain the authoritative geometry
        # instead of retaining a misleading one-node processed trajectory.
        if len(control_points) < 2:
            control_points = ()
        if len(raw_samples) < 2:
            raw_samples = ()
        if not control_points and not raw_samples:
            return None

        return TraceSection(
            section_id=section_id,
            source=TraceSource.AUTOMATIC,
            control_points_px=control_points,
            raw_samples_px=raw_samples,
            smoothing_strength=section.smoothing_strength,
            simplification_tolerance_px=(
                section.simplification_tolerance_px
            ),
            scale_id=section.scale_id,
            scale_offset=section.scale_offset,
        )

    def remove_section(self, section_id: str) -> TraceSection:
        for index, section in enumerate(self.sections):
            if section.section_id == section_id:
                return self.sections.pop(index)
        raise KeyError(section_id)

    def _sort_sections(self) -> None:
        self.sections.sort(
            key=lambda section: (
                section.start_y_px is None,
                section.start_y_px if section.start_y_px is not None else 0.0,
                section.section_id,
            )
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "curve_id": self.curve_id,
            "sections": [section.to_dict() for section in self.sections],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DigitizedCurve":
        try:
            items = data.get("sections", [])
            if not isinstance(items, list):
                raise DigitizationError(
                    "Digitized curve sections must be a list."
                )
            return cls(
                curve_id=str(data["curve_id"]),
                sections=[TraceSection.from_dict(item) for item in items],
            )
        except (KeyError, TypeError, ValueError) as exc:
            if isinstance(exc, DigitizationError):
                raise
            raise DigitizationError(f"Invalid digitized curve: {exc}") from exc
