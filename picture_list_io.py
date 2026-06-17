"""Save/load support for picture lists.

This module only reads and writes the list file format. Validation such as
"does the image still exist" and "does it still contain a metadata date" is
handled by the GUI so it can show progress and allow cancel/pause.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


LIST_SCHEMA_VERSION = 2
VALID_STATUSES = {"", "keep", "delete"}


@dataclass(frozen=True)
class PictureListEntry:
    """One saved row from the picture list."""

    path: Path
    status: str = ""


@dataclass(frozen=True)
class PictureListDocument:
    """Loaded picture list document."""

    entries: list[PictureListEntry]
    locked_path: Path | None = None
    sort_mode: str | None = None
    require_metadata_date: bool = True
    source_file: Path | None = None


def clean_status(status: object) -> str:
    """Normalize saved status text."""
    value = str(status or "").strip().lower()
    if value in VALID_STATUSES:
        return value
    return ""


def write_picture_list(
    file_path: Path,
    picture_paths: Iterable[Path],
    status_by_path: dict[Path, str] | None = None,
    locked_path: Path | None = None,
    sort_mode: str | None = None,
    require_metadata_date: bool = True,
) -> None:
    """Write the current picture list to a JSON file."""
    status_by_path = status_by_path or {}
    picture_paths = [Path(path).expanduser().resolve() for path in picture_paths]

    data = {
        "schema_version": LIST_SCHEMA_VERSION,
        "app": "Picture Metadata Compare",
        "saved_at": dt.datetime.now().isoformat(timespec="seconds"),
        "require_metadata_date": bool(require_metadata_date),
        "sort_mode": str(sort_mode) if sort_mode is not None else None,
        "locked_path": str(Path(locked_path).expanduser().resolve()) if locked_path else None,
        "files": [
            {
                "path": str(path),
                # status is kept for backward compatibility and for the
                # current app state: "", "keep", or "delete".
                "status": clean_status(status_by_path.get(path, "")),
                # Explicit booleans make the JSON easier to read/edit by hand.
                "marked_for_deletion": clean_status(status_by_path.get(path, "")) == "delete",
                "marked_to_keep": clean_status(status_by_path.get(path, "")) == "keep",
            }
            for path in picture_paths
        ],
    }

    file_path = file_path.expanduser()
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def read_picture_list(file_path: Path) -> PictureListDocument:
    """Read a saved picture list JSON file.

    The preferred format is the dict written by ``write_picture_list``. For
    convenience, this also accepts a plain JSON list of path strings.
    """
    file_path = file_path.expanduser().resolve()
    data = json.loads(file_path.read_text(encoding="utf-8"))

    if isinstance(data, list):
        entries = [PictureListEntry(_path_from_saved_value(item, file_path), "") for item in data]
        return PictureListDocument(entries=entries, source_file=file_path)

    if not isinstance(data, dict):
        raise ValueError("Picture list must be a JSON object or a JSON list of paths.")

    files = data.get("files", [])
    if not isinstance(files, list):
        raise ValueError("Picture list JSON field 'files' must be a list.")

    entries: list[PictureListEntry] = []
    for item in files:
        if isinstance(item, str):
            entries.append(PictureListEntry(_path_from_saved_value(item, file_path), ""))
            continue

        if not isinstance(item, dict):
            continue

        raw_path = item.get("path")
        if not raw_path:
            continue

        status = clean_status(item.get("status", ""))
        # Newer list files store deletion/keep marks explicitly. Older files
        # only have "status", so keep supporting both formats.
        if item.get("marked_for_deletion") is True:
            status = "delete"
        elif item.get("marked_to_keep") is True:
            status = "keep"

        entries.append(
            PictureListEntry(
                path=_path_from_saved_value(raw_path, file_path),
                status=status,
            )
        )

    locked_path = data.get("locked_path")
    return PictureListDocument(
        entries=entries,
        locked_path=_path_from_saved_value(locked_path, file_path) if locked_path else None,
        sort_mode=data.get("sort_mode"),
        require_metadata_date=bool(data.get("require_metadata_date", True)),
        source_file=file_path,
    )


def _path_from_saved_value(value: object, list_file_path: Path) -> Path:
    """Build a Path from a saved string.

    Relative paths are treated as relative to the saved list file.
    """
    path = Path(str(value)).expanduser()
    if not path.is_absolute():
        path = list_file_path.parent / path
    return path.resolve()
