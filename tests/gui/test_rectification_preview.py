from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")
PIL = pytest.importorskip("PIL.Image")

from logdigitizer.gui.rectification_preview import (
    RectificationPreviewDialog,
)
from logdigitizer.gui.viewer import ImageViewer
from logdigitizer.processing.geometry import (
    InvalidQuadrilateralError,
)


@pytest.fixture
def viewer_with_selection(
    qtbot,
    tmp_path: Path,
) -> ImageViewer:
    path = tmp_path / "source.png"
    PIL.new(
        "L",
        (240, 180),
        128,
    ).save(path)

    viewer = ImageViewer()
    qtbot.addWidget(viewer)
    viewer.resize(700, 500)
    viewer.show()
    viewer.load_image(path)

    viewer.set_rectification_points(
        [
            (20.0, 20.0),
            (220.0, 25.0),
            (215.0, 160.0),
            (25.0, 165.0),
        ]
    )

    return viewer


def test_preview_does_not_replace_or_clear_source_view(
    viewer_with_selection: ImageViewer,
) -> None:
    viewer = viewer_with_selection

    source_path = viewer.image_path
    pixmap_key = (
        viewer._pixmap_item
        .pixmap()
        .cacheKey()
    )
    points = viewer.rectification_points

    result = (
        viewer.compute_rectification_preview()
    )

    assert result.image.shape[1] == (
        result.transform.output_width
    )
    assert result.image.shape[0] == (
        result.transform.output_height
    )
    assert viewer.image_path == source_path
    assert (
        viewer._pixmap_item
        .pixmap()
        .cacheKey()
        == pixmap_key
    )
    assert viewer.rectification_points == points


def test_preview_dialog_displays_rectified_raster(
    qtbot,
    viewer_with_selection: ImageViewer,
) -> None:
    result = (
        viewer_with_selection
        .compute_rectification_preview()
    )

    dialog = RectificationPreviewDialog(
        result.image,
        result.transform.output_size,
    )
    qtbot.addWidget(dialog)

    assert not dialog.preview_pixmap.isNull()
    assert (
        dialog.preview_pixmap.width()
        == result.transform.output_width
    )
    assert (
        dialog.preview_pixmap.height()
        == result.transform.output_height
    )


def test_preview_requires_four_points(
    qtbot,
    tmp_path: Path,
) -> None:
    path = tmp_path / "source.png"
    PIL.new(
        "L",
        (100, 100),
        128,
    ).save(path)

    viewer = ImageViewer()
    qtbot.addWidget(viewer)
    viewer.load_image(path)
    viewer.set_rectification_points(
        [
            (5.0, 5.0),
            (90.0, 5.0),
            (90.0, 90.0),
        ]
    )

    with pytest.raises(
        InvalidQuadrilateralError,
        match="four",
    ):
        viewer.compute_rectification_preview()
