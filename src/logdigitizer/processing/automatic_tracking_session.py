"""Stateful guided workflow for semi-automatic tracking through step 8.4D.7B.

This module deliberately contains no Qt code. It composes the stateless tracker
from ``automatic_digitization`` into one user-guided session that can stop when
the curve is lost, reconnect to a later resume click with a straight segment,
and replace a deviated tail through an earlier correction click.

Guided correction intervals use a constrained minimum-cost path. Loss/resume
intervals use a dense straight connector because the user selects a nearby
point on the same curve explicitly.
"""

from __future__ import annotations

from enum import StrEnum
from math import ceil, floor, isfinite
from typing import Sequence

import numpy as np

from logdigitizer.domain.digitization import TracePoint
from logdigitizer.processing.automatic_digitization import (
    AutomaticTraceSettings,
    TrackerStopReason,
    TrackingResult,
)
from logdigitizer.processing.minimum_cost_tracking import (
    AnchoredPathResult,
    track_between_points_minimum_cost_result,
    track_downward_minimum_cost_result,
)
from logdigitizer.processing.line_width import (
    LineWidthEstimate,
    LineWidthEstimationError,
    estimate_line_width,
)
from logdigitizer.processing.grid_detection import (
    GridPenaltyResult,
    detect_grid_penalty_mask,
)


class AutomaticTrackingSessionError(ValueError):
    """Raised for an invalid start, resume, or correction operation."""


class AutomaticTrackingSessionState(StrEnum):
    """High-level interaction state of one semi-automatic tracing session."""

    WAITING_FOR_START = "waiting_for_start"
    WAITING_FOR_RESUME = "waiting_for_resume"
    COMPLETE = "complete"


_CORRECTION_ROLLBACK_WIDTH_MULTIPLIER = 4.0
_MIN_CORRECTION_ROLLBACK_ROWS = 8
_MAX_CORRECTION_ROLLBACK_ROWS = 48
_DEFAULT_CORRECTION_LINE_WIDTH_PX = 3.0


