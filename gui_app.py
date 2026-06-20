"""Main Qt application/controller.

This file wires the UI to the smaller helper modules:
- picture_loader.py: finds image files
- image_metadata.py: reads EXIF/file information
- image_display.py: shows previews
- picture_sorting.py: controls list order
- picture_list_columns.py: controls list/table columns and row text
"""

from __future__ import annotations

from collections import OrderedDict
from pathlib import Path
from time import sleep
from typing import Iterable

from PySide6.QtCore import QFile, Qt, QTimer
from PySide6.QtGui import QAction, QBrush, QColor, QFont
from PySide6.QtUiTools import QUiLoader
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHeaderView,
    QLabel,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QStatusBar,
    QTextEdit,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from about_dialog import show_about_dialog
from duplicate_grouping import (
    MetadataDateGroup,
    build_metadata_date_groups,
    duplicate_group_delete_count,
    metadata_date_display_text,
    metadata_date_group_key,
    sanitize_delete_statuses_for_duplicate_groups,
)
from image_display import clear_label, show_picture_on_label
from image_fullscreen import FullscreenImageWindow, PreviewFullscreenButton
from image_metadata import build_info_text, check_supported_metadata_date
from metadata_threading import (
    build_metadata_date_groups_threaded,
    prefetch_picture_summaries_threaded,
)
from picture_list_columns import DEFAULT_COLUMN_WIDTHS, HEADERS, PictureColumn, row_values_for_path
from picture_list_io import PictureListDocument, PictureListEntry, read_picture_list, write_picture_list
from picture_loader import is_image_file, load_picture_paths_with_progress
from threaded_work import default_worker_count, run_path_work_threaded
from warnings_errors_dialog import WarningsErrorsDialog
from trash_delete import move_path_to_trash
from picture_sorting import (
    SortMode,
    sort_mode_to_column_order,
    sort_picture_paths,
    sort_picture_paths_by_column,
)


PATH_ROLE = Qt.ItemDataRole.UserRole
STATUS_ROLE = Qt.ItemDataRole.UserRole + 1
GROUP_KIND_ROLE = Qt.ItemDataRole.UserRole + 2
GROUP_KEY_ROLE = Qt.ItemDataRole.UserRole + 3

GROUP_KIND_DUPLICATE_HEADER = "duplicate-header"
GROUP_KIND_DUPLICATE_CHILD = "duplicate-child"
GROUP_KIND_UNIQUE_HEADER = "unique-header"
GROUP_KIND_UNIQUE_CHILD = "unique-child"
GROUP_KIND_MARKED_HEADER = "marked-header"
GROUP_KIND_MARKED_CHILD = "marked-child"


class ScanCanceled(Exception):
    """Raised internally when the user cancels a folder scan."""


