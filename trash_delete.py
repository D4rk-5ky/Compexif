"""Move files to the operating-system Trash/Recycle Bin.

This module deliberately avoids permanent deletion.  It first tries the
cross-platform Send2Trash package.  If that is not installed, it falls back to
common Linux desktop trash commands.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil
import subprocess


@dataclass(frozen=True)
class TrashMoveResult:
    """Result from moving one file to the trash."""

    path: Path
    success: bool
    message: str = ""


def move_path_to_trash(path: Path) -> TrashMoveResult:
    """Move *path* to the user's Trash/Recycle Bin.

    Returns a result object instead of raising.  This makes it easier for the
    GUI to continue deleting other marked files and show a copyable warning list
    for anything that failed.
    """
    path = Path(path).expanduser()

    if not path.exists():
        return TrashMoveResult(path=path, success=False, message="File no longer exists.")
    if not path.is_file():
        return TrashMoveResult(path=path, success=False, message="Path is not a regular file.")

    try:
        from send2trash import send2trash  # type: ignore
    except Exception:
        send2trash = None

    if send2trash is not None:
        try:
            send2trash(str(path))
            return TrashMoveResult(path=path, success=True, message="Moved to trash.")
        except Exception as exc:
            # Continue to command-line fallbacks below.  Some desktop setups have
            # send2trash installed but not fully working for every filesystem.
            send2trash_error = str(exc)
    else:
        send2trash_error = "Send2Trash is not installed."

    for command in (("gio", "trash"), ("kioclient5", "move"), ("kioclient", "move"), ("trash-put",)):
        executable = command[0]
        if shutil.which(executable) is None:
            continue

        try:
            if executable in {"kioclient5", "kioclient"}:
                # KDE fallback.  The destination trash:/ asks KIO to move the
                # file to trash instead of deleting it.
                cmd = [executable, "move", str(path), "trash:/"]
            else:
                cmd = [*command, str(path)]
            subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            return TrashMoveResult(path=path, success=True, message="Moved to trash.")
        except subprocess.CalledProcessError as exc:
            stderr = (exc.stderr or exc.stdout or str(exc)).strip()
            return TrashMoveResult(path=path, success=False, message=stderr or str(exc))
        except Exception as exc:
            return TrashMoveResult(path=path, success=False, message=str(exc))

    return TrashMoveResult(
        path=path,
        success=False,
        message=(
            "Could not find a working trash handler. Install the Python package "
            "Send2Trash, or make sure gio/trash-put is available. "
            f"First error: {send2trash_error}"
        ),
    )
