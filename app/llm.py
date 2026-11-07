"""Provider-agnostic LLM client built on LangChain chat models.

Hides per-provider differences (endpoints, credentials, Structured Outputs mechanics)
behind one interface with two operations: free-text completion and schema-validated
structured completion (Pydantic schema in -> validated model out). A factory selects the
concrete chat model from ``settings.llm_provider`` so users can plug in OpenAI, Anthropic,
Ollama, or any OpenAI-compatible endpoint (NVIDIA NIM) without touching node code.

Completions are given explicit token headroom (``settings.llm_max_tokens``) because reasoning
models, such as some NVIDIA NIM models, spend part of it on reasoning content before the answer.
"""

import os
from contextvars import ContextVar
from functools import lru_cache
from typing import Any, Protocol, TypeVar

import structlog
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage
from langchain_openai import ChatOpenAI
from openai import LengthFinishReasonError, OpenAI
from pydantic import BaseModel, ValidationError
from tenacity import RetryCallState, retry, stop_after_attempt, wait_exponential

from app.config import LLMProvider, settings
from app.providers.orcarouter import OrcaRouterProvider
from app.schemas import DiagnosisVerdictOutput, PatchOutput, ReviewOutput

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

# Per-provider endpoints, used both to build the client and to report provenance. Ollama runs
# models locally with no credential; the native endpoint (no OpenAI /v1 path).
_OLLAMA_DEFAULT_BASE_URL = "http://localhost:11434"
_ANTHROPIC_DEFAULT_BASE_URL = "https://api.anthropic.com"
_OPENAI_DEFAULT_BASE_URL = "https://api.openai.com/v1"
_NVIDIA_DEFAULT_BASE_URL = "https://integrate.api.nvidia.com/v1"
_ORCAROUTER_DEFAULT_BASE_URL = "https://api.orcarouter.ai/v1"

# DeepSeek's Responses API endpoint. It is stateless (no chat-completions /v1 path), and the
# Responses API currently supports the deepseek-v4-flash model only.
_DEEPSEEK_DEFAULT_BASE_URL = "https://api.deepseek.com"
_DEEPSEEK_DEFAULT_MODEL = "deepseek-v4-flash"

# OpenAI's native Structured Outputs: strict json_schema guarantees the response matches
# PatchOutput/ReviewOutput exactly (the same mechanism the original beta.parse used). Applied
# to the OpenAI-SDK-driven providers; other backends fall back to their default method.
_STRICT_JSON_SCHEMA: dict[str, Any] = {"method": "json_schema", "strict": True}
_STRICT_SCHEMA_PROVIDERS: frozenset[str] = frozenset({"openai", "nvidia"})


class OutputLimitReached(ValueError):
    """The provider stopped at the output-token limit before the answer was complete (#453).

    Reasoning models can spend the whole budget thinking and return nothing usable. The
    tenacity wrappers below retry such a call with a larger budget instead of the same one.
    """

    def __init__(self, budget: int | None) -> None:
        self.budget = budget
        limit = f"the {budget}-token" if budget else "the"
        super().__init__(
            f"the model stopped at {limit} output limit before finishing its answer; "
            "raise E2E_HEALER_LLM_MAX_TOKENS if this keeps happening"
        )


class LLMClient(Protocol):
    """Provider-agnostic surface the nodes call: free-text and structured completion.

    Implementations must ``raise`` on an empty/unparsed response (never return ``None``)
    so the tenacity retry and the Patch Generator feedback loop keep working, and raise
    :class:`OutputLimitReached` when the response was cut off by the output-token limit.
    ``max_tokens`` overrides the configured output budget for one call; ``None`` keeps it.
    """

    def complete(
        self, system_prompt: str, user_prompt: str, *, max_tokens: int | None = None
    ) -> str: ...

    def structured(
        self,
        system_prompt: str,
        user_prompt: str,
        schema: type[SchemaT],
        *,
        max_tokens: int | None = None,
    ) -> SchemaT: ...


