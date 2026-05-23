from __future__ import annotations

import json

from PyQt5.QtCore import QObject, QUrl, pyqtSignal, pyqtSlot
from PyQt5.QtWebChannel import QWebChannel
from PyQt5.QtWebEngineWidgets import QWebEngineView

from annotator.resources import load_map_html


class MapBridge(QObject):
    """Empfängt Aufrufe aus dem JavaScript und leitet sie als Qt-Signale weiter."""

    ready_signal         = pyqtSignal()
    node_clicked_signal  = pyqtSignal(str)
    route_node_signal    = pyqtSignal(str)

    @pyqtSlot()
    def mapReady(self) -> None:
        self.ready_signal.emit()

    @pyqtSlot(str)
    def nodeClicked(self, node_key: str) -> None:
        self.node_clicked_signal.emit(node_key)

    @pyqtSlot(str)
    def routeNodeAdded(self, node_key: str) -> None:
        self.route_node_signal.emit(node_key)


class MapWidget(QWebEngineView):
    """Zeigt eine interaktive OSM-Karte mit Knoten-Markern und Routen-Builder."""

    node_selected = pyqtSignal(str)  # emittiert node_key bei Klick (Annotationsmodus)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._bridge = MapBridge()
        self._channel = QWebChannel(self.page())
        self._channel.registerObject("bridge", self._bridge)
        self.page().setWebChannel(self._channel)
        self._bridge.node_clicked_signal.connect(self.node_selected)
        self.setHtml(load_map_html(), QUrl("qrc:/"))

    # ── Python → JavaScript ────────────────────────────────────────────────

    def update_map(
        self,
        nodes: list[dict],
        edges: list[dict] | None = None,
        overlay_url: str | None = None,
        bounds: list | None = None,
        overlay_opacity: float = 0.65,
    ) -> None:
        """Lädt Knoten und optionales Grundriss-Overlay in die Karte."""
        self.page().runJavaScript(
            f"loadMap({json.dumps(nodes)}, "
            f"{json.dumps(edges or [])}, "
            f"{json.dumps(overlay_url or '')}, "
            f"{json.dumps(bounds or [])}, "
            f"{overlay_opacity});"
        )

    def set_overlay_opacity(self, opacity: float) -> None:
        """Setzt die Transparenz des Grundriss-Overlays (0.0 – 1.0)."""
        self.page().runJavaScript(f"setOverlayOpacity({opacity});")

    def select_node(self, node_key: str) -> None:
        self.page().runJavaScript(f"selectNode({json.dumps(node_key)});")

    def clear_selection(self) -> None:
        self.page().runJavaScript('selectNode("");')

    def set_route_mode(self, enabled: bool) -> None:
        self.page().runJavaScript(
            f"setRouteMode({'true' if enabled else 'false'});"
        )

    def clear_route(self) -> None:
        self.page().runJavaScript("clearRoute();")

    def restore_route(self, nodes: list[dict]) -> None:
        """Stellt eine gespeicherte Route anhand von Node-Daten wieder her."""
        self.page().runJavaScript(f"restoreRoute({json.dumps(nodes)});")

    def get_route(self, callback) -> None:
        """Ruft callback(list[node_key]) asynchron auf."""
        def _cb(result):
            try:
                callback(json.loads(result) if result else [])
            except Exception:
                callback([])
        self.page().runJavaScript("getRoute();", _cb)
