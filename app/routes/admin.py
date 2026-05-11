# =========================================================
# RECALCULATE ALL
# =========================================================

@admin_bp.route('/recalc_all')
@admin_required
def recalc_all():

    conn = get_db()
    cur = conn.cursor()

    try:

        from app.models.scoring import calculate_points

        tournament_id = get_active_tournament_id(cur)

        if not tournament_id:
            flash("Активный турнир не найден", "error")
            return redirect(url_for('admin.admin'))

        cur.execute("""
            SELECT id,
                   home_score,
                   away_score
            FROM matches
            WHERE status = 'FINISHED'
        """)

        matches = cur.fetchall()

        total_updated = 0

        for match in matches:

            match_id = match[0]
            home_score = match[1]
            away_score = match[2]

            cur.execute("""
                UPDATE predictions
                SET points = 0
                WHERE match_id = %s
                AND tournament_id = %s
            """, (
                match_id,
                tournament_id
            ))

            cur.execute("""
                SELECT user_id,
                       home_goals,
                       away_goals
                FROM predictions
                WHERE match_id = %s
                AND tournament_id = %s
            """, (
                match_id,
                tournament_id
            ))

            predictions = cur.fetchall()

            for p in predictions:

                pts = calculate_points(
                    home_score,
                    away_score,
                    p[1],
                    p[2]
                )

                cur.execute("""
                    UPDATE predictions
                    SET points = %s
                    WHERE user_id = %s
                    AND match_id = %s
                    AND tournament_id = %s
                """, (
                    pts,
                    p[0],
                    match_id,
                    tournament_id
                ))

                total_updated += 1

        conn.commit()

        flash(
            f"Пересчитано {total_updated} прогнозов",
            "success"
        )

    except Exception as e:

        conn.rollback()

        flash(f"Ошибка пересчёта: {e}", "error")

    finally:
        close_db(conn, cur)

    return redirect(url_for('admin.admin'))


# =========================================================
# FORCE FINISH MATCH
# =========================================================

@admin_bp.route('/force_finish/<int:match_id>/<int:h>/<int:a>')
@admin_required
def force_finish(match_id, h, a):

    conn = get_db()
    cur = conn.cursor()

    try:

        if h < 0 or a < 0:
            flash("Счёт не может быть отрицательным", "error")
            return redirect(url_for('admin.admin'))

        from app.models.scoring import calculate_points

        tournament_id = get_active_tournament_id(cur)

        if not tournament_id:
            flash("Активный турнир не найден", "error")
            return redirect(url_for('admin.admin'))

        cur.execute("""
            UPDATE matches
            SET status = 'FINISHED',
                home_score = %s,
                away_score = %s
            WHERE id = %s
        """, (
            h,
            a,
            match_id
        ))

        if cur.rowcount == 0:
            flash("Матч не найден", "error")
            return redirect(url_for('admin.admin'))

        cur.execute("""
            UPDATE predictions
            SET points = 0
            WHERE match_id = %s
            AND tournament_id = %s
        """, (
            match_id,
            tournament_id
        ))

        cur.execute("""
            SELECT user_id,
                   home_goals,
                   away_goals
            FROM predictions
            WHERE match_id = %s
            AND tournament_id = %s
        """, (
            match_id,
            tournament_id
        ))

        predictions = cur.fetchall()

        for p in predictions:

            pts = calculate_points(
                h,
                a,
                p[1],
                p[2]
            )

            cur.execute("""
                UPDATE predictions
                SET points = %s
                WHERE user_id = %s
                AND match_id = %s
                AND tournament_id = %s
            """, (
                pts,
                p[0],
                match_id,
                tournament_id
            ))

        conn.commit()

        flash(
            f"Матч #{match_id} завершён: {h}:{a}",
            "success"
        )

    except Exception as e:

        conn.rollback()

        flash(f"Ошибка: {e}", "error")

    finally:
        close_db(conn, cur)

    return redirect(url_for('admin.admin'))


# =========================================================
# TRANSLATE TEAMS
# =========================================================

@admin_bp.route('/translate', methods=['POST'])
@admin_required
def admin_translate():

    conn = get_db()
    cur = conn.cursor()

    try:

        cur.execute("""
            SELECT id,
                   home_team,
                   away_team
            FROM matches
        """)

        matches = cur.fetchall()

        updated = 0

        for m in matches:

            new_home = translate_name(m[1])
            new_away = translate_name(m[2])

            if (
                new_home != m[1]
                or new_away != m[2]
            ):

                cur.execute("""
                    UPDATE matches
                    SET home_team = %s,
                        away_team = %s
                    WHERE id = %s
                """, (
                    new_home,
                    new_away,
                    m[0]
                ))

                updated += 1

        conn.commit()

        flash(
            f"Переведено {updated} матчей",
            "success"
        )

    except Exception as e:

        conn.rollback()

        flash(f"Ошибка перевода: {e}", "error")

    finally:
        close_db(conn, cur)

    return redirect(url_for('admin.admin'))


# =========================================================
# FIX RESULT
# =========================================================

