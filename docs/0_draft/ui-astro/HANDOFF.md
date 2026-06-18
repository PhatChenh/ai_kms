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

- `src/pages/index.astro` — single page, Vietnamese/Thư Đồng branded.
  `features` array at top (4 items, real copy from root `README.md`: Tìm
  Insights Chủ Động, Học Hỏi & Cải Thiện, Làm Nhiều Hơn Với Insights, Context
  Mọi Lúc Mọi Nơi — see "Decisions already made" for the relabel mapping).
  Each feature has a `usecases` array of 3 items (title + desc), also real
  copy. Demo boxes (`.feature-main-demo`, `.usecase-demo`) are still
  placeholder. New `<section class="intro">` between Hero and Feature 01 —
  README's intro paragraph + two value-prop bullet groups ("Với Thư Đồng:" /
  "Và khi bạn không ngồi ở laptop:") + closing paragraph, verbatim.
- `src/styles/global.css` — theme tokens (`:root` vars), nav, hero, intro,
  and the per-feature layout. Intro: `.intro-lead` (centered paragraph),
  `.intro-groups` (2-col grid, 1-col under 760px), `.intro-list` (plain
  bullet list per group), `.intro-closing` (centered italic, top border).
  Feature layout: `.feature-top` (text left / big `.feature-main-demo` box
  right) + `.feature-usecases` (3-col grid below, vertical dividers, each
  `.usecase-box` bottom-aligns its `.usecase-demo` box via flex so all 3
  line up regardless of text length).
- `.claude/launch.json` (repo root) — `ai-kms-demo-ui` config runs
  `npm run dev --prefix docs/0_draft/ui-astro` on port 4321. Use the
  `preview_start`/`preview_screenshot` MCP tools with that name, not Bash.

## Decisions already made (don't re-litigate these)

- **Brand/language (2026-06-16):** site is Vietnamese, branded **Thư Đồng**
  (was English "AI-KMS"). Source: root [README.md](../../../README.md), used
  verbatim for hero copy (tagline "Sống trên đời sống, cần có một Thư Đồng" /
  "Để chi bạn biết không? Để mọi context đừng bị gió cuốn đi"). `<html lang>`
  is now `vi`.
- **Feature relabel (2026-06-16):** the 4 feature boxes now use README's own
  4-feature split, not the old placeholder labels. DOM/CSS unchanged (still
  4×3 grid) — only `title`/`desc`/`usecases` text in the `features` array
  changed. Mapping:
  - 01 Auto-Capture → **Tìm Insights Chủ Động** (capture+extract+search-intent,
    README feature 1)
  - 02 Knowledge Extraction → **Học Hỏi & Cải Thiện** (README feature 2,
    self-learning)
  - 03 Hybrid Search → **Làm Nhiều Hơn Với Insights** (README feature 3,
    synthesis/briefings/reports)
  - 04 Self-Learning & Briefings → **Context Mọi Lúc, Mọi Nơi** (README
    feature 4, MCP/cross-platform — had no slot in the old structure)
  Rejected alternative: keep old English labels and redistribute README
  content into them — would've split README's feature 1 across 3 boxes and
  dropped feature 4 (MCP/context) entirely. Confirmed via `AskUserQuestion`.
