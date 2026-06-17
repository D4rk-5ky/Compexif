"""Sorting rules for the picture list.

Change DEFAULT_SORT_MODE below to change how the list is ordered.
This file is intentionally only about sort order.
"""

from __future__ import annotations

import datetime as _dt
from enum import Enum
from pathlib import Path
from typing import Callable, Iterable

from image_metadata import get_best_embedded_datetime, get_picture_file_summary
from picture_list_columns import PictureColumn


class SortMode(str, Enum):
    NAME = "name"
    PATH = "path"
    MODIFIED_NEWEST = "modified-newest"
    MODIFIED_OLDEST = "modified-oldest"
    SIZE_BIGGEST = "size-biggest"
    SIZE_SMALLEST = "size-smallest"
    METADATA_DATE_NEWEST = "metadata-date-newest"
    METADATA_DATE_OLDEST = "metadata-date-oldest"
    # Backwards-compatible aliases from the earlier version.
    EXIF_DATE_NEWEST = "exif-date-newest"
    EXIF_DATE_OLDEST = "exif-date-oldest"


DEFAULT_SORT_MODE = SortMode.NAME


ColumnOrder = tuple[int, bool]
"""column, descending"""


def parse_sort_mode(value: str | SortMode | None) -> SortMode:
    """Convert text from CLI/config into a SortMode."""
    if isinstance(value, SortMode):
        return value
    if value is None or value == "":
        return DEFAULT_SORT_MODE

    try:
        return SortMode(value)
    except ValueError as exc:
        choices = ", ".join(mode.value for mode in SortMode)
        raise ValueError(f"Unknown sort mode: {value}. Choices: {choices}") from exc


def sort_picture_paths(paths: list[Path], mode: str | SortMode | None = None) -> list[Path]:
    """Return a new list of pictures sorted for display."""
    sort_mode = parse_sort_mode(mode)

    if sort_mode == SortMode.NAME:
        return sorted(paths, key=lambda p: (p.name.lower(), str(p.parent).lower()))

    if sort_mode == SortMode.PATH:
        return sorted(paths, key=lambda p: str(p).lower())

    if sort_mode == SortMode.MODIFIED_NEWEST:
        return sorted(paths, key=safe_mtime, reverse=True)

    if sort_mode == SortMode.MODIFIED_OLDEST:
        return sorted(paths, key=safe_mtime)

    if sort_mode == SortMode.SIZE_BIGGEST:
        return sorted(paths, key=safe_size, reverse=True)

    if sort_mode == SortMode.SIZE_SMALLEST:
        return sorted(paths, key=safe_size)

    if sort_mode in {SortMode.METADATA_DATE_NEWEST, SortMode.EXIF_DATE_NEWEST}:
        # Files without embedded dates are pushed to the bottom.
        return sorted(paths, key=metadata_sort_key_newest)

    if sort_mode in {SortMode.METADATA_DATE_OLDEST, SortMode.EXIF_DATE_OLDEST}:
        # Files without embedded dates are pushed to the bottom.
        return sorted(paths, key=metadata_sort_key_oldest)

    return sorted(paths, key=lambda p: str(p).lower())


def sort_mode_to_column_order(mode: str | SortMode | None) -> ColumnOrder:
    """Return the matching table column/order for an old command-line sort mode."""
    sort_mode = parse_sort_mode(mode)

    if sort_mode == SortMode.NAME:
        return int(PictureColumn.NAME), False
    if sort_mode == SortMode.PATH:
        return int(PictureColumn.FOLDER), False
    if sort_mode == SortMode.MODIFIED_NEWEST:
        return int(PictureColumn.FILE_DATE), True
    if sort_mode == SortMode.MODIFIED_OLDEST:
        return int(PictureColumn.FILE_DATE), False
    if sort_mode == SortMode.SIZE_BIGGEST:
        return int(PictureColumn.SIZE), True
    if sort_mode == SortMode.SIZE_SMALLEST:
        return int(PictureColumn.SIZE), False
    if sort_mode in {SortMode.METADATA_DATE_NEWEST, SortMode.EXIF_DATE_NEWEST}:
        return int(PictureColumn.METADATA_DATE), True
    if sort_mode in {SortMode.METADATA_DATE_OLDEST, SortMode.EXIF_DATE_OLDEST}:
        return int(PictureColumn.METADATA_DATE), False

    return int(PictureColumn.NAME), False


