# Shadow Testing — Architecture

> **Status: design reference.** This document describes the _planned_ Shadow Testing
> execution pipeline and the responsibilities of each component. It introduces **no
> production code** and changes **no runtime behavior** — it is the shared reference
> that future Shadow Testing issues (#13+) build against. Terminology and stage
> boundaries may evolve as implementation lands, but the data-flow contract described
> here should stay stable.

## Motivation

The core self-healing loop (`Diagnoser → Patch Generator → Selector Verifier →
Test Runner`, see [`design.md`](design.md)) validates a patch by running the _real_
Playwright test against a _live_ application and network. That is authoritative, but it
is also slow, flaky, and non-deterministic: every verification run re-hits the backend,
depends on network conditions, and can fail for reasons unrelated to the selector under
repair.

**Shadow Testing** is a complementary execution mode that replays a test against a
**captured, deterministic snapshot** of the application's network behavior instead of a
live backend. A previously recorded Playwright trace is parsed into snapshots, those
snapshots are served back to the browser through request interception, and the test runs
in this isolated "shadow" of the real environment. The goal is a fast, repeatable,
side-effect-free run that the healer can use to verify candidate patches without touching
production services.

## Pipeline overview

```
        Playwright Trace          (recorded run: requests, responses, timing)
                │
                ▼
        Trace Parser              (extract network interactions → snapshot objects)
                │
                ▼
        Snapshot Store            (persist / load ShadowSnapshot as JSON on disk)
                │
                ▼
        Mock Injector             (intercept requests, match, fulfill from snapshots)
                │
                ▼
        Shadow Runtime            (workspace + wiring that hosts the isolated run)
                │
                ▼
        Playwright Execution      (test runs against replayed responses, no live backend)
```

Data moves **one direction** through the pipeline. Each stage consumes the artifact
produced by the previous stage and emits a narrower, more structured artifact for the
next one — mirroring how the Data Preprocessor abstracts raw inputs before they reach the
LLM. The unit of exchange between stages is the Pydantic schema defined in
[`app/shadow/schemas.py`](../app/shadow/schemas.py), not raw trace bytes.

## Stages

### 1. Playwright Trace (input)

**Purpose:** the raw material. A Playwright trace (or an equivalent capture of a real
run) records the requests the page made, the responses it received, headers, bodies, and
ordering.

**Produces:** an opaque trace artifact on disk (a path).

This stage is _external_ to the engine — the trace is recorded by a normal Playwright run
(e.g. `--trace on`) and handed to the pipeline as a file path. Nothing here interprets
the trace yet.

### 2. Trace Parser

**Purpose:** turn the opaque trace into structured snapshot objects. It walks the trace,
isolates each network interaction, and abstracts it into typed request/response pairs —
discarding everything irrelevant to replay.

**Consumes:** a trace file path.
**Produces:** a `ShadowSnapshot` containing a list of `NetworkSnapshot` objects
(each a `CapturedRequest` + `CapturedResponse` pair).

**Contract / current state:** the interface exists as
[`ITraceParser`](../app/shadow/interfaces.py) (`parse(trace_path: Path) -> Any`); a
concrete parser is a future issue. It is the analogue of the Error Log Parser in the heal
pipeline: it exists to keep noisy, oversized raw input from flowing downstream unabstracted.

### 3. Snapshot Store

**Purpose:** persist parsed snapshots and load them back deterministically. Decouples
capture-time from replay-time so a snapshot recorded once can be reused across many
verification runs.

**Consumes:** a `ShadowSnapshot` (or a validated dict) plus a `snapshot_id`.
**Produces:** a JSON file under the workspace's `snapshots/` directory on `save`; a
validated `ShadowSnapshot` on `get`.

**Current state:** implemented as [`SnapshotStore`](../app/shadow/snapshot_store.py)
(#48). Key properties already in place:

- **Deterministic serialization** — `json.dumps(..., sort_keys=True, indent=2)` so the
  same snapshot always serializes identically (diff-friendly, cache-friendly).
- **Schema validation on both ends** — dicts are validated against `ShadowSnapshot`
  before writing; files are validated after reading. Invalid/corrupt data raises the
  typed `SnapshotStoreError` family (`SnapshotNotFoundError`, `SnapshotCorruptionError`)
  rather than propagating raw parse errors.
- **Path-traversal safety** — `snapshot_id` is reduced to `Path(snapshot_id).name`
  before being resolved inside the workspace.

### 4. Mock Injector

**Purpose:** serve the stored snapshots back to the browser. It attaches to a Playwright
`Page`/`BrowserContext`, intercepts outgoing requests, matches each against the loaded
snapshots, and fulfills the request with the recorded response — so the app under test
believes it is talking to a live backend.

**Consumes:** a Playwright page/context + a set of `NetworkSnapshot`s (directly, or via a
`SnapshotMatcher`).
**Produces:** fulfilled/aborted routes at runtime; a list of `unmatched_requests` for
observability.

**Current state:** implemented as [`MockInjector`](../app/shadow/injector.py) +
[`SnapshotMatcher`](../app/shadow/matcher.py) (#43). Notable behavior:

- **Sync and async Playwright APIs** are both supported (the injector inspects
  `page.route` and routes accordingly).
- **Two-tier matching** in `SnapshotMatcher`: exact `method + URL` first, then a
  `method + URL-path` fallback, so replay survives volatile query strings.
- **Miss handling** — an unmatched request is recorded, logged as
  `network_mock_no_match`, and the route is aborted (a miss is surfaced, never silently
  passed through to the live network).
- **Base64 bodies** are decoded when `CapturedResponse.is_base64` is set, so binary
  responses replay correctly.

### 5. Shadow Runtime

**Purpose:** the environment that hosts an isolated run. It owns the temporary workspace
(scratch dirs, cached artifacts, the `snapshots/` directory the Store writes into) and is
where the Trace Parser, Snapshot Store, and Mock Injector are wired together for a single
shadow execution.

**Consumes:** a snapshot id / snapshot set + a test path.
**Produces:** a configured, sandboxed context ready for Playwright to run in, and cleans
it up afterward.

**Current state:** [`ShadowWorkspace`](../app/shadow/workspace.py) provides the workspace
substrate today — it creates and resolves `base_dir/{cache,snapshots,tmp}` and tears the
whole tree down on `cleanup()`. The orchestration that composes parser + store + injector
into a runnable shadow context is a future issue.

### 6. Playwright Execution (output)

**Purpose:** actually run the test. Playwright drives the test file exactly as in a normal
run, except every network call is answered from snapshots via the Mock Injector — no live
backend, deterministic timing, no external side effects.

**Consumes:** the test file + the wired Shadow Runtime.
**Produces:** a pass/fail result plus artifacts (unmatched-request list, logs) that a
caller can act on.

This reuses the existing subprocess-based Test Runner discipline (`npx playwright test
<path>`, raw log captured, never blocking) — Shadow Testing changes _what the network
returns_, not _how the test is executed_.

## Component ↔ code map

| Pipeline stage      | Interface (`interfaces.py`) | Concrete component                                  | Status            |
| ------------------- | --------------------------- | --------------------------------------------------- | ----------------- |
| Trace Parser        | `ITraceParser`              | ✅ Implemented (v0.4)                                        | `app/shadow/trace_parser.py`     |
| Snapshot Store      | `ISnapshotStore`            | `SnapshotStore` (`snapshot_store.py`)               | implemented (#48) |
| Mock Injector       | `IMockInjector`             | `MockInjector` + `SnapshotMatcher`                  | implemented (#43) |
| Shadow Runtime      | `IShadowWorkspace`          | `ShadowWorkspace` (`workspace.py`); orchestration TBD | partial         |
| Data exchange types | —                           | `ShadowSnapshot`, `NetworkSnapshot`, `CapturedRequest`, `CapturedResponse` | implemented |

Programming against the `I*` interfaces (rather than the concrete classes) keeps each
stage swappable — see extension points below.

## Data-flow contract

The artifact handed between stages is always a schema object, never raw bytes:

```
trace file  ──parse──▶  ShadowSnapshot { snapshot_id, metadata, network_snapshots[] }
                                          └── NetworkSnapshot { request, response }
                                                              CapturedRequest  { method, url, headers, body }
                                                              CapturedResponse { status, headers, body, is_base64 }

ShadowSnapshot  ──save/get (JSON on disk)──▶  ShadowSnapshot
network_snapshots[]  ──inject──▶  SnapshotMatcher  ──match(CapturedRequest)──▶  CapturedResponse  ──▶  route.fulfill(...)
```

Because every boundary is a validated Pydantic model, a malformed snapshot fails **at the
boundary it crosses** (parse, store, or match) with a typed error — it never reaches
Playwright as a silent bad replay.

## Extension points

Where future Shadow Testing work is expected to plug in:

- **Trace Parser implementations** — implement `ITraceParser.parse` for concrete formats
  (Playwright `trace.zip`, HAR, a custom capture). The rest of the pipeline is agnostic to
  the source format as long as the output is a `ShadowSnapshot`.
- **Matching strategies** — `SnapshotMatcher` currently does exact-then-path matching.
  Additional strategies (header/body-aware matching, query-param normalization, fuzzy or
  ordered matching) can extend or replace it without touching the injector.
- **Storage backends** — `ISnapshotStore` is filesystem-backed today; an alternative
  (content-addressed store, remote/object storage, in-memory for tests) can be dropped in
  behind the same interface.
- **Snapshot scope beyond network** — `ShadowSnapshot.metadata` and the "fully serialized
  application state" framing leave room for future capture beyond HTTP (localStorage,
  cookies, time/clock, WebSocket frames) as new `*Snapshot` schemas.
- **Runtime orchestration** — the glue that composes workspace + store + injector + a
  Playwright run into a single "shadow run" command, and its integration point with the
  heal/review graph (e.g. shadow-verify a candidate patch before a live Test Runner run).
- **Miss policy** — today an unmatched request is aborted and recorded; strict vs.
  lenient vs. record-and-augment policies are a natural configuration surface.

## Non-goals (for this document)

- No new modules, functions, or schema changes are introduced here.
- No wiring into the existing LangGraph heal/review graph is implemented.
- The exact CLI surface and config flags for launching a shadow run are deferred to the
  implementing issues.

## References

- Existing shadow module: [`app/shadow/`](../app/shadow/)
  — [`interfaces.py`](../app/shadow/interfaces.py),
  [`schemas.py`](../app/shadow/schemas.py),
  [`snapshot_store.py`](../app/shadow/snapshot_store.py),
  [`injector.py`](../app/shadow/injector.py),
  [`matcher.py`](../app/shadow/matcher.py),
  [`workspace.py`](../app/shadow/workspace.py)
- Core engine design: [`docs/design.md`](design.md)
- Playwright tracing / request interception: https://playwright.dev/
