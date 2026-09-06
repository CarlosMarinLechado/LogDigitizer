"""Geometry utilities for standard and multipoint log-image rectification.

This module is deliberately independent of PySide6. All persisted/editable
coordinates are expressed in source-image pixel coordinates as floating-point
values.

Standard rectification uses four corners and a projective homography. The
optional multipoint mode keeps those outer corners but accepts additional
left/right control pairs. Each pair represents one physical transverse section
of the log. The selected track is then straightened band by band into a
rectangle while the area outside the track retains the standard full-image
homography, so the scan is not cropped.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import hypot
from typing import Sequence, TypeAlias, cast

import cv2
import numpy as np
from numpy.typing import NDArray

FloatArray: TypeAlias = NDArray[np.float64]


class InvalidQuadrilateralError(ValueError):
    """Raised when four points cannot define a valid perspective quadrilateral."""


class InvalidControlPairsError(InvalidQuadrilateralError):
    """Raised when multipoint left/right control pairs define an invalid mesh."""


@dataclass(frozen=True, slots=True)
class ImagePoint:
    """A point expressed in canonical source-image pixel coordinates."""

    x: float
    y: float


PointLike: TypeAlias = ImagePoint | Sequence[float]


@dataclass(frozen=True, slots=True)
class PerspectiveTransformResult:
    """Reproducible description of a four-point perspective transform.

    ``output_width`` and ``output_height`` describe the complete output canvas.

    ``reference_width`` and ``reference_height`` describe the rectangular
    reference grid defined by the user's four points. For the legacy/reference-
    quad transform these equal the output size. For full-image rectification the
    output canvas can be larger because information outside the reference grid
    is intentionally preserved.

    ``canvas_offset_x`` and ``canvas_offset_y`` are the translation added after
    the grid-straightening homography so that transformed source-image
    coordinates are non-negative.
    """

    source_points: tuple[ImagePoint, ImagePoint, ImagePoint, ImagePoint]
    ordered_points: tuple[ImagePoint, ImagePoint, ImagePoint, ImagePoint]
    transform_matrix: FloatArray
    inverse_matrix: FloatArray
    output_width: int
    output_height: int
    reference_width: int | None = None
    reference_height: int | None = None
    canvas_offset_x: float = 0.0
    canvas_offset_y: float = 0.0
    full_image: bool = False

    @property
    def output_size(self) -> tuple[int, int]:
        """Return complete canvas ``(width, height)`` as expected by OpenCV."""
        return self.output_width, self.output_height

    @property
    def reference_size(self) -> tuple[int, int]:
        """Return the rectified reference-grid ``(width, height)``."""
        return (
            self.reference_width
            if self.reference_width is not None
            else self.output_width,
            self.reference_height
            if self.reference_height is not None
            else self.output_height,
        )

    @property
    def destination_points(
        self,
    ) -> tuple[ImagePoint, ImagePoint, ImagePoint, ImagePoint]:
        """Return the rectified reference-grid corners, ordered TL, TR, BR, BL."""
        width, height = self.reference_size
        left = float(self.canvas_offset_x)
        top = float(self.canvas_offset_y)
        right = left + float(width - 1)
        bottom = top + float(height - 1)

        return (
            ImagePoint(left, top),
            ImagePoint(right, top),
            ImagePoint(right, bottom),
            ImagePoint(left, bottom),
        )


@dataclass(frozen=True, slots=True)
class RectificationResult:
    """Rectified raster together with the transform that produced it."""

    image: np.ndarray
    transform: PerspectiveTransformResult


@dataclass(frozen=True, slots=True)
class RectificationControlPair:
    """One corresponding left/right section of a distorted log track."""

    left: ImagePoint
    right: ImagePoint


ControlPairLike: TypeAlias = (
    RectificationControlPair
    | Sequence[PointLike]
)


@dataclass(frozen=True, slots=True)
class MultipointTransformResult:
    """Reproducible description of a piecewise-bilinear track dewarping.

    ``base_transform`` is the ordinary four-corner full-image homography
    defined by the first and last control pairs. It determines the output
    canvas and preserves scan content outside the selected track.

    ``destination_y`` stores the rectified Y coordinate assigned to every
    transverse control pair. Between consecutive pairs the track is mapped by
    bilinear interpolation.
    """

    control_pairs: tuple[RectificationControlPair, ...]
    destination_y: tuple[float, ...]
    base_transform: PerspectiveTransformResult
    interpolation: str = "piecewise_bilinear"
    full_image: bool = True

    @property
    def output_width(self) -> int:
        return self.base_transform.output_width

    @property
    def output_height(self) -> int:
        return self.base_transform.output_height

    @property
    def output_size(self) -> tuple[int, int]:
        return self.base_transform.output_size

    @property
    def reference_size(self) -> tuple[int, int]:
        return self.base_transform.reference_size

    @property
    def canvas_offset_x(self) -> float:
        return self.base_transform.canvas_offset_x

    @property
    def canvas_offset_y(self) -> float:
        return self.base_transform.canvas_offset_y

    @property
    def destination_control_pairs(
        self,
    ) -> tuple[RectificationControlPair, ...]:
        """Return every control pair on the final rectangular track boundary."""
        left_x = self.base_transform.destination_points[0].x
        right_x = self.base_transform.destination_points[1].x
        return tuple(
            RectificationControlPair(
                ImagePoint(left_x, y),
                ImagePoint(right_x, y),
            )
            for y in self.destination_y
        )


@dataclass(frozen=True, slots=True)
class MultipointRectificationResult:
    """Multipoint-rectified raster and its reproducible transform metadata."""

    image: np.ndarray
    transform: MultipointTransformResult


_MIN_POINT_SEPARATION_PX = 1.0e-3
_MIN_CONTROL_ROW_SEPARATION_PX = 1.0e-3
_MIN_RELATIVE_AREA = 1.0e-6
_MIN_OUTPUT_DIMENSION = 2


def order_quad_points(
    points: Sequence[PointLike],
) -> tuple[ImagePoint, ImagePoint, ImagePoint, ImagePoint]:
    """Return four corners consistently ordered as TL, TR, BR, BL.

    The ordering uses the convex hull and evaluates cyclic corner assignments
    against the image axes. It therefore does not rely solely on the common
    ``x + y`` / ``x - y`` heuristic, which can become unstable for perspective
    distortions and small rotations.
    """
    array = _as_point_array(points)
    _validate_point_separation(array)

    hull_indices = cv2.convexHull(array.astype(np.float32), returnPoints=False)
    if hull_indices is None or len(hull_indices) != 4:
        raise InvalidQuadrilateralError(
            "The four points must form a convex quadrilateral; no point may lie "
            "inside the triangle formed by the other three."
        )

    hull = array[hull_indices.reshape(-1)]
    centroid = hull.mean(axis=0)
    angles = np.arctan2(hull[:, 1] - centroid[1], hull[:, 0] - centroid[0])
    cyclic = hull[np.argsort(angles)]

    min_x, min_y = array.min(axis=0)
    max_x, max_y = array.max(axis=0)
    span = np.array(
        [
            max(float(max_x - min_x), 1.0e-12),
            max(float(max_y - min_y), 1.0e-12),
        ],
        dtype=np.float64,
    )
    target = np.array(
        [
            [min_x, min_y],
            [max_x, min_y],
            [max_x, max_y],
            [min_x, max_y],
        ],
        dtype=np.float64,
    )

    best_score = float("inf")
    best: FloatArray | None = None

    for base in (cyclic, cyclic[::-1]):
        for shift in range(4):
            candidate = np.roll(base, -shift, axis=0)
            score = float(np.sum(((candidate - target) / span) ** 2))

            if float(np.mean(candidate[:2, 1])) >= float(
                np.mean(candidate[2:, 1])
            ):
                score += 100.0
            if float(np.mean(candidate[[0, 3], 0])) >= float(
                np.mean(candidate[[1, 2], 0])
            ):
                score += 100.0

            if score < best_score:
                best_score = score
                best = candidate.copy()

    if best is None:  # pragma: no cover - defensive
        raise InvalidQuadrilateralError(
            "Could not determine a consistent corner order."
        )

    if _polygon_area(best) <= _minimum_area_threshold(best):
        raise InvalidQuadrilateralError(
            "The quadrilateral area is too small or nearly degenerate."
        )

    return _points_from_array(best)


def validate_quad(
    points: Sequence[PointLike],
    image_shape: Sequence[int] | None = None,
) -> tuple[ImagePoint, ImagePoint, ImagePoint, ImagePoint]:
    """Validate four reference-grid corners and return TL, TR, BR, BL order.

    ``image_shape`` may be a NumPy-style shape ``(height, width, ...)``.
    When supplied, every reference point must lie inside the source raster.
    """
    array = _as_point_array(points)
    ordered = order_quad_points(points)
    ordered_array = _as_point_array(ordered)

    if image_shape is not None:
        height, width = _image_dimensions(image_shape)
        if (
            np.any(array[:, 0] < 0.0)
            or np.any(array[:, 1] < 0.0)
            or np.any(array[:, 0] > width - 1)
            or np.any(array[:, 1] > height - 1)
        ):
            raise InvalidQuadrilateralError(
                "All rectification points must lie inside the source image."
            )

    area = _polygon_area(ordered_array)
    if area <= _minimum_area_threshold(ordered_array):
        raise InvalidQuadrilateralError(
            "The quadrilateral area is too small or nearly degenerate."
        )

    width, height = _estimate_size_from_ordered(ordered_array)
    if width < _MIN_OUTPUT_DIMENSION or height < _MIN_OUTPUT_DIMENSION:
        raise InvalidQuadrilateralError(
            "The rectified reference grid would be smaller than 2 pixels "
            "in one dimension."
        )

    return ordered


def estimate_rectified_size(
    points: Sequence[PointLike],
) -> tuple[int, int]:
    """Estimate reference-grid ``(width, height)`` from opposing edge lengths.

    This is the size of the straightened reference grid, not necessarily the
    size of the complete output canvas when the whole image is preserved.
    """
    ordered = validate_quad(points)
    return _estimate_size_from_ordered(_as_point_array(ordered))


def compute_perspective_transform(
    points: Sequence[PointLike],
    image_shape: Sequence[int] | None = None,
) -> PerspectiveTransformResult:
    """Compute the four-point homography and its inverse without warping.

    When ``image_shape`` is omitted, this retains the original reference-quad
    behavior: the selected quadrilateral maps to an output canvas of exactly the
    estimated reference-grid size.

    When ``image_shape`` is supplied, the same grid-straightening homography is
    expanded to a complete-image canvas. The final transform therefore keeps
    every transformed source-image corner inside the output raster and does not
    crop information outside the selected reference grid.
    """
    source_array = _as_point_array(points)
    ordered = validate_quad(points, image_shape=image_shape)
    ordered_array = _as_point_array(ordered)
    reference_width, reference_height = _estimate_size_from_ordered(
        ordered_array
    )

    destination = np.array(
        [
            [0.0, 0.0],
            [float(reference_width - 1), 0.0],
            [float(reference_width - 1), float(reference_height - 1)],
            [0.0, float(reference_height - 1)],
        ],
        dtype=np.float32,
    )

    base_matrix = cv2.getPerspectiveTransform(
        ordered_array.astype(np.float32),
        destination,
    )
    if not np.all(np.isfinite(base_matrix)):
        raise InvalidQuadrilateralError(
            "OpenCV produced a non-finite perspective transform."
        )

    if image_shape is None:
        return _build_transform_result(
            source_array=source_array,
            ordered=ordered,
            matrix=np.asarray(base_matrix, dtype=np.float64),
            output_width=reference_width,
            output_height=reference_height,
            reference_width=reference_width,
            reference_height=reference_height,
            canvas_offset_x=0.0,
            canvas_offset_y=0.0,
            full_image=False,
        )

    image_height, image_width = _image_dimensions(image_shape)
    source_corners = (
        ImagePoint(0.0, 0.0),
        ImagePoint(float(image_width - 1), 0.0),
        ImagePoint(float(image_width - 1), float(image_height - 1)),
        ImagePoint(0.0, float(image_height - 1)),
    )

    transformed_corners = apply_homography(source_corners, base_matrix)
    if not np.all(np.isfinite(transformed_corners)):
        raise InvalidQuadrilateralError(
            "The full-image rectification produced non-finite canvas bounds."
        )

    min_x = float(np.min(transformed_corners[:, 0]))
    min_y = float(np.min(transformed_corners[:, 1]))
    max_x = float(np.max(transformed_corners[:, 0]))
    max_y = float(np.max(transformed_corners[:, 1]))

    left = float(np.floor(min_x))
    top = float(np.floor(min_y))
    right = float(np.ceil(max_x))
    bottom = float(np.ceil(max_y))

    output_width = int(right - left + 1.0)
    output_height = int(bottom - top + 1.0)
    if output_width < _MIN_OUTPUT_DIMENSION or output_height < _MIN_OUTPUT_DIMENSION:
        raise InvalidQuadrilateralError(
            "The full-image rectified canvas would be smaller than 2 pixels "
            "in one dimension."
        )

    offset_x = -left
    offset_y = -top
    translation = np.array(
        [
            [1.0, 0.0, offset_x],
            [0.0, 1.0, offset_y],
            [0.0, 0.0, 1.0],
        ],
        dtype=np.float64,
    )
    final_matrix = translation @ np.asarray(base_matrix, dtype=np.float64)

    final_corners = apply_homography(source_corners, final_matrix)
    tolerance = 1.0e-6
    if (
        np.any(final_corners[:, 0] < -tolerance)
        or np.any(final_corners[:, 1] < -tolerance)
        or np.any(final_corners[:, 0] > float(output_width - 1) + tolerance)
        or np.any(final_corners[:, 1] > float(output_height - 1) + tolerance)
    ):
        raise InvalidQuadrilateralError(
            "Could not construct a canvas that preserves the full source image."
        )

    return _build_transform_result(
        source_array=source_array,
        ordered=ordered,
        matrix=final_matrix,
        output_width=output_width,
        output_height=output_height,
        reference_width=reference_width,
        reference_height=reference_height,
        canvas_offset_x=offset_x,
        canvas_offset_y=offset_y,
        full_image=True,
    )


def rectify_image(
    image: np.ndarray,
    points: Sequence[PointLike],
) -> RectificationResult:
    """Rectify the complete ``image`` using four reference-grid corners.

    The selected quadrilateral defines the geometry to straighten; it is not a
    crop boundary. Information elsewhere in the scan is preserved whenever the
    projective transform is finite over the complete source raster.
    """
    if not isinstance(image, np.ndarray):
        raise TypeError("image must be a NumPy array.")
    if image.ndim not in (2, 3) or image.size == 0:
        raise ValueError(
            "image must be a non-empty 2-D or 3-D NumPy array."
        )

    validate_quad(points, image_shape=image.shape)
    transform = compute_perspective_transform(points, image_shape=image.shape)

    rectified = cv2.warpPerspective(
        image,
        transform.transform_matrix,
        transform.output_size,
    )
    return RectificationResult(image=rectified, transform=transform)


def validate_control_pairs(
    control_pairs: Sequence[ControlPairLike],
    image_shape: Sequence[int] | None = None,
) -> tuple[RectificationControlPair, ...]:
    """Validate ordered left/right control pairs for advanced rectification.

    Pairs must be supplied from top to bottom. The first and last pairs define
    the same four outer corners used by standard rectification. Intermediate
    pairs may be tilted in the source scan, but after the outer homography their
    mean Y positions must remain strictly ordered and every left point must
    remain left of its corresponding right point.
    """
    pairs = _normalize_control_pairs(control_pairs)
    if len(pairs) < 2:
        raise InvalidControlPairsError(
            "At least two left/right control pairs are required."
        )

    if image_shape is not None:
        height, width = _image_dimensions(image_shape)
        for pair in pairs:
            for point in (pair.left, pair.right):
                if (
                    point.x < 0.0
                    or point.y < 0.0
                    or point.x > width - 1
                    or point.y > height - 1
                ):
                    raise InvalidControlPairsError(
                        "All multipoint rectification controls must lie inside "
                        "the source image."
                    )

    for index, pair in enumerate(pairs):
        separation = hypot(
            pair.right.x - pair.left.x,
            pair.right.y - pair.left.y,
        )
        if separation < _MIN_POINT_SEPARATION_PX:
            raise InvalidControlPairsError(
                f"Control pair {index + 1} has coincident or nearly coincident "
                "left/right points."
            )

    outer_points = (
        pairs[0].left,
        pairs[0].right,
        pairs[-1].right,
        pairs[-1].left,
    )
    try:
        validate_quad(outer_points, image_shape=image_shape)
    except InvalidQuadrilateralError as exc:
        raise InvalidControlPairsError(
            f"The first and last control pairs do not define a valid outer "
            f"track: {exc}"
        ) from exc

    # Every consecutive band must be a simple convex quadrilateral in the
    # logical order left-top, right-top, right-bottom, left-bottom. This prevents
    # a folded remap mesh.
    for index in range(len(pairs) - 1):
        band = np.array(
            [
                [pairs[index].left.x, pairs[index].left.y],
                [pairs[index].right.x, pairs[index].right.y],
                [pairs[index + 1].right.x, pairs[index + 1].right.y],
                [pairs[index + 1].left.x, pairs[index + 1].left.y],
            ],
            dtype=np.float32,
        )
        if not cv2.isContourConvex(band):
            raise InvalidControlPairsError(
                f"Control pairs {index + 1} and {index + 2} form a folded or "
                "non-convex band."
            )
        if _polygon_area(band.astype(np.float64)) <= _minimum_area_threshold(
            band.astype(np.float64)
        ):
            raise InvalidControlPairsError(
                f"Control pairs {index + 1} and {index + 2} define a "
                "degenerate band."
            )

    # The global transform gives us a stable frame in which 'top-to-bottom' and
    # 'left-to-right' are unambiguous even when the source scan is rotated.
    ordered_outer = validate_quad(outer_points, image_shape=image_shape)
    reference_width, reference_height = _estimate_size_from_ordered(
        _as_point_array(ordered_outer)
    )
    destination = np.array(
        [
            [0.0, 0.0],
            [float(reference_width - 1), 0.0],
            [float(reference_width - 1), float(reference_height - 1)],
            [0.0, float(reference_height - 1)],
        ],
        dtype=np.float32,
    )
    base_matrix = cv2.getPerspectiveTransform(
        _as_point_array(ordered_outer).astype(np.float32),
        destination,
    )

    transformed_rows: list[tuple[float, float, float]] = []
    for pair in pairs:
        mapped = apply_homography(
            (pair.left, pair.right),
            base_matrix,
        )
        left_x = float(mapped[0, 0])
        right_x = float(mapped[1, 0])
        center_y = float((mapped[0, 1] + mapped[1, 1]) * 0.5)
        transformed_rows.append((left_x, right_x, center_y))

    for index, (left_x, right_x, _center_y) in enumerate(transformed_rows):
        if not left_x < right_x:
            raise InvalidControlPairsError(
                f"Control pair {index + 1} reverses the left/right track order."
            )

    for index in range(len(transformed_rows) - 1):
        first_y = transformed_rows[index][2]
        second_y = transformed_rows[index + 1][2]
        if second_y - first_y <= _MIN_CONTROL_ROW_SEPARATION_PX:
            raise InvalidControlPairsError(
                "Control pairs must be ordered strictly from top to bottom."
            )

    return pairs


def compute_multipoint_transform(
    control_pairs: Sequence[ControlPairLike],
    image_shape: Sequence[int],
) -> MultipointTransformResult:
    """Build reproducible multipoint geometry without resampling an image."""
    pairs = validate_control_pairs(control_pairs, image_shape=image_shape)

    outer_points = (
        pairs[0].left,
        pairs[0].right,
        pairs[-1].right,
        pairs[-1].left,
    )
    base_transform = compute_perspective_transform(
        outer_points,
        image_shape=image_shape,
    )

    flattened: list[ImagePoint] = []
    for pair in pairs:
        flattened.extend((pair.left, pair.right))
    mapped = apply_homography(
        flattened,
        base_transform.transform_matrix,
    )

    destination_y: list[float] = []
    for index in range(len(pairs)):
        left = mapped[2 * index]
        right = mapped[2 * index + 1]
        destination_y.append(
            float((left[1] + right[1]) * 0.5)
        )

    # Anchor the first/last logical sections exactly to the reference rectangle.
    destination = base_transform.destination_points
    destination_y[0] = destination[0].y
    destination_y[-1] = destination[3].y

    for index in range(len(destination_y) - 1):
        if (
            destination_y[index + 1] - destination_y[index]
            <= _MIN_CONTROL_ROW_SEPARATION_PX
        ):
            raise InvalidControlPairsError(
                "Control pairs collapse or reverse after the outer "
                "four-point rectification."
            )

    return MultipointTransformResult(
        control_pairs=pairs,
        destination_y=tuple(destination_y),
        base_transform=base_transform,
    )


def rectify_image_multipoint(
    image: np.ndarray,
    control_pairs: Sequence[ControlPairLike],
    *,
    interpolation: int = cv2.INTER_LINEAR,
    remap_chunk_rows: int = 512,
) -> MultipointRectificationResult:
    """Rectify a distorted track from corresponding left/right control pairs.

    The first/last pairs define the outer four-point homography and therefore
    the complete output canvas. Pixels outside the selected rectangular track
    retain that standard projective rectification. Pixels inside the track are
    resampled directly from the original raster using a piecewise-bilinear mesh,
    so each control pair becomes horizontal and both track boundaries become
    vertical at the specified control rows.

    Direct source remapping avoids a second interpolation pass inside the track.
    ``remap_chunk_rows`` limits temporary map memory for very large scans.
    """
    if not isinstance(image, np.ndarray):
        raise TypeError("image must be a NumPy array.")
    if image.ndim not in (2, 3) or image.size == 0:
        raise ValueError(
            "image must be a non-empty 2-D or 3-D NumPy array."
        )
    if remap_chunk_rows < 1:
        raise ValueError("remap_chunk_rows must be at least 1.")

    transform = compute_multipoint_transform(
        control_pairs,
        image.shape,
    )
    base = transform.base_transform

    # Preserve the complete scan exactly as in the established four-point mode.
    rectified = cv2.warpPerspective(
        image,
        base.transform_matrix,
        base.output_size,
        flags=interpolation,
    )

    destination = base.destination_points
    left_x = int(round(destination[0].x))
    right_x = int(round(destination[1].x))
    top_y = int(round(destination[0].y))
    bottom_y = int(round(destination[3].y))

    if right_x <= left_x or bottom_y <= top_y:
        raise InvalidControlPairsError(
            "The multipoint reference rectangle is too small."
        )

    x_values = np.arange(
        left_x,
        right_x + 1,
        dtype=np.float64,
    )
    u = (
        (x_values - float(destination[0].x))
        / float(destination[1].x - destination[0].x)
    )

    dest_rows = np.asarray(
        transform.destination_y,
        dtype=np.float64,
    )
    left_source = np.asarray(
        [(pair.left.x, pair.left.y) for pair in transform.control_pairs],
        dtype=np.float64,
    )
    right_source = np.asarray(
        [(pair.right.x, pair.right.y) for pair in transform.control_pairs],
        dtype=np.float64,
    )

    for chunk_start in range(
        top_y,
        bottom_y + 1,
        remap_chunk_rows,
    ):
        chunk_end = min(
            bottom_y + 1,
            chunk_start + remap_chunk_rows,
        )
        y_values = np.arange(
            chunk_start,
            chunk_end,
            dtype=np.float64,
        )

        band_index = np.searchsorted(
            dest_rows,
            y_values,
            side="right",
        ) - 1
        band_index = np.clip(
            band_index,
            0,
            len(dest_rows) - 2,
        )

        y0 = dest_rows[band_index]
        y1 = dest_rows[band_index + 1]
        v = (y_values - y0) / (y1 - y0)
        v = np.clip(v, 0.0, 1.0)

        source_left = (
            left_source[band_index] * (1.0 - v[:, None])
            + left_source[band_index + 1] * v[:, None]
        )
        source_right = (
            right_source[band_index] * (1.0 - v[:, None])
            + right_source[band_index + 1] * v[:, None]
        )

        map_x = (
            source_left[:, 0, None]
            + u[None, :]
            * (source_right[:, 0, None] - source_left[:, 0, None])
        ).astype(np.float32)
        map_y = (
            source_left[:, 1, None]
            + u[None, :]
            * (source_right[:, 1, None] - source_left[:, 1, None])
        ).astype(np.float32)

        corrected = cv2.remap(
            image,
            map_x,
            map_y,
            interpolation,
            borderMode=cv2.BORDER_CONSTANT,
        )
        rectified[
            chunk_start:chunk_end,
            left_x:right_x + 1,
        ] = corrected

    return MultipointRectificationResult(
        image=rectified,
        transform=transform,
    )


def apply_homography(
    points: Sequence[PointLike],
    matrix: np.ndarray,
) -> FloatArray:
    """Apply a 3x3 homography to one or more image-coordinate points."""
    point_array = _as_nonempty_point_array(points)
    matrix_array = np.asarray(matrix, dtype=np.float64)

    if matrix_array.shape != (3, 3) or not np.all(np.isfinite(matrix_array)):
        raise ValueError("matrix must be a finite 3x3 homography.")

    homogeneous = np.column_stack(
        (point_array, np.ones(len(point_array), dtype=np.float64))
    )
    transformed = homogeneous @ matrix_array.T
    denominators = transformed[:, 2]

    if np.any(np.isclose(denominators, 0.0, atol=1.0e-12)):
        raise InvalidQuadrilateralError(
            "Homography maps at least one point to infinity."
        )

    return transformed[:, :2] / denominators[:, np.newaxis]


def _build_transform_result(
    *,
    source_array: FloatArray,
    ordered: tuple[ImagePoint, ImagePoint, ImagePoint, ImagePoint],
    matrix: FloatArray,
    output_width: int,
    output_height: int,
    reference_width: int,
    reference_height: int,
    canvas_offset_x: float,
    canvas_offset_y: float,
    full_image: bool,
) -> PerspectiveTransformResult:
    if not np.all(np.isfinite(matrix)):
        raise InvalidQuadrilateralError(
            "The perspective transform contains non-finite values."
        )

    try:
        inverse = np.linalg.inv(matrix)
    except np.linalg.LinAlgError as exc:
        raise InvalidQuadrilateralError(
            "The perspective transform is singular."
        ) from exc

    return PerspectiveTransformResult(
        source_points=_points_from_array(source_array),
        ordered_points=ordered,
        transform_matrix=np.asarray(matrix, dtype=np.float64),
        inverse_matrix=np.asarray(inverse, dtype=np.float64),
        output_width=output_width,
        output_height=output_height,
        reference_width=reference_width,
        reference_height=reference_height,
        canvas_offset_x=float(canvas_offset_x),
        canvas_offset_y=float(canvas_offset_y),
        full_image=bool(full_image),
    )


def _normalize_control_pairs(
    control_pairs: Sequence[ControlPairLike],
) -> tuple[RectificationControlPair, ...]:
    try:
        raw_pairs = list(control_pairs)
    except TypeError as exc:
        raise InvalidControlPairsError(
            "Control pairs must be an ordered sequence."
        ) from exc

    normalized: list[RectificationControlPair] = []
    for index, raw_pair in enumerate(raw_pairs):
        if isinstance(raw_pair, RectificationControlPair):
            left = raw_pair.left
            right = raw_pair.right
        else:
            try:
                if len(raw_pair) != 2:
                    raise ValueError
                raw_left = raw_pair[0]
                raw_right = raw_pair[1]
            except (TypeError, ValueError, IndexError) as exc:
                raise InvalidControlPairsError(
                    f"Control pair {index + 1} must contain one left and one "
                    "right 2-D point."
                ) from exc

            points = _convert_items_to_array((raw_left, raw_right))
            left = ImagePoint(float(points[0, 0]), float(points[0, 1]))
            right = ImagePoint(float(points[1, 0]), float(points[1, 1]))

        normalized.append(
            RectificationControlPair(
                ImagePoint(float(left.x), float(left.y)),
                ImagePoint(float(right.x), float(right.y)),
            )
        )

    return tuple(normalized)


def _image_dimensions(image_shape: Sequence[int]) -> tuple[int, int]:
    if len(image_shape) < 2:
        raise ValueError("image_shape must contain at least height and width.")
    height = int(image_shape[0])
    width = int(image_shape[1])
    if width <= 0 or height <= 0:
        raise ValueError("image_shape must describe a non-empty image.")
    return height, width


def _as_point_array(points: Sequence[PointLike]) -> FloatArray:
    try:
        items = list(points)
    except TypeError as exc:
        raise InvalidQuadrilateralError(
            "Exactly four 2-D points are required."
        ) from exc

    if len(items) != 4:
        raise InvalidQuadrilateralError(
            "Exactly four 2-D points are required."
        )

    return _convert_items_to_array(items)


def _as_nonempty_point_array(points: Sequence[PointLike]) -> FloatArray:
    try:
        items = list(points)
    except TypeError as exc:
        raise ValueError("At least one 2-D point is required.") from exc
    if not items:
        raise ValueError("At least one 2-D point is required.")
    return _convert_items_to_array(items)


def _convert_items_to_array(items: Sequence[PointLike]) -> FloatArray:
    converted: list[tuple[float, float]] = []
    for point in items:
        if isinstance(point, ImagePoint):
            converted.append((float(point.x), float(point.y)))
            continue
        try:
            if len(point) != 2:
                raise ValueError
            converted.append((float(point[0]), float(point[1])))
        except (TypeError, ValueError, IndexError) as exc:
            raise InvalidQuadrilateralError(
                "Each point must contain exactly two numeric coordinates."
            ) from exc

    array = np.asarray(converted, dtype=np.float64)
    if not np.all(np.isfinite(array)):
        raise InvalidQuadrilateralError(
            "Point coordinates must be finite numbers."
        )
    return array


def _validate_point_separation(array: FloatArray) -> None:
    for first in range(4):
        for second in range(first + 1, 4):
            distance = hypot(
                float(array[first, 0] - array[second, 0]),
                float(array[first, 1] - array[second, 1]),
            )
            if distance < _MIN_POINT_SEPARATION_PX:
                raise InvalidQuadrilateralError(
                    "Rectification points must be distinct; two points "
                    "are coincident or nearly coincident."
                )


def _estimate_size_from_ordered(ordered: FloatArray) -> tuple[int, int]:
    top_left, top_right, bottom_right, bottom_left = ordered
    width_top = float(np.linalg.norm(top_right - top_left))
    width_bottom = float(np.linalg.norm(bottom_right - bottom_left))
    height_left = float(np.linalg.norm(bottom_left - top_left))
    height_right = float(np.linalg.norm(bottom_right - top_right))
    width = int(round(max(width_top, width_bottom)))
    height = int(round(max(height_left, height_right)))
    return width, height


def _polygon_area(ordered: FloatArray) -> float:
    x = ordered[:, 0]
    y = ordered[:, 1]
    return 0.5 * abs(
        float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))
    )


def _minimum_area_threshold(array: FloatArray) -> float:
    span_x = float(np.ptp(array[:, 0]))
    span_y = float(np.ptp(array[:, 1]))
    bounding_area = span_x * span_y
    return max(1.0e-6, bounding_area * _MIN_RELATIVE_AREA)


def _points_from_array(
    array: FloatArray,
) -> tuple[ImagePoint, ImagePoint, ImagePoint, ImagePoint]:
    points = tuple(ImagePoint(float(x), float(y)) for x, y in array)
    return cast(
        tuple[ImagePoint, ImagePoint, ImagePoint, ImagePoint],
        points,
    )
