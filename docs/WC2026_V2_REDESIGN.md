# WC2026 V2 Redesign

## Second-pass fixes

### Background circles/blobs

- Убрал blur с WC home background shapes через `filter: none`.
- Перевёл цветные формы на более crisp radial stops: у кругов теперь резче край и меньше glow-тумана.
- Сохранил FIFA26-inspired palette: electric blue, cyan, aqua, neon lime, coral и magenta.
- Дополнительно переопределил WC background на главной в `home.css`, чтобы корректировка была scoped именно к home visual layer.

### Score stepper

- Усилил `.score-stepper` в WC card:
  - border/rim стал заметнее;
  - добавлен внешний cyan/lime rim;
  - внутренние сегменты `+`, число, `-` стали читаться как единый input-control;
  - центральное число получило больше visual weight.
- Размеры touch targets и DOM/JS hooks не менялись.

### Deadline spacing

- Выровнял vertical rhythm внутри WC deadline row.
- Убрал случайные `margin` у label, timer, helper и cue.
- Задал единый `gap`, line-height и аккуратный timer padding, чтобы строки выглядели ровно на mobile.

## Что специально не трогал

- Backend, routes, tournament logic, scoring и AJAX.
- JS behavior, forms behavior и `data-*`.
- HTML structure и home partials.
- Обычные стили РПЛ/Кубка.
- Profile/table/admin WC styling.
- Shared `templates/base.html` в этом corrective pass.

## Риски

- Фон стал графичнее и контрастнее, поэтому на редких составах карточек с длинными названиями команд нужен ручной visual smoke на 360-380px.
- Усиленный stepper визуально тяжелее; важно проверить, что CTA всё ещё остаётся главным действием после выбора счёта.
- Старые WC стили table/profile/admin всё ещё отличаются от WC2026 V2 home.

## WC2026 V2 table/profile rollout

### Table blocks updated

- Standings header now uses WC2026 V2 electric blue, cyan, aqua, neon lime, coral and magenta.
- Header and table shell use crisp graphic circles instead of old dark/gold blur surfaces.
- Ranking rows are more event-like while preserving existing table structure and links.
- Top-3 rows received brighter V2 accents: lime/aqua, cyan/aqua and coral/magenta.
- Points pills now match the V2 score/CTA language and use IBM Plex Sans Condensed.
- Empty table state was aligned with the same V2 surface language.

### Profile blocks updated

- Profile hero moved from old dark/gold WC skin to vibrant WC2026 V2 panels.
- Stat cards now use V2 crisp accent circles and display typography for numbers.
- Accuracy cards and recent prediction rows now use the shared blue/cyan/lime/coral system.
- Recent prediction points badges were updated to V2 good/mid/empty states.
- Back CTA now follows the home CTA language: lime/aqua, high contrast, tactile.

### Intentionally preserved

- Backend, routes, tournament selection, ranking, scoring and profile stats logic.
- Table AJAX tournament switch, including body theme updates and bottom nav link sync.
- Existing table/profile HTML structure, links, forms behavior and JS behavior.
- RPL/Cup visual styling and admin/auth screens.
- Body text remains readable; IBM Plex Sans Condensed is used only as WC display/accent typography.

### Still old styling

- Admin pages still use their existing admin visual system.
- Auth/login screens were not touched.
- Match predictions detail page (`/match/<id>/predictions`) was not part of this pass.
- Some legacy gold WC rules remain earlier in CSS, but are intentionally overridden later by scoped WC2026 V2 rules.

### Rollout risks

- Table CSS has several older late sizing overrides, so future table changes should be checked at 360-380px with long usernames.
- Profile stat cards can still feel tight if numbers become unusually large.
- AJAX table switching should be manually smoke-tested because header HTML is replaced client-side while styling relies on stable IDs.

## WC2026 V2 home match-card readability pass

### Long team names

- WC home team names now use a tighter two-line layout in `body.tournament-wc2026`.
- Active WC card grid columns were adjusted so long names do not collide with the central score/stepper.
- Team names keep a readable two-line clamp with smaller WC-only font-size, tighter line-height, stable max-width and careful hyphenation.
- Finished WC team names received matching safer typography without changing the shared team partial.

### Match card background

- WC match-card background was brightened from dark/night premium toward the approved V2 home reference.
- The card now uses brighter electric blue surfaces with cyan, lime, coral and magenta graphic accents.
- Trophy opacity was reduced slightly so the brighter card surface stays readable.
- Topline, teams panel and prediction box were lightened together to keep one event-like card surface.

### Intentionally preserved

- Backend, JS, forms, `data-*`, match logic and scoring.
- HTML structure and `templates/partials/home/_team_v2.html`.
- RPL/Cup styles.
- Existing flag/logo sizing and score stepper touch targets.

