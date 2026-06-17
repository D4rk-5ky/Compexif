"""About dialog for the picture metadata compare app.

Keep the app/about text here so the main GUI controller stays focused on
wiring buttons and picture behavior.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)


APP_NAME = "Picture Metadata Compare"
APP_VERSION = "0.1"
APP_AUTHOR = "D4rk-5ky"
APP_DESCRIPTION = (
    "An app to find and compare similar images by embedded metadata dates, "
    "EXIF/XMP/IPTC/text metadata, and previewed file details."
)


# This is intentionally simple to edit later. Add/remove rows here when you
# want more information in the About table.
ABOUT_ROWS = [
    ("App", APP_NAME),
    ("Version", APP_VERSION),
    ("Purpose", "Find similar images by metadata data and embedded metadata dates."),
    ("Created by", APP_AUTHOR),
    ("Metadata used", "EXIF, XMP, IPTC, PNG/WebP/JPEG text/comment data"),
    ("Normal file info", "Shown for comparison, but not used as metadata qualification"),
    ("Toolkit", "Python 3, PySide6, Pillow"),
    ("License", "Add license text here later"),
]


class AboutDialog(QDialog):
    """Small editable-style About dialog with a two-column information table."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"About {APP_NAME}")
        self.resize(620, 360)

        layout = QVBoxLayout(self)

        title = QLabel(f"<h2>{APP_NAME}</h2>")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        description = QLabel(APP_DESCRIPTION)
        description.setWordWrap(True)
        description.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(description)

        self.table = QTableWidget(len(ABOUT_ROWS), 2, self)
        self.table.setHorizontalHeaderLabels(["Field", "Info"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)

        for row, (field, value) in enumerate(ABOUT_ROWS):
            field_item = QTableWidgetItem(field)
            value_item = QTableWidgetItem(value)
            field_item.setFlags(field_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            value_item.setFlags(value_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(row, 0, field_item)
            self.table.setItem(row, 1, value_item)

        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok, self)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)


def show_about_dialog(parent=None) -> None:
    """Open the About dialog."""
    dialog = AboutDialog(parent)
    dialog.exec()
