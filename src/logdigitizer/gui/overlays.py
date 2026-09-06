"""Reusable overlay-layer management for the image scene."""

from __future__ import annotations

from PySide6.QtWidgets import QGraphicsItem, QGraphicsItemGroup, QGraphicsScene


class OverlayManager:
    """Manage named overlays without modifying the base raster.

    Visual items normally live in a QGraphicsItemGroup. Interactive items such
    as draggable handles can instead be direct scene items while remaining
    logically associated with the same named overlay layer.
    """

    def __init__(self, scene: QGraphicsScene) -> None:
        self._scene = scene
        self._layers: dict[str, QGraphicsItemGroup] = {}
        self._scene_items: dict[str, list[QGraphicsItem]] = {}

    def layer(self, name: str) -> QGraphicsItemGroup:
        """Return the named grouped overlay, creating it when necessary."""
        if name not in self._layers:
            group = QGraphicsItemGroup()
            group.setZValue(10.0)
            self._scene.addItem(group)
            self._layers[name] = group
        return self._layers[name]

    def add_item(self, layer_name: str, item: QGraphicsItem) -> QGraphicsItem:
        """Add a normal visual item to a grouped overlay layer."""
        self.layer(layer_name).addToGroup(item)
        return item

    def add_scene_item(self, layer_name: str, item: QGraphicsItem) -> QGraphicsItem:
        """Add an independently interactive item directly to the scene."""
        if item.scene() is not self._scene:
            self._scene.addItem(item)
        self._scene_items.setdefault(layer_name, []).append(item)
        return item

    def set_visible(self, layer_name: str, visible: bool) -> None:
        """Show/hide grouped and direct items belonging to one logical layer."""
        self.layer(layer_name).setVisible(visible)
        for item in self._scene_items.get(layer_name, []):
            item.setVisible(visible)

    def clear_layer(self, layer_name: str) -> None:
        """Remove all items from one logical layer but keep its group reusable."""
        group = self.layer(layer_name)
        for item in list(group.childItems()):
            group.removeFromGroup(item)
            if item.scene() is self._scene:
                self._scene.removeItem(item)

        for item in self._scene_items.pop(layer_name, []):
            if item.scene() is self._scene:
                self._scene.removeItem(item)

    def clear_all(self) -> None:
        """Clear every known logical overlay layer."""
        for name in set(self._layers) | set(self._scene_items):
            self.clear_layer(name)
