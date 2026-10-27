"""Fixture writer for the disposable CI database, never production."""
import json
import os
from pathlib import Path
import sys
import uuid

import psycopg2

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def main():
    dsn = os.getenv("AUTO_RESULTS_TEST_DATABASE_URL")
    if not dsn:
        raise RuntimeError("explicit_disposable_test_database_required")
    conn = psycopg2.connect(dsn)
    path = Path("test-results/site-journey/fixture.json")
    try:
        with conn.cursor() as cur:
            if sys.argv[1] == "seed":
                name = "browser_" + uuid.uuid4().hex[:12]
                cur.execute("INSERT INTO tournaments(name,is_active,start_date) VALUES('Чемпионат России 🇷🇺',1,'2026-01-01') RETURNING id")
                tid = cur.fetchone()[0]
                cur.execute("""INSERT INTO matches(tournament_id,league,home_team,away_team,kickoff_time,deadline,status)
                               VALUES(%s,'rpl','Зенит','ЦСКА',NOW()+interval '1 day',NOW()+interval '22 hours','SCHEDULED') RETURNING id""", (tid,))
                mid = cur.fetchone()[0]
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps({"tid": tid, "mid": mid, "username": name}))
            else:
                fixture = json.loads(path.read_text())
                cur.execute("UPDATE matches SET status='FINISHED',home_score=2,away_score=1,kickoff_time=NOW()-interval '1 day',deadline=NOW()-interval '26 hours' WHERE id=%s", (fixture["mid"],))
                from app.services.scoring_recalculation_service import recalc_match_points
                recalc_match_points(fixture["mid"], fixture["tid"], conn=conn, cur=cur, emit_result_event=False)
        conn.commit()
    finally:
        conn.close()


if __name__ == "__main__":
    main()
