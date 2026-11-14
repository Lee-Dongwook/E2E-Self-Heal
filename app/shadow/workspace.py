"""Filesystem workspace for the Shadow Runtime.

Owns the on-disk directory layout (cache, snapshots, tmp) and its lifecycle
(creation and policy-aware cleanup). All directory names derive from the shared
:class:`ShadowConfig`.
"""

import json
import shutil
from pathlib import Path

import structlog

from app.shadow.config import CleanupPolicy, ShadowConfig
from app.shadow.interfaces import IShadowWorkspace

logger = structlog.get_logger(__name__)
_OWNERSHIP_MARKER = ".e2e-self-heal-shadow-workspace"
_OWNERSHIP_VERSION = 1


class ShadowWorkspace(IShadowWorkspace):
    """Manages temporary runtime resources, cached artifacts, and snapshots.

    Every path is resolved from the shared :class:`ShadowConfig`.
    """

    def __init__(self, config: ShadowConfig | None = None):
        self.config = config or ShadowConfig()
        self.base_dir = Path(self.config.workspace_dir).expanduser().resolve()
        self._marker_path = self.base_dir / _OWNERSHIP_MARKER
        self._configured_directories = {
            "cache": self.base_dir / self.config.cache_dir,
            "snapshots": self.base_dir / self.config.snapshots_dir,
            "tmp": self.base_dir / self.config.tmp_dir,
        }

        self._assert_safe_root()
        resolved_directories = self._resolve_configured_directories()
        self.cache_dir = resolved_directories["cache"]
        self.snapshots_dir = resolved_directories["snapshots"]
        self.tmp_dir = resolved_directories["tmp"]
        self.setup_dirs()

    def setup_dirs(self) -> None:
        """Claim or validate the workspace, then create its managed directories."""
        self._assert_safe_root()
        self._claim_or_validate_workspace()
        resolved_directories = self._resolve_configured_directories()
        for directory in resolved_directories.values():
            directory.mkdir(parents=True, exist_ok=True)
            self._assert_no_symlink_components(directory)

        self.cache_dir = resolved_directories["cache"]
        self.snapshots_dir = resolved_directories["snapshots"]
        self.tmp_dir = resolved_directories["tmp"]

    def resolve_path(self, relative_path: str | Path) -> Path:
        """Safely resolves paths relative to the workspace base."""
        return self._resolve_under(self.base_dir, relative_path)

    def cache_path(self, name: str | Path) -> Path:
        """Returns a path inside the workspace cache directory."""

        return self._resolve_under(self.cache_dir, name)

    def snapshot_path(self, name: str | Path) -> Path:
        """Returns a path inside the workspace snapshots directory."""

        return self._resolve_under(self.snapshots_dir, name)

    def tmp_path(self, name: str | Path) -> Path:
        """Returns a path inside the workspace temporary directory."""

        return self._resolve_under(self.tmp_dir, name)

    def cleanup(self, is_success: bool = False) -> None:
        """Remove disposable directories according to the configured cleanup policy.

        ``NEVER`` always keeps artifacts; ``ON_SUCCESS`` removes cache and tmp only
        after a successful run; ``ALWAYS`` removes them regardless of outcome. The
        owned workspace root and persistent snapshots are never removed.
        """

        policy = self.config.cleanup_policy

        if policy is CleanupPolicy.NEVER:
            logger.info("workspace_cleanup_skipped", policy=policy.value, path=str(self.base_dir))
            return

        if policy is CleanupPolicy.ON_SUCCESS and not is_success:
            logger.info(
                "workspace_cleanup_skipped",
                policy=policy.value,
                is_success=is_success,
                path=str(self.base_dir),
            )
            return

        if not self.base_dir.exists():
            return
        self._assert_safe_root()
        self._validate_ownership_marker()
        resolved_directories = self._resolve_configured_directories()
        disposable_directories = [
            resolved_directories["cache"],
            resolved_directories["tmp"],
        ]
        for directory in disposable_directories:
            self._validate_cleanup_target(directory)

        for directory in disposable_directories:
            if directory.exists():
                shutil.rmtree(directory)
        logger.info(
            "workspace_cleaned",
            policy=policy.value,
            is_success=is_success,
            paths=[str(path) for path in disposable_directories],
            snapshots_path=str(self.snapshots_dir),
        )

    def _assert_safe_root(self) -> None:
        """Reject roots that must never be treated as a Shadow workspace."""
        filesystem_root = Path(self.base_dir.anchor).resolve()
        home_directory = Path.home().expanduser().resolve()
        if self.base_dir == filesystem_root:
            raise ValueError("Shadow workspace cannot be the filesystem root")
        if self.base_dir == home_directory:
            raise ValueError("Shadow workspace cannot be the user home directory")
        git_marker = self.base_dir / ".git"
        if git_marker.exists() or git_marker.is_symlink():
            raise ValueError("Shadow workspace cannot be a repository root")

    def _claim_or_validate_workspace(self) -> None:
        """Create the ownership marker without adopting a non-empty unowned directory."""
        if self.base_dir.exists():
            if not self.base_dir.is_dir():
                raise ValueError(f"Shadow workspace is not a directory: {self.base_dir}")
            if self._marker_path.exists() or self._marker_path.is_symlink():
                self._validate_ownership_marker()
                return
            if any(self.base_dir.iterdir()):
                raise ValueError(
                    f"Refusing to use non-empty unowned Shadow workspace: {self.base_dir}"
                )
        else:
            self.base_dir.mkdir(parents=True)

        with self._marker_path.open("x", encoding="utf-8") as marker:
            marker.write(self._expected_ownership_marker())

        unexpected_entries = [
            entry for entry in self.base_dir.iterdir() if entry != self._marker_path
        ]
        if unexpected_entries:
            self._marker_path.unlink(missing_ok=True)
            raise ValueError(f"Shadow workspace changed while being claimed: {self.base_dir}")

    def _validate_ownership_marker(self) -> None:
        """Require the exact regular marker written when the workspace was claimed."""
        if self._marker_path.is_symlink() or not self._marker_path.is_file():
            raise ValueError(f"Shadow workspace ownership marker is invalid: {self._marker_path}")
        try:
            signature = self._marker_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ValueError(
                f"Shadow workspace ownership marker cannot be read: {self._marker_path}"
            ) from exc
        if signature != self._expected_ownership_marker():
            raise ValueError(f"Shadow workspace ownership marker is invalid: {self._marker_path}")

    def _expected_ownership_marker(self) -> str:
        """Describe the owned directory roles so they cannot be changed on reuse."""
        payload = {
            "version": _OWNERSHIP_VERSION,
            "cache_dir": self.config.cache_dir,
            "snapshots_dir": self.config.snapshots_dir,
            "tmp_dir": self.config.tmp_dir,
        }
        return f"{json.dumps(payload, sort_keys=True, separators=(',', ':'))}\n"

    def _resolve_configured_directories(self) -> dict[str, Path]:
        """Resolve configured directories and reject escapes or symlink-based overlap."""
        for name, path in self._configured_directories.items():
            try:
                path.relative_to(self._marker_path)
            except ValueError:
                continue
            raise ValueError(f"Shadow {name} directory overlaps the ownership marker")
        resolved = {
            name: self._resolve_under(self.base_dir, path.relative_to(self.base_dir))
            for name, path in self._configured_directories.items()
        }
        for directory in self._configured_directories.values():
            self._assert_no_symlink_components(directory)

        items = list(resolved.items())
        for index, (left_name, left_path) in enumerate(items):
            for right_name, right_path in items[index + 1 :]:
                if self._paths_overlap(left_path, right_path):
                    raise ValueError(
                        f"Resolved Shadow directories {left_name} and {right_name} overlap"
                    )
        return resolved

    def _assert_no_symlink_components(self, path: Path) -> None:
        """Reject symlinks between the owned root and a managed directory."""
        relative_path = path.relative_to(self.base_dir)
        current = self.base_dir
        for part in relative_path.parts:
            current /= part
            if current.is_symlink():
                raise ValueError(
                    f"Shadow workspace managed directory cannot be a symlink: {current}"
                )

    def _validate_cleanup_target(self, directory: Path) -> None:
        """Ensure a disposable target is an ordinary directory inside the owned root."""
        directory.relative_to(self.base_dir)
        self._assert_no_symlink_components(directory)
        if directory.exists() and not directory.is_dir():
            raise ValueError(f"Shadow cleanup target is not a directory: {directory}")

    @staticmethod
    def _paths_overlap(left: Path, right: Path) -> bool:
        try:
            left.relative_to(right)
            return True
        except ValueError:
            pass
        try:
            right.relative_to(left)
            return True
        except ValueError:
            return False

    @staticmethod
    def _resolve_under(root: Path, name: str | Path) -> Path:
        path = (root / name).resolve()
        path.relative_to(root)
        return path
