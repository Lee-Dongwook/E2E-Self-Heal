"""The LLM client must be import-safe: no credentials needed to import or collect.

Also covers the provider-agnostic factory: the right chat model is built from
``settings.llm_provider`` and a missing key fails loudly for key-requiring providers.
"""

from typing import Any, TypedDict, cast

import pytest
from langchain_anthropic import ChatAnthropic  # pyright: ignore[reportMissingImports]
from langchain_ollama import ChatOllama  # pyright: ignore[reportMissingImports]
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


def test_factory_builds_orcarouter_openai_compatible_client(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "orcarouter")
    monkeypatch.setattr(settings, "llm_api_key", "router-key")
    monkeypatch.setattr(settings, "llm_base_url", "https://api.orcarouter.ai/v1")
    monkeypatch.setattr(settings, "llm_model", "router-model")

    model = llm._build_chat_model("orcarouter")

    assert isinstance(model, ChatOpenAI)
    assert model.model_name == "router-model"
    assert str(model.openai_api_base) == "https://api.orcarouter.ai/v1"


def test_orcarouter_missing_key_raises(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "orcarouter")
    monkeypatch.setattr(settings, "llm_api_key", "")
    monkeypatch.setattr(settings, "llm_model", "router-model")

    with pytest.raises(RuntimeError, match="E2E_HEALER_LLM_API_KEY"):
        llm._build_chat_model("orcarouter")


def test_factory_builds_anthropic_client(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "anthropic")
    monkeypatch.setattr(settings, "llm_api_key", "test-key")
    monkeypatch.setattr(settings, "llm_model", "claude-opus-4-8")

    model = llm._build_chat_model("anthropic")

    assert isinstance(model, ChatAnthropic)
    assert model.model == "claude-opus-4-8"  # pyright: ignore[reportAttributeAccessIssue]


def test_anthropic_falls_back_to_standard_anthropic_api_key(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "anthropic")
    monkeypatch.setattr(settings, "llm_api_key", "")  # generic var unset
    monkeypatch.setattr(settings, "llm_model", "claude-sonnet-4-6")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-from-env")

    model = llm._build_chat_model("anthropic")

    assert isinstance(model, ChatAnthropic)
    assert cast(SecretStr, model.anthropic_api_key).get_secret_value() == "sk-ant-from-env"  # pyright: ignore[reportAttributeAccessIssue]


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
    monkeypatch.setattr(settings, "llm_model", "llama3.1")

    model = llm._build_chat_model("ollama")

    assert isinstance(model, ChatOllama)
    assert model.base_url == "http://localhost:11434"  # pyright: ignore[reportAttributeAccessIssue]
    assert model.model == "llama3.1"  # pyright: ignore[reportAttributeAccessIssue]


def test_factory_ollama_honors_base_url_override(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "ollama")
    monkeypatch.setattr(settings, "llm_api_key", "")
    monkeypatch.setattr(settings, "llm_base_url", "http://remote-ollama:11434")
    monkeypatch.setattr(settings, "llm_model", "qwen2.5-coder")

    model = llm._build_chat_model("ollama")

    assert isinstance(model, ChatOllama)
    assert model.base_url == "http://remote-ollama:11434"  # pyright: ignore[reportAttributeAccessIssue]


def test_ollama_missing_extra_raises(monkeypatch):
    # When langchain-ollama isn't installed, provider=ollama must fail with an install hint.
    monkeypatch.setattr(settings, "llm_provider", "ollama")
    monkeypatch.setattr(settings, "llm_model", "llama3.1")
    monkeypatch.setattr(llm, "ChatOllama", None)

    with pytest.raises(RuntimeError, match=r"ollama\]"):
        llm._build_chat_model("ollama")


def test_ollama_uses_default_native_json_schema():
    # Ollama uses its native format=<schema> (default with_structured_output method),
    # not the OpenAI strict-schema kwargs which its client doesn't accept.
    assert "ollama" not in llm._STRICT_SCHEMA_PROVIDERS


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


# --- DeepSeek Responses API provider (mocked SDK, no live API calls) -------------------


class _ResponsesRequest(TypedDict, total=False):
    """Shape of a Responses API ``create`` request captured by the fake SDK."""

    model: str
    instructions: str
    input: str
    max_output_tokens: int
    text: dict[str, Any]


class _FakeResponses:
    """Stands in for OpenAI SDK ``client.responses`` and records create() kwargs."""

    def __init__(self) -> None:
        self.calls: list[_ResponsesRequest] = []
        self.output_text: str = ""

    def create(self, **kwargs: Any) -> "_FakeResponse":
        self.calls.append(cast(_ResponsesRequest, kwargs))
        return _FakeResponse(self.output_text)


