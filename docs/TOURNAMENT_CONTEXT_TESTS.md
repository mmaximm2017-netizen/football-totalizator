# TOURNAMENT_CONTEXT_TESTS

## Что добавлено

Добавлена минимальная тестовая структура `tests/` без изменения app factory и runtime behavior.

Тесты находятся в `tests/test_tournament_context.py` и используют стандартный `unittest`, потому что в текущем окружении нет установленного `pytest`. Это позволяет запускать стабилизационные проверки без новой зависимости и без перестройки тестового каркаса.

## Что покрыто

### `get_selected_tournament_id()`

Покрыты критические ветки unified selected tournament fallback:

- valid `tid` возвращается напрямую;
- invalid `tid` уходит в fallback;
- no `tid` + nearest upcoming;
- no upcoming + first active;
- no active + latest tournament;
- no tournaments -> `None`.

### `get_tournament_state_flags()`

Покрыты состояния:

- normal active season;
- offseason;
- no tournaments.

### Route smoke

Добавлены минимальные smoke checks для:

- `/?tid=X`;
- `/table?tid=X`;
- `/profile?tid=X`.

Route tests не проверяют HTML, CSS или JS. Они подменяют DB/render dependencies и проверяют только критичное: route не падает, передаёт `tid` в selected helper и возвращает selected tournament context в template context.

В текущем локальном Codex runtime Flask не установлен, поэтому route smoke tests автоматически пропускаются через `unittest.skipUnless`. В окружении с зависимостями из `requirements.txt` они должны выполняться как обычные unittest-тесты.

## Запуск

Локально выполнено:

- `python -m unittest discover -s tests`: 12 тестов найдено, 9 выполнено, 3 route smoke пропущены из-за отсутствия Flask.
- `python -m compileall app scripts`: успешно.
- `git diff --check`: успешно.

## Что остаётся без тестов

- Реальный HTML render и визуальные состояния.
- `localStorage` sync и table AJAX behavior в браузере.
- `/my-predictions` route smoke.
- `/match/<id>/predictions` fallback для матчей без `tournament_id`.
- Реальная PostgreSQL-интеграция и SQL совместимость.
- Admin tournament context behavior.

## Риски

- Route smoke tests используют fake DB cursor и patch `render_template`, поэтому они ловят contract regression, но не ловят ошибки реального template render.
- Тесты намеренно не создают полноценную test database, чтобы не менять инфраструктуру проекта.
- Если в проект позже добавят pytest, эти unittest-тесты всё равно должны запускаться pytest-ом, но сейчас базовый запуск идёт через `python -m unittest`.
