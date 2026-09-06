"""Build one project-wide curve from per-image consolidated digitizations.

The image order shown by the GUI is deliberately ignored.  Sources are sorted
from their calibrated depth intervals, while the non-destructive digitization
stored in every image remains untouched.  Adjacent sources of the same curve
are cut logically at the midpoint of their real digitized overlap.
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from math import isfinite

from scipy.interpolate import PchipInterpolator

from logdigitizer.domain.calibration import (
    CalibrationError,
    DepthCalibration,
    DepthUnit,
    HorizontalCalibration,
)
from logdigitizer.domain.digitization import DigitizedCurve, TracePoint
from logdigitizer.processing.calibration import (
    CalibrationRangeError,
    depth_to_pixel_y,
    pixel_x_to_value,
    pixel_y_to_depth,
)


class CurveAssemblyError(ValueError):
    """Raised when image curves cannot form one deterministic depth sequence."""


@dataclass(frozen=True, slots=True)
class CurveAssemblyInput:
    """One image that may contribute a consolidated project curve."""

    segment_id: str
    segment_name: str
    depth_calibration: DepthCalibration
    digitized_curve: DigitizedCurve
    horizontal_calibrations: Mapping[str, HorizontalCalibration] = field(
        default_factory=dict
    )


@dataclass(frozen=True, slots=True)
class CurveJoin:
    """Automatic logical cut between two overlapping image digitizations."""

    curve_id: str
    upper_segment_id: str
    upper_segment_name: str
    lower_segment_id: str
    lower_segment_name: str
    overlap_start_depth: float
    overlap_end_depth: float
    depth: float


@dataclass(frozen=True, slots=True)
class CurveGap:
    """Depth interval for which neither adjacent image contributes the curve."""

    curve_id: str
    upper_segment_id: str
    lower_segment_id: str
    start_depth: float
    end_depth: float


@dataclass(frozen=True, slots=True)
class _SectionValueEvaluator:
    section_id: str
    segment_name: str
    minimum_depth: float
    maximum_depth: float
    minimum_y_px: float
    maximum_y_px: float
    depth_calibration: DepthCalibration = field(repr=False, compare=False)
    horizontal_calibration: HorizontalCalibration = field(
        repr=False,
        compare=False,
    )
    scale_offset: int
    interpolation: str
    y_pixels: tuple[float, ...]
    x_pixels: tuple[float, ...]
    interpolator: PchipInterpolator | None = field(
        repr=False,
        compare=False,
    )

    def contains(self, depth: float) -> bool:
        return self.minimum_depth <= depth <= self.maximum_depth

    def value_at_depth(self, depth: float) -> float | None:
        if not self.contains(depth):
            return None
        try:
            y_px = depth_to_pixel_y(
                float(depth),
                self.depth_calibration,
            )
        except (CalibrationError, OverflowError, ValueError) as exc:
            raise CurveAssemblyError(
                f"Cannot evaluate {self.segment_name}: {exc}"
            ) from exc

        x_px = self._x_at_y(y_px)
        if x_px is None:
            return None
        try:
            value = pixel_x_to_value(
                x_px,
                self.horizontal_calibration,
                scale_offset=self.scale_offset,
            )
        except (CalibrationError, OverflowError, ValueError) as exc:
            raise CurveAssemblyError(
                f"Cannot evaluate {self.segment_name}: {exc}"
            ) from exc
        return value if isfinite(value) else None

    def _x_at_y(self, y_px: float) -> float | None:
        """Intersect the exact saved image-space geometry at one image Y."""
        target = float(y_px)
        tolerance = 1e-9 * max(
            1.0,
            abs(self.minimum_y_px),
            abs(self.maximum_y_px),
        )
        if (
            target < self.minimum_y_px - tolerance
            or target > self.maximum_y_px + tolerance
        ):
            return None
        target = min(max(target, self.minimum_y_px), self.maximum_y_px)

        if self.interpolation == "pchip":
            if self.interpolator is None:
                raise CurveAssemblyError(
                    "PCHIP section is missing its interpolator."
                )
            x_px = float(self.interpolator(target))
            return x_px if isfinite(x_px) else None

        if self.interpolation != "linear":
            raise CurveAssemblyError(
                f"Unsupported section interpolation: {self.interpolation!r}."
            )

        index = bisect_right(self.y_pixels, target) - 1
        if index < 0:
            return None
        if index >= len(self.y_pixels) - 1:
            return self.x_pixels[-1]

        y0 = self.y_pixels[index]
        y1 = self.y_pixels[index + 1]
        x0 = self.x_pixels[index]
        x1 = self.x_pixels[index + 1]
        ratio = (target - y0) / (y1 - y0)
        return float(x0 + ratio * (x1 - x0))


@dataclass(frozen=True, slots=True)
class CurveImageSource:
    """Real depth coverage of one curve consolidated inside one image."""

    curve_id: str
    segment_id: str
    segment_name: str
    unit: DepthUnit
    image_minimum_depth: float
    image_maximum_depth: float
    curve_minimum_depth: float
    curve_maximum_depth: float
    section_depth_ranges: tuple[tuple[float, float], ...]
    _value_evaluators: tuple[_SectionValueEvaluator, ...] = field(
        repr=False,
        compare=False,
    )

    def value_at_depth(self, depth: float) -> float | None:
        """Evaluate only this image; never fall back to another source."""
        target = float(depth)
        if not isfinite(target):
            raise CurveAssemblyError("Depth must be finite.")
        for evaluator in self._value_evaluators:
            value = evaluator.value_at_depth(target)
            if value is not None:
                return value
        return None


@dataclass(frozen=True, slots=True)
class CurveSourceAssignment:
    """Logical, non-destructive depth interval assigned to one image source."""

    source: CurveImageSource
    minimum_depth: float
    maximum_depth: float
    minimum_inclusive: bool

    def contains(self, depth: float) -> bool:
        lower_ok = (
            depth >= self.minimum_depth
            if self.minimum_inclusive
            else depth > self.minimum_depth
        )
        return lower_ok and depth <= self.maximum_depth


@dataclass(frozen=True, slots=True)
class AssembledProjectCurve:
    """One deterministic project-wide curve ready for LAS resampling."""

    curve_id: str
    unit: DepthUnit | None
    sources: tuple[CurveImageSource, ...]
    joins: tuple[CurveJoin, ...]
    gaps: tuple[CurveGap, ...]
    assignments: tuple[CurveSourceAssignment, ...]

    @property
    def minimum_depth(self) -> float | None:
        return self.sources[0].curve_minimum_depth if self.sources else None

    @property
    def maximum_depth(self) -> float | None:
        return self.sources[-1].curve_maximum_depth if self.sources else None

    def source_at_depth(self, depth: float) -> CurveImageSource | None:
        """Return the sole source assigned at depth, with upper image at joins."""
        target = float(depth)
        if not isfinite(target):
            raise CurveAssemblyError("Depth must be finite.")
        for assignment in self.assignments:
            if assignment.contains(target):
                return assignment.source
        return None

    def value_at_depth(self, depth: float) -> float | None:
        """Evaluate the assigned image exclusively, returning ``None`` in gaps."""
        source = self.source_at_depth(depth)
        return None if source is None else source.value_at_depth(depth)

    def sample(self, depths: Iterable[float]) -> tuple[float | None, ...]:
        """Evaluate a LAS-style depth sequence without cross-image interpolation."""
        return tuple(self.value_at_depth(depth) for depth in depths)


def build_project_curve(
    curve_id: str,
    inputs: Sequence[CurveAssemblyInput],
    *,
    require_complete_values: bool = True,
) -> AssembledProjectCurve:
    """Build a curve independently of the Project Explorer image order.

    Only inputs containing usable consolidated geometry contribute.  The
    source order is derived from each image's calibrated minimum depth.  The
    midpoint rule is calculated from the real curve extrema, not from the last
    node created chronologically and not from the image calibration bounds.
    """
    normalized_curve_id = str(curve_id).strip()
    if not normalized_curve_id:
        raise CurveAssemblyError("Curve id cannot be empty.")

    sources = [
        source
        for item in inputs
        if (source := _build_image_source(
            normalized_curve_id,
            item,
            require_complete_values=require_complete_values,
        ))
        is not None
    ]
    sources.sort(
        key=lambda source: (
            source.image_minimum_depth,
            source.image_maximum_depth,
            source.segment_id,
        )
    )

    if not sources:
        return AssembledProjectCurve(
            curve_id=normalized_curve_id,
            unit=None,
            sources=(),
            joins=(),
            gaps=(),
            assignments=(),
        )

    units = {source.unit for source in sources}
    if len(units) != 1:
        raise CurveAssemblyError(
            "All images contributing the same curve must use one depth unit."
        )

    # Sorting the GUI list is unnecessary, but calibrated image envelopes must
    # genuinely advance. A contained/repeated image would make adjacent seam
    # ownership ambiguous and is therefore rejected only when assembly runs.
    for upper, lower in zip(sources, sources[1:]):
        if lower.image_maximum_depth <= upper.image_maximum_depth:
            raise CurveAssemblyError(
                "Calibrated image intervals do not advance in depth: "
                f"{upper.segment_name} / {lower.segment_name}."
            )

    joins: list[CurveJoin] = []
    gaps: list[CurveGap] = []
    boundaries: list[CurveJoin | CurveGap] = []
    for upper, lower in zip(sources, sources[1:]):
        if lower.curve_minimum_depth <= upper.curve_maximum_depth:
            overlap_start = lower.curve_minimum_depth
            overlap_end = upper.curve_maximum_depth
            join = CurveJoin(
                curve_id=normalized_curve_id,
                upper_segment_id=upper.segment_id,
                upper_segment_name=upper.segment_name,
                lower_segment_id=lower.segment_id,
                lower_segment_name=lower.segment_name,
                overlap_start_depth=overlap_start,
                overlap_end_depth=overlap_end,
                depth=(overlap_start + overlap_end) / 2.0,
            )
            joins.append(join)
            boundaries.append(join)
        else:
            gap = CurveGap(
                curve_id=normalized_curve_id,
                upper_segment_id=upper.segment_id,
                lower_segment_id=lower.segment_id,
                start_depth=upper.curve_maximum_depth,
                end_depth=lower.curve_minimum_depth,
            )
            gaps.append(gap)
            boundaries.append(gap)

    assignments = _build_assignments(sources, boundaries)
    return AssembledProjectCurve(
        curve_id=normalized_curve_id,
        unit=next(iter(units)),
        sources=tuple(sources),
        joins=tuple(joins),
        gaps=tuple(gaps),
        assignments=assignments,
    )


def _build_image_source(
    curve_id: str,
    item: CurveAssemblyInput,
    *,
    require_complete_values: bool,
) -> CurveImageSource | None:
    if item.digitized_curve.curve_id != curve_id:
        return None

    depth_ranges: list[tuple[float, float]] = []
    evaluators: list[_SectionValueEvaluator] = []
    for section in item.digitized_curve.sections:
        points, interpolation = _effective_geometry(section)
        if len(points) < 2:
            continue

        depth_bounds = _physical_depth_bounds(
            points,
            item.depth_calibration,
        )
        if depth_bounds is None:
            continue
        minimum_depth, maximum_depth = depth_bounds
        depth_ranges.append((minimum_depth, maximum_depth))

        horizontal = item.horizontal_calibrations.get(section.scale_id)
        if horizontal is None:
            if require_complete_values:
                raise CurveAssemblyError(
                    f"Missing horizontal scale {section.scale_id!r} for "
                    f"{item.segment_name}."
                )
            continue

        try:
            # Validate the complete section transformation now so export does
            # not fail halfway through a sampling grid (notably for a
            # wrap-around offset attached to a logarithmic scale).
            for point in points:
                pixel_x_to_value(
                    point.x_px,
                    horizontal,
                    scale_offset=section.scale_offset,
                )
        except (CalibrationError, OverflowError, ValueError) as exc:
            raise CurveAssemblyError(
                f"Cannot evaluate {item.segment_name}: {exc}"
            ) from exc

        y_pixels = tuple(point.y_px for point in points)
        x_pixels = tuple(point.x_px for point in points)
        evaluators.append(
            _SectionValueEvaluator(
                section_id=section.section_id,
                segment_name=item.segment_name,
                minimum_depth=minimum_depth,
                maximum_depth=maximum_depth,
                minimum_y_px=y_pixels[0],
                maximum_y_px=y_pixels[-1],
                depth_calibration=item.depth_calibration,
                horizontal_calibration=horizontal,
                scale_offset=section.scale_offset,
                interpolation=interpolation,
                y_pixels=y_pixels,
                x_pixels=x_pixels,
                interpolator=(
                    PchipInterpolator(
                        y_pixels,
                        x_pixels,
                        extrapolate=False,
                    )
                    if interpolation == "pchip"
                    else None
                ),
            )
        )

    if not depth_ranges:
        return None

    depth_ranges.sort()
    calibration = item.depth_calibration
    return CurveImageSource(
        curve_id=curve_id,
        segment_id=str(item.segment_id),
        segment_name=str(item.segment_name),
        unit=calibration.unit,
        image_minimum_depth=calibration.minimum_depth,
        image_maximum_depth=calibration.maximum_depth,
        curve_minimum_depth=min(bounds[0] for bounds in depth_ranges),
        curve_maximum_depth=max(bounds[1] for bounds in depth_ranges),
        section_depth_ranges=tuple(depth_ranges),
        _value_evaluators=tuple(evaluators),
    )


def _build_assignments(
    sources: Sequence[CurveImageSource],
    boundaries: Sequence[CurveJoin | CurveGap],
) -> tuple[CurveSourceAssignment, ...]:
    assignments: list[CurveSourceAssignment] = []
    for index, source in enumerate(sources):
        previous = boundaries[index - 1] if index else None
        following = boundaries[index] if index < len(boundaries) else None

        if isinstance(previous, CurveJoin):
            minimum = previous.depth
            minimum_inclusive = False
        else:
            minimum = source.curve_minimum_depth
            minimum_inclusive = True

        maximum = (
            following.depth
            if isinstance(following, CurveJoin)
            else source.curve_maximum_depth
        )
        if minimum > maximum:
            raise CurveAssemblyError(
                "Automatic curve joins are not monotonic in depth."
            )
        assignments.append(
            CurveSourceAssignment(
                source=source,
                minimum_depth=minimum,
                maximum_depth=maximum,
                minimum_inclusive=minimum_inclusive,
            )
        )
    return tuple(assignments)


def _effective_geometry(
    section,
) -> tuple[tuple[TracePoint, ...], str]:
    """Return the exact geometry displayed and therefore sampled for LAS."""
    if len(section.control_points_px) >= 2:
        return section.control_points_px, "pchip"
    if len(section.raw_samples_px) >= 2:
        return section.raw_samples_px, "linear"
    return (), "linear"


def _physical_depth_bounds(
    points: Sequence[TracePoint],
    calibration: DepthCalibration,
) -> tuple[float, float] | None:
    """Return non-negative physical support without altering source points.

    A tracker or control point may lie a fraction of a pixel beyond a
    zero-depth calibration reference.  The original geometry is retained so
    its intersection at depth zero remains exact; only the negative portion
    is clipped logically from the assembled curve.
    """
    depths: list[float] = []
    for point in points:
        try:
            depths.append(pixel_y_to_depth(point.y_px, calibration))
        except CalibrationRangeError:
            continue

    minimum_y = points[0].y_px
    maximum_y = points[-1].y_px
    try:
        zero_y = depth_to_pixel_y(0.0, calibration)
    except CalibrationError:
        zero_y = None
    if zero_y is not None and minimum_y <= zero_y <= maximum_y:
        depths.append(0.0)

    if not depths:
        return None
    return min(depths), max(depths)
