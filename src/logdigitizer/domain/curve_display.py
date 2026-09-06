"""Small fixed display-color palette for project curves.

The palette is deliberately presentation-only and independent of Qt.  Keeping
it in the domain package gives curve definitions a stable, serializable color
without introducing GUI dependencies into scientific models.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha1
from typing import Iterable


@dataclass(frozen=True, slots=True)
class CurveDisplayColor:
    """One named color offered for displaying digitized curves."""

    key: str
    label: str
    hex_color: str


CURVE_DISPLAY_PALETTE: tuple[CurveDisplayColor, ...] = (
    CurveDisplayColor("cyan", "Cyan", "#00B8D9"),
    CurveDisplayColor("orange", "Orange", "#FF8C00"),
    CurveDisplayColor("lime", "Lime", "#7CB342"),
    CurveDisplayColor("magenta", "Magenta", "#D946EF"),
    CurveDisplayColor("blue", "Blue", "#2563EB"),
)

DEFAULT_CURVE_DISPLAY_COLOR = CURVE_DISPLAY_PALETTE[0].hex_color
_ALLOWED_BY_HEX = {
    item.hex_color.upper(): item.hex_color
    for item in CURVE_DISPLAY_PALETTE
}


def normalize_curve_display_color(value: str) -> str:
    """Return canonical palette hex or raise ``ValueError`` for other colors."""
    normalized = str(value).strip().upper()
    try:
        return _ALLOWED_BY_HEX[normalized]
    except KeyError as exc:
        allowed = ", ".join(item.label for item in CURVE_DISPLAY_PALETTE)
        raise ValueError(
            f"Unsupported curve display color {value!r}; choose one of: {allowed}."
        ) from exc


def curve_display_color_for_id(curve_id: str) -> str:
    """Choose a stable fallback palette color for legacy curve definitions."""
    digest = sha1(str(curve_id).encode("utf-8")).digest()
    return CURVE_DISPLAY_PALETTE[digest[0] % len(CURVE_DISPLAY_PALETTE)].hex_color


def next_available_curve_display_color(existing: Iterable[str]) -> str:
    """Suggest the first unused palette color, then cycle when all are in use."""
    normalized: list[str] = []
    for value in existing:
        try:
            normalized.append(normalize_curve_display_color(value))
        except ValueError:
            continue

    used = set(normalized)
    for item in CURVE_DISPLAY_PALETTE:
        if item.hex_color not in used:
            return item.hex_color

    return CURVE_DISPLAY_PALETTE[len(normalized) % len(CURVE_DISPLAY_PALETTE)].hex_color
