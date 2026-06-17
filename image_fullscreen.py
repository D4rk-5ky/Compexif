"""Fullscreen/large-image preview helpers.

This module adds a small semi-transparent overlay button to preview labels and
opens a resizable window for comparing the locked/default image with the
currently selected/checked image.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, QEvent, Qt, QTimer
from PySide6.QtGui import QKeyEvent, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QDialog,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
)

from image_display import clear_label, make_preview_pixmap


PathGetter = Callable[[], Path | None]
StatusGetter = Callable[[Path], str]
StatusSetter = Callable[[Path, str], bool]


class PreviewFullscreenButton(QObject):
    """A 50% opacity fullscreen button placed inside a QLabel preview frame."""

    def __init__(self, label: QLabel, callback: Callable[[], None], tooltip: str) -> None:
        super().__init__(label)
        self.label = label
        self.button = QPushButton("⛶", label)
        self.button.setObjectName(f"{label.objectName()}FullscreenButton")
        self.button.setToolTip(tooltip)
        self.button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.button.setFixedSize(34, 34)
        self.button.clicked.connect(callback)

        opacity = QGraphicsOpacityEffect(self.button)
        opacity.setOpacity(0.50)
        self.button.setGraphicsEffect(opacity)

        self.button.setStyleSheet(
            """
            QPushButton {
                color: white;
                background-color: rgba(0, 0, 0, 170);
                border: 1px solid rgba(255, 255, 255, 150);
                border-radius: 6px;
                font-size: 20px;
                font-weight: bold;
                padding-bottom: 2px;
            }
            QPushButton:hover {
                background-color: rgba(0, 0, 0, 220);
                border: 1px solid rgba(255, 255, 255, 220);
            }
            """
        )

        self.label.installEventFilter(self)
        self.button.raise_()
        self.reposition()
        self.button.show()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self.label and event.type() in {
            QEvent.Type.Resize,
            QEvent.Type.Show,
            QEvent.Type.Paint,
        }:
            self.reposition()
        return False

    def reposition(self) -> None:
        """Keep the button at the bottom-right corner inside the preview label."""
        margin = 8
        x = max(margin, self.label.width() - self.button.width() - margin)
        y = max(margin, self.label.height() - self.button.height() - margin)
        self.button.move(x, y)
        self.button.raise_()


class FullscreenImageWindow(QDialog):
    """Resizable large-image window for locked/default and selected images."""

    ROLE_LOCKED = "locked"
    ROLE_SELECTED = "selected"

    ROLE_TITLES = {
        ROLE_LOCKED: "Default image",
        ROLE_SELECTED: "Checked image",
    }

    def __init__(
        self,
        parent,
        locked_path_getter: PathGetter,
        selected_path_getter: PathGetter,
        start_role: str,
        status_getter: StatusGetter | None = None,
        status_setter: StatusSetter | None = None,
    ) -> None:
        super().__init__(parent)
        self.get_locked_path = locked_path_getter
        self.get_selected_path = selected_path_getter
        self.get_status = status_getter or (lambda _path: "")
        self.set_status = status_setter or (lambda _path, _status: False)
        self.active_role = start_role if start_role in self.ROLE_TITLES else self.ROLE_SELECTED
        # The action buttons must always affect the image that is actually
        # visible in this window.  Keep a direct copy of the last displayed
        # path instead of re-deriving it from the role later.  This avoids
        # accidental keep/delete changes on the default image after switching
        # to the checked image, or the other way around.
        self.displayed_path: Path | None = None
        self.displayed_role: str = self.active_role

        self.setWindowTitle("Fullscreen image view")
        self.setWindowFlag(Qt.WindowType.Window, True)
        self.setModal(False)
        self.resize_timer = QTimer(self)
        self.resize_timer.setSingleShot(True)
        self.resize_timer.timeout.connect(self.refresh_image)

        self.title_label = QLabel("", self)
        self.title_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.title_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

        self.btn_show_locked = QPushButton("Default image", self)
        self.btn_show_selected = QPushButton("Checked image", self)
        self.btn_show_locked.setCheckable(True)
        self.btn_show_selected.setCheckable(True)
        self.role_button_group = QButtonGroup(self)
        self.role_button_group.setExclusive(True)
        self.role_button_group.addButton(self.btn_show_locked)
        self.role_button_group.addButton(self.btn_show_selected)
        self.btn_close = QPushButton("Close", self)
        # Explicit ``_checked`` argument keeps PySide's checkable-button signal
        # from accidentally confusing the role-switch callback.
        self.btn_show_locked.clicked.connect(
            lambda _checked=False: self.set_active_role(self.ROLE_LOCKED)
        )
        self.btn_show_selected.clicked.connect(
            lambda _checked=False: self.set_active_role(self.ROLE_SELECTED)
        )
        self.btn_close.clicked.connect(self.close)

        switch_toolbar = QHBoxLayout()
        switch_toolbar.addWidget(self.title_label, 1)
        switch_toolbar.addWidget(self.btn_show_locked)
        switch_toolbar.addWidget(self.btn_show_selected)
        switch_toolbar.addWidget(self.btn_close)

        self.status_label = QLabel("", self)
        self.status_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.status_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.btn_keep = QPushButton("Keep picture", self)
        self.btn_mark_delete = QPushButton("Mark for deletion", self)
        self.btn_clear_mark = QPushButton("Clear mark", self)
        self.btn_keep.clicked.connect(lambda: self.mark_active_image("keep"))
        self.btn_mark_delete.clicked.connect(lambda: self.mark_active_image("delete"))
        self.btn_clear_mark.clicked.connect(lambda: self.mark_active_image(""))

        mark_toolbar = QHBoxLayout()
        mark_toolbar.addWidget(self.status_label, 1)
        mark_toolbar.addWidget(self.btn_keep)
        mark_toolbar.addWidget(self.btn_mark_delete)
        mark_toolbar.addWidget(self.btn_clear_mark)

        self.image_label = QLabel("", self)
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_label.setMinimumSize(480, 360)
        self.image_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.image_label.setStyleSheet(
            "QLabel { background-color: #111; color: #ddd; border: 1px solid #333; }"
        )

        layout = QVBoxLayout(self)
        layout.addLayout(switch_toolbar)
        layout.addLayout(mark_toolbar)
        layout.addWidget(self.image_label, 1)
        self.setLayout(layout)

        self._size_to_screen()
        self.paths_changed(start_role=self.active_role)

    def _size_to_screen(self) -> None:
        """Open as a large normal window, while keeping normal borders/resizing."""
        screen = None
        parent = self.parent()
        if parent is not None and getattr(parent, "windowHandle", None):
            handle = parent.windowHandle()
            if handle is not None:
                screen = handle.screen()
        if screen is None:
            screen = QApplication.primaryScreen()
        if screen is None:
            self.resize(1200, 850)
            return

        geometry = screen.availableGeometry()
        width = int(geometry.width() * 0.90)
        height = int(geometry.height() * 0.90)
        self.resize(width, height)
        self.move(
            geometry.x() + (geometry.width() - width) // 2,
            geometry.y() + (geometry.height() - height) // 2,
        )

    def path_for_role(self, role: str) -> Path | None:
        if role == self.ROLE_LOCKED:
            return self.get_locked_path()
        if role == self.ROLE_SELECTED:
            return self.get_selected_path()
        return None

    def active_path(self) -> Path | None:
        """Return the exact image currently shown in the large window.

        Keep/delete actions must never guess from the main-window selection.
        They always use this stored path, which is updated immediately when the
        fullscreen window opens, when the user switches Default/Checked image,
        and when the shown preview is refreshed.
        """
        return self.displayed_path

    def set_action_target(self, role: str, path: Path | None) -> None:
        """Store the image that the fullscreen action buttons will modify."""
        self.displayed_role = role
        self.displayed_path = path

    def paths_changed(self, start_role: str | None = None) -> None:
        """Refresh button states and image after app selection/lock changes."""
        if start_role in self.ROLE_TITLES:
            self.active_role = start_role

        locked_available = self.get_locked_path() is not None
        selected_available = self.get_selected_path() is not None
        self.btn_show_locked.setEnabled(locked_available)
        self.btn_show_selected.setEnabled(selected_available)

        if self.path_for_role(self.active_role) is None:
            if selected_available:
                self.active_role = self.ROLE_SELECTED
            elif locked_available:
                self.active_role = self.ROLE_LOCKED

        self.refresh_image()

    def set_active_role(self, role: str) -> None:
        """Switch which image is displayed and becomes the action target."""
        if role not in self.ROLE_TITLES:
            return
        path = self.path_for_role(role)
        if path is None:
            return

        # Update the action target before repainting the image. This makes the
        # Keep/Delete/Clear buttons act on the image the user just switched to,
        # even if the picture is large and takes a moment to redraw.
        self.active_role = role
        self.set_action_target(role, path)
        self.refresh_status_controls()
        self.refresh_image()

    def mark_active_image(self, status: str) -> None:
        """Apply keep/delete/clear mark to the image currently shown.

        This deliberately uses ``self.displayed_path``. It does not ask the
        main window which image is selected/locked at button-click time, because
        that could target the wrong file after switching between Default image
        and Checked image inside this fullscreen window.
        """
        path = self.active_path()
        if path is None:
            return

        # Do not derive the target from Default/Checked labels here. The only
        # valid target is the path that is currently open in this window.
        if self.set_status(path, status):
            self.refresh_status_controls()

    def refresh_status_controls(self) -> None:
        """Update keep/delete buttons to match the active image status."""
        path = self.active_path()
        if path is None:
            self.status_label.setText("No image selected.")
            self.btn_keep.setEnabled(False)
            self.btn_mark_delete.setEnabled(False)
            self.btn_clear_mark.setEnabled(False)
            return

        role_title = self.ROLE_TITLES.get(self.displayed_role, "Image")
        status = self.get_status(path)
        if status == "keep":
            self.status_label.setText(
                f"Current fullscreen image: {role_title} — {path.name} · Status: kept. "
                "Clear the mark before it can be marked for deletion."
            )
            self.btn_keep.setEnabled(False)
            self.btn_mark_delete.setEnabled(False)
            self.btn_clear_mark.setEnabled(True)
        elif status == "delete":
            self.status_label.setText(
                f"Current fullscreen image: {role_title} — {path.name} · Status: marked for deletion."
            )
            self.btn_keep.setEnabled(True)
            self.btn_mark_delete.setEnabled(False)
            self.btn_clear_mark.setEnabled(True)
        else:
            self.status_label.setText(
                f"Current fullscreen image: {role_title} — {path.name} · Status: unmarked."
            )
            self.btn_keep.setEnabled(True)
            self.btn_mark_delete.setEnabled(True)
            self.btn_clear_mark.setEnabled(False)

    def refresh_image(self) -> None:
        """Load active image and scale down to fit; do not enlarge small images."""
        path = self.path_for_role(self.active_role)
        self.set_action_target(self.active_role, path)

        self.btn_show_locked.setChecked(self.active_role == self.ROLE_LOCKED)
        self.btn_show_selected.setChecked(self.active_role == self.ROLE_SELECTED)
        self.refresh_status_controls()

        if path is None:
            clear_label(self.image_label, "No image available")
            self.title_label.setText("No image available")
            return

        title = self.ROLE_TITLES.get(self.active_role, "Image")
        self.setWindowTitle(f"{title} — {path.name}")
        self.title_label.setText(f"{title}: {path.name} — {path.parent}")
        self.image_label.setToolTip(str(path))

        try:
            pixmap: QPixmap = make_preview_pixmap(
                path,
                self.image_label.width(),
                self.image_label.height(),
            )
        except Exception as exc:
            clear_label(self.image_label, f"Could not load image:\n{path}\n{exc}")
            return

        if pixmap.isNull():
            clear_label(self.image_label, f"Could not load image:\n{path}")
            return

        self.image_label.setPixmap(pixmap)
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt method name
        super().resizeEvent(event)
        self.resize_timer.start(80)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 - Qt method name
        key = event.key()
        if key == Qt.Key.Key_Escape:
            self.close()
            return
        if key in {Qt.Key.Key_Space, Qt.Key.Key_Left, Qt.Key.Key_Right}:
            other_role = (
                self.ROLE_LOCKED
                if self.active_role == self.ROLE_SELECTED
                else self.ROLE_SELECTED
            )
            if self.path_for_role(other_role) is not None:
                self.set_active_role(other_role)
                return
        super().keyPressEvent(event)
