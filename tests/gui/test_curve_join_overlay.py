from __future__ import annotations

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from PySide6.QtCore import QPointF, Qt
from PySide6.QtWidgets import QGraphicsItem, QGraphicsScene

from logdigitizer.gui.curve_join_overlay import (
    ImageOverlapGuideDefinition,
    ImageOverlapGuideOverlay,
)
from logdigitizer.gui.overlays import OverlayManager


def test_overlap_overlay_draws_red_dashed_line_and_instruction_above(
    qtbot,
) -> None:
    del qtbot
    scene = QGraphicsScene()
    overlay = ImageOverlapGuideOverlay(
        OverlayManager(scene),
        lambda point: QPointF(point),
    )

    text = (
        "Overlap with previous image — digitize slightly above this line."
    )
    overlay.set_guides(
        (
            ImageOverlapGuideDefinition(
                y_px=50.0,
                text=text,
                label_above=True,
                tooltip="Image 1 · Overlap boundary: 1010 m",
            ),
        ),
        image_width=200.0,
    )

    assert len(overlay.line_items) == 1
    assert len(overlay.label_items) == 1
    item = overlay.line_items[0]
    assert item.line().x1() == pytest.approx(0.0)
    assert item.line().x2() == pytest.approx(200.0)
    assert item.line().y1() == pytest.approx(50.0)
    assert item.line().y2() == pytest.approx(50.0)
    assert item.pen().color().name().upper() == "#DC0000"
    assert item.pen().style() == Qt.PenStyle.DashLine
    assert item.pen().isCosmetic() is True
    assert item.acceptedMouseButtons() == Qt.MouseButton.NoButton

    label = overlay.label_items[0]
    assert label.text == text
    assert label.label_above is True
    assert label.label_rect.bottom() < 0.0
    assert label.pos().y() == pytest.approx(50.0)
    assert label.TEXT_COLOR.name().upper() == "#DC0000"
    assert (
        label.flags()
        & QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations
    )
    assert label.acceptedMouseButtons() == Qt.MouseButton.NoButton


def test_overlap_overlay_positions_previous_above_and_next_below(qtbot) -> None:
    del qtbot
    scene = QGraphicsScene()
    overlay = ImageOverlapGuideOverlay(
        OverlayManager(scene),
        lambda point: QPointF(point),
    )

    overlay.set_guides(
        (
            ImageOverlapGuideDefinition(
                10.0,
                "Overlap with previous image — digitize slightly above this line.",
                True,
                "Previous boundary",
            ),
            ImageOverlapGuideDefinition(
                90.0,
                "Overlap with next image — digitize slightly below this line.",
                False,
                "Next boundary",
            ),
        ),
        image_width=120.0,
    )

    assert [item.line().y1() for item in overlay.line_items] == [10.0, 90.0]
    assert overlay.label_items[0].label_rect.bottom() < 0.0
    assert overlay.label_items[1].label_rect.top() > 0.0
