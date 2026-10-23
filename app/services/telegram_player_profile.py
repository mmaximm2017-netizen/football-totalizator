"""Structured Mini App profile using the existing safe ranking and round rules."""
from app.services.telegram_analytics_queries import round_win_rows
from app.services.telegram_sql_assistant import FINISHED, Question, read
from app.utils import MSK


def player_profile(cur, tid, ranking, index, active):
    uid, name, points, forecasts, exact, *_ = ranking[index]
    ranked = any(row[3] for row in ranking)
    profile = {"user_id": uid, "name": name, "active": active,
               "place": index + 1 if ranked else None, "participants": len(ranking),
               "points": points, "exact": exact if forecasts else None,
               "average": f"{points / forecasts:.2f}" if forecasts else None,
               "round_wins": None, "recent": []}
    if not active:
        return profile
    wins = round_win_rows(cur, Question(intent="round_wins"), tid, 1, user_id=uid)
    if wins and wins[0][3]:
        profile["round_wins"] = wins[0][1]
    recent = read(cur, f"""
        SELECT m.home_team, m.away_team, m.kickoff_time, p.points
        FROM gpt_safe.predictions p JOIN gpt_safe.matches m
          ON m.match_id = p.match_id AND m.tournament_id = p.tournament_id
        WHERE m.tournament_id = %s AND p.user_id = %s AND {FINISHED}
          AND m.deadline IS NOT NULL AND m.deadline <= CURRENT_TIMESTAMP
          AND m.home_score BETWEEN 0 AND 99 AND m.away_score BETWEEN 0 AND 99
        ORDER BY m.kickoff_time DESC, m.match_id DESC LIMIT 5
    """, (tid, uid))
    profile["recent"] = [{"home": home, "away": away, "kickoff": start.astimezone(MSK).isoformat(),
                          "points": score if score in (0, 2, 3, 5, 7, 8, 10, 11) else None}
                         for home, away, start, score in recent]
    return profile
