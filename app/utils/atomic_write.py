"""Crash-safe whole-file writes.

``atomic_write_text`` writes to a sibling temp file in the target directory,
flushes and fsyncs it, then ``os.replace``s it over the target. A reader (or a
process restarted after a crash) sees either the old file or the new one,
never a truncated mix. The temp file lives in the same directory so the rename
stays on one filesystem (``os.replace`` is atomic only within a filesystem).

Use it for any corpus file that is rewritten wholesale (signal containers,
generated artifacts, manifests). Append-only logs should append + fsync
instead; see ``SignalAuditStore.append``.
"""

from __future__ import annotations

import contextlib
import os
import stat
import uuid
from pathlib import Path


def _fsync_dir(dir_path: Path) -> None:
    """Best-effort fsync of a directory so the rename itself is durable."""
    try:
        fd = os.open(dir_path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass  # not supported on every platform/filesystem
    finally:
        os.close(fd)


def atomic_write_text(path: Path | str, content: str, *, encoding: str = "utf-8") -> None:
    """Atomically replace ``path`` with ``content``.

    Creates the parent directory if needed. If an existing file is replaced
    its permission bits are kept; a new file gets the process umask default
    (as ``Path.write_text`` would). On any failure the temp file is removed,
    the target is left untouched, and the exception propagates.
    """
    path = Path(path)
    dir_path = path.parent
    dir_path.mkdir(parents=True, exist_ok=True)

    try:
        existing_mode: int | None = stat.S_IMODE(path.stat().st_mode)
    except FileNotFoundError:
        existing_mode = None

    tmp_path = dir_path / f".{path.name}.{uuid.uuid4().hex}.tmp"
    # O_EXCL: never clobber; mode 0o666 lets the umask apply like a normal open().
    fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
    try:
        with os.fdopen(fd, "w", encoding=encoding) as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        if existing_mode is not None:
            os.chmod(tmp_path, existing_mode)
        os.replace(tmp_path, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_path)
        raise
    _fsync_dir(dir_path)
