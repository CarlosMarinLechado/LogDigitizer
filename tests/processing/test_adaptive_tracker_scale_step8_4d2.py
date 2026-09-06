from __future__ import annotations

import numpy as np
import pytest

from logdigitizer.domain.digitization import TracePoint
from logdigitizer.processing.automatic_digitization import (
    AutomaticTraceError,
    AutomaticTraceSettings,
    TrackerStopReason,
    TrackingResult,
    track_downward_result,
)
from logdigitizer.processing import automatic_tracking_session as session_module
from logdigitizer.processing.automatic_tracking_session import (
    AutomaticTrackingSession,
)
from logdigitizer.processing.grid_detection import GridPenaltyResult
from logdigitizer.processing.line_width import LineWidthEstimate


def test_three_pixel_reference_width_preserves_original_geometry() -> None:
    base = AutomaticTraceSettings(
        search_half_width_px=12,
        max_horizontal_step_px=6.0,
        max_consecutive_misses=2,
        min_contrast_ratio=0.12,
        min_confidence=0.55,
    )

    scaled = base.scaled_for_line_width(3.0)

    assert scaled.search_half_width_px == 12
    assert scaled.max_horizontal_step_px == pytest.approx(6.0)
    assert scaled.max_consecutive_misses == 2
    assert scaled.min_contrast_ratio == pytest.approx(0.12)
    assert scaled.min_confidence == pytest.approx(0.55)


def test_thicker_curve_scales_only_pixel_geometry() -> None:
    base = AutomaticTraceSettings(
        search_half_width_px=12,
        max_horizontal_step_px=6.0,
        max_consecutive_misses=2,
        min_contrast_ratio=0.10,
        min_confidence=0.50,
    )

    scaled = base.scaled_for_line_width(9.0)

    assert scaled.search_half_width_px == 36
    assert scaled.max_horizontal_step_px == pytest.approx(18.0)
    assert scaled.max_consecutive_misses == 8
    assert scaled.min_contrast_ratio == base.min_contrast_ratio
    assert scaled.min_confidence == base.min_confidence


@pytest.mark.parametrize(
    (
        "line_width_px",
        "expected_scale",
        "expected_effective_width",
        "expected_search",
        "expected_flexibility",
        "expected_misses",
    ),
    [
        (0.5, 0.5, 1.5, 6, 3.0, 1),
        (1.5, 0.5, 1.5, 6, 3.0, 1),
        (3.0, 1.0, 3.0, 12, 6.0, 2),
        (6.0, 2.0, 6.0, 24, 12.0, 5),
        (12.0, 4.0, 12.0, 48, 24.0, 11),
        (100.0, 8.0, 24.0, 96, 48.0, 23),
    ],
)
def test_search_flexibility_and_gap_share_one_bounded_spatial_scale(
    line_width_px: float,
    expected_scale: float,
    expected_effective_width: float,
    expected_search: int,
    expected_flexibility: float,
    expected_misses: int,
) -> None:
    settings = AutomaticTraceSettings()

    scaled = settings.scaled_for_line_width(line_width_px)

    assert settings.geometry_scale_for_line_width(
        line_width_px
    ) == pytest.approx(expected_scale)
    assert settings.effective_line_width_px(
        line_width_px
    ) == pytest.approx(expected_effective_width)
    assert scaled.search_half_width_px == expected_search
    assert scaled.max_horizontal_step_px == pytest.approx(
        expected_flexibility
    )
    assert scaled.max_consecutive_misses == expected_misses
    assert scaled.min_contrast_ratio == settings.min_contrast_ratio
    assert scaled.min_confidence == settings.min_confidence


def test_complete_resolution_applies_only_spatial_scaling() -> None:
    settings = AutomaticTraceSettings()

    effective = settings.resolved_for_tracking(6.0)

    assert effective.search_half_width_px == 24
    assert effective.max_horizontal_step_px == pytest.approx(12.0)
    assert effective.max_consecutive_misses == 5


