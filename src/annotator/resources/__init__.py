from __future__ import annotations

import json
from pathlib import Path

from annotator.constants import _DEFAULT_COLOR, _TYPE_COLORS


def load_map_html() -> str:
    """Lädt das Leaflet-HTML-Template und injiziert die Farb-Konstanten."""
    template = (Path(__file__).parent / "map.html").read_text(encoding="utf-8")
    return (
        template
        .replace('"__TYPE_COLORS__"', json.dumps(_TYPE_COLORS))
        .replace('"__DEFAULT_COLOR__"', f'"{_DEFAULT_COLOR}"')
    )
