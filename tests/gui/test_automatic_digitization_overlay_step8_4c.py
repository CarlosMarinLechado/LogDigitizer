from __future__ import annotations

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from PySide6.QtCore import QPointF
from PySide6.QtWidgets import QGraphicsScene

from logdigitizer.domain.digitization import TracePoint
from logdigitizer.gui.automatic_digitization_overlay import (
    AutomaticDigitizationOverlay,
)
from logdigitizer.gui.overlays import OverlayManager


def test_overlay_splits_visual_path_across_missing_raw_rows(qtbot) -> None:
    scene = QGraphicsScene()
    overlay = AutomaticDigitizationOverlay(
        OverlayManager(scene),
        lambda point: QPointF(point),
    )
    samples = (
        TracePoint(10, 2),
        TracePoint(10, 3),
        TracePoint(10, 4),
        TracePoint(15, 9),
        TracePoint(15, 10),
        TracePoint(15, 11),
    )

    overlay.set_samples(samples, lost_y_px=12.0)

    assert len(overlay.path_items) == 2
    assert overlay.loss_marker is not None
    assert overlay.raw_samples_px == samples


def test_overlay_clear_removes_transient_geometry(qtbot) -> None:
    scene = QGraphicsScene()
    manager = OverlayManager(scene)
    overlay = AutomaticDigitizationOverlay(manager, lambda point: QPointF(point))
    overlay.set_samples((TracePoint(5, 1), TracePoint(5, 2)))

    assert overlay.path_items
    overlay.clear()

    assert overlay.raw_samples_px == ()
    assert overlay.path_items == ()
    assert overlay.loss_marker is None
