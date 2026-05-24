# FRONTEND_DECOMPOSITION_AUDIT

## Что сейчас в index.html

`templates/index.html` сейчас является монолитом домашнего экрана: в одном файле лежат inline CSS, accordion JS, Jinja-разметка матчей, bottom sheet ставок и весь JS поведения прогнозов.

Крупные блоки:

1. Inline CSS домашнего экрана, строки `4-1820`.
   - Accordion месяцев/дней: `.home-screen`, `.month-*`, `.day-*`, `.today-pill`, `.day-content.open`.
   - Match Cards V2: `.match-card-v2`, `.match-topline`, `.teams-center`, `.team-v2`, `.team-logo-v2`, `.team-name-v2`, `.prediction-box`, `.score-stepper`, `.save-btn-v2`, `.points-v2`, `.bets-link-v2`.
   - Deadline/timer styling: `.deadline-row`, `.deadline-timer`, `.deadline-timer.warning`, `.deadline-timer.danger`, `.deadline-timer.closed`, `.deadline-helper`, `.deadline-cue`, `.urgency-mid`, `.urgency-hot`.
   - Finished state styling: `.match-card-v2.finished`, finished logo sizes, `.final-score`, `.prediction-label-finished`, `.prediction-score-finished`.
   - WC-2026/tournament skin: `body.tournament-wc2026 .match-card-v2...`, `.wc-trophy-bg`, forced `!important` block at the end.
   - Bottom sheet ставок: `.bets-sheet-*`, `.bets-compact-*`, `body.bets-sheet-lock`.
   - Mobile layout overrides: `@media (max-width: 430px)`.

2. Accordion JS, строки `1822-1865`.
   - `toggleMonth(monthId)` controls `#month-content-*`, `#arrow-month-*`, closes opened `.day-content.open` inside a month, and resets `[id^="arrow-day-"]`.
   - `toggleDay(dayId)` controls `#day-content-*`, `#arrow-day-*`, and `.day-content.open`.

3. Main Jinja structure, строки `1867-2276`.
   - Root `.home-screen`.
   - `{% for month in months %}` with `.month-block`.
   - `{% for day in month.days %}` with `.day-block`.
   - `{% for match in day.matches %}` with `.match-card-v2`.
   - Empty state is attached to the `{% for month in months %}` loop via `{% else %}`, lines `2268-2274`.

4. Match card structure, строки `1908-2258`.
   - Match state setup: `has_prediction`, `card_state`, `predicted_class`.
   - Card root: `.match-card-v2 {{ card_state }} {{ predicted_class }}` with `data-finished` and `data-deadline-closed`.
   - WC trophy background: `.wc-trophy-bg` only for `match.league == 'wc2026' and not match.finished`.
   - Topline: league logo/name, kickoff time, status/deadline area.
   - Finished branch: final score, points, prediction summary, bets link.
   - Non-finished branch: hidden form, teams layout, stepper inputs, save button, closed prediction state, admin disabled state, current prediction text.

5. Bottom sheet markup for player bets, строки `2278-2298`.
   - `.bets-sheet-overlay[data-bets-sheet-overlay]`.
   - `.bets-sheet[data-bets-sheet-panel]`.
   - `.bets-sheet-content[data-bets-sheet-content]`.

6. Main behavior JS, строки `2300-2824`.
   - `pluralizeMatch(count)`.
   - `DOMContentLoaded` initialization.
   - Match count rendering via `[data-match-count]`.
   - Initial scroll to `.day-content.open`.
   - Bets bottom sheet open/close/loading/AJAX compact view.
   - Deadline timer close state and atmosphere.
   - Score steppers.
   - AJAX prediction save flow.

`templates/base.html` also matters for this audit because it owns shared app shell, fixed bottom nav, tournament selector sheet, service worker registration, and the `body.tournament-wc2026` / `body.tournament-cup` classes that the `index.html` CSS depends on.

`static/service-worker.js` does not directly depend on `index.html` DOM, but it caches icons, flags, PNG/SVG/JS/CSS assets. Tournament logos, flags and World Cup image paths used by the card UI must remain stable or cache behavior should be considered.

