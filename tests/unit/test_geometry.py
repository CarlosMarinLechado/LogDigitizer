from __future__ import annotations

from itertools import permutations

import cv2
import numpy as np
import pytest

from logdigitizer.processing.geometry import (
    ImagePoint,
    InvalidQuadrilateralError,
    apply_homography,
    compute_perspective_transform,
    estimate_rectified_size,
    order_quad_points,
    rectify_image,
    validate_quad,
)


def _as_array(points: tuple[ImagePoint, ...]) -> np.ndarray:
    return np.array(
        [(point.x, point.y) for point in points],
        dtype=np.float64,
    )


def test_rectangle_rectifies_to_expected_destination() -> None:
    points = [
        (10.0, 20.0),
        (110.0, 20.0),
        (110.0, 220.0),
        (10.0, 220.0),
    ]

    result = compute_perspective_transform(points)
    mapped = apply_homography(
        result.ordered_points,
        result.transform_matrix,
    )

    assert result.output_size == (100, 200)
    np.testing.assert_allclose(
        mapped,
        np.array(
            [
                [0.0, 0.0],
                [99.0, 0.0],
                [99.0, 199.0],
                [0.0, 199.0],
            ]
        ),
        atol=1.0e-6,
    )


def test_known_synthetic_perspective_rectification_preserves_full_image() -> None:
    image = np.zeros((180, 220), dtype=np.uint8)
    points = [
        (35.0, 20.0),
        (185.0, 35.0),
        (170.0, 155.0),
        (20.0, 140.0),
    ]

    image[4:17, 4:17] = 255
    cv2.fillConvexPoly(
        image,
        np.array(points, dtype=np.int32),
        180,
    )

    result = rectify_image(image, points)
    mapped_grid = apply_homography(
        result.transform.ordered_points,
        result.transform.transform_matrix,
    )

    assert result.transform.full_image is True
    assert result.transform.reference_size == estimate_rectified_size(points)
    assert result.image.shape == (
        result.transform.output_height,
        result.transform.output_width,
    )
    np.testing.assert_allclose(
        mapped_grid,
        _as_array(result.transform.destination_points),
        atol=1.0e-5,
    )

    source_corners = [
        (0.0, 0.0),
        (219.0, 0.0),
        (219.0, 179.0),
        (0.0, 179.0),
    ]
    mapped_source_corners = apply_homography(
        source_corners,
        result.transform.transform_matrix,
    )
    assert np.min(mapped_source_corners[:, 0]) >= -1.0e-6
    assert np.min(mapped_source_corners[:, 1]) >= -1.0e-6
    assert np.max(mapped_source_corners[:, 0]) <= (
        result.transform.output_width - 1 + 1.0e-6
    )
    assert np.max(mapped_source_corners[:, 1]) <= (
        result.transform.output_height - 1 + 1.0e-6
    )

    marker_center = apply_homography(
        [(10.0, 10.0)],
        result.transform.transform_matrix,
    )[0]
    x = int(round(float(marker_center[0])))
    y = int(round(float(marker_center[1])))
    patch = result.image[
        max(0, y - 3): min(result.image.shape[0], y + 4),
        max(0, x - 3): min(result.image.shape[1], x + 4),
    ]
    assert patch.size > 0
    assert int(np.max(patch)) > 200


def test_full_image_transform_canvas_contains_all_source_corners() -> None:
    points = [
        (30.0, 25.0),
        (190.0, 40.0),
        (175.0, 150.0),
        (25.0, 135.0),
    ]

    result = compute_perspective_transform(
        points,
        image_shape=(180, 220),
    )
    mapped = apply_homography(
        [
            (0.0, 0.0),
            (219.0, 0.0),
            (219.0, 179.0),
            (0.0, 179.0),
        ],
        result.transform_matrix,
    )

    assert result.full_image is True
    assert result.output_width >= 2
    assert result.output_height >= 2
    assert np.min(mapped[:, 0]) >= -1.0e-6
    assert np.min(mapped[:, 1]) >= -1.0e-6
    assert np.max(mapped[:, 0]) <= result.output_width - 1 + 1.0e-6
    assert np.max(mapped[:, 1]) <= result.output_height - 1 + 1.0e-6

def test_point_order_is_independent_of_click_order() -> None:
    expected = (
        ImagePoint(10.0, 10.0),
        ImagePoint(110.0, 20.0),
        ImagePoint(100.0, 120.0),
        ImagePoint(0.0, 100.0),
    )

    for candidate in permutations(expected):
        assert order_quad_points(candidate) == expected


def test_duplicate_points_are_rejected() -> None:
    points = [
        (0.0, 0.0),
        (100.0, 0.0),
        (100.0, 100.0),
        (100.0, 100.0),
    ]

    with pytest.raises(
        InvalidQuadrilateralError,
        match="distinct",
    ):
        validate_quad(points)


def test_degenerate_collinear_quad_is_rejected() -> None:
    points = [
        (0.0, 0.0),
        (50.0, 0.00001),
        (100.0, 0.00002),
        (150.0, 0.00003),
    ]

    with pytest.raises(InvalidQuadrilateralError):
        validate_quad(points)


def test_output_size_uses_longest_opposing_edges() -> None:
    points = [
        (10.0, 10.0),
        (110.0, 10.0),
        (120.0, 210.0),
        (0.0, 210.0),
    ]

    width, height = estimate_rectified_size(points)

    assert width == 120
    assert height == 200


def test_direct_transform_maps_source_corners_to_destination() -> None:
    points = [
        (25.0, 15.0),
        (150.0, 30.0),
        (140.0, 190.0),
        (10.0, 170.0),
    ]
    result = compute_perspective_transform(points)

    mapped = apply_homography(
        result.ordered_points,
        result.transform_matrix,
    )

    np.testing.assert_allclose(
        mapped,
        _as_array(result.destination_points),
        atol=1.0e-5,
    )


def test_inverse_transform_round_trip_recovers_coordinates() -> None:
    points = [
        (25.0, 15.0),
        (150.0, 30.0),
        (140.0, 190.0),
        (10.0, 170.0),
    ]
    result = compute_perspective_transform(
        points,
        image_shape=(220, 180),
    )
    samples = [
        ImagePoint(50.0, 50.0),
        ImagePoint(90.0, 100.0),
        ImagePoint(120.0, 150.0),
    ]

    rectified = apply_homography(
        samples,
        result.transform_matrix,
    )
    recovered = apply_homography(
        rectified,
        result.inverse_matrix,
    )

    np.testing.assert_allclose(
        recovered,
        _as_array(tuple(samples)),
        atol=1.0e-6,
    )


def test_rectification_does_not_modify_source_array() -> None:
    image = np.arange(
        120 * 160,
        dtype=np.uint16,
    ).reshape(120, 160)
    original = image.copy()
    points = [
        (5.0, 5.0),
        (150.0, 8.0),
        (145.0, 110.0),
        (8.0, 115.0),
    ]

    rectify_image(image, points)

    np.testing.assert_array_equal(image, original)


def test_points_outside_supplied_image_shape_are_rejected() -> None:
    points = [
        (0.0, 0.0),
        (100.0, 0.0),
        (100.0, 100.0),
        (-1.0, 100.0),
    ]

    with pytest.raises(
        InvalidQuadrilateralError,
        match="inside",
    ):
        validate_quad(
            points,
            image_shape=(120, 120),
        )
