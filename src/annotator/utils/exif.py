from __future__ import annotations

from datetime import datetime


def extract_timestamp(image_path: str) -> str | None:
    """Extrahiert den Aufnahme-Zeitstempel aus dem EXIF der Bilddatei."""
    try:
        from PIL import Image  # type: ignore
        with Image.open(image_path) as img:
            exif = None
            try:
                exif = img._getexif()  # type: ignore[attr-defined]
            except AttributeError:
                pass
            if exif is None:
                try:
                    exif = dict(img.getexif())
                except Exception:
                    pass
            if not exif:
                return None
            for tag_id in (36867, 36868, 306):
                value = exif.get(tag_id)
                if value:
                    dt = datetime.strptime(value, "%Y:%m:%d %H:%M:%S")
                    return dt.isoformat()
    except Exception:
        pass
    return None
