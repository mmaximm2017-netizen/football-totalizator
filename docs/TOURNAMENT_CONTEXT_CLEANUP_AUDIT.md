# TOURNAMENT_CONTEXT_CLEANUP_AUDIT

## Коротко

Unified selected tournament logic уже внедрена в основные пользовательские маршруты. Сейчас `get_selected_tournament_id(requested_tid)` является фактическим контрактом для `home`, `table`, `profile`, `/my-predictions` и fallback-а страницы `/match/<id>/predictions` для матчей без `tournament_id`.

Legacy helpers в `app/services/tournament_context_service.py` сейчас не используются в Python routes напрямую. Они остаются в файле как совместимость и как потенциальная ловушка для будущих изменений: их имена выглядят полезными и route-specific, но fallback logic у них отличается от unified selected tournament contract.

Рекомендация: оставить legacy helpers временно, но считать их deprecated. Удалять сейчас не стоит без тестов и отдельной cleanup-задачи.

## Фактическое использование

### Unified helper

`get_selected_tournament_id(requested_tid)`

- `app/routes/main.py`: основной выбор `tid` для главной, загрузки матчей, user predictions и template context.
- `app/routes/table.py`: основной выбор `tid` для таблицы, AJAX-ответа, selected tournament metadata и ranking.
- `app/routes/profile.py`: основной выбор `tournament_id` для ranking, stats, recent predictions и template context.
- `app/routes/predictions.py`: основной выбор `tournament_id` для `/my-predictions`; fallback для `/match/<id>/predictions`, если у матча `tournament_id IS NULL`.

### State flags helper

`get_tournament_state_flags(tournaments)`

- `app/routes/main.py`: передаёт `has_any_tournament`, `has_active_tournament`, `is_offseason` на главную.
- `app/routes/table.py`: передаёт те же flags в table render и AJAX partial.
- `app/routes/profile.py`: передаёт flags в профиль.
- `app/routes/predictions.py`: передаёт flags в `/my-predictions`.

### Legacy helpers

По текущему поиску в `app/` эти helpers не импортируются route-ами:

- `get_requested_or_current_tournament_id`
- `get_table_tournament_id`
- `get_profile_tournament_id`
- `get_active_context_tournament_id`

Они упоминаются в документах как старая модель и как оставленный technical debt.

## Helper audit

### `get_nearest_upcoming_tournament_id()`

Purpose: выбрать турнир с ближайшим будущим матчем. Это первый fallback unified helper-а и исторический дефолт главной/таблицы.

Current usage: вызывается внутри `get_current_tournament_id()`, `get_selected_tournament_id()` и `get_table_tournament_id()`.

Risk: SQL смотрит только на `matches.kickoff_time >= NOW()` и `tournament_id IS NOT NULL`. Если ближайшие будущие матчи есть только в архивном или неожиданном турнире, он станет fallback-выбором. Это продуктово допустимо только пока "ближайший матч" считается главным default.

Можно ли удалить: нет.

Что сломается при удалении: `get_selected_tournament_id()` потеряет первый fallback; главная, таблица, профиль и `/my-predictions` начнут выбирать first active/latest иначе. `get_current_tournament_id()` и legacy table helper тоже упадут.

### `get_first_active_tournament_id()`

Purpose: выбрать первый active tournament по `id`.

Current usage: вызывается внутри `get_current_tournament_id()`, `get_selected_tournament_id()` и `get_table_tournament_id()`.

Risk: при нескольких active tournaments выбор идёт по минимальному `id`, а не по start date, current status или admin intention. Это может расходиться с ожиданием, если активных турниров одновременно больше одного.

Можно ли удалить: нет.

Что сломается при удалении: unified fallback потеряет active stage между upcoming и latest; без будущих матчей пользовательские страницы могут сразу падать в latest archive.

### `get_latest_tournament_id()`

Purpose: выбрать последний турнир по `start_date DESC, id DESC`.

Current usage: вызывается внутри `get_selected_tournament_id()` и `get_table_tournament_id()`.

Risk: в offseason это полезный archive fallback, но для активного продукта может тихо показать архив, если нет upcoming и active. Особенно чувствительно для invalid `tid`: пользователь получает другой турнир без явного сообщения.

Можно ли удалить: нет.

Что сломается при удалении: offseason/archive режим потеряет graceful fallback. `/table`, `/profile`, `/my-predictions` чаще будут уходить в empty/redirect при отсутствии active tournaments.

### `get_current_tournament_id()`

