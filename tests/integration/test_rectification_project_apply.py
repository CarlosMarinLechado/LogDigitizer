from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

PIL = pytest.importorskip("PIL.Image")

from logdigitizer.application.project_service import (
    ProjectService,
)
from logdigitizer.domain.states import StageStatus
from logdigitizer.processing.geometry import (
    compute_perspective_transform,
)


def test_apply_rectification_saves_derived_and_persists_parameters(
    tmp_path: Path,
) -> None:
    imported_source = tmp_path / "input.png"
    PIL.new(
        "L",
        (120, 100),
        123,
    ).save(imported_source)
    input_bytes = imported_source.read_bytes()

    service = ProjectService()
    service.new_project(
        name="P",
        project_directory=tmp_path / "P_project",
    )
    segment = service.add_images(
        [imported_source]
    )[0]

    copied_source = (
        service.resolve_segment_source(segment)
    )
    assert copied_source is not None
    copied_bytes_before = (
        copied_source.read_bytes()
    )

    transform = compute_perspective_transform(
        [
            (10.0, 10.0),
            (110.0, 10.0),
            (110.0, 90.0),
            (10.0, 90.0),
        ]
    )
    rectified = np.full(
        (
            transform.output_height,
            transform.output_width,
        ),
        200,
        dtype=np.uint8,
    )

    saved = service.apply_rectification(
        rectified,
        transform,
    )

    assert saved.is_file()
    assert (
        saved.parent
        == service.paths.preprocessed
    )
    assert copied_source.read_bytes() == (
        copied_bytes_before
    )
    assert imported_source.read_bytes() == (
        input_bytes
    )
    assert (
        segment.preprocess_status
        == StageStatus.COMPLETE
    )
    assert service.dirty is True

    preprocess = (
        segment.extensions["preprocess"]
    )
    assert (
        preprocess["mode"]
        == "perspective_4pt"
    )
    assert len(
        preprocess["corner_points"]
    ) == 4
    assert len(
        preprocess["transform_matrix"]
    ) == 3
    assert (
        preprocess["output_size"]
        == [
            transform.output_width,
            transform.output_height,
        ]
    )
    assert preprocess["reference_grid_size"] == [
        transform.reference_size[0],
        transform.reference_size[1],
    ]
    assert preprocess["canvas_offset"] == [
        transform.canvas_offset_x,
        transform.canvas_offset_y,
    ]
    assert preprocess["rectification_scope"] == "reference_quad"
    assert preprocess[
        "derived_path"
    ].startswith("preprocessed/")

    # Explicit JSON compatibility is a key persistence requirement.
    json.dumps(
        segment.extensions,
        ensure_ascii=False,
    )

    service.save()
    project_file = service.paths.project_file

    reopened = ProjectService()
    reopened.open_project(project_file)
    restored_segment = (
        reopened.active_segment
    )
    assert restored_segment is not None
    assert (
        restored_segment
        .extensions["preprocess"]
        == preprocess
    )
    assert (
        reopened
        .resolve_segment_preprocessed()
        .is_file()
    )


def test_reapplying_preprocess_marks_downstream_review_required(
    tmp_path: Path,
) -> None:
    imported_source = tmp_path / "input.png"
    PIL.new(
        "L",
        (120, 100),
        123,
    ).save(imported_source)

    service = ProjectService()
    service.new_project(
        name="P",
        project_directory=tmp_path / "P_project",
    )
    segment = service.add_images(
        [imported_source]
    )[0]
    segment.calibration_status = (
        StageStatus.COMPLETE
    )
    segment.digitization_status = (
        StageStatus.COMPLETE
    )

    transform = compute_perspective_transform(
        [
            (10.0, 10.0),
            (110.0, 10.0),
            (110.0, 90.0),
            (10.0, 90.0),
        ]
    )
    rectified = np.zeros(
        (
            transform.output_height,
            transform.output_width,
        ),
        dtype=np.uint8,
    )

    service.apply_rectification(
        rectified,
        transform,
    )

    assert (
        segment.calibration_status
        == StageStatus.REVIEW_REQUIRED
    )
    assert (
        segment.digitization_status
        == StageStatus.REVIEW_REQUIRED
    )



def test_full_image_rectification_persists_full_canvas_metadata(
    tmp_path: Path,
) -> None:
    imported_source = tmp_path / "input_full.png"
    PIL.new("L", (220, 180), 123).save(imported_source)

    service = ProjectService()
    service.new_project(
        name="P",
        project_directory=tmp_path / "P_full_project",
    )
    segment = service.add_images([imported_source])[0]

    from logdigitizer.processing.geometry import rectify_image

    source_array = np.full((180, 220), 123, dtype=np.uint8)
    result = rectify_image(
        source_array,
        [
            (35.0, 20.0),
            (185.0, 35.0),
            (170.0, 155.0),
            (20.0, 140.0),
        ],
    )

    saved = service.apply_rectification(
        result.image,
        result.transform,
    )

    assert saved.is_file()
    preprocess = segment.extensions["preprocess"]
    assert preprocess["rectification_scope"] == "full_image"
    assert preprocess["output_size"] == [
        result.transform.output_width,
        result.transform.output_height,
    ]
    assert preprocess["reference_grid_size"] == [
        result.transform.reference_size[0],
        result.transform.reference_size[1],
    ]
    assert preprocess["canvas_offset"] == [
        result.transform.canvas_offset_x,
        result.transform.canvas_offset_y,
    ]
