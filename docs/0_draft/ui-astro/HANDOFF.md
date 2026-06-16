---
created: 2026-06-16
purpose: handoff for next AI session continuing the AI-KMS demo UI
---

# Handoff — AI-KMS Demo UI (Astro)

## Where things stand

Structure-only scaffold of a 4-feature showcase page, built in Astro, themed
after https://mainline-astro-template.vercel.app/ (light, Vercel "Mainline"
style). **Layout is locked** — committed at `34b6c6d` ("Scaffold Astro demo
UI: 4-feature structure, Vercel-light theme") on branch `cloud-native`. Do
not change the structure without re-confirming with the user; this doc
exists so the next session can fill in *content*, not redesign layout.

No backend, no real data, no animation/interactivity yet — every demo box
is a static placeholder. That was deliberate (1-day, UI-only scope, user
explicitly said "no moving component, no flow, interactive component" for
this pass).

reference: https://mainline-astro-template.vercel.app/#

## What's built (read the code, this is just a map)

- `src/pages/index.astro` — single page. `features` array at top (4 items:
  Auto-Capture, Knowledge Extraction, Hybrid Search, Self-Learning &
  Briefings) — copy is reused placeholder text from the old draft
  (`docs/0_draft/ui/index.html`), **not final copy**. Each feature has a
  `usecases` array of 3 items (title + desc), also placeholder.
- `src/styles/global.css` — theme tokens (`:root` vars), nav, hero, and the
  per-feature layout: `.feature-top` (text left / big `.feature-main-demo`
  box right) + `.feature-usecases` (3-col grid below, vertical dividers,
  each `.usecase-box` bottom-aligns its `.usecase-demo` box via flex so all
  3 line up regardless of text length).
- `.claude/launch.json` (repo root) — `ai-kms-demo-ui` config runs
  `npm run dev --prefix docs/0_draft/ui-astro` on port 4321. Use the
  `preview_start`/`preview_screenshot` MCP tools with that name, not Bash.

## Decisions already made (don't re-litigate these)

- Stack: **Astro**, not plain HTML/CSS/JS — chosen over keeping the old
  static draft, specifically to match the reference template's real
  framework.
- Location: `docs/0_draft/ui-astro/` (user's explicit choice, not repo
  root, despite that mixing a Node project into a normally-markdown `docs/`
  tree — accepted tradeoff).
- Feature set: kept the old draft's grouping — **Capture / Extract
  (Classify) / Search / Self-Learning & Briefings** — even though
  Self-Learning/Briefings isn't fully built yet in the backend (Phase 10
  in progress per root `STATE.md`). This is a UI mock, not tied to backend
  build status.
- Per-feature layout: text + one big "core demo" side by side on top,
  3 smaller "usecase demo" boxes in a row below. Matches the visual
  reference `docs/0_draft/ui/vercel_ui.png`.
- Interactivity for the demo boxes: when content goes in, default to
  **scripted mock UI loop** (auto-plays a fake interaction on scroll into
  view, loops) — this was the user's pick during brainstorming, but they
  then deferred actually building it ("no moving component... for now").
  Treat that pick as the working default for when they say go, not a final
  lock-in — confirm before building it.

## What's NOT done — likely next session's job

1. **Real copy** for all 4 features + 12 usecases. Current text is
   placeholder reused from the old draft for layout purposes only — user
   said explicitly "real content is not those."
2. **Demo content** for each `.feature-main-demo` (4 of them) and each
   `.usecase-demo` (12 of them) — currently just gray boxes saying
   "demo — placeholder" / "core demo — placeholder". User hasn't decided
   yet what each demo actually shows.
3. **Animation/interactivity** for those demo boxes, once content exists
   (see "scripted mock UI loop" decision above).
4. **Coloring / visual polish** beyond the current minimal light theme —
   user mentioned "coloring" as outstanding.
5. Responsive/accessibility pass — explicitly out of scope for this round,
   not blocking, but not done either.

## Don't re-ask, just check

- User confirmed (via `AskUserQuestion`) Astro over plain HTML, the
  `docs/0_draft/ui-astro/` location, reusing draft copy as mock, and the
  draft's feature grouping. Re-asking these wastes a turn — read this doc
  first.
- The global `CLAUDE.md` HITL contract (5 Principles, AskUserQuestion at
  architecture checkpoints, reversibility rule) still applies in any new
  session — this isn't a process exception, just a content-only mode.

## Suggested skills for next session

- **`brainstorming`** — if the user shows up with vague "here's some
  content ideas" rather than a finished decision on what each demo shows,
  run this before touching code, same as this session did.
- **`ui-styling`** or **`ui-ux-pro-max`** — once real content exists, for
  the coloring/visual-polish pass (palette, type pairing, spacing).
- **`verify`** — after any code change, to drive the Astro dev server via
  the `preview_*` MCP tools (`ai-kms-demo-ui` launch config) and confirm
  visually, same pattern used throughout this session.

## Reference artifacts (don't duplicate, just read)

- Commit: `34b6c6d` on `cloud-native` — the locked scaffold.
- Visual reference: `docs/0_draft/ui/vercel_ui.png` (screenshot of the
  Vercel Mainline template section being matched).
- Old static draft (superseded, kept for copy/reference only):
  `docs/0_draft/ui/index.html`, `style.css`, `script.js`.
