from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")
PIL = pytest.importorskip("PIL.Image")

from PySide6.QtCore import QPointF, Qt
from PySide6.QtWidgets import QGraphicsLineItem

from logdigitizer.gui.rectification import RectificationControlHandle
from logdigitizer.gui.viewer import ImageViewer
from logdigitizer.processing.geometry import MultipointRectificationResult


def create_test_image(
    path: Path,
    size: tuple[int, int] = (300, 220),
) -> None:
    PIL.new("L", size, 128).save(path)


def click_image_point(
    qtbot,
    viewer: ImageViewer,
    x: float,
    y: float,
) -> None:
    scene_point = viewer.image_to_scene(QPointF(x, y))
    viewport_point = viewer.mapFromScene(scene_point)
    qtbot.mouseClick(
        viewer.viewport(),
        Qt.MouseButton.LeftButton,
        pos=viewport_point,
    )


@pytest.fixture
def viewer_with_corners(qtbot, tmp_path: Path) -> ImageViewer:
    path = tmp_path / "multipoint.png"
    create_test_image(path)
    viewer = ImageViewer()
    qtbot.addWidget(viewer)
    viewer.resize(850, 650)
    viewer.show()
    viewer.load_image(path)
    viewer.actual_size()
    viewer.set_rectification_points(
        [
            (30.0, 25.0),
            (270.0, 30.0),
            (260.0, 195.0),
            (35.0, 190.0),
        ]
    )
    return viewer


def control_handles(viewer: ImageViewer) -> list[RectificationControlHandle]:
    return sorted(
        [
            item
            for item in viewer.scene().items()
            if isinstance(item, RectificationControlHandle)
        ],
        key=lambda item: (item.pair_index, item.side),
    )


def drag_control_handle(
    qtbot,
    viewer: ImageViewer,
    pair_index: int,
    side: str,
    x: float,
    y: float,
) -> None:
    handle = next(
        item
        for item in control_handles(viewer)
        if item.pair_index == pair_index and item.side == side
    )
    start = viewer.mapFromScene(handle.scenePos())
    target = viewer.mapFromScene(viewer.image_to_scene(QPointF(x, y)))
    qtbot.mousePress(
        viewer.viewport(),
        Qt.MouseButton.LeftButton,
        pos=start,
    )
    qtbot.mouseMove(viewer.viewport(), pos=target)
    qtbot.mouseRelease(
        viewer.viewport(),
        Qt.MouseButton.LeftButton,
        pos=target,
    )


def test_outer_control_pairs_are_derived_from_four_corners(
    viewer_with_corners: ImageViewer,
) -> None:
    pairs = viewer_with_corners.rectification_control_pairs

    assert len(pairs) == 2
    assert pairs[0].left.x == pytest.approx(30.0)
    assert pairs[0].right.x == pytest.approx(270.0)
    assert pairs[-1].left.x == pytest.approx(35.0)
    assert pairs[-1].right.x == pytest.approx(260.0)


def test_one_explicit_request_captures_exactly_one_left_right_pair(
    qtbot,
    viewer_with_corners: ImageViewer,
) -> None:
    viewer = viewer_with_corners

    assert viewer.set_rectification_control_pair_capture_enabled(True)
    assert viewer.rectification_control_pair_capture_enabled

    click_image_point(qtbot, viewer, 45.0, 85.0)
    assert viewer.rectification.has_pending_control_point
    assert len(viewer.rectification_extra_control_pairs) == 0

    click_image_point(qtbot, viewer, 250.0, 90.0)

    assert not viewer.rectification.has_pending_control_point
    assert not viewer.rectification_control_pair_capture_enabled
    assert len(viewer.rectification_extra_control_pairs) == 1
    assert len(viewer.rectification_control_pairs) == 3
    assert viewer.rectification.multipoint_is_geometry_valid is True

    # A third click must not silently start another pair.
    click_image_point(qtbot, viewer, 50.0, 120.0)
    assert len(viewer.rectification_extra_control_pairs) == 1
    assert not viewer.rectification.has_pending_control_point


def test_intermediate_pair_renders_two_draggable_handles_and_mesh_lines(
    qtbot,
    viewer_with_corners: ImageViewer,
) -> None:
    viewer = viewer_with_corners
    viewer.set_rectification_control_pair_capture_enabled(True)
    click_image_point(qtbot, viewer, 45.0, 85.0)
    click_image_point(qtbot, viewer, 250.0, 90.0)

    handles = control_handles(viewer)
    assert len(handles) == 2
    assert all(handle.parentItem() is None for handle in handles)

    grouped = viewer.overlays.layer("rectification").childItems()
    # Four established quad lines + one transverse control line + four
    # side-envelope segments for one intermediate pair.
    assert sum(isinstance(item, QGraphicsLineItem) for item in grouped) >= 9


def test_drag_control_point_after_zoom_keeps_image_coordinates(
    qtbot,
    viewer_with_corners: ImageViewer,
) -> None:
    viewer = viewer_with_corners
    viewer.set_rectification_control_pair_capture_enabled(True)
    click_image_point(qtbot, viewer, 45.0, 85.0)
    click_image_point(qtbot, viewer, 250.0, 90.0)

    viewer.zoom_in()
    viewer.zoom_in()
    drag_control_handle(qtbot, viewer, 0, "left", 60.0, 100.0)

    pair = viewer.rectification_extra_control_pairs[0]
    assert pair.left.x == pytest.approx(60.0, abs=1.5)
    assert pair.left.y == pytest.approx(100.0, abs=1.5)


def test_clear_intermediate_pairs_preserves_established_corners(
    qtbot,
    viewer_with_corners: ImageViewer,
) -> None:
    viewer = viewer_with_corners
    corners = viewer.rectification_points

    viewer.set_rectification_control_pair_capture_enabled(True)
    click_image_point(qtbot, viewer, 45.0, 85.0)
    click_image_point(qtbot, viewer, 250.0, 90.0)
    assert len(viewer.rectification_extra_control_pairs) == 1

    viewer.clear_rectification_control_pairs()

    assert viewer.rectification_points == corners
    assert viewer.rectification_extra_control_pairs == ()
    assert len(viewer.rectification_control_pairs) == 2
    assert control_handles(viewer) == []


def test_multipoint_preview_uses_control_mesh_without_replacing_source(
    qtbot,
    viewer_with_corners: ImageViewer,
) -> None:
    viewer = viewer_with_corners
    source_path = viewer.image_path
    pixmap_key = viewer._pixmap_item.pixmap().cacheKey()

    viewer.set_rectification_control_pair_capture_enabled(True)
    click_image_point(qtbot, viewer, 45.0, 85.0)
    click_image_point(qtbot, viewer, 250.0, 90.0)

    result = viewer.compute_rectification_preview(multipoint=True)

    assert isinstance(result, MultipointRectificationResult)
    assert result.image.shape == (
        result.transform.output_height,
        result.transform.output_width,
    )
    assert viewer.image_path == source_path
    assert viewer._pixmap_item.pixmap().cacheKey() == pixmap_key
    assert len(viewer.rectification_extra_control_pairs) == 1
