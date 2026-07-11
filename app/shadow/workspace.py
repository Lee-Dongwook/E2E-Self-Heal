import shutil
from pathlib import Path
from shadow.interfaces import IShadowWorkspace


class ShadowWorkspace(IShadowWorkspace):
    """
    Manages temporary runtime resources, cached artifacts, and future snapshots
    for the Shadow Runtime, conforming to the IShadowWorkspace interface.
    """

    def __init__(self, base_dir: str | Path = ".shadow_workspace"):
        # Ensure we work with absolute paths
        self.base_dir = Path(base_dir).resolve()

        # Define subdirectories required for future features
        self.cache_dir = self.base_dir / "cache"
        self.snapshots_dir = self.base_dir / "snapshots"
        self.tmp_dir = self.base_dir / "tmp"

        # Automatically build the folders when initialized
        self.setup_dirs()

    def setup_dirs(self) -> None:
        """Creates the directory structure safely."""

        for directory in [self.base_dir, self.cache_dir, self.snapshots_dir, self.tmp_dir]:
            directory.mkdir(parents=True, exist_ok=True)

    def resolve_path(self, relative_path: str | Path) -> Path:
        """Safely resolves paths relative to the workspace base."""

        return (self.base_dir / relative_path).resolve()

    def cleanup(self) -> None:
        """Safely removes the workspace directory and all its contents."""

        if self.base_dir.exists():
            shutil.rmtree(self.base_dir)
