"""The LLM client must be import-safe: no credentials needed to import or collect.

Also covers the provider-agnostic factory: the right chat model is built from
``settings.llm_provider`` and a missing key fails loudly for key-requiring providers.
"""

from typing import cast

import pytest
from langchain_anthropic import ChatAnthropic
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from app import llm
from app.config import settings


def test_get_client_requires_key(monkeypatch):
    llm._get_client.cache_clear()
    monkeypatch.setattr(settings, "llm_provider", "nvidia")
    monkeypatch.setattr(settings, "llm_api_key", "")
    with pytest.raises(RuntimeError, match="E2E_HEALER_LLM_API_KEY"):
        llm._get_client()
    llm._get_client.cache_clear()  # don't leak the empty-key client to other tests


def test_import_does_not_build_client():
    # Importing app.llm must not instantiate a client; _get_client is lazy + cached.
    llm._get_client.cache_clear()
    assert llm._get_client.cache_info().currsize == 0


def test_factory_builds_openai_compatible_client_for_nvidia(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "nvidia")
    monkeypatch.setattr(settings, "llm_api_key", "test-key")
    monkeypatch.setattr(settings, "llm_base_url", "https://integrate.api.nvidia.com/v1")
    monkeypatch.setattr(settings, "llm_model", "openai/gpt-oss-120b")

    model = llm._build_chat_model("nvidia")

    assert isinstance(model, ChatOpenAI)
    assert model.model_name == "openai/gpt-oss-120b"
    assert str(model.openai_api_base) == "https://integrate.api.nvidia.com/v1"


def test_factory_builds_anthropic_client(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "anthropic")
    monkeypatch.setattr(settings, "llm_api_key", "test-key")
    monkeypatch.setattr(settings, "llm_model", "claude-opus-4-8")

    model = llm._build_chat_model("anthropic")

    assert isinstance(model, ChatAnthropic)
    assert model.model == "claude-opus-4-8"


def test_anthropic_falls_back_to_standard_anthropic_api_key(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "anthropic")
    monkeypatch.setattr(settings, "llm_api_key", "")  # generic var unset
    monkeypatch.setattr(settings, "llm_model", "claude-sonnet-4-6")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-from-env")

    model = llm._build_chat_model("anthropic")

    assert isinstance(model, ChatAnthropic)
    assert cast(SecretStr, model.anthropic_api_key).get_secret_value() == "sk-ant-from-env"


def test_anthropic_missing_key_raises(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "anthropic")
    monkeypatch.setattr(settings, "llm_api_key", "")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        llm._build_chat_model("anthropic")


def test_anthropic_missing_extra_raises(monkeypatch):
    # When langchain-anthropic isn't installed, provider=anthropic must fail with an install hint.
    monkeypatch.setattr(settings, "llm_provider", "anthropic")
    monkeypatch.setattr(settings, "llm_api_key", "sk-ant-test")
    monkeypatch.setattr(llm, "ChatAnthropic", None)

    with pytest.raises(RuntimeError, match=r"anthropic\]"):
        llm._build_chat_model("anthropic")


def test_anthropic_uses_default_tool_use_not_strict_schema():
    # Claude has no json_schema response_format; it must not be forced into strict mode.
    assert "anthropic" not in llm._STRICT_SCHEMA_PROVIDERS


def test_factory_ollama_needs_no_key(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "ollama")
    monkeypatch.setattr(settings, "llm_api_key", "")
    monkeypatch.setattr(settings, "llm_base_url", "")
    monkeypatch.setattr(settings, "llm_model", "llama3")

    model = llm._build_chat_model("ollama")

    assert isinstance(model, ChatOpenAI)
    assert str(model.openai_api_base) == "http://localhost:11434/v1"


def test_factory_builds_openai_client_with_base_url_override(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "openai")
    monkeypatch.setattr(settings, "llm_api_key", "sk-test")
    monkeypatch.setattr(settings, "llm_base_url", "https://azure.example.com/v1")
    monkeypatch.setattr(settings, "llm_model", "gpt-4o-mini")

    model = llm._build_chat_model("openai")

    assert isinstance(model, ChatOpenAI)
    assert model.model_name == "gpt-4o-mini"
    assert str(model.openai_api_base) == "https://azure.example.com/v1"


def test_openai_falls_back_to_standard_openai_api_key(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "openai")
    monkeypatch.setattr(settings, "llm_api_key", "")  # generic var unset
    monkeypatch.setattr(settings, "llm_base_url", "")
    monkeypatch.setattr(settings, "llm_model", "gpt-4o-mini")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-from-env")

    model = llm._build_chat_model("openai")

    assert isinstance(model, ChatOpenAI)
    assert cast(SecretStr, model.openai_api_key).get_secret_value() == "sk-from-env"


def test_openai_missing_key_raises(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "openai")
    monkeypatch.setattr(settings, "llm_api_key", "")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        llm._build_chat_model("openai")


def test_openai_and_nvidia_use_strict_json_schema():
    # OpenAI + NVIDIA get native strict Structured Outputs; other providers use the default.
    captured: dict[str, object] = {}

    class _FakeStructured:
        def invoke(self, _messages):
            return llm.PatchOutput(instructions=[])

    class _FakeModel:
        def with_structured_output(self, _schema, **kwargs):
            captured.update(kwargs)
            return _FakeStructured()

    client = llm.LangChainClient(_FakeModel(), llm._STRICT_JSON_SCHEMA)  # type: ignore[arg-type]
    result = client.structured("sys", "usr", llm.PatchOutput)

    assert isinstance(result, llm.PatchOutput)
    assert captured == {"method": "json_schema", "strict": True}


def test_structured_raises_on_none_parse():
    class _FakeStructured:
        def invoke(self, _messages):
            return None

    class _FakeModel:
        def with_structured_output(self, _schema, **_kwargs):
            return _FakeStructured()

    client = llm.LangChainClient(_FakeModel())  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="llm_returned_no_parsed_output"):
        client.structured("sys", "usr", llm.PatchOutput)


def test_complete_raises_on_empty_content():
    class _FakeMessage:
        content = ""

    class _FakeModel:
        def invoke(self, _messages):
            return _FakeMessage()

    client = llm.LangChainClient(_FakeModel())  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="llm_returned_empty_completion"):
        client.complete("sys", "usr")
