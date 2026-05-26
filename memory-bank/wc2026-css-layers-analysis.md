# Анализ слоёв для центрального блока карточки ЧМ-2026

Файл: `static/css/home.css`

Граница финального WC-блока: строка 1606, комментарий `/* WC2026 V2 FINAL HOME CARD LAYER */`.

Принцип оценки:
- правила после строки 1606 находятся в финальном WC-слое;
- `!important` побеждает обычные правила, даже если селектор менее специфичный;
- при равном приоритете более специфичный селектор побеждает менее специфичный;
- свойства, которые не переопределены позднее, продолжают применяться из ранних слоёв.

## `.teams-center`

| Строка | Селектор | Свойства | `!important` | Позиция | Побеждает? |
|--------|----------|----------|--------------|---------|------------|
| 528-535 | `.teams-center` | `display`, `grid-template-columns: ... 128px ...`, `align-items`, `gap`, `margin`, `padding` | Нет | До WC | Нет для переопределённых свойств; может быть только базовым fallback |
| 537-546 | `.match-card-v2:not(.finished) .teams-center` | `padding`, `border-radius`, `background`, `border`, `box-shadow`, `transition` | Нет | До WC | Нет для фона/бордера, так как WC-слой задаёт прозрачный фон и нулевые рамки; ранний `transition` может остаться |
| 1449-1453 | `.teams-center` в mobile media | `grid-template-columns: ... 114px ...`, `gap`, `margin` | Нет | До WC | Нет, финальный WC-слой позднее и с `!important` для ключевых свойств |
| 1455-1458 | `.match-card-v2:not(.finished) .teams-center` в mobile media | `padding`, `border-radius` | Нет | До WC | Частично да: финальный WC-слой не сбрасывает общий `padding`, только поздний mobile-WC сбрасывает `padding-left/right` |
| 1881-1897 | `body.tournament-wc2026 .match-card-v2:not(.finished) .teams-center`, `body.tournament-wc2026 .match-card-v2 .teams-center` | `display`, `grid-template-columns: ... 132px ...`, `align-items`, `column-gap`, `min-width`, `width`, `margin`, `padding`, `overflow`, `border-radius`, `background`, `border`, `box-shadow`, `backdrop-filter` | Частично: `background`, `border`, `box-shadow` | После WC | Частично: остаются `min-width`, `padding`, `overflow`, `border-radius`, `background/border/box-shadow`; но `grid-template-columns`, `gap/column-gap`, `margin`, `width`, `align-items` перебиты строкой 2140 |
| 2089-2095 | `body.tournament-wc2026 .match-card-v2:not(.finished) .teams-center`, `body.tournament-wc2026 .match-card-v2 .teams-center` в mobile media | `grid-template-columns: ... 128px ...`, `column-gap`, `padding-left`, `padding-right` | Нет | После WC | Частично: `padding-left/right` остаются; `grid-template-columns` и `column-gap` перебиты строкой 2140 с `!important` |
| 2140-2150 | `body.tournament-wc2026 .teams-center` | `display`, `grid-template-columns: ... auto ...`, `align-items`, `justify-items`, `gap`, `column-gap`, `margin`, `width`, `position` | Да | После WC | Да для перечисленных свойств; но не сбрасывает `padding`, `overflow`, `border-radius`, `min-width` из строк 1881-1897 |

## `.team-v2`

