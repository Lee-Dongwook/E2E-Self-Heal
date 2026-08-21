"""Filesystem helpers."""

import os
import stat
import tempfile
from pathlib import Path

from app.sandbox import SandboxViolation, assert_write_allowed


def split_line_ending(line: str) -> tuple[str, str]:
    """Return a line's content and its exact trailing line ending (``''`` if none).

    Handles ``\\r\\n``, ``\\n`` and lone ``\\r`` so callers preserve the original file's
    line endings instead of silently normalizing CRLF to LF.
    """
    if line.endswith("\r\n"):
        return line[:-2], "\r\n"
    if line.endswith(("\n", "\r")):
        return line[:-1], line[-1:]
    return line, ""


def _fsync_parent_directory(path: Path) -> None:
    """Persist the directory entry after an atomic replacement where supported."""
    if os.name == "nt":
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    directory_fd = os.open(path.parent, flags)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _target_mode(path: Path) -> int:
    """Return existing mode bits, or private permissions for a new file."""
    if path.exists():
        return stat.S_IMODE(path.stat().st_mode)
    return 0o600


def _assert_regular_non_symlink_target(path: Path) -> None:
    """Reject symlinked path components and existing non-regular targets."""
    if any(component.is_symlink() for component in (path, *path.parents)):
        raise SandboxViolation(f"symlink write denied: {path}")

    try:
        target_mode = path.lstat().st_mode
    except FileNotFoundError:
        return
    if not stat.S_ISREG(target_mode):
        raise SandboxViolation(f"non-regular write target denied: {path}")


def atomic_write(path: Path, content: str, reason: str = "atomic_write") -> None:
    """Write ``content`` to ``path`` atomically and durably.

    Writes to a temp file in the same directory, syncs its contents, replaces the target,
    then syncs the parent directory so a crash mid-write cannot leave a half-patched file
    or lose the durable rename on supported platforms.
    """
    _assert_regular_non_symlink_target(path)
    assert_write_allowed(path, reason=reason)
    mode = _target_mode(path)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=path.suffix)
    try:
        with os.fdopen(fd, "w", newline="") as handle:
            os.chmod(tmp, mode)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
        _fsync_parent_directory(path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