## Что можно вынести безопасно

Safe candidates are “HTML-only partials” where the rendered DOM must stay byte-for-byte equivalent in class names, IDs, `data-*`, form names and nesting.

1. Month/day wrappers can be extracted after a snapshot check.
   - Candidate: `templates/partials/home/_month_block.html`.
   - Includes `.month-block`, `.month-header-v2`, `#month-content-*`, month loop over days.
   - Must preserve `month_idx`, `month_is_open`, `open_day`, `data-match-count`, and inline `onclick`.

2. Day wrapper can be extracted after month extraction.
   - Candidate: `templates/partials/home/_day_block.html`.
   - Includes `.day-block`, `.day-header-v2 {{ day.type }}`, `#day-content-*`, day loop over matches.
   - Must preserve generated IDs: `day-content-{{ month_idx }}_{{ day_idx }}` and `arrow-day-{{ month_idx }}_{{ day_idx }}`.

3. Tournament/league badge inside card top line is a reasonable small partial.
   - Candidate: `templates/partials/home/_match_league.html`.
   - Contains league logo and `.league-label`.
   - Safer than extracting the whole card first because it has no JS behavior.
   - Must preserve special Russia logic, `rpl`, `rcup`, `wc2026`, asset paths, `class="tournament-logo"`.

4. Team logo/name block can be extracted with caution.
   - Candidate: `templates/partials/home/_team_v2.html`.
   - Contains `.team-v2`, `.team-logo-v2`, flag/club logo fallback, `.team-name-v2`.
   - It appears twice in finished and non-finished branches with identical structure, so this is a good duplication target.
   - Must preserve `get_flag(team)|safe` before `get_club_logo(team)|safe`.

5. Bets bottom sheet markup can be extracted.
   - Candidate: `templates/partials/home/_bets_sheet.html`.
   - The JS uses `data-bets-sheet-overlay`, `data-bets-sheet-panel`, `data-bets-sheet-content`, `data-bets-sheet-close`, and `.bets-sheet-loading`.
   - Safe if all selectors and position after `.home-screen` remain the same.

6. Empty home state can be extracted.
   - Candidate: `templates/partials/home/_empty_home.html`.
   - Very low logic risk, but keep it attached to the Jinja `{% for month in months %}{% else %}` behavior or replace with an equivalent explicit condition only after backend context is verified.

7. Finished match state can become a partial later, but not as the first extraction.
   - Candidate: `templates/partials/home/_match_finished.html`.
   - It is relatively self-contained visually, but shares `.prediction-box`, `.match-bottomline`, `.my-prediction-v2`, `.bets-link-v2`, team rendering, points word logic, and bottom sheet link behavior.

## Что пока нельзя трогать

These areas are coupled to JS, class names, IDs, or tournament CSS and should not be moved or renamed during the first safe pass.

1. The card root contract:
   - `.match-card-v2`
   - state classes: `.active`, `.closed`, `.finished`, `.predicted`, `.save-success-pulse`, `.urgency-mid`, `.urgency-hot`
   - `data-finished`
   - `data-deadline-closed`

2. Prediction form contract:
   - `.predict-form-v2`
   - `id="predict-form-{{ match.id }}"`
   - `input[name="home_goals"]`
   - `input[name="away_goals"]`
   - `input[name="match_id"]`
   - submit button with `form="predict-form-{{ match.id }}"`
   - hidden form outside the visual center score area.

3. Stepper contract:
   - `[data-stepper]`
   - `.score-input-v2`
   - `.stepper-btn`
   - `data-step="-1"` and `data-step="1"`
   - `min`, `max`, `form`, disabled attributes.

4. Deadline contract:
   - `[data-deadline]`
   - `.deadline-timer`
   - `.deadline-row`
   - `.deadline-cue`
   - `.topline-right`
   - `.status-pill-v2`
   - `.status-closed`
   - `.match-topline`

5. Accordion contract:
   - inline `onclick="toggleMonth('{{ month_idx }}')"`
   - inline `onclick="toggleDay('{{ month_idx }}_{{ day_idx }}')"`
   - `#month-content-*`
   - `#day-content-*`
   - `#arrow-month-*`
   - `#arrow-day-*`
   - `.day-content.open`

