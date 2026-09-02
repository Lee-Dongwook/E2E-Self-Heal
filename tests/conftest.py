"""Shared test-process configuration."""

import os


# ``app.config.settings`` is created while test modules are imported. Keep that
# singleton independent of a developer's local .env without affecting process
# environments outside pytest. Individual configuration tests use their own
# isolated Settings factory.
os.environ.setdefault("E2E_HEALER_LLM_MODEL", "test-model")