Purpose: старый central current-tournament choice: nearest upcoming -> first active -> runtime active tournament из `tournament_service`.

Current usage: вызывается только внутри `get_current_tournament()` и legacy `get_requested_or_current_tournament_id()`. По текущему поиску user-facing routes его напрямую не используют.

Risk: fallback отличается от unified helper: финальный шаг здесь `get_active_tournament_id()`, а не latest tournament. Если будущий route возьмёт этот helper, он может разойтись с home/table/profile в offseason и archive scenarios.

Можно ли удалить: пока нет, только после проверки всех imports за пределами текущих файлов и тестов.

Что сломается при удалении: `get_current_tournament()` и `get_requested_or_current_tournament_id()` упадут. Если есть внешние или будущие вызовы, они потеряют historical current behavior.

### `get_current_tournament()`

Purpose: вернуть объект турнира для `get_current_tournament_id()`.

Current usage: по текущему поиску в `app/` прямых вызовов нет, кроме определения.

Risk: выглядит как удобный generic helper, но фактически несёт old current fallback, не selected tournament contract.

Можно ли удалить: потенциально да, но не сейчас.

Что сломается при удалении: если скрытые/будущие вызовы используют его как "текущий турнир", они упадут. Перед удалением нужен repo-wide search и тесты.

### `get_selected_tournament_id(requested_tid)`

Purpose: unified selected tournament state для пользовательских страниц. `?tid=` является primary state; если он отсутствует или невалиден, fallback: nearest upcoming -> first active -> latest.

Current usage: `main.py`, `table.py`, `profile.py`, `predictions.py`.

Risk: invalid `tid` silently falls back. Это хорошо для работоспособности, но может скрыть битую ссылку или удалённый турнир. Второй риск: helper каждый раз проверяет `get_tournament_by_id(requested_tid)`, поэтому массовое использование может добавлять DB roundtrip.

Можно ли удалить: нет.

Что сломается при удалении: основной selected tournament contract для пользовательских страниц.

### `get_tournament_state_flags(tournaments)`

Purpose: вычислить `has_any_tournament`, `has_active_tournament`, `is_offseason` из списка tournaments.

Current usage: `main.py`, `table.py`, `profile.py`, `predictions.py`.

Risk: helper зависит от того, что route передал полный список tournaments. Если передать только active tournaments, `is_offseason` станет неверным.

Можно ли удалить: нет.

Что сломается при удалении: offseason/empty-state context в основных пользовательских templates.

### `get_requested_or_current_tournament_id(requested_id)`

Purpose: legacy helper главной: использовать requested id или old current fallback.

Current usage: прямых Python usage в `app/` сейчас нет.

Risk: не валидирует requested id. Если `requested_id` задан, helper вернёт его даже при отсутствии такого турнира. Fallback отличается от unified helper: final fallback идёт через `get_current_tournament_id()`, где последний шаг runtime active, а не latest.

Можно ли удалить: не сейчас; можно deprecated.

Что сломается при удалении: текущие routes, прочитанные в аудите, не сломаются. Потенциально сломаются скрытые будущие imports или внешняя документация/скрипты, если они есть.

### `get_table_tournament_id(requested_id)`

Purpose: legacy helper таблицы: requested id -> nearest upcoming -> first active -> latest.

Current usage: прямых Python usage в `app/` сейчас нет.

Risk: почти совпадает с unified fallback, но не валидирует requested id. Поэтому `?tid=999` через этот helper пошёл бы в ranking несуществующего турнира, а не в safe fallback.

Можно ли удалить: не сейчас; можно deprecated.

Что сломается при удалении: текущий `table.py` не сломается. Но если где-то вне проверенных routes таблица всё ещё ожидает этот helper, будет import error.

### `get_profile_tournament_id(requested_id, active_tournaments=None)`

Purpose: legacy helper профиля: requested id -> первый active tournament из переданного списка -> `get_active_tournament_id()`.

Current usage: прямых Python usage в `app/` сейчас нет.

Risk: самый опасный legacy helper для future trap. Он может выбрать первый active из порядка `get_all_tournaments()`, что не равно unified fallback, и не поддерживает latest archive fallback. В offseason он чаще вернёт `None`, хотя unified helper показал бы latest tournament.

Можно ли удалить: не сейчас; можно deprecated с высоким приоритетом.

Что сломается при удалении: текущий `profile.py` не сломается. Потенциально сломаются старые profile-related imports, если они появятся или есть вне текущего поиска.

