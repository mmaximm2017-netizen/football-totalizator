# TOURNAMENT_CONTEXT

## Где выбирается турнир

Единая точка выбора текущего турнира теперь находится в:

`app/services/tournament_context_service.py`

Основные функции:

- `get_current_tournament_id()` - текущий турнир для главной и общего match-контекста.
- `get_current_tournament()` - объект текущего турнира.
- `get_requested_or_current_tournament_id(requested_id)` - использовать `tid` из запроса или текущий турнир.
- `get_table_tournament_id(requested_id)` - выбор для таблицы с прежним fallback на последний турнир.
- `get_profile_tournament_id(requested_id, active_tournaments)` - выбор для профиля с прежним приоритетом первого active-турнира.
- `get_active_context_tournament_id()` - active/current контекст для страниц, которые раньше напрямую брали `get_active_tournament_id()`.

## Текущие правила

Для главной сохранён старый порядок:

1. Турнир с ближайшим будущим матчем.
2. Первый active-турнир по `id`.
3. Runtime active-турнир из `tournament_service`.

Для таблицы сохранён старый порядок:

1. Турнир с ближайшим будущим матчем.
2. Первый active-турнир по `id`.
3. Если нет current/active турнира, берётся последний турнир из списка.

Для профиля сохранён старый порядок:

1. `tid` из URL.
2. Первый active-турнир из списка турниров.
3. Runtime active-турнир из `tournament_service`.

## Чего больше не делать в routes

- Не создавать локальные `get_default_tournament_id()` в маршрутах.
- Не писать SQL выбора default/current tournament прямо в route.
- Не выбирать active/default tournament вручную через разные fallback-цепочки.
- Не использовать `get_active_tournament_id()` напрямую в пользовательских routes, если нужен контекст текущего турнира.

Если нужен новый вариант выбора турнира, его нужно добавить в `tournament_context_service.py` и описать правило здесь.
