"""Type-safe configuration via Pydantic Settings."""

from typing import Literal

from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LLMProvider = Literal["nvidia", "openai", "anthropic", "ollama", "deepseek", "orcarouter"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="E2E_HEALER_", env_file=".env", extra="ignore", populate_by_name=True
    )

    # Provider-neutral LLM configuration. `llm_provider` selects the backend; the generic
    # `llm_*` fields are what every provider client reads. The legacy `nvidia_*` fields below
    # are mapped onto the generic ones when provider=nvidia, so
    # existing NVIDIA-only setups keep working without touching the new variables.
    llm_provider: LLMProvider = Field(
        default="nvidia",
        description="LLM backend: nvidia | openai | anthropic | ollama | deepseek | orcarouter",
    )
    llm_api_key: str = Field(default="", description="API key for the selected provider")
    llm_base_url: str = Field(
        default="", description="OpenAI-compatible endpoint (empty = provider SDK default)"
    )
    llm_model: str = Field(default="", description="Structured-Outputs-capable model")
    llm_max_tokens: int = Field(
        default=4096,
        ge=1,
        description="completion token cap (reasoning models need headroom); must be >= 1",
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
    nvidia_max_tokens: int = Field(
        default=4096, ge=1, description="completion token cap (legacy); must be >= 1"
    )
    # OrcaRouter supports its own standard variable names. They are mapped onto generic
    # settings only when selected, keeping existing provider defaults untouched.
    orcarouter_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("ORCAROUTER_API_KEY", "E2E_HEALER_ORCAROUTER_API_KEY"),
        description="OrcaRouter API key (used only when llm_provider=orcarouter)",
    )
    orcarouter_base_url: str = Field(
        default="https://api.orcarouter.ai/v1",
        validation_alias=AliasChoices("ORCAROUTER_BASE_URL", "E2E_HEALER_ORCAROUTER_BASE_URL"),
        description="OrcaRouter OpenAI-compatible endpoint",
    )
    max_loops: int = Field(
        default=3,
        ge=1,
        le=3,
        description="repair loop cap (Router termination); Commandment #3 keeps this in 1..3",
    )
    playwright_cmd: str = Field(default="npx playwright test", description="Playwright invocation")
    test_timeout_seconds: int = Field(
        default=120,
        gt=0,
        description="max seconds a Playwright test run may take before it is killed and "
        "treated as a failure (prevents a hung run from blocking the repair loop)",
    )
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
    jsx_chunk_margin_lines: int = Field(
        default=1,
        ge=0,
        description="extra context lines around the enclosing JSX element chunk",
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
    slack_webhook_url: str = Field(
        default="",
        description="Slack incoming webhook URL for heal notifications (no-op when empty)",
    )
    log_level: str = Field(default="INFO")

    @model_validator(mode="after")
    def _map_provider_specific_fields(self) -> "Settings":
        """Fold provider-specific values into generic LLM settings when selected.

        Only fields the user did not set explicitly are back-filled, so an explicit
        llm_* override always wins over the legacy default. This keeps existing
        E2E_HEALER_NVIDIA_* setups working unchanged while the generic fields become the
        single source of truth every provider client reads.
        """
        explicit = self.model_fields_set
        if self.llm_provider == "nvidia":
            if "llm_api_key" not in explicit and self.nvidia_api_key:
                self.llm_api_key = self.nvidia_api_key
            if "llm_base_url" not in explicit:
                self.llm_base_url = self.nvidia_base_url
            if "llm_model" not in explicit:
                self.llm_model = self.nvidia_model
            if "llm_max_tokens" not in explicit and "nvidia_max_tokens" in explicit:
                self.llm_max_tokens = self.nvidia_max_tokens
        elif self.llm_provider == "orcarouter":
            if "llm_api_key" not in explicit and self.orcarouter_api_key:
                self.llm_api_key = self.orcarouter_api_key
            if "llm_base_url" not in explicit:
                self.llm_base_url = self.orcarouter_base_url
        return self

    @model_validator(mode="after")
    def _require_model_for_provider(self) -> "Settings":
        """Fail fast when a provider is selected without a model name.

        Runs after provider-specific settings are mapped so the NVIDIA back-compat default
        (``nvidia_model``) satisfies the check. An empty model otherwise surfaces deep
        inside the provider SDK on the first LLM call, which is much harder to diagnose.
        """
        if not self.llm_model.strip():
            raise ValueError(
                f"llm_model must be set for provider '{self.llm_provider}' "
                "(set E2E_HEALER_LLM_MODEL in .env)"
            )
        return self


settings = Settings()