def test_session_passes_bounded_width_to_every_spatial_consumer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image = np.full((12, 40), 230, dtype=np.uint8)
    captured: dict[str, float] = {}

    def fake_estimate_line_width(
        _image: np.ndarray,
        point: TracePoint,
    ) -> LineWidthEstimate:
        return LineWidthEstimate(
            width_px=100.0,
            sample_point_px=point,
            contrast_ratio=0.8,
            line_intensity=30.0,
            background_intensity=230.0,
        )

    def fake_detect_grid(
        _image: np.ndarray,
        *,
        line_width_px: float | None = None,
    ) -> GridPenaltyResult:
        assert line_width_px is not None
        captured["grid_width"] = line_width_px
        return GridPenaltyResult(
            penalty_mask=np.zeros(image.shape, dtype=np.float32),
            vertical_line_count=0,
            horizontal_line_count=0,
        )

    def fake_track(
        _image: np.ndarray,
        start_point: TracePoint,
        *,
        settings: AutomaticTraceSettings,
        line_width_px: float | None = None,
        grid_penalty_mask: np.ndarray | None = None,
    ) -> TrackingResult:
        assert line_width_px is not None
        assert grid_penalty_mask is not None
        captured["tracking_width"] = line_width_px
        captured["search"] = float(settings.search_half_width_px)
        captured["flexibility"] = settings.max_horizontal_step_px
        captured["misses"] = float(settings.max_consecutive_misses)
        end = TracePoint(start_point.x_px, start_point.y_px + 1.0)
        return TrackingResult(
            raw_samples_px=(start_point, end),
            confidences=(1.0, 1.0),
            stop_reason=TrackerStopReason.IMAGE_BOTTOM,
        )

    monkeypatch.setattr(
        session_module,
        "estimate_line_width",
        fake_estimate_line_width,
    )
    monkeypatch.setattr(
        session_module,
        "detect_grid_penalty_mask",
        fake_detect_grid,
    )
    monkeypatch.setattr(
        session_module,
        "track_downward_minimum_cost_result",
        fake_track,
    )

    session = AutomaticTrackingSession(image)
    session.start(TracePoint(20.0, 2.0))

    assert session.estimated_line_width_px == pytest.approx(100.0)
    assert session.geometry_scale_factor == pytest.approx(8.0)
    assert session.effective_line_width_px == pytest.approx(24.0)
    assert captured == {
        "grid_width": 24.0,
        "tracking_width": 24.0,
        "search": 96.0,
        "flexibility": 48.0,
        "misses": 23.0,
    }


@pytest.mark.parametrize("bad_width", [0, -1, float("inf"), float("nan")])
def test_invalid_line_width_for_scaling_is_rejected(bad_width: float) -> None:
    settings = AutomaticTraceSettings()

    with pytest.raises(AutomaticTraceError, match="positive finite"):
        settings.scaled_for_line_width(bad_width)


def _abrupt_shift_image(scale: int) -> np.ndarray:
    """Same physical geometry rasterized at different resolutions."""
    h = 24 * scale
    w = 60 * scale
    image = np.full((h, w), 235, dtype=np.uint8)

    line_width = 3 * scale
    x_before = 20 * scale
    x_after = 25 * scale
    change_y = 10 * scale

    image[:change_y, x_before : x_before + line_width] = 25
    image[change_y:, x_after : x_after + line_width] = 25
    return image


def test_adaptive_session_handles_proportional_lateral_change_at_high_resolution() -> None:
    high = _abrupt_shift_image(scale=3)

    # The old fixed 8.4B geometry cannot accept the 15-pixel abrupt change.
    fixed = track_downward_result(
        high,
        TracePoint(61.0, 5.0),
        settings=AutomaticTraceSettings(),
    )
    assert fixed.stop_reason is TrackerStopReason.LOST_CURVE

    # The session first measures ~9 px line width and scales 6 px flexibility
    # to ~18 px, so the same physical change remains trackable.
    adaptive = AutomaticTrackingSession(high)
    result = adaptive.start(TracePoint(61.0, 5.0))

    assert adaptive.estimated_line_width_px is not None
    assert adaptive.geometry_scale_factor > 2.0
    assert adaptive.effective_settings.max_horizontal_step_px > 12.0
    assert result.stop_reason is TrackerStopReason.IMAGE_BOTTOM


def _gap_image(scale: int) -> np.ndarray:
    h = 26 * scale
    w = 45 * scale
    image = np.full((h, w), 230, dtype=np.uint8)

    line_width = 3 * scale
    x = 20 * scale
    gap_start = 10 * scale
    gap_stop = 12 * scale  # 2 physical low-res rows -> 6 rows at scale=3.

    image[:gap_start, x : x + line_width] = 30
    image[gap_stop:, x : x + line_width] = 30
    return image


def test_gap_budget_scales_with_raster_resolution() -> None:
    high = _gap_image(scale=3)

    fixed = track_downward_result(
        high,
        TracePoint(61.0, 5.0),
        settings=AutomaticTraceSettings(),
    )
    assert fixed.stop_reason is TrackerStopReason.LOST_CURVE

    adaptive = AutomaticTrackingSession(high)
    result = adaptive.start(TracePoint(61.0, 5.0))

    assert adaptive.effective_settings.max_consecutive_misses >= 6
    assert result.stop_reason is TrackerStopReason.IMAGE_BOTTOM


def test_failed_width_estimate_falls_back_to_original_fixed_settings() -> None:
    image = np.full((20, 40), 220, dtype=np.uint8)
    session = AutomaticTrackingSession(
        image,
        settings=AutomaticTraceSettings(
            search_half_width_px=7,
            max_horizontal_step_px=5.0,
            max_consecutive_misses=1,
        ),
    )

    session.start((20.0, 3.0))

    assert session.estimated_line_width_px is None
    assert session.effective_line_width_px is None
    assert session.geometry_scale_factor == pytest.approx(1.0)
    assert session.effective_settings.search_half_width_px == 7
    assert session.effective_settings.max_horizontal_step_px == pytest.approx(5.0)
    assert session.effective_settings.max_consecutive_misses == 1
