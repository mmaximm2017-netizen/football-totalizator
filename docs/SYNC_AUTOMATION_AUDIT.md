# SYNC_AUTOMATION_AUDIT

## Что сейчас есть

Сейчас обновление матчей сосредоточено вокруг `app/services/match_service.py`.

Основные функции:

- `fetch_matches()` ходит в football-data API по `LEAGUE_IDS` из `app/config.py` и забирает матчи со статусами `SCHEDULED,TIMED,FINISHED,IN_PLAY,PAUSED,POSTPONED,CANCELLED`.
- `fetch_rpl_matches()` ходит в Understat через `understatapi`, выбирает сезон РПЛ через `resolve_rpl_season()` и маппит матчи в общий формат.
- `update_matches()` объединяет данные football-data и Understat, вставляет новые матчи в `matches`, обновляет существующие, считает `deadline`, выставляет `status`, `home_score`, `away_score`, `league`, `tournament_id`.
- `run_sync_with_lock()` оборачивает `update_matches()` в advisory lock и после синка вызывает `point_service.calculate_all_points()`.
- `update_matches_safe()` содержит старую защиту через `should_update()` и локальный in-memory cache, но сейчас не используется ни admin-кнопкой, ни скриптом.

На старте приложения тяжёлый синк отключён. В `app/__init__.py` внутри `create_app()` выполняется `init_db()`, но рядом явно зафиксировано правило: не запускать match/points sync в web startup, потому что `create_app()` может выполняться несколькими worker-ами.

Пересчёт очков уже вынесен в `app/services/scoring_recalculation_service.py`:

- `recalc_match_points(match_id, tournament_id=None)` пересчитывает прогнозы одного матча.
- `recalc_tournament_points(tournament_id)` пересчитывает завершённые матчи с прогнозами выбранного турнира.
- `recalc_all_points()` пересчитывает все прогнозы всех завершённых матчей.

`app/services/point_service.py` остался совместимым слоем:

- `calculate_points_for_match(match_id)` делегирует в `recalc_match_points()`.
- `calculate_all_points()` делегирует в `recalc_all_points()`.

## Что запускается вручную

Admin-кнопки:

- `templates/admin_matches.html` содержит кнопку `Обновить матчи из API`, которая отправляет `POST` на `admin.admin` с `action=update_matches`.
- В `app/routes/admin.py` ветка `action == 'update_matches'` вызывает `run_sync_with_lock()`.
- Там же есть кнопка `/admin/recalc_all`, которая вызывает `recalc_tournament_points(get_active_tournament_id())`.
- Формы внесения результата в `templates/admin_matches.html` вызывают `action=set_result` или `/admin/fix_result`.
- Есть debug-route `/admin/debug_match`, который вручную пересчитывает один матч и показывает прогнозы.

Scripts:

- `scripts/sync_once.py` создаёт Flask app, входит в app context и вызывает `run_sync_with_lock()`.
- README описывает ручную команду:

```bash
python scripts/sync_once.py
```

Manual commands:

- Единственная явно задокументированная команда для синка сейчас `python scripts/sync_once.py`.
- README прямо говорит, что команду можно запускать вручную или позже повесить на Render Cron, но cron сейчас не настроен.

App startup:

- Автоматического синка на старте нет.
- `create_app()` делает только `init_db()` и регистрацию blueprint-ов.
- Комментарий в `app/__init__.py` запрещает heavy data sync в startup.

## Что можно автоматизировать безопасно

Безопаснее всего автоматизировать уже существующий путь `run_sync_with_lock()` через отдельный script/worker, не меняя web startup и не встраивая синк в request lifecycle.

Можно автоматизировать:

- Запуск `scripts/sync_once.py` или нового отдельного worker-script, который использует тот же app context.
- Синк external API -> `matches` через существующий `update_matches()`.
- Пересчёт очков после синка через уже существующий `point_service.calculate_all_points()` или, следующим улучшением, через более узкий пересчёт только матчей, которые стали `FINISHED`.
- Логирование старта, завершения, ошибки, факта lock skip.
- Summary результата в stdout/logs: started_at, finished_at, lock_acquired, sync_completed, matches_seen, matches_inserted, matches_updated, finished_changed, points_updated, errors.

