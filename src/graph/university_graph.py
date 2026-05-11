"""
university_graph.py
-------------------
Builds a unified, navigable graph from all the indoor-floor XML files in
data/university/ plus the outdoor campus map (outside_0.xml).

Usage inside Analysis.ipynb
============================
    import importlib, university_graph
    importlib.reload(university_graph)          # convenient when iterating

    G = university_graph.UniversityGraph()      # build with default data dir
    G = university_graph.UniversityGraph("data/university")   # explicit dir

Main attributes
===============
G.graph        : networkx.Graph with every node and edge from all files
G.nodes_df     : pandas DataFrame with one row per node
G.edges_df     : pandas DataFrame with one row per edge
G.buildings    : list of building names found
G.levels       : dict  building_name -> list of (level_id, storey)

Node attributes (stored in G.graph.node_attributes / G.nodes_df columns)
=========================================================================
  node_key   - unique str  "{building}::{level_id}::{node_id}"
  building   - e.g. "ZentralesHoersaalgebaeude"
  level_id   - int  (the id= attribute of the <level> element)
  storey     - int  (the storey= attribute; floor number)
  node_id    - int  (original id inside the file)
  x, y       - pixel coordinates
  type       - "branch", "doorway", "Lecturehall", "link", "GpsLink", …
  name       - human-readable label (may be empty)
  filename   - target building for cross-file links (type=="link")
  link_level - target level  for cross-file links
  link_nodeid- target node   for cross-file links
  lat, long  - GPS coordinates (GpsLink nodes only)
  roomid, lsf, isdestination, imageid, tags  - misc optional fields

Edge attributes
===============
  edge_key   - unique str
  building   - source building
  level_id   - source level id
  edge_id    - original id= inside the file
  source_key / sink_key - node_key of both endpoints
  type       - int (0 = normal walk, higher values = stairs/lift/…)
  cross_building - True when the edge bridges two different buildings
"""

import os
import glob
import xml.etree.ElementTree as ET
from collections import defaultdict
from typing import Optional

import networkx as nx
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import matplotlib.lines as mlines
import numpy as np


# ---------------------------------------------------------------------------
# Helper: derive a "building" name from an XML filename
# ---------------------------------------------------------------------------

def _building_from_path(xml_path: str) -> str:
    """
    'data/university/ZentralesHoersaalgebaeude_0.xml'
    -> 'ZentralesHoersaalgebaeude'

    'data/university/outside_0.xml'
    -> 'outside'   (will be treated as "Campusplan" alias)
    """
    basename = os.path.basename(xml_path)          # e.g. "ZentralesHoersaalgebaeude_0.xml"
    name_no_ext = os.path.splitext(basename)[0]    # "ZentralesHoersaalgebaeude_0"
    # drop trailing  _<number>
    parts = name_no_ext.rsplit("_", 1)
    if len(parts) == 2 and parts[1].lstrip("-").isdigit():
        return parts[0]
    return name_no_ext


# ---------------------------------------------------------------------------
# Main class
# ---------------------------------------------------------------------------