def _extract_text(message: BaseMessage) -> str:
    """Flatten an LLM message's content to plain text (providers may return content blocks)."""
    content = message.content
    if isinstance(content, str):
        return content
    parts = [block if isinstance(block, str) else str(block.get("text", "")) for block in content]
    return "".join(parts)


def _stopped_at_output_limit(message: BaseMessage) -> bool:
    """Whether the provider reports the message was cut off by the output-token limit.

    Each LangChain chat model reports it under its own key: OpenAI-compatible endpoints
    ``finish_reason="length"``, Anthropic ``stop_reason="max_tokens"``, Ollama
    ``done_reason="length"``.
    """
    metadata = getattr(message, "response_metadata", None) or {}
    return (
        metadata.get("finish_reason") == "length"
        or metadata.get("stop_reason") == "max_tokens"
        or metadata.get("done_reason") == "length"
    )


class LangChainClient:
    """Adapts a LangChain ``BaseChatModel`` to the ``LLMClient`` interface.

    ``with_structured_output`` normalizes each provider's Structured Outputs mechanism
    (OpenAI json-schema/tool-calling, Anthropic tool use) into one call that returns a
    validated Pydantic model, so the schema enforcement (PatchOutput/ReviewOutput) holds
    regardless of backend.
    """

    def __init__(
        self,
        model: BaseChatModel,
        structured_kwargs: dict[str, Any] | None = None,
        budget_field: str = "max_tokens",
    ) -> None:
        self._model = model
        # Per-provider Structured Outputs tuning (e.g. OpenAI/NVIDIA use strict json_schema,
        # which is OpenAI's native Structured Outputs). Empty = the model's default method.
        self._structured_kwargs = structured_kwargs or {}
        # The chat model's output-budget field: `max_tokens`, or Ollama's `num_predict`.
        self._budget_field = budget_field

    def _model_with_budget(self, max_tokens: int | None) -> BaseChatModel:
        if max_tokens is None:
            return self._model
        # A shallow copy shares the underlying HTTP clients; only the budget differs.
        return self._model.model_copy(update={self._budget_field: max_tokens})

    def _budget(self, max_tokens: int | None) -> int | None:
        return (
            max_tokens if max_tokens is not None else getattr(self._model, self._budget_field, None)
        )

    def complete(
        self, system_prompt: str, user_prompt: str, *, max_tokens: int | None = None
    ) -> str:
        messages = [("system", system_prompt), ("human", user_prompt)]
        response = self._model_with_budget(max_tokens).invoke(messages)
        if _stopped_at_output_limit(response):
            # Even a non-empty answer is incomplete; a truncated diagnosis misleads the patch.
            logger.warning("llm_output_limit_reached", budget=self._budget(max_tokens))
            raise OutputLimitReached(self._budget(max_tokens))
        content = _extract_text(response)
        if not content:
            logger.warning("llm_returned_empty_completion")
            raise ValueError("llm_returned_empty_completion")
        return content

    def structured(
        self,
        system_prompt: str,
        user_prompt: str,
        schema: type[SchemaT],
        *,
        max_tokens: int | None = None,
    ) -> SchemaT:
        messages = [("system", system_prompt), ("human", user_prompt)]
        model = self._model_with_budget(max_tokens)
        runnable = model.with_structured_output(schema, **self._structured_kwargs)
        try:
            parsed = runnable.invoke(messages)
        except LengthFinishReasonError as exc:
            # OpenAI-compatible strict json_schema parsing raises this on finish_reason=length.
            logger.warning(
                "llm_output_limit_reached", budget=self._budget(max_tokens), schema=schema.__name__
            )
            raise OutputLimitReached(self._budget(max_tokens)) from exc
        if not isinstance(parsed, schema):
            logger.warning("llm_returned_no_parsed_output", schema=schema.__name__)
            raise ValueError("llm_returned_no_parsed_output")
        return parsed


