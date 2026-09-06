from __future__ import annotations

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from logdigitizer.gui.preprocess_panel import (
    PreprocessPanel,
)


def test_preview_and_apply_enable_only_for_valid_four_points(
    qtbot,
) -> None:
    panel = PreprocessPanel()
    qtbot.addWidget(panel)
    panel.set_image_available(True)

    panel.set_selection_state(
        point_count=3,
        is_valid=None,
    )
    assert not panel.preview_button.isEnabled()
    assert not panel.apply_button.isEnabled()

    panel.set_selection_state(
        point_count=4,
        is_valid=False,
        error="degenerate",
    )
    assert not panel.preview_button.isEnabled()
    assert not panel.apply_button.isEnabled()

    panel.set_selection_state(
        point_count=4,
        is_valid=True,
        estimated_size=(100, 200),
    )
    assert panel.preview_button.isEnabled()
    assert panel.apply_button.isEnabled()
    assert "100 × 200" in panel.size_label.text()
