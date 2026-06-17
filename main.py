#!/usr/bin/env python3
"""Start the Picture EXIF Compare app."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication, QMessageBox

from gui_app import PictureExifCompareApp
from picture_sorting import SortMode


def find_ui_file(ui_arg: str | None) -> Path:
    """Find the Qt Designer .ui file."""
    if ui_arg:
        return Path(ui_arg).expanduser().resolve()

    script_dir = Path(__file__).resolve().parent
    cwd = Path.cwd().resolve()
    candidates = [
        script_dir / "image_compare_layout.ui",
        script_dir / "image_compare_layout(1).ui",
        cwd / "image_compare_layout.ui",
        cwd / "image_compare_layout(1).ui",
    ]

    for candidate in candidates:
        if candidate.exists():
            return candidate

    return script_dir / "image_compare_layout.ui"


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Picture EXIF compare GUI")
    parser.add_argument(
        "paths",
        nargs="*",
        help="Optional image files or folders to load on startup.",
    )
    parser.add_argument(
        "--ui",
        help="Path to image_compare_layout.ui. If omitted, the script searches next to itself and in the current folder.",
    )
    parser.add_argument(
        "--sort",
        default=SortMode.NAME.value,
        choices=[mode.value for mode in SortMode],
        help="How to sort the picture list.",
    )
    parser.add_argument(
        "--include-without-metadata-date",
        "--include-without-metadata",
        dest="include_without_metadata_date",
        action="store_true",
        help="Load all supported image files, even when they do not contain an embedded metadata date.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    app = QApplication(sys.argv)

    try:
        ui_path = find_ui_file(args.ui)
        initial_paths = [Path(p) for p in args.paths]
        exif_app = PictureExifCompareApp(
            ui_path=ui_path,
            initial_paths=initial_paths,
            sort_mode=args.sort,
            require_metadata_date=not args.include_without_metadata_date,
        )
    except Exception as exc:
        QMessageBox.critical(None, "Could not start Picture Compare", str(exc))
        return 1

    exif_app.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