class DeepSeekResponsesClient:
    """Calls DeepSeek's Responses API directly through the OpenAI SDK.

    DeepSeek exposes the Responses API shape (``client.responses.create``) at
    ``https://api.deepseek.com``; unlike the LangChain chat path used by the other
    providers, the Responses API is stateless so every call carries the full prompt.
    Structured output is requested with the Responses API ``text.format`` json_schema
    parameter (documented as supported by DeepSeek) and the returned JSON is then
    validated against the Pydantic schema, so PatchOutput/ReviewOutput enforcement
    holds without LangChain.
    """

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        max_output_tokens: int,
        timeout_seconds: float,
    ) -> None:
        # max_retries=0: tenacity, around every call, is the only retry layer (#471).
        self._client = OpenAI(
            api_key=api_key, base_url=base_url, timeout=timeout_seconds, max_retries=0
        )
        self._model = model
        self._max_output_tokens = max_output_tokens

    def _create(self, max_tokens: int | None, **request: Any) -> Any:
        budget = max_tokens if max_tokens is not None else self._max_output_tokens
        response = self._client.responses.create(
            model=self._model, max_output_tokens=budget, **request
        )
        details = getattr(response, "incomplete_details", None)
        if (
            getattr(response, "status", None) == "incomplete"
            and getattr(details, "reason", None) == "max_output_tokens"
        ):
            logger.warning("llm_output_limit_reached", budget=budget)
            raise OutputLimitReached(budget)
        return response

    def complete(
        self, system_prompt: str, user_prompt: str, *, max_tokens: int | None = None
    ) -> str:
        response = self._create(max_tokens, instructions=system_prompt, input=user_prompt)
        text = response.output_text or ""
        if not text:
            logger.warning("llm_returned_empty_completion")
            raise ValueError("llm_returned_empty_completion")
        return text

    def structured(
        self,
        system_prompt: str,
        user_prompt: str,
        schema: type[SchemaT],
        *,
        max_tokens: int | None = None,
    ) -> SchemaT:
        response = self._create(
            max_tokens,
            instructions=system_prompt,
            input=user_prompt,
            text={
                "format": {
                    "type": "json_schema",
                    "name": schema.__name__,
                    "strict": True,
                    "schema": schema.model_json_schema(),
                }
            },
        )
        text = response.output_text or ""
        try:
            parsed = schema.model_validate_json(text)
        except ValidationError:
            logger.warning("llm_returned_no_parsed_output", schema=schema.__name__)
            raise ValueError("llm_returned_no_parsed_output") from None
        return parsed


def resolved_endpoint(provider: LLMProvider) -> str:
    """Return the endpoint the client for ``provider`` talks to.

    Single source of truth shared by the client factory and by provider provenance, so the
    reported endpoint is the one evidence really left for rather than the raw configuration
    (an empty ``E2E_HEALER_LLM_BASE_URL`` means the provider default). Anthropic currently
    has no configurable endpoint: ``ChatAnthropic`` is built without a ``base_url``.
    """
    if provider == "anthropic":
        return _ANTHROPIC_DEFAULT_BASE_URL
    if provider == "ollama":
        return settings.llm_base_url or _OLLAMA_DEFAULT_BASE_URL
    if provider == "deepseek":
        return settings.llm_base_url or _DEEPSEEK_DEFAULT_BASE_URL
    if provider == "openai":
        return settings.llm_base_url or _OPENAI_DEFAULT_BASE_URL
    if provider == "orcarouter":
        return settings.llm_base_url or _ORCAROUTER_DEFAULT_BASE_URL
    return settings.llm_base_url or _NVIDIA_DEFAULT_BASE_URL


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


