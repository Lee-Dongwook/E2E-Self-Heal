"""OpenAI client wrapper enforcing Structured Outputs for deterministic patches."""

import structlog
from openai import OpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

from app.config import settings
from app.schemas import PatchOutput

logger = structlog.get_logger(__name__)

_client = OpenAI(api_key=settings.openai_api_key)


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
def generate_diagnosis(system_prompt: str, user_prompt: str) -> str:
    """Call the LLM for a free-text failure diagnosis (the Diagnoser node)."""
    completion = _client.chat.completions.create(
        model=settings.openai_model,
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
    completion = _client.beta.chat.completions.parse(
        model=settings.openai_model,
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