### `get_active_context_tournament_id()`

Purpose: legacy active/current context для страниц, которые раньше напрямую брали `get_active_tournament()`.

Current usage: прямых Python usage в `app/` сейчас нет.

Risk: игнорирует `?tid=` полностью и берёт active tournament из `tournament_service`. Для user-facing selected tournament это неправильный контракт. Особенно опасен для `/my-predictions` или match fallback, если его вернут случайно.

Можно ли удалить: не сейчас; можно deprecated.

Что сломается при удалении: текущие routes не сломаются. Потенциально сломаются старые active-context callers, если они есть вне текущего audited path.

## Потенциальные расхождения fallback logic

- `get_selected_tournament_id`: validates explicit `tid`; fallback nearest upcoming -> first active -> latest.
- `get_requested_or_current_tournament_id`: does not validate explicit id; fallback nearest upcoming -> first active -> runtime active.
- `get_table_tournament_id`: does not validate explicit id; fallback nearest upcoming -> first active -> latest.
- `get_profile_tournament_id`: does not validate explicit id; fallback first active from passed list -> runtime active.
- `get_active_context_tournament_id`: ignores explicit id; fallback only active tournament from `tournament_service`.

Главный риск не в текущем поведении, а в том, что имена legacy helpers выглядят route-specific и "правильными". Новый разработчик может увидеть `get_profile_tournament_id()` и использовать его в профиле или новой profile-like странице, вернув старый bug.

## Future traps

- Invalid `tid`: unified helper safely falls back, legacy helpers могут вернуть несуществующий id.
- Offseason: unified helper показывает latest tournament, `get_profile_tournament_id()` и `get_active_context_tournament_id()` могут вернуть `None`.
- Multiple active tournaments: `get_first_active_tournament_id()` берёт минимальный `id`; `tournament_service.get_active_tournament_id()` может иметь другой смысл active/current.
- New user-facing pages: любая новая страница с tournament data должна импортировать `get_selected_tournament_id`, а не route-specific legacy helper.
- Match predictions: `/match/<id>/predictions` правильно доверяет `match.tournament_id`, но fallback для `NULL` tournament id остаётся чувствительной зоной.
- Admin routes: админские routes сейчас могут использовать `get_active_tournament_id()` из `tournament_service` для своих рабочих действий. Это не обязательно ошибка, но нельзя автоматически переносить user-facing selected tournament contract на admin flows без отдельного решения.

## Routes, которые могут случайно использовать legacy helper

Наиболее вероятные зоны будущей ошибки:

- Новые страницы рядом с `/profile`: из-за существующего имени `get_profile_tournament_id`.
- Новые leaderboard/table variants: из-за имени `get_table_tournament_id`.
- Новые prediction/history pages: из-за имени `get_active_context_tournament_id`.
- Любая новая главная/feed-like страница: из-за имени `get_requested_or_current_tournament_id` или `get_current_tournament_id`.

Текущие проверенные routes (`main.py`, `table.py`, `profile.py`, `predictions.py`) уже используют unified helper там, где нужен selected tournament state.

## Recommendation

Оставить legacy helpers temporarily.

Причины:

- Сейчас задача cleanup-аудита, не refactor.
- Текущие routes не используют legacy helpers, поэтому срочного functional bug от них нет.
- Удаление без тестов может создать import errors, если есть внешние сценарии, старые scripts, будущие незамеченные references или документация, которая ещё не синхронизирована.
- Основной риск можно снизить документацией и тестами, не трогая код.

## Safe deprecation plan

1. Добавить tests для `get_selected_tournament_id`: valid `tid`, invalid `tid`, no `tid` with upcoming, no upcoming with active, no active with latest, no tournaments.
2. Добавить route smoke tests для `/`, `/table`, `/profile`, `/my-predictions` с одинаковым `?tid=`.
3. Проверить repo-wide search не только по `app/`, но и по scripts/tests/docs/deploy snippets.
4. После тестов пометить legacy helpers как deprecated в docstring или отдельном cleanup audit note.
5. Подождать один небольшой релиз/итерацию без новых usage.
6. Только затем отдельной задачей удалить legacy helpers, если search показывает ноль runtime usage.

Не рекомендуется сейчас переписывать систему выбора турнира. Достаточно закрепить правило: для user-facing selected tournament context использовать `get_selected_tournament_id(request.args.get('tid', type=int))`; для offseason flags использовать `get_tournament_state_flags(all_tournaments)`.