6. Bets bottom sheet contract:
   - `[data-bets-sheet]` links.
   - `[data-bets-sheet-overlay]`
   - `[data-bets-sheet-panel]`
   - `[data-bets-sheet-content]`
   - `[data-bets-sheet-close]`
   - `.bets-sheet-lock`
   - Compact view classes created in JS: `.bets-compact`, `.bets-compact-row`, `.bets-compact-name`, `.bets-compact-score`, `.bets-compact-points`, `.zero`.

7. WC-2026/tournament visual contract:
   - `body.tournament-wc2026` comes from `base.html`.
   - `.wc-trophy-bg` must remain inside `.match-card-v2` for non-finished WC matches.
   - `.save-btn-v2` and `.score-input-row` must keep their z-index relationship over `.wc-trophy-bg`.
   - Asset path `/static/clubs/WorldCup.png` is hardcoded in CSS.

8. CSS should not be moved during the first decomposition.
   - The inline CSS has cross-cutting selectors and many `body.tournament-wc2026 ... !important` overrides.
   - Moving CSS before HTML partial extraction increases risk without reducing template complexity first.

## Риски

1. Сохранение прогноза: high risk.
   - AJAX save relies on `.predict-form-v2`, external submit button using `form="predict-form-{{ match.id }}"`, `input[name="home_goals"]`, `input[name="away_goals"]`, `match_id`, CSRF meta, and `fetch('/')`.
   - On success it mutates `.match-card-v2`, `.prediction-box`, `.lock-pill`, `.prediction-title`, `.my-prediction-v2`, `.match-bottomline`, `.bets-link-v2`.
   - Biggest hidden risk: the form is not wrapping the inputs; inputs are associated by `form` attribute. Moving form/input/button without preserving this exact contract can silently break `FormData(form)`.

2. Степперы: high risk.
   - JS finds each `[data-stepper]`, then searches inside for `.score-input-v2` and `.stepper-btn`.
   - Buttons depend on numeric `data-step`.
   - If the input is moved outside `[data-stepper]`, stepper clicks stop working.

3. Дедлайны: high risk.
   - Timers use `[data-deadline]`, closest `.match-card-v2`, `.deadline-cue`, `.deadline-row`, `.topline-right`, `.status-pill-v2`.
   - `applyDeadlineClosedState(card)` disables `.score-input-v2` and `.stepper-btn`, disables the submit button, hides `.deadline-row`, and creates/updates `.status-pill-v2`.
   - Moving deadline markup away from `.match-topline` or changing `.topline-right` breaks the close-state UI.

4. Finished state: medium-high risk.
   - Finished cards use a separate branch, but share class names with active cards.
   - CSS uses `.match-card-v2.finished ...` heavily.
   - Finished prediction/points markup contains inline Jinja word/plural/class logic. Extracting it too early may duplicate or lose scoring presentation logic.

5. Mobile layout: high visual risk.
   - `@media (max-width: 430px)` changes card padding, grid widths, logo sizes, WC flag sizes, score stepper dimensions, finished state sizes.
   - Small DOM nesting changes can cause mobile overflow or z-index issues even if desktop looks fine.

6. Кубок/фон турниров: high visual risk.
   - `base.html` sets `body.tournament-wc2026` or `body.tournament-cup`.
   - `index.html` has both regular WC rules and forced final WC rules with `!important`.
   - `.wc-trophy-bg` is absolutely positioned with z-index `2`, while interactive controls are forced to z-index `3`.
   - Removing or moving `.wc-trophy-bg`, or wrapping it in an unexpected parent, can hide controls or lose the tournament background.

7. Bottom sheet: medium risk.
   - The sheet loads `/match/<id>/predictions`, parses returned HTML, and depends on classes from another page: `.predictions-page`, `.team-name`, `.score-box`, `.prediction-row`, `.prediction-name`, `.prediction-score`, `.points-pill`, `data-points`.
   - A future change outside `index.html` can break compact sheet rendering.

