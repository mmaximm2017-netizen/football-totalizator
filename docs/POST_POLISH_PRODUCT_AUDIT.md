# POST_POLISH_PRODUCT_AUDIT

## Что стало сильнее после polish

Продукт заметно приблизился к ощущению цельного мобильного приложения. Главная перестала выглядеть как тяжёлая страница с равными акцентами: match cards лучше ведут взгляд к командам, счёту, дедлайну и сохранению прогноза. Finished/closed состояния стали честнее: пользователь быстрее понимает, что уже завершено, что закрыто, где его прогноз, где итоговый счёт и где очки.

Таблица стала ближе к тому же mobile-card rhythm: спокойнее поверхности, понятнее top-3, стабильнее колонка очков, меньше риска развала от длинных username. Профиль после polish уже не живёт отдельной логикой: статистика и последние прогнозы фильтруются по выбранному `p.tournament_id`, а место берётся из ranking выбранного турнира. Это важный продуктовый шаг, потому что визуальный контекст и цифры больше не спорят друг с другом.

Tournament context стал значительно взрослее. `get_selected_tournament_id(requested_tid)` сделал `?tid=` главным server-side состоянием для пользовательских страниц, а fallback стал единым: ближайший будущий матч, первый активный турнир, latest tournament. Offseason больше не выглядит как поломка: появились `has_any_tournament`, `has_active_tournament`, `is_offseason`, а переключатель турниров теперь умеет показывать архивные турниры, если активных нет.

Админка стала более доверительной: `admin_matches.html` разделяет ежедневную работу, матчи и техническое обслуживание, а `admin_tournaments.html` отделяет активные турниры от архива и danger-зоны. Это не просто косметика: риск случайного удаления или массового действия стал ниже.

## Product UX audit

### app-feel

Что хорошо: главная, таблица и профиль уже используют близкий язык: мягкие карточки, ясные акценты, bottom sheet, bottom nav, tactile active states. Сохранение прогноза, stepper-кнопки, tournament sheet и bets sheet ощущаются как части одного приложения.

Что ещё выбивается: часть CSS и UI-паттернов всё ещё живёт прямо в шаблонах, поэтому app-feel держится на нескольких локальных реализациях, а не на общей системе компонентов. Профиль и админка визуально близки к продукту, но пока имеют свои отдельные классы и rhythm.

Severity: medium

### mobile hierarchy

Что хорошо: на мобильном главная стала читаемее. Верхняя строка карточки тише, основной сценарий прогноза сильнее, bottom nav стабилен, профиль защищён от длинных username через `overflow-wrap`, таблица защищает длинные имена через ellipsis.

Что ещё выбивается: активная match card остаётся самой плотной зоной продукта на 360-380px, особенно при длинных командах и уже сохранённом прогнозе. В профиле три stat-card в одну строку могут стать тесными при больших числах. В админке формы редактирования ручных матчей всё ещё тяжёлые на телефоне, хотя danger-действия отделены лучше.

Severity: medium

### navigation flow

Что хорошо: bottom nav сохраняет `?tid={{ current_tid }}` для главной, таблицы и профиля. Таблица после AJAX-переключения синхронизирует URL, `localStorage`, active state и bottom nav links.

Что ещё выбивается: `/match/<id>/predictions` не несёт `tid` и правильно опирается на `match.tournament_id`, но для матчей без `tournament_id` fallback может привести к выбранному/latest турниру, который не является фактическим контекстом матча. Админские формы местами всё ещё отправляют действия на старые endpoints и могут возвращать на `/admin`, что ломает ощущение новой разделённой админки.

Severity: medium

### consistency между home, table, profile, admin

Что хорошо: home/table/profile теперь лучше согласованы по `tid`, визуальному ритму и состояниям. Admin получил локальную навигацию и более понятные зоны, не смешивая обычные и редкие действия так сильно, как раньше.

Что ещё выбивается: админка использует похожую, но отдельную систему `.admin-*` классов. Это нормально для внутреннего инструмента, но при дальнейшем polish есть риск дрейфа: `admin_matches`, `admin_tournaments` и будущий `admin_users` могут начать выглядеть как разные продукты.

Severity: low

### tactile feel

Что хорошо: stepper buttons, save button, match rows, bottom nav и profile rows имеют быстрый, мягкий active-state. Bottom sheets открываются как нативные слои, а не как отдельные страницы.

