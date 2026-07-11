# E2E-Healer Docs — Design Foundation

**Read this before building any docs-site component or page.**

This file is the locked design foundation. The visual direction, tokens, and layouts
here are **decisions already made by maintainers**. Your job as a contributor is to
**implement against this spec — not to invent styling**. If something visual is
undefined, ask in the issue; don't guess.

> Why this exists: E2E-Healer's core is Python, so the docs site is where **frontend
> contributors** add value. To keep contributions unblocked, we pre-decide the design
> so you can focus on clean React/CSS implementation.

---

## 1. Rules (non-negotiable)

1. **No raw values.** Never hardcode colors, spacing, radii, or fonts. Use the CSS
   variables in `src/css/custom.css` (`--eeh-*` and `--ifm-*`). Missing a token you
   need? Propose adding one in your PR — don't inline a hex/px.
2. **Dark-mode-first.** Design and review against dark mode first. Light mode must stay
   readable, but dark is the primary target. This is locked in `docusaurus.config.ts`
   (`colorMode: defaultMode 'dark', respectPrefersColorScheme false`) — don't change it.
3. **Both themes must work.** Every component is checked in light **and** dark before
   merge. No component may look broken in either.
4. **Responsive.** Mobile (~360px) to desktop. Multi-column layouts stack on mobile.
5. **Semantic healing colors are fixed.** "Broken" is always `--eeh-broken`, "healed"
   is always `--eeh-healed`. Never swap or recolor these — they carry meaning.
6. **Component-scoped CSS.** Use CSS Modules (`*.module.css`) per component. No global
   style bleed outside `src/css/custom.css`.

---

## 2. Design tokens (defined in `src/css/custom.css`)

| Purpose | Token | Notes |
|---|---|---|
| Body font | `--eeh-font-sans` | system UI stack |
| Code / selectors | `--eeh-font-mono` | use for all selector/code text |
| Spacing | `--eeh-space-1` … `--eeh-space-10` | 4px scale; never arbitrary px |
| Radius | `--eeh-radius-sm/md/lg/pill` | cards use `--eeh-radius-md` |
| Page background | `--eeh-bg` | |
| Card / panel | `--eeh-surface`, `--eeh-surface-raised` | raised = hover/elevated |
| Borders | `--eeh-border` | |
| Text | `--eeh-text`, `--eeh-text-muted` | |
| Brand accent | `--ifm-color-primary` | links, CTAs |
| **Broken selector** | `--eeh-broken` | red — old/failing |
| **Healed selector** | `--eeh-healed` | green — new/repaired |
| In-progress | `--eeh-warn` | amber |
| Difficulty badges | `--eeh-diff-easy/medium/hard` | HelpWantedBoard |
| Card shadow | `--eeh-shadow-card` | |
| Transitions | `--eeh-transition` | hover/focus |

**Visual direction:** dark developer-tool aesthetic (think a modern CLI/devtool
landing page) — high-contrast text on near-black surfaces, one teal-green accent,
mono font for anything selector/code related. Restrained: no gradients, no more than
one accent color per view.

---

## 3. Layout wireframes

Low-fidelity on purpose. Match **structure and hierarchy**, not pixel positions.
Spacing between major sections: `--eeh-space-10` desktop / `--eeh-space-6` mobile.

### 3.1 Landing page (`src/pages/index.tsx`) — Issue C1

```
┌──────────────────────────────────────────────────────────┐
│  NAVBAR: E2E-Healer   Docs · Architecture · Roadmap · GH  │
├──────────────────────────────────────────────────────────┤
│                                                          │
│                 HERO                                     │
│      "Self-healing E2E tests"  (H1)                      │
│      one-line subtitle (--eeh-text-muted)                │
│      [ Get Started ]  [ ★ Star on GitHub ]               │
│                                                          │
├──────────────────────────────────────────────────────────┤
│  WHY IT EXISTS   — 3 short value props (cards or cols)   │
├──────────────────────────────────────────────────────────┤
│  HOW IT WORKS    — <ArchitectureFlow/>  (see 3.2)        │
├──────────────────────────────────────────────────────────┤
│  ARCHITECTURE    — short summary + link to /architecture │
├──────────────────────────────────────────────────────────┤
│  ROADMAP TEASER  — 2–3 upcoming items + link             │
├──────────────────────────────────────────────────────────┤
│  HELP WANTED     — <HelpWantedBoard/>  (see 3.4)         │
├──────────────────────────────────────────────────────────┤
│  FOOTER: License · Contributing · Code of Conduct · GH   │
└──────────────────────────────────────────────────────────┘
```

