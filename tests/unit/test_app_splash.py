from unittest.mock import Mock

import logdigitizer.app as app_module
from logdigitizer.app import (
    SPLASH_MINIMUM_DURATION_MS,
    _remaining_splash_time_ms,
    _show_main_window,
    _splash_image_path,
)


def test_splash_minimum_duration_is_one_and_a_half_seconds() -> None:
    assert SPLASH_MINIMUM_DURATION_MS == 1500


def test_remaining_splash_time_never_becomes_negative() -> None:
    assert _remaining_splash_time_ms(0) == 1500
    assert _remaining_splash_time_ms(500) == 1000
    assert _remaining_splash_time_ms(1500) == 0
    assert _remaining_splash_time_ms(2500) == 0


def test_negative_elapsed_time_is_treated_as_zero() -> None:
    assert _remaining_splash_time_ms(-1) == 1500


def test_main_window_is_revealed_before_splash_finishes() -> None:
    window = Mock()
    splash = Mock()

    _show_main_window(window, splash)

    window.show.assert_called_once_with()
    splash.finish.assert_called_once_with(window)


def test_main_window_is_revealed_when_splash_is_unavailable() -> None:
    window = Mock()

    _show_main_window(window, None)

    window.show.assert_called_once_with()


def test_splash_path_uses_image_next_to_module(tmp_path, monkeypatch) -> None:
    module_file = tmp_path / "logdigitizer" / "app.py"
    module_file.parent.mkdir()
    module_file.write_text("", encoding="utf-8")
    adjacent_splash = module_file.with_name("splash_screen.png")
    adjacent_splash.write_bytes(b"PNG")
    monkeypatch.setattr(app_module, "__file__", str(module_file))

    assert _splash_image_path() == adjacent_splash


def test_splash_path_supports_internal_package_layout(
    tmp_path,
    monkeypatch,
) -> None:
    internal_root = tmp_path / "_internal"
    module_file = internal_root / "app.py"
    monkeypatch.setattr(app_module, "__file__", str(module_file))
    monkeypatch.setattr(
        app_module,
        "bundled_resource_path",
        lambda relative_path: internal_root / relative_path,
    )

    assert _splash_image_path() == (
        internal_root / "logdigitizer" / "splash_screen.png"
    )