- **Intro section placement (2026-06-16):** README's full intro pitch
  (tagline → value-props → closing) gets its own `<section class="intro">`
  between Hero and Feature 01 — not folded into the hero, not a closing
  section before the footer. Rejected alternatives: cramming it into
  `.hero-subtitle` (hero is sized for one short line, not a paragraph + 5
  bullets + closing line), or placing it as a recap before the footer
  (wrong narrative beat vs. README's own order). Confirmed via
  `AskUserQuestion`.

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

1. ~~**Real copy** for all 4 features + 12 usecases.~~ **DONE 2026-06-16** —
   real Vietnamese copy from root `README.md` now in `index.astro`'s
   `features` array (see brand/language and feature-relabel decisions
   above). No more placeholder text in titles/descs.
2. ~~**Copy polish pass** (2026-06-16 session).~~ **DONE.** All feature + usecase copy
   edited for tone and clarity. See "Copy changes (2026-06-16)" below.
3. **Demo content** for each `.feature-main-demo` (4 of them) and each
   `.usecase-demo` (12 of them) — partially done (see below), rest still
   placeholder.
4. **Animation/interactivity** for those demo boxes, once content exists
   (see "scripted mock UI loop" decision above).
5. **Coloring / visual polish** beyond the current minimal light theme —
   user mentioned "coloring" as outstanding.
6. Responsive/accessibility pass — explicitly out of scope for this round,
   not blocking, but not done either.

## Copy changes (2026-06-16)

All in `index.astro` `features` array. Vietnamese throughout.

| Location | Old | New |
|---|---|---|
| Hero desc | Trỏ Thư Đồng vào folder bạn muốn — email, PDF, Word... | Trỏ Thư Đồng vào folder bạn muốn, **thả vào** email, PDF, Word... **hoặc nói với Thư Đồng lưu lại** cuộc trò chuyện với AI... |
| F01 usecase 2 title+desc | "AI agent đọc qua từng file, tự rút ra insights..." | "**Thư Đồng** đọc qua từng file, tự rút ra insights **và lưu lại để cung cấp context cho AI**" |
| F01 usecase 3 desc | "Nói thẳng với Thư Đồng... Thư Đồng tìm và gom insights" | "Nói với Thư Đồng... Thư Đồng **sẽ chú ý tìm kiếm và lưu trữ theo phân loại như bạn muốn**" |
| F02 main desc | "Thư Đồng ghi nhớ... AI càng hiểu bạn" | "Thư Đồng **giúp AI** ghi nhớ... **AI và Thư Đồng** càng hiểu bạn" |
| F02 usecase 2 desc | "Thư Đồng tự tìm và đọc lại bài học cũ" | "Thư Đồng **tìm và chỉ dẫn AI** đọc lại bài học cũ" |
| F03 usecase 3 title | Viết Báo Cáo Giúp Bạn | **Giúp Bạn Hiểu Chính Mình** |
| F03 usecase 3 desc | "soạn báo cáo, phân tích, documentation thay bạn" | "Thư Đồng quan sát, và gửi bạn những insights về chính mình, **để bạn thấu hiểu mình hơn**" |

## Demo content changes (2026-06-16)

**Feature 03 — Alpha/Beta → Operations/Sales** throughout:
- `.demo-id-project` cards (2 places)
- `.demo-tr-name` tracker rows (2 places)
- `.demo-rp-body` report paragraphs (2 places)

**Feature 03 usecase 3 demo** (formerly "Viết Báo Cáo", now "Giúp Bạn Hiểu Chính Mình"):
- Replaced static weekly report mock with "Góc nhìn về bạn" showing behavioral patterns:
  - Time allocation: "70% thời gian cho Sales"
  - Prioritization: "ưu tiên task gấp hơn task quan trọng"
  - Strategic thinking: "Cuối tuần là lúc bạn suy nghĩ chiến lược nhiều nhất"
- Footer: "Thư Đồng quan sát mỗi ngày"

## Character images (2026-06-16)

All feature characters positioned + styled. Hero and footer updated.

| Location | Image | Position |
|---|---|---|
| Feature 01 | `peeking-behind-web-2.png` | Absolute top-right of `.feature-top`, peeking over border. Class: `.feature-character-peek` (`right: -10px; top: -20px; width: 180px`) |
| Feature 02 | `taking-note.png` | Inside `.feature-top-text`, under description text. Class: `.feature-character-inline` (`width: 180px; margin-bottom: -45px` — overflows into next section) |
| Feature 03 | `peeking.png` | Absolute right side of demo box, vertically centered. Class: `.feature-character-side` (`right: -30px; top: 50%; width: 150px`). Section has `.feature-top-compact` (`padding-right: 75px`) to give room. |
| Feature 04 | `jolly.png` | Absolute bottom-right of demo box. Class: `.feature-character-small` (`width: 100px; bottom: -20px; top: auto`) |
| Footer | Removed | No character |

**Float animation**: reduced from `-10px` to `-4px` in `@keyframes char-float`.

**Hero**: line-height increased to `1.25`. Subtitle: line break + "context" bold with accent color (`.hero-context`).

## Still placeholder (demos not yet done)

- All 4 `.feature-main-demo` boxes (right side of each feature) — still
  static demo content from original scaffold, not updated to match new copy
- Feature 01 usecase demos (cloud sync, extract, search) — scaffold content
- Feature 02 usecase demos (correction flow, retrieval, dual feedback) —
  scaffold content, still references "Alpha" (e.g. "Deadline dự án Alpha")
- Feature 04 usecase demos — scaffold content
- Feature 01 sidebar demo folders still show "Alpha"/"Beta"

## Don't re-ask, just check

- User confirmed (via `AskUserQuestion`) Astro over plain HTML, the
  `docs/0_draft/ui-astro/` location, reusing draft copy as mock, and the
  draft's feature grouping. Re-asking these wastes a turn — read this doc
  first.
- User confirmed (via `AskUserQuestion`, 2026-06-16) Vietnamese/Thư Đồng
  branding over keeping English/AI-KMS, and relabeling the 4 features to
  README's own split over keeping the old labels. See "Decisions already
  made" above — don't re-ask either.
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
