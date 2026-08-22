# Scenario: JSX prompt-context benchmark

This is a static benchmark fixture, not a runnable Playwright demo. It provides a JSX-heavy
source module and a selector-rename diff so `e2e-healer benchmark` can measure semantic JSX
chunking against a full-file baseline.

The fixture’s `legacy-submit` test id changes to `submit-account` in `change.patch`. The
benchmark detects the enclosing button context and reports token savings without requiring an
LLM provider, browser, or local web server.