def sort_picture_paths_by_column(
    paths: list[Path],
    column: int | PictureColumn,
    descending: bool = False,
) -> list[Path]:
    """Sort paths using the same meaning as the visible table columns.

    This avoids normal text sorting mistakes. For example, file sizes are sorted
    by bytes, width/height by total pixels, and dates by real datetime values.
    """
    column = int(column)

    if column == int(PictureColumn.NAME):
        return sorted(
            paths,
            key=lambda p: (p.name.lower(), str(p.parent).lower(), str(p).lower()),
            reverse=descending,
        )

    if column == int(PictureColumn.WIDTH_HEIGHT):
        return sorted(paths, key=lambda p: dimension_sort_key(p, descending))

    if column == int(PictureColumn.SIZE):
        return sorted(paths, key=lambda p: number_sort_key(safe_size(p), descending, p))

    if column == int(PictureColumn.METADATA_DATE):
        return sorted(
            paths,
            key=lambda p: datetime_sort_key(get_best_embedded_datetime(p), descending, p),
        )

    if column == int(PictureColumn.FILE_DATE):
        return sorted(paths, key=lambda p: datetime_sort_key(safe_file_datetime(p), descending, p))

    if column == int(PictureColumn.FOLDER):
        return sorted(
            paths,
            key=lambda p: (str(p.parent).lower(), p.name.lower(), str(p).lower()),
            reverse=descending,
        )

    return sort_picture_paths(paths, SortMode.NAME)


def safe_mtime(path: Path) -> float:
    """Modification time, safe for missing/unreadable files."""
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def safe_file_datetime(path: Path) -> _dt.datetime | None:
    """File modification datetime, safe for missing/unreadable files."""
    try:
        return _dt.datetime.fromtimestamp(path.stat().st_mtime)
    except OSError:
        return None


def safe_size(path: Path) -> int:
    """File size, safe for missing/unreadable files."""
    try:
        return path.stat().st_size
    except OSError:
        return 0


def number_sort_key(value: int | float | None, descending: bool, path: Path) -> tuple[int, float, str]:
    """Sort numbers with missing/zero values last."""
    if value is None or value <= 0:
        return (1, 0.0, str(path).lower())
    number = float(value)
    return (0, -number if descending else number, str(path).lower())


def datetime_sort_key(
    value: _dt.datetime | None,
    descending: bool,
    path: Path,
) -> tuple[int, float, str]:
    """Sort datetimes with missing dates last."""
    if value is None:
        return (1, 0.0, str(path).lower())
    timestamp = value.timestamp()
    return (0, -timestamp if descending else timestamp, str(path).lower())


def dimension_sort_key(path: Path, descending: bool) -> tuple[int, int, int, int, str]:
    """Sort dimensions by total pixels, then width, then height."""
    summary = get_picture_file_summary(path)
    if summary.pixel_count <= 0:
        return (1, 0, 0, 0, str(path).lower())

    pixel_count = summary.pixel_count
    width = summary.width or 0
    height = summary.height or 0

    if descending:
        return (0, -pixel_count, -width, -height, str(path).lower())
    return (0, pixel_count, width, height, str(path).lower())


def metadata_sort_key_newest(path: Path) -> tuple[int, float, str]:
    """Sort newest embedded metadata date first; files without dates last."""
    metadata_date = get_best_embedded_datetime(path)
    if metadata_date is None:
        return (1, 0.0, str(path).lower())
    return (0, -metadata_date.timestamp(), str(path).lower())


def metadata_sort_key_oldest(path: Path) -> tuple[int, float, str]:
    """Sort oldest embedded metadata date first; files without dates last."""
    metadata_date = get_best_embedded_datetime(path)
    if metadata_date is None:
        return (1, 0.0, str(path).lower())
    return (0, metadata_date.timestamp(), str(path).lower())