8. Encoding/readability risk: medium.
   - Several docs and command output currently appear mojibaked in the terminal, while templates render Russian text. Any scripted rewrite must preserve file encoding exactly.

## Safe decomposition plan

### Step 1

Create low-risk, HTML-only partials while keeping CSS and JS in `index.html`.

Recommended extraction order:

1. `_empty_home.html`.
2. `_bets_sheet.html`.
3. `_match_league.html`.
4. `_team_v2.html`.

Rules for Step 1:

- Do not change class names.
- Do not change IDs.
- Do not change `data-*`.
- Do not change form/input/button relationships.
- Do not move CSS.
- Do not move JS.
- Compare rendered HTML before/after on at least one page with open matches, closed matches, finished matches, and WC-2026 match if available.

### Step 2

Extract structural wrappers after Step 1 is verified.

Recommended extraction order:

1. `_day_block.html`.
2. `_month_block.html`.

Rules for Step 2:

- Preserve `month_idx`, `day_idx`, `month_is_open`, `day_is_open`.
- Preserve inline accordion handlers until a separate JS refactor is planned.
- Preserve exact ID formats for month/day content and arrows.
- Re-test initial scroll to open day and accordion open/close behavior.

### Step 3

Extract match-state partials only after the smaller partials are stable.

Recommended extraction order:

1. `_match_finished.html`.
2. `_match_prediction_controls.html`.
3. `_match_card.html` as the final wrapper, only after the sub-parts are already stable.

Rules for Step 3:

- Keep the card root in one place until the end.
- Keep prediction form, inputs and submit button together as a tested contract, even if they are not physically nested.
- Keep deadline top-line behavior intact.
- Verify save flow, deadline close flow, stepper flow, bottom sheet flow, mobile layout, and WC-2026 background.

## First recommended step

Start with `_bets_sheet.html` or `_empty_home.html`, not with match cards.

The safest useful first step is extracting the bets bottom sheet markup from lines `2278-2298` into `templates/partials/home/_bets_sheet.html` and including it back in the same location. It is outside the match loop, has clear `data-*` hooks, and does not touch prediction form state, deadline state, card layout, or tournament background. After that, extract `_empty_home.html`.

Do not start by extracting `.match-card-v2`. The match card is the highest-risk unit because save, deadline, stepper, finished state, WC background, mobile layout and bottom sheet links all meet there.

## Completed decomposition step

Completed Step 1.1: bets bottom sheet markup was extracted from `templates/index.html` into `templates/partials/home/_bets_sheet.html`.

What changed:

- `templates/index.html` now keeps the bottom sheet in the same DOM position via `{% include 'partials/home/_bets_sheet.html' %}`.
- `templates/partials/home/_bets_sheet.html` contains only the previous bottom sheet HTML markup:
  - `.bets-sheet-overlay[data-bets-sheet-overlay]`
  - `.bets-sheet[data-bets-sheet-panel]`
  - `.bets-sheet-handle`
  - `.bets-sheet-top`
  - `.bets-sheet-title`
  - `.bets-sheet-close[data-bets-sheet-close]`
  - `.bets-sheet-content[data-bets-sheet-content]`
  - `.bets-sheet-loading`

Not changed:

- CSS remains inline in `templates/index.html`.
- JS remains inline in `templates/index.html`.
- Fetch logic remains in `templates/index.html`.
- Compact rendering logic remains in `templates/index.html`.
- Match cards were not touched.
- Class names, `data-*` attributes and nesting of the bottom sheet markup were preserved.

## Current partial structure

Current frontend partials for the home page:

- `templates/partials/home/_bets_sheet.html` - extracted player bets bottom sheet markup.

The rest of `templates/index.html` is still monolithic:

- month/day accordion wrappers;
- match cards;
- prediction form and steppers;
- finished match state;
- deadline/timer markup;
- inline CSS;
- inline JS.

## Next recommended step

Next low-risk step: extract the empty home state into `templates/partials/home/_empty_home.html`.

Why this is the next safest move:

- It does not touch match cards.
- It does not touch prediction forms.
- It does not touch deadlines.
- It does not touch steppers.
- It has no JS behavior.