class PictureExifCompareApp:
    """Controller class for the picture compare window."""

    def __init__(
        self,
        ui_path: Path,
        initial_paths: list[Path] | None = None,
        sort_mode: SortMode | str | None = None,
        require_metadata_date: bool = True,
        require_metadata: bool | None = None,
        metadata_worker_count: int | None = None,
    ) -> None:
        self.ui_path = ui_path
        self.sort_mode = sort_mode
        self.metadata_worker_count = default_worker_count() if metadata_worker_count is None else max(1, int(metadata_worker_count))
        if require_metadata is not None:
            require_metadata_date = require_metadata
        self.require_metadata_date = require_metadata_date
        self.locked_path: Path | None = None
        self.current_path: Path | None = None
        self.picture_paths: list[Path] = []
        self.status_by_path: dict[Path, str] = {}
        self.duplicate_view_enabled = False
        self.last_walked_over_count = 0
        self.last_metadata_date_count = 0
        self.last_read_error_count = 0
        self.last_statistics_text = "No scan statistics yet."
        self.warning_error_messages: list[str] = []
        self.warnings_errors_dialog: WarningsErrorsDialog | None = None
        self.fullscreen_image_window: FullscreenImageWindow | None = None
        self.updating_item_state = False
        self.column_sort_active = sort_mode is not None
        initial_column, initial_descending = sort_mode_to_column_order(sort_mode)
        self.current_sort_column = initial_column
        self.current_sort_order = (
            Qt.SortOrder.DescendingOrder if initial_descending else Qt.SortOrder.AscendingOrder
        )

        self.scan_is_running = False
        self.scan_is_paused = False
        self.scan_cancel_requested = False

        self.window = self._load_ui(ui_path)
        self._bind_widgets()
        self._setup_menu_bar()
        self._connect_signals()

        if initial_paths:
            self.load_paths(initial_paths)

    def _load_ui(self, ui_path: Path):
        if not ui_path.exists():
            raise FileNotFoundError(f"Could not find UI file: {ui_path}")

        ui_file = QFile(str(ui_path))
        if not ui_file.open(QFile.OpenModeFlag.ReadOnly):
            raise RuntimeError(f"Could not open UI file: {ui_path}")

        try:
            loader = QUiLoader()
            window = loader.load(ui_file)
        finally:
            ui_file.close()

        if window is None:
            raise RuntimeError(f"Could not load UI file: {ui_path}")
        return window

    def _required_child(self, qt_type, object_name: str):
        widget = self.window.findChild(qt_type, object_name)
        if widget is None:
            raise RuntimeError(f"UI file is missing required widget: {object_name}")
        return widget

    def _bind_widgets(self) -> None:
        self.btn_load: QPushButton = self._required_child(QPushButton, "btnLoadPictures")
        self.btn_append_images: QPushButton = self._required_child(QPushButton, "btnAppendImages")
        self.btn_append_image_list: QPushButton = self._required_child(QPushButton, "btnAppendImageList")
        self.btn_save_image_list: QPushButton = self._required_child(QPushButton, "btnSaveImageList")
        self.btn_load_image_list: QPushButton = self._required_child(QPushButton, "btnLoadImageList")
        self.btn_search_duplicates: QPushButton = self._required_child(QPushButton, "btnSearchDuplicates")
        self.list_pictures: QTreeWidget = self._required_child(QTreeWidget, "listPictures")
        self.btn_keep: QPushButton = self._required_child(QPushButton, "btnKeepPicture")
        self.btn_clear_keep: QPushButton = self._required_child(QPushButton, "btnClearKeepMark")
        self.btn_delete: QPushButton = self._required_child(QPushButton, "btnMarkForDeletion")
        self.btn_move_to_trash: QPushButton = self._required_child(QPushButton, "btnMoveMarkedToTrash")
        self.btn_lock: QPushButton = self._required_child(QPushButton, "btnLockSelected")
        self.btn_clear_locked: QPushButton = self._required_child(QPushButton, "btnClearLocked")
        self.label_locked: QLabel = self._required_child(QLabel, "labelLockedImage")
        self.label_selected: QLabel = self._required_child(QLabel, "labelSelectedImage")
        self.text_locked: QTextEdit = self._required_child(QTextEdit, "textLockedInfo")
        self.text_selected: QTextEdit = self._required_child(QTextEdit, "textSelectedInfo")
        self.statusbar: QStatusBar | None = self.window.findChild(QStatusBar, "statusbar")

        self._configure_picture_table()

        self.progress_scan = self._find_or_create_progress_bar()
        self.label_scan_stats = self._find_or_create_stats_label()
        self.btn_pause_scan = self._find_or_create_scan_button("btnPauseScan", "Pause")
        self.btn_cancel_scan = self._find_or_create_scan_button("btnCancelScan", "Cancel")
        self.set_scan_controls_idle()

        clear_label(self.label_locked, "No locked image")
        clear_label(self.label_selected, "No selected image")
        self.text_locked.setPlainText("")
        self.text_selected.setPlainText("")

        # Semi-transparent fullscreen buttons inside each preview frame.
        self.locked_fullscreen_button = PreviewFullscreenButton(
            self.label_locked,
            lambda: self.open_fullscreen_image("locked"),
            "Open the locked/default image in a large resizable window",
        )
        self.selected_fullscreen_button = PreviewFullscreenButton(
            self.label_selected,
            lambda: self.open_fullscreen_image("selected"),
            "Open the selected/checked image in a large resizable window",
        )

    def _configure_picture_table(self) -> None:
        """Configure the resizable multi-column picture list."""
        self.list_pictures.setColumnCount(len(HEADERS))
        self.list_pictures.setHeaderLabels(HEADERS)
        self.list_pictures.setRootIsDecorated(True)
        self.list_pictures.setItemsExpandable(True)
        self.list_pictures.setUniformRowHeights(True)
        self.list_pictures.setAlternatingRowColors(True)
        self.list_pictures.setAllColumnsShowFocus(True)
        # Sorting is handled manually when the user clicks a header. That lets
        # us sort Size, Width x Height, and Date columns by their real values
        # instead of simple alphabetic text sorting.
        self.list_pictures.setSortingEnabled(False)

        header = self.list_pictures.header()
        # A QTreeWidget/QHeaderView does not always emit sectionClicked unless
        # the header sections are explicitly made clickable. Without this, the
        # user can drag/resize columns, but clicking a column header may appear
        # to do nothing.
        header.setSectionsClickable(True)
        header.setHighlightSections(True)
        header.setSectionsMovable(True)
        header.setStretchLastSection(True)
        header.setSortIndicatorShown(True)
        header.setSortIndicator(self.current_sort_column, self.current_sort_order)
        for column, width in DEFAULT_COLUMN_WIDTHS.items():
            header.setSectionResizeMode(int(column), QHeaderView.ResizeMode.Interactive)
            self.list_pictures.setColumnWidth(int(column), width)

        self.list_pictures.setToolTip(
            "Click a column header to sort. Click it again to reverse order. "
            "Images with the same embedded metadata date are grouped as possible duplicates. "
            "Tick a box to mark that duplicate for deletion."
        )

    def _find_or_create_progress_bar(self) -> QProgressBar:
        """Find a progress bar from the UI file or add one under the list."""
        progress = self.window.findChild(QProgressBar, "progressScan")
        if progress is None:
            progress = QProgressBar(self.window)
            progress.setObjectName("progressScan")
            pictures_layout = self.window.findChild(QVBoxLayout, "picturesLayout")
            if pictures_layout is not None:
                # Put it below the list and above the keep/delete buttons.
                pictures_layout.insertWidget(2, progress)

        progress.setVisible(False)
        progress.setMinimum(0)
        progress.setValue(0)
        progress.setTextVisible(True)
        return progress

    def _find_or_create_stats_label(self) -> QLabel:
        """Find/create the persistent statistics label in the bottom status bar.

        The live scan text already appears in the QStatusBar at the very bottom
        of the window. This label is placed in that same status-bar area, so
        temporary live messages can appear while scanning, and the same bottom
        line shows permanent statistics again when the operation finishes.
        """
        label = self.window.findChild(QLabel, "labelScanStats")
        if label is None:
            label = QLabel(self.window)
            label.setObjectName("labelScanStats")

        # Older UI versions placed this label below the metadata/file-info
        # boxes. Remove it from that layout so the permanent statistics only
        # appear in the bottom status bar, exactly where the live scan text is.
        for layout_name in ("compareInfoOuterLayout", "picturesLayout"):
            layout = self.window.findChild(QVBoxLayout, layout_name)
            if layout is not None and layout.indexOf(label) >= 0:
                layout.removeWidget(label)

        label.setParent(None)

        if self.statusbar is not None:
            self.statusbar.addWidget(label, 1)
        else:
            # Fallback for unusual UI files without a status bar.
            pictures_layout = self.window.findChild(QVBoxLayout, "picturesLayout")
            if pictures_layout is not None:
                pictures_layout.addWidget(label)

        label.setWordWrap(False)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        label.setText(self.last_statistics_text)
        label.setToolTip(
            "Persistent scan/list statistics. File dates do not count as metadata dates."
        )
        return label


    def _find_or_create_scan_button(self, object_name: str, text: str) -> QPushButton:
        """Find a scan-control button, or create one as a fallback for old UI files."""
        button = self.window.findChild(QPushButton, object_name)
        if button is None:
            button = QPushButton(text, self.window)
            button.setObjectName(object_name)
            pictures_layout = self.window.findChild(QVBoxLayout, "picturesLayout")
            if pictures_layout is not None:
                # Fallback for older .ui files. The updated .ui places these
                # buttons on the same row as the progress bar.
                pictures_layout.insertWidget(3, button)
        button.setText(text)
        return button

    def set_scan_controls_idle(self) -> None:
        """Hide/disable scan controls when no scan is active."""
        self.scan_is_running = False
        self.scan_is_paused = False
        self.scan_cancel_requested = False
        self.progress_scan.setVisible(False)
        self.btn_pause_scan.setText("Pause")
        self.btn_pause_scan.setEnabled(False)
        self.btn_cancel_scan.setEnabled(False)
        self.btn_pause_scan.setVisible(False)
        self.btn_cancel_scan.setVisible(False)
        self._set_image_list_controls_enabled(True)
        self.update_move_to_trash_button_state()
        self.show_persistent_statistics_in_statusbar()

    def set_scan_controls_running(self, text: str) -> None:
        """Show/enable scan controls while a folder scan is active."""
        self.scan_is_running = True
        self.scan_is_paused = False
        self.scan_cancel_requested = False
        self.progress_scan.setVisible(True)
        self.progress_scan.setFormat(text)
        self.btn_pause_scan.setText("Pause")
        self.btn_pause_scan.setEnabled(True)
        self.btn_cancel_scan.setEnabled(True)
        self.btn_pause_scan.setVisible(True)
        self.btn_cancel_scan.setVisible(True)
        self._set_image_list_controls_enabled(False)
        self.update_move_to_trash_button_state()

    def _set_image_list_controls_enabled(self, enabled: bool) -> None:
        """Enable/disable buttons and menu actions that start list operations."""
        for attr_name in (
            "btn_load",
            "btn_append_images",
            "btn_append_image_list",
            "btn_save_image_list",
            "btn_load_image_list",
            "btn_search_duplicates",
            "btn_move_to_trash",
        ):
            widget = getattr(self, attr_name, None)
            if widget is not None:
                widget.setEnabled(enabled)

        for attr_name in (
            "action_load_pictures",
            "action_append_images",
            "action_append_image_list",
            "action_save_list",
            "action_load_list",
            "action_search_duplicates",
        ):
            action = getattr(self, attr_name, None)
            if action is not None:
                action.setEnabled(enabled)

    def show_busy_progress(self, text: str) -> None:
        """Show an indeterminate progress bar for work without a known total."""
        self.set_scan_controls_running(text)
        self.progress_scan.setRange(0, 0)
        self.message(text, 0)
        QApplication.processEvents()

    def toggle_scan_pause(self) -> None:
        """Pause or resume the active scan."""
        if not self.scan_is_running:
            return

        self.scan_is_paused = not self.scan_is_paused
        if self.scan_is_paused:
            self.btn_pause_scan.setText("Resume")
            self.message("Scan paused.", 0)
            self.progress_scan.setFormat("Paused · click Resume to continue")
        else:
            self.btn_pause_scan.setText("Pause")
            self.message("Scan resumed.", 0)

    def cancel_scan(self) -> None:
        """Request cancellation of the active scan."""
        if not self.scan_is_running:
            return

        self.scan_cancel_requested = True
        self.scan_is_paused = False
        self.btn_pause_scan.setEnabled(False)
        self.btn_cancel_scan.setEnabled(False)
        self.progress_scan.setFormat("Canceling scan...")
        self.message("Canceling scan...", 0)
        QApplication.processEvents()

    def _handle_pause_or_cancel(self) -> None:
        """Let the Qt event loop process Pause/Resume/Cancel during scans."""
        QApplication.processEvents()

        if self.scan_cancel_requested:
            raise ScanCanceled

        while self.scan_is_paused:
            QApplication.processEvents()
            if self.scan_cancel_requested:
                raise ScanCanceled
            sleep(0.05)

    def update_scan_progress(
        self,
        done: int,
        total: int,
        current_path: Path | None,
        kept_count: int,
        total_files_seen: int,
        read_error_count: int = 0,
    ) -> None:
        """Update progress while image metadata dates are being read."""
        self._handle_pause_or_cancel()

        if total <= 0:
            self.progress_scan.setRange(0, 0)
            text = f"Counting files... walked over {total_files_seen} file(s)."
        else:
            self.progress_scan.setRange(0, total)
            self.progress_scan.setValue(done)
            if self.require_metadata_date:
                kept_text = f"{kept_count} with metadata date"
            else:
                kept_text = f"{kept_count} loadable"
            text = (
                f"Checked {done}/{total} supported images · "
                f"{kept_text} · walked over {total_files_seen} file(s)"
            )
            if read_error_count:
                text += f" · {read_error_count} read warning/error(s)"

        self.progress_scan.setFormat(text)
        self.set_stats_text(
            self.format_statistics_text(
                walked_over=total_files_seen,
                metadata_date_count=kept_count,
                duplicate_file_count=None,
                unique_count=None,
                read_error_count=read_error_count,
            )
        )
        if current_path is not None:
            self.progress_scan.setToolTip(str(current_path))
        self.message(text, 0)
        self._handle_pause_or_cancel()

    def update_list_progress(
        self,
        done: int,
        total: int,
        current_path: Path | None,
        phase: str = "Preparing rows",
    ) -> None:
        """Update progress while row data or table rows are being prepared."""
        self._handle_pause_or_cancel()

        self.progress_scan.setRange(0, max(total, 1))
        self.progress_scan.setValue(done)
        text = f"{phase} {done}/{total} picture(s)..."
        self.progress_scan.setFormat(text)
        if current_path is not None:
            self.progress_scan.setToolTip(str(current_path))
        self.message(text, 0)
        self._handle_pause_or_cancel()

    def update_threaded_metadata_progress(
        self,
        done: int,
        total: int,
        current_path: Path | None,
        phase: str = "Reading image details in parallel",
    ) -> None:
        """Update progress for threaded metadata/summary work."""
        self._handle_pause_or_cancel()

        self.progress_scan.setRange(0, max(total, 1))
        self.progress_scan.setValue(done)
        text = f"{phase} {done}/{total} picture(s) · {self.metadata_worker_count} thread(s)..."
        self.progress_scan.setFormat(text)
        if current_path is not None:
            self.progress_scan.setToolTip(str(current_path))
        self.message(text, 0)
        self._handle_pause_or_cancel()

    def prefetch_picture_summaries_with_progress(
        self,
        paths: list[Path],
        phase: str = "Reading image details in parallel",
    ) -> None:
        """Read/cache image details in worker threads before sort/list rebuild."""
        if not paths:
            return

        self.set_scan_controls_running(f"{phase}...")
        warnings = prefetch_picture_summaries_threaded(
            list(dict.fromkeys(paths)),
            max_workers=self.metadata_worker_count,
            progress_callback=self.update_threaded_metadata_progress,
            phase=phase,
        )
        self.add_warning_error_messages(warnings)

    def update_non_cancelable_progress(
        self,
        done: int,
        total: int,
        text: str,
        current_path: Path | None = None,
    ) -> None:
        """Show progress for short final GUI operations that should not be canceled.

        Once the app starts replacing the visible table contents, canceling would
        leave the old table half-replaced. So these final phases keep the progress
        bar visible but disable Pause/Cancel until the table is consistent again.
        """
        self.progress_scan.setRange(0, max(total, 1))
        self.progress_scan.setValue(done)
        self.progress_scan.setFormat(text)
        if current_path is not None:
            self.progress_scan.setToolTip(str(current_path))
        self.message(text, 0)
        QApplication.processEvents()

    def begin_non_cancelable_final_phase(self, text: str) -> None:
        """Show scan controls for a final table-update phase without Pause/Cancel."""
        self.set_scan_controls_running(text)
        self.btn_pause_scan.setEnabled(False)
        self.btn_cancel_scan.setEnabled(False)
        self.progress_scan.setFormat(text)
        self.message(text, 0)
        QApplication.processEvents()

    def replace_picture_table_with_progress(self, items: list[QTreeWidgetItem]) -> None:
        """Replace the visible picture table while keeping the progress bar active.

        Building row objects is only one part of the work. For large lists, Qt may
        also spend noticeable time clearing the old tree, inserting thousands of
        rows, recalculating header/layout state, and repainting. This method makes
        that final phase visible instead of letting the window appear frozen.
        """
        total = len(items)
        self.begin_non_cancelable_final_phase("Updating visible list...")

        self.list_pictures.blockSignals(True)
        self.list_pictures.setUpdatesEnabled(False)
        try:
            self.list_pictures.clear()
            self.update_non_cancelable_progress(
                0,
                total,
                f"Updating visible list 0/{total} picture(s)...",
            )

            batch: list[QTreeWidgetItem] = []
            batch_size = 250
            for index, item in enumerate(items, start=1):
                batch.append(item)
                if len(batch) >= batch_size or index == total:
                    self.list_pictures.addTopLevelItems(batch)
                    batch = []
                    self.update_non_cancelable_progress(
                        index,
                        total,
                        f"Updating visible list {index}/{total} picture(s)...",
                        Path(item.data(PictureColumn.NAME, PATH_ROLE))
                        if item.data(PictureColumn.NAME, PATH_ROLE)
                        else None,
                    )
        finally:
            self.update_non_cancelable_progress(
                total,
                total,
                "Finalizing list display...",
            )
            # Re-apply the sort indicator after replacing the rows, so the
            # header keeps showing the active column/order.
            self.list_pictures.header().setSortIndicator(
                self.current_sort_column,
                self.current_sort_order,
            )
            self.list_pictures.setUpdatesEnabled(True)
            self.list_pictures.blockSignals(False)
            QApplication.processEvents()

    def iter_picture_items(self):
        """Yield every real picture row in the visible tree.

        Duplicate/unique header rows are skipped because they do not represent
        files.
        """
        for top_index in range(self.list_pictures.topLevelItemCount()):
            top_item = self.list_pictures.topLevelItem(top_index)
            if top_item.data(PictureColumn.NAME, PATH_ROLE):
                yield top_item
            for child_index in range(top_item.childCount()):
                child = top_item.child(child_index)
                if child.data(PictureColumn.NAME, PATH_ROLE):
                    yield child

    def first_picture_item(self) -> QTreeWidgetItem | None:
        """Return the first real picture row, skipping group headers."""
        return next(self.iter_picture_items(), None)

    def find_item_for_path_with_progress(self, path: Path | None) -> QTreeWidgetItem | None:
        """Find an image row in the visible grouped tree while showing progress."""
        if path is None:
            return None

        wanted = str(path)
        total = len(self.picture_paths)
        if total == 0:
            return None

        # For small lists this is instant, so avoid flickering progress text.
        show_progress = total >= 1000
        if show_progress:
            self.begin_non_cancelable_final_phase("Selecting picture in list...")
            self.progress_scan.setRange(0, total)

        for index, item in enumerate(self.iter_picture_items(), start=1):
            if show_progress and (index == 1 or index % 250 == 0):
                self.update_non_cancelable_progress(
                    index,
                    total,
                    f"Selecting picture in list {index}/{total}...",
                    path,
                )
            if item.data(PictureColumn.NAME, PATH_ROLE) == wanted:
                return item

        return None

    def set_current_item_with_progress(
        self,
        item: QTreeWidgetItem | None,
        text: str = "Loading selected picture...",
    ) -> None:
        """Select a row while showing an indeterminate progress message.

        Selecting a row also loads the preview and metadata text through the
        currentItemChanged signal. Large files or large metadata blocks can make
        that moment look like a freeze, so keep the progress bar visible.
        """
        if item is None:
            return
        self.begin_non_cancelable_final_phase(text)
        self.progress_scan.setRange(0, 0)
        self.progress_scan.setFormat(text)
        QApplication.processEvents()
        self.list_pictures.setCurrentItem(item)
        QApplication.processEvents()

    def refresh_locked_picture_with_progress(self) -> None:
        """Refresh the locked preview/info while the progress bar is visible."""
        if self.locked_path is None:
            clear_label(self.label_locked, "No locked image")
            self.text_locked.setPlainText("")
            return

        self.begin_non_cancelable_final_phase("Restoring locked picture preview...")
        self.progress_scan.setRange(0, 0)
        self.progress_scan.setToolTip(str(self.locked_path))
        show_picture_on_label(self.locked_path, self.label_locked)
        self.text_locked.setPlainText(build_info_text(self.locked_path))
        QApplication.processEvents()

    def _setup_menu_bar(self) -> None:
        """Create the top menu bar.

        The Files menu mirrors the picture-list action buttons, so every main
        list operation is available both as a button and as a menu action.
        """
        menu_bar = self.window.menuBar()
        menu_bar.clear()

        files_menu = menu_bar.addMenu("Files")
        self.action_load_pictures = QAction("Load pictures...", self.window)
        self.action_append_images = QAction("Append images to list...", self.window)
        self.action_append_image_list = QAction("Append image list...", self.window)
        self.action_save_list = QAction("Save image list...", self.window)
        self.action_load_list = QAction("Load image list...", self.window)
        self.action_search_duplicates = QAction("Search duplicates", self.window)
        self.action_show_warnings_errors = QAction("Show warnings/errors...", self.window)
        self.action_quit = QAction("Quit", self.window)

        self.action_load_pictures.setShortcut("Ctrl+L")
        self.action_append_images.setShortcut("Ctrl+Shift+O")
        self.action_append_image_list.setShortcut("Ctrl+Shift+L")
        self.action_save_list.setShortcut("Ctrl+S")
        self.action_load_list.setShortcut("Ctrl+O")
        self.action_search_duplicates.setShortcut("Ctrl+D")
        self.action_show_warnings_errors.setShortcut("Ctrl+E")
        self.action_quit.setShortcut("Ctrl+Q")

        files_menu.addAction(self.action_load_pictures)
        files_menu.addAction(self.action_append_images)
        files_menu.addAction(self.action_append_image_list)
        files_menu.addSeparator()
        files_menu.addAction(self.action_save_list)
        files_menu.addAction(self.action_load_list)
        files_menu.addSeparator()
        files_menu.addAction(self.action_search_duplicates)
        files_menu.addSeparator()
        files_menu.addAction(self.action_show_warnings_errors)
        files_menu.addSeparator()
        files_menu.addAction(self.action_quit)

        about_menu = QMenu("About", self.window)
        self.action_about_app = QAction("About this app...", self.window)
        self.action_about_qt = QAction("About Qt...", self.window)
        about_menu.addAction(self.action_about_app)
        about_menu.addAction(self.action_about_qt)

        self.btn_about_menu = QToolButton(self.window)
        self.btn_about_menu.setObjectName("btnAboutMenu")
        self.btn_about_menu.setText("About")
        self.btn_about_menu.setToolTip("About this app")
        self.btn_about_menu.setAutoRaise(True)
        self.btn_about_menu.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.btn_about_menu.setMenu(about_menu)

        # QMenuBar::setCornerWidget places the button at the far top-right.
        menu_bar.setCornerWidget(self.btn_about_menu, Qt.Corner.TopRightCorner)

    def _connect_signals(self) -> None:
        self.action_load_pictures.triggered.connect(self.choose_picture_folder)
        self.action_append_images.triggered.connect(self.choose_append_picture_folder)
        self.action_append_image_list.triggered.connect(self.append_saved_picture_list)
        self.action_save_list.triggered.connect(self.save_current_picture_list)
        self.action_load_list.triggered.connect(self.load_saved_picture_list)
        self.action_search_duplicates.triggered.connect(self.search_duplicates_in_current_list)
        self.action_show_warnings_errors.triggered.connect(self.show_warnings_errors_window)
        self.action_quit.triggered.connect(self.window.close)
        self.action_about_app.triggered.connect(lambda: show_about_dialog(self.window))
        self.action_about_qt.triggered.connect(QApplication.aboutQt)

        self.btn_load.clicked.connect(self.choose_picture_folder)
        self.btn_append_images.clicked.connect(self.choose_append_picture_folder)
        self.btn_append_image_list.clicked.connect(self.append_saved_picture_list)
        self.btn_save_image_list.clicked.connect(self.save_current_picture_list)
        self.btn_load_image_list.clicked.connect(self.load_saved_picture_list)
        self.btn_search_duplicates.clicked.connect(self.search_duplicates_in_current_list)
        self.btn_pause_scan.clicked.connect(self.toggle_scan_pause)
        self.btn_cancel_scan.clicked.connect(self.cancel_scan)
        self.list_pictures.currentItemChanged.connect(self.on_picture_selected)
        self.list_pictures.itemChanged.connect(self.on_picture_item_changed)
        self.list_pictures.itemDoubleClicked.connect(self.open_fullscreen_from_item)
        self.list_pictures.header().sectionClicked.connect(self.sort_picture_list_by_clicked_column)
        self.btn_lock.clicked.connect(self.lock_selected_picture)
        self.btn_clear_locked.clicked.connect(self.clear_locked_picture)
        self.btn_keep.clicked.connect(lambda: self.set_selected_status("keep"))
        self.btn_clear_keep.clicked.connect(self.clear_keep_for_selected_picture)
        self.btn_delete.clicked.connect(lambda: self.set_selected_status("delete"))
        self.btn_move_to_trash.clicked.connect(self.move_marked_pictures_to_trash)

    def show(self) -> None:
        self.window.show()

    def message(self, text: str, timeout_ms: int = 5000) -> None:
        if self.statusbar is not None:
            self.statusbar.showMessage(text, timeout_ms)

    def set_stats_text(self, text: str) -> None:
        """Update the persistent bottom statistics text."""
        self.last_statistics_text = text
        self.label_scan_stats.setText(text)
        self.label_scan_stats.setToolTip(text)

    def show_persistent_statistics_in_statusbar(self) -> None:
        """Reveal the permanent statistics line in the bottom-left status bar."""
        if self.statusbar is not None:
            # Clear any temporary live/progress message so the normal status-bar
            # widget containing the statistics becomes visible again.
            self.statusbar.clearMessage()
        self.label_scan_stats.setText(self.last_statistics_text)
        self.label_scan_stats.setToolTip(self.last_statistics_text)

    def show_temporary_message_then_statistics(self, text: str, timeout_ms: int = 3000) -> None:
        """Show a short note, then return to the permanent statistics line."""
        self.message(text, timeout_ms)
        QTimer.singleShot(timeout_ms, self.show_persistent_statistics_in_statusbar)

    def open_fullscreen_image(self, start_role: str) -> None:
        """Open locked/default or selected/checked image in a large resizable window."""
        if start_role == "locked" and self.locked_path is None:
            QMessageBox.information(
                self.window,
                "No default image",
                "Lock a default image first, then open it in fullscreen view.",
            )
            return
        if start_role == "selected" and self.current_path is None:
            QMessageBox.information(
                self.window,
                "No checked image",
                "Select an image first, then open it in fullscreen view.",
            )
            return

        if self.fullscreen_image_window is None:
            self.fullscreen_image_window = FullscreenImageWindow(
                self.window,
                locked_path_getter=lambda: self.locked_path,
                selected_path_getter=lambda: self.current_path,
                start_role=start_role,
                group_paths_getter=self.current_selected_group_paths,
                selected_path_setter=self.select_path_from_fullscreen,
                status_getter=lambda path: self.status_by_path.get(path, ""),
                status_setter=self.set_path_status_from_fullscreen,
                default_setter=self.set_default_path_from_fullscreen,
            )
        else:
            self.fullscreen_image_window.paths_changed(start_role=start_role)

        self.fullscreen_image_window.show()
        self.fullscreen_image_window.raise_()
        self.fullscreen_image_window.activateWindow()

    def open_fullscreen_from_item(self, item: QTreeWidgetItem, column: int = 0) -> None:
        """Double-click an image row to open it in the large preview.

        This is useful when no default image has been chosen yet: select or
        double-click a picture in the list and the large preview opens directly
        on that selected image.
        """
        del column
        path_text = item.data(PictureColumn.NAME, PATH_ROLE) if item is not None else ""
        if not path_text:
            return
        self.list_pictures.setCurrentItem(item)
        self.open_fullscreen_image("selected")

    def current_visible_picture_paths(self) -> list[Path]:
        """Return visible picture paths in the same order as the tree list."""
        paths: list[Path] = []
        for item in self.iter_picture_items():
            path_text = item.data(PictureColumn.NAME, PATH_ROLE)
            if path_text:
                paths.append(Path(path_text))
        return paths

    def current_selected_group_paths(self) -> list[Path]:
        """Return paths belonging to the currently selected visible group.

        In duplicate view this means the sibling rows under the same
        Duplicates/Unique parent. In flat view there are no parent headers, so
        the whole visible list is the browsing group.
        """
        current = self.list_pictures.currentItem()
        if current is None:
            return self.current_visible_picture_paths()

        current_path_text = current.data(PictureColumn.NAME, PATH_ROLE)
        if not current_path_text:
            return self.current_visible_picture_paths()

        parent = current.parent()
        if parent is not None:
            paths: list[Path] = []
            for index in range(parent.childCount()):
                child = parent.child(index)
                path_text = child.data(PictureColumn.NAME, PATH_ROLE)
                if path_text:
                    paths.append(Path(path_text))
            if paths:
                return paths

        # Flat list row or a fallback if the parent did not contain image rows.
        return self.current_visible_picture_paths()

    def select_path_from_fullscreen(self, path: Path) -> None:
        """Select a picture row when the large preview moves prev/next."""
        path = Path(path)
        item = self.find_item_for_path_with_progress(path)
        if item is not None:
            self.list_pictures.setCurrentItem(item)
        else:
            # Fallback if the item cannot be found in the current visible tree.
            self.current_path = path
            if path.exists():
                show_picture_on_label(path, self.label_selected)
                self.text_selected.setPlainText(build_info_text(path))

    def set_default_path_from_fullscreen(self, path: Path) -> bool:
        """Set the default/locked picture from the fullscreen window."""
        path = Path(path)
        if path not in self.picture_paths:
            QMessageBox.information(
                self.window,
                "Picture not in list",
                "That picture is no longer in the current list.",
            )
            return False

        self.locked_path = path
        show_picture_on_label(self.locked_path, self.label_locked)
        self.text_locked.setPlainText(build_info_text(self.locked_path))
        self.message(f"Locked default picture: {self.locked_path.name}")
        return True

    def refresh_fullscreen_window_if_open(self) -> None:
        """Keep the large image window in sync when selected/locked images change."""
        if self.fullscreen_image_window is not None and self.fullscreen_image_window.isVisible():
            self.fullscreen_image_window.paths_changed()

    def add_warning_error_messages(self, messages: list[str]) -> None:
        """Remember warning/error messages and update the window if it is open."""
        if not messages:
            return

        self.warning_error_messages.extend(messages)
        if self.warnings_errors_dialog is not None:
            self.warnings_errors_dialog.set_messages(self.warning_error_messages)

    def marked_for_deletion_paths(self) -> list[Path]:
        """Return currently loaded images marked for deletion, in list order."""
        return [
            path
            for path in self.picture_paths
            if self.status_by_path.get(path) == "delete"
        ]

    def update_move_to_trash_button_state(self) -> None:
        """Update the trash button text/enabled state from current marks."""
        button = getattr(self, "btn_move_to_trash", None)
        if button is None:
            return

        count = len(self.marked_for_deletion_paths())
        if count:
            button.setText(f"🗑 Move {count} to trash")
            button.setToolTip(f"Move {count} picture(s) marked for deletion to the system Trash")
        else:
            button.setText("🗑 Move to trash")
            button.setToolTip("Mark one or more pictures for deletion first, then move them to the system Trash")
        button.setEnabled((not self.scan_is_running) and count > 0)

    def show_warnings_errors_window(self) -> None:
        """Open a copyable warning/error list window."""
        if self.warnings_errors_dialog is None:
            self.warnings_errors_dialog = WarningsErrorsDialog(self.window)

        self.warnings_errors_dialog.set_messages(self.warning_error_messages)
        self.warnings_errors_dialog.show()
        self.warnings_errors_dialog.raise_()
        self.warnings_errors_dialog.activateWindow()

    def format_statistics_text(
        self,
        *,
        walked_over: int | None,
        metadata_date_count: int | None,
        duplicate_file_count: int | None,
        unique_count: int | None,
        read_error_count: int | None,
        duplicate_group_count: int | None = None,
        skipped_without_metadata_date: int | None = None,
        skipped_same_path_count: int | None = None,
    ) -> str:
        """Build the persistent scan/list statistics line.

        The duplicate/unique values are based on embedded metadata date in the
        currently loaded list. They are not based on same file path.
        """
        parts: list[str] = []

        if walked_over is not None:
            parts.append(f"Walked over {walked_over:,} file(s)")
        if metadata_date_count is not None:
            parts.append(f"{metadata_date_count:,} had metadata date")
        if duplicate_file_count is not None:
            if duplicate_group_count is not None:
                parts.append(
                    f"{duplicate_file_count:,} duplicate picture(s) in {duplicate_group_count:,} group(s)"
                )
            else:
                parts.append(f"{duplicate_file_count:,} duplicate picture(s)")
        if unique_count is not None:
            parts.append(f"{unique_count:,} unique picture(s)")
        if skipped_without_metadata_date is not None:
            parts.append(f"{skipped_without_metadata_date:,} without metadata date")
        if skipped_same_path_count is not None:
            parts.append(f"{skipped_same_path_count:,} already in list")
        if read_error_count is not None:
            parts.append(f"{read_error_count:,} read warning/error(s)")

        return " · ".join(parts) if parts else "No scan statistics yet."

    def calculate_metadata_duplicate_stats(self, paths: Iterable[Path]) -> tuple[int, int, int]:
        """Return duplicate_file_count, unique_count, duplicate_group_count.

        Possible duplicates are images that share the same embedded metadata
        date. Same file path is a separate append/load-list de-duplication rule.
        """
        grouped: dict[str, list[Path]] = {}
        no_date_count = 0

        for path in paths:
            key = metadata_date_group_key(path)
            if key:
                grouped.setdefault(key, []).append(path)
            else:
                no_date_count += 1

        duplicate_group_count = sum(1 for group_paths in grouped.values() if len(group_paths) >= 2)
        duplicate_file_count = sum(
            len(group_paths) for group_paths in grouped.values() if len(group_paths) >= 2
        )
        unique_count = no_date_count + sum(
            1 for group_paths in grouped.values() if len(group_paths) == 1
        )
        return duplicate_file_count, unique_count, duplicate_group_count

    def update_persistent_statistics(
        self,
        *,
        walked_over: int | None = None,
        metadata_date_count: int | None = None,
        read_error_count: int | None = None,
        skipped_without_metadata_date: int | None = None,
        skipped_same_path_count: int | None = None,
    ) -> None:
        """Update persistent stats using the current picture list."""
        if walked_over is not None:
            self.last_walked_over_count = walked_over
        if metadata_date_count is not None:
            self.last_metadata_date_count = metadata_date_count
        if read_error_count is not None:
            self.last_read_error_count = read_error_count

        duplicate_file_count, unique_count, duplicate_group_count = self.calculate_metadata_duplicate_stats(
            self.picture_paths
        )
        self.set_stats_text(
            self.format_statistics_text(
                walked_over=self.last_walked_over_count,
                metadata_date_count=self.last_metadata_date_count,
                duplicate_file_count=duplicate_file_count,
                unique_count=unique_count,
                duplicate_group_count=duplicate_group_count,
                read_error_count=self.last_read_error_count,
                skipped_without_metadata_date=skipped_without_metadata_date,
                skipped_same_path_count=skipped_same_path_count,
            )
        )


    def sort_paths_for_active_order(self, paths: list[Path]) -> list[Path]:
        """Sort paths using either the selected column order or the startup sort mode."""
        if self.column_sort_active:
            descending = self.current_sort_order == Qt.SortOrder.DescendingOrder
            return sort_picture_paths_by_column(paths, self.current_sort_column, descending)
        return sort_picture_paths(paths, self.sort_mode)

    def sort_picture_list_by_clicked_column(self, column: int) -> None:
        """Reorder the picture table when the user clicks a column header."""
        if self.scan_is_running:
            return
        if not self.picture_paths:
            return

        if column == self.current_sort_column:
            self.current_sort_order = (
                Qt.SortOrder.DescendingOrder
                if self.current_sort_order == Qt.SortOrder.AscendingOrder
                else Qt.SortOrder.AscendingOrder
            )
        else:
            self.current_sort_column = column
            self.current_sort_order = Qt.SortOrder.AscendingOrder

        self.column_sort_active = True
        self.list_pictures.header().setSortIndicator(self.current_sort_column, self.current_sort_order)

        column_name = HEADERS[column] if 0 <= column < len(HEADERS) else f"column {column}"
        order_text = (
            "descending"
            if self.current_sort_order == Qt.SortOrder.DescendingOrder
            else "ascending"
        )

        old_current_path = self.current_path
        old_locked_path = self.locked_path
        old_statuses = dict(self.status_by_path)

        try:
            self.prefetch_picture_summaries_with_progress(
                list(self.picture_paths),
                f"Reading image details before sorting by {column_name}",
            )
            self.show_busy_progress(f"Sorting by {column_name} ({order_text})...")
            sorted_paths = self.sort_paths_for_active_order(list(self.picture_paths))
            self._handle_pause_or_cancel()

            # Build the new rows before replacing the visible table. If sorting
            # is canceled while rows are prepared, the old order stays visible.
            items = self.create_picture_items_with_progress(sorted_paths, old_statuses)
        except ScanCanceled:
            self.set_scan_controls_idle()
            self.message("Sort canceled. Previous order was kept.")
            self.scan_is_paused = False
            return
        finally:
            self.scan_is_paused = False

        self.picture_paths = sorted_paths
        self.status_by_path = {
            path: old_statuses.get(path, "")
            for path in self.picture_paths
            if old_statuses.get(path, "")
        }
        self.locked_path = old_locked_path if old_locked_path in self.picture_paths else None
        self.current_path = None

        self.replace_picture_table_with_progress(items)
        self.refresh_locked_picture_with_progress()

        selected_item = self.find_item_for_path_with_progress(old_current_path)
        if selected_item is None and self.picture_paths:
            selected_item = self.first_picture_item()
        self.set_current_item_with_progress(
            selected_item,
            f"Loading picture after sorting by {column_name}...",
        )

        self.set_scan_controls_idle()
        self.message(f"Sorted {len(self.picture_paths)} picture(s) by {column_name} ({order_text}).")

    def choose_picture_folder(self) -> None:
        start_dir = str(Path.home())
        if self.picture_paths:
            start_dir = str(self.picture_paths[0].parent)

        folder = QFileDialog.getExistingDirectory(
            self.window,
            "Choose folder containing pictures",
            start_dir,
        )
        if not folder:
            return

        self.load_paths([Path(folder)])

    def choose_append_picture_folder(self) -> None:
        """Choose a folder and append matching images to the current list."""
        start_dir = str(Path.home())
        if self.picture_paths:
            start_dir = str(self.picture_paths[0].parent)

        folder = QFileDialog.getExistingDirectory(
            self.window,
            "Choose folder to append pictures from",
            start_dir,
        )
        if not folder:
            return

        self.append_paths([Path(folder)])

    def load_paths(self, paths: Iterable[Path]) -> None:
        old_duplicate_view = self.duplicate_view_enabled
        self.show_busy_progress("Counting files...")

        try:
            scan_result = load_picture_paths_with_progress(
                paths,
                recursive=True,
                require_metadata_date=self.require_metadata_date,
                progress_callback=self.update_scan_progress,
                warning_callback=self.add_warning_error_messages,
                max_workers=self.metadata_worker_count,
            )

            self.prefetch_picture_summaries_with_progress(
                scan_result.picture_paths,
                "Reading image details before sorting",
            )
            self.show_busy_progress("Sorting matching pictures...")
            found = self.sort_paths_for_active_order(scan_result.picture_paths)
            self._handle_pause_or_cancel()

            # Loading pictures returns to the normal flat list. Press
            # "Search duplicates" when you want to group matching metadata dates.
            self.duplicate_view_enabled = False

            # Build the table rows before replacing the old list. If the user
            # cancels here, the previous picture list stays untouched.
            items = self.create_picture_items_with_progress(found)
        except ScanCanceled:
            self.duplicate_view_enabled = old_duplicate_view
            self.set_scan_controls_idle()
            self.message("Scan canceled. Previous picture list was kept.")
            return
        finally:
            # Make sure a pause state never leaks into the next scan.
            self.scan_is_paused = False

        self.picture_paths = found
        self.status_by_path.clear()
        self.locked_path = None
        self.current_path = None

        self.replace_picture_table_with_progress(items)

        clear_label(self.label_locked, "No locked image")
        clear_label(self.label_selected, "No selected image")
        self.text_locked.setPlainText("")
        self.text_selected.setPlainText("")

        if self.picture_paths:
            self.set_current_item_with_progress(
                self.first_picture_item(),
                "Loading first picture preview...",
            )
            self.update_persistent_statistics(
                walked_over=scan_result.total_files_seen,
                metadata_date_count=scan_result.metadata_date_count,
                read_error_count=scan_result.read_error_count,
                skipped_without_metadata_date=scan_result.skipped_without_metadata_date,
            )
            self.set_scan_controls_idle()
            self.show_persistent_statistics_in_statusbar()
        else:
            self.update_persistent_statistics(
                walked_over=scan_result.total_files_seen,
                metadata_date_count=scan_result.metadata_date_count,
                read_error_count=scan_result.read_error_count,
                skipped_without_metadata_date=scan_result.skipped_without_metadata_date,
            )
            self.set_scan_controls_idle()
            self.show_persistent_statistics_in_statusbar()

            if self.require_metadata_date:
                details = (
                    "No pictures with an embedded metadata date were found in that location.\n\n"
                    f"Walked over {scan_result.total_files_seen} file(s).\n"
                    f"Checked {scan_result.candidate_image_count} supported image file(s).\n"
                    f"Skipped {scan_result.skipped_without_metadata_date} image file(s) without a metadata date.\n\n"
                    "Normal file info such as size, modified date, and dimensions does not count."
                )
            else:
                details = (
                    "No supported image files were found in that location.\n\n"
                    f"Walked over {scan_result.total_files_seen} file(s).\n"
                    f"Checked {scan_result.candidate_image_count} supported image file(s)."
                )

            QMessageBox.information(self.window, "No pictures found", details)

    def append_paths(self, paths: Iterable[Path]) -> None:
        """Append matching images from one or more folders/files to the current list.

        Duplicate paths are skipped. Existing keep/delete marks are preserved.
        The final combined list is sorted using the current sort mode, just like a
        normal load.
        """
        old_paths = list(self.picture_paths)
        old_statuses = dict(self.status_by_path)
        old_locked_path = self.locked_path
        old_duplicate_view = self.duplicate_view_enabled

        self.show_busy_progress("Counting files to append...")

        try:
            scan_result = load_picture_paths_with_progress(
                paths,
                recursive=True,
                require_metadata_date=self.require_metadata_date,
                progress_callback=self.update_scan_progress,
                warning_callback=self.add_warning_error_messages,
                max_workers=self.metadata_worker_count,
            )

            existing_paths = set(old_paths)
            new_paths = [path for path in scan_result.picture_paths if path not in existing_paths]
            duplicate_count = len(scan_result.picture_paths) - len(new_paths)

            self.prefetch_picture_summaries_with_progress(
                old_paths + new_paths,
                "Reading image details before sorting append",
            )
            self.show_busy_progress("Sorting appended pictures...")
            combined_paths = self.sort_paths_for_active_order(old_paths + new_paths)
            combined_statuses = {
                path: old_statuses.get(path, "")
                for path in combined_paths
                if old_statuses.get(path, "")
            }
            self._handle_pause_or_cancel()

            # Appending changes the list, so return to the normal flat list.
            # Press "Search duplicates" again to rebuild duplicate groups.
            self.duplicate_view_enabled = False

            # Build all rows before replacing the old widget contents. This keeps
            # the old list visible and untouched if the user presses Cancel here.
            items = self.create_picture_items_with_progress(combined_paths, combined_statuses)
        except ScanCanceled:
            self.duplicate_view_enabled = old_duplicate_view
            self.set_scan_controls_idle()
            self.message("Append canceled. Previous picture list was kept.")
            return
        finally:
            self.scan_is_paused = False

        self.picture_paths = combined_paths
        self.status_by_path = combined_statuses
        self.current_path = None
        self.locked_path = old_locked_path if old_locked_path in self.picture_paths else None

        self.replace_picture_table_with_progress(items)

        clear_label(self.label_selected, "No selected image")
        self.text_selected.setPlainText("")

        self.refresh_locked_picture_with_progress()

        if new_paths:
            # Select the first newly appended image, even after sorting.
            item = self.find_item_for_path_with_progress(new_paths[0])
            self.set_current_item_with_progress(item, "Loading appended picture preview...")
        elif self.picture_paths:
            self.set_current_item_with_progress(
                self.first_picture_item(),
                "Loading first picture preview...",
            )

        self.update_persistent_statistics(
            walked_over=scan_result.total_files_seen,
            metadata_date_count=scan_result.metadata_date_count,
            read_error_count=scan_result.read_error_count,
            skipped_without_metadata_date=scan_result.skipped_without_metadata_date,
            skipped_same_path_count=duplicate_count,
        )
        self.set_scan_controls_idle()
        self.show_persistent_statistics_in_statusbar()

        if not new_paths:
            QMessageBox.information(
                self.window,
                "No new pictures appended",
                "No new qualifying pictures were added.\n\n"
                f"Checked {scan_result.candidate_image_count} supported image file(s).\n"
                f"Skipped {duplicate_count} duplicate picture(s) already in the list.\n"
                f"Walked over {scan_result.total_files_seen} file(s).",
            )

    def search_duplicates_in_current_list(self) -> None:
        """Search the current list for possible duplicates by metadata date.

        The current visible table is left untouched until the duplicate search,
        row preparation, and table rebuild all complete. If the user presses
        Cancel, the previous list view stays visible.
        """
        if self.scan_is_running:
            return

        if not self.picture_paths:
            QMessageBox.information(
                self.window,
                "No pictures loaded",
                "Load or append pictures before searching for duplicates.",
            )
            return

        old_current_path = self.current_path
        old_locked_path = self.locked_path
        old_statuses = dict(self.status_by_path)

        try:
            duplicate_groups, unique_paths, marked_delete_paths = self.build_metadata_date_groups_with_progress(
                list(self.picture_paths),
                old_statuses,
            )

            # Build the grouped rows before replacing the current list. If this
            # is canceled, the old flat/grouped list stays untouched.
            items = self.create_grouped_picture_items_with_progress(
                list(self.picture_paths),
                old_statuses,
                duplicate_groups=duplicate_groups,
                unique_paths=unique_paths,
                marked_delete_paths=marked_delete_paths,
            )
        except ScanCanceled:
            self.set_scan_controls_idle()
            self.message("Duplicate search canceled. Previous list was kept.")
            return
        finally:
            self.scan_is_paused = False

        self.duplicate_view_enabled = True
        self.status_by_path = {
            path: old_statuses.get(path, "")
            for path in self.picture_paths
            if old_statuses.get(path, "")
        }
        self.locked_path = old_locked_path if old_locked_path in self.picture_paths else None
        self.current_path = None

        self.replace_picture_table_with_progress(items)
        self.refresh_locked_picture_with_progress()

        selected_item = self.find_item_for_path_with_progress(old_current_path)
        if selected_item is None and self.picture_paths:
            selected_item = self.first_picture_item()
        self.set_current_item_with_progress(
            selected_item,
            "Loading picture after duplicate search...",
        )

        self.update_persistent_statistics()
        self.set_scan_controls_idle()

        self.show_persistent_statistics_in_statusbar()

        if not duplicate_groups:
            QMessageBox.information(
                self.window,
                "No duplicate groups found",
                "No images in the current list share the same embedded metadata date.",
            )

    def append_saved_picture_list(self) -> None:
        """Append a saved JSON picture list to the current list.

        This differs from Load image list: it keeps the existing list and only
        adds saved paths that are not already present. Existing keep/delete marks
        are preserved, and marks from the appended list are applied to newly
        added paths.
        """
        filename, _selected_filter = QFileDialog.getOpenFileName(
            self.window,
            "Append picture list",
            str(Path.home()),
            "Picture list JSON (*.json);;All files (*)",
        )
        if not filename:
            return

        try:
            document = read_picture_list(Path(filename))
        except Exception as exc:
            QMessageBox.critical(self.window, "Could not append list", str(exc))
            return

        old_paths = list(self.picture_paths)
        old_statuses = dict(self.status_by_path)
        old_locked_path = self.locked_path
        old_duplicate_view = self.duplicate_view_enabled

        try:
            (
                loaded_paths,
                loaded_statuses,
                skipped_missing,
                skipped_unsupported,
                skipped_without_metadata_date,
                metadata_date_count,
                read_error_count,
            ) = self.validate_loaded_picture_list_with_progress(document)

            existing_paths = set(old_paths)
            new_paths = [path for path in loaded_paths if path not in existing_paths]
            already_in_list_count = len(loaded_paths) - len(new_paths)

            self.prefetch_picture_summaries_with_progress(
                old_paths + new_paths,
                "Reading image details before sorting appended list",
            )
            self.show_busy_progress("Sorting appended image list...")
            combined_paths = self.sort_paths_for_active_order(old_paths + new_paths)
            combined_statuses = {
                path: old_statuses.get(path, "")
                for path in combined_paths
                if old_statuses.get(path, "")
            }
            for path in new_paths:
                status = loaded_statuses.get(path, "")
                if status:
                    combined_statuses[path] = status
            self._handle_pause_or_cancel()

            # Appending a saved list changes the active list, so return to the
            # normal flat list. Press Search duplicates again to group matches.
            self.duplicate_view_enabled = False
            items = self.create_picture_items_with_progress(combined_paths, combined_statuses)
        except ScanCanceled:
            self.duplicate_view_enabled = old_duplicate_view
            self.set_scan_controls_idle()
            self.message("Append list canceled. Previous picture list was kept.")
            return
        finally:
            self.scan_is_paused = False

        self.picture_paths = combined_paths
        self.status_by_path = combined_statuses
        self.current_path = None
        if old_locked_path in self.picture_paths:
            self.locked_path = old_locked_path
        elif self.locked_path is None and document.locked_path in self.picture_paths:
            self.locked_path = document.locked_path
        else:
            self.locked_path = old_locked_path if old_locked_path in self.picture_paths else None

        self.replace_picture_table_with_progress(items)

        clear_label(self.label_selected, "No selected image")
        self.text_selected.setPlainText("")
        self.refresh_locked_picture_with_progress()

        if new_paths:
            item = self.find_item_for_path_with_progress(new_paths[0])
            self.set_current_item_with_progress(item, "Loading appended list picture preview...")
        elif self.picture_paths:
            self.set_current_item_with_progress(
                self.first_picture_item(),
                "Loading first picture preview...",
            )

        self.update_persistent_statistics(
            walked_over=len(document.entries),
            metadata_date_count=metadata_date_count,
            read_error_count=read_error_count,
            skipped_without_metadata_date=skipped_without_metadata_date,
            skipped_same_path_count=already_in_list_count,
        )
        self.set_scan_controls_idle()
        self.show_persistent_statistics_in_statusbar()

        if not new_paths:
            QMessageBox.information(
                self.window,
                "No new pictures appended",
                "No new qualifying pictures were added from the saved list.\n\n"
                f"Checked {len(document.entries)} saved row(s).\n"
                f"Skipped {already_in_list_count} picture(s) already in the current list.\n"
                f"Skipped {skipped_missing} missing, {skipped_unsupported} unsupported",
            )

    def save_current_picture_list(self) -> None:
        """Save the current list to a JSON file."""
        if not self.picture_paths:
            QMessageBox.information(self.window, "No list to save", "Load some pictures before saving a list.")
            return

        start_file = Path.home() / "picture_metadata_list.json"
        filename, _selected_filter = QFileDialog.getSaveFileName(
            self.window,
            "Save picture list",
            str(start_file),
            "Picture list JSON (*.json);;All files (*)",
        )
        if not filename:
            return

        save_path = Path(filename).expanduser()
        if save_path.suffix == "":
            save_path = save_path.with_suffix(".json")

        try:
            write_picture_list(
                save_path,
                self.picture_paths,
                status_by_path=self.status_by_path,
                locked_path=self.locked_path,
                sort_mode=str(self.sort_mode) if self.sort_mode is not None else None,
                require_metadata_date=self.require_metadata_date,
            )
        except Exception as exc:
            QMessageBox.critical(self.window, "Could not save list", str(exc))
            return

        self.message(f"Saved {len(self.picture_paths)} picture(s) to {save_path}.")

    def load_saved_picture_list(self) -> None:
        """Load a saved JSON list and rebuild the picture table."""
        filename, _selected_filter = QFileDialog.getOpenFileName(
            self.window,
            "Load picture list",
            str(Path.home()),
            "Picture list JSON (*.json);;All files (*)",
        )
        if not filename:
            return

        try:
            document = read_picture_list(Path(filename))
        except Exception as exc:
            QMessageBox.critical(self.window, "Could not load list", str(exc))
            return

        old_duplicate_view = self.duplicate_view_enabled

        try:
            (
                paths,
                statuses,
                skipped_missing,
                skipped_unsupported,
                skipped_without_metadata_date,
                metadata_date_count,
                read_error_count,
            ) = self.validate_loaded_picture_list_with_progress(document)
            self.prefetch_picture_summaries_with_progress(
                paths,
                "Reading image details before building loaded list",
            )
            # Loading a saved list returns to the normal flat list. Press
            # "Search duplicates" to group matching metadata dates.
            self.duplicate_view_enabled = False
            items = self.create_picture_items_with_progress(paths, statuses)
        except ScanCanceled:
            self.duplicate_view_enabled = old_duplicate_view
            self.set_scan_controls_idle()
            self.message("Load list canceled. Previous picture list was kept.")
            return
        finally:
            self.scan_is_paused = False

        self.picture_paths = paths
        self.status_by_path = statuses
        self.current_path = None
        self.locked_path = None

        if document.locked_path in self.picture_paths:
            self.locked_path = document.locked_path

        self.replace_picture_table_with_progress(items)

        clear_label(self.label_selected, "No selected image")
        self.text_selected.setPlainText("")

        self.refresh_locked_picture_with_progress()

        if self.picture_paths:
            self.set_current_item_with_progress(
                self.first_picture_item(),
                "Loading first picture preview...",
            )

        self.update_persistent_statistics(
            walked_over=len(document.entries),
            metadata_date_count=metadata_date_count,
            read_error_count=read_error_count,
            skipped_without_metadata_date=skipped_without_metadata_date,
        )
        self.set_scan_controls_idle()

        message = (
            f"Loaded {len(self.picture_paths)} picture(s) from saved list. "
            f"Skipped {skipped_missing} missing, {skipped_unsupported} unsupported"
        )
        if self.require_metadata_date:
            message += f", {skipped_without_metadata_date} without metadata date"
        message += "."
        self.show_persistent_statistics_in_statusbar()

        if not self.picture_paths:
            QMessageBox.information(
                self.window,
                "No pictures loaded",
                message + "\n\nThe old list was replaced because the saved list was read successfully, "
                "but no saved pictures still qualified.",
            )

    def validate_loaded_picture_list_with_progress(
        self,
        document: PictureListDocument,
    ) -> tuple[list[Path], dict[Path, str], int, int, int, int, int]:
        """Validate saved paths while showing progress.

        Returns:
            paths, statuses, skipped_missing, skipped_unsupported,
            skipped_without_metadata_date, metadata_date_count, read_error_count
        """
        entries = document.entries
        total = len(entries)
        paths: list[Path] = []
        statuses: dict[Path, str] = {}
        skipped_missing = 0
        skipped_unsupported = 0
        skipped_without_metadata_date = 0
        metadata_date_count = 0
        read_error_count = 0
        seen: set[Path] = set()
        candidates: list[tuple[Path, str]] = []

        self.set_scan_controls_running("Checking saved list paths...")
        self.progress_scan.setRange(0, max(total, 1))
        self.progress_scan.setValue(0)

        # First pass is cheap: de-duplicate exact paths and reject missing or
        # unsupported files. The expensive metadata-date check is threaded below.
        for index, entry in enumerate(entries, start=1):
            self._handle_pause_or_cancel()
            path = entry.path.expanduser().resolve()
            text = (
                f"Checking saved list paths {index}/{total} · "
                f"{skipped_missing} missing · {skipped_unsupported} unsupported"
            )
            self.progress_scan.setValue(index - 1)
            self.progress_scan.setFormat(text)
            self.progress_scan.setToolTip(str(path))
            self.set_stats_text(
                self.format_statistics_text(
                    walked_over=index,
                    metadata_date_count=metadata_date_count,
                    duplicate_file_count=None,
                    unique_count=None,
                    read_error_count=read_error_count,
                    skipped_without_metadata_date=skipped_without_metadata_date,
                )
            )
            self.message(text, 0)

            if path in seen:
                # Duplicate saved row. Keep first occurrence only.
                pass
            elif not path.is_file():
                skipped_missing += 1
                read_error_count += 1
                self.add_warning_error_messages([f"{path}: Saved list entry is missing or no longer a file."])
            elif not is_image_file(path):
                skipped_unsupported += 1
                read_error_count += 1
                self.add_warning_error_messages([f"{path}: Saved list entry is not a supported image file."])
            else:
                seen.add(path)
                candidates.append((path, entry.status))

            self.progress_scan.setValue(index)
            self._handle_pause_or_cancel()

        if not self.require_metadata_date:
            for path, status in candidates:
                paths.append(path)
                statuses[path] = status
                metadata_date_count += 1
        else:
            candidate_paths = [path for path, _status in candidates]
            status_by_candidate = {path: status for path, status in candidates}

            def validate_progress(done: int, worker_total: int, current_path: Path | None, phase: str) -> None:
                self._handle_pause_or_cancel()
                self.progress_scan.setRange(0, max(worker_total, 1))
                self.progress_scan.setValue(done)
                text = (
                    f"Checking saved list metadata {done}/{worker_total} · "
                    f"{metadata_date_count} had metadata date · "
                    f"{skipped_missing} missing · {skipped_unsupported} unsupported · "
                    f"{skipped_without_metadata_date} without metadata date · "
                    f"{read_error_count} read warning/error(s) · "
                    f"{self.metadata_worker_count} thread(s)"
                )
                self.progress_scan.setFormat(text)
                if current_path is not None:
                    self.progress_scan.setToolTip(str(current_path))
                self.set_stats_text(
                    self.format_statistics_text(
                        walked_over=total,
                        metadata_date_count=metadata_date_count,
                        duplicate_file_count=None,
                        unique_count=None,
                        read_error_count=read_error_count,
                        skipped_without_metadata_date=skipped_without_metadata_date,
                    )
                )
                self.message(text, 0)
                self._handle_pause_or_cancel()

            results = run_path_work_threaded(
                candidate_paths,
                check_supported_metadata_date,
                max_workers=self.metadata_worker_count,
                progress_callback=validate_progress,
                phase="Checking saved list metadata",
            )

            accepted: set[Path] = set()
            for path, result in results:
                if isinstance(result, BaseException):
                    read_error_count += 1
                    skipped_without_metadata_date += 1
                    self.add_warning_error_messages([
                        f"{path}: Could not read metadata date in background thread: {result}"
                    ])
                    continue

                read_error_count += result.error_count
                self.add_warning_error_messages([f"{path}: {message}" for message in result.errors])
                if not result.has_metadata_date:
                    skipped_without_metadata_date += 1
                else:
                    metadata_date_count += 1
                    accepted.add(path)

            # Keep the saved-list order even though metadata was checked in
            # parallel and completed out of order.
            for path in candidate_paths:
                if path in accepted:
                    paths.append(path)
                    statuses[path] = status_by_candidate.get(path, "")

        final_text = (
            f"Checked saved list {total}/{total} · "
            f"{metadata_date_count} had metadata date · "
            f"{skipped_missing} missing · {skipped_unsupported} unsupported · "
            f"{skipped_without_metadata_date} without metadata date · "
            f"{read_error_count} read warning/error(s)"
        )
        self.progress_scan.setFormat(final_text)
        self.message(final_text, 0)

        return (
            paths,
            statuses,
            skipped_missing,
            skipped_unsupported,
            skipped_without_metadata_date,
            metadata_date_count,
            read_error_count,
        )

    def create_picture_items_with_progress(
        self,
        paths: list[Path],
        statuses: dict[Path, str] | None = None,
    ) -> list[QTreeWidgetItem]:
        """Create table rows for the current list mode.

        Normal load/append/load-list uses a flat table. Pressing the Search
        duplicates button switches to duplicate-group view, where rows are
        grouped by identical embedded metadata date.
        """
        if self.duplicate_view_enabled:
            return self.create_grouped_picture_items_with_progress(paths, statuses)
        return self.create_flat_picture_items_with_progress(paths, statuses)

    def create_flat_picture_items_with_progress(
        self,
        paths: list[Path],
        statuses: dict[Path, str] | None = None,
    ) -> list[QTreeWidgetItem]:
        """Create a normal, ungrouped table while showing progress."""
        statuses = statuses if statuses is not None else {}
        total = len(paths)
        items: list[QTreeWidgetItem] = []

        if total == 0:
            self.update_list_progress(0, 0, None, "Preparing picture rows")
            return items

        self.set_scan_controls_running("Preparing picture rows...")
        self.progress_scan.setRange(0, total)
        self.progress_scan.setValue(0)

        for index, path in enumerate(paths, start=1):
            self.update_list_progress(index - 1, total, path, "Preparing picture rows")
            items.append(
                self.create_picture_row_item(
                    path,
                    statuses.get(path, ""),
                    checkable=False,
                    group_key="",
                )
            )
            self.update_list_progress(index, total, path, "Preparing picture rows")

        return items

    def build_metadata_date_groups_with_progress(
        self,
        paths: list[Path],
        statuses: dict[Path, str] | None = None,
    ) -> tuple[list[MetadataDateGroup], list[Path], list[Path]]:
        """Find duplicate metadata-date groups with threaded metadata reads.

        Threads read the metadata-date key for each image, but the actual
        grouping is done after all thread results are merged. That means an
        image processed by worker thread 1 can still be grouped with an image
        processed by worker thread 8 if the embedded metadata date matches.
        """
        del statuses  # marks do not affect duplicate grouping

        self.set_scan_controls_running("Searching duplicate metadata dates in parallel...")
        duplicate_groups, unique_paths, warnings = build_metadata_date_groups_threaded(
            paths,
            max_workers=self.metadata_worker_count,
            progress_callback=self.update_threaded_metadata_progress,
            phase="Searching duplicate metadata dates in parallel",
        )
        self.add_warning_error_messages(warnings)

        # The third return value is kept for compatibility with older controller
        # code, but it is intentionally empty because marked-for-deletion rows
        # are no longer split into a separate section.
        return duplicate_groups, unique_paths, []

    def protect_duplicate_groups_from_all_delete(
        self,
        duplicate_groups: list[MetadataDateGroup],
        statuses: dict[Path, str],
    ) -> int:
        """Make sure every duplicate group keeps at least one unmarked file."""
        corrected = 0
        for group in duplicate_groups:
            if group.paths and all(statuses.get(path) == "delete" for path in group.paths):
                statuses[group.paths[0]] = "keep"
                corrected += 1
        return corrected

    def create_grouped_picture_items_with_progress(
        self,
        paths: list[Path],
        statuses: dict[Path, str] | None = None,
        duplicate_groups: list[MetadataDateGroup] | None = None,
        unique_paths: list[Path] | None = None,
        marked_delete_paths: list[Path] | None = None,
    ) -> list[QTreeWidgetItem]:
        """Create duplicate-group table items while showing progress.

        Possible duplicates are grouped by identical embedded metadata date.
        Each duplicate row gets a checkbox; checking it marks that file for
        deletion. Unique rows are kept visible in a separate section so nothing
        silently disappears from the list.
        """
        statuses = statuses if statuses is not None else {}

        if duplicate_groups is None or unique_paths is None or marked_delete_paths is None:
            duplicate_groups, unique_paths, marked_delete_paths = self.build_metadata_date_groups_with_progress(
                paths, statuses
            )
            corrected_groups = self.protect_duplicate_groups_from_all_delete(duplicate_groups, statuses)
        else:
            corrected_groups = self.protect_duplicate_groups_from_all_delete(duplicate_groups, statuses)

        if corrected_groups:
            self.message(
                f"Protected {corrected_groups} duplicate group(s) from having every file marked for deletion.",
                7000,
            )

        total = len(paths)
        items: list[QTreeWidgetItem] = []

        if total == 0:
            self.update_list_progress(0, 0, None, "Preparing duplicate groups")
            return items

        self.set_scan_controls_running("Preparing duplicate groups...")
        self.progress_scan.setRange(0, total)
        self.progress_scan.setValue(0)

        done = 0
        for group_index, group in enumerate(duplicate_groups):
            group_item = self.create_duplicate_group_header_item(group, statuses)
            for path in group.paths:
                self.update_list_progress(done, total, path, "Preparing duplicate rows")
                child = self.create_picture_row_item(
                    path,
                    statuses.get(path, ""),
                    checkable=True,
                    group_key=group.key,
                )
                group_item.addChild(child)
                done += 1
                self.update_list_progress(done, total, path, "Preparing duplicate rows")
            group_item.setExpanded(True)
            items.append(group_item)
            if group_index < len(duplicate_groups) - 1 or unique_paths:
                items.append(self.create_separator_item())

        if unique_paths:
            if duplicate_groups:
                unique_title = f"Unique images — {len(unique_paths)} image(s) with no matching metadata date"
            else:
                unique_title = (
                    f"No duplicate metadata-date groups found — {len(unique_paths)} unique image(s)"
                )
            unique_item = self.create_section_header_item(unique_title, GROUP_KIND_UNIQUE_HEADER)
            unique_item.setToolTip(
                PictureColumn.NAME,
                "These images have embedded metadata dates, but no other image in the current list has the same date.",
            )
            for path in unique_paths:
                self.update_list_progress(done, total, path, "Preparing unique rows")
                child = self.create_picture_row_item(
                    path,
                    statuses.get(path, ""),
                    checkable=False,
                    group_key="",
                )
                unique_item.addChild(child)
                done += 1
                self.update_list_progress(done, total, path, "Preparing unique rows")
            unique_item.setExpanded(True)
            items.append(unique_item)

        if marked_delete_paths:
            if items:
                items.append(self.create_separator_item())
            marked_item = self.create_section_header_item(
                f"Marked for deletion — {len(marked_delete_paths)} image(s)",
                GROUP_KIND_MARKED_HEADER,
            )
            marked_item.setToolTip(
                PictureColumn.NAME,
                "These images are saved in the list as marked for deletion. Uncheck one to make it active again.",
            )
            for path in marked_delete_paths:
                self.update_list_progress(done, total, path, "Preparing marked rows")
                child = self.create_picture_row_item(
                    path,
                    statuses.get(path, "delete"),
                    checkable=True,
                    group_key="",
                    group_kind=GROUP_KIND_MARKED_CHILD,
                )
                marked_item.addChild(child)
                done += 1
                self.update_list_progress(done, total, path, "Preparing marked rows")
            marked_item.setExpanded(True)
            items.append(marked_item)

        return items

    def create_separator_item(self) -> QTreeWidgetItem:
        """Create a simple visual spacer between duplicate groups."""
        item = QTreeWidgetItem()
        item.setText(PictureColumn.NAME, "─" * 80)
        item.setData(PictureColumn.NAME, GROUP_KIND_ROLE, "separator")
        item.setFlags(Qt.ItemFlag.ItemIsEnabled)
        item.setFirstColumnSpanned(True)
        return item

    def create_section_header_item(self, title: str, group_kind: str) -> QTreeWidgetItem:
        """Create a non-selectable top-level section/group header row."""
        item = QTreeWidgetItem()
        item.setText(PictureColumn.NAME, title)
        item.setData(PictureColumn.NAME, GROUP_KIND_ROLE, group_kind)
        item.setFlags(Qt.ItemFlag.ItemIsEnabled)
        item.setFirstColumnSpanned(True)
        font = QFont()
        font.setBold(True)
        item.setFont(PictureColumn.NAME, font)
        return item

    def create_duplicate_group_header_item(
        self,
        group: MetadataDateGroup,
        statuses: dict[Path, str],
    ) -> QTreeWidgetItem:
        """Create a duplicate-group header row."""
        item = self.create_section_header_item("", GROUP_KIND_DUPLICATE_HEADER)
        item.setData(PictureColumn.NAME, GROUP_KEY_ROLE, group.key)
        item.setData(PictureColumn.NAME, PATH_ROLE, "")
        item.setToolTip(
            PictureColumn.NAME,
            "Possible duplicates: every file in this group has the same embedded metadata date.",
        )
        self.update_duplicate_group_header_text(item, group.paths, group.display_date, statuses)
        return item

    def create_picture_row_item(
        self,
        path: Path,
        status: str,
        checkable: bool,
        group_key: str,
        group_kind: str | None = None,
    ) -> QTreeWidgetItem:
        """Create one visible image row."""
        item = QTreeWidgetItem()
        item.setData(PictureColumn.NAME, PATH_ROLE, str(path))
        item.setData(PictureColumn.NAME, STATUS_ROLE, status)
        if group_kind is None:
            group_kind = GROUP_KIND_DUPLICATE_CHILD if checkable else GROUP_KIND_UNIQUE_CHILD
        item.setData(PictureColumn.NAME, GROUP_KIND_ROLE, group_kind)
        item.setData(PictureColumn.NAME, GROUP_KEY_ROLE, group_key)

        flags = item.flags() | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled
        if checkable:
            flags |= Qt.ItemFlag.ItemIsUserCheckable
        item.setFlags(flags)

        self.update_item_text(item, path, status)
        if checkable and status != "keep":
            self.updating_item_state = True
            try:
                item.setCheckState(
                    PictureColumn.NAME,
                    Qt.CheckState.Checked if status == "delete" else Qt.CheckState.Unchecked,
                )
            finally:
                self.updating_item_state = False
        return item

    def update_duplicate_group_header_text(
        self,
        group_item: QTreeWidgetItem,
        group_paths: list[Path],
        display_date: str | None = None,
        statuses: dict[Path, str] | None = None,
    ) -> None:
        """Update duplicate header text, including deletion-mark count."""
        total = len(group_paths)
        active_statuses = self.status_by_path if statuses is None else statuses
        delete_count = duplicate_group_delete_count(group_paths, active_statuses)
        if display_date is None:
            display_date = "?"
            if group_paths:
                # Read the date from the first child row summary through the
                # grouping module's already-cached metadata helpers.
                duplicate_groups, _unique = build_metadata_date_groups(group_paths)
                if duplicate_groups:
                    display_date = duplicate_groups[0].display_date
        group_item.setText(
            PictureColumn.NAME,
            f"Duplicates — {display_date} — {total} files · {delete_count}/{total} marked for deletion",
        )

    def update_parent_duplicate_header(self, child_item: QTreeWidgetItem) -> None:
        """Refresh the parent header after a child check/status changes."""
        parent = child_item.parent()
        if parent is None:
            return
        if parent.data(PictureColumn.NAME, GROUP_KIND_ROLE) != GROUP_KIND_DUPLICATE_HEADER:
            return

        group_paths: list[Path] = []
        for index in range(parent.childCount()):
            child = parent.child(index)
            path_text = child.data(PictureColumn.NAME, PATH_ROLE)
            if path_text:
                group_paths.append(Path(path_text))
        self.update_duplicate_group_header_text(parent, group_paths)

    def duplicate_group_children(self, item: QTreeWidgetItem) -> list[QTreeWidgetItem]:
        """Return all sibling image rows in the same duplicate group."""
        parent = item.parent()
        if parent is None:
            return []
        if parent.data(PictureColumn.NAME, GROUP_KIND_ROLE) != GROUP_KIND_DUPLICATE_HEADER:
            return []
        return [parent.child(index) for index in range(parent.childCount())]

    def can_mark_path_for_deletion(self, path: Path) -> bool:
        """Return False if this would mark every image in a duplicate set.

        The safety rule is based on embedded metadata date across the whole
        loaded list, not only on the rows currently visible under one header.
        A mark-for-deletion is only preparation; it does not remove the image
        from the duplicate group.
        """
        key = metadata_date_group_key(path)
        if not key:
            return True

        same_date_paths = [
            other_path
            for other_path in self.picture_paths
            if metadata_date_group_key(other_path) == key
        ]

        # A truly unique image may still be marked manually. The "keep one"
        # guard only applies when the list contains more than one image with
        # the same embedded metadata date.
        if len(same_date_paths) <= 1:
            return True

        active_others = [
            other_path
            for other_path in same_date_paths
            if other_path != path and self.status_by_path.get(other_path) != "delete"
        ]
        return bool(active_others)

    def can_mark_item_for_deletion(self, item: QTreeWidgetItem) -> bool:
        """Return False if marking this row would delete an entire duplicate set."""
        path_text = item.data(PictureColumn.NAME, PATH_ROLE)
        if not path_text:
            return True
        return self.can_mark_path_for_deletion(Path(path_text))

    def on_picture_item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        """Handle checkbox clicks in duplicate/marked-for-deletion sections."""
        if self.updating_item_state:
            return
        if column != int(PictureColumn.NAME):
            return

        group_kind = item.data(PictureColumn.NAME, GROUP_KIND_ROLE)
        if group_kind not in {GROUP_KIND_DUPLICATE_CHILD, GROUP_KIND_MARKED_CHILD}:
            return

        path_text = item.data(PictureColumn.NAME, PATH_ROLE)
        if not path_text:
            return
        path = Path(path_text)

        checked = item.checkState(PictureColumn.NAME) == Qt.CheckState.Checked
        new_status = "delete" if checked else ""
        if not self.set_path_status(path, new_status, show_dialogs=True):
            self.updating_item_state = True
            try:
                item.setCheckState(
                    PictureColumn.NAME,
                    Qt.CheckState.Checked
                    if self.status_by_path.get(path) == "delete"
                    else Qt.CheckState.Unchecked,
                )
            finally:
                self.updating_item_state = False
            return

        # Do not regroup just because a file was marked for deletion.
        # Marking is only preparation. A duplicate group should change to Unique
        # only after files are actually removed/deleted from the active list.

    def refresh_duplicate_group_view_after_status_change(self, preferred_path: Path | None = None) -> None:
        """Rebuild duplicate sections after the active file list changed.

        This is meant for a future actual delete/remove operation, not for a
        simple mark-for-deletion checkbox change. Marked files stay in their
        duplicate groups until they are really removed from ``self.picture_paths``.
        """
        if not self.duplicate_view_enabled or self.scan_is_running:
            return

        old_locked_path = self.locked_path
        statuses = dict(self.status_by_path)

        try:
            duplicate_groups, unique_paths, marked_delete_paths = self.build_metadata_date_groups_with_progress(
                list(self.picture_paths),
                statuses,
            )
            items = self.create_grouped_picture_items_with_progress(
                list(self.picture_paths),
                statuses,
                duplicate_groups=duplicate_groups,
                unique_paths=unique_paths,
                marked_delete_paths=marked_delete_paths,
            )
        except ScanCanceled:
            self.set_scan_controls_idle()
            self.message("Duplicate regroup canceled. Press Search duplicates to rebuild the grouped view.")
            return
        finally:
            self.scan_is_paused = False

        self.status_by_path = {
            path: statuses.get(path, "")
            for path in self.picture_paths
            if statuses.get(path, "")
        }
        self.locked_path = old_locked_path if old_locked_path in self.picture_paths else None
        self.current_path = None

        self.replace_picture_table_with_progress(items)
        self.refresh_locked_picture_with_progress()

        selected_item = self.find_item_for_path_with_progress(preferred_path)
        if selected_item is None and self.picture_paths:
            selected_item = self.first_picture_item()
        self.set_current_item_with_progress(
            selected_item,
            "Refreshing duplicate groups after active list change...",
        )
        self.set_scan_controls_idle()

    def on_picture_selected(
        self,
        current: QTreeWidgetItem | None,
        previous: QTreeWidgetItem | None = None,
    ) -> None:
        del previous
        if current is None:
            self.current_path = None
            clear_label(self.label_selected, "No selected image")
            self.text_selected.setPlainText("")
            self.refresh_fullscreen_window_if_open()
            return

        path_text = current.data(PictureColumn.NAME, PATH_ROLE)
        if not path_text:
            self.current_path = None
            self.refresh_fullscreen_window_if_open()
            return

        path = Path(path_text)
        self.current_path = path
        show_picture_on_label(path, self.label_selected)
        self.text_selected.setPlainText(build_info_text(path))
        self.refresh_fullscreen_window_if_open()
        self.message(f"Selected: {path.name} — {path.parent}")

    def lock_selected_picture(self) -> None:
        if self.current_path is None:
            QMessageBox.information(self.window, "No picture selected", "Select a picture first.")
            return

        self.locked_path = self.current_path
        show_picture_on_label(self.locked_path, self.label_locked)
        self.text_locked.setPlainText(build_info_text(self.locked_path))
        self.refresh_fullscreen_window_if_open()
        self.message(f"Locked default picture: {self.locked_path.name}")

    def clear_locked_picture(self) -> None:
        self.locked_path = None
        clear_label(self.label_locked, "No locked image")
        self.text_locked.setPlainText("")
        self.refresh_fullscreen_window_if_open()
        self.message("Locked picture cleared.")

    def create_picture_items_after_actual_delete(
        self,
        paths: list[Path],
        statuses: dict[Path, str],
    ) -> list[QTreeWidgetItem]:
        """Create table rows after files were actually removed from the list.

        This phase is deliberately non-cancelable.  The file move to Trash has
        already happened, so leaving the old tree visible with deleted paths
        would be misleading.
        """
        if not self.duplicate_view_enabled:
            return self.create_flat_picture_items_after_actual_delete(paths, statuses)
        return self.create_grouped_picture_items_after_actual_delete(paths, statuses)

    def create_flat_picture_items_after_actual_delete(
        self,
        paths: list[Path],
        statuses: dict[Path, str],
    ) -> list[QTreeWidgetItem]:
        total = len(paths)
        items: list[QTreeWidgetItem] = []
        self.begin_non_cancelable_final_phase("Preparing list after trash move...")
        for index, path in enumerate(paths, start=1):
            self.update_non_cancelable_progress(
                index - 1,
                total,
                f"Preparing list after trash move {index}/{total} picture(s)...",
                path,
            )
            items.append(
                self.create_picture_row_item(
                    path,
                    statuses.get(path, ""),
                    checkable=False,
                    group_key="",
                    group_kind=GROUP_KIND_UNIQUE_CHILD,
                )
            )
            self.update_non_cancelable_progress(
                index,
                total,
                f"Preparing list after trash move {index}/{total} picture(s)...",
                path,
            )
        return items

    def build_metadata_date_groups_after_actual_delete(
        self,
        paths: list[Path],
    ) -> tuple[list[MetadataDateGroup], list[Path]]:
        """Group remaining paths after actual deletion with non-cancelable progress."""
        total = len(paths)
        grouped: "OrderedDict[str, list[Path]]" = OrderedDict()
        display_dates: dict[str, str] = {}
        no_date_paths: list[Path] = []

        self.begin_non_cancelable_final_phase("Regrouping remaining images...")
        for index, path in enumerate(paths, start=1):
            self.update_non_cancelable_progress(
                index - 1,
                total,
                f"Regrouping remaining images {index}/{total} picture(s)...",
                path,
            )
            key = metadata_date_group_key(path)
            if key:
                grouped.setdefault(key, []).append(path)
                display_dates.setdefault(key, metadata_date_display_text(path))
            else:
                no_date_paths.append(path)
            self.update_non_cancelable_progress(
                index,
                total,
                f"Regrouping remaining images {index}/{total} picture(s)...",
                path,
            )

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

    def create_grouped_picture_items_after_actual_delete(
        self,
        paths: list[Path],
        statuses: dict[Path, str],
    ) -> list[QTreeWidgetItem]:
        """Create duplicate-group rows after actual deletion.

        Because deleted files are removed from *paths*, any old duplicate group
        with only one remaining image automatically becomes a Unique images row.
        """
        duplicate_groups, unique_paths = self.build_metadata_date_groups_after_actual_delete(paths)
        self.protect_duplicate_groups_from_all_delete(duplicate_groups, statuses)

        total = len(paths)
        items: list[QTreeWidgetItem] = []
        done = 0
        self.begin_non_cancelable_final_phase("Preparing duplicate groups after trash move...")

        for group_index, group in enumerate(duplicate_groups):
            group_item = self.create_duplicate_group_header_item(group, statuses)
            for path in group.paths:
                self.update_non_cancelable_progress(
                    done,
                    total,
                    f"Preparing duplicate groups after trash move {done}/{total} picture(s)...",
                    path,
                )
                child = self.create_picture_row_item(
                    path,
                    statuses.get(path, ""),
                    checkable=True,
                    group_key=group.key,
                )
                group_item.addChild(child)
                done += 1
            group_item.setExpanded(True)
            items.append(group_item)
            if group_index < len(duplicate_groups) - 1 or unique_paths:
                items.append(self.create_separator_item())

        if unique_paths:
            title = f"Unique images — {len(unique_paths)} image(s) with no matching metadata date"
            if not duplicate_groups:
                title = f"No duplicate metadata-date groups found — {len(unique_paths)} unique image(s)"
            unique_item = self.create_section_header_item(title, GROUP_KIND_UNIQUE_HEADER)
            for path in unique_paths:
                self.update_non_cancelable_progress(
                    done,
                    total,
                    f"Preparing unique images after trash move {done}/{total} picture(s)...",
                    path,
                )
                child = self.create_picture_row_item(
                    path,
                    statuses.get(path, ""),
                    checkable=False,
                    group_key="",
                    group_kind=GROUP_KIND_UNIQUE_CHILD,
                )
                unique_item.addChild(child)
                done += 1
            unique_item.setExpanded(True)
            items.append(unique_item)

        self.update_non_cancelable_progress(total, total, "Finished preparing list after trash move.")
        return items

    def move_marked_pictures_to_trash(self) -> None:
        """Move all pictures marked for deletion to the system Trash."""
        if self.scan_is_running:
            return

        marked_paths = self.marked_for_deletion_paths()
        if not marked_paths:
            QMessageBox.information(
                self.window,
                "No pictures marked for deletion",
                "Mark one or more pictures for deletion first.",
            )
            return

        preview_names = "\n".join(f"• {path.name}" for path in marked_paths[:12])
        if len(marked_paths) > 12:
            preview_names += f"\n• ...and {len(marked_paths) - 12} more"

        reply = QMessageBox.question(
            self.window,
            "Move marked pictures to Trash?",
            (
                f"Move {len(marked_paths)} picture(s) marked for deletion to the system Trash?\n\n"
                f"{preview_names}\n\n"
                "This does not permanently delete the files, but it removes them from this list."
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        old_paths = list(self.picture_paths)
        old_statuses = dict(self.status_by_path)
        old_locked_path = self.locked_path
        old_current_path = self.current_path

        successes: list[Path] = []
        failures: list[str] = []

        self.begin_non_cancelable_final_phase("Moving marked pictures to Trash...")
        total = len(marked_paths)
        for index, path in enumerate(marked_paths, start=1):
            self.update_non_cancelable_progress(
                index - 1,
                total,
                f"Moving marked picture {index}/{total} to Trash...",
                path,
            )
            result = move_path_to_trash(path)
            if result.success:
                successes.append(path)
            else:
                failures.append(f"{path}: {result.message}")
            self.update_non_cancelable_progress(
                index,
                total,
                f"Moving marked picture {index}/{total} to Trash...",
                path,
            )

        if failures:
            self.add_warning_error_messages([f"Trash move failed: {message}" for message in failures])

        if not successes:
            self.set_scan_controls_idle()
            QMessageBox.warning(
                self.window,
                "Could not move pictures to Trash",
                "None of the marked pictures could be moved to Trash.\n\n"
                "Open Files > Show warnings/errors... for details.",
            )
            return

        deleted_set = set(successes)
        remaining_paths = [path for path in old_paths if path not in deleted_set]
        remaining_statuses = {
            path: status
            for path, status in old_statuses.items()
            if path in remaining_paths and status
        }

        self.picture_paths = remaining_paths
        self.status_by_path = remaining_statuses
        self.locked_path = old_locked_path if old_locked_path in remaining_paths else None
        self.current_path = old_current_path if old_current_path in remaining_paths else None

        items = self.create_picture_items_after_actual_delete(self.picture_paths, self.status_by_path)
        self.replace_picture_table_with_progress(items)

        if self.locked_path is None:
            clear_label(self.label_locked, "No locked image")
            self.text_locked.setPlainText("")
        else:
            self.refresh_locked_picture_with_progress()

        preferred_path = self.current_path
        selected_item = self.find_item_for_path_with_progress(preferred_path)
        if selected_item is None and self.picture_paths:
            selected_item = self.first_picture_item()
        self.set_current_item_with_progress(
            selected_item,
            "Loading picture after trash move...",
        )

        if not self.picture_paths:
            self.current_path = None
            clear_label(self.label_selected, "No selected image")
            self.text_selected.setPlainText("")

        self.update_persistent_statistics(
            metadata_date_count=len(self.picture_paths),
            read_error_count=self.last_read_error_count,
        )
        self.update_move_to_trash_button_state()
        self.refresh_fullscreen_window_if_open()
        self.set_scan_controls_idle()
        self.show_persistent_statistics_in_statusbar()

        message = f"Moved {len(successes)} picture(s) to Trash."
        if failures:
            message += f" {len(failures)} picture(s) failed; see warnings/errors."
        self.show_temporary_message_then_statistics(message, 6000)

    def set_selected_status(self, status: str) -> None:
        """Apply a keep/delete mark to the currently selected image."""
        if self.current_path is None:
            QMessageBox.information(self.window, "No picture selected", "Select a picture first.")
            return
        self.set_path_status(self.current_path, status, show_dialogs=True)

    def clear_keep_for_selected_picture(self) -> None:
        """Clear only the keep mark from the currently selected image.

        This is intentionally not a general "clear all marks" button: it is
        for unlocking a kept picture so it can be changed again. Deletion marks
        should still be controlled by the delete checkbox / mark-for-deletion
        workflow.
        """
        if self.current_path is None:
            QMessageBox.information(self.window, "No picture selected", "Select a picture first.")
            return

        if self.status_by_path.get(self.current_path) != "keep":
            QMessageBox.information(
                self.window,
                "Picture is not kept",
                "The selected picture is not marked as keep.",
            )
            return

        self.set_path_status(self.current_path, "", show_dialogs=True)

    def set_path_status_from_fullscreen(self, path: Path, status: str) -> bool:
        """Status callback used by the fullscreen image window.

        The fullscreen window already knows which exact picture is open. Avoid
        forcing a fullscreen refresh from inside this callback, because that can
        re-read the main-window Default/Checked roles while the user is pressing
        Keep/Delete.
        """
        return self.set_path_status(
            path,
            status,
            show_dialogs=True,
            refresh_fullscreen=False,
        )

    def set_path_status(
        self,
        path: Path,
        status: str,
        show_dialogs: bool = True,
        refresh_fullscreen: bool = True,
    ) -> bool:
        """Set keep/delete/clear status for any path in the current list.

        The same code is used by the main Keep/Delete buttons, duplicate row
        checkboxes, and the fullscreen window. A picture marked as ``keep`` is
        greyed out in the list and cannot be marked for deletion until its mark
        is cleared.
        """
        if status not in {"keep", "delete", ""}:
            raise ValueError(f"Unknown status: {status}")

        path = Path(path)
        if path not in self.picture_paths:
            if show_dialogs:
                QMessageBox.information(self.window, "Picture not in list", "That picture is no longer in the current list.")
            return False

        old_status = self.status_by_path.get(path, "")

        if status == "delete" and old_status == "keep":
            if show_dialogs:
                QMessageBox.information(
                    self.window,
                    "Picture is marked to keep",
                    "This picture is marked to keep and cannot be marked for deletion.\n\n"
                    "Clear the keep mark first if you want to change it.",
                )
            return False

        if status == "delete" and not self.can_mark_path_for_deletion(path):
            if show_dialogs:
                QMessageBox.information(
                    self.window,
                    "Keep one image in the duplicate group",
                    "At least one image in each duplicate group must stay unmarked.\n\n"
                    "Choose one file to keep before marking the others for deletion.",
                )
            return False

        if status:
            self.status_by_path[path] = status
        else:
            self.status_by_path.pop(path, None)

        self.update_visible_items_for_path(path, status)
        self.update_move_to_trash_button_state()
        if refresh_fullscreen:
            self.refresh_fullscreen_window_if_open()
        self.message(f"Marked {path.name} as {status or 'unmarked'}.")
        return True

    def update_visible_items_for_path(self, path: Path, status: str) -> None:
        """Update every visible row for *path* after a keep/delete status change."""
        wanted = str(path)
        self.updating_item_state = True
        try:
            for item in self.iter_picture_items():
                if item.data(PictureColumn.NAME, PATH_ROLE) != wanted:
                    continue
                item.setData(PictureColumn.NAME, STATUS_ROLE, status)
                self.update_item_text(item, path, status)

                group_kind = item.data(PictureColumn.NAME, GROUP_KIND_ROLE)
                if group_kind in {GROUP_KIND_DUPLICATE_CHILD, GROUP_KIND_MARKED_CHILD}:
                    if status == "keep":
                        item.setCheckState(PictureColumn.NAME, Qt.CheckState.Unchecked)
                    elif item.flags() & Qt.ItemFlag.ItemIsUserCheckable:
                        item.setCheckState(
                            PictureColumn.NAME,
                            Qt.CheckState.Checked if status == "delete" else Qt.CheckState.Unchecked,
                        )
                self.update_parent_duplicate_header(item)
        finally:
            self.updating_item_state = False

    def update_item_text(self, item: QTreeWidgetItem, path: Path, status: str) -> None:
        values = row_values_for_path(path, status)
        for column, value in enumerate(values):
            item.setText(column, value)
            item.setToolTip(column, str(path))

        # Right-align numeric-ish columns for easier scanning.
        for column in (PictureColumn.WIDTH_HEIGHT, PictureColumn.SIZE):
            item.setTextAlignment(column, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        self.apply_status_visuals_to_item(item, status)

    def apply_status_visuals_to_item(self, item: QTreeWidgetItem, status: str) -> None:
        """Grey kept rows and control whether a row can be checked for deletion."""
        group_kind = item.data(PictureColumn.NAME, GROUP_KIND_ROLE)
        can_have_delete_checkbox = group_kind in {GROUP_KIND_DUPLICATE_CHILD, GROUP_KIND_MARKED_CHILD}

        flags = Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled
        if can_have_delete_checkbox and status != "keep":
            flags |= Qt.ItemFlag.ItemIsUserCheckable
        item.setFlags(flags)

        font = item.font(PictureColumn.NAME)
        font.setItalic(status == "keep")

        if status == "keep":
            foreground = QBrush(QColor(130, 130, 130))
            background = QBrush(QColor(235, 235, 235))
        elif status == "delete":
            foreground = QBrush(QColor(150, 40, 40))
            background = QBrush()
        else:
            foreground = QBrush()
            background = QBrush()

        for column in range(self.list_pictures.columnCount()):
            item.setFont(column, font)
            item.setForeground(column, foreground)
            item.setBackground(column, background)
