# SYNC_PIPELINE

## Как сейчас работает sync pipeline

Sync запускается вручную из двух мест:

- admin action `update_matches` в `app/routes/admin.py`;
- command `python scripts/sync_once.py`.

Оба пути вызывают одну orchestration-функцию:

`app/services/match_service.py::run_sync_with_lock()`

Pipeline сейчас такой:

1. `run_sync_with_lock()` пишет `sync start`.
2. Пытается взять PostgreSQL advisory lock через `pg_try_advisory_lock`.
3. Если lock уже занят, возвращает summary со `status: "skipped"`.
4. Если lock взят, вызывает `update_matches()`.
5. `update_matches()` забирает матчи из football-data и Understat.
6. `update_matches()` вставляет новые матчи и обновляет существующие по старой логике.
7. После sync запускается broad scoring recalculation через `point_service.calculate_all_points()`.
8. `run_sync_with_lock()` пишет scoring summary и финальный `sync end`.

Sync по-прежнему не запускается в `create_app()` и не привязан к web startup.

## Что возвращает summary

`update_matches()` возвращает dict:

```python
{
    "football_data_matches": 0,
    "understat_matches": 0,
    "matches_inserted": 0,
    "matches_updated": 0,
    "matches_skipped_finished": 0,
    "matches_became_finished": [],
    "errors": [],
}
```

`run_sync_with_lock()` возвращает orchestration summary:

```python
{
    "status": "completed",
    "lock_acquired": True,
    "lock_error": None,
    "sync": {...},
    "scoring": {
        "matches_recalculated": 0,
        "predictions_recalculated": 0,
    },
    "errors": [],
}
```

Возможные `status`:

- `completed` - sync и broad scoring завершились.
- `skipped` - lock не был взят, потому что sync уже выполняется.
- `error` - sync упал; ошибка логируется и пробрасывается выше.

`matches_became_finished` пока содержит id матчей, которые при sync перешли в `FINISHED`. На этом шаге это только observability: scoring всё ещё пересчитывается широко через `recalc_all_points()`.

## Как работает lock

Lock находится в `app/services/match_service.py`:

- `SYNC_LOCK_KEY = 88422031`;
- `try_acquire_sync_lock()` вызывает `pg_try_advisory_lock`;
- `release_sync_lock()` вызывает `pg_advisory_unlock`.

Admin action и `scripts/sync_once.py` используют один и тот же lock, потому что оба идут через `run_sync_with_lock()`.

Текущее поведение при технической ошибке lock не изменено: система логирует warning и продолжает sync без lock. Это оставлено для совместимости текущего поведения. Более строгий режим для будущего worker нужно делать отдельным шагом.

## Где логируются ошибки

В sync-path теперь логируются:

- non-200 ответы football-data;
- исключения football-data API;
- ошибки проверки `should_update()`;
- исключения Understat внутри `update_matches()`;
- отсутствие матчей от внешних источников;
- lock acquired/skipped/unavailable;
- start/end/error `run_sync_with_lock()`;
- scoring summary после broad recalculation.

Understat retry-логика внутри `fetch_rpl_matches()` осталась прежней: attempts пишутся warning-ами, финальный провал пишется error.

## Что ещё НЕ автоматизировано

Пока не автоматизировано:

- cron/scheduler;
- отдельный deployed worker;
- запуск sync в startup;
- узкий пересчёт только изменённых завершённых матчей;
- alerting;
- сохранение sync history в БД;
- health endpoint со статусом последнего sync;
- отдельные structured metrics;
- строгий fail-fast при недоступном lock.

На этом шаге scoring rules, tournament logic, deadline logic, UI и scheduler не менялись.
