"""Reconstruct round totals and chronological standings from safe read-only views."""
from datetime import datetime

from app.gpt_db import short_gpt_read
from app.models.scoring import FINISHED_STATUSES_SQL, has_valid_finished_score
from app.services.telegram_sql_assistant import Question, choose_tournament, read
from app.utils import MSK, parse_datetime

VALID_POINTS = (0, 2, 3, 5, 7, 8, 10, 11)


def round_summary_payload(kind, round_number, now=None):
    if not isinstance(kind, str) or kind not in {"rpl", "cup"} or type(round_number) is not int or not 1 <= round_number <= 9999:
        raise ValueError("invalid_round")
    now = now or datetime.now(MSK)
    with short_gpt_read() as cur:
        cur.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        tid, _ = choose_tournament(
            Question(tournament="рпл" if kind == "rpl" else "кубок россии"),
            read(cur, "SELECT tournament_id, tournament_name FROM gpt_safe.tournaments ORDER BY tournament_id"),
        )
        fixtures = read(cur, """
            SELECT m.match_id, m.kickoff_time, m.deadline, m.status, m.home_score, m.away_score
            FROM gpt_safe.matches m WHERE m.tournament_id = %s AND EXISTS (
                SELECT 1 FROM gpt_safe.calendar_rounds r WHERE r.match_id = m.match_id
                  AND r.tournament_id = m.tournament_id AND r.round_number = %s)
            ORDER BY m.kickoff_time, m.match_id
        """, (tid, round_number))
        if not fixtures:
            raise ValueError("unknown_round")
        first, last = fixtures[0][1], fixtures[-1][1]
        finished = [mid for mid, kickoff, deadline, status, hs, aws in fixtures
                    if has_valid_finished_score(status, hs, aws) and kickoff <= now
                    and parse_datetime(deadline) is not None and parse_datetime(deadline) <= now]
        payload = {"type": "round_summary", "kind": kind, "round_number": round_number,
                   "start": first.astimezone(MSK).isoformat(), "end": last.astimezone(MSK).isoformat(),
                   "status": "no_results", "finished_matches": len(finished), "total_matches": len(fixtures),
                   "rows": [], "movement_available": False}
        if not finished:
            return payload
        # Each user has a before/after total with canonical leaderboard tie-breaks.
        # The upper bound excludes later rounds when an old round is selected.
        # Forecast fields are never selected for unfinished or premature matches.
        rows = read(cur, f"""
            WITH eligible AS (
                SELECT m.match_id, m.kickoff_time, p.user_id, p.points
                FROM gpt_safe.matches m JOIN gpt_safe.predictions p
                  ON p.match_id = m.match_id AND p.tournament_id = m.tournament_id
                WHERE m.tournament_id = %s AND m.kickoff_time <= %s AND m.kickoff_time <= %s
                  AND UPPER(m.status) IN {FINISHED_STATUSES_SQL}
                  AND m.deadline IS NOT NULL AND m.deadline <= %s
                  AND m.home_score BETWEEN 0 AND 99 AND m.away_score BETWEEN 0 AND 99
            ), totals AS (
                SELECT u.user_id, u.username,
                    COALESCE(SUM(e.points) FILTER (WHERE e.match_id = ANY(%s::integer[])), 0) AS round_points,
                    COUNT(e.points) FILTER (WHERE e.match_id = ANY(%s::integer[])) AS round_scored,
                    COUNT(e.match_id) FILTER (WHERE e.match_id = ANY(%s::integer[])
                      AND (e.points IS NULL OR NOT e.points = ANY(%s::integer[]))) AS round_pending,
                    COALESCE(SUM(e.points) FILTER (WHERE e.kickoff_time < %s), 0) AS before_points,
                    COUNT(*) FILTER (WHERE e.kickoff_time < %s AND e.points >= 10) AS before_exact,
                    COUNT(*) FILTER (WHERE e.kickoff_time < %s AND e.points BETWEEN 7 AND 8) AS before_diff,
                    COUNT(*) FILTER (WHERE e.kickoff_time < %s AND e.points = 3) AS before_outcome,
                    COUNT(e.points) FILTER (WHERE e.kickoff_time < %s) AS before_scored,
                    COALESCE(SUM(e.points), 0) AS after_points,
                    COUNT(*) FILTER (WHERE e.points >= 10) AS after_exact,
                    COUNT(*) FILTER (WHERE e.points BETWEEN 7 AND 8) AS after_diff,
                    COUNT(*) FILTER (WHERE e.points = 3) AS after_outcome,
                    COUNT(e.points) AS after_scored,
                    COUNT(e.match_id) FILTER (WHERE e.points IS NULL OR NOT e.points = ANY(%s::integer[])) AS after_pending
                FROM gpt_safe.users u LEFT JOIN eligible e ON e.user_id = u.user_id
                GROUP BY u.user_id, u.username
            ), ranked AS (
                SELECT *, RANK() OVER (ORDER BY before_points DESC, before_exact DESC,
                    before_diff DESC, before_outcome DESC, username) AS before_place,
                    RANK() OVER (ORDER BY after_points DESC, after_exact DESC,
                    after_diff DESC, after_outcome DESC, username) AS after_place,
                    RANK() OVER (ORDER BY round_points DESC) AS round_place
                FROM totals
            )
            SELECT user_id, username, round_points, round_scored, round_pending,
                   before_place, after_place, before_scored, after_scored, after_pending, round_place
            FROM ranked ORDER BY round_points DESC, username, user_id
        """, (tid, last, now, now, finished, finished, finished, list(VALID_POINTS),
              first, first, first, first, first, list(VALID_POINTS)))
        if any(row[4] for row in rows):
            payload["status"] = "pending_points"
            return payload
        has_scores = any(row[3] for row in rows)
        payload["has_scores"] = has_scores
        final = len(finished) == len(fixtures)
        payload["status"] = "final" if final else "in_progress"
        # Historical membership snapshots do not exist in the safe schema. Use
        # the same active participant roster as other period-based analytics.
        after_available = any(row[8] for row in rows) and not any(row[9] for row in rows)
        movement_available = after_available and any(row[7] for row in rows)
        payload["movement_available"] = movement_available
        payload["rows"] = [
            {"user_id": row[0], "name": row[1], "points": row[2], "place": row[10],
             "before_place": row[5] if movement_available else None,
             "after_place": row[6] if after_available else None,
             "movement": row[5] - row[6] if movement_available else None,
             "winner": final and has_scores and row[10] == 1}
            for row in rows
        ]
        return payload
