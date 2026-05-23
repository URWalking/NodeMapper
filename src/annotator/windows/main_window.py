from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

import pandas as pd
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QKeySequence
from PyQt5.QtWidgets import (
    QAction,
    QApplication,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QShortcut,
    QSlider,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from annotator.constants import HIDDEN_NODE_TYPES
from annotator.store import AnnotationStore
from annotator.widgets.map_widget import MapWidget
from annotator.widgets.photo_panel import PhotoPanel
from graph import UniversityGraph


class MainWindow(QMainWindow):
    """Main window of the annotation tool."""

    def __init__(
        self,
        data_dir: str,
        annotations_path: str,
    ) -> None:
        super().__init__()
        self.setWindowTitle("URWalking Annotator")
        self.resize(1400, 850)

        self._store = AnnotationStore(annotations_path)

        self._photo_paths: list[str] = []
        self._current_idx: int = 0
        self._floorplan_dir: str = ""
        self._current_floor_nodes: pd.DataFrame = pd.DataFrame()
        self._current_overlay_url: str | None = None
        self._current_bounds: list | None = None
        self._block_floor_signals: bool = False
        self._route_keys: list[str] = []
        self._route_node_data: list[dict] = []  # [{key, lat, lon}, ...]

        self.statusBar().showMessage("Loading university graph …")
        QApplication.processEvents()
        self._graph = UniversityGraph(data_dir)
        self.statusBar().showMessage(
            f"Graph loaded: {len(self._graph.nodes_df):,} nodes, "
            f"{len(self._graph.buildings)} buildings",
            4000,
        )

        self._setup_ui()
        self._setup_menu()
        self._setup_shortcuts()

        self._map_widget._bridge.ready_signal.connect(self._on_map_ready)

    # ══════════════════════════════════════════════════════════════════════
    # UI setup
    # ══════════════════════════════════════════════════════════════════════

    def _setup_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root_layout = QVBoxLayout(central)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        splitter = QSplitter(Qt.Horizontal)
        splitter.setHandleWidth(4)

        # ── Left panel: photo ──────────────────────────────────────────────
        self._photo_panel = PhotoPanel()
        self._photo_panel.setMinimumWidth(280)
        self._photo_panel.setMaximumWidth(420)
        self._photo_panel.setStyleSheet("background: #1e1e1e;")
        splitter.addWidget(self._photo_panel)

        # ── Right panel: filters + map ─────────────────────────────────────
        right = QWidget()
        right.setStyleSheet("background: #2b2d30;")
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(6, 6, 6, 0)
        right_layout.setSpacing(4)

        # Control bar
        ctrl_bar = QHBoxLayout()
        ctrl_bar.setSpacing(8)

        ctrl_bar.addWidget(QLabel("Building:"))
        self._combo_building = QComboBox()
        self._combo_building.setMinimumWidth(220)
        ctrl_bar.addWidget(self._combo_building)

        ctrl_bar.addWidget(QLabel("Floor:"))
        self._combo_floor = QComboBox()
        self._combo_floor.setMinimumWidth(160)
        ctrl_bar.addWidget(self._combo_floor)

        ctrl_bar.addWidget(QLabel("Node type:"))
        self._combo_type = QComboBox()
        self._combo_type.setMinimumWidth(160)
        ctrl_bar.addWidget(self._combo_type)

        ctrl_bar.addStretch()

        ctrl_bar.addWidget(QLabel("Floor plan:"))
        self._slider_opacity = QSlider(Qt.Horizontal)
        self._slider_opacity.setRange(0, 100)
        self._slider_opacity.setValue(65)
        self._slider_opacity.setFixedWidth(100)
        self._slider_opacity.setToolTip("Floor plan overlay opacity")
        self._slider_opacity.valueChanged.connect(
            lambda v: self._map_widget.set_overlay_opacity(v / 100.0)
        )
        ctrl_bar.addWidget(self._slider_opacity)

        btn_open_fp = QPushButton("Floor Plan Folder …")
        btn_open_fp.clicked.connect(self._open_floorplan_folder)
        ctrl_bar.addWidget(btn_open_fp)

        right_layout.addLayout(ctrl_bar)

        # Route builder bar
        route_bar = QHBoxLayout()
        route_bar.setSpacing(8)

        route_bar.addWidget(QLabel("Route Builder:"))

        self._btn_route_mode = QPushButton("Route Mode ON/OFF")
        self._btn_route_mode.setCheckable(True)
        self._btn_route_mode.setToolTip(
            "Enable route mode: click nodes to add them to the route"
        )
        self._btn_route_mode.toggled.connect(self._toggle_route_mode)
        route_bar.addWidget(self._btn_route_mode)

        self._btn_route_load = QPushButton("Open Route …")
        self._btn_route_load.clicked.connect(self._load_route)
        route_bar.addWidget(self._btn_route_load)

        self._btn_route_save = QPushButton("Save Route …")
        self._btn_route_save.setEnabled(False)
        self._btn_route_save.clicked.connect(self._save_route)
        route_bar.addWidget(self._btn_route_save)

        self._btn_route_clear = QPushButton("Clear Route")
        self._btn_route_clear.setEnabled(False)
        self._btn_route_clear.clicked.connect(self._clear_route)
        route_bar.addWidget(self._btn_route_clear)

        self._lbl_route_count = QLabel("0 nodes in route")
        self._lbl_route_count.setStyleSheet("color: #aaa; font-size: 11px;")
        route_bar.addWidget(self._lbl_route_count)

        route_bar.addStretch()

        right_layout.addLayout(route_bar)

        # OSM map
        self._map_widget = MapWidget()
        self._map_widget._bridge.node_clicked_signal.connect(self._on_node_selected)
        self._map_widget._bridge.route_node_signal.connect(self._on_route_node_added)
        right_layout.addWidget(self._map_widget, stretch=1)

        splitter.addWidget(right)
        splitter.setSizes([340, 1060])
        root_layout.addWidget(splitter, stretch=1)

        # ── Bottom toolbar ─────────────────────────────────────────────────
        toolbar = QWidget()
        toolbar.setStyleSheet("background: #1c1d1f; border-top: 1px solid #444;")
        toolbar.setFixedHeight(48)
        tb_layout = QHBoxLayout(toolbar)
        tb_layout.setContentsMargins(12, 4, 12, 4)
        tb_layout.setSpacing(8)

        self._btn_prev = QPushButton("← Back")
        self._btn_prev.setEnabled(False)
        self._btn_prev.clicked.connect(self._prev_photo)
        tb_layout.addWidget(self._btn_prev)

        self._lbl_counter = QLabel("0 / 0")
        self._lbl_counter.setAlignment(Qt.AlignCenter)
        self._lbl_counter.setMinimumWidth(100)
        tb_layout.addWidget(self._lbl_counter)

        self._btn_next = QPushButton("Next →")
        self._btn_next.setEnabled(False)
        self._btn_next.clicked.connect(self._next_photo)
        tb_layout.addWidget(self._btn_next)

        tb_layout.addSpacing(20)

        self._btn_clear = QPushButton("Clear Assignment")
        self._btn_clear.setEnabled(False)
        self._btn_clear.clicked.connect(self._clear_current_annotation)
        tb_layout.addWidget(self._btn_clear)

        tb_layout.addStretch()

        self._lbl_progress = QLabel("Progress: 0 / 0")
        self._lbl_progress.setStyleSheet("color: #aaa;")
        tb_layout.addWidget(self._lbl_progress)

        tb_layout.addSpacing(20)

        self._btn_save = QPushButton("Export CSV")
        self._btn_save.setStyleSheet(
            "QPushButton { background: #2f9e44; color: white; border-radius: 4px; padding: 4px 14px; }"
            "QPushButton:hover { background: #37b24d; }"
        )
        self._btn_save.clicked.connect(self._export_csv)
        tb_layout.addWidget(self._btn_save)

        root_layout.addWidget(toolbar)

        self._combo_building.currentTextChanged.connect(self._on_building_changed)
        self._combo_floor.currentIndexChanged.connect(self._on_floor_changed)
        self._combo_type.currentTextChanged.connect(self._on_type_filter_changed)

        for lbl in self.findChildren(QLabel):
            if not lbl.styleSheet():
                lbl.setStyleSheet("color: #ccc; font-size: 12px;")

    def _setup_menu(self) -> None:
        menu = self.menuBar()
        file_menu = menu.addMenu("File")

        act_open = QAction("Open Photo Folder … (Ctrl+O)", self)
        act_open.triggered.connect(self._open_photo_folder)
        file_menu.addAction(act_open)

        act_fp = QAction("Open Floor Plan Folder …", self)
        act_fp.triggered.connect(self._open_floorplan_folder)
        file_menu.addAction(act_fp)

        file_menu.addSeparator()

        act_save = QAction("Export CSV (Ctrl+S)", self)
        act_save.triggered.connect(self._export_csv)
        file_menu.addAction(act_save)

        act_export = QAction("Export CSV As … (Ctrl+E)", self)
        act_export.triggered.connect(self._export_csv)
        file_menu.addAction(act_export)

        file_menu.addSeparator()

        act_quit = QAction("Quit", self)
        act_quit.triggered.connect(self.close)
        file_menu.addAction(act_quit)

    def _setup_shortcuts(self) -> None:
        def sc(key, slot):
            QShortcut(QKeySequence(key), self).activated.connect(slot)

        sc("Left",   self._prev_photo)
        sc("A",      self._prev_photo)
        sc("Right",  self._next_photo)
        sc("D",      self._next_photo)
        sc("Ctrl+S", self._export_csv)
        sc("Ctrl+O", self._open_photo_folder)
        sc("Ctrl+E", self._export_csv)
        sc("Delete", self._clear_current_annotation)

    # ══════════════════════════════════════════════════════════════════════
    # Map ready
    # ══════════════════════════════════════════════════════════════════════

    def _on_map_ready(self) -> None:
        """Called once Leaflet + QWebChannel are fully initialised."""
        self._map_widget._bridge.ready_signal.disconnect(self._on_map_ready)
        self._populate_building_combo()

    # ══════════════════════════════════════════════════════════════════════
    # Building / floor / type filter
    # ══════════════════════════════════════════════════════════════════════

    def _populate_building_combo(self) -> None:
        self._block_floor_signals = True
        self._combo_building.clear()
        for b in sorted(self._graph.buildings):
            self._combo_building.addItem(b)
        self._block_floor_signals = False
        if self._combo_building.count():
            self._on_building_changed(self._combo_building.currentText())

    def _on_building_changed(self, building: str) -> None:
        if not building:
            return
        self._block_floor_signals = True
        self._combo_floor.clear()
        for meta in self._unique_levels(building):
            storey   = meta.get("storey", 0)
            level_id = meta.get("level_id", meta.get("id", 0))
            label    = f"Storey {storey:+d}  (Level {level_id})"
            self._combo_floor.addItem(
                label,
                userData=(level_id, storey, meta.get("mapfile", "")),
            )
        self._block_floor_signals = False
        if self._combo_floor.count():
            self._on_floor_changed()

    def _on_floor_changed(self) -> None:
        if self._block_floor_signals:
            return
        data = self._combo_floor.currentData()
        if data is None:
            return
        level_id, storey, mapfile = data
        building = self._combo_building.currentText()
        self._load_floor_plan(building, level_id, mapfile)

    def _on_type_filter_changed(self) -> None:
        if self._current_floor_nodes.empty:
            return
        self._update_map()
        self._rehighlight_current()

    def _populate_type_combo(self, nodes_df: pd.DataFrame) -> None:
        self._block_floor_signals = True
        current = self._combo_type.currentText()
        self._combo_type.clear()
        self._combo_type.addItem("All types")
        for t in sorted(nodes_df["type"].dropna().unique()):
            if t not in HIDDEN_NODE_TYPES:
                self._combo_type.addItem(str(t))
        idx = self._combo_type.findText(current)
        if idx >= 0:
            self._combo_type.setCurrentIndex(idx)
        self._block_floor_signals = False

    # ══════════════════════════════════════════════════════════════════════
    # Floor plan loading + map update
    # ══════════════════════════════════════════════════════════════════════

    def _load_floor_plan(
        self,
        building: str,
        level_id: int,
        mapfile: str,
    ) -> None:
        nodes_df = self._graph.nodes_df
        mask = (nodes_df["building"] == building) & (nodes_df["level_id"] == level_id)
        floor_nodes = (
            nodes_df[mask]
            .drop_duplicates(subset=["node_key"])
            .copy()
        )
        self._current_floor_nodes = floor_nodes
        self._populate_type_combo(floor_nodes)

        self._current_overlay_url, self._current_bounds = (
            self._compute_floor_overlay(building, level_id, mapfile)
        )

        self._update_map()
        self._rehighlight_current()

        n = len(floor_nodes)
        self.statusBar().showMessage(
            f"{building} · Level {level_id} · {n} nodes loaded", 3000
        )

    def _compute_floor_overlay(
        self,
        building: str,
        level_id: int,
        mapfile: str,
    ) -> tuple[str | None, list | None]:
        """Returns (base64 data URL, [[lat_min, lon_min], [lat_max, lon_max]])."""
        import base64

        overlay_url: str | None = None
        bounds: list | None = None

        if mapfile and self._floorplan_dir:
            img_path = os.path.join(self._floorplan_dir, mapfile)
            if os.path.isfile(img_path):
                with open(img_path, "rb") as f:
                    data = f.read()
                ext  = os.path.splitext(mapfile)[1].lower()
                mime = "image/png" if ext == ".png" else "image/jpeg"
                overlay_url = f"data:{mime};base64,{base64.b64encode(data).decode()}"
            else:
                self.statusBar().showMessage(
                    f"Floor plan not found: {mapfile}", 3000
                )

        xf = self._graph._geo_transform.get((building, level_id))
        if xf:
            width: float | None = None
            height: float | None = None
            for meta in self._graph.levels.get(building, []):
                if int(meta.get("level_id", meta.get("id", -1))) == level_id:
                    try:
                        width  = float(meta.get("width",  0) or 0)
                        height = float(meta.get("height", 0) or 0)
                    except (TypeError, ValueError):
                        pass
                    break

            if width and height:
                corners = [(0, 0), (width, 0), (0, height), (width, height)]
                lats = [xf["xlat"] * px + xf["ylat"] * py + xf["wlat"]
                        for px, py in corners]
                lons = [xf["xlon"] * px + xf["ylon"] * py + xf["wlon"]
                        for px, py in corners]
                bounds = [[min(lats), min(lons)], [max(lats), max(lons)]]

        if bounds is None:
            valid = self._current_floor_nodes[
                self._current_floor_nodes["geo_lat"].notna()
                & self._current_floor_nodes["geo_lon"].notna()
            ]
            if not valid.empty:
                bounds = [
                    [float(valid["geo_lat"].min()), float(valid["geo_lon"].min())],
                    [float(valid["geo_lat"].max()), float(valid["geo_lon"].max())],
                ]

        return overlay_url, bounds

    def _filter_by_bounds(
        self, df: pd.DataFrame, bounds: list | None, margin: float = 0.2
    ) -> pd.DataFrame:
        """Removes nodes whose geo_lat/geo_lon fall outside the floor bounds."""
        if bounds is None or df.empty:
            return df
        (min_lat, min_lon), (max_lat, max_lon) = bounds
        lat_m = (max_lat - min_lat) * margin
        lon_m = (max_lon - min_lon) * margin
        in_bounds = (
            df["geo_lat"].between(min_lat - lat_m, max_lat + lat_m)
            & df["geo_lon"].between(min_lon - lon_m, max_lon + lon_m)
        )
        no_coords = df["geo_lat"].isna() | df["geo_lon"].isna()
        return df[in_bounds | no_coords]

    def _nodes_to_json(
        self,
        nodes_df: pd.DataFrame,
        annotated_keys: set | None = None,
    ) -> list[dict]:
        """Converts a DataFrame to a JSON-serialisable list."""
        def _sf(val):
            try:
                f = float(val)
                return None if pd.isna(f) else f
            except Exception:
                return None

        annotated_keys = annotated_keys or set()
        result = []
        for _, row in nodes_df.iterrows():
            key = str(row["node_key"])
            result.append({
                "key":       key,
                "lat":       _sf(row.get("geo_lat")),
                "lon":       _sf(row.get("geo_lon")),
                "type":      str(row.get("type", "") or ""),
                "name":      str(row.get("name", "") or ""),
                "px":        _sf(row.get("x")),
                "py":        _sf(row.get("y")),
                "annotated": key in annotated_keys,
            })
        return result

    def _edges_to_json(self, floor_node_keys: set) -> list[dict]:
        """Returns edges where both endpoints are on the current floor."""
        if self._graph.edges_df.empty:
            return []
        df = self._graph.edges_df
        mask = df["source_key"].isin(floor_node_keys) & df["sink_key"].isin(floor_node_keys)
        result = []
        seen = set()
        for _, row in df[mask].iterrows():
            pair = (str(row["source_key"]), str(row["sink_key"]))
            if pair not in seen:
                seen.add(pair)
                result.append({"src": pair[0], "dst": pair[1]})
        return result

    def _update_map(self) -> None:
        """Applies the type filter and sends nodes to the map."""
        if self._current_floor_nodes.empty:
            return
        selected_type = self._combo_type.currentText()
        df = self._current_floor_nodes
        if selected_type and selected_type != "All types":
            df = df[df["type"] == selected_type]
        df = df[~df["type"].isin(HIDDEN_NODE_TYPES)]
        df = self._filter_by_bounds(df, self._current_bounds)

        visible_floor = self._filter_by_bounds(
            self._current_floor_nodes, self._current_bounds
        )
        visible_keys = set(visible_floor["node_key"].astype(str))
        edges = self._edges_to_json(visible_keys)

        annotated_node_keys = {
            v["node_key"]
            for v in self._store._data.values()
            if "node_key" in v
        }

        opacity = self._slider_opacity.value() / 100.0

        self._map_widget.update_map(
            self._nodes_to_json(df, annotated_node_keys),
            edges,
            self._current_overlay_url,
            self._current_bounds,
            opacity,
        )
        if self._route_node_data:
            self._map_widget.restore_route(self._route_node_data)

    def _rehighlight_current(self) -> None:
        if not self._photo_paths:
            return
        path = self._photo_paths[self._current_idx]
        ann  = self._store.get(path)
        if ann:
            self._map_widget.select_node(ann["node_key"])

    # ══════════════════════════════════════════════════════════════════════
    # Photo navigation
    # ══════════════════════════════════════════════════════════════════════

    def _open_photo_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, "Open Photo Folder", os.path.expanduser("~")
        )
        if folder:
            self._load_photos(folder)

    def _load_photos(self, folder: str) -> None:
        extensions = (".jpg", ".jpeg", ".JPG", ".JPEG", ".png", ".PNG")
        paths = sorted(
            str(p) for p in Path(folder).iterdir()
            if p.suffix in extensions
        )
        if not paths:
            QMessageBox.information(self, "Info", "No images found in the selected folder.")
            return
        self._photo_paths = paths
        self._current_idx = 0
        self._btn_prev.setEnabled(True)
        self._btn_next.setEnabled(True)
        self._btn_clear.setEnabled(True)
        self._show_current_photo()

    def _show_current_photo(self) -> None:
        if not self._photo_paths:
            return
        path = self._photo_paths[self._current_idx]
        self._photo_panel.load_photo(path)

        ann = self._store.get(path)
        if ann:
            self._photo_panel.show_assignment(
                ann["node_key"],
                ann.get("node_name", ""),
                ann.get("node_type", ""),
                ann.get("building", ""),
                ann.get("storey", 0),
            )
            self._jump_to_floor(ann["building"], ann["level_id"])
            self._map_widget.select_node(ann["node_key"])
        else:
            self._photo_panel.clear_assignment()
            self._map_widget.clear_selection()

        self._update_status()

    def _prev_photo(self) -> None:
        if self._photo_paths:
            self._current_idx = (self._current_idx - 1) % len(self._photo_paths)
            self._show_current_photo()

    def _next_photo(self) -> None:
        if self._photo_paths:
            self._current_idx = (self._current_idx + 1) % len(self._photo_paths)
            self._show_current_photo()

    # ══════════════════════════════════════════════════════════════════════
    # Node selection → annotation
    # ══════════════════════════════════════════════════════════════════════

    def _on_node_selected(self, node_key: str) -> None:
        if not self._photo_paths:
            self.statusBar().showMessage(
                "Please open a photo folder first.", 3000
            )
            return

        df = self._graph.nodes_df
        rows = df[df["node_key"] == node_key]
        if rows.empty:
            self.statusBar().showMessage(f"Node {node_key} not found.", 3000)
            return
        row = rows.iloc[0]

        path = self._photo_paths[self._current_idx]
        ts   = self._photo_panel.current_timestamp
        self._store.annotate(path, row, ts)

        self._map_widget.select_node(node_key)
        self._photo_panel.show_assignment(
            node_key,
            str(row.get("name",     "") or ""),
            str(row.get("type",     "") or ""),
            str(row.get("building", "") or ""),
            int(row.get("storey",   0)),
        )
        self._update_status()
        self.statusBar().showMessage(f"Assigned: {node_key}", 2500)

    def _clear_current_annotation(self) -> None:
        if not self._photo_paths:
            return
        path = self._photo_paths[self._current_idx]
        self._store.clear(path)
        self._photo_panel.clear_assignment()
        self._map_widget.clear_selection()
        self._update_status()

    # ══════════════════════════════════════════════════════════════════════
    # Jump to annotated floor
    # ══════════════════════════════════════════════════════════════════════

    def _jump_to_floor(self, building: str, level_id: int) -> None:
        self._block_floor_signals = True

        b_idx = self._combo_building.findText(building)
        if b_idx >= 0 and self._combo_building.currentIndex() != b_idx:
            self._combo_building.setCurrentIndex(b_idx)
            self._repopulate_floors_silent(building)

        for i in range(self._combo_floor.count()):
            data = self._combo_floor.itemData(i)
            if data and data[0] == level_id:
                self._combo_floor.setCurrentIndex(i)
                break

        self._block_floor_signals = False

        data = self._combo_floor.currentData()
        if data:
            lid, _, mapfile = data
            self._load_floor_plan(building, lid, mapfile)

    def _unique_levels(self, building: str) -> list:
        seen: set[int] = set()
        unique = []
        for meta in sorted(
            self._graph.levels.get(building, []),
            key=lambda m: m.get("storey", 0),
        ):
            lid = int(meta.get("level_id", meta.get("id", 0)))
            if lid not in seen:
                seen.add(lid)
                unique.append(meta)
        return unique

    def _repopulate_floors_silent(self, building: str) -> None:
        self._combo_floor.clear()
        for meta in self._unique_levels(building):
            storey   = meta.get("storey", 0)
            level_id = meta.get("level_id", meta.get("id", 0))
            label    = f"Storey {storey:+d}  (Level {level_id})"
            self._combo_floor.addItem(
                label,
                userData=(level_id, storey, meta.get("mapfile", "")),
            )

    # ══════════════════════════════════════════════════════════════════════
    # Route builder
    # ══════════════════════════════════════════════════════════════════════

    def _toggle_route_mode(self, enabled: bool) -> None:
        self._map_widget.set_route_mode(enabled)
        self._btn_route_save.setEnabled(enabled)
        self._btn_route_clear.setEnabled(enabled)
        if enabled:
            self._btn_route_mode.setStyleSheet(
                "QPushButton { background: #c92a2a; color: white; "
                "border-radius: 4px; font-weight: bold; }"
                "QPushButton:hover { background: #e03131; }"
            )
            if not self._route_keys:
                self._lbl_route_count.setText("0 nodes in route")
            self._lbl_route_count.setStyleSheet("color: #ff6b6b; font-size: 11px;")
            self.statusBar().showMessage(
                "Route mode active — click nodes to add them to the route", 0
            )
        else:
            self._btn_route_mode.setStyleSheet("")
            self._lbl_route_count.setStyleSheet("color: #aaa; font-size: 11px;")
            self.statusBar().showMessage("Route mode disabled", 2000)

    def _build_route_node_data(self, keys: list[str]) -> list[dict]:
        """Schlägt Lat/Lon für jeden Route-Knoten im Graph nach."""
        nodes_df = self._graph.nodes_df
        result = []
        for key in keys:
            row = nodes_df[nodes_df["node_key"] == key]
            if not row.empty:
                r = row.iloc[0]
                lat = r["geo_lat"]
                lon = r["geo_lon"]
                if lat is not None and lon is not None:
                    result.append({"key": key, "lat": float(lat), "lon": float(lon)})
        return result

    def _on_route_node_added(self, node_key: str) -> None:
        """Updates the node counter in the route bar."""
        def _update_count(keys_json):
            try:
                keys = json.loads(keys_json) if keys_json else []
            except Exception:
                keys = []
            self._route_keys = keys
            self._route_node_data = self._build_route_node_data(keys)
            self._lbl_route_count.setText(f"{len(keys)} nodes in route")
        self._map_widget.page().runJavaScript("getRoute();", _update_count)
        self.statusBar().showMessage(f"Route: added {node_key}", 1500)

    def _clear_route(self) -> None:
        self._route_keys = []
        self._route_node_data = []
        self._map_widget.clear_route()
        self._lbl_route_count.setText("0 nodes in route")

    def _load_route(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Route", "", "JSON files (*.json)"
        )
        if not path:
            return
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Could not read file:\n{e}")
            return

        keys = data.get("nodes", [])
        if not keys:
            QMessageBox.information(self, "Info", "No nodes found in route file.")
            return

        meta     = data.get("metadata", {})
        building = meta.get("building", "")
        level_id = meta.get("level_id")

        if building:
            b_idx = self._combo_building.findText(building)
            if b_idx >= 0:
                self._combo_building.setCurrentIndex(b_idx)
        if level_id is not None:
            for i in range(self._combo_floor.count()):
                d = self._combo_floor.itemData(i)
                if d and d[0] == level_id:
                    self._combo_floor.setCurrentIndex(i)
                    break

        if not self._btn_route_mode.isChecked():
            self._btn_route_mode.setChecked(True)

        self._route_keys = keys
        self._route_node_data = self._build_route_node_data(keys)
        self._map_widget.restore_route(self._route_node_data)
        self._lbl_route_count.setText(f"{len(keys)} nodes in route")
        self.statusBar().showMessage(
            f"Route loaded: {len(keys)} nodes from {path}", 4000
        )

    def _save_route(self) -> None:
        def _on_route(keys: list[str]) -> None:
            if not keys:
                QMessageBox.information(
                    self, "Info",
                    "No nodes in route yet. Please click nodes on the map first.",
                )
                return
            path, _ = QFileDialog.getSaveFileName(
                self, "Save Route", "route.json", "JSON files (*.json)"
            )
            if not path:
                return

            building = self._combo_building.currentText()
            data     = self._combo_floor.currentData()
            level_id = data[0] if data else None
            storey   = data[1] if data else None

            route_data = {
                "nodes": keys,
                "metadata": {
                    "created":  datetime.now().isoformat(timespec="seconds"),
                    "building": building,
                    "level_id": level_id,
                    "storey":   storey,
                    "count":    len(keys),
                },
            }
            Path(path).write_text(
                json.dumps(route_data, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            self.statusBar().showMessage(
                f"Route with {len(keys)} nodes saved: {path}", 4000
            )

        self._map_widget.get_route(_on_route)

    # ══════════════════════════════════════════════════════════════════════
    # Status bar
    # ══════════════════════════════════════════════════════════════════════

    def _update_status(self) -> None:
        n   = len(self._photo_paths)
        ann = sum(1 for p in self._photo_paths if p in self._store.annotated_keys)
        if n:
            self._lbl_counter.setText(f"{self._current_idx + 1} / {n}")
            current = self._photo_paths[self._current_idx]
            if current in self._store.annotated_keys:
                self._lbl_counter.setStyleSheet("color: #a9e34b; font-weight: bold;")
            else:
                self._lbl_counter.setStyleSheet("color: #f08c00; font-weight: bold;")
        else:
            self._lbl_counter.setText("0 / 0")
        self._lbl_progress.setText(f"Progress: {ann} / {n} ✓")

    # ══════════════════════════════════════════════════════════════════════
    # Export / floor plan folder
    # ══════════════════════════════════════════════════════════════════════

    def _open_floorplan_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, "Open Floor Plan Folder", os.path.expanduser("~")
        )
        if folder:
            self._floorplan_dir = folder
            data     = self._combo_floor.currentData()
            building = self._combo_building.currentText()
            if data and building:
                self._load_floor_plan(building, data[0], data[2])

    def _export_csv(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Export CSV", "annotations.csv", "CSV files (*.csv)"
        )
        if path:
            self._store.export_csv(path)
            self.statusBar().showMessage(f"CSV exported: {path}", 4000)

    # ══════════════════════════════════════════════════════════════════════
    # Window close
    # ══════════════════════════════════════════════════════════════════════

    def closeEvent(self, event) -> None:
        event.accept()