Что уже помогает автоматизации:

- Есть `pg_try_advisory_lock`, значит базовая защита от параллельного запуска уже заложена.
- `scripts/sync_once.py` уже возвращает `sys.exit(1)` при исключении, что пригодно для cron/worker monitoring.
- football-data ошибки логируются хотя бы как `logger.error("API error: ...")`.
- Understat имеет retry до трёх попыток и пишет warning/error.
- Пересчёт очков имеет единый сервис и может работать внутри переданного `conn/cur`, сохраняя транзакционность в admin-flow.

## Какие риски

Риск двойного запуска синка:

- `run_sync_with_lock()` использует PostgreSQL advisory lock, но если lock недоступен, `try_acquire_sync_lock()` логирует warning и возвращает `acquired=True`. То есть при проблеме с lock-механизмом синк продолжит работу без защиты.
- Admin-кнопка и `scripts/sync_once.py` используют один и тот же lock, это хорошо. Но если в будущем добавить cron без учёта этого пути, можно получить параллельные запуски.
- `update_matches_safe()` имеет отдельный in-memory throttle, но он не общий между процессами и сейчас не используется основным ручным путём.

Риск пересчёта не тех матчей:

- После любого `run_sync_with_lock()` вызывается `point_service.calculate_all_points()`, то есть `recalc_all_points()` для всех завершённых матчей, а не только для матчей, которые были изменены синком.
- Admin `/admin/recalc_all` пересчитывает только `get_active_tournament_id()`. Если активный турнир не совпадает с турниром матча или пользователь смотрит другой tournament context, можно пересчитать не тот набор прогнозов.
- `set_result` вызывает `recalc_match_points(match_id)` без `tournament_id`, а `force_finish` и `fix_result` передают `tournament_id=get_active_tournament_id()`. Эти пути отличаются по области пересчёта.
- `update_matches()` сам не возвращает список изменённых матчей, поэтому downstream-код не знает, какие матчи реально стали завершёнными.

Риск падения при ошибке API:

- football-data ошибки ловятся внутри `fetch_matches()` по каждой лиге и логируются, после чего функция возвращает частичный или пустой список.
- Understat ошибки ретраятся и логируются, но в `update_matches()` вызов `fetch_rpl_matches()` дополнительно обёрнут в голый `except: pass`. Это может скрыть неожиданную ошибку маппинга или клиента.
- Если оба источника вернули пусто, `update_matches()` просто делает `return` без явного summary. Для ручного запуска это может выглядеть как успешный sync, хотя данные не обновились.
- Если `update_matches()` успешно закоммитил матчи, а затем `calculate_all_points()` упал, матчевые данные уже останутся обновлёнными, а очки могут стать устаревшими. Сейчас это разные транзакционные этапы.

Риск тихой ошибки без логов:

- `should_update()` содержит `except: pass`.
- `update_matches()` содержит `except: pass` вокруг `fetch_rpl_matches()`.
- `app/__init__.py` в `/health/db` ловит общий `Exception` и делает `pass`, из-за чего диагностическая причина теряется.
- Admin routes часто показывают общий `flash(f"Ошибка: {e}")`, но не всегда пишут структурный серверный лог.
- `run_sync_with_lock()` возвращает только `True/False`, где `False` означает lock skip, но нет детального результата синка.

Риск данных матчей:

- Для уже завершённых матчей с известным счётом `update_matches()` делает `continue` и не перезаписывает их. Это защищает ручные исправления, но также означает, что официальный API уже не исправит такой матч автоматически.
- Для новых/незавершённых матчей sync обновляет `kickoff_time`, `deadline`, `league`, `tournament_id`. Ошибка маппинга турнира может перенести матч в неверный турнир.
- `get_tournament_id_by_name()` использует fallback-и по имени/legacy mojibake. Это важно для совместимости, но для автоматизации лучше иметь явный summary, если tournament_id не найден.

## Предлагаемая архитектура

Автоматизацию стоит строить отдельным worker/script слоем, не добавляя работу в Flask startup и не переписывая `match_service`.

