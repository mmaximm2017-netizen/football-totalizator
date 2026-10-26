# Telegram Mini App: design upgrade brief

## Source skills
- `.claude/skills/redesign-existing-projects/SKILL.md`: audit existing UI first, make targeted changes.
- `.claude/skills/landing-page-design/SKILL.md`: typography, spacing, surfaces and states. Marketing landing-page structure is **not applicable** to a data-dense Telegram Mini App.

## Scope and order
1. Audit the existing `static/telegram-app/` HTML/CSS/JS, identify the actual styles and components used for the combined Matches and Predictions screen. Record concrete issues before editing.
2. Redesign only that screen initially: readable home/away names and transparent emblems, home emblem on left and away emblem on right, clearly differentiated finished score vs upcoming kickoff, visually distinct expandable prediction details and selected round.
3. Keep the current RPL/Cup palettes and national-team/Supercup tabs; use existing TOTISH brand tokens rather than importing the upstream skills' generic dark palette.
4. Preserve eight collapsed fixtures on 360×800 and the existing 320×740 mobile baseline without vertical scrolling where feasible. Longer names, accessibility text sizes and expanded fixtures may scroll. Do not shrink names/logos just to hit a rigid count.
5. Keep light/dark modes, focus-visible, loading/error/empty states, reduced-motion support, fast transitions and accessible tap targets. Do not add heavy animations, external fonts or network calls without performance justification.
6. Run existing `tests/browser/telegram_mini_app.cjs` and `tests/test_telegram_mini_app.py`; add focused regression checks for layout and preserved behaviors. Inspect screenshots before proposing merge.
7. Only after this screen passes review, propagate shared design tokens to standings/statistics.

## Non-negotiable constraints
- No changes to scoring, rankings, API semantics, authentication, Telegram initData, data privacy, database or production scheduling.
- No invented fixture results, club badges or user data.
- No automatic merge, deploy or production-data changes.
- Keep the changes in a reviewable PR, with before/after screenshots and a rollback note.
