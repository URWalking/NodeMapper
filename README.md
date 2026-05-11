# Location Annotation Tool

![Python](https://img.shields.io/badge/python-3.12-blue)
![License](https://img.shields.io/badge/license-MIT-green)
![PyQt5](https://img.shields.io/badge/GUI-PyQt5-informational)

A desktop GUI for manually annotating indoor location photos with nodes from a university navigation graph. Part of the **URWalking** project.

![Screenshot](images/screenshot_tool.png)

---

## Overview

This tool is designed for researchers and students who need to create labeled training datasets for indoor navigation systems. Given a folder of photos taken at known locations inside a university building, users can interactively assign each photo to its corresponding node in the floor-plan graph by clicking on an interactive OpenStreetMap-based map.

Annotations are saved as JSON and can be exported as CSV for downstream model training.

---

## Features

- **Interactive Leaflet map** with OpenStreetMap tiles and zoomable node markers
- **Floor-plan overlay** — load building floor plans as georeferenced image overlays
- **Per-floor filtering** — switch between buildings, floors, and node types
- **EXIF timestamp extraction** from photo metadata
- **Route builder** — compose and save node sequences as JSON routes
- **Keyboard navigation** for fast photo-by-photo annotation
- **CSV export** of all annotations

---

## Requirements

- Python 3.12+
- [`uv`](https://github.com/astral-sh/uv) (recommended) or `pip`

---

## Installation

```bash
# Clone the repository
git clone <repo-url>
cd Location-Annotation-Tool

# Create and activate a virtual environment
uv venv
source .venv/bin/activate

# Install dependencies from the pinned lockfile
uv pip install -r requirements.txt

# Install the project itself
uv pip install -e .
```

### Run

```bash
python main.py
# or without activating the venv:
uv run python main.py
```

### Update dependencies

After editing direct dependencies in `pyproject.toml`, regenerate the lockfile:

```bash
uv pip compile pyproject.toml -o requirements.txt
```

---

## Usage

1. **Open a photo folder** via `File → Open Photo Folder` or `Ctrl+O`
2. **Select a building and floor** in the top control bar
3. **Click a node** on the map to assign the current photo to that location
4. Optionally **load a floor-plan folder** to display georeferenced overlays
5. **Export annotations** as CSV via `Ctrl+S`

### Keyboard Shortcuts

| Action | Shortcut |
|--------|----------|
| Next photo | `→` / `D` |
| Previous photo | `←` / `A` |
| Clear assignment | `Del` |
| Open photo folder | `Ctrl+O` |
| Export CSV | `Ctrl+S` / `Ctrl+E` |

### Route Builder

Enable **Route Mode** to record sequences of nodes (e.g. walking paths). Routes can be saved as JSON and reloaded in later sessions.

---

## Project Structure

```
Location-Annotation-Tool/
├── main.py                     # Entry point
├── pyproject.toml              # Project metadata and dependencies
├── requirements.txt            # Pinned dependency lockfile
├── .python-version             # Python 3.12
├── images/                     # Screenshots and assets for documentation
│
└── src/
    ├── annotator/              # GUI application package
    │   ├── app.py              # Application entry point and dark theme setup
    │   ├── constants.py        # Node type color palette and filter constants
    │   ├── store.py            # AnnotationStore — JSON/CSV persistence
    │   ├── resources/
    │   │   ├── __init__.py     # load_map_html() — injects runtime constants
    │   │   └── map.html        # Leaflet HTML/JS map template
    │   ├── utils/
    │   │   └── exif.py         # EXIF timestamp extraction
    │   ├── widgets/
    │   │   ├── map_widget.py   # MapBridge (JS ↔ Python) and MapWidget
    │   │   └── photo_panel.py  # Photo preview panel with metadata display
    │   └── windows/
    │       └── main_window.py  # MainWindow — UI layout and event handling
    │
    ├── graph/                  # Navigation graph data model
    │   └── university_graph.py # UniversityGraph — XML parser, node/edge DataFrames
    │
    └── viz/                    # Visualization and analysis (Jupyter-compatible)
        └── urwalking_viz.py    # BuildingGraph, UniversityMap — Plotly, Folium, pydeck
```

---

## Dependencies

| Package | Purpose |
|---------|---------|
| PyQt5 + PyQtWebEngine | Desktop GUI and embedded Leaflet map |
| pandas | Node and edge DataFrames |
| Pillow | EXIF extraction and image loading |
| networkx | Graph data structure and shortest-path queries |
| matplotlib | Basic floor-plan plots |
| folium | Interactive OSM maps (analysis) |
| plotly | 2D/3D visualizations (analysis) |
| pydeck | 3D maps over OSM (analysis) |

---

## License

MIT — see [LICENSE](LICENSE) for details.
