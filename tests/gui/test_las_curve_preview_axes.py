from __future__ import annotations

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")
pytest.importorskip("qtawesome")

from PySide6.QtGui import QColor
from PySide6.QtWidgets import QGraphicsItem, QGraphicsTextItem

from logdigitizer.domain.calibration import ScaleType
from logdigitizer.gui.las_curve_preview import LasCurveGraphicsView


def test_linear_axes_use_clean_nice_ticks_and_curve_color(qtbot) -> None:
    view = LasCurveGraphicsView()
    qtbot.addWidget(view)

    depths = tuple(float(value) for value in range(0, 1201, 100))
    values = tuple(float(value) / 8.0 for value in range(0, 1201, 100))
    view.set_curve(
        depths=depths,
        values=values,
        mnemonic="GR",
        unit="API",
        depth_unit="m",
        color="#FF8C00",
        scale_type=ScaleType.LINEAR,
    )

    assert view._curve_color == QColor("#FF8C00")
    assert all(float(tick).is_integer() for tick in view.depth_ticks)
    assert view.depth_ticks == (0.0, 200.0, 400.0, 600.0, 800.0, 1000.0, 1200.0)
    assert all(
        view._format_tick(tick) == str(int(tick))
        for tick in view.depth_ticks
    )

    # The first/last useful samples sit slightly inside the plotting area.
    first_y = view.scene_y_for_depth(min(depths))
    last_y = view.scene_y_for_depth(max(depths))
    assert first_y is not None
    assert last_y is not None
    assert first_y > view.TOP_MARGIN
    assert last_y < (
        view.TOP_MARGIN
        + view.BASE_PLOT_HEIGHT * view.depth_stretch
    )

    # Axis labels and titles keep a constant readable on-screen size when the
    # view is zoomed, fitted or depth-compressed/stretched.
    text_items = [
        item
        for item in view.scene().items()
        if isinstance(item, QGraphicsTextItem)
    ]
    assert text_items
    assert all(
        item.flags()
        & QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations
        for item in text_items
    )


def test_logarithmic_x_axis_uses_equal_decade_spacing(qtbot) -> None:
    view = LasCurveGraphicsView()
    qtbot.addWidget(view)

    view.set_curve(
        depths=(0.0, 10.0, 20.0, 30.0, 40.0),
        values=(0.1, 1.0, 10.0, 100.0, 1000.0),
        mnemonic="ILD",
        unit="OHMM",
        depth_unit="m",
        color="#C64545",
        scale_type=ScaleType.LOGARITHMIC,
    )

    assert view.value_ticks == (0.1, 1.0, 10.0, 100.0, 1000.0)

    xs = [
        view._value_to_scene_x(value)
        for value in view.value_ticks
    ]
    spacings = [
        second - first
        for first, second in zip(xs, xs[1:])
    ]
    assert max(spacings) - min(spacings) < 1e-8


def test_small_linear_ranges_keep_clean_decimal_ticks_when_needed(qtbot) -> None:
    view = LasCurveGraphicsView()
    qtbot.addWidget(view)

    view.set_curve(
        depths=(100.2, 100.5, 100.8),
        values=(0.1, 0.3, 0.5),
        mnemonic="NPHI",
        unit="V/V",
        depth_unit="m",
        color="#00B8D9",
        scale_type=ScaleType.LINEAR,
    )

    # Depth labels remain whole metres, while a genuinely fractional X scale
    # is allowed to retain sensible decimal ticks.
    assert all(float(tick).is_integer() for tick in view.depth_ticks)
    assert any(not float(tick).is_integer() for tick in view.value_ticks)


def test_depth_axis_uses_only_valid_curve_interval_and_independent_ticks(
    qtbot,
) -> None:
    view = LasCurveGraphicsView()
    qtbot.addWidget(view)

    depths = tuple(float(value) for value in range(0, 1301, 100))
    values = tuple(
        None if depth < 200.0 else float(index + 1)
        for index, depth in enumerate(depths)
    )

    view.set_curve(
        depths=depths,
        values=values,
        mnemonic="ILD",
        unit="OHMM",
        depth_unit="m",
        color="#FF00FF",
        scale_type=ScaleType.LOGARITHMIC,
    )

    # Overall LAS depth starts at 0 m, but this selected curve starts at 200 m.
    assert view._depth_min < 200.0
    assert view._depth_min > 0.0
    assert view._depth_max > 1300.0
    assert view.depth_ticks[0] >= 200.0
    assert 0.0 not in view.depth_ticks
