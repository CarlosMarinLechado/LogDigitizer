"""Domain models for log calibration.

Coordinates are expressed in rectified-image pixels, canonical from Calibration
onward. No GUI dependencies are allowed in this module.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from math import isfinite
from typing import Any
from uuid import uuid4

from .curve_display import (
    curve_display_color_for_id,
    normalize_curve_display_color,
)


class CalibrationError(ValueError):
    """Base error for invalid calibration definitions."""


class DepthUnit(str, Enum):
    """Supported vertical-axis units."""

    METERS = "M"
    FEET = "FT"


class ScaleType(str, Enum):
    """Supported horizontal calibration scales."""

    LINEAR = "linear"
    LOGARITHMIC = "logarithmic"


DEFAULT_HORIZONTAL_SCALE_ID = "default"


@dataclass(frozen=True, slots=True, order=True)
class DepthCalibrationPoint:
    """One vertical reference point in rectified-image coordinates."""

    pixel_y: float
    depth: float

    def __post_init__(self) -> None:
        if not isfinite(self.pixel_y) or not isfinite(self.depth):
            raise CalibrationError(
                "Depth calibration coordinates must be finite numbers."
            )
        if self.depth < 0:
            raise CalibrationError(
                "Depth calibration points cannot have negative depths."
            )

    def to_dict(self) -> dict[str, float]:
        return {
            "pixel_y": float(self.pixel_y),
            "depth": float(self.depth),
        }

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any],
    ) -> "DepthCalibrationPoint":
        return cls(
            float(payload["pixel_y"]),
            float(payload["depth"]),
        )


@dataclass(frozen=True, slots=True)
class DepthCalibration:
    """Piecewise-linear vertical calibration.

    Points are stored sorted by ``pixel_y``. At least two points are required,
    ``pixel_y`` values must be unique and depths must be strictly monotonic.

    Conversion policy:
    - between control points: piecewise-linear interpolation;
    - before the first ``pixel_y``: extrapolate using the first segment;
    - after the last ``pixel_y``: extrapolate using the last segment;
    - any conversion that would produce depth < 0 is forbidden.
    """

    points: tuple[DepthCalibrationPoint, ...]
    unit: DepthUnit = DepthUnit.METERS

    def __post_init__(self) -> None:
        normalized = tuple(
            sorted(
                (
                    point
                    if isinstance(point, DepthCalibrationPoint)
                    else DepthCalibrationPoint(*point)
                    for point in self.points
                ),
                key=lambda point: point.pixel_y,
            )
        )
        object.__setattr__(self, "points", normalized)

        if not isinstance(self.unit, DepthUnit):
            object.__setattr__(
                self,
                "unit",
                DepthUnit(str(self.unit).upper()),
            )

        if len(normalized) < 2:
            raise CalibrationError(
                "Depth calibration requires at least two reference points."
            )

        pixels = [point.pixel_y for point in normalized]
        if len(set(pixels)) != len(pixels):
            raise CalibrationError(
                "Depth calibration cannot contain duplicate pixel_y values."
            )

        depths = [point.depth for point in normalized]
        deltas = [
            second - first
            for first, second in zip(depths, depths[1:])
        ]
        if any(delta == 0 for delta in deltas):
            raise CalibrationError(
                "Depth values must be strictly monotonic."
            )
        if not (
            all(delta > 0 for delta in deltas)
            or all(delta < 0 for delta in deltas)
        ):
            raise CalibrationError(
                "Depth values must be strictly monotonic after sorting "
                "by pixel_y."
            )

    @property
    def minimum_depth(self) -> float:
        return min(point.depth for point in self.points)

    @property
    def maximum_depth(self) -> float:
        return max(point.depth for point in self.points)

    @property
    def depth_increases_with_y(self) -> bool:
        return self.points[-1].depth > self.points[0].depth

    @property
    def first_pixel_y(self) -> float:
        return float(self.points[0].pixel_y)

    @property
    def last_pixel_y(self) -> float:
        return float(self.points[-1].pixel_y)

    def to_dict(self) -> dict[str, Any]:
        return {
            "unit": self.unit.value,
            "interpolation": "piecewise_linear",
            "extrapolation": {
                "before_first_pixel_y": "first_segment_linear",
                "after_last_pixel_y": "last_segment_linear",
                "negative_depth": "forbidden",
            },
            "points": [point.to_dict() for point in self.points],
        }

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any],
    ) -> "DepthCalibration":
        interpolation = payload.get(
            "interpolation",
            "piecewise_linear",
        )
        if interpolation != "piecewise_linear":
            raise CalibrationError(
                f"Unsupported depth interpolation: {interpolation!r}."
            )

        extrapolation = payload.get("extrapolation")
        expected_policy = {
            "before_first_pixel_y": "first_segment_linear",
            "after_last_pixel_y": "last_segment_linear",
            "negative_depth": "forbidden",
        }
        if extrapolation is not None and extrapolation != expected_policy:
            raise CalibrationError(
                "Unsupported depth extrapolation policy."
            )

        return cls(
            points=tuple(
                DepthCalibrationPoint.from_dict(item)
                for item in payload["points"]
            ),
            unit=DepthUnit(str(payload.get("unit", "M")).upper()),
        )


@dataclass(frozen=True, slots=True, order=True)
class HorizontalCalibrationPoint:
    """One horizontal reference point for a curve in one scanned segment."""

    pixel_x: float
    value: float

    def __post_init__(self) -> None:
        if not isfinite(self.pixel_x) or not isfinite(self.value):
            raise CalibrationError(
                "Horizontal calibration coordinates must be finite numbers."
            )

    def to_dict(self) -> dict[str, float]:
        return {
            "pixel_x": float(self.pixel_x),
            "value": float(self.value),
        }

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any],
    ) -> "HorizontalCalibrationPoint":
        return cls(
            float(payload["pixel_x"]),
            float(payload["value"]),
        )


@dataclass(frozen=True, slots=True)
class HorizontalScaleProfile:
    """One named two-point X calibration belonging to a curve and segment.

    The scientific curve identity remains project-level. Profiles only select
    which horizontal pixel-to-value mapping a digitized section uses. The
    first legacy calibration has the stable ``default`` identifier; additional
    profiles use UUIDs and may temporarily contain zero or one draft point.
    """

    scale_id: str
    points: tuple[HorizontalCalibrationPoint, ...] = ()

    def __post_init__(self) -> None:
        scale_id = str(self.scale_id).strip()
        if not scale_id:
            raise CalibrationError("Horizontal scale id cannot be empty.")
        normalized = tuple(
            sorted(
                (
                    point
                    if isinstance(point, HorizontalCalibrationPoint)
                    else HorizontalCalibrationPoint(*point)
                    for point in self.points
                ),
                key=lambda point: point.pixel_x,
            )
        )
        if len(normalized) > 2:
            raise CalibrationError(
                "Horizontal scale calibration supports exactly two points."
            )
        pixels = [point.pixel_x for point in normalized]
        if len(pixels) != len(set(pixels)):
            raise CalibrationError(
                "Horizontal scale cannot contain duplicate pixel_x values."
            )
        object.__setattr__(self, "scale_id", scale_id)
        object.__setattr__(self, "points", normalized)

    @property
    def complete(self) -> bool:
        return len(self.points) == 2

    def display_name(self, curve: "CurveDefinition") -> str:
        """Return an automatic left-to-right label such as GR: 0–100 API."""
        if self.complete:
            values = f"{self.points[0].value:g}–{self.points[1].value:g}"
            unit = f" {curve.unit}" if curve.unit else ""
            return f"{curve.mnemonic}: {values}{unit}"
        return (
            f"{curve.mnemonic}: Pending scale "
            f"({len(self.points)}/2 points)"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "scale_id": self.scale_id,
            "points": [point.to_dict() for point in self.points],
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "HorizontalScaleProfile":
        raw_points = payload.get("points", [])
        if not isinstance(raw_points, list):
            raise CalibrationError("Horizontal scale points must be a list.")
        return cls(
            scale_id=str(payload["scale_id"]),
            points=tuple(
                HorizontalCalibrationPoint.from_dict(item)
                for item in raw_points
            ),
        )


@dataclass(frozen=True, slots=True)
class CurveDefinition:
    """Project-level scientific identity and display metadata of one curve.

    A curve is defined once for the whole log project. Its horizontal pixel
    calibration is intentionally stored separately for every scanned segment.
    ``display_color`` is presentation metadata only and has no effect on the
    scientific calibration or exported values.
    """

    curve_id: str
    name: str
    mnemonic: str
    unit: str
    scale_type: ScaleType = ScaleType.LINEAR
    description: str = ""
    display_color: str = "#00B8D9"

    def __post_init__(self) -> None:
        if not self.curve_id.strip():
            raise CalibrationError("curve_id cannot be empty.")
        if not self.name.strip():
            raise CalibrationError("Curve name cannot be empty.")
        if not self.mnemonic.strip():
            raise CalibrationError("Curve mnemonic cannot be empty.")
        if not isinstance(self.scale_type, ScaleType):
            object.__setattr__(
                self,
                "scale_type",
                ScaleType(str(self.scale_type).lower()),
            )
        try:
            color = normalize_curve_display_color(self.display_color)
        except ValueError as exc:
            raise CalibrationError(str(exc)) from exc
        object.__setattr__(self, "display_color", color)

    @classmethod
    def create(
        cls,
        *,
        name: str,
        mnemonic: str,
        unit: str,
        scale_type: ScaleType | str = ScaleType.LINEAR,
        description: str = "",
        display_color: str | None = None,
    ) -> "CurveDefinition":
        curve_id = str(uuid4())
        return cls(
            curve_id,
            name.strip(),
            mnemonic.strip(),
            unit.strip(),
            scale_type,
            description.strip(),
            (
                curve_display_color_for_id(curve_id)
                if display_color is None
                else display_color
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "curve_id": self.curve_id,
            "name": self.name,
            "mnemonic": self.mnemonic,
            "unit": self.unit,
            "scale_type": self.scale_type.value,
            "description": self.description,
            "display_color": self.display_color,
        }

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any],
    ) -> "CurveDefinition":
        curve_id = str(payload["curve_id"])
        return cls(
            curve_id,
            str(payload["name"]),
            str(payload["mnemonic"]),
            str(payload.get("unit", "")),
            ScaleType(str(payload.get("scale_type", "linear")).lower()),
            str(payload.get("description", "")),
            str(
                payload.get(
                    "display_color",
                    curve_display_color_for_id(curve_id),
                )
            ),
        )


@dataclass(frozen=True, slots=True)
class HorizontalCalibration:
    """Two-point horizontal calibration for one curve in one segment."""

    pixel_x1: float
    value1: float
    pixel_x2: float
    value2: float
    scale_type: ScaleType = ScaleType.LINEAR

    def __post_init__(self) -> None:
        if not isinstance(self.scale_type, ScaleType):
            object.__setattr__(
                self,
                "scale_type",
                ScaleType(str(self.scale_type).lower()),
            )

        values = (
            self.pixel_x1,
            self.value1,
            self.pixel_x2,
            self.value2,
        )
        if not all(isfinite(value) for value in values):
            raise CalibrationError(
                "Horizontal calibration values must be finite."
            )
        if self.pixel_x1 == self.pixel_x2:
            raise CalibrationError(
                "Horizontal calibration requires two different "
                "pixel X values."
            )
        if self.value1 == self.value2:
            raise CalibrationError(
                "Horizontal calibration requires two different "
                "physical values."
            )
        if (
            self.scale_type is ScaleType.LOGARITHMIC
            and (self.value1 <= 0 or self.value2 <= 0)
        ):
            raise CalibrationError(
                "Logarithmic calibration requires positive "
                "physical values."
            )

    @property
    def points(self) -> tuple[HorizontalCalibrationPoint, HorizontalCalibrationPoint]:
        return (
            HorizontalCalibrationPoint(self.pixel_x1, self.value1),
            HorizontalCalibrationPoint(self.pixel_x2, self.value2),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "pixel_x1": float(self.pixel_x1),
            "value1": float(self.value1),
            "pixel_x2": float(self.pixel_x2),
            "value2": float(self.value2),
            "scale_type": self.scale_type.value,
        }

    @classmethod
    def from_points(
        cls,
        points: tuple[HorizontalCalibrationPoint, HorizontalCalibrationPoint],
        scale_type: ScaleType | str,
    ) -> "HorizontalCalibration":
        ordered = tuple(sorted(points, key=lambda point: point.pixel_x))
        return cls(
            ordered[0].pixel_x,
            ordered[0].value,
            ordered[1].pixel_x,
            ordered[1].value,
            scale_type,
        )

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any],
    ) -> "HorizontalCalibration":
        return cls(
            float(payload["pixel_x1"]),
            float(payload["value1"]),
            float(payload["pixel_x2"]),
            float(payload["value2"]),
            ScaleType(str(payload["scale_type"]).lower()),
        )


@dataclass(frozen=True, slots=True)
class CalibrationModel:
    """Scientific calibration snapshot without any track dependency.

    Curve identities are project-level. The model is retained as a compact
    validation/serialization helper for callers that need one scientific
    snapshot, while segment persistence stores horizontal calibrations keyed by
    ``curve_id``.
    """

    depth: DepthCalibration
    curves: tuple[CurveDefinition, ...] = ()
    horizontal_calibrations: dict[
        str,
        HorizontalCalibration,
    ] = field(default_factory=dict)

    def __post_init__(self) -> None:
        curve_ids = [curve.curve_id for curve in self.curves]
        if len(curve_ids) != len(set(curve_ids)):
            raise CalibrationError(
                "Curve identifiers must be unique."
            )

        if set(self.horizontal_calibrations) - set(curve_ids):
            raise CalibrationError(
                "Horizontal calibration references an unknown curve."
            )

    def is_complete(self) -> bool:
        return bool(self.curves) and all(
            curve.curve_id in self.horizontal_calibrations
            for curve in self.curves
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "depth": self.depth.to_dict(),
            "curves": [
                {
                    **curve.to_dict(),
                    "horizontal_calibration": (
                        self.horizontal_calibrations[
                            curve.curve_id
                        ].to_dict()
                        if curve.curve_id in self.horizontal_calibrations
                        else None
                    ),
                }
                for curve in self.curves
            ],
        }

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any],
    ) -> "CalibrationModel":
        curves: list[CurveDefinition] = []
        calibrations: dict[str, HorizontalCalibration] = {}

        for item in payload.get("curves", []):
            curve = CurveDefinition.from_dict(item)
            curves.append(curve)
            horizontal = item.get("horizontal_calibration")
            if horizontal is not None:
                calibrations[curve.curve_id] = (
                    HorizontalCalibration.from_dict(horizontal)
                )

        return cls(
            depth=DepthCalibration.from_dict(payload["depth"]),
            curves=tuple(curves),
            horizontal_calibrations=calibrations,
        )
