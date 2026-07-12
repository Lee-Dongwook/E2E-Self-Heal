from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class CleanupPolicy(str, Enum):
    """When the Shadow Runtime should remove its workspace artifacts."""

    ALWAYS = "always"
    ON_SUCCESS = "on_success"
    NEVER = "never"


class ShadowConfig(BaseModel):
    """Shared, lightweight configuration for the Shadow Runtime.

    Immutable so a config can be created once and passed around without risk of
    downstream mutation. Directory fields are relative subdirectory names resolved
    under :attr:`workspace_dir`, matching the current workspace layout.
    """

    model_config = ConfigDict(frozen=True)

    workspace_dir: str = Field(
        default=".shadow_workspace",
        description="root directory holding all shadow runtime artifacts",
    )
    cache_dir: str = Field(
        default="cache",
        description="cache subdirectory, relative to workspace_dir",
    )
    snapshots_dir: str = Field(
        default="snapshots",
        description="snapshot subdirectory, relative to workspace_dir",
    )
    tmp_dir: str = Field(
        default="tmp",
        description="temporary subdirectory, relative to workspace_dir",
    )
    offline: bool = Field(
        default=False,
        description="serve exclusively from snapshots without live network access",
    )
    cleanup_policy: CleanupPolicy = Field(
        default=CleanupPolicy.ON_SUCCESS,
        description="when to remove workspace artifacts after a shadow run",
    )
