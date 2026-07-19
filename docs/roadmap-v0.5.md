# E2E-Self-Heal — v0.5 Roadmap (`v0.5-preview`)

> **✅ Shipped — archived.** This document was the plan for the Shadow Testing push, and
> that work has landed: the runtime, the heal-graph integration (`shadow_verifier`), and
> the `--shadow` CLI surface are all implemented as of `0.4.0`. It is kept for historical
> context. **The single source of truth for the forward roadmap is
> [`../roadmap.en.md`](../roadmap.en.md).** Remaining Shadow work is only the extension
> points behind the `I*` interfaces, tracked under the `shadow-extensions` milestone.
>
> _Original planning note follows, preserved as-is:_
>
> This document is the plan for the `v0.5-preview` milestone. v0.5
> has one theme — **ship Shadow Testing** — and it is the first release we are opening up
> to outside contributors. The high-risk core is maintainer-owned; everything around it is
> up for grabs. Terminology may shift as work lands, but the split below is stable.

## Where we are (through v0.4)

The core self-healing loop is stable and shipped: `Diagnoser → Patch Generator →
Selector Verifier → Test Runner`, looping via a conditional Router until the test passes or
the loop cap is hit, plus a read-only `review` mode. See [`design.md`](design.md) for the
full engine design and [`CHANGELOG.md`](../CHANGELOG.md) for the shipped history.

Almost every **Shadow Testing** component also landed during v0.4. Shadow Testing replays a
test against a captured, deterministic snapshot of the app's network behavior instead of a
live backend — fast, repeatable, side-effect-free patch verification. The full pipeline is
specified in [`shadow-testing.md`](shadow-testing.md). Component status today:

| Component                 | Module                         | Status                                          |
| ------------------------- | ------------------------------ | ----------------------------------------------- |
| Trace Parser              | `app/shadow/trace_parser.py`   | Implemented                                     |
| Snapshot Store            | `app/shadow/snapshot_store.py` | Implemented                                     |
| Mock Injector             | `app/shadow/injector.py`       | Implemented (async route path needs finishing)  |
| Snapshot Matcher          | `app/shadow/matcher.py`        | Implemented                                     |
| Match Scorer              | `app/shadow/scoring.py`        | Implemented                                     |
| Request Normalizer        | `app/shadow/normalizer.py`     | Implemented                                     |
| Workspace                 | `app/shadow/workspace.py`      | Implemented                                     |
| **Runtime orchestration** | `app/shadow/runtime.py`        | **Implemented** — `ShadowRuntime` composes workspace + store + injector + a Playwright run |

All pieces are now wired into a runnable shadow run **and** into the heal graph: the
`shadow_verifier` node (`app/nodes/shadow_verifier.py`) gates a candidate patch against
snapshots before the live Test Runner. That gap — the whole point of this milestone — is
closed.

## v0.5 theme — "Ship Shadow Testing"

Turn the finished components into a real, deterministic replay mode (`e2e-healer --shadow`)
and use it to **shadow-verify a candidate patch before a slow, flaky live Test Runner run**.
The Selector Verifier already checks a patch against the live DOM; Shadow Testing adds a
faster, network-deterministic gate in front of the full run. The target end state:

```
   built components                    v0.5 work                     heal graph
 ┌──────────────────┐    ┌───────────────────────────┐    ┌───────────────────────────┐
 │ Trace Parser     │    │                           │    │ Patch Generator           │
 │ Snapshot Store   │──▶ │  Shadow Runtime           │──▶ │        │                  │
 │ Mock Injector    │    │  (compose + Playwright     │    │        ▼                  │
 │ Matcher/Scorer   │    │   run against snapshots)  │    │  Shadow-verify (new gate) │
 │ Normalizer       │    │                           │    │        │ pass              │
 │ Workspace        │    └───────────────────────────┘    │        ▼                  │
 └──────────────────┘                                      │  Selector Verifier → run  │
                                                           └───────────────────────────┘
```

## Maintainer-owned core (not up for grabs)

These are the high-risk, tightly-coupled pieces the maintainer is driving. They are listed
here so contributors don't collide with in-flight work — please don't open PRs against them
without coordinating first.

