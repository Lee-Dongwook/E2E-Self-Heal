"""Provider-neutral LLM configuration: generic llm_* fields and nvidia_* back-compat."""

from typing import Any

import pytest
from pydantic import ValidationError

from app.config import Settings


def make_settings(**overrides: Any) -> Settings:
    """Build Settings ignoring any local .env so tests stay hermetic."""
    return Settings(_env_file=None, **overrides)  # type: ignore[call-arg]


def test_defaults_to_nvidia_provider():
    assert make_settings().llm_provider == "nvidia"


def test_legacy_nvidia_key_backfills_generic_field():
    # An existing E2E_HEALER_NVIDIA_API_KEY-only setup must keep working: the legacy key
    # is folded into the generic llm_api_key, along with the nvidia base_url/model defaults.
    settings = make_settings(nvidia_api_key="nvapi-legacy")
    assert settings.llm_api_key == "nvapi-legacy"
    assert settings.llm_base_url == "https://integrate.api.nvidia.com/v1"
    assert settings.llm_model == "openai/gpt-oss-120b"


def test_explicit_generic_field_overrides_legacy():
    settings = make_settings(nvidia_api_key="nvapi-legacy", llm_api_key="explicit")
    assert settings.llm_api_key == "explicit"


def test_legacy_max_tokens_only_maps_when_set():
    assert make_settings(nvidia_max_tokens=8192).llm_max_tokens == 8192


def test_non_nvidia_provider_ignores_legacy_fields():
    # A different provider must not inherit NVIDIA's key/base_url/model.
    settings = make_settings(
        llm_provider="openai",
        nvidia_api_key="nvapi-legacy",
        llm_api_key="sk-openai",
        llm_model="gpt-4o-mini",
    )
    assert settings.llm_api_key == "sk-openai"
    assert settings.llm_base_url == ""
    assert settings.llm_model == "gpt-4o-mini"


@pytest.mark.parametrize("provider", ["nvidia", "openai", "anthropic", "ollama", "orcarouter"])
def test_known_providers_are_accepted(provider):
    assert make_settings(llm_provider=provider, llm_model="test-model").llm_provider == provider


def test_unknown_provider_is_rejected():
    with pytest.raises(ValidationError):
        make_settings(llm_provider="foobar")


def test_jsx_chunk_margin_lines_rejects_negative_values():
    with pytest.raises(ValidationError):
        make_settings(jsx_chunk_margin_lines=-1)


# --- repair-loop and token-limit bounds -------------------------------------------


@pytest.mark.parametrize("bad", [0, -1, -5])
def test_max_loops_rejects_zero_and_negative(bad):
    with pytest.raises(ValidationError, match="max_loops"):
        make_settings(max_loops=bad)


@pytest.mark.parametrize("bad", [4, 10, 100])
def test_max_loops_rejects_values_above_three(bad):
    with pytest.raises(ValidationError, match="max_loops"):
        make_settings(max_loops=bad)


@pytest.mark.parametrize("good", [1, 2, 3])
def test_max_loops_accepts_one_through_three(good):
    assert make_settings(max_loops=good).max_loops == good


def test_max_loops_defaults_to_three():
    assert make_settings().max_loops == 3


@pytest.mark.parametrize("field", ["llm_max_tokens", "nvidia_max_tokens"])
@pytest.mark.parametrize("bad", [0, -1, -100])
def test_token_limits_reject_zero_and_negative(field, bad):
    with pytest.raises(ValidationError, match=field):
        make_settings(**{field: bad})


@pytest.mark.parametrize("field", ["llm_max_tokens", "nvidia_max_tokens"])
def test_token_limits_accept_positive_values(field):
    assert make_settings(**{field: 512}).model_dump()[field] == 512


# --- fail-fast model requirement --------------------------------------------------


@pytest.mark.parametrize("provider", ["openai", "anthropic", "ollama", "deepseek", "orcarouter"])
def test_provider_without_model_is_rejected(provider):
    # NVIDIA is excluded: its legacy nvidia_model default is backfilled, so no explicit
    # model is required there (see test_nvidia_provider_gets_legacy_default_model).
    with pytest.raises(ValidationError, match="llm_model"):
        make_settings(llm_provider=provider)


@pytest.mark.parametrize("blank", ["", "   ", "\t\n"])
def test_blank_model_is_rejected(blank):
    with pytest.raises(ValidationError, match="llm_model"):
        make_settings(llm_model=blank)


def test_nvidia_provider_gets_legacy_default_model():
    # NVIDIA is the one provider with a back-compat default, so no explicit model is needed.
    assert make_settings().llm_model == "openai/gpt-oss-120b"


def test_orcarouter_specific_settings_map_to_generic_fields():
    settings = make_settings(
        llm_provider="orcarouter",
        llm_model="router-model",
        orcarouter_api_key="router-key",
    )

    assert settings.llm_api_key == "router-key"
    assert settings.llm_base_url == "https://api.orcarouter.ai/v1"


def test_orcarouter_generic_settings_override_specific_fields():
    settings = make_settings(
        llm_provider="orcarouter",
        llm_api_key="generic-key",
        llm_base_url="https://proxy.example.com/v1",
        llm_model="router-model",
        orcarouter_api_key="router-key",
        orcarouter_base_url="https://ignored.example.com/v1",
    )

    assert settings.llm_api_key == "generic-key"
    assert settings.llm_base_url == "https://proxy.example.com/v1"


def test_orcarouter_standard_environment_variables_are_supported(monkeypatch):
    monkeypatch.setenv("ORCAROUTER_API_KEY", "router-key")
    monkeypatch.setenv("ORCAROUTER_BASE_URL", "https://router.example.com/v1")

    settings = make_settings(llm_provider="orcarouter", llm_model="router-model")

    assert settings.llm_api_key == "router-key"
    assert settings.llm_base_url == "https://router.example.com/v1"
