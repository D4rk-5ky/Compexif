"""Picture file discovery/loading.

This module decides what files are pictures and finds them on disk.
It does not display images or decide the display order.

By default it only returns pictures that contain a real embedded metadata date:
EXIF date, XMP date, IPTC/text date, PNG/WebP date text, etc.
Normal file info such as filename, size, modified date, and dimensions does not
count as metadata and does not make a file qualify.

The loader also supports progress reporting:
1. Count every file encountered in the requested folders/files.
2. Count supported image files that need metadata-date inspection.
3. Read metadata from those supported image files in background threads and report progress.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait

from image_metadata import MetadataDateCheckResult, check_supported_metadata_date, has_supported_metadata_date
from threaded_work import normalized_worker_count


IMAGE_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".jpe",
    ".tif",
    ".tiff",
    ".png",
    ".webp",
    ".bmp",
    ".gif",
}


@dataclass(frozen=True)
class PictureScanResult:
    """Result from a picture scan."""

    picture_paths: list[Path]
    total_files_seen: int
    candidate_image_count: int
    skipped_without_metadata_date: int
    metadata_date_count: int = 0
    read_error_count: int = 0
    warning_error_messages: list[str] | None = None

    @property
    def skipped_without_metadata(self) -> int:
        """Backwards-compatible name from the older metadata-only filter."""
        return self.skipped_without_metadata_date


# done, total candidate image files, current path, kept count, all files seen, metadata read warnings/errors
ProgressCallback = Callable[[int, int, Path | None, int, int, int], None]
WarningCallback = Callable[[list[str]], None]


def is_image_file(path: Path) -> bool:
    """Return True when *path* has a supported picture extension."""
    return path.suffix.lower() in IMAGE_EXTENSIONS


def is_loadable_picture(
    path: Path,
    require_metadata_date: bool = True,
    *,
    require_metadata: bool | None = None,
) -> bool:
    """Return True when a file should be loaded into the app.

    This is useful for checking one file. Folder scans should normally use
    ``load_picture_paths_with_progress`` because it avoids recounting work and
    can update the progress bar while metadata is read.

    ``require_metadata`` is kept as a compatibility alias. If supplied, it maps
    to ``require_metadata_date``.
    """
    if require_metadata is not None:
        require_metadata_date = require_metadata

    if not path.is_file() or not is_image_file(path):
        return False
    if not require_metadata_date:
        return True
    return has_supported_metadata_date(path)


def collect_candidate_image_files(
    paths: Iterable[Path],
    recursive: bool = True,
    progress_callback: ProgressCallback | None = None,
) -> tuple[list[Path], int]:
    """Return supported image files and the total number of files encountered.

    ``total_files_seen`` counts every normal file found under the requested
    paths, even files that are not images. This is used to tell the user how
    much the scan walked through.

    The returned candidate list only contains files with supported image
    extensions. These are the files that will be opened/read for metadata dates.
    """
    candidates: list[Path] = []
    total_files_seen = 0

    def report_count_progress() -> None:
        if progress_callback is not None:
            # total=0 tells the GUI that we are still in the counting phase.
            progress_callback(0, 0, None, 0, total_files_seen, 0)

    for input_path in paths:
        path = input_path.expanduser().resolve()

        if path.is_dir():
            if recursive:
                for root, _dirs, files in os.walk(path):
                    total_files_seen += len(files)
                    if total_files_seen == len(files) or total_files_seen % 100 == 0:
                        report_count_progress()
                    for filename in files:
                        candidate = (Path(root) / filename).resolve()
                        if is_image_file(candidate):
                            candidates.append(candidate)
            else:
                for child in path.iterdir():
                    if child.is_file():
                        total_files_seen += 1
                        if total_files_seen == 1 or total_files_seen % 100 == 0:
                            report_count_progress()
                        candidate = child.resolve()
                        if is_image_file(candidate):
                            candidates.append(candidate)
            continue

        if path.is_file():
            total_files_seen += 1
            report_count_progress()
            if is_image_file(path):
                candidates.append(path)

    report_count_progress()

    # De-duplicate while keeping discovery order.
    return list(dict.fromkeys(candidates)), total_files_seen


def iter_image_files(
    folder: Path,
    recursive: bool = True,
    require_metadata_date: bool = True,
    *,
    require_metadata: bool | None = None,
    max_workers: int | None = None,
) -> Iterable[Path]:
    """Yield picture files from *folder*.

    Args:
        folder: Folder to scan.
        recursive: When True, scan subfolders too.
        require_metadata_date: When True, only yield files with an embedded
            metadata date.
        require_metadata: Compatibility alias for require_metadata_date.
    """
    if require_metadata is not None:
        require_metadata_date = require_metadata

    result = load_picture_paths_with_progress(
        [folder],
        recursive=recursive,
        require_metadata_date=require_metadata_date,
        max_workers=max_workers,
    )
    yield from result.picture_paths


def load_picture_paths_with_progress(
    paths: Iterable[Path],
    recursive: bool = True,
    require_metadata_date: bool = True,
    progress_callback: ProgressCallback | None = None,
    warning_callback: WarningCallback | None = None,
    *,
    require_metadata: bool | None = None,
    max_workers: int | None = None,
) -> PictureScanResult:
    """Load qualifying picture files from files/folders with progress support.

    Sorting is intentionally not done here. The list sorting is handled by
    picture_sorting.py so it is easy to change later.

    If ``require_metadata_date`` is True, only supported image files containing
    a real embedded metadata date are returned. File info such as modified time
    does not count.

    ``require_metadata`` is kept as a compatibility alias from older versions.
    """
    if require_metadata is not None:
        require_metadata_date = require_metadata

    candidates, total_files_seen = collect_candidate_image_files(
        paths,
        recursive=recursive,
        progress_callback=progress_callback,
    )
    candidate_count = len(candidates)

    found: list[Path] = []
    skipped_without_metadata_date = 0
    metadata_date_count = 0
    read_error_count = 0
    warning_error_messages: list[str] = []

    def record_warning_errors(path: Path, messages: list[str]) -> None:
        if not messages:
            return
        formatted = [f"{path}: {message}" for message in messages]
        warning_error_messages.extend(formatted)
        if warning_callback is not None:
            warning_callback(formatted)

    if progress_callback is not None:
        progress_callback(0, candidate_count, None, 0, total_files_seen, read_error_count)

    if not require_metadata_date:
        found = list(candidates)
        metadata_date_count = len(found)
        for index, path in enumerate(candidates, start=1):
            if not path.is_file():
                skipped_without_metadata_date += 1
                read_error_count += 1
                record_warning_errors(path, ["File disappeared before it could be loaded."])
            if progress_callback is not None:
                progress_callback(
                    index,
                    candidate_count,
                    path,
                    len(found),
                    total_files_seen,
                    read_error_count,
                )
    else:
        found_set: set[Path] = set()
        worker_count = normalized_worker_count(max_workers, candidate_count)

        def check_worker(path: Path) -> MetadataDateCheckResult:
            return check_supported_metadata_date(path)

        executor: ThreadPoolExecutor | None = None
        pending: dict[Future[MetadataDateCheckResult], Path] = {}
        next_index = 0
        done_count = 0
        max_pending = max(worker_count, worker_count * 2)

        def submit_next() -> bool:
            nonlocal next_index
            if next_index >= candidate_count:
                return False
            path = candidates[next_index]
            pending[executor.submit(check_worker, path)] = path  # type: ignore[union-attr]
            next_index += 1
            return True

        try:
            executor = ThreadPoolExecutor(
                max_workers=worker_count,
                thread_name_prefix="compexif-scan",
            )
            for _ in range(min(candidate_count, max_pending)):
                submit_next()

            while pending:
                completed, _not_done = wait(
                    pending,
                    timeout=0.10,
                    return_when=FIRST_COMPLETED,
                )

                if not completed:
                    # Give the GUI a chance to process Pause/Cancel while worker
                    # threads are waiting on slow disks/NAS/image reads.
                    if progress_callback is not None:
                        progress_callback(
                            done_count,
                            candidate_count,
                            None,
                            metadata_date_count,
                            total_files_seen,
                            read_error_count,
                        )
                    continue

                for future in completed:
                    path = pending.pop(future)

                    if not path.is_file():
                        skipped_without_metadata_date += 1
                        read_error_count += 1
                        record_warning_errors(path, ["File disappeared before metadata could be read."])
                    else:
                        try:
                            metadata_check = future.result()
                        except Exception as exc:
                            metadata_check = MetadataDateCheckResult(
                                metadata_datetime=None,
                                error_count=1,
                                errors=(f"Could not read metadata date in background thread: {exc}",),
                            )

                        read_error_count += metadata_check.error_count
                        record_warning_errors(path, list(metadata_check.errors))
                        if metadata_check.has_metadata_date:
                            found_set.add(path)
                            metadata_date_count += 1
                        else:
                            skipped_without_metadata_date += 1

                    done_count += 1
                    if progress_callback is not None:
                        progress_callback(
                            done_count,
                            candidate_count,
                            path,
                            metadata_date_count,
                            total_files_seen,
                            read_error_count,
                        )

                    while len(pending) < max_pending and submit_next():
                        pass

        except BaseException:
            for future in pending:
                future.cancel()
            if executor is not None:
                executor.shutdown(wait=False, cancel_futures=True)
                executor = None
            raise
        finally:
            if executor is not None:
                executor.shutdown(wait=True)

        # Keep the original discovery order even though metadata was read by
        # many threads and completed out of order.
        found = [path for path in candidates if path in found_set]

    return PictureScanResult(
        picture_paths=list(dict.fromkeys(found)),
        total_files_seen=total_files_seen,
        candidate_image_count=candidate_count,
        skipped_without_metadata_date=skipped_without_metadata_date,
        metadata_date_count=metadata_date_count if require_metadata_date else len(found),
        read_error_count=read_error_count,
        warning_error_messages=warning_error_messages,
    )


def load_picture_paths(
    paths: Iterable[Path],
    recursive: bool = True,
    require_metadata_date: bool = True,
    *,
    require_metadata: bool | None = None,
    max_workers: int | None = None,
) -> list[Path]:
    """Load all qualifying picture files from files/folders and de-duplicate them.

    Kept as a simple compatibility wrapper for code that only wants a list.
    """
    if require_metadata is not None:
        require_metadata_date = require_metadata

    result = load_picture_paths_with_progress(
        paths,
        recursive=recursive,
        require_metadata_date=require_metadata_date,
        max_workers=max_workers,
    )
    return result.picture_paths
