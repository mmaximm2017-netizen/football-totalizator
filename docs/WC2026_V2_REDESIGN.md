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
