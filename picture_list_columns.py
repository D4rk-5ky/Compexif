"""Column setup and row text for the picture list/table.

This file is only about how pictures are shown in the list columns.
Sorting remains in picture_sorting.py.
"""

from __future__ import annotations

from enum import IntEnum
from pathlib import Path

from image_metadata import get_picture_file_summary


class PictureColumn(IntEnum):
    """Column numbers for the resizable picture list."""

    NAME = 0
    WIDTH_HEIGHT = 1
    SIZE = 2
    METADATA_DATE = 3
    FILE_DATE = 4
    FOLDER = 5


HEADERS = [
    "Name",
    "Width x Height",
    "Size",
    "Metadata date",
    "File date",
    "Folder",
]

# Good starting widths. The user can still drag them bigger/smaller in the GUI.
DEFAULT_COLUMN_WIDTHS = {
    PictureColumn.NAME: 240,
    PictureColumn.WIDTH_HEIGHT: 110,
    PictureColumn.SIZE: 90,
    PictureColumn.METADATA_DATE: 160,
    PictureColumn.FILE_DATE: 160,
    PictureColumn.FOLDER: 280,
}


STATUS_PREFIX = {
    "": "   ",
    "keep": "✅ ",
    "delete": "🗑️ ",
}


def row_values_for_path(path: Path, status: str = "") -> list[str]:
    """Return the visible table values for one picture path."""
    summary = get_picture_file_summary(path)
    return [
        f"{STATUS_PREFIX.get(status, '   ')}{summary.name}",
        summary.width_height,
        summary.size_text,
        summary.metadata_date_text,
        summary.file_date_text,
        summary.folder,
    ]