Предлагаемый поток:

1. `scripts/sync_worker.py` или развитие `scripts/sync_once.py`.
2. Worker создаёт Flask app и app context.
3. Worker пытается взять PostgreSQL advisory lock.
4. Если lock не взят, worker пишет понятный summary `skipped: already_running` и завершается с кодом 0.
5. Если lock-механизм недоступен, лучше завершаться с ошибкой, а не продолжать без lock. Это изменение стоит делать отдельным безопасным шагом.
6. Worker запускает sync матчей.
7. Sync возвращает summary изменённых матчей: inserted, updated, became_finished, skipped_completed, api_errors.
8. После успешного обновления матчей worker запускает пересчёт очков только для матчей, которые стали `FINISHED` или у которых изменился счёт.
9. Если пока нет списка изменённых матчей, временно можно оставить `recalc_all_points()`, но логировать это как broad recalculation.
10. Worker пишет финальный summary в stdout и server logs.
11. Внешний scheduler/cron запускает только worker, а не web endpoint.

Lock:

- Использовать один lock key для всех источников запуска: admin, manual script, future cron.
- Не иметь fallback "continue without lock" для автоматического worker.
- Логировать `lock_acquired`, `lock_skipped`, `lock_error`.

Логирование:

- Структурные сообщения на start/end/error.
- Отдельно логировать API-source summary: football-data fetched count, Understat fetched count, errors.
- Отдельно логировать DB summary: inserted, updated, completed_locked, became_finished.
- Отдельно логировать scoring summary: matches_recalculated, predictions_updated.

Summary результата:

Минимально полезный формат:

```text
sync started
lock acquired
football_data matches=...
understat matches=...
matches inserted=... updated=... became_finished=...
points recalculated matches=... predictions=...
sync done
```

Для дальнейшей эксплуатации лучше возвращать dict из orchestration-функции, а script печатает его как JSON или человекочитаемый summary.

Пересчёт очков:

- Целевой безопасный вариант: пересчитывать только матчи, где status перешёл в `FINISHED` или изменился `home_score/away_score`.
- До появления такого списка допустим временный вариант: после worker sync вызывать `recalc_all_points()` и явно логировать, что пересчёт широкий.
- Не добавлять новые циклы расчёта очков в routes/scripts напрямую. Использовать только `scoring_recalculation_service.py` или совместимый `point_service.py`.

## План внедрения по шагам

1. Зафиксировать текущий аудит и не менять runtime-поведение.
2. Добавить sync-summary в `match_service.update_matches()` без изменения бизнес-логики: сколько матчей пришло, сколько вставлено, сколько обновлено, сколько пропущено как locked completed.
3. Сделать отдельную orchestration-функцию для worker, которая вызывает существующий sync и scoring, но возвращает summary.
4. Ужесточить lock-поведение для worker: если lock недоступен технически, завершать worker ошибкой, а не продолжать без lock.
5. Добавить явные логи вместо `except: pass` в sync-пути, не меняя обработку данных.
6. Добавить список матчей, у которых результат стал завершённым или изменился.
7. Перевести worker с `recalc_all_points()` на пересчёт только изменённых завершённых матчей.
8. Оставить admin-кнопку как ручной fallback, но чтобы она использовала тот же orchestration-layer и показывала summary.
9. После ручной проверки worker на production-like окружении подключать внешний scheduler.
10. Добавить smoke-check после синка: есть активный турнир, есть матчи, таблица строится, scoring не упал.

## Что НЕ трогать пока

- Не добавлять cron на этом шаге.
- Не запускать sync в `create_app()` или `wsgi.py`.
- Не переписывать `match_service.py` на fetcher/mapper/persistence слои в рамках первого шага.
- Не трогать UI и шаблоны admin/main/table.
- Не менять правила scoring в `app/models/scoring.py`.
- Не менять tournament selection rules.
- Не менять deadline policy.
- Не удалять legacy fallback-и mojibake/tournament names до отдельной миграционной задачи.
- Не заменять `recalc_all_points()` на узкий пересчёт, пока `update_matches()` не умеет достоверно возвращать список изменённых завершённых матчей.
