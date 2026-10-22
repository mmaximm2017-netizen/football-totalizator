"""Additional fixed read-only templates for the Russian Telegram assistant."""
from app.models.scoring import FINISHED_STATUSES_SQL


def _rows(cur, statement, params):
    cur.execute(statement, tuple(params))
    return cur.fetchall()


def analytics_lines(cur, q, tid, where, params, finished, limit):
    if q.intent == 'round_wins':
        return _round_wins(cur, q, tid, limit)
    if q.intent == 'streak':
        # Walk the full finished match sequence, including missing predictions.
        rows = _rows(cur, f"""
            WITH sequence AS (
                SELECT u.user_id, u.username, m.match_id, m.kickoff_time,
                       p.points,
                       ROW_NUMBER() OVER (PARTITION BY u.user_id ORDER BY m.kickoff_time DESC, m.match_id DESC) AS position
                FROM gpt_safe.users u CROSS JOIN gpt_safe.matches m
                LEFT JOIN gpt_safe.predictions p ON p.user_id = u.user_id
                    AND p.match_id = m.match_id AND p.tournament_id = m.tournament_id
                WHERE {where} AND {finished}
                  AND m.deadline IS NOT NULL AND m.deadline <= CURRENT_TIMESTAMP
                  AND m.home_score IS NOT NULL AND m.away_score IS NOT NULL
                  AND (%s::text IS NULL OR u.username = %s)
            )
            SELECT username, COALESCE(MIN(position) FILTER (WHERE points >= 10) - 1, COUNT(*)), COUNT(*)
            FROM sequence GROUP BY user_id, username ORDER BY 2 DESC, username LIMIT %s
        """, params + [q.player, q.player, limit + 1])
        lines = ['Текущая серия без 10/11 очков. Пропуски прогнозов включены; серия ограничена выбранным периодом/туром.']
        lines += [f'{r[0]}: {r[1]} матчей' + (' (точных счетов в выборке нет)' if r[1] == r[2] else '') for r in rows[:limit]]
    else:
        # A fixed user list is a bound array, never SQL supplied by a user.
        selected = q.players if q.intent == 'compare' else [q.player] if q.player else None
        order = {
            'record': '4 DESC NULLS LAST',
            'average': 'COALESCE(SUM(p.points)::numeric / NULLIF(COUNT(p.points), 0), -1) DESC',
            'hits': '5 DESC' if q.points == -1 else '6 DESC',
            'compare': '3 DESC',
        }[q.intent]
        rows = _rows(cur, f"""
            SELECT u.username, COUNT(p.points), COALESCE(SUM(p.points), 0),
                   MAX(p.points), COUNT(*) FILTER (WHERE p.points >= 10),
                   COUNT(*) FILTER (WHERE p.points = %s)
            FROM gpt_safe.users u
            LEFT JOIN (
                SELECT p.* FROM gpt_safe.predictions p
                JOIN gpt_safe.matches m ON m.match_id = p.match_id AND m.tournament_id = p.tournament_id
                WHERE {where} AND {finished}
            ) p ON p.user_id = u.user_id
            WHERE (%s::text[] IS NULL OR u.username = ANY(%s::text[]))
            GROUP BY u.user_id, u.username ORDER BY {order}, u.username LIMIT %s
        """, [q.points] + params + [selected, selected, limit + 1])
        labels = {'record': 'Рекорд начисленных очков за один матч', 'average': 'Средние очки за прогноз с начисленными очками (пропуски исключены)', 'hits': 'Число прогнозов на 10/11 очков' if q.points == -1 else f'Число прогнозов ровно на {q.points} очков', 'compare': 'Сравнение: сумма, среднее за прогноз, точные счета (10/11)'}
        lines = [labels[q.intent]]
        for name, count, total, record, exact, hits in rows[:limit]:
            if not count:
                lines.append(f'{name}: нет прогнозов с начисленными очками')
            elif q.intent == 'record':
                lines.append(f'{name}: {record} очк.')
            elif q.intent == 'average':
                lines.append(f'{name}: {total / count:.2f} очк. ({count} прогнозов)')
            elif q.intent == 'hits':
                lines.append(f'{name}: {exact if q.points == -1 else hits} раз')
            else:
                lines.append(f'{name}\n{total} очк. · среднее {total / count:.2f}\n{count} прогнозов · точных счетов {exact}')
    if not rows:
        lines.append('Подходящих завершённых матчей нет.')
    if len(rows) > limit:
        lines.append(f'Показаны первые {limit} участников. Уточните игрока.')
    return lines


