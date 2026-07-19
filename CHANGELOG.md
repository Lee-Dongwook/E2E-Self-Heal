# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- `e2e-healer init` command — scaffolds a starter GitHub Actions workflow so a repo can wire
  the healer into CI in one step (#144).
- Architecture-boundary enforcement in the Patch Generator — patches are constrained to stay
  within configured module boundaries and can no longer leak across architectural lines.
- Real **React + Vite** demo app under `examples/` with an id-rename breakage scenario, for
  reproducible end-to-end tries.
- Documentation site (Docusaurus) with GitHub Pages deploy, SEO/GEO metadata, and analytics.

## [0.4.0] - 2026-07-17

### Added
- Multi-provider LLM support: **OpenAI**, **Anthropic (Claude)**, and **Ollama** (local,
  offline) alongside the existing NVIDIA NIM, selected via `E2E_HEALER_LLM_PROVIDER`.
  Configuration is provider-neutral (`E2E_HEALER_LLM_*`); the standard `OPENAI_API_KEY` /
  `ANTHROPIC_API_KEY` are used as fallbacks, and Ollama needs no key. Anthropic and Ollama
  are optional extras — `ai-driven-e2e[anthropic]` / `ai-driven-e2e[ollama]` — so users who
  don't need them don't pull in the dependency.

### Changed
- LLM client rebuilt on a provider-agnostic abstraction over LangChain chat models: one
  interface for free-text and structured completion plus a factory keyed on
  `llm_provider`. Node call sites are unchanged. Structured outputs are enforced per
  provider (OpenAI/NVIDIA strict `json_schema`, Anthropic tool-use, Ollama native
  JSON-schema `format`), with the tenacity retry and Patch Generator feedback loop
  preserved so a flaky JSON response never crashes the run.
- Documentation: README gains a provider matrix and per-provider setup blocks;
  `.env.example` shows every provider.

## [0.3.0] - 2026-07-11

### Changed
- Diff parsing rewritten on a tree-sitter AST: the JSX/TSX diff analyzer now walks the
  parsed syntax tree instead of matching regexes, producing more accurate and robust
  before/after DOM node extraction. Added the tree-sitter dependencies and expanded the
  diff-analyzer test coverage accordingly.

## [0.2.2] - 2026-07-11

### Added
- PR review-bot mode: `e2e-healer` can attach to a repository and review pull requests
  (new Reviewer node, prompts, and structured schemas), surfaced through the CLI, the
  composite Action (`action.yml`), and an example workflow (`ci/github-review-bot.example.yml`).
- `classname-scenario` example: a broken-selector demo covering a className rename.
- Japanese README translation (`README.ja.md`).

### Changed
- CI: added coverage reporting and an examples smoke-test job.
- Tooling: expanded ruff lint/format config, added `.editorconfig` and pre-commit hooks,
  and applied import sorting/formatting across the codebase.
- Added an auto-assign-reviewer workflow.

### Fixed
- Added `None` guards to the preprocessors (error-log parser, diff AST analyzer, aria
  snapshot) and expanded their test coverage.

## [0.2.0] - 2026-07-04

### Added
- Suite mode: `e2e-healer` with no path (or a directory) runs the whole Playwright suite,
  then heals every failing test file and emits an aggregate `SuiteSummary` (exit 0 only if
  all are healed). Single-file usage is unchanged.

## [0.1.0] - 2026-07-04

### Added
- CLI core (`e2e-healer`) that heals a failing Playwright test end-to-end: preprocess
  (error log + JSX/TSX diff), LangGraph loop (Diagnoser → Patch Generator → Selector
  Verifier → Test Runner), and a Router with a loop cap.
- Selector Verifier node: verifies patched selectors against the live DOM via a
  Node/Playwright helper; hallucinated/ambiguous selectors are reverted and re-patched
  before a full test run. Config via `E2E_HEALER_VERIFY_SELECTORS` / `E2E_HEALER_APP_URL`
  and the `--app-url` flag / `app-url` action input.
- Self-run failure capture when `--log` is omitted; `--dry-run`, `--diff-base`, `--json`.
- Atomic in-place writes with restore-on-give-up.
- Reusable composite GitHub Action (`action.yml`) + example patch-PR workflow.
- Unit and mocked end-to-end tests; repo CI (lint, format, typecheck, test).

### Changed
- LLM provider migrated from OpenAI to NVIDIA NIM (`openai/gpt-oss-120b`) via the
  OpenAI-compatible endpoint; Structured Outputs guardrail retained.

[Unreleased]: https://github.com/Lee-Dongwook/E2E-Self-Heal/compare/v0.4.0...HEAD
[0.4.0]: https://github.com/Lee-Dongwook/E2E-Self-Heal/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/Lee-Dongwook/E2E-Self-Heal/compare/v0.2.2...v0.3.0
[0.2.2]: https://github.com/Lee-Dongwook/E2E-Self-Heal/compare/v0.2.0...v0.2.2
[0.2.0]: https://github.com/Lee-Dongwook/E2E-Self-Heal/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/Lee-Dongwook/E2E-Self-Heal/releases/tag/v0.1.0