| Строка | Селектор | Свойства | `!important` | Позиция | Побеждает? |
|--------|----------|----------|--------------|---------|------------|
| 549-555 | `.team-v2` | `display:flex`, `flex-direction`, `align-items`, `justify-content:flex-start`, `gap`, `min-width` | Нет | До WC | Нет для display/flex-свойств; `gap/min-width` могут действовать, если не перебиты более поздними правилами |
| 558-568 | `.match-card-v2:not(.finished) .team-v2` | `display:grid`, `grid-template-rows`, `align-content`, `align-items`, `gap`, `padding`, `border-radius`, `background`, `border`, `box-shadow` | Нет | До WC | Нет для `display/align-items/gap/padding`, так как более поздние WC-правила переопределяют; часть декоративных сбросов уже повторена в WC |
| 571-573 | `.match-card-v2:not(.finished) .teams-center > .team-v2:first-child` | `justify-self:start` | Нет | До WC | Нет, строка 1900 позднее задаёт `justify-self: stretch` |
| 575-577 | `.match-card-v2:not(.finished) .teams-center > .team-v2:last-child` | `justify-self:end` | Нет | До WC | Нет, строка 1900 позднее задаёт `justify-self: stretch` |
| 1476-1487 | `.match-card-v2:not(.finished) .team-v2` в mobile media | `grid-template-rows`, `gap`, `padding`, `border-radius` | Нет | До WC | Нет для `gap/padding`, строка 1900 позднее задаёт другие значения |
| 1900-1913 | `body.tournament-wc2026 .match-card-v2:not(.finished) .teams-center > .team-v2`, плюс `:first-child/:last-child` | `display:flex`, `flex-direction`, `align-self`, `align-items`, `justify-content`, `justify-self:stretch`, `width:100%`, `min-width`, `gap:5px`, `padding:0`, `text-align` | Нет | После WC | Частично да: `justify-self:stretch`, `width:100%`, `min-width`, `gap`, `padding`, `text-align` остаются, потому что строка 2152 их не сбрасывает |
| 2152-2161 | `body.tournament-wc2026 .match-card-v2 .team-v2`, `body.tournament-wc2026 .match-card-v2:not(.finished) .teams-center > .team-v2`, плюс `:first-child/:last-child` | `display:flex`, `flex-direction`, `align-items`, `justify-content:flex-start`, `height:auto` | Да | После WC | Да для перечисленных свойств; не побеждает свойства, которых нет в этом блоке: `justify-self:stretch`, `width:100%`, `gap:5px`, `padding:0` из строки 1900 |

## `.team-logo-v2`

| Строка | Селектор | Свойства | `!important` | Позиция | Побеждает? |
|--------|----------|----------|--------------|---------|------------|
| 255-258 | `.match-card-v2.finished .team-logo-v2 img`, `.match-card-v2.finished .team-logo-v2 .flag-icon` | `filter`, `opacity` | Нет | До WC | Да, если не переопределено позднее; финальный WC-слой не меняет `filter/opacity` |
| 261-267 | `.match-card-v2.finished .team-logo-v2` | `width:86px`, `height:86px`, `box-shadow` | Нет | До WC | Нет для `width/height`, строка 2125 задаёт `88px !important`; `box-shadow` может действовать, если не сброшен более специфичным WC-правилом |
| 269-272 | `.match-card-v2.finished .team-logo-v2 img`, `.flag-icon` | `width:66px !important`, `height:66px !important` | Да | До WC | Конфликтует с 2114; строка 2114 позднее и тоже `!important`, поэтому должна победить, если `--flag-size` определён |
| 579-588 | `.match-card-v2:not(.finished) .teams-center > .team-v2 > .team-logo-v2` | `justify-self`, `width:108px`, `height:108px`, `border-radius`, `background`, `box-shadow`, `backdrop-filter` | Нет | До WC | Нет для `width/height`; `justify-self`, `border-radius`, `background`, `box-shadow`, `backdrop-filter` могут остаться, если не перебиты |
| 590-607 | `.team-logo-v2` | `width:104px`, `height:104px`, `border-radius`, `display:flex`, `align-items`, `justify-content`, `background`, `box-shadow`, `backdrop-filter` | Нет | До WC | Нет для размеров/display/alignment; декоративные свойства могут остаться, если не сброшены позднее |
| 610-614 | `.team-logo-v2 img` | `width:80px !important`, `height:80px !important`, `object-fit`, `margin` | Частично | До WC | Нет для размеров, если строка 2114 валидна; `margin:0 !important` сохраняется |
| 1460-1464 | `.team-logo-v2` в mobile media | `width:92px`, `height:92px`, `border-radius` | Нет | До WC | Нет для размеров; `border-radius` может остаться |
| 1466-1469 | `.team-logo-v2 img` в mobile media | `width:70px !important`, `height:70px !important` | Да | До WC | Нет, строка 2114 позднее и тоже `!important` |
| 1490-1494 | `.match-card-v2:not(.finished) .teams-center > .team-v2 > .team-logo-v2` в mobile media | `width:92px`, `height:92px`, `border-radius` | Нет | До WC | Нет для размеров; `border-radius` может остаться |
| 1496-1500 | `.match-card-v2:not(.finished) .teams-center > .team-v2 > .team-logo-v2 img`, `.flag-icon` в mobile media | `width:70px !important`, `height:70px !important` | Да | До WC | Нет, строка 2114 позднее и тоже `!important` |
| 1523-1526 | `.match-card-v2.finished .team-logo-v2` в mobile media | `width:76px`, `height:76px` | Нет | До WC | Нет для размеров |
| 1528-1531 | `.match-card-v2.finished .team-logo-v2 img`, `.flag-icon` в mobile media | `width:60px !important`, `height:60px !important` | Да | До WC | Нет, строка 2114 позднее и тоже `!important` |
| 1916-1920 | `body.tournament-wc2026 .match-card-v2 .team-logo-v2 .flag-icon` | `box-shadow` | Да | После WC | Да для `.flag-icon` box-shadow |
| 2114-2123 | `body.tournament-wc2026 .team-logo-v2 .flag-icon`, `body.tournament-wc2026 .team-logo-v2 img` | `width`, `height`, `min/max-width`, `min/max-height`, `object-fit` | Частично: `width/height` | После WC | Да для размеров изображения, но зависит от того, определён ли `--flag-size`; в `home.css` переменная не найдена |
| 2125-2137 | `body.tournament-wc2026 .team-logo-v2` | `margin:0`, `padding:0`, `width:88px`, `height:88px`, `min/max`, `display:flex`, `justify-content`, `align-items` | Да для ключевых свойств | После WC | Да для контейнера флага |

