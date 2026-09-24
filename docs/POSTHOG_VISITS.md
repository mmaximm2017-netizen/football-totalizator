# ТОТИШ: отчёт о посещениях через PostHog

Источник: `$pageview`. Для времени использовать `timestamp`, для страницы
`properties.$pathname` и `properties.$current_url`. Время PostHog UTC; календарный
день пользователя считать в Europe/Moscow (UTC+3). Начало 24 сентября МСК:
`2026-09-23 21:00:00` UTC, конец (исключительно): `2026-09-24 21:00:00` UTC.

Ник на момент события: `properties.totish_username`. Ник подтверждён серверной
сессией, значения identity из браузерного payload игнорируются. `$set.username`
обновляет person profile при каждом авторизованном событии, включая старые
сессии. На входе также отправляется серверное `$identify`; клиентский SDK не
нужен. Анонимные события не получают `$set` или ник.

`distinct_id` сохраняет существующий HMAC от внутреннего user_id: смена ника не
создаёт другого пользователя. Не менять его на ник или raw user_id: это разорвёт
связь с предыдущими событиями. При смене серверного SECRET_KEY HMAC изменится;
`totish_user_ref` остаётся независимым стабильным ключом сопоставления.

## Проверенный запрос, включая старые события

Никаких обязательных фильтров `$virt_is_bot = false` или исключения Automation.
Отчёт показывает аккаунт, от имени которого сервер принял событие; сам факт
наличия аккаунта не доказывает, что браузером управлял человек.

```sql
SELECT e.timestamp,
       coalesce(nullIf(e.properties.totish_username, ''), n.username) AS username,
       e.properties.$pathname AS pathname,
       e.properties.$current_url AS url
FROM events e
LEFT JOIN (
    SELECT properties.totish_user_ref AS user_ref,
           argMax(properties.totish_username, timestamp) AS username
    FROM events
    WHERE properties.totish_user_ref IS NOT NULL
      AND properties.totish_username IS NOT NULL
      AND properties.totish_username != ''
    GROUP BY user_ref
) n ON e.properties.totish_user_ref = n.user_ref
WHERE e.event = '$pageview'
  AND e.timestamp >= toDateTime('2026-09-23 21:00:00')
  AND e.timestamp < toDateTime('2026-09-24 21:00:00')
ORDER BY e.timestamp
LIMIT 1000
```

Менять границы под запрошенную дату. При достижении LIMIT дочитывать данные,
не выдавать обрезанный ответ за полный. Отображать timestamp в МСК.
Старые события без собственного ника получают последний известный ник аккаунта,
не обязательно ник на историческую дату. Если сопоставления нет, явно писать
«ник не установлен», не угадывать и не объявлять такие события ботами.
Отсутствие события не доказывает отсутствие посещения: доставка аналитики best-effort.

## Read-only сопоставление с production-БД

Для аккаунтов без новых именованных событий доступен подготовленный SELECT
только к gpt_safe.users. Выполнять через разрешённый read-only доступ; он не
требует расширения pgcrypto, изменения схемы, секретов приложения или паролей:

```sql
SELECT username,
       encode(sha256(convert_to(
           'totish-analytics-user-v1:' || user_id::text, 'UTF8'
       )), 'hex') AS totish_user_ref
FROM gpt_safe.users
ORDER BY user_id;
```

Сопоставлять точный полный хэш, не префикс, IP или порядок пользователей.
Запрос подготовлен; в этой задаче production-БД через Neon недоступна из-за
несовместимости параметра project_id коннектора. Проверенное сопоставление
старых событий выше использует только уже подтверждённые сервером события.

## Навигация и проверки

Событие отправляется при загрузке документа, смене URL через pushState /
replaceState, popstate, восстановлении документа из back/forward cache.
Начальный pageshow не дублирует загрузку. Полный URL используется локально для
обнаружения перехода, но query string и fragment не передаются в PostHog.
Сервер строит current_url из origin и очищенного pathname. Поэтому переключение
турниров /table?tid=5 -> /table?tid=6 записывается как повторное посещение /table.
Service Worker не кэширует навигационные документы (см. static/service-worker.js).

Тесты: tests/test_product_analytics.py, tests/test_analytics_navigation.py.
Все запросы аналитики не добавляют обращений к БД; ник существующей сессии
заполняется уже имеющимся SELECT при обычном запросе страницы.
