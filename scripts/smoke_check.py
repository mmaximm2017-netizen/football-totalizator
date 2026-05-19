from pathlib import Path


def ok(msg):
    print(f"[OK]   {msg}")


def warn(msg):
    print(f"[WARN] {msg}")


def fail(msg):
    print(f"[FAIL] {msg}")


def main():
    failures = 0
    warnings = 0

    try:
        from app import create_app
        app = create_app()
        ok("Flask app created")
    except Exception as e:
        fail(f"create_app failed: {e}")
        return 1

    required_templates = [
        "templates/index.html",
        "templates/table.html",
        "templates/profile.html",
        "templates/admin.html",
        "templates/admin_matches.html",
        "templates/admin_tournaments.html",
        "templates/admin_users.html",
    ]

    for rel in required_templates:
        p = Path(rel)
        if p.exists():
            ok(f"template exists: {rel}")
        else:
            fail(f"missing template: {rel}")
            failures += 1

    try:
        from app.services.ranking_service import get_tournament_ranking
        ok("ranking service import")
    except Exception as e:
        fail(f"ranking service import failed: {e}")
        failures += 1
        get_tournament_ranking = None

    try:
        import app.routes.table  # noqa: F401
        import app.routes.profile  # noqa: F401
        import app.routes.admin  # noqa: F401
        ok("critical routes import")
    except Exception as e:
        fail(f"critical route import failed: {e}")
        failures += 1

    try:
        from app.db import close_db, get_db
        from app.services.tournament_service import (
            ensure_single_active_tournament,
            get_active_tournament_id,
        )
        with app.app_context():
            conn = get_db()
            cur = conn.cursor()
            try:
                cur.execute("SELECT 1")
                cur.fetchone()
                ok("DB connection/query")

                tid = get_active_tournament_id()
                if tid:
                    ok(f"active tournament id: {tid}")
                else:
                    warn("no active tournament found")
                    warnings += 1

                if tid and get_tournament_ranking:
                    ranking = get_tournament_ranking(tid)
                    ok(f"ranking service run ({len(ranking)} rows)")

                    # tie/place consistency: SQL RANK semantics 1,1,3...
                    expected_place = []
                    prev_key = None
                    for idx, row in enumerate(ranking, start=1):
                        key = (
                            row.get("points", 0),
                            row.get("exact_scores", 0),
                            row.get("exact_diffs", 0),
                            row.get("outcomes", 0),
                        )
                        if key == prev_key and expected_place:
                            expected_place.append(expected_place[-1])
                        else:
                            expected_place.append(idx)
                        prev_key = key

                    actual_place = [r.get("place") for r in ranking]
                    if expected_place == actual_place:
                        ok("tie-place correctness (rank 1,1,3...)")
                    else:
                        fail("tie-place mismatch in ranking output")
                        failures += 1

                single_check = ensure_single_active_tournament()
                if single_check.get("ok"):
                    ok("single active tournament check")
                else:
                    warn(
                        f"multiple active tournaments detected: {single_check.get('active_count')}"
                    )
                    warnings += 1
            finally:
                close_db(conn, cur)
    except Exception as e:
        fail(f"DB/ranking smoke failed: {e}")
        failures += 1

    try:
        client = app.test_client()
        r1 = client.get("/health")
        if r1.status_code == 200:
            ok("/health endpoint")
        else:
            fail(f"/health status {r1.status_code}")
            failures += 1

        r2 = client.get("/health/db")
        if r2.status_code == 200:
            ok("/health/db endpoint")
        else:
            warn(f"/health/db status {r2.status_code}")
            warnings += 1
    except Exception as e:
        fail(f"health endpoints check failed: {e}")
        failures += 1

    print("-" * 48)
    print(f"Smoke result: failures={failures}, warnings={warnings}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