## `.center-score`

| Строка | Селектор | Свойства | `!important` | Позиция | Побеждает? |
|--------|----------|----------|--------------|---------|------------|
| 617-624 | `.center-score` | `text-align`, `display:flex`, `flex-direction`, `align-items`, `align-self`, `gap` | Нет | До WC | Нет для display/alignment; `text-align` и `gap` могут остаться, если не перебиты |
| 1922-1930 | `body.tournament-wc2026 .match-card-v2 .center-score` | `align-self`, `justify-self`, `width:100%`, `min-width`, `z-index`, `display:flex`, `justify-content` | Нет | После WC | Частично да: `width:100%`, `min-width`, `z-index` остаются; остальные перебиты строкой 2190 |
| 2190-2199 | `body.tournament-wc2026 .match-card-v2 .center-score` | `display:flex`, `flex-direction`, `align-items`, `justify-content`, `align-self`, `justify-self`, `height:88px`, `margin:0` | Да | После WC | Да для перечисленных свойств |

## `.score-input-row`

| Строка | Селектор | Свойства | `!important` | Позиция | Побеждает? |
|--------|----------|----------|--------------|---------|------------|
| 789-794 | `.score-input-row` | `display:flex`, `justify-content`, `align-items`, `gap:9px` | Нет | До WC | Нет для `gap`; display/alignment могут оставаться, но WC-слой задаёт alignment позднее |
| 1558-1560 | `.score-input-row` в mobile media | `gap:7px` | Нет | До WC | Нет, строка 1932 позднее задаёт `gap:5px`, а mobile-WC строка 2108 задаёт `gap:4px` на малых экранах |
| 1932-1941 | `body.tournament-wc2026 .match-card-v2 .score-input-row` | `position`, `z-index`, `width:100%`, `justify-content`, `align-items`, `gap:5px`, `flex-wrap`, `margin:0 auto` | Частично: `position`, `z-index` | После WC | Частично да: `position`, `z-index`, `width`, `justify-content`, `align-items`, `gap`, `flex-wrap` остаются; `margin` перебит строкой 2201 |
| 2108-2110 | `body.tournament-wc2026 .match-card-v2 .score-input-row` в mobile media | `gap:4px` | Нет | После WC | Да на `max-width:430px`, потому что финальный простой `.score-input-row` не меняет `gap` |
| 2201-2204 | `body.tournament-wc2026 .score-input-row` | `margin:0`, `padding:0` | Да | После WC | Да для `margin/padding` |

## `.score-stepper`

