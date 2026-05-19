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
        from app.db import get_db, get_active_tournament_id, close_db
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
            finally:
                close_db(conn, cur)
    except Exception as e:
        fail(f"DB/ranking smoke failed: {e}")
        failures += 1

    print("-" * 48)
    print(f"Smoke result: failures={failures}, warnings={warnings}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
