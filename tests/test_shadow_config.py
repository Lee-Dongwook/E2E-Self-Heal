import pytest
from pydantic import ValidationError

from app.shadow import CleanupPolicy, MissPolicy, ShadowConfig


def test_shadow_config_is_importable_and_has_defaults():
    config = ShadowConfig()
    assert config.workspace_dir == ".shadow_workspace"
    assert config.cache_dir == "cache"
    assert config.snapshots_dir == "snapshots"
    assert config.tmp_dir == "tmp"
    assert config.offline is False
    assert config.cleanup_policy is CleanupPolicy.ON_SUCCESS
    assert config.miss_policy is MissPolicy.STRICT
    assert config.match_options.allow_cross_origin is False
    assert config.match_options.min_score == 180.0


def test_shadow_config_accepts_overrides():
    config = ShadowConfig(
        workspace_dir="/tmp/shadow",
        offline=True,
        cleanup_policy=CleanupPolicy.NEVER,
    )
    assert config.workspace_dir == "/tmp/shadow"
    assert config.offline is True
    assert config.cleanup_policy is CleanupPolicy.NEVER


def test_cleanup_policy_accepts_string_value():
    config = ShadowConfig.model_validate({"cleanup_policy": "always"})
    assert config.cleanup_policy is CleanupPolicy.ALWAYS


def test_shadow_config_is_immutable():
    config = ShadowConfig()
    with pytest.raises(ValidationError):
        config.offline = True


def test_invalid_cleanup_policy_is_rejected():
    with pytest.raises(ValidationError):
        ShadowConfig.model_validate({"cleanup_policy": "sometimes"})


def test_miss_policy_accepts_string_value():
    config = ShadowConfig.model_validate({"miss_policy": "record-and-augment"})
    assert config.miss_policy is MissPolicy.RECORD_AND_AUGMENT

    lenient = ShadowConfig.model_validate({"miss_policy": "lenient"})
    assert lenient.miss_policy is MissPolicy.LENIENT


def test_invalid_miss_policy_is_rejected():
    with pytest.raises(ValidationError):
        ShadowConfig.model_validate({"miss_policy": "ignore"})


@pytest.mark.parametrize("miss_policy", [MissPolicy.LENIENT, MissPolicy.RECORD_AND_AUGMENT])
def test_offline_mode_rejects_network_capable_miss_policies(miss_policy: MissPolicy) -> None:
    with pytest.raises(ValidationError, match="offline mode requires"):
        ShadowConfig(offline=True, miss_policy=miss_policy)


def test_match_options_accept_nested_configuration():
    config = ShadowConfig.model_validate(
        {"match_options": {"allow_cross_origin": True, "min_score": 120.0}}
    )

    assert config.match_options.allow_cross_origin is True
    assert config.match_options.min_score == 120.0


@pytest.mark.parametrize("field", ["cache_dir", "snapshots_dir", "tmp_dir"])
@pytest.mark.parametrize(
    "value",
    [
        "",
        ".",
        "../outside",
        "nested/../../outside",
        "/absolute",
        r"C:\absolute",
        r"C:relative-to-drive",
        ".git/artifacts",
    ],
)
def test_artifact_directories_reject_unsafe_paths(field: str, value: str) -> None:
    with pytest.raises(ValidationError):
        ShadowConfig.model_validate({field: value})


@pytest.mark.parametrize(
    "overrides",
    [
        {"cache_dir": "artifacts", "snapshots_dir": "artifacts"},
        {"cache_dir": "artifacts", "snapshots_dir": "artifacts/snapshots"},
        {"snapshots_dir": "artifacts", "tmp_dir": r"artifacts\tmp"},
    ],
)
def test_artifact_directories_reject_overlapping_roles(overrides: dict[str, str]) -> None:
    with pytest.raises(ValidationError):
        ShadowConfig.model_validate(overrides)


def test_artifact_directories_are_normalized_for_portable_reuse() -> None:
    config = ShadowConfig(cache_dir=r"artifacts\cache", snapshots_dir="snapshots/./saved")

    assert config.cache_dir == "artifacts/cache"
    assert config.snapshots_dir == "snapshots/saved"
