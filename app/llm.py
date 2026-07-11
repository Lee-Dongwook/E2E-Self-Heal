"""NVIDIA NIM client wrapper enforcing Structured Outputs for deterministic patches.

Uses NVIDIA's OpenAI-compatible endpoint, so the OpenAI SDK drives it unchanged apart
from ``base_url``. The default model (``openai/gpt-oss-120b``) is a reasoning model, so
completions are given explicit token headroom to leave room for its ``reasoning_content``.
"""

from functools import lru_cache

import structlog
from openai import OpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

from app.config import settings
from app.schemas import PatchOutput, ReviewOutput

logger = structlog.get_logger(__name__)


@lru_cache(maxsize=1)
def _get_client() -> OpenAI:
    """Build the OpenAI-compatible client lazily so importing never needs credentials.

    Instantiating at import time would require an API key just to collect tests or run
    ``--help``; deferring it here keeps import side-effect-free and fails only when the
    LLM is actually called without a key configured.
    """
    if not settings.nvidia_api_key:
        raise RuntimeError("E2E_HEALER_NVIDIA_API_KEY is not set")
    return OpenAI(base_url=settings.nvidia_base_url, api_key=settings.nvidia_api_key)


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
def generate_diagnosis(system_prompt: str, user_prompt: str) -> str:
    """Call the LLM for a free-text failure diagnosis (the Diagnoser node)."""
    completion = _get_client().chat.completions.create(
        model=settings.nvidia_model,
        max_tokens=settings.nvidia_max_tokens,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    )
    content = completion.choices[0].message.content
    if not content:
        logger.warning("llm_returned_empty_diagnosis")
        raise ValueError("llm_returned_empty_diagnosis")
    return content


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
def generate_patch(system_prompt: str, user_prompt: str) -> PatchOutput:
    """Call the LLM with an enforced PatchOutput schema and return the parsed result.

    Raises on a missing/malformed parse so tenacity retries; the Patch Generator node
    is responsible for the feedback loop when retries are exhausted.
    """
    completion = _get_client().beta.chat.completions.parse(
        model=settings.nvidia_model,
        max_tokens=settings.nvidia_max_tokens,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        response_format=PatchOutput,
    )
    parsed = completion.choices[0].message.parsed
    if parsed is None:
        logger.warning("llm_returned_no_parsed_output")
        raise ValueError("llm_returned_no_parsed_output")
    return parsed


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
def generate_review(system_prompt: str, user_prompt: str) -> ReviewOutput:
    """Call the LLM with an enforced ReviewOutput schema for source-level suggestions.

    Mirrors ``generate_patch``: Structured Outputs keep the Reviewer to advisory findings
    (never free-form rewrites). Raises on a missing parse so tenacity retries.
    """
    completion = _get_client().beta.chat.completions.parse(
        model=settings.nvidia_model,
        max_tokens=settings.nvidia_max_tokens,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        response_format=ReviewOutput,
    )
    parsed = completion.choices[0].message.parsed
    if parsed is None:
        logger.warning("llm_returned_no_parsed_review")
        raise ValueError("llm_returned_no_parsed_review")
    return parsed
