from __future__ import annotations

import os
import sys
from pathlib import Path

from PyQt5.QtGui import QColor, QPalette
from PyQt5.QtWidgets import QApplication

from annotator.windows.main_window import MainWindow

_HERE = Path(__file__).parent.parent.parent  # Projekt-Root (src/annotator/app.py → root)


def main() -> None:
    # QWebEngine muss vor QApplication initialisiert werden
    os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu")

    app = QApplication(sys.argv)
    app.setApplicationName("URWalking Annotator")
    app.setStyle("Fusion")

    palette = app.palette()
    palette.setColor(QPalette.Window,          QColor("#2b2d30"))
    palette.setColor(QPalette.WindowText,      QColor("#cdd0d4"))
    palette.setColor(QPalette.Base,            QColor("#1e1e1e"))
    palette.setColor(QPalette.AlternateBase,   QColor("#2b2d30"))
    palette.setColor(QPalette.ToolTipBase,     QColor("#3c3f41"))
    palette.setColor(QPalette.ToolTipText,     QColor("#cdd0d4"))
    palette.setColor(QPalette.Text,            QColor("#cdd0d4"))
    palette.setColor(QPalette.Button,          QColor("#3c3f41"))
    palette.setColor(QPalette.ButtonText,      QColor("#cdd0d4"))
    palette.setColor(QPalette.BrightText,      QColor("#ffffff"))
    palette.setColor(QPalette.Highlight,       QColor("#2f6ea5"))
    palette.setColor(QPalette.HighlightedText, QColor("#ffffff"))
    app.setPalette(palette)

    DATA_DIR    = str(_HERE / "data" / "university")
    ANNOTATIONS = str(_HERE / "annotations.json")

    window = MainWindow(data_dir=DATA_DIR, annotations_path=ANNOTATIONS)
    window.show()
    sys.exit(app.exec_())
