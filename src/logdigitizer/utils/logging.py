"""Logging configuration for console and per-project technical logs."""

from __future__ import annotations

import logging
from pathlib import Path

LOGGER_NAME = "logdigitizer"


def configure_logging(project_root: Path | None = None) -> logging.Logger:
    """Configure one package logger and optionally a project file handler."""
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.INFO)
    logger.propagate = False

    if not any(getattr(handler, "_logdigitizer_console", False) for handler in logger.handlers):
        console = logging.StreamHandler()
        console._logdigitizer_console = True  # type: ignore[attr-defined]
        console.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        logger.addHandler(console)

    for handler in list(logger.handlers):
        if getattr(handler, "_logdigitizer_project_file", False):
            logger.removeHandler(handler)
            handler.close()

    if project_root is not None:
        log_dir = project_root / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_dir / "logdigitizer.log", encoding="utf-8")
        file_handler._logdigitizer_project_file = True  # type: ignore[attr-defined]
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s %(module)s: %(message)s")
        )
        logger.addHandler(file_handler)

    return logger


def get_logger(name: str | None = None) -> logging.Logger:
    """Return a child logger under the application namespace."""
    return logging.getLogger(LOGGER_NAME if not name else f"{LOGGER_NAME}.{name}")
