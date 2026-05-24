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