### Risks

- Very long multi-word country names still need manual visual smoke at 360px because the card has fixed central input controls.
- The brighter card surface increases visual energy; CTA and score controls should be checked against active, predicted and finished WC cards.

## WC2026 V2 approved home match-card correction

### Light event-card surface

- Replaced the late WC home match-card override with a light white / pale-cyan / electric-blue event-card surface.
- Kept FIFA26-style shape accents, but moved them into crisp cyan, lime, coral and violet radial forms instead of the previous dark/night base.
- Kept the trophy layer, with lower opacity, brighter filtering and multiply blending so it reads as background texture and does not fight team names or controls.
- Topline, teams panel and prediction panel now share a translucent light surface with blue borders and soft blue shadowing.

### Team and score composition

- Active WC team/score grid now uses three stable zones: flexible team column, fixed central score column, flexible team column.
- The central score column is wider, so the two steppers and divider sit together without competing with the flags.
- Team columns use fixed flag rows and clamped two-line names, which keeps flags steady and keeps long names under their flags.
- Mobile breakpoints keep the central score column wide enough at 360-380px while reducing only the team text and flag shell.

### Score steppers

- WC score steppers now read as segmented blue blocks with a light center input.
- The plus/minus segments keep the existing button dimensions and data hooks, but get stronger blue fills and clearer white symbols.
- The score input remains the same form control, with a brighter white/cyan center and stronger number weight.

### Contracts preserved

- No backend, JS, HTML structure, form logic or `data-*` changes.
- RPL/Cup styles remain untouched because the changes are scoped under `body.tournament-wc2026`.

### Mobile cases checked by CSS constraints

- Long names are constrained to two lines and balanced wrapping for cases such as Netherlands, Bosnia and Herzegovina, Saudi Arabia, Trinidad and Tobago, and Equatorial Guinea.
- Residual risk: exact flag SVG proportions and browser text shaping can still vary slightly, so a real-device smoke pass at 360px is useful before release.

## WC2026 V2 critical home composition fix

### Team / score / team grid

- Added a late `body.tournament-wc2026` corrective layer in `static/css/home.css` for non-finished home match cards.
- Restored the card center to a stable three-column grid: left team, fixed score/stepper zone, right team.
- Left and right team blocks now stretch symmetrically inside their columns instead of inheriting the earlier start/end `justify-self` behavior.
- The center score area is pinned to the middle and keeps the existing two segmented steppers plus divider without changing form markup or JS hooks.

### Flags and team names

- Team blocks now use fixed WC-only rows: one row for the flag shell and one fixed two-line row for the name.
- Flag shells and flag images have stable WC-only sizes at default, `430px`, and `380px` breakpoints.
- Team names are centered directly under flags, clamped to two lines, and kept inside a fixed-height text zone so baselines do not drift between the left and right team.

### Long team names

- WC-only max-width and font-size tuning was tightened for narrow screens.
- Long names such as Netherlands, Bosnia and Herzegovina, Saudi Arabia, Trinidad and Tobago, and Equatorial Guinea are protected by two-line clamp, smaller mobile type, stable line-height, and fixed team rows.
- The score column keeps its own width, so long team names cannot push into or displace the stepper zone.

### Secondary polish

- Reduced the glossy card overlay and softened the large WC background blobs.
- Kept the approved light event-card surface and segmented score-stepper language.

### Intentionally preserved

- Backend, routes, scoring, tournament logic, JS behavior, form logic and `data-*` hooks.
- Existing HTML structure and home partials.
- RPL/Cup styling, because all new CSS is scoped through `body.tournament-wc2026`.
- Existing score-stepper interaction model and touch target dimensions.

### Risks

- Browser text shaping and translated country names can still wrap slightly differently across devices.
- The 360px layout remains dense because the card must fit two teams, two steppers, a divider and status content in one row.
- The requested FIFA guideline PDF and prior chat screenshots were not present as local project files during this pass, so this fix follows the already-approved WC2026 V2 CSS language documented above.

## WC2026 V2 mobile UI recovery pass

### Real selectors reviewed

- Home card layout is not built by `_match_card_v2.html` in the current tree; the existing partials are `_match_active.html`, `_match_finished.html`, and `_team_v2.html`.
- The active prediction layout is composed by `.teams-center`, `.team-v2`, `.team-logo-v2`, `.team-name-v2`, `.center-score`, `.score-input-row`, `.score-stepper`, `.stepper-btn`, `.score-input-v2`, and `.save-btn-v2`.
- The mobile breakpoints that materially affect the card are `480px`, `430px`, and `380px`.

### Composition recovery