def _round_wins(cur, q, tid, limit):
    # Round eligibility is computed BEFORE any date restriction. Otherwise a
    # date slice can turn a partially played round into a completed one.
    if q.teams or q.match_id:
        return ['Победы считаются по целому туру. Уберите фильтр команд или матча.']
    rows = _rows(cur, f"""
        WITH eligible AS (
            SELECT r.tournament_id, r.round_number
            FROM gpt_safe.calendar_rounds r
            JOIN gpt_safe.matches m ON m.match_id = r.match_id AND m.tournament_id = r.tournament_id
            WHERE r.tournament_id = %s
              AND (%s::integer IS NULL OR r.round_number = %s)
            GROUP BY r.tournament_id, r.round_number
            HAVING BOOL_AND(COALESCE(UPPER(m.status) IN {FINISHED_STATUSES_SQL}
                AND m.kickoff_time <= CURRENT_TIMESTAMP
                AND m.deadline <= CURRENT_TIMESTAMP
                AND m.home_score IS NOT NULL AND m.away_score IS NOT NULL, FALSE))
              AND (%s::date[] IS NULL OR MAX((m.kickoff_time AT TIME ZONE 'Europe/Moscow')::date) = ANY(%s::date[]))
              AND NOT EXISTS (
                  SELECT 1 FROM gpt_safe.predictions bad
                  JOIN gpt_safe.calendar_rounds br ON br.match_id = bad.match_id AND br.tournament_id = bad.tournament_id
                  WHERE br.tournament_id = r.tournament_id AND br.round_number = r.round_number
                    AND (bad.points IS NULL OR bad.points NOT IN (0,2,3,5,7,8,10,11))
              )
        ), totals AS (
            SELECT u.user_id, u.username, e.round_number, COALESCE(SUM(p.points), 0) AS points, COUNT(p.points) AS submitted
            FROM gpt_safe.users u CROSS JOIN eligible e
            JOIN gpt_safe.calendar_rounds cr ON cr.tournament_id = e.tournament_id AND cr.round_number = e.round_number
            LEFT JOIN gpt_safe.predictions p ON p.user_id = u.user_id
                AND p.tournament_id = cr.tournament_id AND p.match_id = cr.match_id
            GROUP BY u.user_id, u.username, e.round_number
        ), ranked AS (
            SELECT *, RANK() OVER (PARTITION BY round_number ORDER BY points DESC) AS place,
                   SUM(submitted) OVER (PARTITION BY round_number) AS round_submitted
            FROM totals
        ), wins AS (
            SELECT user_id, COUNT(*) AS count FROM ranked
            WHERE place = 1 AND round_submitted > 0 GROUP BY user_id
        )
        SELECT u.username, COALESCE(w.count, 0), (SELECT COUNT(*) FROM eligible),
               (SELECT COUNT(DISTINCT round_number) FROM ranked WHERE round_submitted > 0)
        FROM gpt_safe.users u LEFT JOIN wins w ON w.user_id = u.user_id
        WHERE (%s::text IS NULL OR u.username = %s)
        ORDER BY 2 DESC, u.username LIMIT %s
    """, [tid, q.round_number, q.round_number, q.dates or None, q.dates or None, q.player, q.player, limit + 1])
    lines = ['Победы в полностью завершённых турах. При равенстве очков победа каждому лидеру.', 'Туры по соседним датам МСК; один пустой день разделяет туры. Сборная и Суперкубок вне туров.']
    if q.dates:
        lines.append('Даты отбирают туры по дате последнего матча (МСК).')
    if not rows or not rows[0][3]:
        return lines + ['Завершённых туров с начисленными прогнозами нет.']
    lines.append(f'Учтено туров с прогнозами: {rows[0][3]}.')
    lines += [f'{i}. {r[0]}: {r[1]} побед' for i, r in enumerate(rows[:limit], 1)]
    if len(rows) > limit:
        lines.append(f'Показаны первые {limit} участников. Уточните игрока.')
    return lines
