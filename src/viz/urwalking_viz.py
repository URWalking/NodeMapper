"""
URWalking Visualization Library
================================
Parse and visualize indoor navigation graphs from XML files.
Supports:
  - 2D floor maps overlaid on OpenStreetMap (Folium)
  - 3D multi-floor visualization (Plotly)
  - Hot-area analysis from destination frequency data
  - Route visualization across hot areas

Usage in Jupyter:
    from urwalking_viz import BuildingGraph, UniversityMap
    bg = BuildingGraph.from_xml_dir("data/university", building="rw")
    bg.plot_3d()
    bg.plot_osm()
"""

from __future__ import annotations

import os
import re
import glob
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import folium
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
from folium.plugins import MarkerCluster


# ─────────────────────────────────────────────
#  Data Classes
# ─────────────────────────────────────────────

@dataclass
class Node:
    id: str
    x: float
    y: float
    type: str
    name: str = ""
    room_id: str = ""
    is_destination: str = ""
    lat: Optional[float] = None
    lon: Optional[float] = None  # computed via pixel→WGS84

    @property
    def has_gps(self) -> bool:
        return self.lat is not None and self.lon is not None


@dataclass
class Edge:
    source: str
    sink: str
    edge_type: int = 0
    frequency: float = 0.0


@dataclass
class Level:
    level_id: str
    storey: int
    mapfile: str
    width: int
    height: int
    nodes: Dict[str, Node] = field(default_factory=dict)
    edges: List[Edge] = field(default_factory=list)
    # Pixel→WGS84 affine coefficients
    xlat: float = 0.0
    ylat: float = 0.0
    wlat: float = 0.0
    xlon: float = 0.0
    ylon: float = 0.0
    wlon: float = 0.0

    def pixel_to_wgs84(self, px: float, py: float) -> Tuple[float, float]:
        """Convert pixel coordinates to (lat, lon)."""
        lat = self.xlat * px + self.ylat * py + self.wlat
        lon = self.xlon * px + self.ylon * py + self.wlon
        return lat, lon

    def assign_gps_to_nodes(self):
        """Compute GPS coords for all nodes using the affine transform."""
        for node in self.nodes.values():
            node.lat, node.lon = self.pixel_to_wgs84(node.x, node.y)


# ─────────────────────────────────────────────
#  XML Parser
# ─────────────────────────────────────────────

def _parse_level(level_el: ET.Element) -> Level:
    lvl = Level(
        level_id=level_el.get("id", "?"),
        storey=int(level_el.get("storey", "0")),
        mapfile=level_el.get("mapfile", ""),
        width=int(level_el.get("width", "1000")),
        height=int(level_el.get("height", "1000")),
    )

    # Coordinate transform
    p2w = level_el.find("pixelToWGS84")
    if p2w is not None:
        def _f(tag): return float(p2w.findtext(tag, "0"))
        lvl.xlat = _f("xlat"); lvl.ylat = _f("ylat"); lvl.wlat = _f("wlat")
        lvl.xlon = _f("xlon"); lvl.ylon = _f("ylon"); lvl.wlon = _f("wlon")

    # Nodes
    for n in level_el.findall("node"):
        node_type = n.get("type", "branch")
        # Skip link nodes (cross-building references)
        if node_type == "link":
            continue
        nid = n.get("id")
        try:
            nx, ny = float(n.get("x", 0)), float(n.get("y", 0))
        except (ValueError, TypeError):
            continue
        node = Node(
            id=nid, x=nx, y=ny, type=node_type,
            name=n.get("name", ""),
            room_id=n.get("roomid", ""),
            is_destination=n.get("isdestination", ""),
        )
        lvl.nodes[nid] = node

    # Edges
    for e in level_el.findall("edge"):
        src = e.get("source"); snk = e.get("sink")
        if src and snk:
            freq = float(e.get("occurrencefrequency", "0"))
            lvl.edges.append(Edge(source=src, sink=snk,
                                  edge_type=int(e.get("type", "0")),
                                  frequency=freq))

    lvl.assign_gps_to_nodes()
    return lvl


def parse_building_xml(path: str) -> Dict[str, Level]:
    """Parse a single building XML file. Returns dict level_id→Level."""
    tree = ET.parse(path)
    root = tree.getroot()
    levels = {}
    for lvl_el in root.findall("level"):
        lvl = _parse_level(lvl_el)
        levels[lvl.level_id] = lvl
    return levels


# ─────────────────────────────────────────────
#  BuildingGraph – main entry point
# ─────────────────────────────────────────────

