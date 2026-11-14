from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any


class IShadowWorkspace(ABC):
    """Interface for managing the Shadow runtime workspace.

    Implementations create, resolve, and clean up temporary directories used
    during Shadow execution.
    """

    @abstractmethod
    def setup_dirs(self) -> None:
        """Ensure all required workspace directories exist before execution."""

    @abstractmethod
    def resolve_path(self, relative_path: str | Path) -> Path:
        """Return the absolute path for ``relative_path``, always inside the workspace root."""

    @abstractmethod
    def cleanup(self, is_success: bool = False) -> None:
        """Remove temporary resources while preserving required output artifacts.

        ``is_success`` reports whether execution completed successfully.
        """


class ITraceParser(ABC):
    """Interface for parsing execution trace files into structured data."""

    @abstractmethod
    def parse(self, trace_path: Path) -> Any:
        """Parse ``trace_path``; raise if the trace cannot be parsed."""


class ISnapshotStore(ABC):
    """Interface for storing and retrieving runtime snapshots."""

    @abstractmethod
    def save_snapshot(self, snapshot_id: str, data: Any) -> None:
        """Store ``data`` under ``snapshot_id``, replacing any existing snapshot."""

    @abstractmethod
    def get_snapshot(self, snapshot_id: str) -> Any:
        """Return the snapshot stored under ``snapshot_id``; raise if it does not exist."""


class IMockInjector(ABC):
    """Interface for injecting mocked responses into runtime execution."""

    @abstractmethod
    def inject_mock(self, target: str, mock_data: Any) -> Any:
        """Register ``mock_data`` for ``target`` so later requests receive the mock."""


class IShadowRuntime(ABC):
    """Owns the Shadow Runtime lifecycle and the shared execution context."""

    @abstractmethod
    def initialize(self) -> None:
        """Prepare all runtime resources before execution begins."""

    @abstractmethod
    def shutdown(self) -> None:
        """Release allocated resources and perform cleanup."""
