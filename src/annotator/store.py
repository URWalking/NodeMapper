from __future__ import annotations

import json
import os
from pathlib import Path

import pandas as pd


class AnnotationStore:
    """Verwaltet Annotationen: Bild → Knoten-Zuordnung."""

    def __init__(self, json_path: str) -> None:
        self._path = Path(json_path)
        self._data: dict[str, dict] = {}
        self._dirty = False

    def load(self) -> None:
        if self._path.exists():
            try:
                records = json.loads(self._path.read_text(encoding="utf-8"))
                self._data = {r["image_path"]: r for r in records}
            except Exception:
                self._data = {}
        self._dirty = False

    def save(self) -> None:
        records = list(self._data.values())
        self._path.write_text(
            json.dumps(records, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        self._dirty = False

    def export_csv(self, csv_path: str) -> None:
        pd.DataFrame(list(self._data.values())).to_csv(csv_path, index=False)

    def annotate(
        self,
        image_path: str,
        node_row: pd.Series,
        timestamp: str | None,
    ) -> None:
        def _safe_float(val) -> float | None:
            try:
                f = float(val)
                return None if pd.isna(f) else f
            except Exception:
                return None

        self._data[image_path] = {
            "image_filename": os.path.basename(image_path),
            "image_path":     image_path,
            "timestamp":      timestamp,
            "node_key":       node_row["node_key"],
            "building":       node_row["building"],
            "level_id":       int(node_row["level_id"]),
            "storey":         int(node_row["storey"]),
            "node_id":        int(node_row["node_id"]),
            "node_type":      node_row.get("type", ""),
            "node_name":      node_row.get("name", ""),
            "pixel_x":        _safe_float(node_row.get("x")),
            "pixel_y":        _safe_float(node_row.get("y")),
            "geo_lat":        _safe_float(node_row.get("geo_lat")),
            "geo_lon":        _safe_float(node_row.get("geo_lon")),
        }
        self._dirty = True

    def get(self, image_path: str) -> dict | None:
        return self._data.get(image_path)

    def clear(self, image_path: str) -> None:
        if image_path in self._data:
            del self._data[image_path]
            self._dirty = True

    @property
    def annotated_keys(self) -> set[str]:
        return set(self._data.keys())

    @property
    def is_dirty(self) -> bool:
        return self._dirty
