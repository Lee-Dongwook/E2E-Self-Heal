"""Provider-agnostic LLM client built on LangChain chat models.

Hides per-provider differences (endpoints, credentials, Structured Outputs mechanics)
behind one interface with two operations: free-text completion and schema-validated
structured completion (Pydantic schema in -> validated model out). A factory selects the
concrete chat model from ``settings.llm_provider`` so users can plug in OpenAI, Anthropic,
Ollama, or any OpenAI-compatible endpoint (NVIDIA NIM) without touching node code.

The default model (``openai/gpt-oss-120b`` on NVIDIA NIM) is a reasoning model, so
completions are given explicit token headroom (``settings.llm_max_tokens``) to leave room
for its reasoning content.
"""

import os
from functools import lru_cache
from typing import Any, Protocol, TypeVar

import structlog
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel
from tenacity import retry, stop_after_attempt, wait_exponential

from app.config import LLMProvider, settings
from app.schemas import PatchOutput, ReviewOutput

# Anthropic and Ollama are optional dependencies: users who don't use them shouldn't have to
# install langchain-anthropic / langchain-ollama. The guarded imports stay at module top per
# the imports-at-top rule; the matching provider branch checks availability at build time.
try:
    from langchain_anthropic import ChatAnthropic  # pyright: ignore[reportMissingImports]
except ImportError:  # pragma: no cover - exercised only without the extra installed
    ChatAnthropic = None

try:
    from langchain_ollama import ChatOllama  # pyright: ignore[reportMissingImports]
except ImportError:  # pragma: no cover - exercised only without the extra installed
    ChatOllama = None

logger = structlog.get_logger(__name__)

SchemaT = TypeVar("SchemaT", bound=BaseModel)

# Ollama runs models locally with no credential; the native endpoint (no OpenAI /v1 path).
_OLLAMA_DEFAULT_BASE_URL = "http://localhost:11434"

# OpenAI's native Structured Outputs: strict json_schema guarantees the response matches
# PatchOutput/ReviewOutput exactly (the same mechanism the original beta.parse used). Applied
# to the OpenAI-SDK-driven providers; other backends fall back to their default method.
_STRICT_JSON_SCHEMA: dict[str, Any] = {"method": "json_schema", "strict": True}
_STRICT_SCHEMA_PROVIDERS: frozenset[str] = frozenset({"openai", "nvidia"})


class LLMClient(Protocol):
    """Provider-agnostic surface the nodes call: free-text and structured completion.

    Implementations must ``raise`` on an empty/unparsed response (never return ``None``)
    so the tenacity retry and the Patch Generator feedback loop keep working.
    """

    def complete(self, system_prompt: str, user_prompt: str) -> str: ...

    def structured(
        self, system_prompt: str, user_prompt: str, schema: type[SchemaT]
    ) -> SchemaT: ...


def _extract_text(message: BaseMessage) -> str:
    """Flatten an LLM message's content to plain text (providers may return content blocks)."""
    content = message.content
    if isinstance(content, str):
        return content
    parts = [block if isinstance(block, str) else str(block.get("text", "")) for block in content]
    return "".join(parts)


class LangChainClient:
    """Adapts a LangChain ``BaseChatModel`` to the ``LLMClient`` interface.

    ``with_structured_output`` normalizes each provider's Structured Outputs mechanism
    (OpenAI json-schema/tool-calling, Anthropic tool use) into one call that returns a
    validated Pydantic model, so the schema enforcement (PatchOutput/ReviewOutput) holds
    regardless of backend.
    """

    def __init__(
        self, model: BaseChatModel, structured_kwargs: dict[str, Any] | None = None
    ) -> None:
        self._model = model
        # Per-provider Structured Outputs tuning (e.g. OpenAI/NVIDIA use strict json_schema,
        # which is OpenAI's native Structured Outputs). Empty = the model's default method.
        self._structured_kwargs = structured_kwargs or {}

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        messages = [("system", system_prompt), ("human", user_prompt)]
        response = self._model.invoke(messages)
        content = _extract_text(response)
        if not content:
            logger.warning("llm_returned_empty_completion")
            raise ValueError("llm_returned_empty_completion")
        return content

    def structured(self, system_prompt: str, user_prompt: str, schema: type[SchemaT]) -> SchemaT:
        messages = [("system", system_prompt), ("human", user_prompt)]
        runnable = self._model.with_structured_output(schema, **self._structured_kwargs)
        parsed = runnable.invoke(messages)
        if not isinstance(parsed, schema):
            logger.warning("llm_returned_no_parsed_output", schema=schema.__name__)
            raise ValueError("llm_returned_no_parsed_output")
        return parsed


def _require_key() -> str:
    """Return the configured API key, or raise so callers fail loudly (not silently)."""
    if not settings.llm_api_key:
        raise RuntimeError(
            "E2E_HEALER_LLM_API_KEY is not set for provider=" + settings.llm_provider
        )
    return settings.llm_api_key