| Строка | Селектор | Свойства | `!important` | Позиция | Побеждает? |
|--------|----------|----------|--------------|---------|------------|
| 838-855 | `.score-stepper` | `display:grid`, `grid-template-*`, `align-items`, `width:52px`, `height:auto`, `border-radius`, `background`, `border`, `box-shadow`, `overflow`, `transition` | Нет | До WC | Нет для размеров/цветов, если WC-слой применён; `display`, `grid-template-columns`, `height`, `overflow`, `transition` могут сохраняться |
| 891-894 | `.score-stepper .stepper-btn[data-step="1"]` | `grid-row`, `border-bottom` | Нет | До WC | `grid-row` остаётся; цвет бордера перебит строкой 1979 |
| 896-902 | `.score-stepper .score-input-v2` | `grid-row`, `background`, `box-shadow` | Частично | До WC | `grid-row` остаётся; background/box-shadow перебиты строками 1988 и 2232 |
| 905-908 | `.score-stepper .stepper-btn[data-step="-1"]` | `grid-row`, `border-top` | Нет | До WC | `grid-row` остаётся; цвет бордера перебит строкой 1983 |
| 910-917 | `.score-stepper:focus-within` | `border-color`, `box-shadow` | Нет | До WC | Нет, строка 1957 позднее и с `!important` для части свойств |
| 925-930 | `.match-card-v2.closed .score-stepper` | `border-color`, `box-shadow` | Нет | До WC | Конфликтует с WC-слоем; для WC карточки обычное правило 1943/1957 обычно сильнее по позиции и `!important` |
| 1568-1574 | `.score-stepper` в mobile media | `grid-template-*`, `width:46px`, `height:auto`, `border-radius` | Нет | До WC | Нет, WC-слой позднее задаёт `width`, а mobile-WC строка 2097 задаёт `width:48px` |
| 1943-1955 | `body.tournament-wc2026 .match-card-v2 .score-stepper` | `flex`, `width:50px`, `grid-template-rows`, `border-radius`, `background`, `border`, `box-shadow`, `backdrop-filter` | Частично: `background`, `border`, `box-shadow` | После WC | Да на desktop; на mobile `width/flex-basis` перебиты строкой 2097 |
| 1957-1962 | `body.tournament-wc2026 .match-card-v2 .score-stepper:focus-within` | `border-color`, `box-shadow` | Да | После WC | Да |
| 1979-1981 | `body.tournament-wc2026 .match-card-v2 .score-stepper .stepper-btn[data-step="1"]` | `border-bottom-color` | Да | После WC | Да |
| 1983-1985 | `body.tournament-wc2026 .match-card-v2 .score-stepper .stepper-btn[data-step="-1"]` | `border-top-color` | Да | После WC | Да |
| 1988-1998 | `body.tournament-wc2026 .match-card-v2 .score-input-v2`, `body.tournament-wc2026 .match-card-v2 .score-stepper .score-input-v2` | `height`, `width`, `background`, `color`, `font-size`, `font-weight`, `text-shadow`, `box-shadow` | Частично | После WC | Частично; color/background/font-weight дополнительно перебиты строкой 2232 теми же значениями |
| 2097-2100 | `body.tournament-wc2026 .match-card-v2 .score-stepper` в mobile media | `flex-basis:48px`, `width:48px` | Нет | После WC | Да на `max-width:430px` для ширины степпера |
| 2103-2106 | `body.tournament-wc2026 .match-card-v2 .score-input-v2`, `.score-stepper .score-input-v2` в mobile media | `width:48px`, `font-size:17px` | Частично: `width` | После WC | Да на `max-width:430px` для `width`; `font-size` может победить desktop 19px, так как позднее и равная/достаточная специфичность |
| 2232-2236 | `body.tournament-wc2026 .match-card-v2 .score-input-v2`, `body.tournament-wc2026 .match-card-v2 .score-stepper .score-input-v2` | `color`, `background`, `font-weight` | Да | После WC | Да для этих трёх свойств |

## `.stepper-btn`