Important condition: keep the extraction attached to the existing Jinja `{% for month in months %}{% else %}` behavior, so empty-state rendering remains equivalent.

## Completed decomposition step 1.2

Completed Step 1.2: empty home state markup was extracted from `templates/index.html` into `templates/partials/home/_empty_home.html`.

What changed:

- `templates/index.html` keeps the existing `{% for month in months %}{% else %}{% endfor %}` behavior.
- The empty state branch now renders via `{% include 'partials/home/_empty_home.html' %}`.
- `templates/partials/home/_empty_home.html` contains only the previous empty-state HTML markup:
  - `.empty-home`
  - football icon paragraph
  - "Нет доступных матчей" text
  - "Скоро появятся новые игры" text

Not changed:

- Month loop behavior.
- Day loop behavior.
- Accordion logic.
- Match rendering.
- CSS.
- JS.
- Match cards.
- Empty-state class, inline styles, text and nesting.

## Current partial structure after step 1.2

Current frontend partials for the home page:

- `templates/partials/home/_bets_sheet.html` - player bets bottom sheet markup.
- `templates/partials/home/_empty_home.html` - empty home state markup.

The rest of `templates/index.html` is still intentionally monolithic:

- month/day accordion wrappers;
- match cards;
- prediction form and steppers;
- finished match state;
- deadline/timer markup;
- inline CSS;
- inline JS.

## Next recommended step after step 1.2

Next low-risk step: extract the tournament/league badge inside the match card top line into `templates/partials/home/_match_league.html`.

Why this is the next safest move:

- It is a small visual sub-block.
- It does not own form state.
- It does not own stepper behavior.
- It does not own deadline timer behavior.
- It does not move the `.match-card-v2` root.

Important condition: preserve the exact Russia/RPL/Russian Cup/WC-2026 conditional logic, asset paths, `class="tournament-logo"`, and `.league-label` output.

## Completed decomposition step 1.3

Completed Step 1.3: the league/tournament badge inside the match card top line was extracted from `templates/index.html` into `templates/partials/home/_match_league.html`.

What changed:

- `templates/index.html` keeps the existing `.league-row` wrapper inside `.match-topline`.
- The league badge branch now renders via `{% include 'partials/home/_match_league.html' %}`.
- `templates/partials/home/_match_league.html` contains only the previous league badge markup:
  - Russia national team special case;
  - RPL logo and label;
  - Russian Cup logo and label;
  - WC-2026 logo and label;
  - fallback "Матч" label;
  - `class="tournament-logo"`;
  - `.league-label`.

Not changed:

- Match card root.
- Top line structure outside `.league-row`.
- Deadline/status right side.
- Prediction form.
- Steppers.
- Finished state.
- CSS.
- JS.
- Asset paths.
- Tournament conditional logic.

## Current partial structure after step 1.3

Current frontend partials for the home page:

- `templates/partials/home/_bets_sheet.html` - player bets bottom sheet markup.
- `templates/partials/home/_empty_home.html` - empty home state markup.
- `templates/partials/home/_match_league.html` - match card league/tournament badge markup.

The rest of `templates/index.html` is still intentionally monolithic:

- month/day accordion wrappers;
- match card root and state classes;
- deadline/timer markup;
- team blocks;
- prediction form and steppers;
- finished match state;
- inline CSS;
- inline JS.

## Next recommended step after step 1.3

Next low-risk step: extract the repeated team logo/name block into `templates/partials/home/_team_v2.html`.

Why this is the next safest move:

- The same team visual structure appears multiple times.
- It is still smaller than the whole match card.
- It does not own prediction form state.
- It does not own steppers.
- It does not own deadlines.

Important condition: preserve `get_flag(team)|safe` before `get_club_logo(team)|safe`, `.team-v2`, `.team-logo-v2`, `.team-name-v2`, and the current finished/non-finished parent layout.

## Completed decomposition step 1.4

Completed Step 1.4: the repeated team logo/name block was extracted from `templates/index.html` into `templates/partials/home/_team_v2.html`.

What changed:

- `templates/partials/home/_team_v2.html` now contains only the team visual block:
  - `.team-v2`
  - `.team-logo-v2`
  - `get_flag(team)` lookup
  - fallback `get_club_logo(team)|safe`
  - `.team-name-v2`
