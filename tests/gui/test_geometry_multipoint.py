from __future__ import annotations

import cv2
import numpy as np
import pytest

from logdigitizer.processing.geometry import (
    ImagePoint,
    InvalidControlPairsError,
    compute_multipoint_transform,
    rectify_image_multipoint,
    validate_control_pairs,
)


def _draw_polyline(
    image: np.ndarray,
    points: list[tuple[float, float]],
    value: int,
    thickness: int = 2,
) -> None:
    cv2.polylines(
        image,
        [np.rint(np.asarray(points)).astype(np.int32)],
        False,
        value,
        thickness,
        lineType=cv2.LINE_8,
    )


def test_multipoint_destination_pairs_form_rectangular_track() -> None:
    pairs = [
        ((35.0, 20.0), (185.0, 30.0)),
        ((45.0, 70.0), (178.0, 76.0)),
        ((30.0, 120.0), (172.0, 112.0)),
        ((20.0, 160.0), (170.0, 150.0)),
    ]

    transform = compute_multipoint_transform(
        pairs,
        image_shape=(180, 220),
    )
    destination_pairs = transform.destination_control_pairs

    left_x = [pair.left.x for pair in destination_pairs]
    right_x = [pair.right.x for pair in destination_pairs]
    rows = [pair.left.y for pair in destination_pairs]

    assert len(destination_pairs) == 4
    assert np.ptp(left_x) == pytest.approx(0.0, abs=1e-9)
    assert np.ptp(right_x) == pytest.approx(0.0, abs=1e-9)
    assert rows == sorted(rows)
    assert rows[0] == pytest.approx(
        transform.base_transform.destination_points[0].y
    )
    assert rows[-1] == pytest.approx(
        transform.base_transform.destination_points[3].y
    )


def test_piecewise_bilinear_rectification_straightens_bent_centerline() -> None:
    image = np.zeros((190, 230), dtype=np.uint8)
    pairs = [
        ((35.0, 20.0), (195.0, 30.0)),
        ((48.0, 70.0), (182.0, 76.0)),
        ((30.0, 125.0), (176.0, 116.0)),
        ((22.0, 170.0), (185.0, 158.0)),
    ]

    centers = [
        (
            (left[0] + right[0]) * 0.5,
            (left[1] + right[1]) * 0.5,
        )
        for left, right in pairs
    ]
    _draw_polyline(image, centers, 255, thickness=3)

    result = rectify_image_multipoint(
        image,
        pairs,
        interpolation=cv2.INTER_NEAREST,
        remap_chunk_rows=32,
    )

    destination = result.transform.base_transform.destination_points
    expected_x = int(round((destination[0].x + destination[1].x) * 0.5))
    top = int(round(destination[0].y))
    bottom = int(round(destination[3].y))

    # The synthetic center trace should concentrate close to one vertical X.
    roi = result.image[top:bottom + 1]
    ys, xs = np.where(roi > 200)
    assert len(xs) > (bottom - top) * 0.55
    absolute_x = xs + 0
    # ROI spans the complete image width, so no X offset is needed.
    assert np.median(np.abs(absolute_x - expected_x)) <= 2.0
    assert np.percentile(np.abs(absolute_x - expected_x), 90) <= 4.0


def test_transverse_control_lines_become_horizontal_at_destination_rows() -> None:
    image = np.zeros((190, 230), dtype=np.uint8)
    pairs = [
        ((35.0, 20.0), (195.0, 30.0)),
        ((48.0, 70.0), (182.0, 76.0)),
        ((30.0, 125.0), (176.0, 116.0)),
        ((22.0, 170.0), (185.0, 158.0)),
    ]

    # Draw the second transverse section as an intensity marker.
    cv2.line(
        image,
        tuple(map(int, pairs[1][0])),
        tuple(map(int, pairs[1][1])),
        255,
        3,
        lineType=cv2.LINE_8,
    )

    result = rectify_image_multipoint(
        image,
        pairs,
        interpolation=cv2.INTER_NEAREST,
        remap_chunk_rows=32,
    )
    target_y = int(round(result.transform.destination_y[1]))
    destination = result.transform.base_transform.destination_points
    x0 = int(round(destination[0].x))
    x1 = int(round(destination[1].x))

    band = result.image[
        max(0, target_y - 2):target_y + 3,
        x0:x1 + 1,
    ]
    assert band.size > 0
    # Most columns across the selected track should contain the horizontal line.
    assert np.mean(np.max(band, axis=0) > 200) > 0.85


def test_multipoint_preserves_standard_full_image_canvas_and_exterior_marker() -> None:
    image = np.zeros((180, 220), dtype=np.uint8)
    image[4:16, 4:16] = 255
    pairs = [
        ((35.0, 20.0), (185.0, 30.0)),
        ((45.0, 75.0), (180.0, 78.0)),
        ((30.0, 125.0), (175.0, 116.0)),
        ((20.0, 160.0), (170.0, 150.0)),
    ]

    result = rectify_image_multipoint(image, pairs)

    assert result.transform.full_image is True
    assert result.image.shape == (
        result.transform.output_height,
        result.transform.output_width,
    )

    base = result.transform.base_transform
    marker = np.array([[10.0, 10.0]], dtype=np.float64)
    homogeneous = np.column_stack([marker, np.ones(1)])
    mapped = homogeneous @ base.transform_matrix.T
    mapped = mapped[:, :2] / mapped[:, 2, None]
    x = int(round(float(mapped[0, 0])))
    y = int(round(float(mapped[0, 1])))
    patch = result.image[
        max(0, y - 4):min(result.image.shape[0], y + 5),
        max(0, x - 4):min(result.image.shape[1], x + 5),
    ]
    assert patch.size > 0
    assert int(np.max(patch)) > 200


def test_control_pairs_must_be_top_to_bottom() -> None:
    pairs = [
        ((30.0, 20.0), (190.0, 25.0)),
        ((35.0, 120.0), (185.0, 120.0)),
        ((40.0, 70.0), (180.0, 75.0)),
        ((25.0, 160.0), (175.0, 155.0)),
    ]

    with pytest.raises(
        InvalidControlPairsError,
        match="top to bottom",
    ):
        validate_control_pairs(
            pairs,
            image_shape=(180, 220),
        )


def test_folded_control_band_is_rejected() -> None:
    pairs = [
        ((30.0, 20.0), (190.0, 25.0)),
        ((180.0, 80.0), (40.0, 80.0)),
        ((25.0, 160.0), (175.0, 155.0)),
    ]

    with pytest.raises(InvalidControlPairsError):
        validate_control_pairs(
            pairs,
            image_shape=(180, 220),
        )


def test_multipoint_rectification_does_not_modify_source_array() -> None:
    image = np.arange(180 * 220, dtype=np.uint16).reshape(180, 220)
    original = image.copy()
    pairs = [
        ((35.0, 20.0), (185.0, 30.0)),
        ((45.0, 75.0), (180.0, 78.0)),
        ((30.0, 125.0), (175.0, 116.0)),
        ((20.0, 160.0), (170.0, 150.0)),
    ]

    rectify_image_multipoint(image, pairs, remap_chunk_rows=32)

    np.testing.assert_array_equal(image, original)
