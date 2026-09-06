from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

PIL = pytest.importorskip("PIL.Image")

from logdigitizer.application.project_service import ProjectService
from logdigitizer.domain.states import StageStatus
from logdigitizer.processing.enhancement import (
    EnhancementSettings,
    enhance_image,
)
from logdigitizer.processing.geometry import compute_perspective_transform


def _prepare_rectified_segment(tmp_path: Path):
    source = tmp_path / "source.png"
    PIL.new("L", (140, 120), 110).save(source)

    service = ProjectService()
    service.new_project(
        name="P",
        project_directory=tmp_path / "P_project",
    )
    segment = service.add_images([source])[0]

    transform = compute_perspective_transform(
        [
            (10.0, 10.0),
            (130.0, 10.0),
            (130.0, 110.0),
            (10.0, 110.0),
        ]
    )
    rectified = np.full(
        (
            transform.output_height,
            transform.output_width,
        ),
        110,
        dtype=np.uint8,
    )
    service.apply_rectification(
        rectified,
        transform,
    )
    return service, segment, rectified


def test_apply_enhancement_persists_working_image_and_settings(tmp_path: Path):
    service, segment, rectified = _prepare_rectified_segment(tmp_path)

    settings = EnhancementSettings(
        black_point=25,
        midtones=0.80,
        white_point=220,
    )
    enhanced = enhance_image(
        rectified,
        settings,
    )

    saved = service.apply_enhancement(
        enhanced,
        settings,
    )

    assert saved.is_file()
    assert saved.parent == service.paths.preprocessed
    assert saved != service.resolve_segment_preprocessed(segment)

    preprocess = segment.extensions["preprocess"]
    assert preprocess["working_path"].startswith("preprocessed/")
    persisted = preprocess["enhancement_settings"]
    assert persisted == {
        "black_point": 25,
        "midtones": pytest.approx(0.80),
        "white_point": 220,
    }
    assert "brightness" not in persisted
    assert "contrast" not in persisted
    assert "local_contrast_enabled" not in persisted
    assert "local_contrast_clip_limit" not in persisted
    assert "local_contrast_grid_size" not in persisted

    json.dumps(segment.extensions, ensure_ascii=False)


def test_enhancement_settings_and_working_path_survive_reopen(tmp_path: Path):
    service, segment, rectified = _prepare_rectified_segment(tmp_path)
    settings = EnhancementSettings(
        black_point=18,
        midtones=1.20,
        white_point=230,
    )
    service.apply_enhancement(
        enhance_image(rectified, settings),
        settings,
    )
    service.save()

    reopened = ProjectService()
    reopened.open_project(service.paths.project_file)

    restored = reopened.get_enhancement_settings()
    assert restored == settings

    working = reopened.resolve_segment_working()
    assert working is not None
    assert working.is_file()


def test_legacy_clahe_keys_are_ignored_when_restoring_settings(tmp_path: Path):
    service, segment, _ = _prepare_rectified_segment(tmp_path)
    segment.extensions["preprocess"]["enhancement_settings"] = {
        "black_point": 12,
        "midtones": 0.85,
        "white_point": 225,
        "local_contrast_enabled": True,
        "local_contrast_clip_limit": 4.0,
        "local_contrast_grid_size": [6, 6],
    }

    restored = service.get_enhancement_settings(segment)

    assert restored == EnhancementSettings(
        black_point=12,
        midtones=0.85,
        white_point=225,
    )


def test_working_path_falls_back_to_rectified_before_enhancement(tmp_path: Path):
    service, segment, _ = _prepare_rectified_segment(tmp_path)

    assert service.resolve_segment_working(segment) == (
        service.resolve_segment_preprocessed(segment)
    )


def test_enhancement_does_not_invalidate_calibration_or_manual_digitization(
    tmp_path: Path,
):
    service, segment, rectified = _prepare_rectified_segment(tmp_path)

    segment.calibration_status = StageStatus.COMPLETE
    segment.digitization_status = StageStatus.COMPLETE

    settings = EnhancementSettings(
        black_point=15,
        midtones=0.90,
        white_point=235,
    )
    service.apply_enhancement(
        enhance_image(rectified, settings),
        settings,
    )

    assert segment.calibration_status == StageStatus.COMPLETE
    assert segment.digitization_status == StageStatus.COMPLETE


def test_enhancement_requires_applied_rectification(tmp_path: Path):
    source = tmp_path / "source.png"
    PIL.new("L", (50, 50), 100).save(source)

    service = ProjectService()
    service.new_project(
        name="P",
        project_directory=tmp_path / "P_project",
    )
    service.add_images([source])

    with pytest.raises(
        RuntimeError,
        match="rectification",
    ):
        service.apply_enhancement(
            np.zeros((20, 20), dtype=np.uint8),
            EnhancementSettings(),
        )