All section copy is provided by maintainers as Markdown — do not write product copy.

### 3.2 `<ArchitectureFlow/>` — Issue B1

Vertical pipeline, 7 stages, in order. Each stage is a labeled box connected by a
down-arrow. Hovering a box reveals a one-line tooltip (text via props).

```
     ┌───────────────────┐
     │   Broken Test     │   ← box: --eeh-surface, border --eeh-border,
     └─────────┬─────────┘     radius --eeh-radius-md, mono label
               ▼               arrow color: --eeh-text-muted
     ┌───────────────────┐
     │  Playwright Trace │
     └─────────┬─────────┘
               ▼
     ┌───────────────────┐
     │  Snapshot Builder │
     └─────────┬─────────┘
               ▼
     ┌───────────────────┐
     │   Snapshot Store  │
     └─────────┬─────────┘
               ▼
     ┌───────────────────┐
     │  Matching Engine  │
     └─────────┬─────────┘
               ▼
     ┌───────────────────┐
     │   Mock Injector   │
     └─────────┬─────────┘
               ▼
     ┌───────────────────┐
     │ Playwright (re-run)│
     └───────────────────┘
```

- Desktop: centered column, comfortable width (~280–360px boxes).
- Mobile: same vertical flow, full-width boxes.
- Props-driven: stage labels and tooltips come from props — no hardcoded copy.
- Implementation freedom: hand-rolled CSS/SVG **or** the Mermaid plugin — your call,
  as long as tokens and order are respected.

### 3.3 `<HealingDiff/>` — Issue B2

Before (broken) vs after (healed) selector, side by side on desktop, stacked on mobile.

```
Desktop:
┌───────────────────────────┬───────────────────────────┐
│  BEFORE                   │  AFTER                     │
│  (label, --eeh-broken)    │  (label, --eeh-healed)     │
│  ┌───────────────────┐    │  ┌───────────────────┐     │
│  │ #old-id .btn      │    │  │ #new-id .btn      │     │  ← mono font
│  │ (red-tinted bg)   │    │  │ (green-tinted bg) │     │
│  └───────────────────┘    │  └───────────────────┘     │
└───────────────────────────┴───────────────────────────┘
  reason (optional): "id changed: old-id → new-id"  (--eeh-text-muted)

Mobile: BEFORE block stacked above AFTER block.
```

- Props: `before: string`, `after: string`, `reason?: string`.
- Highlight the changed part; use `--eeh-broken` / `--eeh-healed` tints for panels.
- Long selectors must wrap, not overflow.

### 3.4 `<HelpWantedBoard/>` — Issue B3

Card grid of contributable components, each linking to its GitHub issue. Data comes
from `src/data/help-wanted.json` (`{title, description, issueUrl, area, difficulty}`).

```
[ Filter:  ( All ) ( Frontend ) ( Backend ) ]

┌────────────────────┐  ┌────────────────────┐  ┌────────────────────┐
│ Snapshot Store      │  │ Trace Parser        │  │ Mock Injector       │
│ [Backend] [Medium]  │  │ [Backend] [Easy]    │  │ [Backend] [Medium]  │
│ short description…  │  │ short description…  │  │ short description…  │
│           → Issue → │  │           → Issue → │  │           → Issue → │
└────────────────────┘  └────────────────────┘  └────────────────────┘
```

- Cards: `--eeh-surface`, radius `--eeh-radius-md`, `--eeh-shadow-card`, hover raises
  to `--eeh-surface-raised` with `--eeh-transition`.
- Difficulty badge color: `--eeh-diff-easy/medium/hard`.
- Filter toggles by `area`. Whole card links to `issueUrl`.
- Grid: 3 cols desktop → 2 tablet → 1 mobile.

### 3.5 Architecture doc page (`docs/architecture/overview.md`) — Issue C2

Standard Docusaurus MDX doc: sidebar entry under an "Architecture" category, anchored
headings, and `<ArchitectureFlow/>` embedded inline. Content Markdown provided by
maintainers.

---

## 4. Definition of Done (every component PR)

- [ ] Uses only `--eeh-*` / `--ifm-*` tokens — no raw hex/px for color/space/radius.
- [ ] Correct in **dark and light** mode.
- [ ] Responsive down to ~360px; multi-column layouts stack.
- [ ] Copy/data is prop- or file-driven, not hardcoded.
- [ ] `npm run build` passes with no new console errors.
- [ ] Matches the relevant wireframe in §3.
