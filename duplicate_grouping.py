"""Group pictures that share the same embedded metadata date.

This module is only about duplicate detection/grouping. The rule is simple:
images are possible duplicates when their best embedded metadata date is the
same. Filesystem dates are not used here.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from image_metadata import get_picture_file_summary


@dataclass(frozen=True)
class MetadataDateGroup:
    """A group of image paths that share one embedded metadata date."""

    key: str
    display_date: str
    paths: list[Path]

    @property
    def is_duplicate_group(self) -> bool:
        return len(self.paths) >= 2


def metadata_date_group_key(path: Path) -> str:
    """Return a stable grouping key for the embedded metadata date.

    The app filters for images with metadata dates before they reach the list,
    but this still returns an empty string for safety if a file later becomes
    unreadable or loses its metadata.
    """
    summary = get_picture_file_summary(path)
    if summary.metadata_datetime is None:
        return ""

    # Most camera metadata is only precise to seconds. Normalizing to seconds
    # keeps identical camera dates together even if one parser provided
    # microseconds and another did not.
    return summary.metadata_datetime.replace(microsecond=0).isoformat(sep=" ")


def metadata_date_display_text(path: Path) -> str:
    """Return the text shown in the Metadata date column/header."""
    return get_picture_file_summary(path).metadata_date_text


def build_metadata_date_groups(paths: Iterable[Path]) -> tuple[list[MetadataDateGroup], list[Path]]:
    """Split paths into duplicate metadata-date groups and unique paths.

    The order of groups follows the first occurrence in *paths*. This means the
    caller can sort by any column first, and the duplicate groups will still stay
    together while preserving that chosen sort order as much as possible.
    """
    grouped: "OrderedDict[str, list[Path]]" = OrderedDict()
    display_dates: dict[str, str] = {}
    no_date_paths: list[Path] = []

    for path in paths:
        key = metadata_date_group_key(path)
        if not key:
            no_date_paths.append(path)
            continue
        grouped.setdefault(key, []).append(path)
        display_dates.setdefault(key, metadata_date_display_text(path))

    duplicate_groups: list[MetadataDateGroup] = []
    unique_paths: list[Path] = list(no_date_paths)

    for key, group_paths in grouped.items():
        if len(group_paths) >= 2:
            duplicate_groups.append(
                MetadataDateGroup(
                    key=key,
                    display_date=display_dates.get(key, key),
                    paths=group_paths,
                )
            )
        else:
            unique_paths.extend(group_paths)

    return duplicate_groups, unique_paths


def sanitize_delete_statuses_for_duplicate_groups(
    paths: Iterable[Path],
    statuses: dict[Path, str],
) -> int:
    """Make sure no duplicate group has every item marked for deletion.

    Mutates *statuses* in place and returns how many groups were corrected.
    If a saved list or older session marked every file in a group for deletion,
    the first file in that group is changed to "keep".
    """
    duplicate_groups, _unique_paths = build_metadata_date_groups(paths)
    corrected = 0

    for group in duplicate_groups:
        if not group.paths:
            continue
        if all(statuses.get(path) == "delete" for path in group.paths):
            statuses[group.paths[0]] = "keep"
            corrected += 1

    return corrected


def duplicate_group_delete_count(paths: Iterable[Path], statuses: dict[Path, str]) -> int:
    """Count how many paths in a group are marked for deletion."""
    return sum(1 for path in paths if statuses.get(path) == "delete")
