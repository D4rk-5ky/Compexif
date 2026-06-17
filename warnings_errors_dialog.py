"""Warnings/errors window for the Picture Compare app."""

from __future__ import annotations

from PySide6.QtGui import QGuiApplication, QTextCursor
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


class WarningsErrorsDialog(QDialog):
    """Copyable window showing metadata/image read warnings and errors."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Warnings / errors")
        self.resize(900, 500)

        self.label_summary = QLabel("No warnings/errors yet.", self)

        self.text_errors = QTextEdit(self)
        self.text_errors.setReadOnly(True)
        self.text_errors.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        self.text_errors.setPlaceholderText(
            "Warnings and errors from metadata/image reading will appear here."
        )

        self.btn_copy = QPushButton("Copy all", self)
        self.btn_clear_selection = QPushButton("Deselect text", self)
        self.btn_close = QPushButton("Close", self)

        button_row = QHBoxLayout()
        button_row.addWidget(self.btn_copy)
        button_row.addWidget(self.btn_clear_selection)
        button_row.addStretch(1)
        button_row.addWidget(self.btn_close)

        layout = QVBoxLayout(self)
        layout.addWidget(self.label_summary)
        layout.addWidget(self.text_errors, 1)
        layout.addLayout(button_row)

        self.btn_copy.clicked.connect(self.copy_all)
        self.btn_clear_selection.clicked.connect(lambda: self.text_errors.moveCursor(QTextCursor.MoveOperation.End))
        self.btn_close.clicked.connect(self.close)

    def set_messages(self, messages: list[str]) -> None:
        """Replace the visible list with the supplied messages."""
        if not messages:
            self.label_summary.setText("No warnings/errors yet.")
            self.text_errors.setPlainText("")
            return

        self.label_summary.setText(f"{len(messages):,} warning/error message(s).")
        self.text_errors.setPlainText("\n".join(messages))
        self.text_errors.moveCursor(QTextCursor.MoveOperation.End)

    def append_messages(self, messages: list[str]) -> None:
        """Append new messages without closing/reopening the window."""
        if not messages:
            return
        cursor = self.text_errors.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        if self.text_errors.toPlainText():
            cursor.insertText("\n")
        cursor.insertText("\n".join(messages))
        self.text_errors.setTextCursor(cursor)

    def copy_all(self) -> None:
        """Copy the full warning/error list to the clipboard."""
        QGuiApplication.clipboard().setText(self.text_errors.toPlainText())