class AutomaticTrackingSession:
    """Accumulate one downward semi-automatic raw trace across user restarts."""

    def __init__(
        self,
        image: np.ndarray,
        *,
        settings: AutomaticTraceSettings | None = None,
    ) -> None:
        # Snapshot the raster so display/filter changes cannot alter an active run.
        self._image = np.array(image, copy=True)
        self._settings = settings or AutomaticTraceSettings()
        self._effective_settings = self._settings
        self._samples: tuple[TracePoint, ...] = ()
        self._confidences: tuple[float, ...] = ()
        self._last_result: TrackingResult | None = None
        self._line_width_estimates: tuple[LineWidthEstimate, ...] = ()
        self._grid_penalty_result: GridPenaltyResult | None = None
        self._last_splice_point_px: TracePoint | None = None
        self._last_correction_point_px: TracePoint | None = None
        self._state = AutomaticTrackingSessionState.WAITING_FOR_START

    @property
    def state(self) -> AutomaticTrackingSessionState:
        return self._state

    @property
    def raw_samples_px(self) -> tuple[TracePoint, ...]:
        return self._samples

    @property
    def confidences(self) -> tuple[float, ...]:
        return self._confidences

    @property
    def last_result(self) -> TrackingResult | None:
        return self._last_result

    @property
    def stop_y_px(self) -> float | None:
        if self._last_result is None:
            return None
        return self._last_result.stop_y_px

    @property
    def line_width_estimates(self) -> tuple[LineWidthEstimate, ...]:
        """Successful width measurements from start/reconnect clicks."""
        return self._line_width_estimates

    @property
    def effective_settings(self) -> AutomaticTraceSettings:
        """Pixel settings actually used for the most recent tracking run."""
        return self._effective_settings

    @property
    def grid_penalty_result(self) -> GridPenaltyResult | None:
        """Internal Hough grid analysis, available after the first click."""
        return self._grid_penalty_result

    @property
    def grid_penalty_mask(self) -> np.ndarray | None:
        """Internal [0, 1] grid penalty; the raster itself is never changed."""
        if self._grid_penalty_result is None:
            return None
        return self._grid_penalty_result.penalty_mask

    @property
    def geometry_scale_factor(self) -> float:
        """Current line-width spatial scale; 1.0 when no estimate is available."""
        width = self.estimated_line_width_px
        if width is None:
            return 1.0
        return self._settings.geometry_scale_for_line_width(width)

    @property
    def effective_line_width_px(self) -> float | None:
        """Bounded line width shared by every spatial tracker component."""
        width = self.estimated_line_width_px
        if width is None:
            return None
        return self._settings.effective_line_width_px(width)

    @property
    def last_splice_point_px(self) -> TracePoint | None:
        """Most recent safe point kept before a corrective tail replacement."""
        return self._last_splice_point_px

    @property
    def last_correction_point_px(self) -> TracePoint | None:
        """Most recent explicit correction or resume anchor in the draft."""
        return self._last_correction_point_px

    @property
    def estimated_line_width_px(self) -> float | None:
        """Robust current line-width scale, or None if it could not be measured."""
        if not self._line_width_estimates:
            return None
        widths = np.asarray(
            [estimate.width_px for estimate in self._line_width_estimates],
            dtype=float,
        )
        return float(np.median(widths))

    def start(
        self,
        point: TracePoint | Sequence[float],
    ) -> TrackingResult:
        """Start the first automatic run from the user's explicit click."""
        if self._state is not AutomaticTrackingSessionState.WAITING_FOR_START:
            raise AutomaticTrackingSessionError(
                "Automatic tracking has already been started."
            )

        start_point = _coerce_point(point)
        self._append_line_width_estimate(start_point)
        self._effective_settings = self._resolve_effective_settings()
        self._ensure_grid_penalty_result()
        result = track_downward_minimum_cost_result(
            self._image,
            start_point,
            settings=self._effective_settings,
            line_width_px=self.effective_line_width_px,
            grid_penalty_mask=self.grid_penalty_mask,
        )
        self._samples = result.raw_samples_px
        self._confidences = result.confidences
        self._last_result = result
        self._state = _state_from_result(result)
        return result

    def resume(
        self,
        point: TracePoint | Sequence[float],
    ) -> TrackingResult:
        """Reconnect a detected loss to a lower anchor, then resume tracking."""
        if self._state is not AutomaticTrackingSessionState.WAITING_FOR_RESUME:
            raise AutomaticTrackingSessionError(
                "Automatic tracking can only resume after the curve is lost."
            )
        if not self._samples:
            raise AutomaticTrackingSessionError(
                "Automatic tracking has no previous samples to resume from."
            )

        restart = _coerce_point(point)
        height, width = self._image.shape[:2]
        if not (
            0.0 <= restart.x_px < width
            and 0.0 <= restart.y_px < height
        ):
            raise AutomaticTrackingSessionError(
                "Resume point must lie inside the image bounds."
            )
        last_y = self._samples[-1].y_px
        if restart.y_px <= last_y:
            raise AutomaticTrackingSessionError(
                "Resume point must be below the last accepted raw sample."
            )

        # A resume click may be placed on or below the first lost row. Requiring
        # this also prevents accidental backwards clicks in a visually large gap.
        stop_y = self.stop_y_px
        if stop_y is not None and restart.y_px + 1e-9 < stop_y:
            raise AutomaticTrackingSessionError(
                "Resume point must be at or below the detected loss location."
            )

        return self._reconnect_and_track(
            restart,
            splice_index=len(self._samples) - 1,
            straight_connector=True,
        )

    def correct(
        self,
        point: TracePoint | Sequence[float],
    ) -> TrackingResult:
        """Reconnect a safe earlier splice to a mandatory correction click."""
        if self._state is AutomaticTrackingSessionState.WAITING_FOR_START:
            raise AutomaticTrackingSessionError(
                "Automatic tracking must be started before it can be corrected."
            )
        if len(self._samples) < 2:
            raise AutomaticTrackingSessionError(
                "Automatic tracking needs at least two samples before correction."
            )

        correction = _coerce_point(point)
        height, width = self._image.shape[:2]
        if not (
            0.0 <= correction.x_px < width
            and 0.0 <= correction.y_px < height
        ):
            raise AutomaticTrackingSessionError(
                "Correction point must lie inside the image bounds."
            )

        first_y = self._samples[0].y_px
        last_y = self._samples[-1].y_px
        if not first_y < correction.y_px < last_y:
            raise AutomaticTrackingSessionError(
                "Correction point must lie inside the already traced Y interval."
            )

        splice_index = self._safe_splice_index(
            correction.y_px,
            protected_y_px=self._previous_anchor_y(correction.y_px),
        )
        splice = self._samples[splice_index]
        if correction.y_px <= splice.y_px:
            raise AutomaticTrackingSessionError(
                "Correction point must be below the selected splice point."
            )

        return self._reconnect_and_track(
            correction,
            splice_index=splice_index,
        )

    def _reconnect_and_track(
        self,
        anchor: TracePoint,
        *,
        splice_index: int,
        straight_connector: bool = False,
    ) -> TrackingResult:
        """Replace a provisional tail by splice→anchor→downward tracking."""
        splice = self._samples[splice_index]
        if anchor.y_px <= splice.y_px:
            raise AutomaticTrackingSessionError(
                "Reconnect anchor must be below the selected splice point."
            )

        previous_estimates = self._line_width_estimates
        previous_effective_settings = self._effective_settings
        self._append_line_width_estimate(anchor)
        self._effective_settings = self._resolve_effective_settings()
        try:
            self._ensure_grid_penalty_result()
            if straight_connector:
                bridge = _straight_anchored_path(splice, anchor)
            else:
                bridge = track_between_points_minimum_cost_result(
                    self._image,
                    splice,
                    anchor,
                    settings=self._effective_settings,
                    line_width_px=self.effective_line_width_px,
                    grid_penalty_mask=self.grid_penalty_mask,
                )
            result = track_downward_minimum_cost_result(
                self._image,
                anchor,
                settings=self._effective_settings,
                line_width_px=self.effective_line_width_px,
                grid_penalty_mask=self.grid_penalty_mask,
            )
        except Exception:
            self._line_width_estimates = previous_estimates
            self._effective_settings = previous_effective_settings
            raise

        if (
            bridge.raw_samples_px[0] != splice
            or bridge.raw_samples_px[-1] != anchor
        ):
            self._line_width_estimates = previous_estimates
            self._effective_settings = previous_effective_settings
            raise AutomaticTrackingSessionError(
                "Anchored connector did not preserve its mandatory endpoints."
            )

        retained_samples = self._samples[: splice_index + 1]
        retained_confidences = self._confidences[: splice_index + 1]
        merged_samples = (
            retained_samples
            + bridge.raw_samples_px[1:]
            + result.raw_samples_px[1:]
        )
        merged_confidences = (
            retained_confidences
            + bridge.confidences[1:]
            + result.confidences[1:]
        )
        y_values = [sample.y_px for sample in merged_samples]
        if any(second <= first for first, second in zip(y_values, y_values[1:])):
            self._line_width_estimates = previous_estimates
            self._effective_settings = previous_effective_settings
            raise AutomaticTrackingSessionError(
                "Reconnected raw samples must remain strictly increasing in Y."
            )

        self._samples = merged_samples
        self._confidences = merged_confidences
        self._last_splice_point_px = splice
        self._last_correction_point_px = anchor
        self._last_result = result
        self._state = _state_from_result(result)
        return result

    def _previous_anchor_y(self, new_anchor_y_px: float) -> float | None:
        """Return the last explicit anchor that must survive this reconnect."""
        previous = self._last_correction_point_px
        if previous is None or previous.y_px >= new_anchor_y_px:
            return None
        return previous.y_px

    def _safe_splice_index(
        self,
        correction_y_px: float,
        *,
        protected_y_px: float | None = None,
    ) -> int:
        """Return the latest sample before a bounded width-scaled rollback."""
        line_width = (
            self.effective_line_width_px
            or _DEFAULT_CORRECTION_LINE_WIDTH_PX
        )
        rollback_rows = int(
            np.clip(
                round(
                    _CORRECTION_ROLLBACK_WIDTH_MULTIPLIER * line_width
                ),
                _MIN_CORRECTION_ROLLBACK_ROWS,
                _MAX_CORRECTION_ROLLBACK_ROWS,
            )
        )
        target_y = correction_y_px - rollback_rows
        if protected_y_px is not None:
            target_y = max(target_y, protected_y_px)
        eligible = tuple(
            index
            for index, sample in enumerate(self._samples)
            if sample.y_px <= target_y
        )
        if not eligible:
            return 0
        return eligible[-1]

    def _ensure_grid_penalty_result(self) -> None:
        """Run Hough grid analysis once for this immutable session raster."""
        if self._grid_penalty_result is not None:
            return
        self._grid_penalty_result = detect_grid_penalty_mask(
            self._image,
            line_width_px=self.effective_line_width_px,
        )

    def _resolve_effective_settings(self) -> AutomaticTraceSettings:
        """Resolve the continuous tracker geometry for the raster scale."""
        return self._settings.resolved_for_tracking(
            self.estimated_line_width_px
        )

    def _append_line_width_estimate(self, point: TracePoint) -> None:
        """Measure width without changing the proven 8.4B tracking behaviour.

        Step 8.4D.1 is intentionally observational: failure to estimate width
        does not prevent the existing tracker from running. Later advanced
        tracker steps can use this value as their spatial scale and define an
        explicit fallback when required.
        """
        try:
            estimate = estimate_line_width(self._image, point)
        except LineWidthEstimationError:
            return
        self._line_width_estimates = (
            self._line_width_estimates + (estimate,)
        )


