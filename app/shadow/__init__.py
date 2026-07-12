"""Shadow Runtime module for E2E-Self-Heal.

Provides workspaces, snapshots, matching, and playwright mock injection.
"""

from app.shadow.injector import MockInjector
from app.shadow.interfaces import (
    IMockInjector,
    IShadowRuntime,
    IShadowWorkspace,
    ISnapshotStore,
    ITraceParser,
)
from app.shadow.matcher import NoMatchError, SnapshotMatcher
from app.shadow.runtime import ShadowRuntime
from app.shadow.schemas import CapturedRequest, CapturedResponse, NetworkSnapshot, ShadowSnapshot
from app.shadow.scoring import MatchScorer, ScoringWeights
from app.shadow.snapshot_store import (
    SnapshotCorruptionError,
    SnapshotNotFoundError,
    SnapshotStore,
    SnapshotStoreError,
)
from app.shadow.workspace import ShadowWorkspace

__all__ = [
    "IMockInjector",
    "IShadowRuntime",
    "IShadowWorkspace",
    "ISnapshotStore",
    "ITraceParser",
    "MockInjector",
    "ShadowRuntime",
    "SnapshotMatcher",
    "NoMatchError",
    "RequestNormalizer",
    "MatchScorer",
    "ScoringWeights",
    "CapturedRequest",
    "CapturedResponse",
    "NetworkSnapshot",
    "ShadowSnapshot",
    "SnapshotStore",
    "SnapshotStoreError",
    "SnapshotNotFoundError",
    "SnapshotCorruptionError",
    "ShadowWorkspace",
]
