from __future__ import annotations

import pytest

from logdigitizer.domain.calibration import (
    CalibrationError,
    CurveDefinition,
    ScaleType,
)
from logdigitizer.domain.curve_display import (
    CURVE_DISPLAY_PALETTE,
    next_available_curve_display_color,
)


def test_palette_contains_five_distinct_colors() -> None:
    assert len(CURVE_DISPLAY_PALETTE) == 5
    assert len({item.hex_color for item in CURVE_DISPLAY_PALETTE}) == 5
    assert len({item.label for item in CURVE_DISPLAY_PALETTE}) == 5


def test_curve_definition_round_trip_preserves_display_color() -> None:
    curve = CurveDefinition.create(
        name="Gamma Ray",
        mnemonic="GR",
        unit="API",
        scale_type=ScaleType.LINEAR,
        display_color="#FF8C00",
    )

    payload = curve.to_dict()
    assert payload["display_color"] == "#FF8C00"
    assert CurveDefinition.from_dict(payload) == curve


def test_legacy_curve_without_color_receives_stable_palette_color() -> None:
    payload = {
        "curve_id": "legacy-curve-1",
        "name": "Gamma Ray",
        "mnemonic": "GR",
        "unit": "API",
        "scale_type": "linear",
        "description": "",
    }

    first = CurveDefinition.from_dict(payload)
    second = CurveDefinition.from_dict(payload)
    allowed = {item.hex_color for item in CURVE_DISPLAY_PALETTE}

    assert first.display_color in allowed
    assert second.display_color == first.display_color


def test_curve_rejects_color_outside_fixed_palette() -> None:
    with pytest.raises(CalibrationError, match="Unsupported curve display color"):
        CurveDefinition.create(
            name="Gamma Ray",
            mnemonic="GR",
            unit="API",
            display_color="#123456",
        )


def test_next_available_color_prefers_unused_palette_entries() -> None:
    first = CURVE_DISPLAY_PALETTE[0].hex_color
    second = CURVE_DISPLAY_PALETTE[1].hex_color
    suggested = next_available_curve_display_color((first, second))
    assert suggested == CURVE_DISPLAY_PALETTE[2].hex_color
