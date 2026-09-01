# app/routes/profile.py

from flask import (
    Blueprint,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from app.db import close_db, get_db
from app.services.profile_stats_service import (
    ProfileStatsIntegrityError,
    get_profile_stats,
)
from app.services.ranking_service import get_tournament_ranking
from app.services.tournament_context_service import (
    get_selected_tournament_id,
    get_tournament_state_flags,
)
from app.services.tournament_service import (
    get_all_tournaments,
    get_tournament_by_id,
)

profile_bp = Blueprint('profile', __name__)


def format_profile_points(points):
    value = abs(int(points))
    if value % 10 == 1 and value % 100 != 11:
        word = "очко"
    elif value % 10 in (2, 3, 4) and value % 100 not in (12, 13, 14):
        word = "очка"
    else:
        word = "очков"
    return f"{value} {word}"


def build_profile_position_metric(ranking, user_id):
    if not ranking:
        return None

    def numeric_points(row):
        try:
            return int(row.get("points"))
        except (TypeError, ValueError):
            return None

    try:
        target_id = int(user_id)
    except (TypeError, ValueError):
        return None

    target = next((row for row in ranking if row.get("user_id") == target_id), None)
    if target is None:
        target = next((row for row in ranking if str(row.get("user_id")) == str(user_id)), None)
    if target is None or target.get("place") is None:
        return None

    target_points = numeric_points(target)
    leader_points = numeric_points(ranking[0])
    if target_points is None or leader_points is None:
        return None

    if str(ranking[0].get("user_id")) == str(user_id):
        if len(ranking) < 2:
            return None
        next_points = numeric_points(ranking[1])
        if next_points is None:
            return None
        points = max(0, leader_points - next_points)
        return {"kind": "lead", "label": "Преимущество", "points": points, "points_text": format_profile_points(points)}

    points = max(0, leader_points - target_points)
    return {"kind": "gap", "label": "До лидера", "points": points, "points_text": format_profile_points(points)}


@profile_bp.route('/profile')
def profile():
    conn = get_db()
    cur = conn.cursor()

    try:
        requested_username = request.args.get('username')

        if requested_username:
            cur.execute(
                """
                SELECT id, username, COALESCE(is_deleted, 0)
                FROM users
                WHERE username = %s
                """,
                (requested_username,),
            )
            row = cur.fetchone()
            if not row:
                flash("Игрок не найден", "error")
                return redirect(url_for('table.table'))
            uid = row[0]
            username = row[1] if len(row) > 2 else requested_username
            user_is_deleted = row[2] if len(row) > 2 else row[1]
        else:
            uid = session.get('user_id')
            if not uid:
                flash("Сессия не найдена", "error")
                return redirect(url_for('auth.login'))

            cur.execute(
                """
                SELECT username, COALESCE(is_deleted, 0)
                FROM users
                WHERE id = %s
                """,
                (uid,),
            )
            row = cur.fetchone()
            if not row:
                flash("Пользователь не найден", "error")
                return redirect(url_for('auth.login'))
            username = row[0]
            user_is_deleted = row[1] if len(row) > 1 else 0

        viewer_user_id = session.get("user_id")
        try:
            is_own_profile = viewer_user_id is not None and int(viewer_user_id) == int(uid)
        except (TypeError, ValueError):
            is_own_profile = False
        profile_subject_username = username

        all_tournaments = get_all_tournaments()
        active_tournaments = [t for t in all_tournaments if t.get("is_active")]
        tournament_state = get_tournament_state_flags(all_tournaments)

        tournament_id = get_selected_tournament_id(request.args.get('tid', type=int))
        if not tournament_id:
            flash("Активный турнир не найден", "error")
            return redirect(url_for('table.table'))

        selected_tournament = get_tournament_by_id(tournament_id)
        if user_is_deleted == 1 and selected_tournament and selected_tournament.get("is_active"):
            flash("Игрок не участвует в активном турнире", "error")
            return redirect(url_for('table.table', tid=tournament_id))

        ranking = get_tournament_ranking(tournament_id)
        user_row = next((r for r in ranking if str(r.get('user_id')) == str(uid)), None)
        current_place = user_row['place'] if user_row else None
        position_metric = build_profile_position_metric(ranking, uid)

        cur.execute(
            """
            SELECT title, awarded_at
            FROM user_titles
            WHERE user_id = %s
            ORDER BY awarded_at DESC
            """,
            (uid,),
        )
        titles = [{'title': r[0], 'awarded_at': r[1]} for r in cur.fetchall()]
    finally:
        close_db(conn, cur)

    tournaments = all_tournaments
    current_tournament_name = selected_tournament["name"] if selected_tournament else "Турнир"

    return render_template(
        'profile.html',
        username=username,
        profile_subject_username=profile_subject_username,
        is_own_profile=is_own_profile,
        titles=titles,
        current_place=current_place,
        tournaments=tournaments,
        active_tournaments=active_tournaments,
        **tournament_state,
        current_tournament_id=tournament_id,
        current_tournament_name=current_tournament_name,
        position_metric=position_metric,
    )


@profile_bp.route('/profile/stats')
def profile_stats():
    uid = session.get('user_id')
    if not uid:
        flash("Сессия не найдена", "error")
        return redirect(url_for('auth.login'))

    all_tournaments = get_all_tournaments()
    active_tournaments = [t for t in all_tournaments if t.get("is_active")]
    tournament_state = get_tournament_state_flags(all_tournaments)
    tournament_id = get_selected_tournament_id(request.args.get('tid', type=int))
    if not tournament_id:
        flash("Активный турнир не найден", "error")
        return redirect(url_for('table.table'))

    selected_tournament = get_tournament_by_id(tournament_id)
    ranking = get_tournament_ranking(tournament_id)
    user_row = next((row for row in ranking if str(row.get("user_id")) == str(uid)), None)

    conn = cur = None
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            "SELECT username FROM users WHERE id = %s AND COALESCE(is_deleted, 0) = 0",
            (uid,),
        )
        row = cur.fetchone()
    finally:
        if conn is not None:
            close_db(conn, cur)
    if not row:
        flash("Пользователь не найден", "error")
        return redirect(url_for('auth.login'))

    try:
        stats = get_profile_stats(uid, tournament_id)
    except ProfileStatsIntegrityError:
        flash("Статистика временно недоступна: обнаружены некорректные данные очков", "error")
        return redirect(url_for('profile.profile', tid=tournament_id))

    return render_template(
        'profile_stats.html',
        username=row[0],
        stats=stats,
        current_place=user_row['place'] if user_row else None,
        tournaments=all_tournaments,
        active_tournaments=active_tournaments,
        **tournament_state,
        current_tournament_id=tournament_id,
        current_tournament_name=selected_tournament['name'] if selected_tournament else 'Турнир',
        is_own_profile=True,
    )
