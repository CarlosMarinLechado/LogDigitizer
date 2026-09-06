from __future__ import annotations

import numpy as np
import pytest

from logdigitizer.processing.enhancement import (
    EnhancementSettings,
    InvalidEnhancementImageError,
    InvalidEnhancementSettingsError,
    apply_levels,
    enhance_image,
)


@pytest.mark.parametrize("dtype", [np.uint8, np.uint16])
def test_neutral_settings_return_equal_but_independent_copy(dtype) -> None:
    image = np.arange(12, dtype=dtype).reshape(3, 4)
    result = enhance_image(image)

    np.testing.assert_array_equal(result, image)
    assert result is not image
    assert not np.shares_memory(result, image)


def test_enhancement_never_modifies_source_array() -> None:
    image = np.array(
        [
            [20, 40, 60],
            [80, 100, 120],
            [140, 160, 180],
        ],
        dtype=np.uint8,
    )
    original = image.copy()

    enhance_image(
        image,
        EnhancementSettings(
            black_point=30,
            midtones=0.80,
            white_point=220,
        ),
    )

    np.testing.assert_array_equal(image, original)


def test_levels_map_black_and_white_points_to_full_range() -> None:
    image = np.array([[0, 51, 102, 153, 204, 255]], dtype=np.uint8)

    result = apply_levels(
        image,
        black_point=51,
        midtones=1.0,
        white_point=204,
    )

    np.testing.assert_array_equal(
        result,
        np.array([[0, 0, 85, 170, 255, 255]], dtype=np.uint8),
    )


def test_midtones_below_one_brighten_and_above_one_darken() -> None:
    image = np.array([[128]], dtype=np.uint8)

    brighter = apply_levels(image, midtones=0.50)
    darker = apply_levels(image, midtones=2.00)

    assert int(brighter[0, 0]) > 128
    assert int(darker[0, 0]) < 128


def test_lower_white_point_pushes_light_background_to_white() -> None:
    image = np.array([[160, 190, 220, 240]], dtype=np.uint8)

    result = apply_levels(image, white_point=220)

    assert int(result[0, 1]) > 190
    assert int(result[0, 2]) == 255
    assert int(result[0, 3]) == 255


def test_higher_black_point_strengthens_dark_pixels() -> None:
    image = np.array([[20, 40, 60, 100]], dtype=np.uint8)

    result = apply_levels(image, black_point=40)

    assert int(result[0, 0]) == 0
    assert int(result[0, 1]) == 0
    assert int(result[0, 2]) < 60


def test_uint16_levels_preserve_dtype_and_use_normalized_0_255_controls() -> None:
    maximum = np.iinfo(np.uint16).max
    image = np.array(
        [[0, maximum // 4, maximum // 2, 3 * maximum // 4, maximum]],
        dtype=np.uint16,
    )

    result = apply_levels(
        image,
        black_point=64,
        midtones=1.0,
        white_point=192,
    )

    assert result.dtype == np.uint16
    assert result.shape == image.shape
    assert int(result[0, 0]) == 0
    assert int(result[0, -1]) == maximum
    assert int(result[0, 2]) == pytest.approx(maximum // 2, abs=300)


def test_rgb_image_remains_rgb_and_is_not_converted_to_grayscale() -> None:
    image = np.zeros((32, 32, 3), dtype=np.uint8)
    image[..., 0] = 180
    image[..., 1] = 80
    image[..., 2] = 30

    result = enhance_image(
        image,
        EnhancementSettings(
            black_point=20,
            midtones=0.90,
            white_point=230,
        ),
    )

    assert result.shape == image.shape
    assert result.dtype == image.dtype
    assert not np.array_equal(result[..., 0], result[..., 1])
    assert not np.array_equal(result[..., 1], result[..., 2])


def test_rgba_alpha_channel_is_preserved_exactly() -> None:
    image = np.zeros((24, 24, 4), dtype=np.uint8)
    image[..., 0] = 40
    image[..., 1] = 120
    image[..., 2] = 200
    image[..., 3] = np.arange(24, dtype=np.uint8)[:, None]

    result = enhance_image(
        image,
        EnhancementSettings(
            black_point=25,
            midtones=1.25,
            white_point=225,
        ),
    )

    np.testing.assert_array_equal(result[..., 3], image[..., 3])


def test_same_settings_produce_deterministic_result() -> None:
    rng = np.random.default_rng(42)
    image = rng.integers(
        0,
        256,
        size=(80, 60, 3),
        dtype=np.uint8,
    )
    settings = EnhancementSettings(
        black_point=18,
        midtones=0.82,
        white_point=224,
    )

    first = enhance_image(image, settings)
    second = enhance_image(image, settings)

    np.testing.assert_array_equal(first, second)


@pytest.mark.parametrize(
    "image",
    [
        np.zeros((10, 10), dtype=np.float32),
        np.zeros((10, 10, 2), dtype=np.uint8),
        np.zeros((0, 10), dtype=np.uint8),
    ],
)
def test_invalid_images_are_rejected(image) -> None:
    with pytest.raises(InvalidEnhancementImageError):
        enhance_image(image)


@pytest.mark.parametrize(
    "settings",
    [
        EnhancementSettings(black_point=-1),
        EnhancementSettings(black_point=255),
        EnhancementSettings(white_point=0),
        EnhancementSettings(white_point=256),
        EnhancementSettings(black_point=100, white_point=100),
        EnhancementSettings(midtones=0.09),
        EnhancementSettings(midtones=3.01),
    ],
)
def test_invalid_settings_are_rejected(settings) -> None:
    with pytest.raises(InvalidEnhancementSettingsError):
        enhance_image(
            np.zeros((16, 16), dtype=np.uint8),
            settings,
        )
