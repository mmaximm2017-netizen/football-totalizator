# TOURNAMENT_SWITCH_AUDIT

## Как сейчас работает выбор турнира

Сейчас выбранный турнир не является одним серверным состоянием. На разных страницах `tournament_id` определяется похожими, но не одинаковыми правилами, а затем клиентский `localStorage` пытается восстановить последний выбор через `?tid=`.

Главная (`app/routes/main.py`) читает `tid` из query string:

- `tid = request.args.get('tid', type=int)`
- затем `get_requested_or_current_tournament_id(tid)`
- если `tid` не задан, fallback идет через `get_current_tournament_id()`
- `get_current_tournament_id()` выбирает:
  1. турнир с ближайшим будущим матчем
  2. первый активный турнир по `id`
  3. runtime active tournament из `tournament_service`

Главная использует этот `tid` для:

- загрузки матчей: `tournament_id = tid OR tournament_id IS NULL`
- загрузки прогнозов пользователя: `p.tournament_id = tid`
- сохранения прогноза: берется `match.tournament_id`, а если его нет, текущий `tid`
- передачи в шаблон `current_tournament_id` и `current_tournament_name`

Таблица (`app/routes/table.py`) тоже сначала читает `tid` из URL:

- `tid = request.args.get('tid', type=int)`
- если `tid` не задан, вызывает `get_table_tournament_id(tid)`
- fallback таблицы:
  1. турнир с ближайшим будущим матчем
  2. первый активный турнир по `id`
  3. последний турнир по `start_date DESC, id DESC`

Таблица использует `tid` для:

- `get_tournament_ranking(tid)`
- подсветки выбранного турнира в bottom sheet
- ссылок на профиль из таблицы: `/profile?username=...&tid={{ selected_tid }}`
- AJAX-переключения таблицы через `/table?tid=...&ajax=1`

Профиль (`app/routes/profile.py`) читает:

- `tournament_id = request.args.get('tid', type=int)`
- затем `get_profile_tournament_id(tournament_id, active_tournaments)`
- если `tid` не задан, профиль берет первый активный турнир из `get_all_tournaments()`
- если активных нет, fallback на `get_active_tournament_id()`

Профиль использует выбранный турнир для:

- места пользователя в рейтинге: `get_tournament_ranking(tournament_id)`
- `current_tournament_id/current_tournament_name` в `base.html`
- ссылки назад на главную с `?tid=`

Важно: статистика профиля и последние матчи сейчас не фильтруются по `p.tournament_id`. Текст говорит про текущий турнир, но SQL считает все завершенные прогнозы пользователя.

Мои прогнозы (`app/routes/predictions.py`, `/my-predictions`) не читают `tid` из URL вообще:

- всегда берет `tournament_id = get_active_context_tournament_id()`
- `get_active_context_tournament_id()` использует `get_active_tournament()`
- `localStorage` и query params на этой странице не участвуют

Публичная страница прогнозов матча (`/match/<id>/predictions`) работает от самого матча:

- берет `match.tournament_id`
- если у матча `tournament_id IS NULL`, fallback на `get_active_context_tournament_id()`
- `tid` из URL не используется

В `templates/base.html` есть общий переключатель турнира:

- header button `.tournament-trigger`
- bottom sheet `.tournament-sheet`
- ссылки `.tournament-item` вида `{{ tournament_switch_base }}?tid={{ t.id }}`
- bottom nav ссылки на главную, таблицу, профиль тоже получают `?tid={{ current_tid }}`

В `base.html` также есть клиентская память выбора:

- `localStorage.selected_tid`
- `localStorage.selected_tournament_name`
- функция `persistTournamentSelection()`
- работает только на путях `/`, `/table`, `/profile`
- если в URL нет `tid`, но в `localStorage` есть `selected_tid`, делает `window.location.replace(path + '?tid=' + savedTid)`
- если в URL есть `tid`, записывает его в `localStorage`

На странице таблицы есть дополнительная AJAX-логика в `templates/table.html`:

