import pytest
from pydantic import ValidationError

from app.shadow import CleanupPolicy, ShadowConfig


def test_shadow_config_is_importable_and_has_defaults():
    config = ShadowConfig()
    assert config.workspace_dir == ".shadow_workspace"
    assert config.cache_dir == "cache"
    assert config.snapshots_dir == "snapshots"
    assert config.tmp_dir == "tmp"
    assert config.offline is False
    assert config.cleanup_policy is CleanupPolicy.ON_SUCCESS


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
