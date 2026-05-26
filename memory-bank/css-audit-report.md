# CSS Audit Report

Аудит выполнен без изменений в `static/css/home.css`, `templates/index.html`, `templates/table.html`, `templates/profile.html`, `templates/base.html`.

Примечание: для HTML в колонке "Строки" указаны строки CSS внутри `<style>`. Inline-стили считаются по атрибутам `style="..."`. Для проверки мёртвого кода дополнительно просматривались include-шаблоны главной и таблицы, потому что `index.html`/`table.html` подключают живую разметку через partials.

## 1. Общая статистика

| Файл | Строки | !important | Медиа-запросы | Inline-стили |
|------|--------|------------|---------------|--------------|
| `static/css/home.css` | 2410 | 219 | 4 | 0 |
| `templates/index.html` | 0 | 0 | 0 | 0 |
| `templates/table.html` | 151 | 29 | 2 | 22 |
| `templates/profile.html` | 788 | 33 | 3 | 0 |
| `templates/base.html` | 1133 | 57 | 2 | 0 |

Итого в проверяемых файлах: 4482 строки CSS, 338 `!important`, 11 медиа-запросов, 22 inline-стиля.

## 2. Дублирования

| Селектор | Где дублируется | Что делать |
|----------|-----------------|------------|
| `body.tournament-wc2026 .match-card-v2:not(.finished) .teams-center > .team-v2 > .team-logo-v2` | `home.css`: 1528, 1973, 2257, 2314, 2340 | Оставить один финальный размер/лейаут для WC-флагов, мобильные отличия вынести в короткие media overrides. |
| `body.tournament-wc2026 .match-card-v2 .team-logo-v2 .flag-icon/img` | `home.css`: 1993, 2005, 2267, 2303 | Сейчас 46/44/88px конкурируют между собой; выбрать целевой размер и удалить промежуточные слои. |
| `body.tournament-wc2026 .match-card-v2 .team-name-v2` | `home.css`: 2011, 2227, 2276, 2346, 2389, 2406 | Свести типографику имени команды в один WC-блок; убрать повторные `color/font-weight/line-clamp` с `!important`. |
| `body.tournament-wc2026 .match-card-v2 .deadline-timer` | `home.css`: 1852, 1877, 2204, 2214, 2389 | Разделить base-state, warning, danger, closed; сейчас цвет и фон переопределяются несколькими слоями. |
| `body.tournament-wc2026 .month-title`, `.day-title` | `home.css`: 1754, 1761, 2201, 2202; `base.html`: 909, 924, 1098 | Убрать турнирную типографику аккордеона из `base.html` или из `home.css`; сейчас ответственность смешана. |
| `body.tournament-wc2026 .profile-hero` | `profile.html`: 381, 393, 620, 634 | Старый WC-профиль и новый V2-слой живут одновременно; оставить V2-блок, если он финальный. |
| `body.tournament-wc2026 .stat-value` | `profile.html`: 447, 649, 718, 790 | Свести цветовые варианты `blue/gray` в токены или модификаторы без повторной перекраски. |
| `body.tournament-wc2026 .bottom-nav` | `base.html`: 597, 1120 | Ранний тёмный WC nav полностью перебивается V2 nav; удалить старый блок после проверки. |
| `.table-shell`, `.standings-table`, `.player-name`, `.points-number` | `table_content.html` и `table.html` через include | Вынести table CSS из partial в отдельный слой; сейчас базовый table CSS многократно перекрывается финальным unified sizing. |
| Повторяющиеся значения | `border-radius: 999px`, `20/22/24px`, `rgba(0,78,140,...)`, `#0077c8`, `#d8ff22` | Завести tokens для радиусов, синих теней и WC-палитры; часть уже есть в `:root`, но используется непоследовательно. |

## 3. Конфликтующие слои