- перехватывает клики по `[data-ajax-table]`, только если href содержит `/table?tid=`
- грузит `/table?tid=<tid>&ajax=1`
- обновляет HTML таблицы, body class, заголовок, trigger text
- пишет `selected_tid` и `selected_tournament_name` в `localStorage`
- делает `history.pushState(..., '/table?tid=<tid>')`

## Где расходится логика

Главная и таблица используют разные fallback-функции:

- главная: `get_requested_or_current_tournament_id()` -> `get_current_tournament_id()`
- таблица: `get_table_tournament_id()`
- профиль: `get_profile_tournament_id()`
- мои прогнозы: `get_active_context_tournament_id()`

Главная и таблица совпадают только если `tid` явно есть в URL или если первые fallback-и дают один и тот же турнир. При отсутствии `tid` они могут разойтись из-за последнего fallback-а:

- главная в конце берет runtime active tournament
- таблица в конце берет latest tournament
- профиль берет первый активный из списка, что зависит от порядка `get_all_tournaments()`
- мои прогнозы вообще не участвуют в выбранном `tid`

`localStorage` работает поверх серверной логики, но не является серверным источником истины:

- сервер сначала рендерит страницу со своим fallback `tid`
- затем браузер может сразу заменить URL на сохраненный `tid`
- пользователь видит состояние, которое может мигнуть или поменяться после client-side redirect
- при отключенном JS, первом заходе, чистом `localStorage` или прямой ссылке без `tid` каждая страница снова живет по своему fallback

AJAX-переключение таблицы работает только на `/table`. На главной и профиле ссылка из bottom sheet делает обычную навигацию. Это нормально технически, но усиливает ощущение разных механизмов: таблица меняется на месте, а главная перезагружается.

Есть отдельный риск в ссылках на прогнозы матча:

- ссылки `/match/<id>/predictions` не несут `tid`
- страница определяет турнир по `match.tournament_id`
- для матчей без `tournament_id` fallback идет в active context, а не в выбранный пользователем турнир

## Почему пользователь путается

Пользователь воспринимает переключатель в header как глобальный выбор турнира. Но фактически состояние распределено между:

- query string `?tid=`
- backend fallback-логикой конкретной страницы
- `localStorage.selected_tid`
- текущим `current_tournament_id`, который передан конкретным route
- AJAX-state таблицы после `history.pushState`

Из-за этого возможны такие сценарии:

- пользователь выбрал турнир на таблице, таблица обновилась AJAX-ом, но главная будет совпадать только если `?tid=` попал в URL/localStorage и `base.html` успел восстановить его
- пользователь открывает главную без `?tid=`: backend выбирает ближайший upcoming tournament, а затем JS может заменить его на сохраненный `selected_tid`
- пользователь открывает таблицу без `?tid=`: backend может выбрать latest tournament, если нет current/active
- профиль без `?tid=` может показать первый active tournament, даже если главная/таблица выбрали другой fallback
- `/my-predictions` показывает active context, а не выбранный в UI турнир
- текст профиля говорит про текущий турнир, но часть статистики агрегируется по всем турнирам

Итог: визуально есть один переключатель, но модель данных говорит, что страниц несколько, и каждая помнит турнир по-своему.

## Что должно быть

Нужен один selected tournament state для пользовательских страниц:

- первичный источник для текущего request: `?tid=`
- если `?tid=` задан и валиден, все страницы используют именно его
- `localStorage` только хранит последний выбранный `tid` и помогает добавить `?tid=` при заходе без query param
- fallback применяется только если `tid` не задан вообще
- fallback должен быть одинаковым для главной, таблицы, профиля и моих прогнозов
- все навигационные ссылки между страницами должны сохранять выбранный `tid`
- страницы, которые показывают данные турнира, должны фильтровать данные по тому же `tid`

Рекомендуемый порядок выбора:

1. `request.args["tid"]`, если задан и существует
2. fallback selected tournament, общий для продукта
3. только после этого historical fallback: ближайший будущий матч / active / latest, но в одном месте и одинаково для всех страниц

`localStorage` не должен быть отдельной логикой выбора на сервере. Его роль: память последнего выбора на клиенте и синхронизация URL, чтобы сервер всегда получил явный `tid`.

## Safe fix plan

1. Ввести/расширить единый helper в `tournament_context_service`, например `get_selected_tournament_id(requested_id, mode='default')`.

