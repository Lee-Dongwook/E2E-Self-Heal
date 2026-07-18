# good-first-issue drafts — docs-site (frontend contributors)

Drafts to file on GitHub to attract **frontend/DX contributors**. Copy each block into a new
issue and apply the `good first issue`, `help wanted`, and `docs-site` labels.

**Context for all of these:** the two flagship components now have **reference scaffolds** on
`docs-site-work`:

- `src/components/HealingDiff/` — before/after selector diff (DESIGN.md §3.3)
- `src/components/ArchitectureFlow/` — vertical pipeline, props-driven (DESIGN.md §3.2)

They establish the pattern every docs-site component follows: **CSS Modules + `--eeh-*`
tokens + responsive + dark-mode-first**. Read [`docs-site/DESIGN.md`](../DESIGN.md) before
starting any of these — it's the locked design spec, and the §4 Definition of Done is the
merge checklist.

> Note for maintainers: since `ArchitectureFlow`/`HealingDiff` are now scaffolded, the
> existing issues #51/#52 should be re-scoped to "polish/extend" (see #4/#5 below) rather
> than "build from scratch".

---

## 1. `<CodeCompare>` — reusable CLI ↔ spec Tabs wrapper

**Labels:** `good first issue`, `docs-site`, `frontend`
**Area:** Frontend · **Difficulty:** Easy

The Getting Started docs repeat a two-tab pattern: the **Python CLI command** on the left, the
**JS/TS Playwright spec it scans** on the right (see `docs/getting-started/quickstart-cli.mdx`).
Right now that's hand-written `<Tabs>` markup in every page.

**Task:** build `src/components/CodeCompare/` that wraps `@theme/Tabs`/`@theme/TabItem` and
takes props like `cli`, `spec`, and `specFilename`, so a page can write one component instead
of ~20 lines of Tabs boilerplate. Convert one existing page to use it.

**Done when:** the component renders identically to the current Tabs, is prop-driven, uses
only tokens, and works in dark + light. No product copy hardcoded.

---

## 2. `<ScopeGuardrailCallout>` — reusable admonition

**Labels:** `good first issue`, `docs-site`, `frontend`
**Area:** Frontend · **Difficulty:** Easy

The "Scope guardrail" warning (only selectors/waits are patched — never `expect(...)` or
flow) is repeated as a `:::warning` block across many docs. It should be one component so the
wording stays consistent.

**Task:** build `src/components/ScopeGuardrailCallout/` styled like a Docusaurus admonition
using `--eeh-*` tokens (accent with `--eeh-healed`/`--eeh-warn` as appropriate). Accept an
optional `variant` prop (`heal` | `review`) to tweak the sentence. Replace the inline
`:::warning` blocks in the Getting Started pages.

**Done when:** consistent rendering in both themes, responsive, copy comes from props/default,
Definition of Done met.

---

## 3. Homepage hero redesign (DESIGN.md §3.1)

**Labels:** `good first issue`, `docs-site`, `frontend`
**Area:** Frontend · **Difficulty:** Medium

`src/pages/index.tsx` still uses the default Docusaurus hero + `HomepageFeatures`
("Easy to Use / Powered by React" with undraw SVGs). Replace it with the product hero per the
DESIGN.md §3.1 wireframe.

**Task:** implement the hero — H1, one-line subtitle (`--eeh-text-muted`), and two CTAs
(`[ Get Started ]` → `/docs/getting-started/introduction`, `[ ★ Star on GitHub ]`). Replace
the three feature cards' copy/icons with the product's value props (maintainer-provided copy).

**Done when:** matches the §3.1 structure, tokens only, responsive to ~360px, both themes,
`npm run build` clean.

---

## 4. `<ArchitectureFlow>` polish — Mermaid option & keyboard focus

**Labels:** `help wanted`, `docs-site`, `frontend`
**Area:** Frontend · **Difficulty:** Medium · **Extends #51**

The scaffold in `src/components/ArchitectureFlow/` renders a props-driven vertical pipeline
with hover tooltips.

**Task:** (a) make stage boxes keyboard-focusable so tooltips show on `:focus-visible`, not
just hover; (b) add an optional `variant="mermaid"` that renders the same stages via the
Docusaurus Mermaid plugin (DESIGN.md §3.2 explicitly allows either); (c) embed the component
in an Architecture doc page fed the `HEAL_LOOP_STAGES` default.

**Done when:** accessible (focus + `title` fallback), both render paths respect tokens/order,
both themes verified.

---

## 5. `<HealingDiff>` — highlight the changed selector fragment

**Labels:** `help wanted`, `docs-site`, `frontend`
**Area:** Frontend · **Difficulty:** Medium · **Extends #52**

The scaffold in `src/components/HealingDiff/` shows before (red) vs after (green) selector
lines side by side.

**Task:** add optional inline highlighting of just the **changed fragment** (e.g. bold/tint
`submit-btn` → `submit` within the line) via a `highlight` prop, so readers' eyes go straight
to the delta. Keep long selectors wrapping, not overflowing.

**Done when:** highlight is opt-in and prop-driven, uses `--eeh-broken`/`--eeh-healed` tints
only, degrades to the current plain rendering when `highlight` is omitted, both themes pass.
