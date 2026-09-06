"""Regular scientific sampling of assembled project curves.

This module creates the common depth axis required by LAS without formatting
or writing a LAS file.  Missing geometry remains ``None`` so a later exporter
can substitute its configured NULL value without losing scientific meaning.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_FLOOR
from math import isfinite

from logdigitizer.domain.calibration import DepthUnit
from logdigitizer.processing.curve_assembly import (
    AssembledProjectCurve,
    CurveAssemblyError,
)


class ScientificSamplingError(ValueError):
    """Raised when a regular project sampling grid cannot be produced."""


MAX_SAMPLING_ROWS = 10_000_000


@dataclass(frozen=True, slots=True)
class DepthSamplingDefinition:
    """Ascending common depth grid defined by START, STOP and STEP."""

    start_depth: float
    stop_depth: float
    step_depth: float

    def __post_init__(self) -> None:
        try:
            start = float(self.start_depth)
            stop = float(self.stop_depth)
            step = float(self.step_depth)
        except (TypeError, ValueError) as exc:
            raise ScientificSamplingError(
                "Sampling depths must be finite numbers."
            ) from exc

        if not all(isfinite(value) for value in (start, stop, step)):
            raise ScientificSamplingError(
                "Sampling depths must be finite numbers."
            )
        if start < 0.0 or stop < 0.0:
            raise ScientificSamplingError(
                "Sampling depths cannot be negative."
            )
        if stop < start:
            raise ScientificSamplingError(
                "STOP depth must be greater than or equal to START depth."
            )
        if step <= 0.0:
            raise ScientificSamplingError(
                "Sampling STEP must be greater than zero."
            )

        object.__setattr__(self, "start_depth", start)
        object.__setattr__(self, "stop_depth", stop)
        object.__setattr__(self, "step_depth", step)

    def depths(self) -> tuple[float, ...]:
        """Generate ``START + i * STEP`` without cumulative float drift."""
        try:
            start = Decimal(str(self.start_depth))
            stop = Decimal(str(self.stop_depth))
            step = Decimal(str(self.step_depth))
            interval_count = int(
                ((stop - start) / step).to_integral_value(
                    rounding=ROUND_FLOOR
                )
            )
        except (InvalidOperation, ValueError, OverflowError) as exc:
            raise ScientificSamplingError(
                "Cannot construct the requested depth grid."
            ) from exc

        sample_count = interval_count + 1
        if sample_count > MAX_SAMPLING_ROWS:
            raise ScientificSamplingError(
                "Requested depth grid is too large; increase STEP or reduce "
                "the START–STOP interval."
            )

        return tuple(
            float(start + step * index)
            for index in range(sample_count)
        )


@dataclass(frozen=True, slots=True)
class SampledCurve:
    """Values of one project curve on the common sampling grid."""

    curve_id: str
    values: tuple[float | None, ...]

    def __post_init__(self) -> None:
        normalized_id = str(self.curve_id).strip()
        if not normalized_id:
            raise ScientificSamplingError("Sampled curve id cannot be empty.")
        for value in self.values:
            if value is not None and not isfinite(float(value)):
                raise ScientificSamplingError(
                    f"Curve {normalized_id!r} contains a non-finite value."
                )
        object.__setattr__(self, "curve_id", normalized_id)


@dataclass(frozen=True, slots=True)
class SampledProjectCurves:
    """LAS-ready scientific value matrix, still preserving ``None`` gaps."""

    definition: DepthSamplingDefinition
    depths: tuple[float, ...]
    depth_unit: DepthUnit | None
    curves: tuple[SampledCurve, ...]

    def __post_init__(self) -> None:
        if not self.depths:
            raise ScientificSamplingError("Sampling grid cannot be empty.")
        expected = len(self.depths)
        curve_ids = [curve.curve_id for curve in self.curves]
        if len(curve_ids) != len(set(curve_ids)):
            raise ScientificSamplingError(
                "Sampling result cannot contain duplicate curve ids."
            )
        for curve in self.curves:
            if len(curve.values) != expected:
                raise ScientificSamplingError(
                    f"Curve {curve.curve_id!r} does not match the depth grid."
                )

    @property
    def actual_stop_depth(self) -> float:
        """Last generated row; it never exceeds the requested STOP value."""
        return self.depths[-1]

    @property
    def curve_ids(self) -> tuple[str, ...]:
        return tuple(curve.curve_id for curve in self.curves)

    def values_for(self, curve_id: str) -> tuple[float | None, ...]:
        normalized = str(curve_id).strip()
        for curve in self.curves:
            if curve.curve_id == normalized:
                return curve.values
        raise KeyError(normalized)

    def rows(self) -> tuple[tuple[float | None, ...], ...]:
        """Return rows ordered as ``DEPTH, curve 1, curve 2, ...``."""
        return tuple(
            (
                depth,
                *(curve.values[index] for curve in self.curves),
            )
            for index, depth in enumerate(self.depths)
        )


def sample_assembled_project_curves(
    assemblies: Mapping[str, AssembledProjectCurve],
    definition: DepthSamplingDefinition,
) -> SampledProjectCurves:
    """Evaluate several independently assembled curves on one depth axis."""
    if not assemblies:
        raise ScientificSamplingError(
            "Select at least one project curve for sampling."
        )

    normalized: list[tuple[str, AssembledProjectCurve]] = []
    for supplied_id, assembly in assemblies.items():
        curve_id = str(supplied_id).strip()
        if not curve_id:
            raise ScientificSamplingError("Curve id cannot be empty.")
        if assembly.curve_id != curve_id:
            raise ScientificSamplingError(
                f"Assembly id {assembly.curve_id!r} does not match "
                f"sampling id {curve_id!r}."
            )
        normalized.append((curve_id, assembly))

    units = {
        assembly.unit
        for _curve_id, assembly in normalized
        if assembly.unit is not None
    }
    if len(units) > 1:
        raise ScientificSamplingError(
            "All curves in one LAS sampling grid must use one depth unit."
        )

    depths = definition.depths()
    sampled: list[SampledCurve] = []
    for curve_id, assembly in normalized:
        try:
            values = assembly.sample(depths)
        except CurveAssemblyError as exc:
            raise ScientificSamplingError(
                f"Cannot sample curve {curve_id!r}: {exc}"
            ) from exc
        sampled.append(SampledCurve(curve_id=curve_id, values=values))

    return SampledProjectCurves(
        definition=definition,
        depths=depths,
        depth_unit=next(iter(units), None),
        curves=tuple(sampled),
    )
