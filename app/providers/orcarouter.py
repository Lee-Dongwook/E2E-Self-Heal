"""OrcaRouter's OpenAI-compatible chat-model adapter."""

from dataclasses import dataclass

from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI

DEFAULT_BASE_URL = "https://api.orcarouter.ai/v1"


@dataclass(frozen=True)
class OrcaRouterProvider:
    """Build a chat model from explicit OrcaRouter connection settings."""

    api_key: str
    model: str
    max_tokens: int
    timeout_seconds: float
    base_url: str = DEFAULT_BASE_URL

    def build_chat_model(self) -> BaseChatModel:
        params = {
            "model": self.model,
            "api_key": self.api_key,
            "base_url": self.base_url,
            "max_tokens": self.max_tokens,
            "timeout": self.timeout_seconds,
        }
        return ChatOpenAI(**params)
