import os
import socket
import stat
import tempfile
from pathlib import Path

import pytest

from app.config import settings
from app.sandbox import SandboxViolation
from app.utils import files
from app.utils.files import atomic_write


def test_atomic_write_creates_and_overwrites(tmp_path):
    target = tmp_path / "sample.spec.ts"
    atomic_write(target, "first")
    assert target.read_text() == "first"
    atomic_write(target, "second")
    assert target.read_text() == "second"


def test_atomic_write_leaves_no_temp_files(tmp_path):
    target = tmp_path / "sample.spec.ts"
    atomic_write(target, "content")
    leftovers = [p.name for p in tmp_path.iterdir() if p.name.startswith(".tmp-")]
    assert leftovers == []


@pytest.mark.parametrize(
    "content",
    ["alpha\nbeta\n", "alpha\r\nbeta\r\n", "alpha\rbeta\r"],
)
def test_atomic_write_preserves_line_endings(tmp_path: Path, content: str) -> None:
    """Write ``content`` byte-for-byte, never translating line endings (LF -> CRLF on Windows)."""
    target = tmp_path / "sample.spec.ts"

    atomic_write(target, content)

    assert target.read_bytes() == content.encode("utf-8")


def test_atomic_write_fsyncs_file_before_replace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "sample.spec.ts"
    events: list[str] = []
    real_fsync = files.os.fsync
    real_replace = files.os.replace

    def record_fsync(fd: int) -> None:
        events.append("fsync")
        real_fsync(fd)

    def record_replace(source: Path, destination: Path) -> None:
        events.append("replace")
        real_replace(source, destination)

    monkeypatch.setattr(files.os, "fsync", record_fsync)
    monkeypatch.setattr(files.os, "replace", record_replace)

    atomic_write(target, "durable")

    # The file content must be synced before the rename; a final directory
    # fsync may follow the rename.
    assert events[:2] == ["fsync", "replace"]
    assert target.read_text() == "durable"


def test_atomic_write_fsyncs_parent_dir_after_replace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "sample.spec.ts"
    events: list[str] = []
    real_fsync = files.os.fsync
    real_replace = files.os.replace

    def record_fsync(fd: int) -> None:
        events.append("fsync")
        real_fsync(fd)

    def record_replace(source: Path, destination: Path) -> None:
        events.append("replace")
        real_replace(source, destination)

    monkeypatch.setattr(files.os, "fsync", record_fsync)
    monkeypatch.setattr(files.os, "replace", record_replace)

    atomic_write(target, "durable")

    assert events == ["fsync", "replace", "fsync"]
    assert target.read_text() == "durable"


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode bits are not portable on Windows")
def test_atomic_write_preserves_existing_mode(tmp_path: Path) -> None:
    target = tmp_path / "sample.spec.ts"
    target.write_text("old")
    target.chmod(0o754)

    atomic_write(target, "new")

    assert stat.S_IMODE(target.stat().st_mode) == 0o754
    assert target.read_text() == "new"


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode bits are not portable on Windows")
def test_atomic_write_failure_preserves_existing_content_and_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "sample.spec.ts"
    target.write_text("old")
    target.chmod(0o754)

    def fail_replace(source: Path, destination: Path) -> None:
        raise OSError("replace denied")

    monkeypatch.setattr(files.os, "replace", fail_replace)

    with pytest.raises(OSError, match="replace denied"):
        atomic_write(target, "new")

    assert target.read_text() == "old"
    assert stat.S_IMODE(target.stat().st_mode) == 0o754


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode bits are not portable on Windows")
def test_atomic_write_new_file_uses_private_mode(tmp_path: Path) -> None:
    target = tmp_path / "new.spec.ts"

    atomic_write(target, "new")

    assert stat.S_IMODE(target.stat().st_mode) == 0o600


def _create_symlink(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target)
    except OSError as exc:
        pytest.skip(f"symlinks unavailable: {exc}")


def test_atomic_write_rejects_symlink_to_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "sandbox_mode", "off")
    target = tmp_path / "target.spec.ts"
    link = tmp_path / "link.spec.ts"
    target.write_text("old")
    _create_symlink(link, target)

    with pytest.raises(SandboxViolation, match="symlink"):
        atomic_write(link, "new")

    assert link.is_symlink()
    assert target.read_text() == "old"


def test_atomic_write_rejects_symlink_escape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "sandbox_mode", "off")
    outside = tmp_path / "outside.spec.ts"
    root = tmp_path / "repo"
    link = root / "link.spec.ts"
    root.mkdir()
    outside.write_text("secret")
    _create_symlink(link, outside)

    with pytest.raises(SandboxViolation, match="symlink"):
        atomic_write(link, "new")

    assert link.is_symlink()
    assert outside.read_text() == "secret"


def test_atomic_write_rejects_symlinked_parent_inside_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "sandbox_mode", "off")
    real_dir = tmp_path / "real"
    linked_dir = tmp_path / "linked"
    real_dir.mkdir()
    _create_symlink(linked_dir, real_dir)
    target = linked_dir / "target.spec.ts"

    with pytest.raises(SandboxViolation, match="symlink"):
        atomic_write(target, "new")

    assert not (real_dir / "target.spec.ts").exists()


def test_atomic_write_rejects_symlinked_parent_escape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "sandbox_mode", "off")
    root = tmp_path / "repo"
    outside = tmp_path / "outside"
    linked_dir = root / "linked"
    root.mkdir()
    outside.mkdir()
    _create_symlink(linked_dir, outside)
    target = linked_dir / "target.spec.ts"

    with pytest.raises(SandboxViolation, match="symlink"):
        atomic_write(target, "new")

    assert not (outside / "target.spec.ts").exists()


def test_atomic_write_rejects_existing_directory_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "sandbox_mode", "off")
    target = tmp_path / "directory.spec.ts"
    target.mkdir()

    with pytest.raises(SandboxViolation, match="non-regular"):
        atomic_write(target, "new")


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFOs unavailable")
def test_atomic_write_rejects_existing_fifo_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "sandbox_mode", "off")
    target = tmp_path / "fifo.spec.ts"
    os.mkfifo(target)

    try:
        with pytest.raises(SandboxViolation, match="non-regular"):
            atomic_write(target, "new")
    finally:
        target.unlink(missing_ok=True)


@pytest.mark.skipif(not hasattr(socket, "AF_UNIX"), reason="Unix sockets unavailable")
def test_atomic_write_rejects_existing_socket_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "sandbox_mode", "off")
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    # macOS limits AF_UNIX paths to 104 bytes; pytest's default temporary
    # directory under /private/var/folders can exceed that limit. Use the
    # real /private/tmp path because /tmp is a symlink on macOS.
    with tempfile.TemporaryDirectory(dir="/private/tmp", prefix="e2e-") as directory:
        target = Path(directory) / "socket.spec.ts"
        try:
            server.bind(str(target))
            with pytest.raises(SandboxViolation, match="non-regular"):
                atomic_write(target, "new")
        finally:
            server.close()
            target.unlink(missing_ok=True)