| Селектор (побеждает) | Селектор (проигрывает) | Файл |
|----------------------|------------------------|------|
| `body.tournament-wc2026 .match-card-v2 .team-logo-v2 .flag-icon/img` на 88px | WC-слои на 46px/44px/50px и base `body.tournament-wc2026 .flag-icon` на 36px | `home.css`, `base.html` |
| `body.tournament-wc2026 .match-card-v2 .team-name-v2` с белым текстом | Более ранний WC слой с `color: #041a48 !important` | `home.css` |
| Финальный `body.tournament-wc2026 .match-card-v2` с `color: #ffffff !important` | Блоки deadline/status с `color: rgba(6,22,58,0.72) !important` | `home.css` |
| V2 `body.tournament-wc2026 .bottom-nav` | Ранний `body.tournament-wc2026 .bottom-nav` с `background: #0d1f3a !important` | `base.html` |
| V2 `body.tournament-wc2026 #wc-standings-header` | Базовый `#wc-standings-header` | `table.html` |
| V2 `body.tournament-wc2026 .profile-hero/.stat-card/.section-card` | Старый WC premium profile block | `profile.html` |
| `.standings-table tbody tr.top-* .points-number` финального table polish | Ранние `body.tournament-cup/wc2026 .standings-table tr.top-* td.points-cell` | `table_content.html` |
| Inline `style="display:flex..."` на headers | CSS для `#cup-standings-header`, `#wc-standings-header` | `table.html` |

## 4. Мёртвый код

| Селектор | Файл | Почему мёртвый |
|----------|------|----------------|
| `.status-open` | `home.css` | В проверенных шаблонах открытый статус не рендерится: используются `status-done` и `status-closed`; `status-open` есть только в CSS. |
| `.prediction-label` | `home.css`, `base.html` | В разметке используется `prediction-label-finished`; JS/partials не создают `.prediction-label`. |
| `.btn-outline` | `base.html` | В проверенных пользовательских страницах класс не используется; может быть общим компонентом для других страниц, но в данном scope мёртвый. |
| `.username-cell` | `base.html` | Не найден в проверенных шаблонах и partials. |
| `.bottom-nav a .icon` | `base.html` | Навигация использует `.nav-icon`, не `.icon`. |
| `.tournament-wc2026 .month-header`, `.day-header`, `.toggle-icon` | `base.html` | На главной используются `.month-header-v2`/`.day-header-v2`; старые `.month-header/.day-header` встречаются в admin-шаблонах, но не с `toggle-icon`. |
| Ранний WC bottom-nav block | `base.html` | Не полностью мёртвый селекторно, но фактически перебит поздним V2-блоком с теми же selectors и `!important`. |
| Ранние WC team-logo размеры 46/50px | `home.css` | Фактически перебиты финальным принудительным 88px-блоком после media queries. |

Важно: `.flag-icon` не считать мёртвым, хотя его нет в HTML-шаблонах напрямую: он генерируется в `app/utils.py` через `get_flag()`.

## 5. Проблемы специфичности

| Селектор | Специфичность | Рекомендация |
|----------|---------------|--------------|
| `body.tournament-wc2026 .match-card-v2:not(.finished) .teams-center > .team-v2 > .team-logo-v2 .flag-icon` | `0-7-1` | Заменить на scoped utility/modifier вроде `.wc-card .team-flag`; убрать цепочку потомков. |
| `body.tournament-wc2026 .match-card-v2:not(.finished) .teams-center > .team-v2 > .team-logo-v2` | `0-6-1` | Перенести состояние в класс компонента: `.match-card-v2.is-wc .team-logo-v2`. |
| `body.tournament-wc2026 .match-card-v2 .score-stepper .stepper-btn[data-step="-1"]` | `0-5-1` | Использовать `.stepper-btn.is-minus`/`.is-plus`; атрибут оставить для JS. |
| `body.tournament-wc2026 #wc-standings-header div div:first-child` | `1-2-3` | Убрать зависимость от `div div`; дать заголовку класс (`.standings-title`). |
| `#cup-standings-header div div:last-child` | `1-1-2` | Заменить структурный селектор на класс подзаголовка. |
| `body.tournament-wc2026 .bottom-nav a.active .nav-icon` | `0-4-2` | Ввести `.bottom-nav-link` и `.is-active`, снизить зависимость от тега `a`. |
| `.score-input-v2` с 10 `!important` внутри одного правила | `0-1-0` | Разделить базовый input, locked state и WC theme; убрать `!important` после упорядочивания слоёв. |

Главная причина высокой специфичности: поздние WC2026 overrides пытаются победить старые правила через `body.tournament-wc2026`, длинные цепочки потомков и массовый `!important`.

## 6. Адаптивность

