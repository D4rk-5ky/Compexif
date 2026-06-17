#!/usr/bin/env python3
"""Start the Picture EXIF Compare app."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

from gui_app import PictureExifCompareApp
from picture_sorting import SortMode



def resource_path(relative_path: str) -> Path:
    """Return a path that works both from source and from a PyInstaller bundle."""
    base_path = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base_path / relative_path


def find_icon_file() -> Path | None:
    """Find the application icon for normal runs and PyInstaller runs."""
    candidates = [
        resource_path("assets/Compexif_Exif_multi_size.ico"),
        resource_path("Compexif_Exif_multi_size.ico"),
        Path(__file__).resolve().parent / "assets" / "Compexif_Exif_multi_size.ico",
        Path.cwd().resolve() / "assets" / "Compexif_Exif_multi_size.ico",
        Path.cwd().resolve() / "Compexif_Exif_multi_size.ico",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None

def find_ui_file(ui_arg: str | None) -> Path:
    """Find the Qt Designer .ui file."""
    if ui_arg:
        return Path(ui_arg).expanduser().resolve()

    script_dir = Path(__file__).resolve().parent
    cwd = Path.cwd().resolve()
    candidates = [
        resource_path("image_compare_layout.ui"),
        resource_path("image_compare_layout(1).ui"),
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

    icon_path = find_icon_file()
    app_icon = QIcon(str(icon_path)) if icon_path is not None else QIcon()
    if not app_icon.isNull():
        app.setWindowIcon(app_icon)

    try:
        ui_path = find_ui_file(args.ui)
        initial_paths = [Path(p) for p in args.paths]
        exif_app = PictureExifCompareApp(
            ui_path=ui_path,
            initial_paths=initial_paths,
            sort_mode=args.sort,
            require_metadata_date=not args.include_without_metadata_date,
        )
        if not app_icon.isNull():
            exif_app.window.setWindowIcon(app_icon)
    except Exception as exc:
        QMessageBox.critical(None, "Could not start Picture Compare", str(exc))
        return 1

    exif_app.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
