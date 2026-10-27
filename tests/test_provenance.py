"""Provider provenance: which endpoint saw the evidence, and whether it left the machine."""

import pytest

import app.config as config_module
from app.provenance import is_local_endpoint, provider_provenance
from app.schemas import ProviderProvenance
from tests.factories import make_state


def _configure(
    monkeypatch: pytest.MonkeyPatch, *, provider: str, model: str, base_url: str = ""
) -> None:
    """Point the settings at a provider, as a user would in .env."""
    monkeypatch.setattr(config_module.settings, "llm_provider", provider)
    monkeypatch.setattr(config_module.settings, "llm_model", model)
    monkeypatch.setattr(config_module.settings, "llm_base_url", base_url)


# --- endpoint classification ---------------------------------------------------------------


@pytest.mark.parametrize(
    "endpoint",
    [
        "",
        "http://localhost:11434",
        "http://LOCALHOST:8000/v1",
        "http://127.0.0.1:8080/v1",
        "http://[::1]:11434/v1",
        "http://10.0.0.5/v1",
        "http://172.16.0.9/v1",
        "http://192.168.1.50:8000/v1",
        "http://169.254.169.254/latest/meta-data",
    ],
)
def test_an_address_that_stays_on_this_machine_is_local(endpoint: str) -> None:
    assert is_local_endpoint(endpoint) is True


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://api.openai.com/v1",
        "https://api.anthropic.com",
        "http://8.8.8.8/v1",
        # Names are never resolved, so a hostname that happens to point at a private address is
        # reported remote — the conservative answer for a data boundary.
        "https://llm.internal.example.com/v1",
        # Tailscale hands out 100.64.0.0/10 addresses, which are neither loopback nor RFC 1918
        # private, so tailnet traffic is reported remote as well.
        "http://100.101.102.103:11434/v1",
    ],
)
def test_anything_else_is_remote(endpoint: str) -> None:
    assert is_local_endpoint(endpoint) is False


# --- reported provenance --------------------------------------------------------------------


def test_a_local_provider_is_reported_as_local(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(monkeypatch, provider="ollama", model="llama3.2")

    assert provider_provenance(make_state()) == ProviderProvenance(
        provider="ollama",
        model="llama3.2",
        endpoint="http://localhost:11434",
        mode="local",
        redaction_applied=False,
        redaction_count=0,
    )


def test_a_hosted_provider_reports_the_endpoint_the_client_actually_uses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # llm_base_url is empty here on purpose: the reported endpoint is the provider default the
    # client falls back to, not the raw configuration.
    _configure(monkeypatch, provider="openai", model="gpt-5-5-medium")

    provenance = provider_provenance(make_state())

    assert provenance.endpoint == "https://api.openai.com/v1"
    assert provenance.mode == "remote"


def test_a_private_openai_compatible_endpoint_is_reported_local(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The local-first recipe: an OpenAI-compatible server on the LAN, selected with no code
    # change. Evidence stays inside the network, and the JSON contract says so.
    _configure(
        monkeypatch, provider="openai", model="qwen3-32b", base_url="http://192.168.1.20:8000/v1"
    )

    provenance = provider_provenance(make_state())

    assert (provenance.endpoint, provenance.mode) == ("http://192.168.1.20:8000/v1", "local")


def test_endpoint_credentials_and_sensitive_query_values_are_removed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Provenance is emitted into --json and CI logs, so the endpoint is redacted like any other
    # evidence.
    _configure(
        monkeypatch,
        provider="openai",
        model="qwen3-32b",
        base_url="https://user:hunter2@llm.example.com/v1?api_key=abc123",
    )

    endpoint = provider_provenance(make_state()).endpoint

    assert "hunter2" not in endpoint
    assert "abc123" not in endpoint
    assert endpoint.startswith("https://llm.example.com/v1")


def test_redaction_reported_by_the_boundary_reaches_provenance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure(monkeypatch, provider="anthropic", model="claude-sonnet-4-6")

    provenance = provider_provenance(make_state(redaction_count=3, redaction_applied=True))

    assert provenance.redaction_count == 3
    assert provenance.redaction_applied is True


def test_a_count_alone_marks_redaction_as_applied(monkeypatch: pytest.MonkeyPatch) -> None:
    # `applied` is derived from the count when a node did not set it explicitly.
    _configure(monkeypatch, provider="anthropic", model="claude-sonnet-4-6")

    assert provider_provenance(make_state(redaction_count=1)).redaction_applied is True
