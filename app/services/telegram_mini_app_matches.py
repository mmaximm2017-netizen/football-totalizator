"""Whole-round Mini App fixtures with finished-only, read-only prediction data."""
from datetime import datetime

from app.gpt_db import short_gpt_read
from app.models.scoring import has_valid_finished_score
from app.services.telegram_sql_assistant import Question, choose_tournament, read
from app.utils import MSK, parse_datetime


def matches_payload(kind, round_number=None, now=None):
    if not isinstance(kind, str) or kind not in {"rpl", "cup"} or (
        round_number is not None and (type(round_number) is not int or not 1 <= round_number <= 9999)
    ):
        raise ValueError("invalid_round")
    now = now or datetime.now(MSK)
    with short_gpt_read() as cur:
        # Keep the score and stored points in one snapshot during result updates.
        cur.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        tid, _ = choose_tournament(
            Question(tournament="рпл" if kind == "rpl" else "кубок россии"),
            read(cur, "SELECT tournament_id, tournament_name FROM gpt_safe.tournaments ORDER BY tournament_id"),
        )
        rounds = read(cur, """
            SELECT r.round_number, MIN(m.kickoff_time), MAX(m.kickoff_time)
            FROM gpt_safe.calendar_rounds r JOIN gpt_safe.matches m
              ON m.match_id = r.match_id AND m.tournament_id = r.tournament_id
            WHERE r.tournament_id = %s GROUP BY r.round_number ORDER BY MIN(m.kickoff_time), r.round_number
        """, (tid,))
        payload = {"type": "round_matches", "kind": kind, "round_number": None, "rounds": [
            {"number": number, "start": start.astimezone(MSK).isoformat(), "end": end.astimezone(MSK).isoformat()}
            for number, start, end in rounds
        ], "matches": []}
        if not rounds:
            if round_number is not None:
                raise ValueError("unknown_round")
            return payload
        if round_number is None:
            round_number = next((number for number, _, end in rounds if end.astimezone(MSK).date() >= now.astimezone(MSK).date()), rounds[-1][0])
        if round_number not in {row[0] for row in rounds}:
            raise ValueError("unknown_round")
        payload["round_number"] = round_number
        fixtures = read(cur, """
            SELECT m.match_id, m.home_team, m.away_team, m.kickoff_time,
                   m.deadline, m.status, m.home_score, m.away_score
            FROM gpt_safe.matches m
            WHERE m.tournament_id = %s AND EXISTS (
                SELECT 1 FROM gpt_safe.calendar_rounds r WHERE r.match_id = m.match_id
                  AND r.tournament_id = m.tournament_id AND r.round_number = %s)
            ORDER BY m.kickoff_time, m.match_id
        """, (tid, round_number))
        _populate_matches(cur, payload, fixtures, tid, now)
    return payload


def _populate_matches(cur, payload, fixtures, tid, now):
    completed = {}
    for mid, home, away, kickoff, deadline, status, hs, aws in fixtures:
        deadline = parse_datetime(deadline)
        finished = (has_valid_finished_score(status, hs, aws) and kickoff <= now
                    and deadline is not None and deadline <= now)
        match = {"id": mid, "home": home, "away": away,
                 "kickoff": kickoff.astimezone(MSK).isoformat(), "finished": finished}
        if finished:
            match.update(score=[hs, aws], predictions=[])
            completed[mid] = match
        payload["matches"].append(match)
    # One batched read for completed fixtures only. No N+1 or live/prematch
    # forecast reads, including absence information. Preserve stored points.
    if completed:
        predictions = read(cur, """
            SELECT m.match_id, u.user_id, u.username, p.predicted_home, p.predicted_away, p.points
            FROM gpt_safe.matches m CROSS JOIN gpt_safe.users u
            LEFT JOIN gpt_safe.predictions p ON p.user_id = u.user_id
              AND p.match_id = m.match_id AND p.tournament_id = m.tournament_id
            WHERE m.tournament_id = %s AND m.match_id = ANY(%s::integer[])
            ORDER BY m.match_id, u.username, u.user_id
        """, (tid, list(completed)))
        for mid, uid, name, ph, pa, points in predictions:
            prediction = [ph, pa] if ph is not None and pa is not None else None
            completed[mid]["predictions"].append({"user_id": uid, "name": name, "score": prediction,
                                                 "points": points if prediction else None})


def event_matches_payload(event, year=None, now=None):
    """RPL extras, independent of calendar rounds, with stored-point subtotals."""
    if not isinstance(event, str) or event not in {"national", "supercup"} or (
        year is not None and year != "all" and (type(year) is not int or not 1900 <= year <= 2200)
    ):
        raise ValueError("invalid_event")
    now = now or datetime.now(MSK)
    with short_gpt_read() as cur:
        cur.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        tid, _ = choose_tournament(Question(tournament="рпл"), read(
            cur, "SELECT tournament_id, tournament_name FROM gpt_safe.tournaments ORDER BY tournament_id"))
        fixtures = read(cur, """
            SELECT m.match_id, m.home_team, m.away_team, m.kickoff_time,
                   m.deadline, m.status, m.home_score, m.away_score
            FROM gpt_safe.matches m JOIN gpt_safe.match_events e
              ON e.match_id = m.match_id AND e.tournament_id = m.tournament_id
            WHERE m.tournament_id = %s AND e.event_kind = %s
            ORDER BY m.kickoff_time, m.match_id
        """, (tid, event))
        years = sorted({row[3].astimezone(MSK).year for row in fixtures}, reverse=True)
        if year is None and fixtures:
            next_match = next((row for row in fixtures if row[3].astimezone(MSK).date() >= now.astimezone(MSK).date()), fixtures[-1])
            year = next_match[3].astimezone(MSK).year
        if year not in {None, "all", *years}:
            raise ValueError("unknown_year")
        selected = [row for row in fixtures if year == "all" or row[3].astimezone(MSK).year == year]
        payload = {"type": "event_matches", "kind": "rpl", "event": event, "year": year,
                   "years": years, "matches": [], "standings": [], "finished_matches": 0}
        _populate_matches(cur, payload, selected, tid, now)
        completed = [match for match in payload["matches"] if match["finished"]]
        payload["finished_matches"] = len(completed)
        if event == "national":
            # Forecasts were fetched only for eligible completed matches. Missing
            # forecasts contribute zero; a forecast awaiting points stays pending.
            from app.services.telegram_round_summary import VALID_POINTS
            totals = {}
            for match in completed:
                for prediction in match["predictions"]:
                    uid = prediction["user_id"]
                    row = totals.setdefault(uid, {"user_id": uid, "name": prediction["name"], "points": 0, "pending": 0})
                    if prediction["score"] is not None:
                        if prediction["points"] in VALID_POINTS:
                            row["points"] += prediction["points"]
                        else:
                            row["pending"] += 1
            pending = any(row["pending"] for row in totals.values())
            # Do not assign places while any participant's subtotal is incomplete.
            rows = sorted(totals.values(), key=lambda row: (-row["points"], row["name"], row["user_id"]))
            for row in rows:
                row["place"] = None if pending else 1 + sum(other["points"] > row["points"] for other in rows)
            for row in rows:
                if row["pending"]:
                    row["points"] = None
            payload["standings"] = rows
    return payload
