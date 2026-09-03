from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path, old, new):
    text = path.read_text(encoding="utf-8-sig")
    if old not in text:
        raise RuntimeError(f"anchor not found in {path}: {old[:80]!r}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


# main.py: keep HTTP/deadline handling in route, move atomic write SQL to service.
main = ROOT / "app/routes/main.py"
replace_once(
    main,
    "from app.services.home_match_view_service import apply_home_match_card_state\n",
    "from app.services.home_match_view_service import apply_home_match_card_state\nfrom app.services.prediction_write_service import save_prediction_before_deadline\n",
)
replace_once(
    main,
    '''            cur.execute("""\n                INSERT INTO predictions\n                    (user_id, match_id, tournament_id, home_goals, away_goals)\n                SELECT %s, %s, %s, %s, %s\n                WHERE (\n                    SELECT CURRENT_TIMESTAMP < m.deadline\n                    FROM matches m\n                    WHERE m.id = %s\n                )\n                ON CONFLICT (user_id, match_id, tournament_id)\n                DO UPDATE SET\n                    home_goals = EXCLUDED.home_goals,\n                    away_goals = EXCLUDED.away_goals\n                WHERE (\n                    SELECT CURRENT_TIMESTAMP < m.deadline\n                    FROM matches m\n                    WHERE m.id = %s\n                )\n                RETURNING 1\n            """, (\n                session['user_id'],\n                match_id,\n                match_tid,\n                h,\n                a,\n                match_id,\n                match_id,\n            ))\n\n            inserted_row = cur.fetchone()\n            conn.commit()\n\n            if not inserted_row:\n''',
    '''            saved = save_prediction_before_deadline(\n                cur,\n                session['user_id'],\n                match_id,\n                match_tid,\n                h,\n                a,\n            )\n            conn.commit()\n\n            if not saved:\n''',
)

# auth.py: route owns HTTP/session flow; service owns user persistence SQL.
auth = ROOT / "app/routes/auth.py"
replace_once(
    auth,
    "from app.services.login_rate_limit_service import (\n",
    "from app.services.auth_user_service import create_user, get_auth_user, upgrade_user_password_hash\nfrom app.services.login_rate_limit_service import (\n",
)
replace_once(
    auth,
    '''            cur.execute(\n                """\n                SELECT id, password, COALESCE(is_deleted, 0)\n                FROM users\n                WHERE username = %s\n                """,\n                (username,),\n            )\n            user = cur.fetchone()\n''',
    '''            user = get_auth_user(cur, username)\n''',
)
replace_once(
    auth,
    '''                    cur.execute(\n                        """\n                        UPDATE users\n                        SET password = %s\n                        WHERE id = %s\n                        """,\n                        (new_hash, user_id),\n                    )\n''',
    '''                    upgrade_user_password_hash(cur, user_id, new_hash)\n''',
)
replace_once(
    auth,
    '''            cur.execute(\n                """\n                INSERT INTO users (username, password)\n                VALUES (%s, %s)\n                """,\n                (username, password_hash),\n            )\n''',
    '''            create_user(cur, username, password_hash)\n''',
)

# admin_actions.py: validation/flash/transactions stay in HTTP layer, CRUD SQL moves to service.
actions = ROOT / "app/routes/admin_actions.py"
replace_once(
    actions,
    "from app.routes.admin_sync import handle_manual_sync_update\n",
    "from app.routes.admin_sync import handle_manual_sync_update\nfrom app.services.admin_title_service import (\n    award_title,\n    get_title_target_admin_flag,\n    remove_title,\n    replace_title,\n)\n",
)
replace_once(
    actions,
    '''    cur.execute(\n        """\n        SELECT is_admin\n        FROM users\n        WHERE id = %s\n        """,\n        (user_id,),\n    )\n    row = cur.fetchone()\n    if not row:\n''',
    '''    is_admin = get_title_target_admin_flag(cur, user_id)\n    if is_admin is None:\n''',
)
replace_once(actions, "    if row[0] == 1:\n", "    if is_admin == 1:\n")
replace_once(
    actions,
    '''        cur.execute(\n            """\n            INSERT INTO user_titles (user_id, title, awarded_by)\n            VALUES (%s, %s, %s)\n            ON CONFLICT (user_id, title) DO NOTHING\n            """,\n            (user_id, title, session.get("user_id")),\n        )\n\n        if cur.rowcount == 0:\n''',
    '''        created = award_title(cur, user_id, title, session.get("user_id"))\n\n        if not created:\n''',
)
replace_once(
    actions,
    '''        cur.execute(\n            "DELETE FROM user_titles WHERE user_id = %s AND title = %s",\n            (user_id, old_title),\n        )\n        if cur.rowcount == 0:\n''',
    '''        replaced = replace_title(\n            cur, user_id, old_title, title, session.get("user_id")\n        )\n        if not replaced:\n''',
)
replace_once(
    actions,
    '''\n        cur.execute(\n            "INSERT INTO user_titles (user_id, title, awarded_by) VALUES (%s, %s, %s)",\n            (user_id, title, session.get("user_id")),\n        )\n''',
    '''\n''',
)
replace_once(
    actions,
    '''        cur.execute(\n            "DELETE FROM user_titles WHERE user_id = %s AND title = %s",\n            (user_id, title),\n        )\n        if cur.rowcount == 0:\n''',
    '''        removed = remove_title(cur, user_id, title)\n        if not removed:\n''',
)

# admin_tournaments.py: move mutation-heavy batches/lifecycle SQL to one service.
tournaments = ROOT / "app/routes/admin_tournaments.py"
replace_once(
    tournaments,
    "from app.routes.admin_common import admin_required\n",
    "from app.routes.admin_common import admin_required\nfrom app.services.admin_tournament_mutation_service import (\n    create_tournament,\n    delete_archived_tournament,\n    set_tournament_active,\n    translate_match_names,\n)\n",
)
start = '''        cur.execute(\n            """\n            SELECT id,\n                   home_team,\n                   away_team\n            FROM matches\n            """\n        )\n\n        matches = cur.fetchall()\n\n        updated = 0\n\n        for m in matches:\n            new_home = translate_name(m[1])\n            new_away = translate_name(m[2])\n\n            if (\n                new_home != m[1]\n                or new_away != m[2]\n            ):\n                cur.execute(\n                    """\n                    UPDATE matches\n                    SET home_team = %s,\n                        away_team = %s\n                    WHERE id = %s\n                    """,\n                    (\n                        new_home,\n                        new_away,\n                        m[0],\n                    ),\n                )\n\n                updated += 1\n'''
replace_once(tournaments, start, '''        updated = translate_match_names(cur, translate_name)\n''')
replace_once(
    tournaments,
    '''        cur.execute(\n            """\n            SELECT id\n            FROM tournaments\n            WHERE LOWER(name) = LOWER(%s)\n            """,\n            (name,),\n        )\n\n        existing = cur.fetchone()\n\n        if existing:\n''',
    '''        created = create_tournament(cur, name, start_date)\n\n        if not created:\n''',
)
replace_once(
    tournaments,
    '''\n        cur.execute(\n            """\n            INSERT INTO tournaments (\n                name,\n                is_active,\n                start_date\n            )\n            VALUES (%s, 1, %s)\n            """,\n            (\n                name,\n                start_date,\n            ),\n        )\n''',
    '''\n''',
)
replace_once(
    tournaments,
    '''        cur.execute(\n            """\n            UPDATE tournaments\n            SET is_active = 0\n            WHERE id = %s\n            """,\n            (tid,),\n        )\n\n        if cur.rowcount == 0:\n''',
    '''        updated = set_tournament_active(cur, tid, False)\n\n        if not updated:\n''',
)
replace_once(
    tournaments,
    '''        cur.execute(\n            """\n            UPDATE tournaments\n            SET is_active = 1\n            WHERE id = %s\n            """,\n            (tid,),\n        )\n\n        if cur.rowcount == 0:\n''',
    '''        updated = set_tournament_active(cur, tid, True)\n\n        if not updated:\n''',
)
old_delete = '''        cur.execute(\n            """\n            SELECT is_active\n            FROM tournaments\n            WHERE id = %s\n            """,\n            (tid,),\n        )\n\n        row = cur.fetchone()\n\n        if not row:\n            flash("Турнир не указан", "error")\n            return redirect(url_for("admin.admin_tournaments"))\n\n        if row[0] == 1:\n            flash("Нельзя удалить активный турнир", "error")\n            return redirect(url_for("admin.admin_tournaments"))\n\n        cur.execute(\n            "SELECT 1 FROM predictions WHERE tournament_id = %s LIMIT 1",\n            (tid,),\n        )\n        if cur.fetchone():\n            flash("Нельзя удалить турнир: существуют связанные прогнозы", "error")\n            return redirect(url_for("admin.admin_tournaments"))\n\n        cur.execute(\n            """\n            DELETE FROM tournaments\n            WHERE id = %s\n            """,\n            (tid,),\n        )\n'''
new_delete = '''        delete_status = delete_archived_tournament(cur, tid)\n        if delete_status == "missing":\n            flash("Турнир не указан", "error")\n            return redirect(url_for("admin.admin_tournaments"))\n        if delete_status == "active":\n            flash("Нельзя удалить активный турнир", "error")\n            return redirect(url_for("admin.admin_tournaments"))\n        if delete_status == "has_predictions":\n            flash("Нельзя удалить турнир: существуют связанные прогнозы", "error")\n            return redirect(url_for("admin.admin_tournaments"))\n'''
replace_once(tournaments, old_delete, new_delete)

print("Risky route SQL extracted into services")
