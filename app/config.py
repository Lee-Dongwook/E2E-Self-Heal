"""Type-safe configuration via Pydantic Settings."""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="E2E_HEALER_", env_file=".env", extra="ignore")

    nvidia_api_key: str = Field(default="", description="NVIDIA NIM API key")
    nvidia_base_url: str = Field(
        default="https://integrate.api.nvidia.com/v1",
        description="NVIDIA OpenAI-compatible endpoint",
    )
    nvidia_model: str = Field(
        default="openai/gpt-oss-120b", description="Structured-Outputs-capable model"
    )
    nvidia_max_tokens: int = Field(
        default=4096, description="completion token cap (reasoning models need headroom)"
    )
    max_loops: int = Field(default=3, description="repair loop cap (Router termination)")
    playwright_cmd: str = Field(default="npx playwright test", description="Playwright invocation")
    verify_selectors: bool = Field(
        default=True, description="verify patched selectors against the live DOM before re-running"
    )
    app_url: str = Field(
        default="", description="URL the Selector Verifier loads to check candidate selectors"
    )
    node_cmd: str = Field(
        default="node", description="Node.js executable for the selector verifier"
    )
    test_results_dir: str = Field(
        default="test-results",
        description="Playwright output dir holding error-context.md failure snapshots",
    )
    log_level: str = Field(default="INFO")


settings = Settings()