def _openai_api_key() -> str:
    """OpenAI key: prefer the generic setting, else the standard ``OPENAI_API_KEY`` env var.

    Lets a user with an existing ``OPENAI_API_KEY`` drop in without duplicating it under the
    ``E2E_HEALER_`` prefix.
    """
    key = settings.llm_api_key or os.environ.get("OPENAI_API_KEY", "")
    if not key:
        raise RuntimeError(
            "E2E_HEALER_LLM_API_KEY or OPENAI_API_KEY is not set for provider=openai"
        )
    return key


def _anthropic_api_key() -> str:
    """Anthropic key: prefer the generic setting, else the standard ``ANTHROPIC_API_KEY`` env var.

    Lets a user with an existing ``ANTHROPIC_API_KEY`` drop in without duplicating it under
    the ``E2E_HEALER_`` prefix.
    """
    key = settings.llm_api_key or os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        raise RuntimeError(
            "E2E_HEALER_LLM_API_KEY or ANTHROPIC_API_KEY is not set for provider=anthropic"
        )
    return key


def _build_chat_model(provider: LLMProvider) -> BaseChatModel:
    """Construct the LangChain chat model for ``provider`` from settings (the factory core).

    Model kwargs are splatted from a dict because the chat-model classes expose ``model`` /
    ``max_tokens`` as pydantic aliases the type checker can't see on the constructor.
    """
    if provider == "anthropic":
        if ChatAnthropic is None:
            raise RuntimeError(
                "provider=anthropic requires the optional 'anthropic' extra: "
                'install it with pip install "ai-driven-e2e[anthropic]"'
            )
        # Anthropic has no OpenAI-style response_format; with_structured_output maps
        # PatchOutput/ReviewOutput onto Claude tool-use (the default method), so the schema
        # is still enforced. That's why anthropic is absent from _STRICT_SCHEMA_PROVIDERS.
        params: dict[str, Any] = {
            "model": settings.llm_model,
            "api_key": _anthropic_api_key(),
            "max_tokens": settings.llm_max_tokens,
        }
        return ChatAnthropic(**params)
    if provider == "ollama":
        if ChatOllama is None:
            raise RuntimeError(
                "provider=ollama requires the optional 'ollama' extra: "
                'install it with pip install "ai-driven-e2e[ollama]"'
            )
        # Fully local, no API key. Ollama's native structured output (format=<json schema>,
        # the default with_structured_output method) validates against PatchOutput/ReviewOutput;
        # a parse/validation failure raises out of structured() so tenacity retries and, on
        # exhaustion, the Patch Generator feedback loop kicks in (local models are less reliable
        # at strict JSON). num_predict is Ollama's token-cap name.
        params = {
            "model": settings.llm_model,
            "base_url": settings.llm_base_url or _OLLAMA_DEFAULT_BASE_URL,
            "num_predict": settings.llm_max_tokens,
        }
        return ChatOllama(**params)
    if provider == "openai":
        # Native OpenAI. base_url stays empty for api.openai.com, or points at an
        # Azure / OpenAI-compatible endpoint via E2E_HEALER_LLM_BASE_URL.
        params = {
            "model": settings.llm_model,
            "api_key": _openai_api_key(),
            "base_url": settings.llm_base_url or None,
            "max_tokens": settings.llm_max_tokens,
        }
        return ChatOpenAI(**params)
    # nvidia: OpenAI-compatible NIM endpoint driven by the OpenAI SDK; needs an explicit
    # base_url (folded in from the legacy nvidia_* settings by the config layer).
    params = {
        "model": settings.llm_model,
        "api_key": _require_key(),
        "base_url": settings.llm_base_url or None,
        "max_tokens": settings.llm_max_tokens,
    }
    return ChatOpenAI(**params)


@lru_cache(maxsize=1)
def _get_client() -> LLMClient:
    """Build the provider client lazily so importing never needs credentials.

    Instantiating at import time would require an API key just to collect tests or run
    ``--help``; deferring it here keeps import side-effect-free and fails only when the
    LLM is actually called without a key configured.
    """
    provider = settings.llm_provider
    structured_kwargs = _STRICT_JSON_SCHEMA if provider in _STRICT_SCHEMA_PROVIDERS else {}
    return LangChainClient(_build_chat_model(provider), structured_kwargs)


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
def generate_diagnosis(system_prompt: str, user_prompt: str) -> str:
    """Call the LLM for a free-text failure diagnosis (the Diagnoser node)."""
    return _get_client().complete(system_prompt, user_prompt)


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
def generate_patch(system_prompt: str, user_prompt: str) -> PatchOutput:
    """Call the LLM with an enforced PatchOutput schema and return the parsed result.

    Raises on a missing/malformed parse so tenacity retries; the Patch Generator node
    is responsible for the feedback loop when retries are exhausted.
    """
    return _get_client().structured(system_prompt, user_prompt, PatchOutput)


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
def generate_review(system_prompt: str, user_prompt: str) -> ReviewOutput:
    """Call the LLM with an enforced ReviewOutput schema for source-level suggestions.

    Mirrors ``generate_patch``: Structured Outputs keep the Reviewer to advisory findings
    (never free-form rewrites). Raises on a missing parse so tenacity retries.
    """
    return _get_client().structured(system_prompt, user_prompt, ReviewOutput)
