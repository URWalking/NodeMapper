from __future__ import annotations

import io
import os

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QImage, QPixmap
from PyQt5.QtWidgets import QLabel, QSizePolicy, QVBoxLayout, QWidget

from annotator.utils.exif import extract_timestamp


def _info_label(prefix: str, value: str) -> QLabel:
    lbl = QLabel(f"{prefix}  {value}")
    lbl.setWordWrap(True)
    lbl.setStyleSheet("color: #ccc; font-size: 12px;")
    return lbl


class PhotoPanel(QWidget):
    """Displays the current photo with timestamp and node assignment."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._timestamp: str | None = None
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        self.lbl_image = QLabel()
        self.lbl_image.setAlignment(Qt.AlignCenter)
        self.lbl_image.setMinimumHeight(200)
        self.lbl_image.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.lbl_image.setStyleSheet("background: #1e1e1e; border-radius: 4px;")
        layout.addWidget(self.lbl_image, stretch=1)

        self.lbl_filename = QLabel("—")
        self.lbl_filename.setAlignment(Qt.AlignCenter)
        self.lbl_filename.setWordWrap(True)
        font = self.lbl_filename.font()
        font.setBold(True)
        self.lbl_filename.setFont(font)
        layout.addWidget(self.lbl_filename)

        self.lbl_timestamp = _info_label("Timestamp:", "—")
        layout.addWidget(self.lbl_timestamp)

        sep = QWidget()
        sep.setFixedHeight(1)
        sep.setStyleSheet("background: #444;")
        layout.addWidget(sep)

        self.lbl_node_key  = _info_label("Node:",     "not assigned")
        self.lbl_node_name = _info_label("Name:",     "—")
        self.lbl_node_type = _info_label("Type:",     "—")
        self.lbl_building  = _info_label("Building:", "—")
        self.lbl_storey    = _info_label("Floor:",    "—")
        for w in (self.lbl_node_key, self.lbl_node_name, self.lbl_node_type,
                  self.lbl_building, self.lbl_storey):
            layout.addWidget(w)

        layout.addStretch()

    def load_photo(self, image_path: str) -> str | None:
        pixmap = self._load_pixmap_with_orientation(image_path)
        if not pixmap.isNull():
            scaled = pixmap.scaled(
                self.lbl_image.width() or 340,
                self.lbl_image.height() or 300,
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation,
            )
            self.lbl_image.setPixmap(scaled)
        else:
            self.lbl_image.setText("Image could not be loaded")

        self.lbl_filename.setText(os.path.basename(image_path))

        ts = extract_timestamp(image_path)
        self._timestamp = ts
        if ts:
            self.lbl_timestamp.setText(f"Timestamp:  {ts.replace('T', '  ')}")
            self.lbl_timestamp.setStyleSheet("color: #a9e34b;")
        else:
            self.lbl_timestamp.setText("Timestamp:  no EXIF")
            self.lbl_timestamp.setStyleSheet("color: #aaa;")

        return ts

    @staticmethod
    def _load_pixmap_with_orientation(image_path: str) -> QPixmap:
        """Load image and apply EXIF orientation so iPhone photos display correctly."""
        try:
            from PIL import Image, ImageOps
            with Image.open(image_path) as pil_img:
                pil_img = ImageOps.exif_transpose(pil_img)
                pil_img = pil_img.convert("RGB")
                buf = io.BytesIO()
                pil_img.save(buf, format="JPEG", quality=95)
            qimage = QImage.fromData(buf.getvalue())
            return QPixmap.fromImage(qimage)
        except Exception:
            return QPixmap(image_path)

    def show_assignment(
        self,
        node_key: str,
        node_name: str,
        node_type: str,
        building: str,
        storey: int,
    ) -> None:
        self.lbl_node_key.setText(f"Node:  {node_key}")
        self.lbl_node_key.setStyleSheet("color: #ffe066; font-weight: bold;")
        self.lbl_node_name.setText(f"Name:  {node_name or '—'}")
        self.lbl_node_type.setText(f"Type:  {node_type or '—'}")
        self.lbl_building.setText(f"Building:  {building}")
        self.lbl_storey.setText(f"Floor (storey):  {storey:+d}")

    def clear_assignment(self) -> None:
        self.lbl_node_key.setText("Node:  not assigned")
        self.lbl_node_key.setStyleSheet("color: #aaa;")
        for lbl in (self.lbl_node_name, self.lbl_node_type,
                    self.lbl_building, self.lbl_storey):
            lbl.setText(lbl.text().split(":")[0] + ":  —")

    @property
    def current_timestamp(self) -> str | None:
        return self._timestamp
