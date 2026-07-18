"""Type-safe configuration via Pydantic Settings."""

from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LLMProvider = Literal["nvidia", "openai", "anthropic", "ollama"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="E2E_HEALER_", env_file=".env", extra="ignore")

    # Provider-neutral LLM configuration. `llm_provider` selects the backend; the generic
    # `llm_*` fields are what every provider client reads. The legacy `nvidia_*` fields below
    # are mapped onto the generic ones when provider=nvidia (see _map_legacy_nvidia_fields), so
    # existing NVIDIA-only setups keep working without touching the new variables.
    llm_provider: LLMProvider = Field(
        default="nvidia", description="LLM backend: nvidia | openai | anthropic | ollama"
    )
    llm_api_key: str = Field(default="", description="API key for the selected provider")
    llm_base_url: str = Field(
        default="", description="OpenAI-compatible endpoint (empty = provider SDK default)"
    )
    llm_model: str = Field(default="", description="Structured-Outputs-capable model")
    llm_max_tokens: int = Field(
        default=4096, description="completion token cap (reasoning models need headroom)"
    )

    # Legacy NVIDIA-specific fields, kept for backward compatibility. Prefer the generic
    # llm_* fields above; these are folded into them when llm_provider is "nvidia".
    nvidia_api_key: str = Field(default="", description="NVIDIA NIM API key (legacy)")
    nvidia_base_url: str = Field(
        default="https://integrate.api.nvidia.com/v1",
        description="NVIDIA OpenAI-compatible endpoint (legacy)",
    )
    nvidia_model: str = Field(
        default="openai/gpt-oss-120b", description="Structured-Outputs-capable model (legacy)"
    )
    nvidia_max_tokens: int = Field(default=4096, description="completion token cap (legacy)")
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
    sandbox_mode: str = Field(
        default="relaxed",
        description="sandbox mode: strict, relaxed, or off",
    )
    workspace_root: str = Field(
        default=".",
        description="root directory for strict sandbox path checks",
    )
    write_globs: str = Field(
        default="*.spec.js,*.spec.jsx,*.spec.ts,*.spec.tsx,"
        "*.test.js,*.test.jsx,*.test.ts,*.test.tsx,"
        "**/*.spec.js,**/*.spec.jsx,**/*.spec.ts,**/*.spec.tsx,"
        "**/*.test.js,**/*.test.jsx,**/*.test.ts,**/*.test.tsx",
        description="comma-separated writable test-file globs",
    )
    deny_globs: str = Field(
        default=".env,.env.*,**/.env,**/.env.*,.git/**,.github/**,"
        "node_modules/**,.venv/**,uv.lock,package-lock.json,pnpm-lock.yaml,yarn.lock",
        description="comma-separated path globs denied by the sandbox",
    )
    architecture_allow_globs: str = Field(
        default="**/*", description="path globs allowed for generated patches"
    )
    architecture_deny_globs: str = Field(
        default="", description="path globs forbidden for generated patches"
    )
    allow_temp_helper: bool = Field(
        default=True,
        description="allow the temporary selector verifier helper file",
    )
    log_level: str = Field(default="INFO")

    @model_validator(mode="after")
    def _map_legacy_nvidia_fields(self) -> "Settings":
        """Fold legacy nvidia_* values into the generic llm_* fields for provider=nvidia.

        Only fields the user did not set explicitly are back-filled, so an explicit
        llm_* override always wins over the legacy default. This keeps existing
        E2E_HEALER_NVIDIA_* setups working unchanged while the generic fields become the
        single source of truth every provider client reads.
        """
        if self.llm_provider != "nvidia":
            return self
        explicit = self.model_fields_set
        if "llm_api_key" not in explicit and self.nvidia_api_key:
            self.llm_api_key = self.nvidia_api_key
        if "llm_base_url" not in explicit:
            self.llm_base_url = self.nvidia_base_url
        if "llm_model" not in explicit:
            self.llm_model = self.nvidia_model
        if "llm_max_tokens" not in explicit and "nvidia_max_tokens" in explicit:
            self.llm_max_tokens = self.nvidia_max_tokens
        return self


settings = Settings()