class _FakeResponse:
    def __init__(self, output_text: str) -> None:
        self.output_text: str = output_text


class _FakeOpenAI:
    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        self.responses = _FakeResponses()


def _make_deepseek_client(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[llm.DeepSeekResponsesClient, _FakeResponses]:
    fake_openai = _FakeOpenAI()
    monkeypatch.setattr(llm, "OpenAI", lambda *_a, **_k: fake_openai)
    client = llm.DeepSeekResponsesClient(
        api_key="sk-test",
        base_url="https://api.deepseek.com",
        model="deepseek-v4-flash",
        max_output_tokens=512,
    )
    return client, fake_openai.responses


def test_factory_builds_deepseek_responses_client(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "llm_api_key", "sk-deepseek-test")
    monkeypatch.setattr(settings, "llm_base_url", "")
    monkeypatch.setattr(settings, "llm_model", "")
    monkeypatch.setattr(settings, "llm_max_tokens", 4096)

    client = llm._build_deepseek_client()

    assert isinstance(client, llm.DeepSeekResponsesClient)
    assert client._model == "deepseek-v4-flash"
    assert client._max_output_tokens == 4096
    assert str(client._client.base_url) == "https://api.deepseek.com"


def test_factory_deepseek_honors_base_url_and_model_override(monkeypatch: pytest.MonkeyPatch):
    # A custom base URL may serve models other than the Responses API default.
    monkeypatch.setattr(settings, "llm_api_key", "sk-deepseek-test")
    monkeypatch.setattr(settings, "llm_base_url", "https://proxy.example.com")
    monkeypatch.setattr(settings, "llm_model", "deepseek-v4-pro")

    client = llm._build_deepseek_client()

    assert client._model == "deepseek-v4-pro"
    assert str(client._client.base_url) == "https://proxy.example.com"


def test_deepseek_rejects_unsupported_model_on_default_endpoint(monkeypatch: pytest.MonkeyPatch):
    # The Responses API supports only deepseek-v4-flash on api.deepseek.com.
    monkeypatch.setattr(settings, "llm_api_key", "sk-deepseek-test")
    monkeypatch.setattr(settings, "llm_base_url", "")
    monkeypatch.setattr(settings, "llm_model", "deepseek-v4-pro")

    with pytest.raises(ValueError, match="deepseek-v4-flash"):
        llm._build_deepseek_client()


def test_deepseek_falls_back_to_standard_deepseek_api_key(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "llm_api_key", "")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-ds-from-env")

    assert llm._deepseek_api_key() == "sk-ds-from-env"


def test_deepseek_missing_key_raises(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "llm_api_key", "")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    with pytest.raises(RuntimeError, match="DEEPSEEK_API_KEY"):
        llm._deepseek_api_key()


def test_deepseek_uses_responses_api_not_langchain_strict_schema():
    # DeepSeek is served by DeepSeekResponsesClient (Responses API text.format),
    # not the LangChain strict json_schema kwargs used for OpenAI/NVIDIA.
    assert "deepseek" not in llm._STRICT_SCHEMA_PROVIDERS


def test_deepseek_complete_sends_instructions_input_and_returns_text(
    monkeypatch: pytest.MonkeyPatch,
):
    client, fake = _make_deepseek_client(monkeypatch)
    fake.output_text = "diagnosis text"

    result = client.complete("SYS", "USR")

    assert result == "diagnosis text"
    assert fake.calls == [
        {
            "model": "deepseek-v4-flash",
            "instructions": "SYS",
            "input": "USR",
            "max_output_tokens": 512,
        }
    ]


def test_deepseek_complete_raises_on_empty_text(monkeypatch: pytest.MonkeyPatch):
    client, _fake = _make_deepseek_client(monkeypatch)

    with pytest.raises(ValueError, match="llm_returned_empty_completion"):
        client.complete("SYS", "USR")


def test_deepseek_structured_sends_json_schema_format_and_parses(monkeypatch: pytest.MonkeyPatch):
    client, fake = _make_deepseek_client(monkeypatch)
    fake.output_text = '{"instructions": []}'

    result = client.structured("SYS", "USR", llm.PatchOutput)

    assert isinstance(result, llm.PatchOutput)
    call = fake.calls[0]
    assert call.get("instructions") == "SYS"
    assert call.get("input") == "USR"
    fmt = call.get("text", {})["format"]
    assert fmt["type"] == "json_schema"
    assert fmt["name"] == "PatchOutput"
    assert fmt["strict"] is True
    assert fmt["schema"]["title"] == "PatchOutput"


def test_deepseek_structured_raises_on_invalid_json(monkeypatch: pytest.MonkeyPatch):
    client, fake = _make_deepseek_client(monkeypatch)
    fake.output_text = "not json"

    with pytest.raises(ValueError, match="llm_returned_no_parsed_output"):
        client.structured("SYS", "USR", llm.PatchOutput)


def test_get_client_builds_deepseek_responses_client(monkeypatch: pytest.MonkeyPatch):
    llm._get_client.cache_clear()
    monkeypatch.setattr(settings, "llm_provider", "deepseek")
    monkeypatch.setattr(llm, "_build_deepseek_client", lambda: "deepseek-client")

    assert llm._get_client() == "deepseek-client"
    llm._get_client.cache_clear()


# --- Adapter happy paths with mocked chat models (no live API calls) ------------------


def test_complete_returns_text_and_sends_system_then_user():
    captured: dict[str, object] = {}

    class _FakeMessage:
        content = "diagnosis text"

    class _FakeModel:
        def invoke(self, messages):
            captured["messages"] = messages
            return _FakeMessage()

    client = llm.LangChainClient(_FakeModel())  # type: ignore[arg-type]
    result = client.complete("SYS", "USR")

    assert result == "diagnosis text"
    assert captured["messages"] == [("system", "SYS"), ("human", "USR")]


def test_structured_returns_validated_model_and_sends_messages():
    captured: dict[str, object] = {}

    class _FakeStructured:
        def invoke(self, messages):
            captured["messages"] = messages
            return llm.PatchOutput(instructions=[])

    class _FakeModel:
        def with_structured_output(self, _schema, **_kwargs):
            return _FakeStructured()

    client = llm.LangChainClient(_FakeModel())  # type: ignore[arg-type]
    result = client.structured("SYS", "USR", llm.PatchOutput)

    assert isinstance(result, llm.PatchOutput)
    assert captured["messages"] == [("system", "SYS"), ("human", "USR")]


def test_extract_text_flattens_content_blocks():
    # Providers like Anthropic can return content as a list of blocks, not a plain string.
    class _FakeMessage:
        content = [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}, "c"]

    assert llm._extract_text(_FakeMessage()) == "abc"  # type: ignore[arg-type]


