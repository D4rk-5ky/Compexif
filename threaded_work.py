"""Small helpers for bounded background thread work.

Qt widgets must stay on the main thread, but slow file/metadata reads can run in
background Python threads. These helpers keep only a small number of pending
jobs so Pause/Cancel can still react reasonably quickly and so huge folders do
not submit thousands of futures at once.
"""

from __future__ import annotations

import os
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from pathlib import Path
from typing import Callable, TypeVar

T = TypeVar("T")
PathWorker = Callable[[Path], T]
PathWorkProgressCallback = Callable[[int, int, Path | None, str], None]


def available_cpu_thread_count() -> int:
    """Return the number of logical CPU threads available to this process."""
    try:
        # Linux can restrict a process to a subset of CPUs. Respect that when
        # available so the drop-down does not offer impossible worker counts.
        affinity = os.sched_getaffinity(0)  # type: ignore[attr-defined]
        if affinity:
            return max(1, len(affinity))
    except (AttributeError, OSError):
        pass

    return max(1, os.cpu_count() or 1)


def default_worker_count() -> int:
    """Return the default number of metadata worker threads.

    Can be overridden with the COMPEXIF_METADATA_WORKERS environment variable.
    The default is intentionally capped so spinning disks/NAS folders are not
    hammered too hard.
    """
    env_value = os.environ.get("COMPEXIF_METADATA_WORKERS", "").strip()
    if env_value:
        try:
            return max(1, int(env_value))
        except ValueError:
            pass

    return max(1, min(8, available_cpu_thread_count()))


def normalized_worker_count(max_workers: int | None, total_items: int | None = None) -> int:
    """Return a sane positive worker count."""
    workers = default_worker_count() if max_workers is None else int(max_workers)
    workers = max(1, workers)
    if total_items is not None and total_items > 0:
        workers = min(workers, total_items)
    return workers


def run_path_work_threaded(
    paths: list[Path],
    worker: PathWorker[T],
    *,
    max_workers: int | None = None,
    progress_callback: PathWorkProgressCallback | None = None,
    phase: str = "Working",
    max_pending_multiplier: int = 2,
) -> list[tuple[Path, T | BaseException]]:
    """Run path work in background threads and return results in input order.

    The progress callback is called on the caller/main thread whenever work
    completes, and also during short wait timeouts so GUI Pause/Cancel buttons
    can still be processed even when a single file takes a while.

    Worker exceptions are returned as result values instead of being raised, so
    callers can turn per-file failures into warning/error messages without
    stopping the whole scan.
    """
    total = len(paths)
    if total == 0:
        if progress_callback is not None:
            progress_callback(0, 0, None, phase)
        return []

    workers = normalized_worker_count(max_workers, total)

    # Keep the sequential path for explicit --metadata-workers 1. This is also
    # easier to debug when needed.
    if workers <= 1:
        results: list[tuple[Path, T | BaseException]] = []
        for index, path in enumerate(paths, start=1):
            try:
                result: T | BaseException = worker(path)
            except BaseException as exc:  # per-file failure, not global failure
                result = exc
            results.append((path, result))
            if progress_callback is not None:
                progress_callback(index, total, path, phase)
        return results

    executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="compexif-metadata")
    pending: dict[Future[T], tuple[int, Path]] = {}
    results_by_index: list[tuple[Path, T | BaseException] | None] = [None] * total
    next_index = 0
    done_count = 0
    max_pending = max(workers, workers * max(1, max_pending_multiplier))

    def submit_next() -> bool:
        nonlocal next_index
        if next_index >= total:
            return False
        index = next_index
        path = paths[index]
        future = executor.submit(worker, path)
        pending[future] = (index, path)
        next_index += 1
        return True

    try:
        for _ in range(min(total, max_pending)):
            submit_next()

        if progress_callback is not None:
            progress_callback(0, total, None, phase)

        while pending:
            completed, _not_done = wait(
                pending,
                timeout=0.10,
                return_when=FIRST_COMPLETED,
            )

            if not completed:
                # Let the GUI process Pause/Cancel even while waiting for a
                # slow image/NAS read to finish.
                if progress_callback is not None:
                    progress_callback(done_count, total, None, phase)
                continue

            for future in completed:
                index, path = pending.pop(future)
                try:
                    result = future.result()
                except BaseException as exc:  # per-file failure
                    result = exc

                results_by_index[index] = (path, result)
                done_count += 1

                if progress_callback is not None:
                    progress_callback(done_count, total, path, phase)

                while len(pending) < max_pending and submit_next():
                    pass

    except BaseException:
        for future in pending:
            future.cancel()
        executor.shutdown(wait=False, cancel_futures=True)
        raise

    executor.shutdown(wait=True)

    # All slots should be filled, but keep a defensive fallback.
    return [
        item if item is not None else (paths[index], RuntimeError("Background result was missing."))
        for index, item in enumerate(results_by_index)
    ]
