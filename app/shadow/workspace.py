"""Filesystem workspace for the Shadow Runtime.

Owns the on-disk directory layout (cache, snapshots, tmp) and its lifecycle
(creation and policy-aware cleanup). All directory names are derived from the
shared :class:`ShadowConfig` so they live in exactly one place.
"""

import shutil
from pathlib import Path

import structlog

from app.shadow.config import CleanupPolicy, ShadowConfig
from app.shadow.interfaces import IShadowWorkspace

logger = structlog.get_logger(__name__)


class ShadowWorkspace(IShadowWorkspace):
    """
    Manages temporary runtime resources, cached artifacts, and snapshots for the
    Shadow Runtime, conforming to the IShadowWorkspace interface.

    Every path is resolved from the shared :class:`ShadowConfig`, so the workspace
    layout is defined in a single place rather than hardcoded here.
    """

    def __init__(self, config: ShadowConfig | None = None):
        self.config = config or ShadowConfig()

        # Resolve the workspace root and subdirectories from the shared config.
        self.base_dir = Path(self.config.workspace_dir).resolve()
        self.cache_dir = self.base_dir / self.config.cache_dir
        self.snapshots_dir = self.base_dir / self.config.snapshots_dir
        self.tmp_dir = self.base_dir / self.config.tmp_dir

        # Automatically build the folders when initialized
        self.setup_dirs()

    def setup_dirs(self) -> None:
        """Creates the directory structure safely."""

        for directory in [self.base_dir, self.cache_dir, self.snapshots_dir, self.tmp_dir]:
            directory.mkdir(parents=True, exist_ok=True)

    def resolve_path(self, relative_path: str | Path) -> Path:
        """Safely resolves paths relative to the workspace base."""

        return (self.base_dir / relative_path).resolve()

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
        """Removes the workspace directory according to the configured cleanup policy.

        - ``NEVER``: always keep artifacts.
        - ``ON_SUCCESS``: remove only when the shadow run succeeded.
        - ``ALWAYS``: remove regardless of outcome.
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

        shutil.rmtree(self.base_dir)
        logger.info(
            "workspace_cleaned",
            policy=policy.value,
            is_success=is_success,
            path=str(self.base_dir),
        )

    @staticmethod
    def _resolve_under(root: Path, name: str | Path) -> Path:
        path = (root / name).resolve()
        path.relative_to(root)
        return path