def _straight_anchored_path(
    start: TracePoint,
    end: TracePoint,
) -> AnchoredPathResult:
    """Return a dense straight splice→resume connector with exact endpoints."""
    if end.y_px <= start.y_px:
        raise AutomaticTrackingSessionError(
            "Straight resume endpoint must be below its start point."
        )

    samples: list[TracePoint] = [start]
    delta_x = end.x_px - start.x_px
    delta_y = end.y_px - start.y_px
    for y_px in range(
        floor(start.y_px) + 1,
        ceil(end.y_px),
    ):
        if not start.y_px < y_px < end.y_px:
            continue
        fraction = (float(y_px) - start.y_px) / delta_y
        samples.append(
            TracePoint(
                start.x_px + fraction * delta_x,
                float(y_px),
            )
        )
    samples.append(end)
    return AnchoredPathResult(
        raw_samples_px=tuple(samples),
        confidences=tuple(1.0 for _sample in samples),
    )


def _state_from_result(
    result: TrackingResult,
) -> AutomaticTrackingSessionState:
    if result.stop_reason is TrackerStopReason.IMAGE_BOTTOM:
        return AutomaticTrackingSessionState.COMPLETE
    return AutomaticTrackingSessionState.WAITING_FOR_RESUME


def _coerce_point(value: TracePoint | Sequence[float]) -> TracePoint:
    if isinstance(value, TracePoint):
        return value
    try:
        x_px, y_px = value
    except (TypeError, ValueError) as exc:
        raise AutomaticTrackingSessionError(
            "Tracking point must contain exactly two coordinates."
        ) from exc

    try:
        x = float(x_px)
        y = float(y_px)
    except (TypeError, ValueError, OverflowError) as exc:
        raise AutomaticTrackingSessionError(
            "Tracking point coordinates must be finite numbers."
        ) from exc

    if not isfinite(x) or not isfinite(y):
        raise AutomaticTrackingSessionError(
            "Tracking point coordinates must be finite numbers."
        )
    return TracePoint(x, y)