| Work item                                                                                                               | Where                         | Difficulty |
| ----------------------------------------------------------------------------------------------------------------------- | ----------------------------- | ---------- |
| Runtime orchestration — compose workspace + store + injector + a Playwright run into a real `run_shadow()`              | `app/shadow/runtime.py`       | Hard       |
| Heal-graph integration — a shadow-verify step/edge that validates a patch against snapshots before the live Test Runner | `app/graph.py`, `app/nodes/`  | Hard       |
| CLI + config surface for `--shadow` (record vs. replay, snapshot selection)                                             | `app/cli.py`, `app/config.py` | Medium     |

## Contributor track — Shadow extension points (`help wanted`)

Each of these sits behind an existing `I*` interface (see
[`app/shadow/interfaces.py`](../app/shadow/interfaces.py)) and is intentionally small — a
first PR should be completable in roughly an hour without touching the orchestration core.
They map directly to the "Extension points" section of [`shadow-testing.md`](shadow-testing.md).

| Idea                                                                                   | Behind interface        | Difficulty     | Label              |
| -------------------------------------------------------------------------------------- | ----------------------- | -------------- | ------------------ |
| HAR trace parser (alternate input format)                                              | `ITraceParser`          | Medium         | `help wanted`      |
| Richer matching — header/body-aware, query-param normalization, fuzzy/ordered          | `SnapshotMatcher`       | Medium         | `help wanted`      |
| In-memory snapshot store for tests; content-addressed store                            | `ISnapshotStore`        | Easy-Medium    | `help wanted`      |
| Miss policy config — strict / lenient (fall back to live) / record-and-augment         | `IMockInjector`         | Medium         | `help wanted`      |
| Finish + test the injector async route path                                            | `IMockInjector`         | Medium         | `good first issue` |
| Snapshot scope beyond HTTP (localStorage / cookies / clock) as new `*Snapshot` schemas | `app/shadow/schemas.py` | Hard (stretch) | `help wanted`      |

Because every stage boundary is a validated Pydantic model, a new implementation only has to
honor the schema contract — the rest of the pipeline stays agnostic to it.

## Community / DX track (`good first issue`)

| Idea                                                                                                            | Where                    | Difficulty |
| --------------------------------------------------------------------------------------------------------------- | ------------------------ | ---------- |
| Real React + Vite demo environment ([#3](https://github.com/Lee-Dongwook/E2E-Self-Heal/issues/3))               | `examples/`              | Medium     |
| "Record a trace → replay it" quickstart, once `--shadow` lands                                                  | `docs/`                  | Easy       |
| Correct [`shadow-testing.md`](shadow-testing.md) status — the Trace Parser is now **implemented**, not "future" | `docs/shadow-testing.md` | Easy       |

The `ko` / `ja` / `zh-CN` README translations are all in place, so no translation help is
needed for v0.5.

## Deferred to post-v0.5

Out of scope for this milestone, kept here so the theme stays focused:

- **Failure-time snapshot capture** and post-navigation selector verification — the Selector
  Verifier checks the entry-page state only today (see the README "Limitations").
- ~~**Multi-provider LLM pluggability**~~ — **shipped in `0.4.0`** (OpenAI / Anthropic /
  Ollama / NVIDIA NIM, selected via `E2E_HEALER_LLM_PROVIDER`). No longer deferred.

## Guardrails (unchanged)

The Patch Generator still fixes **only** failing selectors and wait conditions — never
assertions or test logic — enforced at both the prompt and schema level. Shadow Testing
changes _what the network returns_, not _how a test is executed_ or _what a patch is allowed
to touch_. Any contribution must preserve this invariant.

## How to contribute

Read [`CONTRIBUTING.md`](../CONTRIBUTING.md) first, then comment on an issue to claim it so
work isn't duplicated. Issues are labeled
[`good first issue`](https://github.com/Lee-Dongwook/E2E-Self-Heal/labels/good%20first%20issue)
and [`help wanted`](https://github.com/Lee-Dongwook/E2E-Self-Heal/labels/help%20wanted) and
tracked under the **`v0.5-preview`** milestone, with a difficulty tier
(Easy / Easy-Medium / Medium / Hard). Each issue is scoped to be small — roughly a
one-hour task for a focused coding session — and independent of the maintainer-owned core.
