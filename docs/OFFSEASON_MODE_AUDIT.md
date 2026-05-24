# OFFSEASON_MODE_AUDIT

## Что будет сейчас без активных турниров

Если в базе больше нет турниров с `is_active = 1`, поведение не полностью ломается, потому что часть маршрутов уже использует fallback не только на active tournament.

`get_selected_tournament_id()` в `app/services/tournament_context_service.py` выбирает турнир в таком порядке:

1. Явный `?tid=`, если такой турнир существует.
2. Турнир с ближайшим будущим матчем.
3. Первый active tournament.
4. Последний турнир по `start_date DESC, id DESC`.

Из-за этого страницы, которые используют `get_selected_tournament_id()`, чаще всего продолжат открываться даже без active tournaments, если в таблице `tournaments` есть хотя бы один завершённый турнир.

Главная `/` в `app/routes/main.py` выберет latest tournament через `get_selected_tournament_id()`, но список `active_tournaments` будет пустым. Матчи будут загружаться по выбранному `tid`, а если будущих матчей нет, `grouped_months` и `days` станут пустыми. Это должно приводить к пустому состоянию в контенте главной страницы, но текст текущего partial `templates/partials/home/_empty_home.html` говорит “нет доступных матчей / скоро появятся новые игры”, а не “сезон завершён”.

`/table` в `app/routes/table.py` также выберет latest tournament через `get_selected_tournament_id()` и покажет таблицу по архивному турниру. Если турниров нет вообще, маршрут уже отдаёт пустую таблицу и `selected_tid=None`.

`/profile` в `app/routes/profile.py` выберет latest tournament через `get_selected_tournament_id()`, покажет позицию пользователя в ranking этого турнира или `None`, если пользователя нет в таблице. Но статистика профиля сейчас считается по всем finished матчам пользователя, а не только по выбранному турниру.

`/my-predictions` в `app/routes/predictions.py` выберет latest tournament через `get_selected_tournament_id()` и покажет списки прогнозов по выбранному турниру. В offseason ожидаемо будут пустыми `pending` и `awaiting`, а `finished` может содержать завершённые прогнозы.

`/match/<id>/predictions` берёт `tournament_id` из самого матча. Если у матча `tournament_id IS NULL`, маршрут fallback-ится через `get_selected_tournament_id()`. Без active tournaments это может привязать такой матч к latest tournament, что не всегда является фактическим турниром матча.

`templates/base.html` строит tournament switcher из `active_tournaments|default(tournaments|default([]))`. В Jinja пустой список считается значением, поэтому при `active_tournaments=[]` fallback на `tournaments` не сработает. В результате кнопка выбора турнира и sheet исчезнут, даже если архивные турниры есть.

## Где возможны поломки

Основные риски сейчас не в прямом падении приложения, а в пустых и неверно описанных состояниях:

- Главная страница может показывать пустое состояние “скоро появятся новые игры”, хотя реальное состояние системы — сезон завершён.
- В `base.html` исчезает переключатель турниров, потому что `active_tournaments` пустой, а fallback на `tournaments` не включается для пустого списка.
- Нижняя навигация продолжает прокидывать `?tid={{ current_tid }}` только если текущий id есть. На страницах без выбранного турнира ссылки станут без `tid`, что нормально для fallback, но плохо для явного архивного режима.
- `body` получает тему по `current_tournament_name`: всё, что не `ЧМ-2026`, становится `tournament-cup`. Для offseason нет отдельного визуального состояния.
- В `/profile` `current_place` может быть `None`, если в latest tournament нет пользователя в ranking. Это не ошибка само по себе, но шаблон должен быть готов показывать отсутствие места.
- В `/profile` общая статистика и последние прогнозы не фильтруются по выбранному турниру, поэтому архивный турнир в заголовке может не совпадать с агрегатами профиля.
- В `/my-predictions` при latest tournament без прогнозов все списки будут пустыми. Это должно быть нормальным состоянием, но UX должен объяснять, что активных матчей больше нет.
- В `/match/<id>/predictions` матч без `tournament_id` может получить latest tournament как fallback. В offseason это особенно рискованно, потому что latest tournament может быть просто последним завершённым, а не турниром конкретного матча.
- Если таблица `tournaments` полностью пустая, `get_selected_tournament_id()` вернёт `None`. `/table` это обрабатывает, `/profile` и `/my-predictions` редиректят с flash, `/` отрендерит главную с `current_tournament_id=None` и пустыми матчами.

## Как должен работать offseason mode

Offseason mode должен быть явным состоянием продукта: “активного сезона сейчас нет, последний сезон завершён, архив доступен”.