Что ещё выбивается: часть admin actions визуально нажимаемая, но не все массовые операции имеют одинаковую степень подтверждения. `recalc_all` и `translate` вынесены в обслуживание, но остаются обычными submit-кнопками без дополнительного `confirm`.

Severity: medium

### empty states

Что хорошо: главная различает offseason, отсутствие турниров и обычное отсутствие матчей. Профиль и админка не разваливаются на пустых списках. Таблица умеет жить с пустым ranking.

Что ещё выбивается: профиль всё ещё говорит "Пока нет завершённых матчей", не уточняя "в выбранном турнире". `/my-predictions` при выбранном latest tournament без прогнозов может выглядеть как пустота без объяснения архивного/offseason контекста. В админке empty states функциональны, но не всегда объясняют следующий шаг.

Severity: low

### WC2026 theme consistency

Что хорошо: WC2026 имеет узнаваемый отдельный skin: фон, bottom nav, match cards, table header и profile blocks получают специальную тему. Основные элементы остаются читаемыми.

Что ещё выбивается: WC2026 визуально насыщеннее обычного режима. Это уместно для special tournament, но риск шума выше, особенно если добавлять новые badges, декоративные слои или дополнительные CTA поверх текущего золото-тёмного языка.

Severity: low

### visual hierarchy

Что хорошо: главные действия стали заметнее, вторичные статусы тише. В таблице очки и место читаются быстрее. В профиле имя, место и основные цифры находятся выше вторичных деталей.

Что ещё выбивается: в finished card всё ещё много смыслов рядом: итог, очки, прогноз пользователя и ссылка на ставки. В профиле CTA "Вернуться к матчам" выглядит как большое основное действие, хотя bottom nav уже содержит путь на матчи.

Severity: low

## Tournament context audit

### selected tournament consistency

Current behavior: `get_selected_tournament_id(requested_tid)` принимает явный `?tid=` как основной state и используется пользовательскими страницами. Fallback общий: nearest upcoming, first active, latest.

Risk: старые helpers (`get_requested_or_current_tournament_id`, `get_table_tournament_id`, `get_profile_tournament_id`, `get_active_context_tournament_id`) остались в сервисе. Если будущий route случайно начнёт использовать старый helper, снова появится расхождение выбранного турнира.

Severity: medium

### archive tournament behavior

Current behavior: при отсутствии active tournaments переключатель в `base.html` использует архивные `tournaments`, если они есть. Таблица, профиль и прогнозы могут показывать latest/selected tournament.

Risk: архивный режим визуально не отделён от активного сезона, кроме empty state на главной. Пользователь может не всегда понимать, что смотрит историю, а не текущий турнир.

Severity: low

### offseason behavior

Current behavior: `is_offseason` передаётся на основные страницы, главная показывает "Сезон завершён", tournament switcher не исчезает при пустом `active_tournaments`.

Risk: `/my-predictions` и профиль в offseason могут показывать пустые или слабые состояния без явного объяснения, что активных матчей нет, но архив доступен.

Severity: low

### invalid tid behavior

Current behavior: если `requested_tid` задан, но такого турнира нет, helper молча применяет общий fallback.

Risk: пользователь с битой ссылкой `?tid=999` не получает явного сигнала, что выбранный турнир не найден. Это безопасно для работоспособности, но может привести к тихому показу другого турнира и неверному пользовательскому ожиданию.

Severity: medium

### profile/table/home consistency

Current behavior: home/table/profile получают `current_tournament_id/current_tournament_name`, bottom nav сохраняет `tid`, профиль фильтрует статистику и recent по выбранному турниру.

Risk: consistency держится на том, что каждый route передаёт правильный context. Новая пользовательская страница должна явно подключиться к тому же контракту, иначе визуальный shell покажет один турнир, а данные могут жить в другом.

Severity: medium

### localStorage sync consistency

Current behavior: `base.html` восстанавливает `selected_tid` на `/`, `/table`, `/profile`, `/my-predictions`; таблица дополнительно синхронизирует bottom nav после AJAX.

Risk: `localStorage` всё ещё является client-side memory, а не server-side source. При первом рендере без `tid` сервер может отдать fallback, а браузер затем сделать `replace` на сохранённый `tid`. Это почти незаметно, но остаётся потенциальный миг состояния.

Severity: low

