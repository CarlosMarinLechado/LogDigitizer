"""Headless LAS 2.0 export planning and writing.

The scientific curve assembly remains non-destructive and independent for
every curve.  This module selects the common regular depth grid, keeps missing
samples as ``None`` until file formatting, and writes an unwrapped LAS 2.0
file without depending on ``lasio`` or PySide6.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, fields
from decimal import (
    Decimal,
    InvalidOperation,
    ROUND_CEILING,
    ROUND_FLOOR,
    ROUND_HALF_UP,
)
from math import isfinite
from pathlib import Path
from typing import Any
from unicodedata import normalize
from uuid import uuid4

from logdigitizer import __version__
from logdigitizer.domain.calibration import CurveDefinition, DepthUnit
from logdigitizer.processing.curve_assembly import AssembledProjectCurve
from logdigitizer.processing.scientific_sampling import (
    MAX_SAMPLING_ROWS,
    DepthSamplingDefinition,
    SampledCurve,
    SampledProjectCurves,
    ScientificSamplingError,
    sample_assembled_project_curves,
)


DEFAULT_LAS_NULL_VALUE = -999.25
MAX_LAS_SCALE_TRANSITION_GAP_Y_PX = 8.0
MAX_LAS_AUTOMATIC_GAP_SAMPLES = 5


class LasExportError(ValueError):
    """Raised when a conforming LAS 2.0 export cannot be produced."""


@dataclass(frozen=True, slots=True)
class LasWellMetadata:
    """User-editable LAS header metadata.

    Unicode is preserved in the project.  Transliteration to the ASCII range
    required by LAS 2.0 happens only while serializing the output file.
    """

    company: str = ""
    well: str = ""
    field: str = ""
    location: str = ""
    province: str = ""
    county: str = ""
    state: str = ""
    country: str = ""
    service_company: str = ""
    log_date: str = ""
    unique_well_id: str = ""
    api_number: str = ""
    licence_number: str = ""
    other_information: str = ""

    def __post_init__(self) -> None:
        for item in fields(self):
            value = getattr(self, item.name)
            object.__setattr__(
                self,
                item.name,
                "" if value is None else str(value).strip(),
            )

    def to_dict(self) -> dict[str, str]:
        """Return a JSON-compatible representation for project metadata."""
        return {
            item.name: getattr(self, item.name)
            for item in fields(self)
        }

    @classmethod
    def from_project_metadata(
        cls,
        *,
        project_name: str,
        metadata: Mapping[str, Any],
    ) -> "LasWellMetadata":
        """Prefill known project values and any previously saved LAS fields."""
        stored = metadata.get("las", {})
        if not isinstance(stored, Mapping):
            stored = {}

        supported = {item.name for item in fields(cls)}
        values = {
            key: value
            for key, value in stored.items()
            if key in supported
        }
        values.setdefault(
            "well",
            str(metadata.get("well_name", "")).strip()
            or str(project_name).strip(),
        )
        values.setdefault(
            "location",
            str(metadata.get("location", "")).strip(),
        )
        values.setdefault(
            "other_information",
            str(metadata.get("description", "")).strip(),
        )
        return cls(**values)


@dataclass(frozen=True, slots=True)
class LasCurveColumn:
    """One non-index curve declared in ``~Curve`` and ``~ASCII``."""

    curve_id: str
    mnemonic: str
    unit: str
    description: str

    @classmethod
    def from_definition(cls, curve: CurveDefinition) -> "LasCurveColumn":
        description = curve.description.strip() or curve.name.strip()
        return cls(
            curve_id=curve.curve_id,
            mnemonic=_las_token(curve.mnemonic, kind="Curve mnemonic"),
            unit=_las_unit(curve.unit),
            description=_ascii_text(description),
        )


@dataclass(frozen=True, slots=True)
class LasExportCurveInfo:
    """Availability and real depth coverage of one project curve."""

    curve_id: str
    mnemonic: str
    name: str
    unit: str
    minimum_depth: float | None
    maximum_depth: float | None
    depth_unit: DepthUnit | None
    exportable: bool
    unavailable_reason: str = ""


@dataclass(frozen=True, slots=True)
class LasCurveGap:
    """One run of missing LAS-grid samples between valid samples of a curve."""

    curve_id: str
    mnemonic: str
    previous_depth: float
    next_depth: float
    missing_start_depth: float
    missing_stop_depth: float
    missing_sample_count: int




@dataclass(frozen=True, slots=True)
class LasScaleTransition:
    """One intentional scale/offset seam eligible for LAS-only interpolation.

    The original digitized geometry remains untouched.  During LAS sampling,
    only NULL grid samples strictly between the two saved section endpoints may
    be filled, and only when the image-space gap is small enough to represent
    a deliberate scale transition rather than an ordinary digitization gap.
    """

    curve_id: str
    segment_id: str
    upper_scale_id: str
    lower_scale_id: str
    upper_scale_offset: int
    lower_scale_offset: int
    upper_depth: float
    lower_depth: float
    upper_value: float
    lower_value: float
    gap_y_px: float

    def __post_init__(self) -> None:
        curve_id = str(self.curve_id).strip()
        segment_id = str(self.segment_id).strip()
        upper_scale_id = str(self.upper_scale_id).strip()
        lower_scale_id = str(self.lower_scale_id).strip()
        if not curve_id or not segment_id or not upper_scale_id or not lower_scale_id:
            raise LasExportError("LAS scale-transition identifiers cannot be empty.")
        numeric = (
            self.upper_depth,
            self.lower_depth,
            self.upper_value,
            self.lower_value,
            self.gap_y_px,
        )
        if not all(isfinite(float(value)) for value in numeric):
            raise LasExportError("LAS scale-transition values must be finite.")
        if float(self.gap_y_px) <= 0.0:
            raise LasExportError("LAS scale-transition pixel gap must be positive.")
        if abs(float(self.lower_depth) - float(self.upper_depth)) <= 1e-12:
            raise LasExportError("LAS scale-transition endpoint depths must differ.")
        if (
            upper_scale_id == lower_scale_id
            and int(self.upper_scale_offset) == int(self.lower_scale_offset)
        ):
            raise LasExportError(
                "LAS scale transition requires a scale id or scale offset change."
            )
        object.__setattr__(self, "curve_id", curve_id)
        object.__setattr__(self, "segment_id", segment_id)
        object.__setattr__(self, "upper_scale_id", upper_scale_id)
        object.__setattr__(self, "lower_scale_id", lower_scale_id)
        object.__setattr__(self, "upper_scale_offset", int(self.upper_scale_offset))
        object.__setattr__(self, "lower_scale_offset", int(self.lower_scale_offset))
        object.__setattr__(self, "upper_depth", float(self.upper_depth))
        object.__setattr__(self, "lower_depth", float(self.lower_depth))
        object.__setattr__(self, "upper_value", float(self.upper_value))
        object.__setattr__(self, "lower_value", float(self.lower_value))
        object.__setattr__(self, "gap_y_px", float(self.gap_y_px))

    @property
    def eligible(self) -> bool:
        """Whether this seam is small enough for automatic LAS interpolation."""
        return self.gap_y_px <= MAX_LAS_SCALE_TRANSITION_GAP_Y_PX

    @property
    def minimum_depth(self) -> float:
        return min(self.upper_depth, self.lower_depth)

    @property
    def maximum_depth(self) -> float:
        return max(self.upper_depth, self.lower_depth)

    def value_at_depth(self, depth: float) -> float | None:
        """Interpolate linearly in physical depth/value coordinates."""
        target = float(depth)
        if not self.minimum_depth < target < self.maximum_depth:
            return None
        span = self.lower_depth - self.upper_depth
        ratio = (target - self.upper_depth) / span
        value = self.upper_value + ratio * (self.lower_value - self.upper_value)
        return float(value) if isfinite(value) else None


@dataclass(frozen=True, slots=True)
class LasExportPreview:
    """Exact LAS grid summary without evaluating every curve sample."""

    coverage_start_depth: float
    coverage_stop_depth: float
    start_depth: float
    stop_depth: float
    step_depth: float
    row_count: int
    depth_unit: DepthUnit
    first_image_resolution: float | None = None
    internal_gaps: tuple[LasCurveGap, ...] = ()
    interpolated_gaps: tuple[LasCurveGap, ...] = ()

    @property
    def step_below_first_image_resolution(self) -> bool:
        return (
            self.first_image_resolution is not None
            and self.step_depth < self.first_image_resolution
        )

    @property
    def has_internal_gaps(self) -> bool:
        """Whether unresolved internal LAS gaps still require review."""
        return bool(self.internal_gaps)

    @property
    def missing_internal_sample_count(self) -> int:
        """Total number of unresolved internal LAS-grid samples."""
        return sum(gap.missing_sample_count for gap in self.internal_gaps)

    @property
    def has_interpolated_gaps(self) -> bool:
        """Whether small internal gaps will be interpolated in the LAS output."""
        return bool(self.interpolated_gaps)

    @property
    def interpolated_sample_count(self) -> int:
        """Total number of LAS-grid samples automatically interpolated."""
        return sum(gap.missing_sample_count for gap in self.interpolated_gaps)


@dataclass(frozen=True, slots=True)
class LasExportPlan:
    """Fully sampled, format-independent content required by a LAS writer."""

    coverage_start_depth: float
    coverage_stop_depth: float
    sampled: SampledProjectCurves
    columns: tuple[LasCurveColumn, ...]
    null_value: float = DEFAULT_LAS_NULL_VALUE
    first_image_resolution: float | None = None

    def __post_init__(self) -> None:
        if self.coverage_stop_depth < self.coverage_start_depth:
            raise LasExportError("Invalid digitized depth coverage.")
        if (
            tuple(column.curve_id for column in self.columns)
            != self.sampled.curve_ids
        ):
            raise LasExportError(
                "LAS curve definitions do not match the sampled columns."
            )
        mnemonics = [column.mnemonic.casefold() for column in self.columns]
        if len(mnemonics) != len(set(mnemonics)):
            raise LasExportError(
                "LAS curve mnemonics must remain unique after ASCII "
                "normalization."
            )
        if not isfinite(float(self.null_value)):
            raise LasExportError("LAS NULL must be a finite number.")
        if self.first_image_resolution is not None and (
            not isfinite(float(self.first_image_resolution))
            or float(self.first_image_resolution) <= 0.0
        ):
            raise LasExportError(
                "First-image vertical resolution must be positive."
            )

        null_value = float(self.null_value)
        for curve in self.sampled.curves:
            if any(
                value is not None and float(value) == null_value
                for value in curve.values
            ):
                raise LasExportError(
                    f"Curve {curve.curve_id!r} contains the configured LAS "
                    "NULL value; choose a different NULL sentinel."
                )

    @property
    def start_depth(self) -> float:
        """First exported depth row (LAS ``STRT``)."""
        return self.sampled.depths[0]

    @property
    def stop_depth(self) -> float:
        """Last exported depth row (LAS ``STOP``)."""
        return self.sampled.depths[-1]

    @property
    def step_depth(self) -> float:
        return self.sampled.definition.step_depth

    @property
    def depth_unit(self) -> DepthUnit:
        if self.sampled.depth_unit is None:
            raise LasExportError("LAS depth unit is unavailable.")
        return self.sampled.depth_unit

    @property
    def row_count(self) -> int:
        return len(self.sampled.depths)

    @property
    def step_below_first_image_resolution(self) -> bool:
        """Whether the requested STEP is smaller than one first-image pixel."""
        return (
            self.first_image_resolution is not None
            and self.step_depth < self.first_image_resolution
        )


def prepare_las_export(
    assemblies: Mapping[str, AssembledProjectCurve],
    curve_definitions: Sequence[CurveDefinition],
    *,
    step_depth: float,
    null_value: float = DEFAULT_LAS_NULL_VALUE,
    first_image_resolution: float | None = None,
    gap_qc_excluded_intervals: Sequence[tuple[float, float]] = (),
    gap_qc_excluded_intervals_by_curve: Mapping[
        str, Sequence[tuple[float, float]]
    ] | None = None,
    scale_transitions: Mapping[str, Sequence[LasScaleTransition]] | None = None,
) -> LasExportPlan:
    """Create a strict regular LAS grid from selected assembled curves.

    Raw coverage is derived only from the selected curves.  The exported grid
    is aligned to integer multiples of the user-selected STEP, as clarified by
    the LAS 2.0 specification.  Leading and trailing rows that are entirely
    NULL are removed. Approved scale/offset transitions are filled first.
    Remaining internal NULL runs of at most five LAS samples are then filled by
    linear interpolation, except inside overlap-guide exclusion intervals.
    """
    definitions, selected = _selected_las_curves(
        assemblies,
        curve_definitions,
    )
    preview = preview_las_export(
        selected,
        definitions,
        step_depth,
        first_image_resolution=first_image_resolution,
        gap_qc_excluded_intervals=gap_qc_excluded_intervals,
        gap_qc_excluded_intervals_by_curve=(
            gap_qc_excluded_intervals_by_curve or {}
        ),
        scale_transitions=scale_transitions,
    )

    try:
        sampled = sample_assembled_project_curves(
            selected,
            DepthSamplingDefinition(
                start_depth=preview.start_depth,
                stop_depth=preview.stop_depth,
                step_depth=step_depth,
            ),
        )
    except ScientificSamplingError as exc:
        raise LasExportError(str(exc)) from exc

    sampled = _apply_las_scale_transitions(
        sampled,
        scale_transitions or {},
    )
    sampled = _apply_las_small_gap_interpolation(
        sampled,
        excluded_intervals=gap_qc_excluded_intervals,
        excluded_intervals_by_curve=(
            gap_qc_excluded_intervals_by_curve or {}
        ),
    )
    sampled = _trim_all_null_edges(sampled)
    columns = tuple(
        LasCurveColumn.from_definition(curve)
        for curve in definitions
    )
    return LasExportPlan(
        coverage_start_depth=preview.coverage_start_depth,
        coverage_stop_depth=preview.coverage_stop_depth,
        sampled=sampled,
        columns=columns,
        null_value=float(null_value),
        first_image_resolution=first_image_resolution,
    )


def preview_las_export(
    assemblies: Mapping[str, AssembledProjectCurve],
    curve_definitions: Sequence[CurveDefinition],
    step_depth: float,
    *,
    first_image_resolution: float | None = None,
    gap_qc_excluded_intervals: Sequence[tuple[float, float]] = (),
    gap_qc_excluded_intervals_by_curve: Mapping[
        str, Sequence[tuple[float, float]]
    ] | None = None,
    scale_transitions: Mapping[str, Sequence[LasScaleTransition]] | None = None,
) -> LasExportPreview:
    """Calculate the exact exported bounds and row count without sampling.

    The first and last grid points are found from the non-destructive source
    assignments and the real section depth intervals. Interior gaps remain
    rows in the LAS grid but require no PCHIP evaluation for this preview.
    """
    _definitions, selected = _selected_las_curves(
        assemblies,
        curve_definitions,
    )
    columns = tuple(
        LasCurveColumn.from_definition(curve)
        for curve in _definitions
    )
    mnemonics = [column.mnemonic.casefold() for column in columns]
    if len(mnemonics) != len(set(mnemonics)):
        raise LasExportError(
            "LAS curve mnemonics must remain unique after ASCII "
            "normalization."
        )
    units = {
        assembly.unit
        for assembly in selected.values()
        if assembly.unit is not None
    }
    if len(units) != 1:
        raise LasExportError(
            "All selected LAS curves must use one depth unit."
        )
    depth_unit = next(iter(units))

    coverage_start = min(
        float(assembly.minimum_depth)
        for assembly in selected.values()
        if assembly.minimum_depth is not None
    )
    coverage_stop = max(
        float(assembly.maximum_depth)
        for assembly in selected.values()
        if assembly.maximum_depth is not None
    )
    aligned_start, aligned_stop = _aligned_depth_bounds(
        coverage_start,
        coverage_stop,
        step_depth,
    )
    start_depth, stop_depth = _supported_grid_bounds(
        selected.values(),
        aligned_start,
        aligned_stop,
        step_depth,
    )
    row_count = _regular_row_count(
        start_depth,
        stop_depth,
        step_depth,
    )

    if first_image_resolution is not None and (
        not isfinite(float(first_image_resolution))
        or float(first_image_resolution) <= 0.0
    ):
        raise LasExportError(
            "First-image vertical resolution must be positive."
        )
    internal_gaps, interpolated_gaps = _preview_internal_grid_gaps(
        _definitions,
        selected,
        start_depth=start_depth,
        stop_depth=stop_depth,
        step_depth=step_depth,
        excluded_intervals=gap_qc_excluded_intervals,
        excluded_intervals_by_curve=(
            gap_qc_excluded_intervals_by_curve or {}
        ),
        scale_transitions=scale_transitions or {},
    )
    return LasExportPreview(
        coverage_start_depth=coverage_start,
        coverage_stop_depth=coverage_stop,
        start_depth=start_depth,
        stop_depth=stop_depth,
        step_depth=float(step_depth),
        row_count=row_count,
        depth_unit=depth_unit,
        first_image_resolution=(
            None
            if first_image_resolution is None
            else float(first_image_resolution)
        ),
        internal_gaps=internal_gaps,
        interpolated_gaps=interpolated_gaps,
    )



def _eligible_scale_transitions_for_curve(
    scale_transitions: Mapping[str, Sequence[LasScaleTransition]],
    curve_id: str,
) -> tuple[LasScaleTransition, ...]:
    """Return validated eligible transitions belonging to one LAS curve."""
    result: list[LasScaleTransition] = []
    for transition in scale_transitions.get(curve_id, ()):
        if transition.curve_id != curve_id:
            raise LasExportError(
                "LAS scale-transition mapping does not match its curve id."
            )
        if transition.eligible:
            result.append(transition)
    return tuple(result)


def _preview_internal_grid_gaps(
    definitions: Sequence[CurveDefinition],
    selected: Mapping[str, AssembledProjectCurve],
    *,
    start_depth: float,
    stop_depth: float,
    step_depth: float,
    excluded_intervals: Sequence[tuple[float, float]] = (),
    excluded_intervals_by_curve: Mapping[
        str, Sequence[tuple[float, float]]
    ] | None = None,
    scale_transitions: Mapping[str, Sequence[LasScaleTransition]] | None = None,
) -> tuple[tuple[LasCurveGap, ...], tuple[LasCurveGap, ...]]:
    """Classify STEP-aware internal gaps for LAS QC and interpolation.

    The first returned tuple contains unresolved gaps that still require review.
    The second contains small internal runs (at most five current LAS samples)
    that the final LAS plan will fill by linear interpolation.

    ``excluded_intervals`` is the legacy project-wide exclusion list.
    ``excluded_intervals_by_curve`` contains the exact red-guide domains for each
    curve-specific image sequence. Both are excluded from gap warnings and generic
    small-gap interpolation. Scale/offset transitions already approved for
    LAS-only interpolation are counted as supported before the generic rule is
    applied. Saved digitization and curve assembly are never modified here.
    """
    grid_start = Decimal(str(start_depth))
    grid_stop = Decimal(str(stop_depth))
    step = Decimal(str(step_depth))
    gaps: list[LasCurveGap] = []
    interpolated: list[LasCurveGap] = []

    def _grid_index_range(
        low_depth: float | Decimal,
        high_depth: float | Decimal,
    ) -> tuple[int, int] | None:
        low = max(Decimal(str(low_depth)), grid_start)
        high = min(Decimal(str(high_depth)), grid_stop)
        if high < low:
            return None
        first_index = int(
            ((low - grid_start) / step).to_integral_value(
                rounding=ROUND_CEILING
            )
        )
        last_index = int(
            ((high - grid_start) / step).to_integral_value(
                rounding=ROUND_FLOOR
            )
        )
        if first_index > last_index:
            return None
        return first_index, last_index

    def _merge_ranges(ranges: list[tuple[int, int]]) -> list[list[int]]:
        if not ranges:
            return []
        ranges.sort()
        merged: list[list[int]] = []
        for first_index, last_index in ranges:
            if not merged or first_index > merged[-1][1] + 1:
                merged.append([first_index, last_index])
            else:
                merged[-1][1] = max(merged[-1][1], last_index)
        return merged

    def _subtract_ranges(
        first_index: int,
        last_index: int,
        excluded: Sequence[Sequence[int]],
    ) -> list[tuple[int, int]]:
        """Return inclusive index runs not covered by excluded intervals."""
        remaining: list[tuple[int, int]] = []
        cursor = first_index
        for excluded_first, excluded_last in excluded:
            if excluded_last < cursor:
                continue
            if excluded_first > last_index:
                break
            if excluded_first > cursor:
                remaining.append((cursor, min(last_index, excluded_first - 1)))
            cursor = max(cursor, excluded_last + 1)
            if cursor > last_index:
                break
        if cursor <= last_index:
            remaining.append((cursor, last_index))
        return remaining

    def _intersects_ranges(
        first_index: int,
        last_index: int,
        ranges: Sequence[Sequence[int]],
    ) -> bool:
        return any(
            not (last_index < range_first or first_index > range_last)
            for range_first, range_last in ranges
        )

    def _gap_from_indices(
        curve: CurveDefinition,
        first_index: int,
        last_index: int,
        curve_first_index: int,
        curve_last_index: int,
    ) -> LasCurveGap:
        missing_start = grid_start + first_index * step
        missing_stop = grid_start + last_index * step
        previous_index = max(curve_first_index, first_index - 1)
        next_index = min(curve_last_index, last_index + 1)
        previous_depth = grid_start + previous_index * step
        next_depth = grid_start + next_index * step
        return LasCurveGap(
            curve_id=curve.curve_id,
            mnemonic=curve.mnemonic,
            previous_depth=float(previous_depth),
            next_depth=float(next_depth),
            missing_start_depth=float(missing_start),
            missing_stop_depth=float(missing_stop),
            missing_sample_count=(last_index - first_index + 1),
        )

    def _mapped_exclusion_ranges(
        intervals: Sequence[tuple[float, float]],
    ) -> list[list[int]]:
        """Validate depth exclusions and map them to merged LAS-grid ranges."""
        mapped: list[tuple[int, int]] = []
        for supplied_start, supplied_stop in intervals:
            try:
                interval_start = float(supplied_start)
                interval_stop = float(supplied_stop)
            except (TypeError, ValueError, OverflowError) as exc:
                raise LasExportError(
                    "Gap-QC exclusion depths must be finite numbers."
                ) from exc
            if not isfinite(interval_start) or not isfinite(interval_stop):
                raise LasExportError(
                    "Gap-QC exclusion depths must be finite numbers."
                )
            interval_start, interval_stop = sorted(
                (interval_start, interval_stop)
            )
            index_range = _grid_index_range(interval_start, interval_stop)
            if index_range is not None:
                mapped.append(index_range)
        return _merge_ranges(mapped)

    global_ignored = _mapped_exclusion_ranges(excluded_intervals)
    specific_exclusions = excluded_intervals_by_curve or {}

    for curve in definitions:
        curve_specific = _mapped_exclusion_ranges(
            specific_exclusions.get(curve.curve_id, ())
        )
        ignored = _merge_ranges(
            [tuple(item) for item in global_ignored]
            + [tuple(item) for item in curve_specific]
        )
        assembly = selected[curve.curve_id]
        if assembly.minimum_depth is None or assembly.maximum_depth is None:
            continue

        curve_grid_range = _grid_index_range(
            assembly.minimum_depth,
            assembly.maximum_depth,
        )
        if curve_grid_range is None:
            continue
        curve_first_index, curve_last_index = curve_grid_range

        covered_ranges: list[tuple[int, int]] = []
        for source in assembly.sources:
            for section_start, section_stop in source.section_depth_ranges:
                index_range = _grid_index_range(section_start, section_stop)
                if index_range is not None:
                    covered_ranges.append(index_range)

        # Approved scale/offset transitions are already genuine LAS support.
        for transition in _eligible_scale_transitions_for_curve(
            scale_transitions or {},
            curve.curve_id,
        ):
            index_range = _grid_index_range(
                transition.minimum_depth,
                transition.maximum_depth,
            )
            if index_range is not None:
                covered_ranges.append(index_range)

        covered = _merge_ranges(covered_ranges)
        if not covered:
            continue

        cursor = curve_first_index
        candidate_missing: list[tuple[int, int]] = []
        for covered_first, covered_last in covered:
            if covered_last < curve_first_index:
                continue
            if covered_first > curve_last_index:
                break
            covered_first = max(covered_first, curve_first_index)
            covered_last = min(covered_last, curve_last_index)
            if covered_first > cursor:
                candidate_missing.append((cursor, covered_first - 1))
            cursor = max(cursor, covered_last + 1)
        if cursor <= curve_last_index:
            candidate_missing.append((cursor, curve_last_index))

        for missing_first, missing_last in candidate_missing:
            missing_count = missing_last - missing_first + 1
            has_two_grid_neighbours = (
                missing_first > curve_first_index
                and missing_last < curve_last_index
            )
            can_interpolate = (
                has_two_grid_neighbours
                and missing_count <= MAX_LAS_AUTOMATIC_GAP_SAMPLES
                and not _intersects_ranges(missing_first, missing_last, ignored)
            )
            if can_interpolate:
                interpolated.append(
                    _gap_from_indices(
                        curve,
                        missing_first,
                        missing_last,
                        curve_first_index,
                        curve_last_index,
                    )
                )
                continue

            # Overlap-guide samples remain outside QC. If a larger NULL run crosses
            # an excluded interval, report only the samples that remain in QC scope;
            # never treat those partial fragments as candidates for interpolation.
            for qc_first, qc_last in _subtract_ranges(
                missing_first,
                missing_last,
                ignored,
            ):
                if qc_first > qc_last:
                    continue
                gaps.append(
                    _gap_from_indices(
                        curve,
                        qc_first,
                        qc_last,
                        curve_first_index,
                        curve_last_index,
                    )
                )

    return tuple(gaps), tuple(interpolated)


def _apply_las_scale_transitions(
    sampled: SampledProjectCurves,
    scale_transitions: Mapping[str, Sequence[LasScaleTransition]],
) -> SampledProjectCurves:
    """Fill NULL LAS samples only across approved scale/offset seams.

    Existing sampled values always win.  This function never changes saved
    digitization or curve assembly; it creates a new sampled value matrix used
    only by LAS preview serialization/export.
    """
    if not scale_transitions:
        return sampled

    rebuilt: list[SampledCurve] = []
    changed = False
    for curve in sampled.curves:
        transitions = _eligible_scale_transitions_for_curve(
            scale_transitions,
            curve.curve_id,
        )
        if not transitions:
            rebuilt.append(curve)
            continue

        values = list(curve.values)
        curve_changed = False
        for transition in transitions:
            for index, depth in enumerate(sampled.depths):
                if values[index] is not None:
                    continue
                value = transition.value_at_depth(depth)
                if value is None:
                    continue
                values[index] = value
                curve_changed = True

        if curve_changed:
            rebuilt.append(
                SampledCurve(
                    curve_id=curve.curve_id,
                    values=tuple(values),
                )
            )
            changed = True
        else:
            rebuilt.append(curve)

    if not changed:
        return sampled
    return SampledProjectCurves(
        definition=sampled.definition,
        depths=sampled.depths,
        depth_unit=sampled.depth_unit,
        curves=tuple(rebuilt),
    )



def _apply_las_small_gap_interpolation(
    sampled: SampledProjectCurves,
    *,
    excluded_intervals: Sequence[tuple[float, float]] = (),
    excluded_intervals_by_curve: Mapping[
        str, Sequence[tuple[float, float]]
    ] | None = None,
) -> SampledProjectCurves:
    """Linearly fill only short internal NULL runs in the LAS value matrix.

    The rule is intentionally defined in LAS-grid samples, not physical length:
    at most ``MAX_LAS_AUTOMATIC_GAP_SAMPLES`` consecutive NULL rows may be
    filled. A valid sample must exist immediately before and after the run.
    Samples inside displayed overlap-guide exclusion intervals for that curve are
    never filled by this generic rule. Existing real or scale-transition values
    always win.
    """

    def _normalize_exclusions(
        intervals: Sequence[tuple[float, float]],
    ) -> list[tuple[float, float]]:
        normalized: list[tuple[float, float]] = []
        for supplied_start, supplied_stop in intervals:
            try:
                interval_start = float(supplied_start)
                interval_stop = float(supplied_stop)
            except (TypeError, ValueError, OverflowError) as exc:
                raise LasExportError(
                    "Gap-QC exclusion depths must be finite numbers."
                ) from exc
            if not isfinite(interval_start) or not isfinite(interval_stop):
                raise LasExportError(
                    "Gap-QC exclusion depths must be finite numbers."
                )
            normalized.append(
                tuple(sorted((interval_start, interval_stop)))
            )
        return normalized

    global_exclusions = _normalize_exclusions(excluded_intervals)
    specific_exclusions = excluded_intervals_by_curve or {}

    rebuilt: list[SampledCurve] = []
    changed = False
    for curve in sampled.curves:
        normalized_exclusions = (
            global_exclusions
            + _normalize_exclusions(
                specific_exclusions.get(curve.curve_id, ())
            )
        )

        def _run_hits_exclusion(first_index: int, last_index: int) -> bool:
            if not normalized_exclusions:
                return False
            for index in range(first_index, last_index + 1):
                depth = sampled.depths[index]
                if any(
                    start <= depth <= stop
                    for start, stop in normalized_exclusions
                ):
                    return True
            return False
        values = list(curve.values)
        curve_changed = False
        index = 0
        while index < len(values):
            if values[index] is not None:
                index += 1
                continue

            first_missing = index
            while index < len(values) and values[index] is None:
                index += 1
            last_missing = index - 1
            missing_count = last_missing - first_missing + 1

            # Leading/trailing NULLs are never interpolated, and neither are runs
            # larger than the five-sample LAS-domain threshold.
            if (
                first_missing == 0
                or last_missing == len(values) - 1
                or missing_count > MAX_LAS_AUTOMATIC_GAP_SAMPLES
                or _run_hits_exclusion(first_missing, last_missing)
            ):
                continue

            previous_value = values[first_missing - 1]
            next_value = values[last_missing + 1]
            if previous_value is None or next_value is None:
                continue

            previous_depth = sampled.depths[first_missing - 1]
            next_depth = sampled.depths[last_missing + 1]
            depth_span = next_depth - previous_depth
            if abs(depth_span) <= 1e-15:
                continue

            for fill_index in range(first_missing, last_missing + 1):
                depth = sampled.depths[fill_index]
                ratio = (depth - previous_depth) / depth_span
                value = float(previous_value) + ratio * (
                    float(next_value) - float(previous_value)
                )
                if not isfinite(value):
                    raise LasExportError(
                        "Automatic LAS gap interpolation produced a non-finite value."
                    )
                values[fill_index] = value
            curve_changed = True

        if curve_changed:
            rebuilt.append(
                SampledCurve(curve_id=curve.curve_id, values=tuple(values))
            )
            changed = True
        else:
            rebuilt.append(curve)

    if not changed:
        return sampled
    return SampledProjectCurves(
        definition=sampled.definition,
        depths=sampled.depths,
        depth_unit=sampled.depth_unit,
        curves=tuple(rebuilt),
    )

def serialize_las_2(
    plan: LasExportPlan,
    metadata: LasWellMetadata,
) -> str:
    """Serialize one unwrapped LAS 2.0 file using mandatory CRLF lines."""
    return "\r\n".join(_iter_las_lines(plan, metadata))


def write_las_2(
    path: Path,
    plan: LasExportPlan,
    metadata: LasWellMetadata,
) -> Path:
    """Atomically write a LAS 2.0 file and return its resolved path."""
    target = Path(path).expanduser().resolve()
    if target.suffix.lower() != ".las":
        raise LasExportError("LAS export path must use the .las extension.")

    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(
        f".{target.name}.{uuid4().hex}.tmp"
    )
    try:
        with temporary.open(
            "w",
            encoding="ascii",
            errors="strict",
            newline="",
        ) as stream:
            first = True
            for line in _iter_las_lines(plan, metadata):
                if not first:
                    stream.write("\r\n")
                stream.write(line)
                first = False
            stream.flush()
        temporary.replace(target)
    except (OSError, UnicodeError) as exc:
        try:
            temporary.unlink(missing_ok=True)
        finally:
            raise LasExportError(f"Cannot write LAS file: {exc}") from exc
    return target


def _selected_las_curves(
    assemblies: Mapping[str, AssembledProjectCurve],
    curve_definitions: Sequence[CurveDefinition],
) -> tuple[
    tuple[CurveDefinition, ...],
    dict[str, AssembledProjectCurve],
]:
    definitions = tuple(curve_definitions)
    if not definitions:
        raise LasExportError("Select at least one digitized curve for LAS.")

    curve_ids = [curve.curve_id for curve in definitions]
    if len(curve_ids) != len(set(curve_ids)):
        raise LasExportError("A curve can be selected only once for LAS.")

    selected: dict[str, AssembledProjectCurve] = {}
    for curve in definitions:
        try:
            assembly = assemblies[curve.curve_id]
        except KeyError as exc:
            raise LasExportError(
                f"Missing assembled curve {curve.mnemonic!r}."
            ) from exc
        if not assembly.sources:
            raise LasExportError(
                f"Curve {curve.mnemonic!r} has no usable digitized data."
            )
        selected[curve.curve_id] = assembly
    return definitions, selected


def _supported_grid_bounds(
    assemblies: Iterable[AssembledProjectCurve],
    aligned_start: float,
    aligned_stop: float,
    step_depth: float,
) -> tuple[float, float]:
    """Find outer grid samples intersecting assigned section support."""
    start = Decimal(str(aligned_start))
    stop = Decimal(str(aligned_stop))
    step = Decimal(str(step_depth))
    first_candidates: list[Decimal] = []
    last_candidates: list[Decimal] = []

    for assembly in assemblies:
        for assignment in assembly.assignments:
            for section_start, section_stop in (
                assignment.source.section_depth_ranges
            ):
                low = max(
                    Decimal(str(section_start)),
                    Decimal(str(assignment.minimum_depth)),
                    start,
                )
                high = min(
                    Decimal(str(section_stop)),
                    Decimal(str(assignment.maximum_depth)),
                    stop,
                )
                if high < low:
                    continue
                first = (
                    (low / step).to_integral_value(
                        rounding=ROUND_CEILING
                    )
                    * step
                )
                assignment_start = Decimal(
                    str(assignment.minimum_depth)
                )
                if (
                    not assignment.minimum_inclusive
                    and low == assignment_start
                    and first == assignment_start
                ):
                    first += step
                last = (
                    (high / step).to_integral_value(
                        rounding=ROUND_FLOOR
                    )
                    * step
                )
                if first <= last:
                    first_candidates.append(first)
                    last_candidates.append(last)

    if not first_candidates:
        raise LasExportError(
            "The selected STEP does not intersect any selected curve."
        )
    return float(min(first_candidates)), float(max(last_candidates))


def _regular_row_count(
    start_depth: float,
    stop_depth: float,
    step_depth: float,
) -> int:
    start = Decimal(str(start_depth))
    stop = Decimal(str(stop_depth))
    step = Decimal(str(step_depth))
    try:
        intervals = int(
            ((stop - start) / step).to_integral_value(
                rounding=ROUND_FLOOR
            )
        )
    except (InvalidOperation, ValueError, OverflowError) as exc:
        raise LasExportError("Cannot calculate the LAS row count.") from exc
    row_count = intervals + 1
    if row_count > MAX_SAMPLING_ROWS:
        raise LasExportError(
            "Requested LAS grid is too large; increase STEP."
        )
    return row_count


def _aligned_depth_bounds(
    coverage_start: float,
    coverage_stop: float,
    step_depth: float,
) -> tuple[float, float]:
    try:
        start = Decimal(str(coverage_start))
        stop = Decimal(str(coverage_stop))
        step = Decimal(str(step_depth))
    except (InvalidOperation, ValueError) as exc:
        raise LasExportError("LAS depths and STEP must be finite numbers.") from exc

    if not all(value.is_finite() for value in (start, stop, step)):
        raise LasExportError("LAS depths and STEP must be finite numbers.")
    if step <= 0:
        raise LasExportError("LAS STEP must be greater than zero.")

    aligned_start = (
        (start / step).to_integral_value(rounding=ROUND_CEILING) * step
    )
    aligned_stop = (
        (stop / step).to_integral_value(rounding=ROUND_FLOOR) * step
    )
    if aligned_stop < aligned_start:
        raise LasExportError(
            "The selected STEP does not place any regular sample inside the "
            "digitized depth coverage. Choose a smaller STEP."
        )
    return float(aligned_start), float(aligned_stop)


def _trim_all_null_edges(
    sampled: SampledProjectCurves,
) -> SampledProjectCurves:
    row_has_value = [
        any(curve.values[index] is not None for curve in sampled.curves)
        for index in range(len(sampled.depths))
    ]
    try:
        first = row_has_value.index(True)
    except ValueError as exc:
        raise LasExportError(
            "The selected STEP does not intersect any selected curve."
        ) from exc
    last = len(row_has_value) - 1 - row_has_value[::-1].index(True)
    if first == 0 and last == len(sampled.depths) - 1:
        return sampled

    depths = sampled.depths[first : last + 1]
    return SampledProjectCurves(
        definition=DepthSamplingDefinition(
            start_depth=depths[0],
            stop_depth=depths[-1],
            step_depth=sampled.definition.step_depth,
        ),
        depths=depths,
        depth_unit=sampled.depth_unit,
        curves=tuple(
            SampledCurve(
                curve_id=curve.curve_id,
                values=curve.values[first : last + 1],
            )
            for curve in sampled.curves
        ),
    )


def _iter_las_lines(
    plan: LasExportPlan,
    metadata: LasWellMetadata,
):
    depth_unit = plan.depth_unit.value
    null_text = _format_number(plan.null_value)

    yield "~Version Information"
    yield "VERS. 2.0 : CWLS LOG ASCII STANDARD - VERSION 2.0"
    yield "WRAP. NO : ONE LINE PER DEPTH STEP"
    yield "~Well Information"
    yield f"STRT.{depth_unit} {_format_number(plan.start_depth)} : START DEPTH"
    yield f"STOP.{depth_unit} {_format_number(plan.stop_depth)} : STOP DEPTH"
    yield f"STEP.{depth_unit} {_format_number(plan.step_depth)} : STEP"
    yield f"NULL. {null_text} : NULL VALUE"
    yield _header_line("COMP", "", metadata.company, "COMPANY")
    yield _header_line("WELL", "", metadata.well, "WELL")
    yield _header_line("FLD", "", metadata.field, "FIELD")
    yield _header_line("LOC", "", metadata.location, "LOCATION")

    if metadata.province:
        yield _header_line("PROV", "", metadata.province, "PROVINCE")
    else:
        yield _header_line("CNTY", "", metadata.county, "COUNTY")
        yield _header_line("STAT", "", metadata.state, "STATE")
        yield _header_line("CTRY", "", metadata.country, "COUNTRY")

    yield _header_line(
        "SRVC",
        "",
        metadata.service_company,
        "SERVICE COMPANY",
    )
    yield _header_line("DATE", "", metadata.log_date, "LOG DATE")
    if metadata.unique_well_id:
        yield _header_line(
            "UWI",
            "",
            metadata.unique_well_id,
            "UNIQUE WELL ID",
        )
        if metadata.api_number:
            yield _header_line(
                "API",
                "",
                metadata.api_number,
                "API NUMBER",
            )
    elif metadata.api_number:
        yield _header_line("API", "", metadata.api_number, "API NUMBER")
    else:
        yield _header_line("UWI", "", "", "UNIQUE WELL ID")
    if metadata.licence_number:
        yield _header_line(
            "LIC",
            "",
            metadata.licence_number,
            "LICENCE NUMBER",
        )

    yield "~Curve Information"
    yield f"DEPT.{depth_unit} : DEPTH"
    for column in plan.columns:
        yield _header_line(
            column.mnemonic,
            column.unit,
            "",
            column.description,
        )

    other = _ascii_text(metadata.other_information)
    yield "~Other Information"
    yield (
        "Curves digitized from scanned well logs with "
        f"LogDigitizer {__version__}."
    )
    if other:
        yield other

    yield "~ASCII Log Data"
    for index, depth in enumerate(plan.sampled.depths):
        values = [_format_number(depth)]
        values.extend(
            null_text
            if curve.values[index] is None
            else _format_curve_value(curve.values[index])
            for curve in plan.sampled.curves
        )
        yield " ".join(
            f"{value:>16}"
            for value in values
        )


def _header_line(
    mnemonic: str,
    unit: str,
    value: str,
    description: str,
) -> str:
    return (
        f"{_las_token(mnemonic, kind='LAS mnemonic')}."
        f"{_las_unit(unit)} {_ascii_text(value)} : "
        f"{_las_description(description)}"
    )


def _ascii_text(value: Any) -> str:
    text = "" if value is None else str(value)
    text = " ".join(text.replace("\r", " ").replace("\n", " ").split())
    return (
        normalize("NFKD", text)
        .encode("ascii", "ignore")
        .decode("ascii")
        .strip()
    )


def _las_token(value: Any, *, kind: str) -> str:
    token = _ascii_text(value)
    if not token:
        raise LasExportError(f"{kind} cannot be empty.")
    if any(character in token for character in (" ", ".", ":")):
        raise LasExportError(
            f"{kind} {token!r} cannot contain spaces, dots or colons."
        )
    return token


def _las_unit(value: Any) -> str:
    unit = _ascii_text(value)
    if any(character in unit for character in (" ", ":")):
        raise LasExportError(
            f"LAS unit {unit!r} cannot contain spaces or colons."
        )
    return unit


def _las_description(value: Any) -> str:
    # LAS uses the final colon on a header line as the description delimiter.
    return _ascii_text(value).replace(":", ";")


def _format_number(value: float) -> str:
    number = float(value)
    if not isfinite(number):
        raise LasExportError("LAS numeric values must be finite.")
    if number == 0.0:
        return "0"
    try:
        return format(Decimal(str(number)), "f")
    except InvalidOperation as exc:
        raise LasExportError("Cannot format LAS numeric value.") from exc


def _format_curve_value(value: float) -> str:
    """Format one sampled curve value with four decimal places.

    Sampling and curve assembly retain their full floating-point precision;
    rounding is applied only to the textual LAS representation.
    """
    number = float(value)
    if not isfinite(number):
        raise LasExportError("LAS numeric values must be finite.")
    try:
        rounded = Decimal(str(number)).quantize(
            Decimal("0.0001"),
            rounding=ROUND_HALF_UP,
        )
    except InvalidOperation as exc:
        raise LasExportError("Cannot format LAS curve value.") from exc
    if rounded == 0:
        rounded = abs(rounded)
    return format(rounded, ".4f")
