"""Non-destructive image enhancement for rectified log rasters.

This module is independent of PySide6. It preserves the visible color channels
of color scans; grayscale conversion for automatic tracking belongs to a later,
internal processing stage.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

import numpy as np

_SUPPORTED_DTYPES: Final = (np.dtype(np.uint8), np.dtype(np.uint16))
_LEVELS_MAX: Final = 255
_MIDTONES_MIN: Final = 0.10
_MIDTONES_MAX: Final = 3.00


class InvalidEnhancementImageError(ValueError):
    """Raised when an image cannot be processed by the enhancement pipeline."""


class InvalidEnhancementSettingsError(ValueError):
    """Raised when enhancement parameters are outside their valid domain."""


@dataclass(frozen=True, slots=True)
class EnhancementSettings:
    """Parameters for non-destructive visible image level adjustment.

    ``black_point`` and ``white_point`` use an 8-bit-style 0..255 scale even for
    uint16 rasters. Internally they are normalized to the full dtype range.

    ``midtones`` is a gamma-like exponent. 1.00 is neutral, values below 1.00
    brighten midtones and values above 1.00 darken them.
    """

    black_point: int = 0
    midtones: float = 1.0
    white_point: int = 255


def enhance_image(
    image: np.ndarray,
    settings: EnhancementSettings | None = None,
) -> np.ndarray:
    """Return an enhanced copy of ``image`` without modifying the input array.

    The enhancement is a single global levels adjustment using black point,
    midtones and white point.

    Supported visible rasters:
    - HxW grayscale uint8 / uint16;
    - HxWx3 RGB uint8 / uint16;
    - HxWx4 RGBA uint8 / uint16.

    Alpha is always preserved unchanged.
    """
    settings = settings or EnhancementSettings()
    validate_enhancement_settings(settings)
    validate_enhancement_image(image)

    return apply_levels(
        image,
        black_point=settings.black_point,
        midtones=settings.midtones,
        white_point=settings.white_point,
    )


def apply_levels(
    image: np.ndarray,
    *,
    black_point: int = 0,
    midtones: float = 1.0,
    white_point: int = 255,
) -> np.ndarray:
    """Apply black point, midtone gamma and white point using a lookup table.

    The 0..255 level controls are mapped proportionally to uint8 or uint16. RGB
    channels receive the same lookup table and RGBA alpha is preserved exactly.
    """
    validate_enhancement_image(image)
    _validate_levels(black_point, midtones, white_point)

    result = image.copy()
    if black_point == 0 and midtones == 1.0 and white_point == 255:
        return result

    lut = _build_levels_lut(
        image.dtype,
        black_point=black_point,
        midtones=midtones,
        white_point=white_point,
    )

    if image.ndim == 2:
        return lut[image]

    result[..., :3] = lut[image[..., :3]]
    return result


def validate_enhancement_image(image: np.ndarray) -> None:
    """Validate raster types accepted by the Image Enhancement tool."""
    if not isinstance(image, np.ndarray):
        raise InvalidEnhancementImageError("image must be a NumPy array.")

    if image.size == 0:
        raise InvalidEnhancementImageError("image must not be empty.")

    if image.dtype not in _SUPPORTED_DTYPES:
        raise InvalidEnhancementImageError(
            "image dtype must be uint8 or uint16."
        )

    if image.ndim == 2:
        return

    if image.ndim == 3 and image.shape[2] in (3, 4):
        return

    raise InvalidEnhancementImageError(
        "image must be HxW grayscale, HxWx3 RGB, or HxWx4 RGBA."
    )


def validate_enhancement_settings(settings: EnhancementSettings) -> None:
    """Validate a complete enhancement configuration."""
    if not isinstance(settings, EnhancementSettings):
        raise InvalidEnhancementSettingsError(
            "settings must be an EnhancementSettings instance."
        )

    _validate_levels(
        settings.black_point,
        settings.midtones,
        settings.white_point,
    )


def _validate_levels(
    black_point: int,
    midtones: float,
    white_point: int,
) -> None:
    if not _is_level_integer(black_point) or not 0 <= int(black_point) <= 254:
        raise InvalidEnhancementSettingsError(
            "black_point must be an integer between 0 and 254."
        )

    if not _is_level_integer(white_point) or not 1 <= int(white_point) <= 255:
        raise InvalidEnhancementSettingsError(
            "white_point must be an integer between 1 and 255."
        )

    if int(black_point) >= int(white_point):
        raise InvalidEnhancementSettingsError(
            "black_point must be lower than white_point."
        )

    if (
        not np.isfinite(midtones)
        or not _MIDTONES_MIN <= float(midtones) <= _MIDTONES_MAX
    ):
        raise InvalidEnhancementSettingsError(
            "midtones must be finite and between 0.10 and 3.00."
        )


def _is_level_integer(value: object) -> bool:
    return isinstance(value, (int, np.integer)) and not isinstance(value, bool)


def _build_levels_lut(
    dtype: np.dtype,
    *,
    black_point: int,
    midtones: float,
    white_point: int,
) -> np.ndarray:
    maximum = int(np.iinfo(dtype).max)
    values = np.arange(maximum + 1, dtype=np.float64)

    black_value = (float(black_point) / _LEVELS_MAX) * maximum
    white_value = (float(white_point) / _LEVELS_MAX) * maximum

    normalized = (values - black_value) / (white_value - black_value)
    np.clip(normalized, 0.0, 1.0, out=normalized)
    adjusted = np.power(normalized, float(midtones)) * maximum
    return np.rint(adjusted).astype(dtype)