| Медиа-запрос | Разрешение | Проблема |
|--------------|------------|----------|
| `@media (max-width: 480px)` | 430px/375px тоже попадают | Есть в `home.css` и `base.html`; часть правил дублирует 430px, нужно понять, что реально отличается между 480 и 430. |
| `@media (max-width: 430px)` | Покрывает 430px и 375px | Основной мобильный breakpoint есть во всех крупных слоях, но много `!important`; риск неожиданных побед над 380px/480px. |
| `@media (max-width: 380px)` | Покрывает 375px | Есть в `home.css` и `profile.html`; затем в `home.css` после media идёт финальный 88px WC-блок, который отменяет часть 380px-правил для флагов. |
| `@media (prefers-reduced-motion: reduce)` | accessibility | Есть только в `base.html`; хорошо, но анимации `wc26FloatA/B` и `matchFade` зависят от глобального reset через `*`, что работает грубо. |
| `table.html` headers | 430px | Inline-стили в HTML и JS `outerHTML` повторяют desktop размеры, CSS компенсирует через `!important`; лучше заменить inline разметку классами. |

Покрытие основных разрешений: 430px и 375px покрыты явно; 768px отдельного breakpoint нет. Это не обязательно ошибка, но сейчас desktop/tablet полагается на max-width контейнера 600px и мобильные max-width правила.

## 7. Производительность

| Эффект | Где | Риск |
|--------|-----|------|
| `backdrop-filter: blur(24px/20px/18px/16px/14px/10px)` | `base.html`, `home.css`, `profile.html`, `table.html` | Дорогой blur на sticky/fixed/header/cards/sheets; на мобильных может давать просадки при скролле. |
| Многоступенчатые `box-shadow` | `match-card-v2`, `.bottom-nav`, `.profile-hero`, `.table-shell`, `.score-stepper` | Несколько теней на повторяющихся карточках увеличивают paint cost. |
| `background-attachment: fixed` | `body`, `body.tournament-wc2026` | На мобильных браузерах часто дорогой эффект при скролле. |
| `animation: matchFade` на каждой карточке | `home.css` | При длинном списке матчей одновременные анимации могут быть заметны. |
| `animation: wc26FloatA/B` на fixed pseudo-elements | `home.css`, `profile.html`, `table_content.html` | Постоянные фоновые анимации на fixed элементах могут держать композитор занятым. |
| `transition: max-height 0.35s` до `6000px` | `.day-content.open` | Анимация layout-свойства тяжелее transform/opacity; при больших списках матчей возможны рывки. |
| `filter: drop-shadow`, `filter: grayscale/saturate/contrast` | WC logos, finished logos | Фильтры на изображениях в списках добавляют paint/composite нагрузку. |

## 8. Рекомендации по cleanup

1. Разделить CSS на слои: `base`, `components`, `pages/home`, `pages/table`, `pages/profile`, `theme/wc2026`. Сейчас `base.html` содержит и shell, и home/table overrides.
2. Убрать inline-стили из `table.html` и JS `outerHTML`: дать элементам классы (`standings-header`, `standings-logo`, `standings-title`, `standings-subtitle`).
3. Сначала стабилизировать WC2026: выбрать один финальный V2-блок и удалить старые WC premium overrides, которые полностью перебиваются.
4. Сократить `!important`: начать с размеров флагов/логотипов, team names, bottom nav и table sizing. Это самые шумные зоны.
5. Заменить длинные descendant selectors на классы компонентов и модификаторы (`.match-card-v2.is-wc`, `.team-logo-v2.is-large`, `.standings-header.is-wc`).
6. Вынести повторяющиеся значения в CSS variables: радиусы `12/14/18/20/22/24/999`, shadows, WC colors, common gradients.
7. Перенести table CSS из `table_content.html` в обычный CSS-файл; partial должен рендерить разметку, а не большой style layer.
8. Удалить или подтвердить legacy selectors: `.status-open`, `.prediction-label`, `.username-cell`, `.bottom-nav a .icon`, `.toggle-icon`.
9. Проверить 375/430/768 визуально после cleanup: особое внимание WC флагам, score stepper, table row widths, header title wrapping.
10. Снизить дорогие эффекты на списках: blur оставить на shell/header/sheet, но убрать с повторяющихся карточек или отключать на mobile/reduced-motion.