def _deepseek_api_key() -> str:
    """DeepSeek key: prefer the generic setting, else the standard ``DEEPSEEK_API_KEY`` env var."""
    key = settings.llm_api_key or os.environ.get("DEEPSEEK_API_KEY", "")
    if not key:
        raise RuntimeError(
            "E2E_HEALER_LLM_API_KEY or DEEPSEEK_API_KEY is not set for provider=deepseek"
        )
    return key


def _build_deepseek_client() -> DeepSeekResponsesClient:
    """Build the DeepSeek Responses API client from settings.

    The DeepSeek Responses API currently supports only ``deepseek-v4-flash``, so an
    explicit model override is rejected unless the base URL points at a custom
    (OpenAI-compatible) endpoint that may serve other models.
    """
    base_url = resolved_endpoint("deepseek")
    model = settings.llm_model or _DEEPSEEK_DEFAULT_MODEL
    if base_url == _DEEPSEEK_DEFAULT_BASE_URL and model != _DEEPSEEK_DEFAULT_MODEL:
        raise ValueError(
            f"model={model!r} is not supported by the DeepSeek Responses API at "
            f"{_DEEPSEEK_DEFAULT_BASE_URL}; use {_DEEPSEEK_DEFAULT_MODEL!r} or point "
            "E2E_HEALER_LLM_BASE_URL at a compatible OpenAI-compatible endpoint"
        )
    return DeepSeekResponsesClient(
        api_key=_deepseek_api_key(),
        base_url=base_url,
        model=model,
        max_output_tokens=settings.llm_max_tokens,
        timeout_seconds=settings.llm_timeout_seconds,
    )


