"""Application entry point."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QElapsedTimer, QTimer, Qt
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import QApplication, QSplashScreen, QWidget

from logdigitizer.utils.logging import configure_logging, get_logger
from logdigitizer.utils.paths import application_icon_path, bundled_resource_path

SPLASH_MINIMUM_DURATION_MS = 1500
SPLASH_SCREEN_RELATIVE_PATH = Path("logdigitizer") / "splash_screen.png"


def _remaining_splash_time_ms(elapsed_ms: int) -> int:
    """Return the remaining time needed to meet the minimum splash duration."""
    return max(0, SPLASH_MINIMUM_DURATION_MS - max(0, elapsed_ms))


def _show_main_window(
    window: QWidget,
    splash: QSplashScreen | None,
) -> None:
    """Reveal the main window and close the splash in the correct order."""
    window.show()
    if splash is not None:
        splash.finish(window)


def _splash_image_path() -> Path:
    """Locate the splash in source and in both supported bundle layouts."""
    adjacent_path = Path(__file__).resolve().with_name("splash_screen.png")
    if adjacent_path.is_file():
        return adjacent_path
    return bundled_resource_path(SPLASH_SCREEN_RELATIVE_PATH)


def _create_splash(app: QApplication) -> QSplashScreen | None:
    """Create and display the startup splash screen when its image exists."""
    image_path = _splash_image_path()
    pixmap = QPixmap(str(image_path))

    if pixmap.isNull():
        return None

    screen = app.primaryScreen()
    if screen is not None:
        available = screen.availableGeometry().size()
        max_width = min(960, int(available.width() * 0.85))
        max_height = int(available.height() * 0.85)
        pixmap = pixmap.scaled(
            max_width,
            max_height,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )

    splash = QSplashScreen(
        pixmap,
        Qt.WindowType.WindowStaysOnTopHint,
    )
    splash.show()

    # Paint the splash before importing and constructing the main window.
    app.processEvents()
    return splash


def main() -> int:
    """Start the desktop application and return the Qt exit code."""
    app = QApplication(sys.argv)
    app.setApplicationName("Digitalización de Logs")
    app.setOrganizationName("IGME-CSIC")

    icon_path = application_icon_path()
    if icon_path.is_file():
        app.setWindowIcon(QIcon(str(icon_path)))

    splash = _create_splash(app)
    splash_timer = QElapsedTimer()
    if splash is not None:
        splash_timer.start()

    configure_logging()
    logger = get_logger("app")

    try:
        # Keep this import here so the splash is already visible while the
        # application modules and the main interface are being loaded.
        from logdigitizer.gui.main_window import MainWindow

        window = MainWindow()
        app.processEvents()
    except Exception:
        if splash is not None:
            splash.close()
        raise

    remaining_ms = (
        _remaining_splash_time_ms(splash_timer.elapsed())
        if splash is not None
        else 0
    )
    if remaining_ms > 0:
        QTimer.singleShot(
            remaining_ms,
            lambda: _show_main_window(window, splash),
        )
    else:
        _show_main_window(window, splash)

    logger.info("Application started")
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