class BuildingGraph:
    """
    Aggregated graph for one building (all floors).

    Parameters
    ----------
    name : str
        Human-readable building name, e.g. "rw" or "pt".
    levels : dict
        Mapping storey→Level.
    hot_rooms : Counter, optional
        Counter of room_id visit frequency for heat colouring.
    """

    # Colour palette per node type
    NODE_COLORS = {
        "Office":      "#4A90D9",
        "branch":      "#7FB5D5",
        "doorway":     "#F5A623",
        "Entry":       "#9B9B9B",
        "Toilet":      "#50E3C2",
        "Landmark":    "#BD10E0",
        "Areanode":    "#417505",
        "Lecturehall": "#D0021B",
        "GpsLink":     "#000000",
    }
    FLOOR_HEIGHT_M = 4.0  # metres per storey for 3-D offset

    def __init__(self, name: str, levels: Dict[str, Level],
                 hot_rooms: Optional[Counter] = None):
        self.name = name
        self.levels = levels  # level_id → Level
        # Accepts both node-id keys ("409") and room-id keys ("RWL 201")
        self.hot_rooms: Counter = hot_rooms or Counter()
        # Quick lookup: sorted storeys
        self._sorted_storeys = sorted(levels.values(), key=lambda l: l.storey)

    # ── Constructors ──────────────────────────

    @classmethod
    def from_xml_dir(cls, xml_dir: str, building: str,
                     hot_rooms: Optional[Counter] = None) -> "BuildingGraph":
        """
        Load all XML files matching `<building>_*.xml` in `xml_dir`.

        Example
        -------
        >>> bg = BuildingGraph.from_xml_dir("data/university", "rw")
        """
        pattern = os.path.join(xml_dir, f"{building}_*.xml")
        files = sorted(glob.glob(pattern))
        if not files:
            raise FileNotFoundError(
                f"No files matching '{pattern}' found.")

        all_levels: Dict[str, Level] = {}
        seen_storeys: set = set()
        for fpath in files:
            parsed = parse_building_xml(fpath)
            for lid, lvl in parsed.items():
                key = f"{lvl.storey}"
                if key not in seen_storeys:
                    all_levels[lid] = lvl
                    seen_storeys.add(key)

        return cls(name=building, levels=all_levels, hot_rooms=hot_rooms)

    # ── Internal helpers ──────────────────────

    def _node_color(self, node: Node) -> str:
        return self.NODE_COLORS.get(node.type, "#888888")

    def _heat_color(self, node_id: str, room_id: str = "") -> str:
        """Return a red-tinted colour based on visit frequency."""
        freq = self.hot_rooms.get(node_id, 0) or self.hot_rooms.get(room_id, 0)
        if not freq:
            return None
        max_count = max(self.hot_rooms.values()) if self.hot_rooms else 1
        ratio = freq / max_count
        r = int(255)
        g = int(255 * (1 - ratio))
        b = int(60 * (1 - ratio))
        return f"#{r:02x}{g:02x}{b:02x}"

    def _iter_destination_nodes(self):
        """Yield (level, node) for all nodes with isdestination set."""
        for lvl in self._sorted_storeys:
            for node in lvl.nodes.values():
                if node.is_destination and node.has_gps:
                    yield lvl, node

    # ── 3-D Plotly Visualisation ─────────────

    def plot_3d(self,
                show_edges: bool = True,
                show_hot: bool = True,
                filter_types: Optional[List[str]] = None,
                title: Optional[str] = None) -> go.Figure:
        """
        Render an interactive 3-D Plotly figure of the building.

        Each floor is placed at z = storey * FLOOR_HEIGHT_M.

        Parameters
        ----------
        show_edges : bool
            Draw corridor connections.
        show_hot : bool
            Highlight hot rooms red if hot_rooms is provided.
        filter_types : list of str, optional
            Only show these node types. None = show all.
        title : str, optional
            Figure title.
        """
        fig = go.Figure()
        all_lats, all_lons = [], []

        for lvl in self._sorted_storeys:
            z_offset = lvl.storey * self.FLOOR_HEIGHT_M
            label = f"Storey {lvl.storey}"

            # ── Edges ──
            if show_edges:
                ex, ey, ez = [], [], []
                for edge in lvl.edges:
                    src = lvl.nodes.get(edge.source)
                    snk = lvl.nodes.get(edge.sink)
                    if src and snk and src.has_gps and snk.has_gps:
                        ex += [src.lon, snk.lon, None]
                        ey += [src.lat, snk.lat, None]
                        ez += [z_offset, z_offset, None]
                if ex:
                    fig.add_trace(go.Scatter3d(
                        x=ex, y=ey, z=ez,
                        mode="lines",
                        line=dict(color="rgba(120,150,200,0.35)", width=2),
                        name=f"{label} edges",
                        showlegend=False,
                        hoverinfo="skip",
                    ))

            # ── Nodes ──
            displayed = [n for n in lvl.nodes.values()
                         if n.has_gps and
                         (filter_types is None or n.type in filter_types)]

            if not displayed:
                continue

            node_lats = [n.lat for n in displayed]
            node_lons = [n.lon for n in displayed]
            node_z    = [z_offset] * len(displayed)
            all_lats.extend(node_lats)
            all_lons.extend(node_lons)

            colors, hover, sizes = [], [], []
            for n in displayed:
                heat = self._heat_color(n.id, n.room_id) if show_hot else None
                colors.append(heat or self._node_color(n))
                freq = self.hot_rooms.get(n.id, 0) or self.hot_rooms.get(n.room_id, 0)
                hover.append(
                    f"<b>{n.name or n.room_id or n.id}</b><br>"
                    f"Type: {n.type}<br>"
                    f"Room: {n.room_id}<br>"
                    f"Storey: {lvl.storey}<br>"
                    f"Visits: {freq}"
                )
                base_size = 5 if n.type in ("branch", "Entry") else 8
                sizes.append(base_size + min(freq / 10, 10) if freq else base_size)

            fig.add_trace(go.Scatter3d(
                x=node_lons, y=node_lats, z=node_z,
                mode="markers",
                marker=dict(size=sizes, color=colors, opacity=0.85,
                            line=dict(width=0.5, color="white")),
                text=hover,
                hovertemplate="%{text}<extra></extra>",
                name=label,
            ))

        # Floor reference planes (thin transparent rectangles)
        if all_lats and all_lons:
            min_lat, max_lat = min(all_lats), max(all_lats)
            min_lon, max_lon = min(all_lons), max(all_lons)
            for lvl in self._sorted_storeys:
                z = lvl.storey * self.FLOOR_HEIGHT_M
                fig.add_trace(go.Mesh3d(
                    x=[min_lon, max_lon, max_lon, min_lon],
                    y=[min_lat, min_lat, max_lat, max_lat],
                    z=[z, z, z, z],
                    i=[0, 0], j=[1, 2], k=[2, 3],
                    color="rgba(200,210,230,0.12)",
                    showlegend=False, hoverinfo="skip",
                    name=f"Floor {lvl.storey}",
                ))

        fig.update_layout(
            title=title or f"Building '{self.name}' — 3D Navigation Graph",
            scene=dict(
                xaxis_title="Longitude",
                yaxis_title="Latitude",
                zaxis_title="Height (m)",
                bgcolor="rgba(15,15,30,1)",
                xaxis=dict(gridcolor="#334"),
                yaxis=dict(gridcolor="#334"),
                zaxis=dict(gridcolor="#334"),
            ),
            paper_bgcolor="rgba(15,15,30,1)",
            font=dict(color="white"),
            margin=dict(l=0, r=0, t=50, b=0),
            legend=dict(font=dict(size=10)),
        )
        return fig

    # ── 2-D OpenStreetMap Visualisation ──────

    def plot_osm(self,
                 zoom_start: int = 18,
                 show_edges: bool = True,
                 only_destinations: bool = False,
                 storey: Optional[int] = None) -> folium.Map:
        """
        Render an interactive Folium map overlaid on OpenStreetMap.

        Parameters
        ----------
        zoom_start : int
            Initial zoom level.
        show_edges : bool
            Draw navigation edges as polylines.
        only_destinations : bool
            Only plot nodes that are navigational destinations.
        storey : int, optional
            If set, only show that single storey.
        """
        # Centre map on mean GPS of all nodes
        all_lats, all_lons = [], []
        for lvl in self._sorted_storeys:
            for n in lvl.nodes.values():
                if n.has_gps:
                    all_lats.append(n.lat)
                    all_lons.append(n.lon)

        if not all_lats:
            raise ValueError("No GPS-enabled nodes found.")

        center = [np.mean(all_lats), np.mean(all_lons)]
        m = folium.Map(location=center, zoom_start=zoom_start,
                       tiles="CartoDB positron")

        # One FeatureGroup per floor (toggleable in layer control)
        for lvl in self._sorted_storeys:
            if storey is not None and lvl.storey != storey:
                continue

            fg = folium.FeatureGroup(name=f"Floor {lvl.storey}", show=True)

            # Edges
            if show_edges:
                for edge in lvl.edges:
                    src = lvl.nodes.get(edge.source)
                    snk = lvl.nodes.get(edge.sink)
                    if src and snk and src.has_gps and snk.has_gps:
                        color = "rgba(80,130,200,0.5)"
                        folium.PolyLine(
                            [[src.lat, src.lon], [snk.lat, snk.lon]],
                            color="#5082C8", weight=1.5, opacity=0.4,
                        ).add_to(fg)

            # Nodes
            for node in lvl.nodes.values():
                if not node.has_gps:
                    continue
                if only_destinations and not node.is_destination:
                    continue

                heat = self._heat_color(node.id, node.room_id)
                base_color = heat or self.NODE_COLORS.get(node.type, "#888888")
                freq = self.hot_rooms.get(node.id, 0) or self.hot_rooms.get(node.room_id, 0)
                radius = 4 + min(freq / 20, 12) if freq else 4

                popup_html = (
                    f"<b>{node.name or node.room_id or node.id}</b><br>"
                    f"Type: {node.type}<br>"
                    f"Room: {node.room_id}<br>"
                    f"Floor: {lvl.storey}<br>"
                    f"Visits: {freq}"
                )
                folium.CircleMarker(
                    location=[node.lat, node.lon],
                    radius=radius,
                    color="white",
                    weight=0.5,
                    fill=True,
                    fill_color=base_color,
                    fill_opacity=0.8,
                    popup=folium.Popup(popup_html, max_width=220),
                    tooltip=node.name or node.room_id or f"ID {node.id}",
                ).add_to(fg)

            fg.add_to(m)

        folium.LayerControl(collapsed=False).add_to(m)
        return m

    # ── Hot-Area Analysis ──────────────────────

    def get_hot_nodes(self, top_n: int = 20) -> pd.DataFrame:
        """
        Return a DataFrame of the top-N hottest destination nodes.

        Columns: building, storey, node_id, room_id, name, type, lat, lon, visits

        The counter keys can be node IDs (e.g. "409") or room IDs (e.g. "RWL 201").
        Both are checked so any counter format works.
        """
        rows = []
        for lvl in self._sorted_storeys:
            for node in lvl.nodes.values():
                freq = (self.hot_rooms.get(node.id, 0) or
                        self.hot_rooms.get(node.room_id, 0))
                if freq > 0:
                    rows.append({
                        "building": self.name,
                        "storey":   lvl.storey,
                        "node_id":  node.id,
                        "room_id":  node.room_id,
                        "name":     node.name,
                        "type":     node.type,
                        "lat":      node.lat,
                        "lon":      node.lon,
                        "visits":   freq,
                    })
        if not rows:
            return pd.DataFrame(columns=["building","storey","node_id","room_id","name","type","lat","lon","visits"])
        df = pd.DataFrame(rows).drop_duplicates(subset="room_id")
        df = df.sort_values("visits", ascending=False).head(top_n)
        return df.reset_index(drop=True)

    def plot_hot_bar(self, top_n: int = 20) -> go.Figure:
        """Bar chart of the hottest rooms."""
        df = self.get_hot_nodes(top_n)
        if df.empty:
            raise ValueError("No hot_rooms data. Pass hot_rooms= to constructor.")
        label = df["name"].where(df["name"] != "", df["room_id"])
        fig = px.bar(df, x=label, y="visits",
                     color="storey", color_continuous_scale="Blues",
                     title=f"Top {top_n} Hot Areas — Building '{self.name}'",
                     labels={"x": "Room", "visits": "Visit Count"})
        fig.update_layout(xaxis_tickangle=-40,
                          paper_bgcolor="#161625", plot_bgcolor="#1e1e35",
                          font=dict(color="white"))
        return fig

    def plot_hot_map_2d(self, top_n: int = 30) -> go.Figure:
        """
        2-D scatter map (latitude vs longitude) coloured by visit frequency.
        Good for identifying geographic clusters of activity.
        """
        df = self.get_hot_nodes(top_n)
        if df.empty:
            raise ValueError("No hot_rooms data.")
        fig = px.scatter(
            df, x="lon", y="lat",
            size="visits", color="visits",
            color_continuous_scale="YlOrRd",
            hover_name="name",
            hover_data={"room_id": True, "storey": True,
                        "visits": True, "lat": False, "lon": False},
            title=f"Hot Areas — Building '{self.name}'",
            labels={"lon": "Longitude", "lat": "Latitude"},
        )
        fig.update_layout(
            paper_bgcolor="#161625", plot_bgcolor="#1e1e35",
            font=dict(color="white"),
            yaxis=dict(scaleanchor="x", scaleratio=1),
        )
        return fig

    # ── Corridor Load Analysis ────────────────

    CORRIDOR_TYPES = {"branch", "Entry", "doorway"}  # nodes that form hallways

    def compute_corridor_load(
        self,
        storey: Optional[int] = None,
    ) -> Tuple[Dict[Tuple[str, str], float], Dict[str, float]]:
        """
        Project room visit frequency onto corridor edges.

        For every destination node (room) with visits > 0, its visit count is
        added to every *adjacent* corridor/entry node (1-hop neighbourhood).
        The resulting per-node load is then distributed to every corridor edge
        incident to that node.

        Parameters
        ----------
        storey : int, optional
            Restrict analysis to this storey; ``None`` = all storeys.

        Returns
        -------
        edge_load : dict  (source_id, sink_id) → accumulated load
        node_load : dict  node_id → accumulated load
        """
        edge_load: Dict[Tuple[str, str], float] = defaultdict(float)
        node_load: Dict[str, float] = defaultdict(float)

        levels = (
            [l for l in self._sorted_storeys if l.storey == storey]
            if storey is not None else self._sorted_storeys
        )

        for lvl in levels:
            # Build adjacency: node_id → list of (neighbour_id, edge_key)
            adj: Dict[str, List[str]] = defaultdict(list)
            for edge in lvl.edges:
                if edge.source in lvl.nodes and edge.sink in lvl.nodes:
                    adj[edge.source].append(edge.sink)
                    adj[edge.sink].append(edge.source)

            # For each hot room: spread its load to adjacent corridor nodes
            for node in lvl.nodes.values():
                freq = (
                    self.hot_rooms.get(node.id, 0)
                    or self.hot_rooms.get(node.room_id, 0)
                )
                if freq <= 0:
                    continue

                for nb_id in adj[node.id]:
                    nb = lvl.nodes.get(nb_id)
                    if nb and nb.type in self.CORRIDOR_TYPES:
                        node_load[nb_id] += freq

            # Spread node load to corridor-to-corridor edges
            for edge in lvl.edges:
                src = lvl.nodes.get(edge.source)
                snk = lvl.nodes.get(edge.sink)
                if (
                    src and snk
                    and src.type in self.CORRIDOR_TYPES
                    and snk.type in self.CORRIDOR_TYPES
                ):
                    load = node_load.get(edge.source, 0) + node_load.get(edge.sink, 0)
                    if load > 0:
                        key = (edge.source, edge.sink)
                        edge_load[key] = max(edge_load[key], load)

        return dict(edge_load), dict(node_load)

    def get_top_corridors(
        self,
        top_n: int = 20,
        storey: Optional[int] = None,
    ) -> pd.DataFrame:
        """
        Return a DataFrame of the most-loaded corridor *edges* (gang segments).

        Columns: building, storey, source_id, sink_id,
                 src_lat, src_lon, snk_lat, snk_lon, load
        """
        edge_load, _ = self.compute_corridor_load(storey=storey)

        rows = []
        levels = (
            [l for l in self._sorted_storeys if l.storey == storey]
            if storey is not None else self._sorted_storeys
        )
        for lvl in levels:
            for (sid, tid), load in edge_load.items():
                src = lvl.nodes.get(sid)
                snk = lvl.nodes.get(tid)
                if src and snk and src.has_gps and snk.has_gps:
                    rows.append({
                        "building": self.name,
                        "storey":   lvl.storey,
                        "source_id": sid,
                        "sink_id":   tid,
                        "src_lat":   src.lat,
                        "src_lon":   src.lon,
                        "snk_lat":   snk.lat,
                        "snk_lon":   snk.lon,
                        "load":      load,
                    })

        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame(rows).drop_duplicates(subset=["source_id", "sink_id"])
        return df.sort_values("load", ascending=False).head(top_n).reset_index(drop=True)

    def plot_corridor_heatmap(
        self,
        top_n: int = 30,
        storey: Optional[int] = None,
        title: Optional[str] = None,
    ) -> go.Figure:
        """
        2-D scatter map overlaying corridor edges, coloured and weighted by
        projected search load.  Hot corridors appear thicker and more orange.

        Parameters
        ----------
        top_n : int
            How many top-loaded corridor edges to highlight.
        storey : int, optional
            Restrict to one floor; ``None`` = all floors (colour-coded by storey).
        title : str, optional
            Figure title.
        """
        edge_load, node_load = self.compute_corridor_load(storey=storey)

        if not edge_load:
            raise ValueError(
                "No corridor load computed – make sure hot_rooms is set and "
                "rooms are connected to corridor nodes."
            )

        max_load = max(edge_load.values())

        fig = go.Figure()

        levels = (
            [l for l in self._sorted_storeys if l.storey == storey]
            if storey is not None else self._sorted_storeys
        )

        storey_colors = [
            "#4A90D9", "#E74C3C", "#2ECC71", "#F39C12",
            "#9B59B6", "#1ABC9C",
        ]

        for li, lvl in enumerate(levels):
            # ── Draw ALL corridor edges (faint background)
            bg_x, bg_y = [], []
            for edge in lvl.edges:
                src = lvl.nodes.get(edge.source)
                snk = lvl.nodes.get(edge.sink)
                if (
                    src and snk and src.has_gps and snk.has_gps
                    and src.type in self.CORRIDOR_TYPES
                    and snk.type in self.CORRIDOR_TYPES
                ):
                    bg_x += [src.lon, snk.lon, None]
                    bg_y += [src.lat, snk.lat, None]

            if bg_x:
                fig.add_trace(go.Scatter(
                    x=bg_x, y=bg_y,
                    mode="lines",
                    line=dict(
                        color=storey_colors[li % len(storey_colors)],
                        width=1,
                    ),
                    opacity=0.18,
                    name=f"Floor {lvl.storey} (all)",
                    showlegend=True,
                    hoverinfo="skip",
                ))

            # ── Draw HOT corridor edges (coloured by load)
            for edge in lvl.edges:
                key = (edge.source, edge.sink)
                load = edge_load.get(key, 0)
                if load <= 0:
                    continue
                src = lvl.nodes.get(edge.source)
                snk = lvl.nodes.get(edge.sink)
                if not (src and snk and src.has_gps and snk.has_gps):
                    continue

                ratio = load / max_load
                # Interpolate: blue(cold) → orange → red(hot)
                r = int(50 + 205 * ratio)
                g = int(120 * (1 - ratio))
                b = int(200 * (1 - ratio))
                width = 1.5 + 6 * ratio
                color = f"rgb({r},{g},{b})"

                fig.add_trace(go.Scatter(
                    x=[src.lon, snk.lon],
                    y=[src.lat, snk.lat],
                    mode="lines",
                    line=dict(color=color, width=width),
                    opacity=0.85,
                    showlegend=False,
                    hovertemplate=(
                        f"<b>Gang-Segment</b><br>"
                        f"Etage {lvl.storey}<br>"
                        f"Load: {load:.0f}<br>"
                        f"({edge.source} → {edge.sink})"
                        "<extra></extra>"
                    ),
                ))

        # ── Overlay hot destination nodes
        dest_lats, dest_lons, dest_hover, dest_sizes, dest_colors = [], [], [], [], []
        for lvl in levels:
            if storey is not None and lvl.storey != storey:
                continue
            for node in lvl.nodes.values():
                freq = (
                    self.hot_rooms.get(node.id, 0)
                    or self.hot_rooms.get(node.room_id, 0)
                )
                if freq > 0 and node.has_gps:
                    dest_lats.append(node.lat)
                    dest_lons.append(node.lon)
                    dest_hover.append(
                        f"<b>{node.name or node.room_id or node.id}</b><br>"
                        f"Visits: {freq}<br>Etage: {lvl.storey}"
                    )
                    dest_sizes.append(6 + min(freq / 8, 14))
                    dest_colors.append(freq)

        if dest_lats:
            fig.add_trace(go.Scatter(
                x=dest_lons, y=dest_lats,
                mode="markers",
                marker=dict(
                    size=dest_sizes,
                    color=dest_colors,
                    colorscale="YlOrRd",
                    colorbar=dict(
                        title="Raum-Visits",
                        len=0.5,
                        y=0.25,
                    ),
                    opacity=0.9,
                    line=dict(color="white", width=0.7),
                ),
                text=dest_hover,
                hovertemplate="%{text}<extra></extra>",
                name="Zielorte (Räume)",
            ))

        building_title = title or (
            f"Gang-Frequenz Heatmap — Gebäude '{self.name}'"
            + (f", Etage {storey}" if storey is not None else "")
        )
        fig.update_layout(
            title=dict(text=building_title, font=dict(size=16)),
            xaxis_title="Längengrad",
            yaxis_title="Breitengrad",
            yaxis=dict(scaleanchor="x", scaleratio=1),
            paper_bgcolor="#161625",
            plot_bgcolor="#1e1e35",
            font=dict(color="white"),
            legend=dict(
                bgcolor="rgba(30,30,60,0.7)",
                bordercolor="#444",
                borderwidth=1,
            ),
            margin=dict(l=0, r=0, t=60, b=0),
        )
        return fig

    def plot_top_corridors_bar(
        self,
        top_n: int = 20,
        storey: Optional[int] = None,
    ) -> go.Figure:
        """
        Horizontal bar chart ranking the top-N most-loaded corridor edges.
        """
        df = self.get_top_corridors(top_n=top_n, storey=storey)
        if df.empty:
            raise ValueError("No corridor load data available.")

        labels = [
            f"Gang {row.source_id}↔{row.sink_id}  (Etage {row.storey})"
            for row in df.itertuples()
        ]

        fig = go.Figure(go.Bar(
            y=labels,
            x=df["load"],
            orientation="h",
            marker=dict(
                color=df["load"],
                colorscale="YlOrRd",
                colorbar=dict(title="Load"),
                line=dict(color="rgba(255,255,255,0.15)", width=0.5),
            ),
            hovertemplate="%{y}<br>Load: %{x:.0f}<extra></extra>",
        ))

        fig.update_layout(
            title=dict(
                text=(
                    f"Top-{top_n} wichtigste Gänge — Gebäude '{self.name}'"
                    + (f", Etage {storey}" if storey is not None else "")
                ),
                font=dict(size=15),
            ),
            xaxis_title="Projizierte Suchhäufigkeit (Load)",
            yaxis=dict(autorange="reversed"),
            paper_bgcolor="#161625",
            plot_bgcolor="#1e1e35",
            font=dict(color="white"),
            margin=dict(l=220, r=20, t=60, b=40),
            height=max(400, top_n * 28),
        )
        return fig

    def plot_corridor_heatmap_osm(
        self,
        storey: Optional[int] = None,
        zoom_start: int = 18,
    ) -> "folium.Map":
        """
        OpenStreetMap Folium map with corridor edges coloured by projected
        search load.

        - **Grey thin** lines: the full corridor network (background).
        - **Coloured thick** lines: hot corridors; blue→orange→red = cold→hot.
        - **Pulse markers**: destination rooms sized by visit frequency.

        Parameters
        ----------
        storey : int, optional
            Single storey to show; ``None`` = all storeys (layer-control).
        zoom_start : int
            Initial Leaflet zoom level (default 18).
        """
        edge_load, _ = self.compute_corridor_load(storey=storey)
        max_load = max(edge_load.values()) if edge_load else 1

        # Collect all GPS points for map centre
        all_lats, all_lons = [], []
        for lvl in self._sorted_storeys:
            for n in lvl.nodes.values():
                if n.has_gps:
                    all_lats.append(n.lat)
                    all_lons.append(n.lon)
        if not all_lats:
            raise ValueError("No GPS nodes found.")

        center = [np.mean(all_lats), np.mean(all_lons)]
        m = folium.Map(location=center, zoom_start=zoom_start,
                       tiles="CartoDB dark_matter")

        storey_palette = [
            "#4A90D9", "#E74C3C", "#2ECC71",
            "#F39C12", "#9B59B6", "#1ABC9C",
        ]

        levels = (
            [l for l in self._sorted_storeys if l.storey == storey]
            if storey is not None else self._sorted_storeys
        )

        for li, lvl in enumerate(levels):
            fg = folium.FeatureGroup(
                name=f"Etage {lvl.storey}", show=(li == 0)
            )
            base_color = storey_palette[li % len(storey_palette)]

            # ── Background: all corridor edges (faint)
            for edge in lvl.edges:
                src = lvl.nodes.get(edge.source)
                snk = lvl.nodes.get(edge.sink)
                if (
                    src and snk and src.has_gps and snk.has_gps
                    and src.type in self.CORRIDOR_TYPES
                    and snk.type in self.CORRIDOR_TYPES
                    and (edge.source, edge.sink) not in edge_load
                ):
                    folium.PolyLine(
                        [[src.lat, src.lon], [snk.lat, snk.lon]],
                        color="#888888", weight=1, opacity=0.25,
                    ).add_to(fg)

            # ── Hot corridor edges (coloured by load)
            for edge in lvl.edges:
                key = (edge.source, edge.sink)
                load = edge_load.get(key, 0)
                if load <= 0:
                    continue
                src = lvl.nodes.get(edge.source)
                snk = lvl.nodes.get(edge.sink)
                if not (src and snk and src.has_gps and snk.has_gps):
                    continue

                ratio = load / max_load
                # blue → orange → red
                r = int(50 + 205 * ratio)
                g = int(120 * (1 - ratio))
                b = int(200 * (1 - ratio))
                hex_color = f"#{r:02x}{g:02x}{b:02x}"
                weight = 2 + 7 * ratio

                folium.PolyLine(
                    [[src.lat, src.lon], [snk.lat, snk.lon]],
                    color=hex_color,
                    weight=weight,
                    opacity=0.85,
                    tooltip=(
                        f"Etage {lvl.storey} | "
                        f"Gang {edge.source}↔{edge.sink} | "
                        f"Load: {load:.0f}"
                    ),
                ).add_to(fg)

            # ── Destination room markers
            for node in lvl.nodes.values():
                freq = (
                    self.hot_rooms.get(node.id, 0)
                    or self.hot_rooms.get(node.room_id, 0)
                )
                if freq > 0 and node.has_gps:
                    radius = 4 + min(freq / 12, 14)
                    heat = self._heat_color(node.id, node.room_id) or base_color
                    folium.CircleMarker(
                        location=[node.lat, node.lon],
                        radius=radius,
                        color="white", weight=0.6,
                        fill=True, fill_color=heat, fill_opacity=0.9,
                        tooltip=f"{node.name or node.room_id or node.id} ({freq} Besuche)",
                        popup=folium.Popup(
                            f"<b>{node.name or node.room_id}</b><br>"
                            f"Etage: {lvl.storey}<br>Besuche: {freq}",
                            max_width=200,
                        ),
                    ).add_to(fg)

            fg.add_to(m)

        folium.LayerControl(collapsed=False).add_to(m)

        # ── Simple gradient legend (HTML overlay)
        legend_html = """
        <div style="
            position: fixed; bottom: 30px; left: 30px; z-index: 1000;
            background: rgba(20,20,40,0.88); border-radius: 8px;
            padding: 10px 16px; font-family: Arial, sans-serif;
            color: white; font-size: 12px; border: 1px solid #555;">
          <b>Gang-Suchfrequenz</b><br>
          <div style="display:flex; align-items:center; margin-top:6px;">
            <div style="width:100px; height:12px;
                 background: linear-gradient(to right, #3278c8, #ff7800, #ff0000);
                 border-radius:3px; margin-right:8px;"></div>
            <span>niedrig → hoch</span>
          </div>
          <div style="margin-top:6px;">
            ● Raumgröße = Besuchshäufigkeit
          </div>
        </div>
        """
        m.get_root().html.add_child(folium.Element(legend_html))
        return m

    def plot_corridor_heatmap_3d(
        self,
        storey: Optional[int] = None,
        title: Optional[str] = None,
    ) -> go.Figure:
        """
        3-D Plotly figure with building floors stacked vertically.

        Corridor edges are coloured and thickened by projected search load.
        Hot destination nodes appear as glowing spheres.

        Parameters
        ----------
        storey : int, optional
            Restrict to one storey; ``None`` = all (stacked).
        title : str, optional
            Figure title override.
        """
        edge_load, node_load = self.compute_corridor_load(storey=storey)
        max_load = max(edge_load.values()) if edge_load else 1

        fig = go.Figure()
        all_lats, all_lons = [], []

        levels = (
            [l for l in self._sorted_storeys if l.storey == storey]
            if storey is not None else self._sorted_storeys
        )

        for lvl in levels:
            z = lvl.storey * self.FLOOR_HEIGHT_M

            # ── Background corridor edges (faint group per floor)
            bg_x, bg_y, bg_z = [], [], []
            for edge in lvl.edges:
                src = lvl.nodes.get(edge.source)
                snk = lvl.nodes.get(edge.sink)
                if (
                    src and snk and src.has_gps and snk.has_gps
                    and src.type in self.CORRIDOR_TYPES
                    and snk.type in self.CORRIDOR_TYPES
                    and (edge.source, edge.sink) not in edge_load
                ):
                    bg_x += [src.lon, snk.lon, None]
                    bg_y += [src.lat, snk.lat, None]
                    bg_z += [z, z, None]
                    all_lats += [src.lat, snk.lat]
                    all_lons += [src.lon, snk.lon]

            if bg_x:
                fig.add_trace(go.Scatter3d(
                    x=bg_x, y=bg_y, z=bg_z,
                    mode="lines",
                    line=dict(color="rgba(140,150,200,0.25)", width=1),
                    name=f"Etage {lvl.storey} (Netz)",
                    showlegend=True,
                    hoverinfo="skip",
                ))

            # ── Hot corridor edges (one trace per edge for individual colours)
            for edge in lvl.edges:
                key = (edge.source, edge.sink)
                load = edge_load.get(key, 0)
                if load <= 0:
                    continue
                src = lvl.nodes.get(edge.source)
                snk = lvl.nodes.get(edge.sink)
                if not (src and snk and src.has_gps and snk.has_gps):
                    continue

                ratio = load / max_load
                r = int(50 + 205 * ratio)
                g = int(120 * (1 - ratio))
                b = int(200 * (1 - ratio))
                color = f"rgb({r},{g},{b})"
                width = 2 + 8 * ratio

                all_lats += [src.lat, snk.lat]
                all_lons += [src.lon, snk.lon]

                fig.add_trace(go.Scatter3d(
                    x=[src.lon, snk.lon],
                    y=[src.lat, snk.lat],
                    z=[z, z],
                    mode="lines",
                    line=dict(color=color, width=width),
                    showlegend=False,
                    hovertemplate=(
                        f"<b>Gang-Segment</b><br>"
                        f"Etage {lvl.storey}<br>"
                        f"Knoten {edge.source} ↔ {edge.sink}<br>"
                        f"Load: {load:.0f}"
                        "<extra></extra>"
                    ),
                ))

            # ── Hot destination nodes (spheres)
            dest_lons_l, dest_lats_l, dest_zs = [], [], []
            dest_colors_l, dest_sizes_l, dest_hover_l = [], [], []
            for node in lvl.nodes.values():
                freq = (
                    self.hot_rooms.get(node.id, 0)
                    or self.hot_rooms.get(node.room_id, 0)
                )
                if freq > 0 and node.has_gps:
                    dest_lons_l.append(node.lon)
                    dest_lats_l.append(node.lat)
                    dest_zs.append(z)
                    dest_colors_l.append(freq)
                    dest_sizes_l.append(5 + min(freq / 8, 14))
                    dest_hover_l.append(
                        f"<b>{node.name or node.room_id or node.id}</b><br>"
                        f"Besuche: {freq}<br>Etage: {lvl.storey}"
                    )
                    all_lats.append(node.lat)
                    all_lons.append(node.lon)

            if dest_lons_l:
                fig.add_trace(go.Scatter3d(
                    x=dest_lons_l, y=dest_lats_l, z=dest_zs,
                    mode="markers",
                    marker=dict(
                        size=dest_sizes_l,
                        color=dest_colors_l,
                        colorscale="YlOrRd",
                        colorbar=dict(
                            title="Besuche",
                            len=0.4, y=0.2, x=1.02,
                        ),
                        opacity=0.95,
                        line=dict(color="white", width=0.5),
                    ),
                    text=dest_hover_l,
                    hovertemplate="%{text}<extra></extra>",
                    name=f"Zielorte Etage {lvl.storey}",
                ))

        # ── Floor reference planes
        if all_lats and all_lons:
            min_lat, max_lat = min(all_lats), max(all_lats)
            min_lon, max_lon = min(all_lons), max(all_lons)
            for lvl in levels:
                z = lvl.storey * self.FLOOR_HEIGHT_M
                fig.add_trace(go.Mesh3d(
                    x=[min_lon, max_lon, max_lon, min_lon],
                    y=[min_lat, min_lat, max_lat, max_lat],
                    z=[z, z, z, z],
                    i=[0, 0], j=[1, 2], k=[2, 3],
                    color="rgba(180,200,240,0.07)",
                    showlegend=False, hoverinfo="skip",
                    name=f"Etage {lvl.storey} (Ebene)",
                ))

        fig.update_layout(
            title=dict(
                text=title or (
                    f"3D Gang-Frequenz-Heatmap — Gebäude '{self.name}'"
                    + (f", Etage {storey}" if storey is not None else "")
                ),
                font=dict(size=16),
            ),
            scene=dict(
                xaxis_title="Längengrad",
                yaxis_title="Breitengrad",
                zaxis_title="Höhe (m)",
                bgcolor="rgba(10,10,20,1)",
                xaxis=dict(gridcolor="#334", showbackground=True,
                           backgroundcolor="rgba(15,15,30,1)"),
                yaxis=dict(gridcolor="#334", showbackground=True,
                           backgroundcolor="rgba(15,15,30,1)"),
                zaxis=dict(gridcolor="#334", showbackground=True,
                           backgroundcolor="rgba(15,15,30,1)"),
            ),
            paper_bgcolor="rgba(10,10,20,1)",
            font=dict(color="white"),
            margin=dict(l=0, r=0, t=60, b=0),
            legend=dict(
                bgcolor="rgba(20,20,40,0.8)",
                bordercolor="#444", borderwidth=1,
            ),
        )
        return fig

    # ── pydeck 3D-over-OSM ────────────────────

    def plot_3d_osm(
        self,
        floor_height_m: float = 8.0,
        pitch: float = 55,
        bearing: float = -20,
        zoom: int = 18,
        map_style: str = "dark",
        show_corridor_load: bool = True,
        column_scale: float = 0.8,
    ):
        """
        3-D pydeck (deck.gl) visualisation with OpenStreetMap as base plate.

        Each storey is lifted by ``floor_height_m`` metres above the true
        GPS ground.  Corridor edges are coloured blue→orange→red by
        projected search load.  Hot destination rooms appear as glowing
        vertical columns (height ∝ visit frequency).

        Parameters
        ----------
        floor_height_m : float
            Vertical spacing between storeys in metres (default 8 m).
        pitch : float
            Camera tilt in degrees (0 = top-down, 60 = steep 3-D).
        bearing : float
            Camera rotation in degrees.
        zoom : int
            Initial map zoom level.
        map_style : str
            Deck.gl map style: ``"dark"`` | ``"light"`` | ``"satellite"``
            or a full URL (e.g. CartoDB / Mapbox style JSON).
        show_corridor_load : bool
            If True, colour corridor edges by projected search load.
        column_scale : float
            Multiplier for destination-column heights (metres per visit).

        Returns
        -------
        pydeck.Deck
        """
        try:
            import pydeck as pdk
        except ImportError:
            raise ImportError(
                "pydeck is required for this visualisation.\n"
                "Install with:  pip install pydeck"
            )

        edge_load, _ = self.compute_corridor_load() if show_corridor_load else ({}, {})
        max_load = max(edge_load.values()) if edge_load else 1

        # Collect GPS centre
        all_lats, all_lons = [], []
        for lvl in self._sorted_storeys:
            for n in lvl.nodes.values():
                if n.has_gps:
                    all_lats.append(n.lat)
                    all_lons.append(n.lon)
        if not all_lats:
            raise ValueError("No GPS nodes found.")
        center_lat = float(np.mean(all_lats))
        center_lon = float(np.mean(all_lons))

        # ── Build edge data ──────────────────────────────────────────────────
        edge_records = []
        for lvl in self._sorted_storeys:
            z = lvl.storey * floor_height_m
            for edge in lvl.edges:
                src = lvl.nodes.get(edge.source)
                snk = lvl.nodes.get(edge.sink)
                if not (src and snk and src.has_gps and snk.has_gps):
                    continue
                load = edge_load.get((edge.source, edge.sink), 0)
                ratio = load / max_load if load > 0 else 0
                # blue(0,120,255) → orange(255,120,0) → red(255,0,0)
                r = int(0   + 255 * ratio)
                g = int(120 * (1 - ratio))
                b = int(255 * (1 - ratio))
                alpha = 220 if load > 0 else 60
                width = max(1, int(1 + 5 * ratio)) if load > 0 else 1
                edge_records.append({
                    "src_lon": src.lon, "src_lat": src.lat, "src_z": z,
                    "snk_lon": snk.lon, "snk_lat": snk.lat, "snk_z": z,
                    "color": [r, g, b, alpha],
                    "width": width,
                    "load": int(load),
                    "storey": lvl.storey,
                })

        # ── Build destination column data ────────────────────────────────────
        col_records = []
        for lvl in self._sorted_storeys:
            z_base = lvl.storey * floor_height_m
            for node in lvl.nodes.values():
                freq = (
                    self.hot_rooms.get(node.id, 0)
                    or self.hot_rooms.get(node.room_id, 0)
                )
                if freq <= 0 or not node.has_gps:
                    continue
                max_freq = max(self.hot_rooms.values()) if self.hot_rooms else 1
                ratio = freq / max_freq
                # rot(255,50,0)=heißest  gelb(255,200,0)=mittel  blau(30,80,220)=kalt
                if ratio > 0.66:
                    r2, g2, b2 = 255, int(50 + 100 * (1 - ratio) * 3), 0
                elif ratio > 0.33:
                    r2 = 255
                    g2 = int(200 * ((ratio - 0.33) / 0.33))
                    b2 = 0
                else:
                    r2 = int(30  + 80  * ratio / 0.33)
                    g2 = int(80  + 120 * ratio / 0.33)
                    b2 = int(220 - 180 * ratio / 0.33)
                col_records.append({
                    "lon": node.lon,
                    "lat": node.lat,
                    "z": z_base,
                    "radius": 2.5 + min(freq / max_freq * 5, 5),
                    "color": [r2, g2, b2, 230],
                    "name": node.name or node.room_id or node.id,
                    "visits": freq,
                    "storey": lvl.storey,
                })

        # ── Build node scatter data (corridor nodes) ─────────────────────────
        node_records = []
        for lvl in self._sorted_storeys:
            z = lvl.storey * floor_height_m
            for node in lvl.nodes.values():
                if not node.has_gps or node.type not in self.CORRIDOR_TYPES:
                    continue
                node_records.append({
                    "lon": node.lon, "lat": node.lat, "z": z,
                    "color": [100, 140, 220, 140],
                    "radius": 0.6,
                    "name": node.id,
                    "storey": lvl.storey,
                })

        import pandas as pd
        df_edges = pd.DataFrame(edge_records)
        df_cols  = pd.DataFrame(col_records)  if col_records  else pd.DataFrame()
        df_nodes = pd.DataFrame(node_records) if node_records else pd.DataFrame()

        layers = []

        # Corridor / edge line layer
        if not df_edges.empty:
            layers.append(pdk.Layer(
                "LineLayer",
                data=df_edges,
                get_source_position=["src_lon", "src_lat", "src_z"],
                get_target_position=["snk_lon", "snk_lat", "snk_z"],
                get_color="color",
                get_width="width",
                width_scale=2,
                width_min_pixels=1,
                pickable=True,
                auto_highlight=True,
            ))

        # Corridor node scatter
        if not df_nodes.empty:
            layers.append(pdk.Layer(
                "ScatterplotLayer",
                data=df_nodes,
                get_position=["lon", "lat", "z"],
                get_fill_color="color",
                get_radius="radius",
                radius_scale=2,
                pickable=False,
                stroked=False,
            ))

        # Hot-room dot scatter (rot=häufig, gelb=mittel, blau=selten)
        if not df_cols.empty:
            layers.append(pdk.Layer(
                "ScatterplotLayer",
                data=df_cols,
                get_position=["lon", "lat", "z"],
                get_fill_color="color",
                get_radius="radius",
                radius_scale=1,
                radius_min_pixels=4,
                radius_max_pixels=18,
                pickable=True,
                auto_highlight=True,
                stroked=True,
                get_line_color=[255, 255, 255, 120],
                line_width_min_pixels=0.5,
            ))

        STYLE_MAP = {
            "dark":      "https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json",
            "light":     "https://basemaps.cartocdn.com/gl/voyager-gl-style/style.json",
            "satellite": "https://basemaps.cartocdn.com/gl/positron-gl-style/style.json",
        }
        style_url = STYLE_MAP.get(map_style, map_style)

        view = pdk.ViewState(
            latitude=center_lat,
            longitude=center_lon,
            zoom=zoom,
            pitch=pitch,
            bearing=bearing,
        )

        return pdk.Deck(
            layers=layers,
            initial_view_state=view,
            map_style=style_url,
            tooltip={
                "html": "<b>{name}</b><br/>Etage: {storey}<br/>Besuche: {visits}<br/>Load: {load}",
                "style": {
                    "backgroundColor": "rgba(20,20,40,0.9)",
                    "color": "white",
                    "fontSize": "12px",
                    "padding": "8px",
                    "borderRadius": "6px",
                },
            },
        )

    def __repr__(self) -> str:
        total_nodes = sum(len(l.nodes) for l in self.levels.values())
        total_edges = sum(len(l.edges) for l in self.levels.values())
        return (f"BuildingGraph(name='{self.name}', "
                f"floors={len(self.levels)}, "
                f"nodes={total_nodes}, edges={total_edges})")