# --- Public functions delegate to the client (the surface the nodes import) -----------


class _RecordingClient:
    """A stand-in LLMClient that records calls and returns canned, schema-valid results."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        self.calls.append(("complete", system_prompt, user_prompt))
        return "diagnosis"

    def structured(self, system_prompt, user_prompt, schema):
        self.calls.append(("structured", system_prompt, user_prompt, schema))
        if schema is llm.ReviewOutput:
            return llm.ReviewOutput(findings=[])
        return llm.PatchOutput(instructions=[])


def test_generate_diagnosis_delegates_to_complete(monkeypatch):
    fake = _RecordingClient()
    monkeypatch.setattr(llm, "_get_client", lambda: fake)

    assert llm.generate_diagnosis("sys", "usr") == "diagnosis"
    assert fake.calls == [("complete", "sys", "usr")]


def test_generate_patch_delegates_to_structured(monkeypatch):
    fake = _RecordingClient()
    monkeypatch.setattr(llm, "_get_client", lambda: fake)

    out = llm.generate_patch("sys", "usr")

    assert isinstance(out, llm.PatchOutput)
    assert fake.calls[0][:3] == ("structured", "sys", "usr")
    assert fake.calls[0][3] is llm.PatchOutput


def test_generate_review_delegates_to_structured(monkeypatch):
    fake = _RecordingClient()
    monkeypatch.setattr(llm, "_get_client", lambda: fake)

    out = llm.generate_review("sys", "usr")

    assert isinstance(out, llm.ReviewOutput)
    assert fake.calls[0][3] is llm.ReviewOutput


def test_generate_patch_surfaces_client_error_for_retry_and_feedback(monkeypatch):
    # A structured parse failure must propagate out of the (undecorated) call so the tenacity
    # wrapper retries and, on exhaustion, the Patch Generator feedback loop engages.
    class _FailingClient:
        def structured(self, *_args, **_kwargs):
            raise ValueError("llm_returned_no_parsed_output")

    monkeypatch.setattr(llm, "_get_client", lambda: _FailingClient())

    undecorated = getattr(llm.generate_patch, "__wrapped__")  # bypass tenacity's backoff waits
    with pytest.raises(ValueError, match="llm_returned_no_parsed_output"):
        undecorated("sys", "usr")
