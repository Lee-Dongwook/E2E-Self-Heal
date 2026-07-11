
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any


class IShadowWorkspace(ABC):
    """Blueprint for managing the temporary workspace folder structure."""

    @abstractmethod
    def setup_dirs(self) -> None:
        pass

    @abstractmethod
    def resolve_path(self, relative_path: str|Path) -> Path:
        pass

    @abstractmethod
    def cleanup(self) -> None:
        pass

class ITraceParser(ABC):
    """Blueprint for reading and parsing execution trace files later."""

    @abstractmethod
    def parse(self, trace_path: Path) -> Any:
        pass

class ISnapshotStore(ABC):
    """Blueprint for saving and loading state snapshots later."""

    @abstractmethod
    def save_snapshot(self, snapshot_id: str, data: Any) -> None:
        pass

    @abstractmethod
    def get_snapshot(self, snapshot_id: str) -> Any:
        pass

class IMockInjector(ABC):
    """Blueprint for mocking network responses later."""

    @abstractmethod
    def inject_mock(self, target: str, mock_data: Any) -> None:
        pass