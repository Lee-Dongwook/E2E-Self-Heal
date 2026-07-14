from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any


class IShadowWorkspace(ABC):
    """
Interface for managing the Shadow runtime workspace.

Implementations are responsible for creating, resolving, and cleaning up
temporary directories used during Shadow execution.
"""
  

    @abstractmethod
    def setup_dirs(self) -> None:
        """
Create and initialize the required workspace directories.

Returns:
    None

Contract:
    Implementations should ensure all required directories exist before
    runtime execution begins.
"""
pass

    @abstractmethod
    def resolve_path(self, relative_path: str | Path) -> Path:
        """
Resolve a relative path within the Shadow workspace.

Args:
    relative_path: Relative file or directory path.

Returns:
    Path: Absolute path inside the workspace.

Contract:
    Returned paths must always remain inside the workspace root.
"""
pass

    @abstractmethod
    def cleanup(self, is_success: bool = False) -> None:
        """
Clean up the workspace after execution.

Args:
    is_success: True if execution completed successfully.

Returns:
    None

Contract:
    Implementations should safely remove temporary resources while preserving
    any required output artifacts.
"""
pass


class ITraceParser(ABC):
    """
Interface for parsing execution trace files.

Implementations convert raw trace files into structured data that can be
consumed by Shadow runtime features.
"""

    @abstractmethod
    def parse(self, trace_path: Path) -> Any:
        """
Parse an execution trace.

Args:
    trace_path: Path to the trace file.

Returns:
    Parsed trace representation.

Contract:
    Raise an appropriate exception if the trace cannot be parsed.
"""
pass


class ISnapshotStore(ABC):
    """
Interface for storing and retrieving runtime snapshots.
"""

    @abstractmethod
    def save_snapshot(self, snapshot_id: str, data: Any) -> None:
        """
Store a runtime snapshot.

Args:
    snapshot_id: Unique snapshot identifier.
    data: Snapshot contents.

Returns:
    None

Contract:
    Existing snapshots with the same identifier may be replaced.
"""
pass

    @abstractmethod
    def get_snapshot(self, snapshot_id: str) -> Any:
        """
Retrieve a stored snapshot.

Args:
    snapshot_id: Snapshot identifier.

Returns:
    Stored snapshot data.

Contract:
    Raise an appropriate error if the snapshot does not exist.
"""
pass


class IMockInjector(ABC):
    """
Interface for injecting mocked responses into runtime execution.
"""

    @abstractmethod
    def inject_mock(self, target: str, mock_data: Any) -> None:
        """
Register a mock response.

Args:
    target: Target endpoint or resource.
    mock_data: Mock response data.

Returns:
    None

Contract:
    Future runtime requests to the target should receive the provided mock.
"""
pass


class IShadowRuntime(ABC):
    """Blueprint for the Shadow Runtime orchestrator.

    Owns the basic runtime lifecycle and the shared execution context that future
    Shadow Testing features (trace parsing, snapshot loading, network
    interception, DOM injection) will build upon. Only the lifecycle surface is
    defined at this foundational stage.
    """

    @abstractmethod
    def initialize(self) -> None:
        """
Initialize the Shadow runtime.

Returns:
    None

Contract:
    Prepare all runtime resources before execution begins.
"""
pass

    @abstractmethod
    def shutdown(self) -> None:
        """
Shut down the Shadow runtime.

Returns:
    None

Contract:
    Release allocated resources and perform cleanup.
"""
pass
