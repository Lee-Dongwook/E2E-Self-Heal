from enum import Enum
from pathlib import PurePosixPath, PureWindowsPath
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.shadow.match_options import MatchOptions


def _portable_parts(value: str) -> tuple[str, ...]:
    """Normalize POSIX and Windows separators for cross-platform comparisons."""
    return PurePosixPath(value.replace("\\", "/")).parts


class CleanupPolicy(str, Enum):
    """When the Shadow Runtime should remove its workspace artifacts."""

    ALWAYS = "always"
    ON_SUCCESS = "on_success"
    NEVER = "never"


class MissPolicy(str, Enum):
    """How the mock injector reacts to a live request with no matching snapshot.

    - ``STRICT``: abort the request so the miss surfaces as a test failure.
    - ``LENIENT``: fall back to the live network and let the request through.
    - ``RECORD_AND_AUGMENT``: fetch the live response, add it to the snapshot
      set, and fulfil the request with the freshly recorded response.
    """

    STRICT = "strict"
    LENIENT = "lenient"
    RECORD_AND_AUGMENT = "record-and-augment"


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
    miss_policy: MissPolicy = Field(
        default=MissPolicy.STRICT,
        description="how to handle a live request with no matching snapshot",
    )
    match_options: MatchOptions = Field(
        default_factory=MatchOptions,
        description="request origin and confidence matching options",
    )

    @field_validator("cache_dir", "snapshots_dir", "tmp_dir")
    @classmethod
    def validate_artifact_subdirectory(cls, value: str) -> str:
        """Require a non-empty relative path that cannot traverse above the workspace."""
        posix_path = PurePosixPath(value)
        windows_path = PureWindowsPath(value)
        parts = _portable_parts(value)
        if (
            not parts
            or posix_path.is_absolute()
            or windows_path.is_absolute()
            or windows_path.drive
            or ".." in parts
        ):
            raise ValueError("artifact directories must be non-empty relative paths without '..'")
        if parts[0].casefold() == ".git":
            raise ValueError("artifact directories cannot use the reserved '.git' path")
        return "/".join(parts)

    @model_validator(mode="after")
    def validate_artifact_directories_do_not_overlap(self) -> Self:
        """Keep durable snapshots outside every disposable cleanup scope."""
        directories = {
            "cache_dir": tuple(part.casefold() for part in _portable_parts(self.cache_dir)),
            "snapshots_dir": tuple(part.casefold() for part in _portable_parts(self.snapshots_dir)),
            "tmp_dir": tuple(part.casefold() for part in _portable_parts(self.tmp_dir)),
        }
        items = list(directories.items())
        for index, (left_name, left_parts) in enumerate(items):
            for right_name, right_parts in items[index + 1 :]:
                common_length = min(len(left_parts), len(right_parts))
                if left_parts[:common_length] == right_parts[:common_length]:
                    raise ValueError(
                        f"{left_name} and {right_name} must not overlap or contain each other"
                    )
        return self

    @model_validator(mode="after")
    def validate_offline_miss_policy(self) -> Self:
        """Keep offline replay a hard no-network boundary.

        Lenient and record-and-augment misses both contact the live origin, so accepting
        either setting alongside ``offline`` would make the advertised isolation false.
        """
        if self.offline and self.miss_policy is not MissPolicy.STRICT:
            raise ValueError("offline mode requires miss_policy='strict' to prevent network access")
        return self
