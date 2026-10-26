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
- Release authorization updated on 2026-10-26: merge PR #116 and deploy only after all CI checks pass. No production-data changes.
- Keep the changes in a reviewable PR, with before/after screenshots and a rollback note.

## Verified implementation (2026-10-26)
- CI #380 failed because expanded prediction names fell from 15px to 14px. Restore 15px/700 without changing the collapsed fixture geometry.
- Browser coverage verifies eight collapsed fixtures without page scrolling at 320×740, 360×800, 393×740 and 414×896. At 393/414px, check both themes, 14px minimum team names, 30px minimum crests, unclipped labels and mirrored crest positions.
- Local evidence: 35 Mini App Python tests pass; full fixture-backed browser scenario passes. Inspect light/dark 393×740 and expanded-prediction screenshots before release. These are fixture tests, not proof of live authenticated Telegram data.
- PR #116 has a one-time Actions merge job after test, UI, lint, security and Docker checks. It refuses draft/changed head/base and uses GitHub's merge endpoint without bypassing branch protections. It then dispatches main CI, because GITHUB_TOKEN merges do not trigger push workflows. Main publication and the existing production deployment remain gated by all five checks.
- Rollback: use the existing immutable-image deployment workflow with the prior release SHA and image digest from the preceding successful deployment. Do not revert database data or edit live static files by hand.