# ─────────────────────────────────────────────
#  UniversityMap – multi-building wrapper
# ─────────────────────────────────────────────

class UniversityMap:
    """
    Combine multiple BuildingGraph objects into one campus-level view.

    Example
    -------
    >>> umap = UniversityMap.from_xml_dir("data/university",
    ...            buildings=["rw", "pt", "chemie"],
    ...            hot_rooms=my_counter)
    >>> umap.plot_campus_osm()
    >>> umap.plot_campus_3d()
    """

    # Approximate storey heights for stacking buildings in 3D
    INTER_BUILDING_OFFSET_Z = 0.0  # same ground level

    _BUILDING_COLORS = [
        "#4A90D9", "#E74C3C", "#2ECC71", "#F39C12",
        "#9B59B6", "#1ABC9C", "#D35400", "#2980B9",
    ]

    def __init__(self, buildings: Dict[str, BuildingGraph]):
        self.buildings = buildings  # name → BuildingGraph

    @classmethod
    def from_xml_dir(cls, xml_dir: str,
                     buildings: Optional[List[str]] = None,
                     hot_rooms: Optional[Counter] = None) -> "UniversityMap":
        """
        Auto-detect building prefixes from XML filenames.

        Parameters
        ----------
        xml_dir : str
            Folder with `<building>_*.xml` files.
        buildings : list of str, optional
            Limit to these prefixes. Auto-detect if None.
        hot_rooms : Counter, optional
            Shared visit frequency counter across all buildings.
        """
        if buildings is None:
            xmls = glob.glob(os.path.join(xml_dir, "*.xml"))
            buildings = list({
                re.match(r"(.+?)_", os.path.basename(f)).group(1)
                for f in xmls
                if re.match(r"(.+?)_", os.path.basename(f))
            })
        result = {}
        for bname in buildings:
            try:
                result[bname] = BuildingGraph.from_xml_dir(
                    xml_dir, bname, hot_rooms=hot_rooms)
            except FileNotFoundError:
                print(f"Warning: no XML files for building '{bname}', skipped.")
        return cls(result)

    def plot_campus_osm(self, zoom_start: int = 17) -> folium.Map:
        """Interactive OSM map showing all buildings."""
        all_lats, all_lons = [], []
        for bg in self.buildings.values():
            for lvl in bg.levels.values():
                for n in lvl.nodes.values():
                    if n.has_gps:
                        all_lats.append(n.lat)
                        all_lons.append(n.lon)

        if not all_lats:
            raise ValueError("No GPS data found.")
        center = [np.mean(all_lats), np.mean(all_lons)]
        m = folium.Map(location=center, zoom_start=zoom_start,
                       tiles="CartoDB positron")

        for idx, (bname, bg) in enumerate(self.buildings.items()):
            color = self._BUILDING_COLORS[idx % len(self._BUILDING_COLORS)]
            fg = folium.FeatureGroup(name=bname.upper(), show=True)

            # Only show ground floor (storey 0) on default campus view
            ground = next(
                (l for l in bg._sorted_storeys if l.storey == 0),
                bg._sorted_storeys[0] if bg._sorted_storeys else None
            )
            if not ground:
                continue

            for edge in ground.edges:
                src = ground.nodes.get(edge.source)
                snk = ground.nodes.get(edge.sink)
                if src and snk and src.has_gps and snk.has_gps:
                    folium.PolyLine(
                        [[src.lat, src.lon], [snk.lat, snk.lon]],
                        color=color, weight=1.5, opacity=0.5,
                    ).add_to(fg)

            for node in ground.nodes.values():
                if not node.has_gps:
                    continue
                freq = bg.hot_rooms.get(node.id, 0) or bg.hot_rooms.get(node.room_id, 0)
                radius = 4 + min(freq / 15, 12) if freq else 3
                fill_color = bg._heat_color(node.id, node.room_id) or color
                label = node.name or node.room_id or node.id
                folium.CircleMarker(
                    location=[node.lat, node.lon],
                    radius=radius,
                    color="white", weight=0.5,
                    fill=True, fill_color=fill_color, fill_opacity=0.85,
                    tooltip=f"[{bname.upper()}] {label}",
                    popup=folium.Popup(
                        f"<b>{label}</b><br>Building: {bname}<br>"
                        f"Room: {node.room_id}<br>Visits: {freq}",
                        max_width=200),
                ).add_to(fg)

            fg.add_to(m)

        folium.LayerControl(collapsed=False).add_to(m)
        return m

    def plot_campus_3d(self) -> go.Figure:
        """3-D figure with all buildings side by side."""
        fig = go.Figure()
        for idx, (bname, bg) in enumerate(self.buildings.items()):
            color = self._BUILDING_COLORS[idx % len(self._BUILDING_COLORS)]
            for lvl in bg._sorted_storeys:
                z = lvl.storey * BuildingGraph.FLOOR_HEIGHT_M
                nodes = [n for n in lvl.nodes.values() if n.has_gps]
                if not nodes:
                    continue
                fig.add_trace(go.Scatter3d(
                    x=[n.lon for n in nodes],
                    y=[n.lat for n in nodes],
                    z=[z] * len(nodes),
                    mode="markers",
                    marker=dict(size=4, color=color, opacity=0.7),
                    name=f"{bname.upper()} F{lvl.storey}",
                    hovertext=[n.name or n.room_id or n.id for n in nodes],
                ))

        fig.update_layout(
            title="University Campus — 3D Navigation Overview",
            scene=dict(
                xaxis_title="Longitude", yaxis_title="Latitude",
                zaxis_title="Height (m)",
                bgcolor="rgba(10,10,20,1)",
            ),
            paper_bgcolor="rgba(10,10,20,1)",
            font=dict(color="white"),
            margin=dict(l=0, r=0, t=50, b=0),
        )
        return fig

    def get_campus_hot_df(self, top_n: int = 50) -> pd.DataFrame:
        """Combined hot-area DataFrame across all buildings."""
        frames = []
        for bg in self.buildings.values():
            df = bg.get_hot_nodes(top_n)
            frames.append(df)
        if not frames:
            return pd.DataFrame()
        combined = pd.concat(frames, ignore_index=True)
        return combined.sort_values("visits", ascending=False).head(top_n)

    def plot_campus_3d_osm(
        self,
        floor_height_m: float = 8.0,
        pitch: float = 55,
        bearing: float = -20,
        zoom: int = 17,
        map_style: str = "dark",
        column_scale: float = 0.6,
    ):
        """
        Campus-wide 3-D pydeck map over OpenStreetMap.

        All buildings are shown together, each in its own colour palette.
        Corridor edges are coloured by projected search load within each
        building.  Hot destination rooms rise as glowing column towers.

        Parameters
        ----------
        floor_height_m : float
            Vertical storey spacing in metres.
        pitch / bearing / zoom
            Camera orientation.
        map_style : str
            ``"dark"`` | ``"light"`` or a style-JSON URL.
        column_scale : float
            Metres per visit for the column towers.

        Returns
        -------
        pydeck.Deck
        """
        try:
            import pydeck as pdk
        except ImportError:
            raise ImportError("Install pydeck:  pip install pydeck")

        BLDG_PALETTES = [
            ([74,  144, 217], [0,  100, 220]),   # blue
            ([231,  76,  60], [180,  20,  10]),  # red
            ([46,  204, 113], [20, 140,  60]),   # green
            ([243, 156,  18], [200, 110,   0]),  # orange
            ([155,  89, 182], [100,  40, 140]),  # purple
            ([26,  188, 156], [10, 130, 110]),   # teal
            ([211,  84,   0], [160,  50,   0]),  # dark orange
            ([41, 128, 185], [10,  80, 150]),    # navy
        ]

        all_lats, all_lons = [], []
        all_edge_records, all_col_records = [], []

        for bidx, (bname, bg) in enumerate(self.buildings.items()):
            hot_c, cold_c = BLDG_PALETTES[bidx % len(BLDG_PALETTES)]
            edge_load, _ = bg.compute_corridor_load()
            max_load = max(edge_load.values()) if edge_load else 1
            max_freq = max(bg.hot_rooms.values()) if bg.hot_rooms else 1

            for lvl in bg._sorted_storeys:
                z = lvl.storey * floor_height_m

                for edge in lvl.edges:
                    src = lvl.nodes.get(edge.source)
                    snk = lvl.nodes.get(edge.sink)
                    if not (src and snk and src.has_gps and snk.has_gps):
                        continue
                    all_lats += [src.lat, snk.lat]
                    all_lons += [src.lon, snk.lon]
                    load = edge_load.get((edge.source, edge.sink), 0)
                    ratio = min(load / max_load, 1.0) if load > 0 else 0
                    # Interpolate building's cold→hot colour
                    r = int(cold_c[0] + (hot_c[0] - cold_c[0]) * ratio)
                    g = int(cold_c[1] + (hot_c[1] - cold_c[1]) * ratio)
                    b = int(cold_c[2] + (hot_c[2] - cold_c[2]) * ratio)
                    alpha = 200 if load > 0 else 45
                    all_edge_records.append({
                        "src_lon": src.lon, "src_lat": src.lat, "src_z": z,
                        "snk_lon": snk.lon, "snk_lat": snk.lat, "snk_z": z,
                        "color": [r, g, b, alpha],
                        "width": max(1, int(1 + 4 * ratio)),
                        "load": int(load),
                        "building": bname,
                        "storey": lvl.storey,
                    })

                for node in lvl.nodes.values():
                    freq = (
                        bg.hot_rooms.get(node.id, 0)
                        or bg.hot_rooms.get(node.room_id, 0)
                    )
                    if freq <= 0 or not node.has_gps:
                        continue
                    ratio = freq / max_freq
                    all_lats.append(node.lat)
                    all_lons.append(node.lon)
                    # rot=heißest  gelb=mittel  blau=kalt
                    if ratio > 0.66:
                        nr, ng, nb2 = 255, int(50 + 100 * (1 - ratio) * 3), 0
                    elif ratio > 0.33:
                        nr, ng, nb2 = 255, int(200 * ((ratio - 0.33) / 0.33)), 0
                    else:
                        nr = int(30  + 80  * ratio / 0.33)
                        ng = int(80  + 120 * ratio / 0.33)
                        nb2 = int(220 - 180 * ratio / 0.33)
                    all_col_records.append({
                        "lon": node.lon, "lat": node.lat,
                        "z": z,
                        "radius": 2.5 + min(ratio * 5, 5),
                        "color": [nr, ng, nb2, 230],
                        "name": node.name or node.room_id or node.id,
                        "visits": freq,
                        "building": bname,
                        "storey": lvl.storey,
                    })

        import pandas as pd
        df_edges = pd.DataFrame(all_edge_records)
        df_cols  = pd.DataFrame(all_col_records) if all_col_records else pd.DataFrame()

        layers = []
        if not df_edges.empty:
            layers.append(pdk.Layer(
                "LineLayer",
                data=df_edges,
                get_source_position=["src_lon", "src_lat", "src_z"],
                get_target_position=["snk_lon", "snk_lat", "snk_z"],
                get_color="color",
                get_width="width",
                width_scale=2,
                width_min_pixels=1,
                pickable=True,
                auto_highlight=True,
            ))
        if not df_cols.empty:
            layers.append(pdk.Layer(
                "ScatterplotLayer",
                data=df_cols,
                get_position=["lon", "lat", "z"],
                get_fill_color="color",
                get_radius="radius",
                radius_scale=1,
                radius_min_pixels=4,
                radius_max_pixels=18,
                pickable=True,
                auto_highlight=True,
                stroked=True,
                get_line_color=[255, 255, 255, 100],
                line_width_min_pixels=0.5,
            ))

        STYLE_MAP = {
            "dark":  "https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json",
            "light": "https://basemaps.cartocdn.com/gl/voyager-gl-style/style.json",
        }
        style_url = STYLE_MAP.get(map_style, map_style)

        center_lat = float(np.mean(all_lats)) if all_lats else 48.999
        center_lon = float(np.mean(all_lons)) if all_lons else 12.093

        view = pdk.ViewState(
            latitude=center_lat,
            longitude=center_lon,
            zoom=zoom,
            pitch=pitch,
            bearing=bearing,
        )
        return pdk.Deck(
            layers=layers,
            initial_view_state=view,
            map_style=style_url,
            tooltip={
                "html": "<b>{name}</b><br/>{building} · Etage {storey}<br/>Besuche: {visits}",
                "style": {
                    "backgroundColor": "rgba(15,15,35,0.92)",
                    "color": "white",
                    "fontSize": "12px",
                    "padding": "8px",
                    "borderRadius": "6px",
                },
            },
        )

    def __repr__(self) -> str:
        return (f"UniversityMap(buildings={list(self.buildings)}, "
                f"total_floors={sum(len(b.levels) for b in self.buildings.values())})")


