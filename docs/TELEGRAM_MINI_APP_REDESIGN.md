# Mini App visual redesign, 26 October 2026

## Diagnosis

PR #116 changed fixture-row overrides only. The saturated gradient canvas,
typography, standings, statistics list, header and navigation stayed unchanged.
This did not fulfil the requested visual redesign.

## Applied skills

Read `.claude/skills/redesign-existing-projects/SKILL.md` and
`.claude/skills/landing-page-design/SKILL.md`. Apply their existing-stack audit,
typography, flat surfaces, spacing, rounded geometry, clear interaction states,
and reduced-motion guidance. The Mini App is a private data interface, so landing
page marketing sections are not relevant. Keep the user's blue/white palette,
RPL red detail and Cup identity; these brand choices override generic skill colours.

## Implementation

- One explicit, shared `static/telegram-app/design.css` layer loaded after the
  existing behavioural CSS, with the same release query parameter.
- Manrope variable font with Cyrillic, bundled locally (about 68 KB), SIL OFL
  licence included. No third-party font request or new JavaScript dependency.
- Pale blue light canvas, neutral black/charcoal dark surfaces. Flat backgrounds.
- Segmented tournament switch, clear active navigation, larger standings numbers,
  16 px participant names and a two-column statistics dashboard.
- Shared surfaces and typography for round summaries, profiles, prediction details,
  national-team subtotals, forms, loading, empty and error states.
- Preserve mirrored transparent crests, final scores, hidden future predictions,
  all API semantics and existing authentication.
- Dense fixture exceptions: 14 px team names / 30 px crests on normal phones;
  original 13 px / 26 px floor at 320 px. Eight fixtures fit 320×740, 360×800,
  393×740 and 414×896. Opening details may scroll.

## Evidence

`docs/screenshots/telegram-redesign/` contains matched before/after fixture views
at 393×740, both new themes, and before/after statistics screens. All are labelled
test-fixture screenshots, not authenticated production data.

The browser scenario retains privacy, navigation, error/retry, Telegram back,
round selection, escaping, profiles and mobile layout checks. Styling assertions
were updated to the new design (16 px participant names, flat Cup profile,
selected tournament fill, neutral dark theme). Contrast checks remain at 4.5:1.
Screenshots wait for loading and theme transitions to finish. A regression check
ensures winner links are white on the accent-coloured winners panel.

Validation commands: `node tests/browser/telegram_mini_app.cjs` with the disposable
fixture server, and `python -m pytest -q tests/test_telegram_mini_app.py
tests/test_telegram_mini_app_matches.py` (80 tests). CI uses the existing five jobs.

No scoring, database, API, authentication, scheduling or release-workflow changes.
Rollback uses the preceding immutable production image and its release SHA.
