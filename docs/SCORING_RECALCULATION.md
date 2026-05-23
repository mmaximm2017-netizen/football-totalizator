# SCORING_RECALCULATION

## Где теперь пересчитываются очки

Единая точка пересчёта очков находится в:

`app/services/scoring_recalculation_service.py`

Сервис использует существующую функцию:

`app.models.scoring.calculate_points`

Правила начисления очков в этой задаче не менялись.

## Какие функции использовать

- `recalc_match_points(match_id, tournament_id=None)` - пересчитать прогнозы одного матча.
- `recalc_tournament_points(tournament_id)` - пересчитать завершённые матчи, у которых есть прогнозы выбранного турнира.
- `recalc_all_points()` - пересчитать все прогнозы всех завершённых матчей.

В admin-обработчиках можно передавать текущие `conn` и `cur`:

`recalc_match_points(match_id, tournament_id=tournament_id, conn=conn, cur=cur)`

Это сохраняет прежнюю транзакционность: изменение результата матча и пересчёт очков коммитятся или откатываются вместе.

## Чего больше нельзя делать напрямую в routes

- Не импортировать `calculate_points` в routes для массового пересчёта.
- Не писать циклы `SELECT predictions -> calculate_points -> UPDATE predictions.points` в routes.
- Не обнулять и пересчитывать `predictions.points` вручную в admin-ветках.
- Не добавлять новые варианты пересчёта без функции в `scoring_recalculation_service.py`.

## Что осталось на потом

- Категории очков пока не унифицированы:
  - `app/services/ranking_service.py` считает `exact_diffs` как `points BETWEEN 7 AND 8`.
  - `app/routes/profile.py` считает похожий показатель как `points BETWEEN 7 AND 9`.
- Это не менялось сейчас, потому что правка может изменить видимую статистику профиля или таблицы.
- Следующий безопасный шаг: отдельно описать продуктовые категории очков и только потом вынести их в общий `scoring_categories.py`.

## Совместимость

`app/services/point_service.py` оставлен как совместимый слой и делегирует в новый сервис.
