from __future__ import annotations

import cv2
import numpy as np

from logdigitizer.processing.geometry import (
    ImagePoint,
    RectificationControlPair,
    rectify_image_multipoint,
)


def _shifted_middle_pairs() -> tuple[RectificationControlPair, ...]:
    return (
        RectificationControlPair(
            ImagePoint(50.0, 20.0),
            ImagePoint(170.0, 20.0),
        ),
        RectificationControlPair(
            ImagePoint(60.0, 80.0),
            ImagePoint(180.0, 80.0),
        ),
        RectificationControlPair(
            ImagePoint(50.0, 140.0),
            ImagePoint(170.0, 140.0),
        ),
    )


def test_multipoint_deformation_extends_across_full_canvas_width() -> None:
    # X ramp makes horizontal source displacement directly observable.
    image = np.tile(np.arange(220, dtype=np.uint8), (160, 1))

    result = rectify_image_multipoint(
        image,
        _shifted_middle_pairs(),
        interpolation=cv2.INTER_NEAREST,
    )
    base = result.transform.base_transform
    homography_only = cv2.warpPerspective(
        image,
        base.transform_matrix,
        base.output_size,
        flags=cv2.INTER_NEAREST,
    )

    middle_y = int(round(result.transform.destination_y[1]))
    left_x = int(round(base.destination_points[0].x))
    right_x = int(round(base.destination_points[1].x))

    # Before this fix only [left_x, right_x] was replaced by the multipoint
    # remap. Both page margins therefore retained the homography-only image and
    # could expose a duplicated/slanted border beside the straightened track.
    assert np.any(
        result.image[middle_y, :left_x]
        != homography_only[middle_y, :left_x]
    )
    assert np.any(
        result.image[middle_y, right_x + 1 :]
        != homography_only[middle_y, right_x + 1 :]
    )


def test_rows_outside_controlled_vertical_span_keep_full_image_homography() -> None:
    image = np.tile(np.arange(220, dtype=np.uint8), (160, 1))

    result = rectify_image_multipoint(
        image,
        _shifted_middle_pairs(),
        interpolation=cv2.INTER_NEAREST,
    )
    base = result.transform.base_transform
    homography_only = cv2.warpPerspective(
        image,
        base.transform_matrix,
        base.output_size,
        flags=cv2.INTER_NEAREST,
    )

    top_y = int(round(base.destination_points[0].y))
    if top_y > 0:
        assert np.array_equal(
            result.image[top_y - 1],
            homography_only[top_y - 1],
        )

    bottom_y = int(round(base.destination_points[3].y))
    if bottom_y + 1 < result.image.shape[0]:
        assert np.array_equal(
            result.image[bottom_y + 1],
            homography_only[bottom_y + 1],
        )


def test_full_width_fix_keeps_existing_output_canvas_and_metadata() -> None:
    image = np.zeros((160, 220), dtype=np.uint8)
    result = rectify_image_multipoint(image, _shifted_middle_pairs())

    assert result.image.shape[:2] == (
        result.transform.output_height,
        result.transform.output_width,
    )
    assert result.transform.full_image is True
    assert result.transform.base_transform.full_image is True
    assert result.transform.interpolation == "piecewise_bilinear"
