"""Central QtAwesome icon palette for the desktop interface."""

from __future__ import annotations

import qtawesome as qta
from PySide6.QtGui import QIcon

ICON_NORMAL = "#4A5560"
ICON_ACTIVE = "#1F5F8B"
ICON_DISABLED = "#B8BEC5"
ICON_DANGER = "#C64545"
ICON_SUCCESS = "#438A5E"
ICON_WARNING = "#D58B20"
ICON_MUTED = "#8B949E"


def app_icon(name: str) -> QIcon:
    """Return a standard Font Awesome 6 Solid application icon."""
    qualified_name = name if "." in name else f"fa6s.{name}"
    return qta.icon(
        qualified_name,
        color=ICON_NORMAL,
        color_active=ICON_ACTIVE,
        color_selected=ICON_ACTIVE,
        color_disabled=ICON_DISABLED,
    )


def highlighted_icon(name: str) -> QIcon:
    """Return an icon that remains highlighted while a mode is active."""
    qualified_name = name if "." in name else f"fa6s.{name}"
    return qta.icon(
        qualified_name,
        color=ICON_ACTIVE,
        color_active=ICON_ACTIVE,
        color_selected=ICON_ACTIVE,
        color_disabled=ICON_DISABLED,
    )


def danger_icon(name: str) -> QIcon:
    """Return a destructive-action icon: neutral normally, red on interaction."""
    qualified_name = name if "." in name else f"fa6s.{name}"
    return qta.icon(
        qualified_name,
        color=ICON_NORMAL,
        color_active=ICON_DANGER,
        color_selected=ICON_DANGER,
        color_disabled=ICON_DISABLED,
    )


def colored_icon(name: str, color: str) -> QIcon:
    """Return a Font Awesome 6 Solid icon in one explicit semantic color."""
    qualified_name = name if "." in name else f"fa6s.{name}"
    return qta.icon(
        qualified_name,
        color=color,
        color_active=color,
        color_selected=color,
        color_disabled=ICON_DISABLED,
    )
