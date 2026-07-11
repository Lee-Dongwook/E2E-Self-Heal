"""Shadow Runtime module for E2E-Self-Heal.

Provides workspaces, snapshots, matching, and playwright mock injection.
"""

from app.shadow.injector import MockInjector
from app.shadow.interfaces import IMockInjector, IShadowWorkspace, ISnapshotStore, ITraceParser
from app.shadow.matcher import NoMatchError, SnapshotMatcher
from app.shadow.schemas import CapturedRequest, CapturedResponse, NetworkSnapshot
from app.shadow.workspace import ShadowWorkspace

__all__ = [
    "IMockInjector",
    "IShadowWorkspace",
    "ISnapshotStore",
    "ITraceParser",
    "MockInjector",
    "SnapshotMatcher",
    "NoMatchError",
    "CapturedRequest",
    "CapturedResponse",
    "NetworkSnapshot",
    "ShadowWorkspace",
]
