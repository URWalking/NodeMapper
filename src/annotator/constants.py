from __future__ import annotations

HIDDEN_NODE_TYPES: frozenset[str] = frozenset({
    "doorway", "doorwayElectric", "doorwayAutomatic", "Revolvingdoor",
})

_TYPE_COLORS: dict[str, str] = {
    "branch":            "#adb5bd",
    "doorway":           "#4dabf7",
    "doorwayElectric":   "#339af0",
    "doorwayAutomatic":  "#74c0fc",
    "Revolvingdoor":     "#74c0fc",
    "Entry":             "#51cf66",
    "Lecturehall":       "#f03e3e",
    "link":              "#f59f00",
    "GpsLink":           "#cc5de8",
    "Landmark":          "#ffa94d",
    "Globallandmark":    "#ff6b6b",
    "Toilet":            "#74c0fc",
    "Areanode":          "#a9e34b",
    "Bus":               "#20c997",
    "Parking":           "#868e96",
    "Shop":              "#f783ac",
    "Stairs":            "#ffd43b",
    "Elevator":          "#da77f2",
    "Office":            "#e9ecef",
    "Ramp":              "#63e6be",
}

_DEFAULT_COLOR = "#dee2e6"