| Строка | Селектор | Свойства | `!important` | Позиция | Побеждает? |
|--------|----------|----------|--------------|---------|------------|
| 858-878 | `.stepper-btn` | `width`, `height:30px`, `border`, `background`, `color`, `font-size`, `font-weight`, `line-height`, `box-shadow`, `cursor`, `transition`, touch styles | Нет | До WC | Частично: `width`, `border`, `line-height`, `cursor`, `transition`, touch styles остаются; `height/background/color/font-size/font-weight/box-shadow` перебиты WC |
| 881-889 | `.stepper-btn:not(:disabled):active` | `transform`, `background`, `color`, `box-shadow` | Нет | До WC | Частично: `transform` остаётся; colors/box-shadow перебиты active WC |
| 891-894 | `.score-stepper .stepper-btn[data-step="1"]` | `grid-row`, `border-bottom` | Нет | До WC | `grid-row` остаётся; border color перебит |
| 905-908 | `.score-stepper .stepper-btn[data-step="-1"]` | `grid-row`, `border-top` | Нет | До WC | `grid-row` остаётся; border color перебит |
| 919-923 | `.stepper-btn:disabled` | `cursor`, `color`, `background` | Нет | До WC | Может применяться для disabled, но WC `.stepper-btn` с `background/color !important` может визуально перебивать disabled color/background |
| 1120-1124 | `.stepper-btn:focus-visible`, `.save-btn-v2:focus-visible`, `.bets-link-v2:focus-visible` | `outline`, `outline-offset` | Нет | До WC | Да, финальный WC-слой не меняет outline |
| 1127-1130 | `.stepper-btn:focus-visible`, `.save-btn-v2:focus-visible` | `outline-color` | Нет | До WC | Да |
| 1576-1580 | `.stepper-btn` в mobile media | `width`, `height:28px`, `font-size:14px` | Нет | До WC | Нет для `height/font-size`, строка 1964 позднее задаёт WC-значения |
| 1964-1972 | `body.tournament-wc2026 .match-card-v2 .stepper-btn` | `height:28px`, `background`, `color`, `font-size:15px`, `font-weight:800`, `text-shadow`, `box-shadow` | Частично | После WC | Да для перечисленных свойств |
| 1974-1977 | `body.tournament-wc2026 .match-card-v2 .stepper-btn:active` | `background`, `color` | Да | После WC | Да для active |
| 1979-1981 | `body.tournament-wc2026 .match-card-v2 .score-stepper .stepper-btn[data-step="1"]` | `border-bottom-color` | Да | После WC | Да |
| 1983-1985 | `body.tournament-wc2026 .match-card-v2 .score-stepper .stepper-btn[data-step="-1"]` | `border-top-color` | Да | После WC | Да |

## `.score-divider`

| Строка | Селектор | Свойства | `!important` | Позиция | Побеждает? |
|--------|----------|----------|--------------|---------|------------|
| 830-835 | `.score-divider` | `font-size:21px`, `font-weight`, `color`, `line-height`, `padding-bottom` | Нет | До WC | Частично: `font-size/font-weight/padding-bottom` остаются; `color/line-height` перебиты WC |
| 1595-1598 | `.score-divider` в mobile media | `font-size:20px`, `padding-bottom:0` | Нет | До WC | Да на mobile для `font-size`; `padding-bottom` остаётся 0 |
| 2001-2005 | `body.tournament-wc2026 .match-card-v2 .score-divider`, `body.tournament-wc2026 .match-card-v2 .vs-badge` | `flex`, `color`, `line-height`, `text-shadow` | Частично: `color` | После WC | Частично: `flex`, `color`, `text-shadow` остаются; `line-height` перебит строкой 2206 с `!important` |
| 2206-2210 | `body.tournament-wc2026 .score-divider` | `display:inline-flex`, `align-items:center`, `line-height:1` | Да | После WC | Да для перечисленных свойств |

## Правила, которые реально побеждают

1. Для сетки `.teams-center` побеждает поздний блок строки 2140-2150 по ключевым свойствам `display`, `grid-template-columns`, `align-items`, `justify-items`, `gap`, `margin`, `width`, потому что все они с `!important`.
2. Для контейнера флага `.team-logo-v2` побеждает строка 2125-2137: контейнер становится `88x88`, без `margin/padding`, с flex-центрированием.
3. Для содержимого флага (`img`/`.flag-icon`) должна побеждать строка 2114-2123, но в `home.css` не найдено определение `--flag-size`; если переменная не задана в другом файле, декларации с `var(--flag-size)` будут невалидны.
4. Для `.center-score` побеждает строка 2190-2199: блок получает `height:88px`, `margin:0`, flex-центрирование.
5. Для `.score-input-row` побеждает строка 2201-2204 только по `margin/padding`. Остальные свойства (`width:100%`, `gap`, `position`, `z-index`, `flex-wrap`) остаются из строки 1932, а на mobile `gap:4px` остаётся из строки 2108.
6. Для `.score-stepper` побеждает строка 1943-1955 на desktop, а на mobile ширина перебивается строкой 2097-2100.
7. Для `.stepper-btn` побеждает строка 1964-1972; focus-outline остаётся из строк 1120-1130.
8. Для `.score-divider` побеждает строка 2206-2210 по display/alignment/line-height, но цвет и flex остаются из строки 2001.

