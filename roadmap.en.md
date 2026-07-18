# e2e-healer — Product Roadmap

> [!IMPORTANT]
   > ### 📍 Track Active Progress
   > Follow live engineering velocity in the **[v1.0 Roadmap Tracking Issue (#141)](https://github.com/Lee-Dongwook/E2E-Self-Heal/issues/141)** — a living checklist of every milestone (`v0.5.5` → `v1.0`) and its open issues.

> Strategic roadmap for growing `e2e-healer` from a repair CLI into a test-management
> platform, developer-experience tool, and viral open-source ecosystem.
>
> **Current release: `0.4.0`** (multi-provider LLM: OpenAI / Anthropic / Ollama / NVIDIA NIM).
>
> The initiatives below are organized into four strategic axes. Each item carries a
> **suggested target version** — see [Release Plan](#release-plan) for the reasoning and
> sequencing. Some source items were partially unclear and are reconstructed from intent
> (marked _reconstructed_).

---

## Axis 1 — Aggressive Virality & Ecosystem Penetration

Turn every successful heal into an outward-facing growth signal, and reposition the tool
from a one-shot fixer into a team-level test dashboard.

### 1.1 "Failed Test Registry" — trend analysis report
Aggregate, across an org's usage, **which components and selector patterns break most
often**, and emit a Markdown report.

- **Value:** quantitative insight like _"40% of our test failures are caused by text
  changes"_ — positions the tool as a **team test-management dashboard**, not just a fixer.
- **Depends on:** structured healing-history data (see 4.2).
- **Suggested: `0.6.0`**

### 1.2 GitHub Star → "Free Token Event" promotion architecture
When a developer stars the repo (from the playground or CLI) via their GitHub account,
credit them a quota of **free healing credits** (OpenAI/Anthropic) routed through a
first-party proxy server.

- **Value:** the single most reliable OSS marketing trigger for jumping from 1K → 5K stars.
- **Note:** requires a hosted proxy/credit backend — lives **outside the CLI core** per the
  core/wrapper separation rule.
- **Suggested: `1.0.0`+ (separate backend track)**

### 1.3 "Wall of Saved Hours" — global hall of fame badge
Sum the total developer-hours saved across every adopting repo/org and expose it as a
**live badge** on the repo homepage.

- **Value:** builds outward credibility and a large narrative — _"this tool saved
  developers worldwide N thousand hours."_
- **Depends on:** opt-in telemetry aggregation + hosted counter.
- **Suggested: `0.9.0`**

### 1.4 "Engineering Case Study" auto-generator
After a large test set self-heals, auto-build a **Markdown draft for a tech-blog post**
describing _how the AI resolved selector defects via AST analysis_.

- **Value:** developers publish to Velog / Medium after using the tool — automates
  technical virality.
- **Suggested: `0.9.0`**

### 1.5 Slack / X (Twitter) bot — 1-minute patch summary
When a heal completes, post a concise summary to **Slack or X** (which component broke,
what the patch changed).

- **Value:** keeps the team in the loop and surfaces the tool socially on every fix.
- **Suggested: `0.6.0`**

---

## Axis 2 — Enterprise Trust, Security & Audit

> _Reconstructed axis — the source text for items 2.x was partially garbled; the recoverable
> intent is an audit/compliance trail for regulated enterprises._

### 2.1 Immutable healing audit trail _(reconstructed)_
Convert every repair run into a **permanently preserved, tamper-evident record** (e.g.
signed/structured log or exportable XML) of what changed, why, and by which model.

- **Value:** provides the mandatory justification enterprise white-hat / security teams need
  to **pass security-regulation audits**.
- **Suggested: `0.8.0`**

_(Additional axis-2 items 2.2–2.x were unclear in the source and are omitted pending
clarification.)_

---

## Axis 3 — Extreme Developer Experience (DX) & Tooling Chain

Drive first-run friction to zero and extend the user base beyond CLI-native engineers.

### 3.1 "Healer-Init" — one-click project diagnosis CLI
`npx e2e-healer init` analyzes the repo's Playwright config and test code, then reports
e.g. _"this repo has an estimated 92% AI-repair success rate"_ and **auto-scaffolds a
starter CI YAML**.

- **Value:** removes essentially all onboarding fatigue at first entry.
- **Suggested: `0.6.0`**

### 3.2 Browser-extension "Live DOM Picker"
A Chrome extension lets a developer look at their locally running screen and say _"fix this
broken region"_; it links to the CLI to **pinpoint-heal only that component**.

- **Value:** extends the audience to juniors and QA engineers who aren't CLI-comfortable.
- **Note:** requires an extension + CLI bridge — a separate delivery surface.
- **Suggested: `1.0.0`+ (separate extension track)**

### 3.3 Architecture-boundary rule layer _(reconstructed)_
A rules layer that **constrains repairs to stay within internal architectural boundaries**
(e.g. Entities / domain layers), so patches never leak across module lines.

- **Value:** lets large, modern frontend teams adopt the tool **without fearing messy code**.
- **Suggested: `0.7.0`**

---

## Axis 4 — Agent Compute Advancement & Cost Optimization

Make the core engine cheaper, faster, and more universal.

### 4.1 Tree-Sitter "Semantic Code Chunking"
Advance the tree-sitter work from 0.3.0: instead of sending the whole file to the LLM,
extract **only the parent JSX Element block** enclosing the error line as context.

- **Value:** up to **80% token reduction** — cost and latency win simultaneously.
- **Suggested: `0.5.0`**

### 4.2 "Past Healing History" — RAG / fine-tuning memory layer
Remember previously successful selector-change patterns (e.g. `data-testid` naming-convention
changes) in a **vector DB or local JSON**, and on a similar error attempt an **instant
first-pass patch without an LLM call**.

- **Value:** eliminates repeated LLM spend on recurring failure patterns; foundation for the
  1.1 registry.
- **Suggested: `0.5.0`**

### 4.3 Framework-adaptive prompt layer _(reconstructed)_
A higher-order prompt-engineering layer that **dynamically tunes the optimal repair-selector
style per framework spec** (React 19, Vue 3, Svelte, …).

- **Value:** maximizes agent generality — high repair success no matter which frontend stack
  a new user brings.
- **Suggested: `0.7.0`**

---

## Release Plan

My suggested sequencing. Principle: **core-engine wins first** (they compound and need no
external infra), **local DX and reporting next**, **narrative/marketing once there's data to
tell a story with**, and **anything needing a hosted backend after `1.0.0`** so it doesn't
violate the CLI-core / thin-wrapper separation.

| Version   | Theme                          | Items                                              |
|-----------|--------------------------------|----------------------------------------------------|
| **0.5.0** | Engine cost & memory           | 4.1 Semantic Chunking, 4.2 Healing-History memory  |
| **0.6.0** | Reporting & zero-friction DX   | 1.1 Failed Test Registry, 1.5 Slack/X bot, 3.1 Healer-Init |
| **0.7.0** | Universality & code hygiene    | 4.3 Framework-adaptive prompts, 3.3 Boundary rules |
| **0.8.0** | Enterprise & compliance        | 2.1 Immutable audit trail                          |
| **0.9.0** | Narrative & virality           | 1.4 Case-Study generator, 1.3 Wall of Saved Hours  |
| **1.0.0** | Stability + growth backend     | Stabilize; begin 1.2 Free-Token proxy, 3.2 Live DOM Picker |
| **1.0.0+**| Separate tracks (hosted infra) | 1.2 Free-Token event, 3.2 Browser extension        |

### Why this order

- **0.5.0 first (4.1 + 4.2):** both are pure core-engine upgrades that directly extend the
  tree-sitter foundation shipped in 0.3.0. They lower cost/latency and **produce the
  structured healing history** that 1.1's registry and 1.3's saved-hours counter later depend
  on. Ship the data layer before the features that read it.
- **0.6.0 (1.1 + 1.5 + 3.1):** all local, no backend. Turns the tool into a **dashboard +
  onboarding experience** and creates the first viral surface (Slack/X) — the fastest path to
  visible team value.
- **0.7.0 (4.3 + 3.3):** broadens the addressable market — framework-adaptivity captures any
  frontend stack, boundary rules unblock large enterprise codebases.
- **0.8.0 (2.1):** compliance is a gate for enterprise adoption; sequence it once the tool is
  already valuable enough to be worth auditing.
- **0.9.0 (1.4 + 1.3):** marketing/narrative features that are only credible **after** real
  usage data exists (from 0.5–0.6).
- **1.0.0+ (1.2, 3.2):** these require **hosted infrastructure** (credit proxy, browser
  extension) that must live outside the CLI core. Treat them as **separate repos/tracks** to
  keep the core locally runnable and CI-callable without duplication.