- Replaced the late emergency WC layer with a cleaner mobile-first reset scoped to `body.tournament-wc2026`.
- `.teams-center` is now a strict three-column grid: flexible team, fixed score zone, flexible team.
- Team blocks use fixed rows for flag and name, so the two sides share height, baseline, and rhythm.
- The score zone keeps a stable center width and the steppers use fixed flex-basis values, preventing names from pushing into the controls.

### Team names and flags

- WC team names are clamped to two lines with WC-only font-size and line-height tuning.
- Long names are constrained by max-width, `min-width: 0`, and controlled wrapping so they stay under the flag and outside the score zone.
- Flag shells and images have stable sizes across default, `430px`, and `380px` breakpoints.

### Cleaner visual language

- Match cards moved back toward a light event-card surface: white, pale cyan, and restrained electric-blue depth.
- Heavy glass/gloss layers were reduced by removing backdrop blur from the home card surface and team panel.
- Background blobs were made secondary with lower opacity and smaller visual dominance, especially the red and purple shapes.
- The score stepper now reads as a lighter segmented control integrated into the card instead of a heavy glowing widget.
- The lime CTA remains bright but loses the heavier glow and gets a cleaner shadow.

### Preserved contracts

- No backend, JS, form logic, `data-*`, or HTML structure changes.
- RPL/Cup styling remains untouched because the recovery layer is scoped to `body.tournament-wc2026`.
- Existing score-stepper controls and touch-target structure are preserved.

### Remaining risks

- Real browser/device visual smoke is still recommended at 360px, 390px, and 430px because this CSS file contains several older WC layers with `!important`.
- The FIFA guideline PDF and prior chat screenshots were not present as local project files during this pass.

## WC2026 V2 home-card cascade cleanup

### Cascade map before cleanup

- Early shared home-card rules defined the normal RPL/Cup card, team grid, score controls, deadline row and mobile breakpoints.
- The first WC-specific block was the old `WC-2026 premium night skin`; it forced a dark/gold card, dark glass team panel, gold text, gold flag rings and global WC stepper colors.
- A later WC V2 home-screen block repainted the card as dark electric blue and also redefined `.match-card-v2`, `.teams-center`, `.team-logo-v2`, `.team-name-v2`, `.score-stepper`, `.stepper-btn`, `.score-input-v2`, `.match-topline`, deadline states and finished-card accents.
- A corrective WC block then changed the background blobs, deadline rhythm and score stepper weight.
- An approved light event-card block then moved the card toward white / pale cyan / electric blue and added a stricter team / score / team grid.
- The final old `WC2026 mobile-first reset` block came after the approved light block and won the cascade for the real card surface, team grid, flag sizes, team-name widths and stepper sizing.

### Conflicts removed or neutralized

- Removed the old dark/gold premium WC card overrides near the first match-card section.
- Removed the late forced trophy / z-index helper rules that sat before the V2 passes.
- Replaced the stacked late V2 passes with one authoritative final layer instead of keeping `visual redesign`, `corrective pass`, `approved home match card` and `mobile-first reset` as competing blocks.
- Kept the ordinary RPL/Cup styles and shared mobile rules intact; remaining earlier WC mobile flag tweaks are above the final layer and are intentionally overridden there.

### Authoritative layer

- The single final WC home-card layer now starts at `/* WC2026 V2 FINAL HOME CARD LAYER */` near the end of `static/css/home.css`.
- Nothing below that block can override the WC home card because it is the last block in the file.
- The layer owns the WC body/home background, event-card surface, trophy texture, topline, deadline row, team grid, flags, names, center score, score steppers, CTA, empty state and finished-card accents.

### Visual result targeted

- Card surface is light, clean and event-like: white, pale cyan and electric blue with restrained FIFA26 shapes.
- `.teams-center` is a strict three-column grid: left team, fixed center score zone, right team.
- Flag and name rows are fixed for active cards, keeping both teams on one axis.
- Team names are clamped to two lines and sized for long country names such as Netherlands, Bosnia and Herzegovina, Saudi Arabia, Trinidad and Tobago and Equatorial Guinea.
- The stepper remains the existing touch/control structure, but now reads as a clean segmented control integrated into the card.
- Background blobs are still energetic but lower-opacity so they do not compete with the card.

### Preserved contracts

- No backend, routes, scoring, tournament logic, JS behavior, form logic, `data-*` hooks or HTML structure were changed.
- RPL/Cup styling remains governed by the shared rules because the new authority is scoped to `body.tournament-wc2026`.

### Residual risks

- Real device smoke at 360px, 390px and 430px is still useful because country-name wrapping can vary by browser and locale.
- Earlier shared mobile rules still exist by design; future WC edits should be made only inside the final layer to avoid rebuilding cascade drift.
