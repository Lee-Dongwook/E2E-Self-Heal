# Contributing to e2e-healer

First off — **thank you** for taking the time to contribute! 🎉

e2e-healer is an open-source engine that automatically repairs broken Playwright E2E tests.
Every contribution helps, whether it's filing a bug, improving the docs, sharing an idea, or
sending a pull request. You do **not** need to be an expert in LangGraph, LLMs, or Playwright
to help — there's room for everyone here.

> New to the project? Skim the [README](README.md) ([한국어](README.ko.md)) for what the tool
> does, then jump to [Ways to contribute](#ways-to-contribute). Prefer Korean? 편하게 한국어로
> 이슈나 PR을 작성하셔도 됩니다.

---

## Table of contents

- [Code of Conduct](#code-of-conduct)
- [Ways to contribute](#ways-to-contribute)
- [Reporting a bug](#reporting-a-bug)
- [Requesting a feature](#requesting-a-feature)
- [Asking a question](#asking-a-question)
- [Finding something to work on](#finding-something-to-work-on)
- [Development setup](#development-setup)
- [Making your change](#making-your-change)
- [Before you open a PR](#before-you-open-a-pr)
- [Opening the pull request](#opening-the-pull-request)
- [Project conventions](#project-conventions)
- [Architecture at a glance](#architecture-at-a-glance)
- [Where to get help](#where-to-get-help)

---

## Code of Conduct

This project and everyone participating in it is governed by our
[Code of Conduct](CODE_OF_CONDUCT.md). By participating, you're expected to uphold it. Please
be kind, assume good intent, and help make this a welcoming space.

---

## Ways to contribute

You don't have to write code to make a difference:

| Contribution | How |
| --- | --- |
| 🐛 **Report a bug** | [Open a bug report](https://github.com/Lee-Dongwook/E2E-Self-Heal/issues/new?template=bug_report.yml) |
| 💡 **Suggest a feature** | [Open a feature request](https://github.com/Lee-Dongwook/E2E-Self-Heal/issues/new?template=feature_request.yml) |
| ❓ **Ask a question** | [Start a Q&A](https://github.com/Lee-Dongwook/E2E-Self-Heal/issues/new?template=question.yml) |
| 📖 **Improve docs** | Fix a typo, clarify a step, or add an example — even one line helps |
| 🧪 **Add a test / example** | Cover a real-world selector-breakage case under `tests/` or `examples/` |
| 🔧 **Fix a bug / build a feature** | Grab a [good first issue](https://github.com/Lee-Dongwook/E2E-Self-Heal/labels/good%20first%20issue) and send a PR |
| ⭐ **Spread the word** | Star the repo, share it, or write about your experience |

**No contribution is too small.** A typo fix is a real contribution.

---

## Reporting a bug

Found something broken? Please [**open a bug report**][new-bug]. Our issue form walks you
through everything we need, but a great report generally includes:

- **What you ran** — the exact `e2e-healer …` command (or CI config).
- **What happened vs. what you expected.**
- **The `--json` RepairSummary** and relevant console output (redact any secrets/tokens).
- **Environment** — OS, Python version, package version, Playwright version.

Before filing, a quick search of [existing issues][issues] may show it's already known — a 👍
on an existing issue helps us prioritize.

> ⚠️ **Security issues:** please do **not** open a public issue for vulnerabilities. Follow
> [SECURITY.md](SECURITY.md) to report privately.

[new-bug]: https://github.com/Lee-Dongwook/E2E-Self-Heal/issues/new?template=bug_report.yml

---

## Requesting a feature

Have an idea? [**Open a feature request**][new-feat]. Tell us:

- **The problem** you're trying to solve (what's hard or impossible today).
- **What you'd like to see** — even a rough sketch is fine.
- **Alternatives** you've considered.

Framing the *problem* first (not just the solution) helps us design the right thing.

[new-feat]: https://github.com/Lee-Dongwook/E2E-Self-Heal/issues/new?template=feature_request.yml

---

## Asking a question

Not sure if it's a bug? Wondering how something works? That's welcome too —
[**open a question**](https://github.com/Lee-Dongwook/E2E-Self-Heal/issues/new?template=question.yml).
There are no dumb questions, and your question often reveals a docs gap we can fix.

---

## Finding something to work on

Looking for a place to start?

- 🌱 [**good first issue**](https://github.com/Lee-Dongwook/E2E-Self-Heal/labels/good%20first%20issue)
  — scoped, beginner-friendly tasks.
- 🙋 [**help wanted**](https://github.com/Lee-Dongwook/E2E-Self-Heal/labels/help%20wanted)
  — things we'd love a hand with.
- 🗺️ [**v0.3 roadmap**](https://github.com/Lee-Dongwook/E2E-Self-Heal/issues/9)
  — the bigger picture and how the current work fits together.

**🙋 We're actively recruiting contributors for these right now:**

| Issue | What |
| --- | --- |
| [#3](https://github.com/Lee-Dongwook/E2E-Self-Heal/issues/3) | Build a real **React + Vite frontend demo** environment for the Playwright examples |
| [#4](https://github.com/Lee-Dongwook/E2E-Self-Heal/issues/4) | Add a **Simplified Chinese (zh-CN)** README translation — 欢迎中文开发者参与！ |

Interested? Comment on the issue to claim it — no prior experience with this codebase needed.

Found one you like? **Comment on the issue to claim it** so we don't duplicate effort. If
nobody responds within a few days, feel free to go ahead. For anything larger than a small
fix, it's worth opening an issue to discuss the approach **before** writing a lot of code —
it saves everyone rework.

---

## Development setup

You'll need **Python 3.13+** and [**uv**](https://docs.astral.sh/uv/).

```bash
# 1. Fork the repo on GitHub, then clone your fork
git clone https://github.com/<your-username>/E2E-Self-Heal.git
cd E2E-Self-Heal

# 2. Install dependencies (incl. dev extras) and set up your env
make install          # → uv sync --extra dev + enables the git pre-commit hook
cp .env.example .env  # add your OPENAI_API_KEY (never commit this file)

# 3. Sanity-check that everything works
make check            # ruff (lint) + pyright (types)
make test             # pytest
```

`make install` also enables a native git pre-commit hook (`.githooks/pre-commit`) that runs `ruff format` on your staged Python files, so you never fail CI on formatting. No husky/npm needed. If you set up without `make install`, enable it manually with `git config core.hooksPath .githooks`.

Run `make help` to see every available task. Handy ones:

| Command | What it does |
| --- | --- |
| `make check` | Lint + typecheck (ruff + pyright) |
| `make test` | Run the test suite (pytest) |
| `make format` | Auto-format with ruff |
| `make run ARGS="tests/example.spec.ts --log playwright.log"` | Run the healer locally |

---

## Making your change

1. **Create a branch** off `main`:
   ```bash
   git checkout -b fix/short-description
   ```
2. **Read the existing code first.** Match the surrounding style and reuse existing patterns.
3. **Keep it focused.** One logical change per PR is far easier to review than a grab-bag.
4. **Add or update tests** under `tests/` for any behavior change.
5. **Update docs** (README, `docs/`) if you change user-facing behavior.

---

## Before you open a PR

Run the same checks CI runs — all must be green locally:

```bash
make format   # ruff format
make check    # ruff (lint) + pyright (types) — must pass
make test     # pytest — must pass
```

If `make check` or `make test` fails and you're not sure why, open the PR as a **draft** and
ask — we're happy to help you get it green.

---

## Opening the pull request

1. Push your branch and open a PR against `main`.
2. Fill out the [PR template](.github/PULL_REQUEST_TEMPLATE.md) — describe **what** changed,
   **why**, and **how you verified** it.
3. Link any related issue (e.g. `Closes #123`).
4. A maintainer will review. Expect some back-and-forth — that's a normal, healthy part of
   the process, not a sign anything's wrong.

**Commit messages** follow a light [Conventional Commits](https://www.conventionalcommits.org/)
style, matching the existing history:

```
feat: add selector-verifier retry budget
fix: handle empty git diff in preprocessor
docs: clarify CI wrapper setup
test: cover ambiguous-selector revert path
```

---

## Project conventions

This repo follows the rules in [`.claude/CLAUDE.md`](.claude/CLAUDE.md) and
[`AGENTS.md`](AGENTS.md). The essentials:

- **Imports at the top** of every file — never inside functions or classes.
- **Type hints** on every function signature; prefer Pydantic models / `TypedDict` over raw
  dicts for structured data.
- **Logging** with `structlog` — lowercase_underscore event names, no f-strings, pass data as
  kwargs. Use `logger.exception()` inside `except` blocks.
- **Retries** with `tenacity` (exponential backoff); **console output** with `rich`.
- **Guardrail (non-negotiable):** the Patch Generator only fixes **selectors and wait
  conditions** — never assertions or test logic. This is enforced at *both* the prompt and the
  `PatchOutput` schema level. Keep it that way.
- **Preprocess before the LLM:** never send raw Playwright logs or a full `git diff` to the
  model — abstract them into compact context first.
- **CLI core vs. CI wrapper:** all repair logic lives in the CLI core; the CI wrapper only
  orchestrates and reports. Don't duplicate the graph in the wrapper.

When in doubt, look for an existing example in the codebase and follow it.

---

## Architecture at a glance

Read [`docs/design.md`](docs/design.md) before making structural changes. The layout under
`app/`:

| Path | Responsibility |
| --- | --- |
| `app/cli.py` | CLI entry point (`e2e-healer`) — the single source of truth |
| `app/preprocess/` | Error-log parser + diff/JSX AST analyzer (abstracts inputs) |
| `app/nodes/` | LangGraph nodes: Diagnoser → Patch Generator → Selector Verifier → Test Runner |
| `app/verify/` | Selector verification against the live DOM |
| `app/prompts/` | LLM prompts |
| `app/graph.py` | Wires the nodes into the repair loop |
| `app/runner.py` | Runs `npx playwright test` via subprocess |
| `tests/` | Test suite |
| `examples/` | Runnable examples of selector-breakage repairs |

---

## Where to get help

- 💬 Stuck on setup or unsure how something works? [Open a question](https://github.com/Lee-Dongwook/E2E-Self-Heal/issues/new?template=question.yml).
- 🔍 Browse [existing issues][issues] to see what others are discussing.
- 📚 Check the [README](README.md) and [`docs/`](docs/) for deeper context.

Thanks again for contributing — we're glad you're here. 💛

[issues]: https://github.com/Lee-Dongwook/E2E-Self-Heal/issues