def _build_chat_model(provider: LLMProvider) -> BaseChatModel:
    """Construct the LangChain chat model for ``provider`` from settings (the factory core).

    Model kwargs are splatted from a dict because the chat-model classes expose ``model`` /
    ``max_tokens`` as pydantic aliases the type checker can't see on the constructor.

    Every client gets ``settings.llm_timeout_seconds`` (#471): LangChain's defaults pass
    ``timeout=None`` to the SDKs, which disables their own limits, so a provider that accepts
    the connection and never answers would block forever; the tenacity retries can't help with
    a call that never returns. A timeout raises like any other provider error.

    SDK-level retries are off (``max_retries=0``) so tenacity, wrapped around every call, is
    the only retry layer: otherwise each tenacity attempt hides up to three SDK attempts, and
    one logical call could take 3 x 3 x the timeout. Ollama's client has no retries of its own.
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
            "timeout": settings.llm_timeout_seconds,
            "max_retries": 0,
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
            "base_url": resolved_endpoint("ollama"),
            "num_predict": settings.llm_max_tokens,
            # ChatOllama has no timeout field; its httpx client takes one.
            "client_kwargs": {"timeout": settings.llm_timeout_seconds},
        }
        return ChatOllama(**params)
    if provider == "openai":
        # Native OpenAI, or an Azure / OpenAI-compatible endpoint via
        # E2E_HEALER_LLM_BASE_URL; the resolved default is api.openai.com.
        params = {
            "model": settings.llm_model,
            "api_key": _openai_api_key(),
            "base_url": resolved_endpoint("openai"),
            "max_tokens": settings.llm_max_tokens,
            "timeout": settings.llm_timeout_seconds,
            "max_retries": 0,
        }
        return ChatOpenAI(**params)
    if provider == "orcarouter":
        return OrcaRouterProvider(
            api_key=_require_key(),
            base_url=resolved_endpoint("orcarouter"),
            model=settings.llm_model,
            max_tokens=settings.llm_max_tokens,
            timeout_seconds=settings.llm_timeout_seconds,
        ).build_chat_model()
    # nvidia: OpenAI-compatible NIM endpoint driven by the OpenAI SDK; needs an explicit
    # base_url (folded in from the legacy nvidia_* settings by the config layer).
    params = {
        "model": settings.llm_model,
        "api_key": _require_key(),
        "base_url": resolved_endpoint("nvidia"),
        "max_tokens": settings.llm_max_tokens,
        "timeout": settings.llm_timeout_seconds,
        "max_retries": 0,
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
    if provider == "deepseek":
        # DeepSeek uses the Responses API (stateless), not the LangChain chat path.
        return _build_deepseek_client()
    structured_kwargs = _STRICT_JSON_SCHEMA if provider in _STRICT_SCHEMA_PROVIDERS else {}
    budget_field = "num_predict" if provider == "ollama" else "max_tokens"
    return LangChainClient(_build_chat_model(provider), structured_kwargs, budget_field)


# The output budget for the current attempt of an LLM call. `None` means the configured
# `settings.llm_max_tokens`; it only changes after a response was cut off (#453).
_output_budget: ContextVar[int | None] = ContextVar("llm_output_budget", default=None)
# A cut-off doubles the budget for the next attempt, up to this multiple of the configured one:
# 4096 -> 8192 -> 16384 over tenacity's three attempts.
_OUTPUT_BUDGET_CEILING_FACTOR = 4


def _reset_output_budget(retry_state: RetryCallState) -> None:
    if retry_state.attempt_number == 1:
        _output_budget.set(None)


def _raise_output_budget_after_cut_off(retry_state: RetryCallState) -> None:
    """Before the next attempt, double the budget if the last one was cut off by it.

    Retrying a cut-off call with the same budget fails the same way: a reasoning model
    that spent all of it thinking will do so again. Other failures keep the budget.
    """
    outcome = retry_state.outcome
    if outcome is None or not outcome.failed:
        return
    if not isinstance(outcome.exception(), OutputLimitReached):
        return
    configured = settings.llm_max_tokens
    current = _output_budget.get() or configured
    raised = min(current * 2, configured * _OUTPUT_BUDGET_CEILING_FACTOR)
    if raised > current:
        logger.info("llm_output_budget_raised", from_tokens=current, to_tokens=raised)
        _output_budget.set(raised)


def _llm_retry() -> Any:
    return retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=10),
        before=_reset_output_budget,
        after=_raise_output_budget_after_cut_off,
    )


@_llm_retry()
def generate_diagnosis(system_prompt: str, user_prompt: str) -> str:
    """Call the LLM for a free-text failure diagnosis (the Diagnoser node)."""
    return _get_client().complete(system_prompt, user_prompt, max_tokens=_output_budget.get())


@_llm_retry()
def generate_patch(system_prompt: str, user_prompt: str) -> PatchOutput:
    """Call the LLM with an enforced PatchOutput schema and return the parsed result.

    Raises on a missing/malformed parse so tenacity retries; the Patch Generator node
    is responsible for the feedback loop when retries are exhausted.
    """
    return _get_client().structured(
        system_prompt, user_prompt, PatchOutput, max_tokens=_output_budget.get()
    )


@_llm_retry()
def generate_review(system_prompt: str, user_prompt: str) -> ReviewOutput:
    """Call the LLM with an enforced ReviewOutput schema for source-level suggestions.

    Mirrors ``generate_patch``: Structured Outputs keep the Reviewer to advisory findings
    (never free-form rewrites). Raises on a missing parse so tenacity retries.
    """
    return _get_client().structured(
        system_prompt, user_prompt, ReviewOutput, max_tokens=_output_budget.get()
    )


@_llm_retry()
def generate_diagnosis_verdict(system_prompt: str, user_prompt: str) -> DiagnosisVerdictOutput:
    """Call the LLM with an enforced DiagnosisVerdictOutput schema (the decline adjudicator).

    Mirrors ``generate_patch``: a structured call, so an unparseable verdict retries and then
    raises. The adjudicator treats exhausted retries as "no verdict" rather than as evidence.
    """
    return _get_client().structured(
        system_prompt, user_prompt, DiagnosisVerdictOutput, max_tokens=_output_budget.get()
    )
