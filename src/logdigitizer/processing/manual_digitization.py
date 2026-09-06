"""Pure manual-trace construction and PCHIP preview utilities.

This module deliberately has no PySide6 dependency.  Manual digitization is
created in canonical rectified-image pixel coordinates and only becomes a
persistent :class:`TraceSection` when the caller explicitly finishes the draft.
"""

from __future__ import annotations

from enum import StrEnum
from math import ceil, isfinite
from typing import Iterable

import numpy as np
from scipy.interpolate import PchipInterpolator

from logdigitizer.domain.digitization import (
    DigitizationError,
    TracePoint,
    TraceSection,
)


class ManualTraceError(DigitizationError):
    """Raised when a manual trace cannot accept or finish an operation."""


class ManualTraceDirection(StrEnum):
    """Direction in which control points are being created on the image."""

    UP = "up"
    DOWN = "down"


class ManualTraceDraft:
    """Mutable, non-persistent draft for one manually digitized section.

    The first point does not impose a direction.  The second point establishes
    whether creation proceeds upward (decreasing image Y) or downward
    (increasing image Y).  Every later point must continue strictly in that
    direction, preserving the scientific requirement that the final trace can
    be represented as ``X = f(Y)``.

    Creation direction is intentionally *not* persisted.  When the draft is
    finished, :class:`TraceSection` stores the points in canonical ascending-Y
    order, so a trace created upward and the same trace created downward have
    the same domain representation.
    """

    def __init__(self, points: Iterable[TracePoint] = ()) -> None:
        self._points: list[TracePoint] = []
        self._direction: ManualTraceDirection | None = None
        for point in points:
            self.add_point(point)

    @property
    def points(self) -> tuple[TracePoint, ...]:
        """Return control points in the order in which the user created them."""
        return tuple(self._points)

    @property
    def direction(self) -> ManualTraceDirection | None:
        """Return creation direction, or ``None`` until a second point exists."""
        return self._direction

    @property
    def can_finish(self) -> bool:
        """Return whether at least two control points define a trace section."""
        return len(self._points) >= 2

    def add_point(
        self,
        point: TracePoint | tuple[float, float],
    ) -> TracePoint:
        """Append one control point while enforcing strict vertical ordering.

        ``point`` may be an existing :class:`TracePoint` or an ``(x_px, y_px)``
        pair.  Horizontal movement is unrestricted.  A rejected point leaves
        the draft unchanged.
        """
        candidate = (
            point
            if isinstance(point, TracePoint)
            else TracePoint(float(point[0]), float(point[1]))
        )

        if not self._points:
            self._points.append(candidate)
            return candidate

        previous = self._points[-1]
        delta_y = candidate.y_px - previous.y_px
        if delta_y == 0.0:
            raise ManualTraceError(
                "A new manual control point must have a different y_px "
                "from the previous point."
            )

        if self._direction is None:
            self._direction = (
                ManualTraceDirection.DOWN
                if delta_y > 0.0
                else ManualTraceDirection.UP
            )
        elif self._direction is ManualTraceDirection.DOWN and delta_y <= 0.0:
            raise ManualTraceError(
                "Manual trace creation is moving downward; the next point "
                "must have a larger y_px."
            )
        elif self._direction is ManualTraceDirection.UP and delta_y >= 0.0:
            raise ManualTraceError(
                "Manual trace creation is moving upward; the next point "
                "must have a smaller y_px."
            )

        self._points.append(candidate)
        return candidate

    def clear(self) -> None:
        """Cancel/reset the draft without creating persistent geometry."""
        self._points.clear()
        self._direction = None

    def preview_samples(
        self,
        *,
        max_step_y_px: float = 1.0,
    ) -> tuple[TracePoint, ...]:
        """Return PCHIP samples for drawing the current manual trace.

        The samples are returned in canonical ascending-Y order regardless of
        the direction in which the user is creating the draft.  Every control
        point is guaranteed to occur in the sampled sequence.  With zero or one
        control point there is no interpolation to perform, so the available
        point(s) are returned directly.
        """
        return sample_pchip_trace(
            self._points,
            max_step_y_px=max_step_y_px,
        )

    def finish(self) -> TraceSection:
        """Create a persistent manual ``TraceSection`` from the current draft."""
        if not self.can_finish:
            raise ManualTraceError(
                "A manual trace requires at least two control points."
            )
        return TraceSection.create_manual(self._points)


def sample_pchip_trace(
    points: Iterable[TracePoint],
    *,
    max_step_y_px: float = 1.0,
) -> tuple[TracePoint, ...]:
    """Interpolate control points as ``X = f(Y)`` with shape-preserving PCHIP.

    ``max_step_y_px`` controls only the density of the *display* geometry; it
    does not create or alter editable control points.  The function always
    includes every original control-point Y coordinate and both endpoints.
    """
    step = float(max_step_y_px)
    if not isfinite(step) or step <= 0.0:
        raise ManualTraceError(
            "max_step_y_px must be a finite value greater than zero."
        )

    normalized = tuple(sorted(points, key=lambda point: point.y_px))
    if not normalized:
        return ()
    if len(normalized) == 1:
        return normalized

    y_values = np.asarray([point.y_px for point in normalized], dtype=float)
    x_values = np.asarray([point.x_px for point in normalized], dtype=float)

    if np.any(np.diff(y_values) <= 0.0):
        raise ManualTraceError(
            "PCHIP control points must have strictly increasing y_px values."
        )

    interpolator = PchipInterpolator(y_values, x_values, extrapolate=False)

    sampled_y: list[float] = []
    for interval_index, (y0, y1) in enumerate(
        zip(y_values[:-1], y_values[1:])
    ):
        span = float(y1 - y0)
        segment_count = max(1, int(ceil(span / step)))
        interval = np.linspace(
            float(y0),
            float(y1),
            segment_count + 1,
            dtype=float,
        )
        if interval_index:
            interval = interval[1:]
        sampled_y.extend(float(value) for value in interval)

    sampled_y_array = np.asarray(sampled_y, dtype=float)
    sampled_x_array = np.asarray(interpolator(sampled_y_array), dtype=float)

    return tuple(
        TracePoint(float(x_px), float(y_px))
        for x_px, y_px in zip(sampled_x_array, sampled_y_array)
    )