2. Сделать общую fallback-цепочку для пользовательских страниц. Для главной и таблицы особенно важно убрать разные конечные fallback-и или явно задокументировать один общий порядок.

3. На главной, таблице, профиле и `/my-predictions` читать `tid` одинаково:

- взять `request.args.get('tid', type=int)`
- передать в общий helper
- использовать полученный `selected_tid` для запросов и шаблонов

4. Для `/my-predictions` добавить поддержку `?tid=` и передавать `current_tournament_id/current_tournament_name` в шаблон, если эта страница должна жить в общем shell.

5. Проверить профиль:

- ranking уже считается по выбранному турниру
- stats/recent сейчас не фильтруются по `p.tournament_id`
- если продукт ожидает статистику текущего турнира, добавить `AND p.tournament_id = %s`

6. Сохранить URL sync:

- при клике в bottom sheet все страницы должны переходить на `?tid=<id>`
- bottom nav уже сохраняет `current_tid`; проверить после унификации, что он всегда равен selected state
- `localStorage` продолжает хранить последний `selected_tid`

7. Сделать fallback только при отсутствии `tid`.

8. Добавить минимальные smoke checks:

- открыть `/` без `tid` с пустым `localStorage`
- выбрать турнир на главной
- перейти в `/table`
- перейти в `/profile`
- вернуться на `/`
- открыть `/table` напрямую без `tid` после сохраненного `localStorage`
- открыть `/my-predictions?tid=<id>`

## First recommended fix

Первый безопасный фикс: унифицировать источник выбранного турнира на сервере, не меняя UI.

Практически:

- сделать один helper в `tournament_context_service`, который принимает `requested_id`
- использовать его в `main.py`, `table.py`, `profile.py` и `predictions.py`
- считать `?tid=` главным источником
- если `?tid=` отсутствует, применять единый fallback
- оставить `localStorage` в `base.html` как клиентскую память, которая просто добавляет `?tid=` к URL

Это минимально рискованно, потому что текущий UI уже построен вокруг `?tid=`, bottom nav уже прокидывает `current_tournament_id`, а таблица уже умеет синхронизировать URL через `history.pushState`. Главная причина расхождения сейчас не в визуальном переключателе, а в том, что backend routes не используют один и тот же selected tournament contract.

## Implemented first safe fix

Первый safe fix реализован: добавлен единый helper `get_selected_tournament_id(requested_tid)` в `app/services/tournament_context_service.py`.

Новый контракт:

- `?tid=` является главным selected tournament state для server-side request
- если `tid` задан и существует в `tournaments`, возвращается именно он
- если `tid` не задан или невалиден, используется единый fallback
- fallback теперь одинаковый для главной, таблицы, профиля и `/my-predictions`

Единый fallback order:

1. турнир с ближайшим будущим матчем
2. первый active tournament по `id`
3. latest tournament по `start_date DESC, id DESC`

Переведены routes:

- `app/routes/main.py`
- `app/routes/table.py`
- `app/routes/profile.py`
- `app/routes/predictions.py`

Для `/my-predictions` добавлена поддержка `?tid=`. Страница использует selected tournament id для всех списков прогнозов и получает `current_tournament_id/current_tournament_name` в template context. UI, HTML, CSS, JS, bottom sheet, bottom nav, `localStorage` и AJAX-переключение таблицы не менялись.

## Remaining future improvements

Остались будущие улучшения, которые намеренно не входили в первый safe fix:

- профиль всё ещё считает часть статистики и recent matches без фильтра `p.tournament_id`; это отдельное продуктовое решение, потому что может изменить цифры в профиле
- `/match/<id>/predictions` по-прежнему в первую очередь доверяет `match.tournament_id`; общий selected state используется только как fallback для матчей без `tournament_id`
- `localStorage` всё ещё синхронизирует URL только на `/`, `/table`, `/profile`; `/my-predictions` теперь понимает `?tid=`, но клиентский restore туда не добавлялся, чтобы не менять JS behavior
- старые helper-ы в `tournament_context_service.py` оставлены для совместимости и могут быть удалены отдельной cleanup-задачей после проверки всех вызовов
