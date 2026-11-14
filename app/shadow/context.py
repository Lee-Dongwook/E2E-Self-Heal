"""Shadow Runtime execution context.

Creating a context has no side effects; the runtime activates and deactivates it.
"""

from dataclasses import dataclass

from app.shadow.interfaces import IMockInjector, IShadowWorkspace, ISnapshotStore


@dataclass
class ShadowContext:
    """Shared, per-session state for the Shadow Runtime.

    The collaborators are optional so a minimal runtime can be created and its
    lifecycle exercised without wiring any real components yet.
    """

    workspace: IShadowWorkspace | None = None
    snapshot_store: ISnapshotStore | None = None
    injector: IMockInjector | None = None
    is_active: bool = False

    def activate(self) -> None:
        """Mark the context as live for the current shadow session."""
        self.is_active = True

    def deactivate(self) -> None:
        """Mark the context as no longer live once the session ends."""
        self.is_active = False