# ─────────────────────────────────────────────
#  Utility: build hot_rooms Counter from CSV
# ─────────────────────────────────────────────

def build_hot_rooms_counter(csv_path: str,
                             building_filter: Optional[str] = None,
                             dest_col: str = "Zielort") -> Counter:
    """
    Build a node-visit Counter from the location CSV.

    The CSV destination column is expected to contain strings like:
        ``"rw 2 409"``  →  building="rw", storey=2, node_id="409"

    The returned Counter maps **node_id strings** to visit counts.
    ``BuildingGraph.get_hot_nodes`` looks up both node IDs and room IDs,
    so the Counter works regardless of which identifier matches the XML.

    Parameters
    ----------
    csv_path : str
        Path to location.csv.
    building_filter : str, optional
        Only include rows for this building prefix (e.g. ``"rw"``).
        Case-insensitive. Pass ``None`` to include all buildings.
    dest_col : str
        Column name holding destination strings (default ``"Zielort"``).

    Returns
    -------
    Counter
        Mapping node_id → visit_count.
    """
    df = pd.read_csv(csv_path, low_memory=False)
    if dest_col not in df.columns:
        close = [c for c in df.columns if "ziel" in c.lower() or "dest" in c.lower()]
        if not close:
            raise KeyError(f"Column '{dest_col}' not found. Available: {df.columns.tolist()}")
        dest_col = close[0]

    counter: Counter = Counter()
    pattern = re.compile(r"^(\S+)\s+(\S+)\s+(\S+)$")
    for val in df[dest_col].dropna():
        m = pattern.match(str(val).strip())
        if not m:
            continue
        bld, _storey, node_id = m.group(1), m.group(2), m.group(3)
        if building_filter and bld.lower() != building_filter.lower():
            continue
        counter[node_id] += 1
    return counter
