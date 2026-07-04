"""Filesystem helpers."""

import os
import tempfile
from pathlib import Path


def atomic_write(path: Path, content: str) -> None:
    """Write ``content`` to ``path`` atomically.

    Writes to a temp file in the same directory then ``os.replace`` so a crash mid-write
    never leaves a half-patched test file on disk.
    """
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=path.suffix)
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(content)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