## Конфликты

1. `.teams-center`: строка 1881 задаёт `padding: 4px 0 6px`, а строка 2140 не сбрасывает padding. Из-за этого центральный блок может иметь вертикальный сдвиг относительно ожидания "ровно по центру".
2. `.teams-center`: строка 2089 в mobile media оставляет `padding-left/right: 0`, но не сбрасывает верх/низ. Вертикальный padding из строки 1881 остаётся.
3. `.team-v2`: строка 1900 оставляет `justify-self: stretch` и `width: 100%`. Это конфликтует с идеей `justify-items:center` у родительской сетки: элемент растягивается на колонку, хотя содержимое внутри центрируется.
4. `.team-v2`: строка 1900 оставляет `gap:5px`. Поздний блок 2152 не задаёт `gap`, а `.team-name-v2` дополнительно получает `margin-top:8px`; итоговый отступ между флагом и названием может стать больше ожидаемого.
5. `.team-logo-v2`: строка 2114 использует `var(--flag-size)`, но в `home.css` переменная не объявлена. Если она не приходит из другого CSS, размеры изображения флага не применятся, хотя контейнер останется `88x88`.
6. `.center-score`: строка 1922 оставляет `width:100%`. При центральной колонке `auto` это обычно не ломает центр, но может раздувать центральный grid item в зависимости от содержимого.
7. `.score-input-row`: строка 1932 оставляет `width:100%`. Внутри `.center-score` это может растягивать строку степпера на всю ширину центрального блока.
8. `.score-stepper`: высота степпера фактически задаётся суммой `grid-template-rows: 26px 38px 26px` из строки 1943, то есть около 90px, тогда как `.center-score` зафиксирован на 88px. Это может давать визуальное несовпадение центра с флагами.

## Что нужно удалить

1. Дублирующие ранние размеры `.team-logo-v2` и вложенных `img/.flag-icon`, если проект уже окончательно использует WC-слой: строки 579-588, 590-614, 1460-1469, 1490-1500, 1523-1531.
2. Старую сетку `.teams-center`, если она больше не нужна как fallback: строки 528-546 и mobile строки 1449-1458.
3. Старую структуру `.team-v2` для незавершённых матчей: строки 558-568 и mobile строки 1476-1487, так как она задаёт grid-структуру, которую финальный WC-слой всё равно ломает через flex.
4. В финальном WC-слое желательно удалить или переопределить конфликтующие остатки из строки 1900: `justify-self: stretch`, `width: 100%`, возможно `gap: 5px`.
5. В финальном WC-слое желательно удалить или переопределить `padding: 4px 0 6px` у `.teams-center` из строки 1881, если нужна строгая геометрия без сдвигов.

## Что нужно оставить

1. Единый финальный источник правды для WC-карточки после строки 1606.
2. Для `.teams-center`: один поздний селектор с `display:grid`, `grid-template-columns`, `align-items:center`, `justify-items:center`, `margin:0 auto`, `padding:0`, `gap`.
3. Для `.team-v2`: один поздний селектор с `display:flex`, `flex-direction:column`, `align-items:center`, `justify-content:flex-start`, `justify-self:center`, `width:auto`, `gap:0` или осознанным отступом.
4. Для `.team-logo-v2`: один поздний селектор `width/height:88px`, `margin/padding:0`, flex-центрирование.
5. Для `.center-score`: один поздний селектор `height:88px`, `width:auto`, `margin:0`, flex-центрирование.
6. Для `.score-input-row`: один поздний селектор `width:auto`, `margin/padding:0`, `display:flex`, `align-items:center`, `justify-content:center`.
7. Для `.score-stepper`: согласовать высоту с флагом. Сейчас `26 + 38 + 26 = 90px`, а целевой контейнер 88px.
