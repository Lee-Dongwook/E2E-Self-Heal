# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[Unreleased]: https://github.com/Lee-Dongwook/AI_Anything/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/Lee-Dongwook/AI_Anything/releases/tag/v0.1.0