- `templates/index.html` sets `team` explicitly before each include:
  - finished home team;
  - finished away team;
  - non-finished home team;
  - non-finished away team.

Not changed:

- Match card root.
- `.teams-center` structure.
- Center score / VS area.
- Finished final score.
- Prediction form.
- Score controls.
- Steppers.
- Deadline logic.
- CSS.
- JS.
- Mobile layout rules.

## Current partial structure after step 1.4

Current frontend partials for the home page:

- `templates/partials/home/_bets_sheet.html` - player bets bottom sheet markup.
- `templates/partials/home/_empty_home.html` - empty home state markup.
- `templates/partials/home/_match_league.html` - match card league/tournament badge markup.
- `templates/partials/home/_team_v2.html` - reusable team logo/name block.

The rest of `templates/index.html` is still intentionally monolithic:

- month/day accordion wrappers;
- match card root and state classes;
- deadline/timer markup;
- center score and score controls;
- prediction form and steppers;
- finished match points/prediction state;
- inline CSS;
- inline JS.

## Next recommended step after step 1.4

Next safe step: pause broad decomposition and verify rendered UI manually or with browser screenshots before extracting larger match-state partials.

After that verification, the next candidate is `templates/partials/home/_match_finished.html`, but it should only be extracted if there is a clear snapshot baseline because finished state combines team blocks, final score, points text, prediction summary and the bets link.

Important condition: do not extract prediction controls, deadline logic, or the `.match-card-v2` root until finished-state extraction has been separately verified.

## Completed decomposition step 1.5

Completed Step 1.5: the day wrapper was extracted from `templates/index.html` into `templates/partials/home/_day_block.html`.

What changed:

- `templates/index.html` still owns the month loop and the day loop.
- `templates/index.html` still sets:
  - `day_idx = loop.index`
  - `day_is_open = day.key == open_day`
- The day loop body now renders via `{% include 'partials/home/_day_block.html' %}`.
- `templates/partials/home/_day_block.html` contains:
  - `.day-block`
  - `.day-header-v2 {{ day.type }}`
  - `onclick="toggleDay('{{ month_idx }}_{{ day_idx }}')"`
  - `id="arrow-day-{{ month_idx }}_{{ day_idx }}"`
  - `.day-count[data-match-count]`
  - `id="day-content-{{ month_idx }}_{{ day_idx }}"`
  - `.day-content {% if day_is_open %}open{% endif %}`
  - the existing loop over `day.matches`

Not changed:

- Month wrapper.
- Month/day accordion JS.
- Day IDs.
- Day `onclick`.
- `data-match-count`.
- `day.type` classes.
- Match card root.
- Prediction form.
- Steppers.
- Deadline logic.
- CSS.
- JS.

## Current partial structure after step 1.5

Current frontend partials for the home page:

- `templates/partials/home/_bets_sheet.html` - player bets bottom sheet markup.
- `templates/partials/home/_empty_home.html` - empty home state markup.
- `templates/partials/home/_match_league.html` - match card league/tournament badge markup.
- `templates/partials/home/_team_v2.html` - reusable team logo/name block.
- `templates/partials/home/_day_block.html` - day accordion wrapper and existing per-day match loop.

`templates/index.html` still owns:

- inline CSS;
- accordion JS;
- home screen root;
- month loop and month wrapper;
- `month_idx` / `month_is_open`;
- day loop and `day_idx` / `day_is_open`;
- bottom sheet include.

## Next recommended step after step 1.5

Next safe step: extract the month wrapper into `templates/partials/home/_month_block.html`, but only after confirming the day accordion still works visually.

Why this is the next logical step:

- The day wrapper is already isolated.
- The month wrapper is the remaining outer accordion structure.
- It can preserve the existing month loop contract if `month_idx` and `month_is_open` stay explicit.

Important condition: preserve `id="month-content-{{ month_idx }}"`, `id="arrow-month-{{ month_idx }}"`, `onclick="toggleMonth('{{ month_idx }}')"`, `month_is_open`, and the nested day include exactly.
