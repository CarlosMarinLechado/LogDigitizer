from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

PIL = pytest.importorskip("PIL.Image")

from logdigitizer.application.project_service import ProjectService
from logdigitizer.processing.geometry import rectify_image_multipoint


def test_apply_multipoint_rectification_persists_reproducible_control_mesh(
    tmp_path: Path,
) -> None:
    imported_source = tmp_path / "input.png"
    PIL.new("L", (220, 180), 123).save(imported_source)

    service = ProjectService()
    service.new_project(
        name="P",
        project_directory=tmp_path / "P_project",
    )
    segment = service.add_images([imported_source])[0]

    source_array = np.full((180, 220), 123, dtype=np.uint8)
    control_pairs = [
        ((35.0, 20.0), (185.0, 35.0)),
        ((29.0, 75.0), (181.0, 82.0)),
        ((24.0, 120.0), (176.0, 118.0)),
        ((20.0, 140.0), (170.0, 155.0)),
    ]
    result = rectify_image_multipoint(source_array, control_pairs)

    saved = service.apply_rectification(result.image, result.transform)

    assert saved.is_file()
    preprocess = segment.extensions["preprocess"]
    assert preprocess["mode"] == "multipoint_pairs"
    assert len(preprocess["corner_points"]) == 4
    assert len(preprocess["control_pairs"]) == 4
    assert preprocess["control_pairs"][1] == [
        [29.0, 75.0],
        [181.0, 82.0],
    ]
    assert preprocess["destination_y"] == list(result.transform.destination_y)
    assert preprocess["multipoint_interpolation"] == "piecewise_bilinear"
    assert preprocess["rectification_scope"] == "full_image"
    assert preprocess["output_size"] == [
        result.transform.output_width,
        result.transform.output_height,
    ]

    json.dumps(segment.extensions, ensure_ascii=False)

    service.save()
    reopened = ProjectService()
    reopened.open_project(service.paths.project_file)
    restored = reopened.active_segment
    assert restored is not None
    assert restored.extensions["preprocess"] == preprocess