@admin_bp.route('/fix_result', methods=['POST'])
@admin_required
def admin_fix_result():

    match_id = request.form.get('match_id')

    home_score, away_score = validate_score(
        request.form.get('home_score'),
        request.form.get('away_score')
    )

    if home_score is None:
        flash("Некорректный счёт", "error")
        return redirect(url_for('admin.admin'))

    conn = get_db()
    cur = conn.cursor()

    try:

        tournament_id = get_active_tournament_id(cur)

        if not tournament_id:
            flash("Активный турнир не найден", "error")
            return redirect(url_for('admin.admin'))

        cur.execute("""
            UPDATE matches
            SET home_score = %s,
                away_score = %s
            WHERE id = %s
        """, (
            home_score,
            away_score,
            match_id
        ))

        if cur.rowcount == 0:
            flash("Матч не найден", "error")
            return redirect(url_for('admin.admin'))

        from app.models.scoring import calculate_points

        cur.execute("""
            SELECT user_id,
                   home_goals,
                   away_goals
            FROM predictions
            WHERE match_id = %s
            AND tournament_id = %s
        """, (
            match_id,
            tournament_id
        ))

        predictions = cur.fetchall()

        for p in predictions:

            pts = calculate_points(
                home_score,
                away_score,
                p[1],
                p[2]
            )

            cur.execute("""
                UPDATE predictions
                SET points = %s
                WHERE user_id = %s
                AND match_id = %s
                AND tournament_id = %s
            """, (
                pts,
                p[0],
                match_id,
                tournament_id
            ))

        conn.commit()

        flash(
            f"Результат обновлён: {home_score}:{away_score}",
            "success"
        )

    except Exception as e:

        conn.rollback()

        flash(f"Ошибка: {e}", "error")

    finally:
        close_db(conn, cur)

    return redirect(url_for('admin.admin'))


# =========================================================
# EDIT MATCH
# =========================================================

@admin_bp.route('/edit_match', methods=['POST'])
@admin_required
def admin_edit_match():

    match_id = request.form.get('match_id')
    home_team = request.form.get('home_team', '').strip()
    away_team = request.form.get('away_team', '').strip()

    if not match_id or not home_team or not away_team:

        flash("Заполните все поля", "error")

        return redirect(url_for('admin.admin'))

    conn = get_db()
    cur = conn.cursor()

    try:

        cur.execute("""
            UPDATE matches
            SET home_team = %s,
                away_team = %s
            WHERE id = %s
        """, (
            home_team,
            away_team,
            match_id
        ))

        if cur.rowcount == 0:
            flash("Матч не найден", "error")
            return redirect(url_for('admin.admin'))

        conn.commit()

        flash(
            f"Матч #{match_id} обновлён",
            "success"
        )

    except Exception as e:

        conn.rollback()

        flash(f"Ошибка: {e}", "error")

    finally:
        close_db(conn, cur)

    return redirect(url_for('admin.admin'))


# =========================================================
# DELETE MATCH
# =========================================================

@admin_bp.route('/delete_match', methods=['POST'])
@admin_required
def admin_delete_match():

    match_id = request.form.get('match_id')

    if not match_id:

        flash("Не указан match_id", "error")

        return redirect(url_for('admin.admin'))

    conn = get_db()
    cur = conn.cursor()

    try:

        cur.execute("""
            DELETE FROM predictions
            WHERE match_id = %s
        """, (match_id,))

        cur.execute("""
            DELETE FROM matches
            WHERE id = %s
        """, (match_id,))

        if cur.rowcount == 0:
            flash("Матч не найден", "error")
            return redirect(url_for('admin.admin'))

        conn.commit()

        flash(
            f"Матч #{match_id} удалён",
            "success"
        )

    except Exception as e:

        conn.rollback()

        flash(f"Ошибка удаления: {e}", "error")

    finally:
        close_db(conn, cur)

    return redirect(url_for('admin.admin'))


# =========================================================
# NEW TOURNAMENT
# =========================================================

@admin_bp.route('/new_tournament', methods=['POST'])
@admin_required
def admin_new_tournament():

    name = request.form.get('name', '').strip()
    start_date = request.form.get('start_date')

    if not name:

        flash("Введите название турнира", "error")

        return redirect(url_for('admin.admin'))

    conn = get_db()
    cur = conn.cursor()

    try:

        cur.execute("""
            SELECT id
            FROM tournaments
            WHERE LOWER(name) = LOWER(%s)
        """, (name,))

        existing = cur.fetchone()

        if existing:

            flash("Турнир с таким названием уже существует", "error")

            return redirect(url_for('admin.admin'))

        cur.execute("""
            UPDATE tournaments
            SET is_active = 0
            WHERE is_active = 1
        """)

        cur.execute("""
            INSERT INTO tournaments (
                name,
                is_active,
                start_date
            )
            VALUES (%s, 1, %s)
        """, (
            name,
            start_date
        ))

        conn.commit()

        flash(
            f"Турнир «{name}» создан",
            "success"
        )

    except Exception as e:

        conn.rollback()

        flash(f"Ошибка создания турнира: {e}", "error")

    finally:
        close_db(conn, cur)

    return redirect(url_for('admin.admin'))