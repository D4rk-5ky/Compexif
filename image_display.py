"""Image preview display helpers for Qt labels."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageOps
from PIL.ImageQt import ImageQt
from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QLabel


def clear_label(label: QLabel, text: str) -> None:
    """Clear an image preview label and show placeholder text."""
    label.clear()
    label.setText(text)
    label.setAlignment(Qt.AlignmentFlag.AlignCenter)


def show_picture_on_label(path: Path, label: QLabel) -> None:
    """Load a picture and display a scaled preview on a QLabel."""
    try:
        pixmap = make_preview_pixmap(path, label.width(), label.height())
    except Exception as exc:
        clear_label(label, f"Could not load image:\n{path.name}\n{exc}")
        return

    if pixmap.isNull():
        clear_label(label, f"Could not load image:\n{path.name}")
        return

    label.setPixmap(pixmap)
    label.setAlignment(Qt.AlignmentFlag.AlignCenter)


def make_preview_pixmap(path: Path, max_width: int, max_height: int) -> QPixmap:
    """Create a QPixmap preview while respecting EXIF orientation."""
    max_width = max(100, max_width - 8)
    max_height = max(100, max_height - 8)

    with Image.open(path) as image:
        image = ImageOps.exif_transpose(image)
        image.thumbnail((max_width, max_height), Image.Resampling.LANCZOS)
        if image.mode not in {"RGB", "RGBA"}:
            image = image.convert("RGBA")
        qimage = ImageQt(image)
        return QPixmap.fromImage(qimage)