### routes using fallback incorrectly

Current behavior: в проверенных файлах ключевой helper централизован, но старые fallback helpers не удалены. `/match/<id>/predictions` намеренно работает от `match.tournament_id`, а selected state использует только как fallback для матчей без tournament id.

Risk: самый опасный кейс — матчи с `tournament_id IS NULL`. В активном сезоне это исторически терпимо, но в archive/offseason они могут получить latest tournament как контекст и исказить смысл страницы ставок.

Severity: medium

## Quick wins

- Уточнить empty text профиля: "В выбранном турнире пока нет завершённых прогнозов" вместо общего "Пока нет завершённых матчей".
- Выполнено: добавлен `confirm` для `/admin/recalc_all` и `/admin/translate`, потому что это массовые операции, уже вынесенные в "Техническое обслуживание".
- Добавить короткую подсказку в `/my-predictions` для пустых списков в offseason/archive context.
- В smoke checklist добавить отдельный блок проверки `?tid=`: home -> table AJAX switch -> profile -> back home.
- В smoke checklist добавить invalid `tid`: открыть `/?tid=999` и убедиться, что fallback не ломает страницу и выбранный tournament label понятен.
- На мобильном вручную проверить 360px для активной карточки с длинными названиями команд, профиля с длинным username и таблицы с длинным username.
- Зафиксировать в комментарии или audit note, что новые user-facing routes должны использовать `get_selected_tournament_id`.

## Dangerous zones

- `templates/base.html`: общий shell одновременно отвечает за theme class, tournament switcher, bottom nav, CSRF fallback, splash, service worker и `localStorage` restore. Маленькая правка здесь может затронуть все пользовательские страницы.
- `app/services/tournament_context_service.py`: рядом живут новый unified helper и старые fallback helpers. Это удобно для совместимости, но легко случайно выбрать не тот контракт.
- `templates/index.html`: сохранение прогноза, deadline lock, bets sheet, AJAX submit и визуальное состояние карточки связаны через DOM-классы и `data-*`. Визуальная правка формы может сломать сохранение без явного backend-ошибки.
- `templates/table.html`: AJAX-переключение турниров обновляет несколько независимых вещей: table HTML, body theme, header, URL, `localStorage`, bottom nav, active state. Добавление новых cross-page ссылок потребует синхронизации.
- `/match/<id>/predictions`: контекст турнира идёт от матча. Для матчей без `tournament_id` fallback особенно рискован в archive/offseason.
- `templates/admin_matches.html`: формы отправляются на разные endpoints, часть на `admin.admin`, часть на прямые paths. UX polish здесь легко случайно меняет POST contract.
- `templates/admin_tournaments.html`: UI уже отделил archive/delete, но backend-защита удаления активного турнира остаётся критичной. Нельзя полагаться только на визуальное разделение.
- WC2026 theme: отдельные `!important`-правила и theme-specific overrides могут неожиданно перебить новые общие стили.

## Recommended next phase

Логичнее всего следующей фазой сделать stabilization + tests, а не новый большой visual polish.

Приоритеты:

1. Stabilization: пройти ручной smoke по главной, таблице, профилю, `/my-predictions`, admin matches/tournaments и offseason/archive сценарию.
2. Tests: добавить минимальные route/context tests для `get_selected_tournament_id`, invalid `tid`, отсутствия active tournaments и сохранения `tid` между home/table/profile.
3. Tournament context cleanup: после тестов решить, оставлять ли старые helpers или пометить их как legacy, чтобы новые routes не использовали их случайно.
4. Auth polish: аккуратно проверить login/logout/session states, потому что bottom nav и admin visibility завязаны на `session.user_id` и `g.is_admin`.
5. Admin improvements: добавить подтверждения массовым операциям, затем отдельно разобрать POST redirects, которые могут возвращать на старую `/admin`.
6. PWA: после стабилизации UX проверить manifest, service worker, splash и offline/update behavior. Сейчас PWA-слой есть, но его лучше трогать только после фиксации core flows.
7. Backups: перед следующими backend/refactor задачами зафиксировать backup/restore процесс для базы, потому что турниры, матчи, прогнозы и очки связаны плотно.
8. Refactor: откладывать до появления тестов. Самый разумный refactor позже — вынести повторяющиеся UI стили из шаблонов и укрепить tournament context contract, а не переписывать всё.
