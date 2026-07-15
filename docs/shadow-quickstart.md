# Shadow Testing — Quickstart

> **Status:** the replay pipeline (trace parsing, snapshot storage, mock injection,
> runtime) is implemented today. The `e2e-healer --shadow` CLI surface — picking record
> vs. replay mode and selecting a snapshot — is still pending
> ([#81](https://github.com/Lee-Dongwook/E2E-Self-Heal/issues/81)). This quickstart is
> drafted against that design: Steps 1–2 work as written today; Step 3 shows the
> provisional CLI shape and will be updated once #81 lands. For the full pipeline
> architecture, see [`docs/shadow-testing.md`](shadow-testing.md).

Shadow Testing replays a Playwright test against a **recorded snapshot** of the app's
network traffic instead of a live backend — fast, deterministic, no side effects.

## 1. Capture a trace

A shadow snapshot starts from an ordinary Playwright trace. Run the test you want to
capture with tracing on:

```sh
npx playwright test tests/login.spec.ts --trace on
```

Playwright writes the trace to `test-results/<test-name>/trace.zip`. Nothing
shadow-specific has happened yet — this is a normal recorded run.

## 2. Parse the trace and store the snapshot

Feed the trace through [`PlaywrightTraceParser`](../app/shadow/trace_parser.py) and
persist it with [`SnapshotStore`](../app/shadow/snapshot_store.py). This works today
against `main`:

```sh
uv run python - <<'PY'
from pathlib import Path

from app.shadow.schemas import ShadowSnapshot
from app.shadow.snapshot_store import SnapshotStore
from app.shadow.trace_parser import PlaywrightTraceParser
from app.shadow.workspace import ShadowWorkspace

trace_path = Path("test-results/login-should-succeed/trace.zip")
snapshot_id = "login-happy-path"

network_snapshots = PlaywrightTraceParser().parse(trace_path)
snapshot = ShadowSnapshot(snapshot_id=snapshot_id, network_snapshots=network_snapshots)

store = SnapshotStore(ShadowWorkspace())
store.save_snapshot(snapshot_id, snapshot)
print(f"saved {len(network_snapshots)} network interactions as '{snapshot_id}'")
PY
```

The snapshot lands as JSON under `.shadow_workspace/snapshots/login-happy-path.json`
(the workspace root and subdirectory names come from
[`ShadowConfig`](../app/shadow/config.py)). Deterministic, diff-friendly serialization
means re-running this against the same trace always produces the same file.

## 3. Replay the test with `e2e-healer --shadow`

Today, `e2e-healer --shadow` on its own only exercises the runtime lifecycle as a
placeholder — confirm your install is wired up correctly:

```sh
uv run e2e-healer --shadow
```

```text
╭──────────────── Shadow Testing ────────────────╮
│ Shadow Testing runtime is under development —   │
│ no shadow logic runs yet.                       │
╰──────────────────────────────────────────────────╯
```

Once the CLI surface from #81 lands, the same flag will drive a full replay by pointing
at the test file and the snapshot captured in Step 2 — the underlying
[`run_shadow(test_path, snapshot_id)`](../app/shadow/runtime.py) entry point that
implements this already accepts both:

```sh
# Provisional — exact flag names finalize with #81
uv run e2e-healer --shadow tests/login.spec.ts --snapshot-id login-happy-path
```

That launches a headless Chromium instance, replays every request in the snapshot
through the [`MockInjector`](../app/shadow/injector.py), and runs the test against it —
no live backend involved.

## 4. Read the result

A replay run produces a [`ShadowRunResult`](../app/shadow/schemas.py):

| Field             | Meaning                                                     |
| ------------------ | ------------------------------------------------------------ |
| `is_success`       | whether the Playwright test process exited `0`               |
| `matched_count`    | requests fulfilled from the snapshot                          |
| `missed_count`     | requests with no matching snapshot entry (aborted, logged as `network_mock_no_match`) |
| `score`            | average match confidence across matched requests, `0.0`–`1.0` |
| `missed_requests`  | the actual unmatched `CapturedRequest`s, for debugging        |

The CLI renders this as a panel, e.g.:

```text
╭──────────────── Shadow Testing ────────────────╮
│ passed | matched=12 missed=0 score=0.97         │
╰──────────────────────────────────────────────────╯
```

A `missed_count` above zero usually means the trace didn't cover a request the test
makes on this run (a new endpoint, a changed query string outside the matcher's
method+path fallback) — re-capture the trace, or check `missed_requests` to see exactly
which calls fell through.

## See also

- [`docs/shadow-testing.md`](shadow-testing.md) — full pipeline architecture, stage-by-stage
  contracts, and extension points.
- [`docs/roadmap-v0.5.md`](roadmap-v0.5.md) — current implementation status of each
  Shadow Testing component.