Ожидаемое поведение:

- Сайт не должен считать отсутствие active tournament ошибкой.
- Главная страница должна показывать понятное состояние “Сезон завершён” вместо ожидания новых ближайших матчей.
- Если есть latest tournament, страницы таблицы, профиля и “мои прогнозы” должны уметь показывать архив этого турнира.
- Переключатель турниров должен оставаться доступным для архивных турниров, даже когда active tournaments пустой.
- Текущий выбранный tournament id должен быть консистентным между главной, таблицей, профилем и прогнозами.
- `active_tournaments=[]` должно означать не “турниров нет”, а “нет активного сезона”.
- Если турниров нет вообще, это отдельное empty-state состояние: “турниры ещё не созданы”.
- Для матчей без `tournament_id` fallback на latest tournament должен использоваться осторожно: лучше отображать такие данные только там, где это уже исторически допустимо, и не записывать новые прогнозы без уверенного tournament id.

Минимальная модель состояния может быть такой:

- `selected_tournament`: выбранный `?tid` или latest tournament.
- `has_active_tournament`: есть хотя бы один `is_active`.
- `is_offseason`: нет active tournaments, но есть хотя бы один турнир.
- `has_any_tournament`: список tournaments не пуст.

## Safe implementation plan

1. Не менять бизнес-логику подсчёта и сохранения прогнозов первым шагом.
2. Централизовать вычисление offseason flags рядом с выбором турнира или в route-level context, чтобы шаблоны не гадали по пустым спискам.
3. Передавать в шаблоны явные флаги `is_offseason`, `has_active_tournament`, `has_any_tournament`.
4. В `base.html` для tournament switcher использовать все турниры, если active tournaments пустой, но tournaments есть.
5. На главной заменить generic empty-state на offseason-state, когда `is_offseason=True` и нет будущих матчей.
6. Проверить `/`, `/table`, `/profile`, `/my-predictions`, `/match/<id>/predictions` в трёх состояниях базы: есть active tournament, active нет но есть latest tournament, tournaments нет вообще.
7. Только после стабилизации UI решить, нужно ли менять fallback для матчей с `tournament_id IS NULL`.

## First recommended fix

Первый безопасный шаг — не трогать сохранение прогнозов, ranking и SQL выборки, а добавить явный offseason context на уровне отображения.

Минимально безопасная правка:

- На маршрутах, где уже загружаются `all_tournaments` и `active_tournaments`, вычислить `is_offseason = bool(all_tournaments) and not active_tournaments`.
- Передать `is_offseason` и `has_any_tournament` в шаблоны.
- В `base.html` сделать список переключателя турниров архивным fallback-списком, когда active tournaments пустой.
- В empty-state главной показывать “Сезон завершён” при `is_offseason=True`.

Такой шаг не меняет данные, не меняет алгоритм выбора турнира и не влияет на начисление очков. Он только делает текущее fallback-поведение честным для пользователя: сайт уже умеет жить на latest tournament, но интерфейс пока не объясняет, что это архивный режим между сезонами.

## Implemented first safe fix

Первый safe fix реализован на уровне route context и шаблонов, без изменения scoring, ranking, сохранения прогнозов, sync pipeline и tournament fallback algorithm.

Добавлены явные flags:

- `has_any_tournament`: в списке tournaments есть хотя бы один турнир.
- `has_active_tournament`: среди tournaments есть хотя бы один `is_active`.
- `is_offseason`: турниры есть, но active tournaments нет.

Flags передаются в основные пользовательские страницы:

- `/`
- `/table`
- `/profile`
- `/my-predictions`

`base.html` теперь использует active tournaments для переключателя, если они есть. Если active tournaments пустой, но tournaments есть, переключатель показывает архивные турниры вместо того, чтобы исчезать.

Главная страница при пустом списке матчей теперь различает состояния:

- `is_offseason=True`: “Сезон завершён”, “Новый турнир скоро появится”, “Таблица последнего турнира доступна”.
- `has_any_tournament=False`: “Турниры ещё не созданы”.
- Обычный активный сезон без доступных матчей: прежний смысл “Нет доступных матчей / Скоро появятся новые игры”.

Что осталось на потом:

- Отдельный визуальный режим для offseason, если он понадобится продуктово.
- Более точное поведение для матчей с `tournament_id IS NULL` в архивном режиме.
- Решение, должна ли статистика профиля фильтроваться по выбранному турниру или оставаться общей.
- UX для пустых списков в `/my-predictions`, когда latest tournament выбран, но прогнозов нет.
