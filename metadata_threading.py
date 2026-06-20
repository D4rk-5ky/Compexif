"""Threaded metadata helpers for sorting and duplicate grouping."""

from __future__ import annotations

from collections import OrderedDict
from pathlib import Path
from typing import Callable

from duplicate_grouping import MetadataDateGroup
from image_metadata import PictureFileSummary, get_picture_file_summary
from threaded_work import PathWorkProgressCallback, default_worker_count, run_path_work_threaded


def prefetch_picture_summaries_threaded(
    paths: list[Path],
    *,
    max_workers: int | None = None,
    progress_callback: PathWorkProgressCallback | None = None,
    phase: str = "Reading image details in parallel",
) -> list[str]:
    """Warm the image-summary cache using background threads.

    Sorting and row building both use ``get_picture_file_summary``. Prefetching
    it in parallel means the later sort/table phases can mostly use cached data.
    Returns warning/error text for any unexpected per-file failure.
    """
    unique_paths = list(dict.fromkeys(paths))

    def worker(path: Path) -> PictureFileSummary:
        return get_picture_file_summary(path)

    results = run_path_work_threaded(
        unique_paths,
        worker,
        max_workers=max_workers,
        progress_callback=progress_callback,
        phase=phase,
    )

    messages: list[str] = []
    for path, result in results:
        if isinstance(result, BaseException):
            messages.append(f"{path}: Could not read image summary in background thread: {result}")
    return messages


def _group_key_from_summary(summary: PictureFileSummary) -> tuple[str, str]:
    if summary.metadata_datetime is None:
        return "", ""
    key = summary.metadata_datetime.replace(microsecond=0).isoformat(sep=" ")
    return key, summary.metadata_date_text


def build_metadata_date_groups_threaded(
    paths: list[Path],
    *,
    max_workers: int | None = None,
    progress_callback: PathWorkProgressCallback | None = None,
    phase: str = "Searching duplicate metadata dates in parallel",
) -> tuple[list[MetadataDateGroup], list[Path], list[str]]:
    """Group images by embedded metadata date using threaded summary reads.

    Threads only read each file and return its metadata-date key. The final
    grouping is done on the caller/main thread, so duplicates are still found
    across all threads and across the whole list.
    """
    unique_paths = list(dict.fromkeys(paths))

    def worker(path: Path) -> tuple[str, str]:
        summary = get_picture_file_summary(path)
        return _group_key_from_summary(summary)

    results = run_path_work_threaded(
        unique_paths,
        worker,
        max_workers=max_workers,
        progress_callback=progress_callback,
        phase=phase,
    )

    keys_by_path: dict[Path, tuple[str, str]] = {}
    messages: list[str] = []
    for path, result in results:
        if isinstance(result, BaseException):
            keys_by_path[path] = ("", "")
            messages.append(f"{path}: Could not read metadata date in background thread: {result}")
        else:
            keys_by_path[path] = result

    grouped: "OrderedDict[str, list[Path]]" = OrderedDict()
    display_dates: dict[str, str] = {}
    no_date_paths: list[Path] = []

    # Iterate the original list order here. Completion order from the threads
    # does not decide grouping/order.
    for path in paths:
        key, display_date = keys_by_path.get(path, ("", ""))
        if not key:
            no_date_paths.append(path)
            continue
        grouped.setdefault(key, []).append(path)
        display_dates.setdefault(key, display_date or key)

    duplicate_groups: list[MetadataDateGroup] = []
    unique_paths_out: list[Path] = list(no_date_paths)

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
            unique_paths_out.extend(group_paths)

    return duplicate_groups, unique_paths_out, messages