class UniversityGraph:
    """
    Loads all XML floor plans from *data_dir* and stitches them into one
    unified NetworkX graph, with cross-building edges wherever a
    <node type="link"> exists on both sides.
    """

    # The outdoor campus graph is stored under filename "Campusplan" in links
    # but lives in the file called "outside".
    OUTSIDE_BUILDING = "outside"
    CAMPUSPLAN_ALIAS = "Campusplan"

    def __init__(self, data_dir: str = "data/university"):
        self.data_dir = data_dir
        self.graph: nx.Graph = nx.Graph()

        # Will be populated during build
        self._node_records: list[dict] = []
        self._edge_records: list[dict] = []

        # building -> graph_name (from <graph name="…">)
        self._graph_names: dict[str, str] = {}

        # building -> list of {level_id, storey, mapfile, …}
        self.levels: dict[str, list[dict]] = defaultdict(list)

        # (building, level_id) -> affine transform coefficients for pixel->WGS84
        # lat = xlat*px + ylat*py + wlat
        # lon = xlon*px + ylon*py + wlon
        self._geo_transform: dict = {}

        self._build()

        self.nodes_df = pd.DataFrame(self._node_records)
        self.edges_df = pd.DataFrame(self._edge_records)
        self.buildings = sorted(self._graph_names.keys())

    # ------------------------------------------------------------------
    # Build pipeline
    # ------------------------------------------------------------------

    def _build(self):
        xml_files = sorted(glob.glob(os.path.join(self.data_dir, "*.xml")))
        if not xml_files:
            raise FileNotFoundError(
                f"No XML files found in '{self.data_dir}'. "
                "Make sure the path is correct."
            )

        # Step 1 – parse every file and add nodes + internal edges
        for xml_path in xml_files:
            self._parse_file(xml_path)

        # Step 2 – add cross-building edges for every link node pair
        self._stitch_buildings()

    # ------------------------------------------------------------------

    def _parse_file(self, xml_path: str):
        building = _building_from_path(xml_path)

        tree = ET.parse(xml_path)
        root = tree.getroot()

        # Store graph-level name
        self._graph_names[building] = root.attrib.get("name", building)

        for level_el in root.findall("level"):
            level_id = int(level_el.attrib["id"])
            storey = int(level_el.attrib.get("storey", level_id - 1))

            level_meta = {k: v for k, v in level_el.attrib.items()}
            level_meta["level_id"] = level_id
            level_meta["storey"] = storey
            self.levels[building].append(level_meta)

            # --- parse pixel->WGS84 affine transform for this level ---
            geo_el = level_el.find("pixelToWGS84")
            if geo_el is not None:
                try:
                    xf = {
                        "xlat": float(geo_el.find("xlat").text),
                        "ylat": float(geo_el.find("ylat").text),
                        "wlat": float(geo_el.find("wlat").text),
                        "xlon": float(geo_el.find("xlon").text),
                        "ylon": float(geo_el.find("ylon").text),
                        "wlon": float(geo_el.find("wlon").text),
                    }
                    self._geo_transform[(building, level_id)] = xf
                except (AttributeError, TypeError, ValueError):
                    pass

            # --- nodes ---
            for node_el in level_el.findall("node"):
                node_id = int(node_el.attrib["id"])
                node_key = f"{building}::{level_id}::{node_id}"

                px_str = node_el.attrib.get("x")
                py_str = node_el.attrib.get("y")

                _lat_str = node_el.attrib.get("lat")
                _lon_str = node_el.attrib.get("long")

                px = float(px_str) if px_str else 0.0
                py = float(py_str) if py_str else 0.0

                # Compute geographic lat/lon
                # Priority: direct GPS > affine transform (only if px,py exist)
                if _lat_str is not None and _lon_str is not None:
                    geo_lat = float(_lat_str)
                    geo_lon = float(_lon_str)
                elif px_str is not None and py_str is not None:
                    xf = self._geo_transform.get((building, level_id))
                    if xf:
                        geo_lat = xf["xlat"] * px + xf["ylat"] * py + xf["wlat"]
                        geo_lon = xf["xlon"] * px + xf["ylon"] * py + xf["wlon"]
                    else:
                        geo_lat = None
                        geo_lon = None
                else:
                    geo_lat = None
                    geo_lon = None

                attrs = {
                    "node_key":    node_key,
                    "building":    building,
                    "level_id":    level_id,
                    "storey":      storey,
                    "node_id":     node_id,
                    "x":           px,
                    "y":           py,
                    "type":        node_el.attrib.get("type", ""),
                    "name":        node_el.attrib.get("name", ""),
                    # link-specific
                    "filename":    node_el.attrib.get("filename", ""),
                    "link_level":  int(node_el.attrib["level"])
                                   if "level" in node_el.attrib else None,
                    "link_nodeid": int(node_el.attrib["nodeid"])
                                   if "nodeid" in node_el.attrib else None,
                    # GPS (raw attrs, kept for compat)
                    "lat":  float(_lat_str)  if _lat_str  else None,
                    "long": float(_lon_str) if _lon_str else None,
                    # Computed geographic position (use these for mapping)
                    "geo_lat": geo_lat,
                    "geo_lon": geo_lon,
                    # misc
                    "roomid":        node_el.attrib.get("roomid", ""),
                    "lsf":           node_el.attrib.get("lsf", ""),
                    "isdestination": node_el.attrib.get("isdestination", ""),
                    "imageid":       node_el.attrib.get("imageid", ""),
                    "tags":          node_el.attrib.get("tags", ""),
                }

                self.graph.add_node(node_key, **attrs)
                self._node_records.append(attrs)

            # --- edges (within a single level) ---
            for edge_el in level_el.findall("edge"):
                edge_id = int(edge_el.attrib["id"])
                source_id = int(edge_el.attrib["source"])
                sink_id   = int(edge_el.attrib["sink"])

                source_key = f"{building}::{level_id}::{source_id}"
                sink_key   = f"{building}::{level_id}::{sink_id}"

                edge_key = f"{building}::{level_id}::e{edge_id}"
                edge_attrs = {
                    "edge_key":       edge_key,
                    "building":       building,
                    "level_id":       level_id,
                    "edge_id":        edge_id,
                    "source_key":     source_key,
                    "sink_key":       sink_key,
                    "type":           int(edge_el.attrib.get("type", 0)),
                    "cross_building": False,
                }

                # Guard: both endpoints must exist (they should)
                if self.graph.has_node(source_key) and self.graph.has_node(sink_key):
                    self.graph.add_edge(source_key, sink_key, **edge_attrs)
                    self._edge_records.append(edge_attrs)

    # ------------------------------------------------------------------

    def _normalise_filename(self, filename: str) -> str:
        """
        Inside the XML the outdoor campus is referenced as "Campusplan"
        but the file is called "outside".  Normalise here.
        """
        if filename == self.CAMPUSPLAN_ALIAS:
            return self.OUTSIDE_BUILDING
        return filename

    def _stitch_buildings(self):
        """
        For every 'link' node in building A that points to (building B, level L, node N),
        add an edge between:
            A::{level_id}::{node_id}   <->   B::{L}::{N}
        if both nodes exist in the graph.
        """
        link_nodes = [
            data for _, data in self.graph.nodes(data=True)
            if data.get("type") == "link" and data.get("filename")
        ]

        for src_data in link_nodes:
            target_building = self._normalise_filename(src_data["filename"])
            target_level    = src_data["link_level"]
            target_node_id  = src_data["link_nodeid"]

            if target_level is None or target_node_id is None:
                continue

            src_key = src_data["node_key"]
            dst_key = f"{target_building}::{target_level}::{target_node_id}"

            if not self.graph.has_node(dst_key):
                # Target node not in graph (possibly a building we don't have)
                continue

            if self.graph.has_edge(src_key, dst_key):
                continue

            edge_attrs = {
                "edge_key":       f"cross::{src_key}::{dst_key}",
                "building":       f"{src_data['building']} -> {target_building}",
                "level_id":       src_data["level_id"],
                "edge_id":        None,
                "source_key":     src_key,
                "sink_key":       dst_key,
                "type":           0,
                "cross_building": True,
            }
            self.graph.add_edge(src_key, dst_key, **edge_attrs)
            self._edge_records.append(edge_attrs)

    # ------------------------------------------------------------------
    # Convenience query helpers
    # ------------------------------------------------------------------

    def get_nodes(self, building: str = None, storey: int = None,
                  node_type: str = None) -> pd.DataFrame:
        """
        Filter self.nodes_df.

        Examples:
            G.get_nodes(building="ZentralesHoersaalgebaeude", storey=0)
            G.get_nodes(node_type="Lecturehall")
        """
        df = self.nodes_df
        if building is not None:
            df = df[df["building"] == building]
        if storey is not None:
            df = df[df["storey"] == storey]
        if node_type is not None:
            df = df[df["type"] == node_type]
        return df.reset_index(drop=True)

    def get_edges(self, building: str = None,
                  cross_building: bool = None) -> pd.DataFrame:
        """
        Filter self.edges_df.

        Examples:
            G.get_edges(cross_building=True)
            G.get_edges(building="outside")
        """
        df = self.edges_df
        if building is not None:
            df = df[df["building"].str.startswith(building)]
        if cross_building is not None:
            df = df[df["cross_building"] == cross_building]
        return df.reset_index(drop=True)

    def shortest_path(self, src_node_key: str,
                      dst_node_key: str) -> list[str]:
        """
        Return the shortest path (list of node_keys) between two nodes
        in the unified graph.
        """
        return nx.shortest_path(self.graph, src_node_key, dst_node_key)

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"UniversityGraph("
            f"buildings={self.buildings}, "
            f"nodes={self.graph.number_of_nodes()}, "
            f"edges={self.graph.number_of_edges()})"
        )

    # ------------------------------------------------------------------
    # Visualisation helpers
    # ------------------------------------------------------------------

    # Colour palette used consistently across all plot methods
    _TYPE_COLORS = {
        "branch":          "#adb5bd",   # grey
        "doorway":         "#4dabf7",   # light blue
        "doorwayElectric": "#339af0",   # blue
        "Entry":           "#51cf66",   # green
        "Lecturehall":     "#f03e3e",   # red
        "link":            "#f59f00",   # amber  (cross-building connector)
        "GpsLink":         "#cc5de8",   # purple
        "Landmark":        "#ffa94d",   # orange
        "Globallandmark":  "#ff6b6b",   # salmon
        "Toilet":          "#74c0fc",   # sky
        "Areanode":        "#a9e34b",   # lime
        "Bus":             "#20c997",   # teal
        "Parking":         "#868e96",   # dark grey
        "Shop":            "#f783ac",   # pink
    }
    _DEFAULT_COLOR = "#dee2e6"

    # Edge colours by type integer
    _EDGE_COLORS = {
        0: "#ced4da",   # normal walkway
        1: "#74c0fc",   # room edge
        2: "#a9e34b",   # stairs up
        3: "#ffa94d",   # stairs down
        6: "#f59f00",   # cross-level connector
        9: "#cc5de8",   # image / landmark edge
    }
    _CROSS_EDGE_COLOR = "#f03e3e"   # red for cross-building edges

    def _node_color(self, node_type: str) -> str:
        return self._TYPE_COLORS.get(node_type, self._DEFAULT_COLOR)

    def _edge_color(self, edge_type: int, cross: bool) -> str:
        if cross:
            return self._CROSS_EDGE_COLOR
        return self._EDGE_COLORS.get(edge_type, "#ced4da")

    # ---- 1. Single floor --------------------------------------------------

    def plot_floor(
        self,
        building: str,
        level_id: int,
        *,
        ax: Optional["plt.Axes"] = None,
        figsize: tuple = (14, 10),
        node_size: int = 20,
        show_names: bool = True,
        name_types: tuple = ("Lecturehall", "Entry", "Bus", "Landmark"),
        title: Optional[str] = None,
    ) -> "plt.Figure":
        """
        Draw one floor of a building using its pixel (x, y) coordinates.

        Parameters
        ----------
        building   : short building name, e.g. "ZentralesHoersaalgebaeude"
        level_id   : integer level id (the id= attribute in the XML)
        ax         : existing matplotlib Axes; creates a new figure if None
        figsize    : figure size when creating a new figure
        node_size  : scatter marker size
        show_names : whether to annotate named nodes
        name_types : which node types to annotate (default: lecture halls etc.)
        title      : custom figure title

        Returns
        -------
        matplotlib Figure
        """
        # Collect nodes on this floor
        nodes_here = {
            k: d for k, d in self.graph.nodes(data=True)
            if d["building"] == building and d["level_id"] == level_id
        }
        if not nodes_here:
            raise ValueError(
                f"No nodes found for building='{building}' level_id={level_id}. "
                f"Available buildings: {self.buildings}"
            )

        if ax is None:
            fig, ax = plt.subplots(figsize=figsize)
        else:
            fig = ax.get_figure()

        # Build position dict for networkx drawing
        pos = {k: (d["x"], -d["y"]) for k, d in nodes_here.items()}   # flip y

        # Subgraph of just these nodes
        sub = self.graph.subgraph(list(nodes_here.keys()))

        # Separate cross-building edges
        normal_edges = [(u, v) for u, v, d in sub.edges(data=True) if not d.get("cross_building")]
        cross_edges  = [(u, v) for u, v, d in sub.edges(data=True) if d.get("cross_building")]

        # Group nodes by type for colouring
        type_groups: dict[str, list] = defaultdict(list)
        for k, d in nodes_here.items():
            type_groups[d["type"]].append(k)

        # Draw edges
        for u, v in normal_edges:
            etype = sub[u][v].get("type", 0)
            color = self._edge_color(etype, False)
            x0, y0 = pos[u]; x1, y1 = pos[v]
            ax.plot([x0, x1], [y0, y1], color=color, linewidth=0.6, alpha=0.7, zorder=1)

        for u, v in cross_edges:
            x0, y0 = pos[u]; x1, y1 = pos[v]
            ax.plot([x0, x1], [y0, y1], color=self._CROSS_EDGE_COLOR,
                    linewidth=1.2, linestyle="--", alpha=0.9, zorder=2)

        # Draw nodes by type
        for ntype, keys in type_groups.items():
            xs = [pos[k][0] for k in keys]
            ys = [pos[k][1] for k in keys]
            color = self._node_color(ntype)
            ax.scatter(xs, ys, s=node_size, color=color, zorder=3,
                       label=ntype, edgecolors="white", linewidths=0.3)

        # Annotate named nodes
        if show_names:
            for k, d in nodes_here.items():
                if d["name"] and d["type"] in name_types:
                    ax.annotate(
                        d["name"],
                        xy=pos[k],
                        fontsize=6.5,
                        color="#212529",
                        ha="center", va="bottom",
                        xytext=(0, 4),
                        textcoords="offset points",
                        zorder=5,
                    )

        # Legend (only types present on this floor)
        present = sorted(type_groups.keys())
        handles = [
            mpatches.Patch(color=self._node_color(t), label=t)
            for t in present
        ]
        ax.legend(handles=handles, loc="upper right", fontsize=7,
                  framealpha=0.85, title="Node type", title_fontsize=7)

        storey = next(iter(nodes_here.values()))["storey"]
        default_title = (
            f"{self._graph_names.get(building, building)}  "
            f"– Storey {storey:+d}  (level_id={level_id})"
        )
        ax.set_title(title or default_title, fontsize=11, fontweight="bold")
        ax.set_aspect("equal")
        ax.axis("off")
        fig.tight_layout()
        return fig

    # ---- 2. All floors of one building ------------------------------------

    def plot_building(
        self,
        building: str,
        *,
        figsize_per_floor: tuple = (7, 5),
        node_size: int = 15,
        show_names: bool = True,
        name_types: tuple = ("Lecturehall", "Entry", "Bus", "Landmark"),
    ) -> "plt.Figure":
        """
        Draw every floor of *building* as a grid of subplots.

        Parameters
        ----------
        building          : short building name
        figsize_per_floor : (width, height) per subplot column
        node_size         : scatter marker size
        show_names        : annotate named nodes
        name_types        : which types get text labels

        Returns
        -------
        matplotlib Figure
        """
        if building not in self.levels:
            raise ValueError(
                f"Building '{building}' not found. "
                f"Available: {self.buildings}"
            )

        level_ids = sorted(m["level_id"] for m in self.levels[building])
        n = len(level_ids)
        ncols = min(n, 3)
        nrows = (n + ncols - 1) // ncols

        fig, axes = plt.subplots(
            nrows, ncols,
            figsize=(figsize_per_floor[0] * ncols,
                     figsize_per_floor[1] * nrows),
            squeeze=False,
        )

        for idx, level_id in enumerate(level_ids):
            r, c = divmod(idx, ncols)
            try:
                self.plot_floor(
                    building, level_id,
                    ax=axes[r][c],
                    node_size=node_size,
                    show_names=show_names,
                    name_types=name_types,
                )
            except ValueError:
                axes[r][c].set_visible(False)

        # Hide unused axes
        for idx in range(n, nrows * ncols):
            r, c = divmod(idx, ncols)
            axes[r][c].set_visible(False)

        graph_name = self._graph_names.get(building, building)
        fig.suptitle(graph_name, fontsize=14, fontweight="bold", y=1.01)
        fig.tight_layout()
        return fig

    # ---- 3. Campus overview (cross-building) ------------------------------

    def plot_campus(
        self,
        *,
        figsize: tuple = (16, 12),
        node_size: int = 6,
        show_names: bool = True,
        cross_only: bool = False,
        alpha: float = 0.55,
    ) -> "plt.Figure":
        """
        Draw the full campus using each node's pixel (x, y) coordinates,
        colour-coded by building.  Cross-building edges are drawn in red.

        Because different buildings use independent coordinate systems the
        positions are normalised: each building's nodes are placed in a
        grid cell whose column = building index.

        Parameters
        ----------
        figsize       : figure size
        node_size     : scatter marker size
        show_names    : annotate nodes that carry a non-empty 'name'
        cross_only    : if True, draw only the cross-building link nodes
                        (much less cluttered)
        alpha         : transparency of normal edges and non-link nodes

        Returns
        -------
        matplotlib Figure
        """
        # Build colour palette
        try:
            cmap = plt.colormaps["tab10"]
        except AttributeError:
            cmap = plt.cm.get_cmap("tab10")
        bld_color = {b: cmap(i / max(len(self.buildings), 1))
                     for i, b in enumerate(self.buildings)}

        fig, ax = plt.subplots(figsize=figsize)

        # Compute normalised positions:
        # stack buildings side by side along x, keep relative y within each
        CELL_W = 1.2   # horizontal spacing between building columns

        bld_pos: dict[str, tuple] = {}  # node_key -> (x, y)

        for col_idx, bld in enumerate(self.buildings):
            nodes_bld = {k: d for k, d in self.graph.nodes(data=True)
                         if d["building"] == bld}
            if not nodes_bld:
                continue
            xs = np.array([d["x"] for d in nodes_bld.values()])
            ys = np.array([d["y"] for d in nodes_bld.values()])
            x_range = xs.max() - xs.min() or 1
            y_range = ys.max() - ys.min() or 1
            offset_x = col_idx * CELL_W

            for k, d in nodes_bld.items():
                nx_ = offset_x + (d["x"] - xs.min()) / x_range
                ny_ = -(d["y"] - ys.min()) / y_range   # flip y
                bld_pos[k] = (nx_, ny_)

        # --- decide which nodes to draw ---
        if cross_only:
            draw_keys = {k for k, d in self.graph.nodes(data=True)
                         if d.get("type") == "link"}
        else:
            draw_keys = set(self.graph.nodes())

        # Draw normal (within-building) edges first
        for u, v, d in self.graph.edges(data=True):
            if d.get("cross_building"):
                continue
            if u not in bld_pos or v not in bld_pos:
                continue
            if cross_only and u not in draw_keys and v not in draw_keys:
                continue
            x0, y0 = bld_pos[u]; x1, y1 = bld_pos[v]
            color = self._edge_color(d.get("type", 0), False)
            ax.plot([x0, x1], [y0, y1], color=color,
                    linewidth=0.35, alpha=alpha * 0.6, zorder=1)

        # Draw cross-building edges
        cross_edges_drawn = 0
        for u, v, d in self.graph.edges(data=True):
            if not d.get("cross_building"):
                continue
            if u not in bld_pos or v not in bld_pos:
                continue
            x0, y0 = bld_pos[u]; x1, y1 = bld_pos[v]
            ax.plot([x0, x1], [y0, y1], color=self._CROSS_EDGE_COLOR,
                    linewidth=1.2, linestyle="--", alpha=0.85, zorder=3)
            cross_edges_drawn += 1

        # Draw nodes per building
        for bld in self.buildings:
            if cross_only:
                keys = [k for k in draw_keys
                        if self.graph.nodes[k]["building"] == bld
                        and k in bld_pos]
            else:
                keys = [k for k, d in self.graph.nodes(data=True)
                        if d["building"] == bld and k in bld_pos]
            if not keys:
                continue
            node_pos = np.array([bld_pos[k] for k in keys])
            ax.scatter(
                node_pos[:, 0], node_pos[:, 1],
                s=node_size if not cross_only else node_size * 4,
                color=bld_color[bld],
                label=self._graph_names.get(bld, bld),
                zorder=4, edgecolors="white", linewidths=0.3,
                alpha=alpha,
            )

        # Annotate named nodes
        if show_names:
            for k, d in self.graph.nodes(data=True):
                if d.get("name") and k in bld_pos:
                    if cross_only and k not in draw_keys:
                        continue
                    x, y = bld_pos[k]
                    ax.annotate(
                        d["name"], xy=(x, y),
                        fontsize=5, color="#212529",
                        xytext=(2, 2), textcoords="offset points", zorder=5,
                    )

        # Add building name labels at top of each column
        for col_idx, bld in enumerate(self.buildings):
            ax.text(
                col_idx * CELL_W + 0.5, 0.05,
                self._graph_names.get(bld, bld),
                ha="center", va="bottom", fontsize=8,
                fontweight="bold", color=bld_color[bld],
                transform=ax.transData, alpha=0.85,
            )

        # Legend
        bld_handles = [
            mpatches.Patch(color=bld_color[b],
                           label=self._graph_names.get(b, b))
            for b in self.buildings
        ]
        cross_handle = mlines.Line2D(
            [], [], color=self._CROSS_EDGE_COLOR,
            linewidth=1.5, linestyle="--", label="Cross-building edge"
        )
        ax.legend(
            handles=bld_handles + [cross_handle],
            loc="upper right", fontsize=8,
            framealpha=0.9, title="Building", title_fontsize=8
        )

        mode = "link nodes only" if cross_only else "all nodes"
        ax.set_title(
            "University Campus – unified navigation graph\n"
            f"({self.graph.number_of_nodes()} nodes, "
            f"{self.graph.number_of_edges()} edges, "
            f"{cross_edges_drawn} cross-building links shown · {mode})",
            fontsize=11, fontweight="bold"
        )
        ax.axis("off")
        fig.tight_layout()
        return fig

    # ---- 4. Interactive OpenStreetMap map (folium) -----------------------

    def plot_folium(
        self,
        *,
        building: Optional[str] = None,
        storey: Optional[int] = None,
        show_edges: bool = True,
        show_nodes: bool = True,
        show_edge_types: tuple = (0, 1, 2, 3, 4, 5, 6),
        hide_types: tuple = ("branch", "Areanode", "GpsLink"),
        node_radius: int = 4,
        edge_weight: float = 1.5,
        edge_opacity: float = 0.55,
    ):
        """
        Render the graph on an interactive OpenStreetMap using **folium**.

        Every node is converted to geographic coordinates via the per-level
        pixelToWGS84 affine transform stored in the XML. Nodes whose type
        appears in *hide_types* are still used as edge endpoints but not drawn
        as separate markers (keeps the map uncluttered).
        
        Edges with a type not in `show_edge_types` (like type 9, which represents 
        logical associations across huge distances) are hidden by default.

        Parameters
        ----------
        building        : restrict to one building (None = all)
        storey          : further restrict to one storey (None = all)
        show_edges      : draw walking-path edges
        show_nodes      : draw node markers
        show_edge_types : which edge types to draw (default: walkable paths)
        hide_types      : node types to suppress as markers (still used for edges)
        node_radius     : CircleMarker radius in pixels
        edge_weight     : PolyLine weight
        edge_opacity    : PolyLine opacity

        Returns
        -------
        folium.Map   – display with  m  (or  m.save("map.html") )
        """
        try:
            import folium
            from folium.plugins import MeasureControl, MiniMap
        except ImportError:
            raise ImportError(
                "folium is required for OSM maps.\n"
                "Install it with:  pip install folium"
            )

        # ---- colour palette -----------------------------------------------
        try:
            cmap = plt.colormaps["tab10"]
        except AttributeError:
            cmap = plt.cm.get_cmap("tab10")

        bld_color_hex: dict[str, str] = {}
        for i, b in enumerate(self.buildings):
            rgba = cmap(i / max(len(self.buildings), 1))
            bld_color_hex[b] = "#{:02x}{:02x}{:02x}".format(
                int(rgba[0] * 255), int(rgba[1] * 255), int(rgba[2] * 255)
            )

        type_color_hex = {
            "Lecturehall":    "#e03131",
            "Entry":          "#2f9e44",
            "doorway":        "#1971c2",
            "doorwayElectric":"#1864ab",
            "link":           "#f08c00",
            "Landmark":       "#e8590c",
            "Globallandmark": "#c2255c",
            "Toilet":         "#0c8599",
            "Bus":            "#087f5b",
            "Parking":        "#495057",
            "Shop":           "#c2255c",
        }

        # ---- select nodes -------------------------------------------------
        def _keep_node(d: dict) -> bool:
            if building and d["building"] != building:
                return False
            if storey is not None and d["storey"] != storey:
                return False
            return True

        node_data = {
            k: d for k, d in self.graph.nodes(data=True)
            if _keep_node(d)
        }

        # ---- compute map centre -------------------------------------------
        lats = [d["geo_lat"] for d in node_data.values()
                if d.get("geo_lat") is not None]
        lons = [d["geo_lon"] for d in node_data.values()
                if d.get("geo_lon") is not None]

        if not lats:
            raise ValueError("No georeferenced nodes found.  "
                             "Check that pixelToWGS84 exists in the XML files.")

        center = [float(np.mean(lats)), float(np.mean(lons))]
        
        # Base map (we add TileLayers later)
        m = folium.Map(location=center, zoom_start=17, tiles=None)
        
        # Add multiple basemaps
        folium.TileLayer(
            "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
            attr="Esri",
            name="Satellite (Esri)",
            max_zoom=19,
        ).add_to(m)
        folium.TileLayer("OpenStreetMap", name="Standard OpenStreetMap (Classic)").add_to(m)
        folium.TileLayer("CartoDB positron", name="Light Map (CartoDB)").add_to(m)

        MeasureControl().add_to(m)
        MiniMap(toggle_display=True).add_to(m)

        # ---- one FeatureGroup per building --------------------------------
        fg_map: dict[str, "folium.FeatureGroup"] = {}
        for b in self.buildings:
            if building and b != building:
                continue
            fg = folium.FeatureGroup(
                name=self._graph_names.get(b, b), show=True
            )
            fg_map[b] = fg
            fg.add_to(m)

        # cross-building FeatureGroup
        fg_cross = folium.FeatureGroup(name="🔗 Cross-building edges", show=True)
        fg_cross.add_to(m)

        # ---- draw edges ---------------------------------------------------
        if show_edges:
            for u, v, ed in self.graph.edges(data=True):
                # Filter out logical/metadata edges (e.g. type 9 landmarks)
                etype = ed.get("type", 0)
                is_cross = ed.get("cross_building", False)
                if not is_cross and etype not in show_edge_types:
                    continue

                u_data = self.graph.nodes.get(u, {})
                v_data = self.graph.nodes.get(v, {})

                # both endpoints must be in selection and have coords
                if not (_keep_node(u_data) and _keep_node(v_data)):
                    continue
                ulat, ulon = u_data.get("geo_lat"), u_data.get("geo_lon")
                vlat, vlon = v_data.get("geo_lat"), v_data.get("geo_lon")
                if None in (ulat, ulon, vlat, vlon):
                    continue

                if is_cross:
                    color, weight, opacity, dash = "#e03131", 2.5, 0.9, "8 4"
                    fg_target = fg_cross
                else:
                    stairs_colors = {2: "#66a80f", 3: "#f08c00", 6: "#f08c00"}
                    color = stairs_colors.get(etype, "#74c0fc")
                    weight, opacity, dash = edge_weight, edge_opacity, None
                    fg_target = fg_map.get(u_data.get("building"), fg_cross)

                line_kw = dict(
                    locations=[[ulat, ulon], [vlat, vlon]],
                    color=color, weight=weight, opacity=opacity,
                )
                if dash:
                    line_kw["dash_array"] = dash
                folium.PolyLine(**line_kw).add_to(fg_target)

        # ---- draw nodes ---------------------------------------------------
        if show_nodes:
            for k, d in node_data.items():
                glat, glon = d.get("geo_lat"), d.get("geo_lon")
                if glat is None or glon is None:
                    continue
                ntype = d.get("type", "")
                if ntype in hide_types:
                    continue

                bld = d["building"]
                color = type_color_hex.get(ntype, bld_color_hex.get(bld, "#adb5bd"))
                fg_target = fg_map.get(bld, fg_cross)

                label = d.get("name") or ntype
                popup_html = (
                    f"<b>{label}</b><br>"
                    f"<small>"
                    f"Building: {bld}<br>"
                    f"Floor: {d.get('storey', '?'):+d}<br>"
                    f"Type: {ntype}<br>"
                    f"Key: {k}"
                    f"</small>"
                )
                if d.get("roomid"):
                    popup_html += f"<br>Room: {d['roomid']}"

                folium.CircleMarker(
                    location=[glat, glon],
                    radius=node_radius if ntype not in ("Lecturehall", "Bus", "Shop")
                           else node_radius + 3,
                    color="white",
                    weight=0.8,
                    fill=True,
                    fill_color=color,
                    fill_opacity=0.85,
                    popup=folium.Popup(popup_html, max_width=260),
                    tooltip=label if label != ntype else None,
                ).add_to(fg_target)

        folium.LayerControl(collapsed=False).add_to(m)
        return m
