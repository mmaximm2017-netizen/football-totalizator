# app/routes/profile.py

from flask import (
    Blueprint,
    abort,
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
from app.utils import cached_to_msk

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


def _public_user(user_id):
    conn = cur = None
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, username
            FROM users
            WHERE id = %s
              AND is_admin = 0
              AND COALESCE(is_deleted, 0) = 0
            """,
            (user_id,),
        )
        user = cur.fetchone()
        if not user:
            abort(404)
        cur.execute(
            "SELECT title, awarded_at FROM user_titles WHERE user_id = %s ORDER BY awarded_at DESC",
            (user_id,),
        )
        return {
            "id": user[0],
            "username": user[1],
            "titles": [{"title": row[0], "awarded_at": row[1]} for row in cur.fetchall()],
        }
    finally:
        if conn is not None:
            close_db(conn, cur)


def _profile_tournament_context():
    tournaments = get_all_tournaments()
    tournament_id = get_selected_tournament_id(request.args.get("tid", type=int))
    if not tournament_id:
        abort(404)
    selected_tournament = get_tournament_by_id(tournament_id)
    return {
        "tournaments": tournaments,
        "active_tournaments": [item for item in tournaments if item.get("is_active")],
        "tournament_id": tournament_id,
        "tournament_state": get_tournament_state_flags(tournaments),
        "tournament_name": selected_tournament["name"] if selected_tournament else "Турнир",
    }


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
            viewer_user_id = session.get("user_id")
            if not viewer_user_id:
                return redirect(url_for("auth.login"))
            if str(viewer_user_id) != str(uid):
                return redirect(
                    url_for(
                        "profile.public_profile",
                        user_id=uid,
                        tid=request.args.get("tid", type=int),
                    )
                )
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

        all_tournaments = get_all_tournaments(cur=cur)
        active_tournaments = [t for t in all_tournaments if t.get("is_active")]
        tournament_state = get_tournament_state_flags(all_tournaments)

        tournament_id = get_selected_tournament_id(request.args.get('tid', type=int), cur=cur)
        if not tournament_id:
            flash("Активный турнир не найден", "error")
            return redirect(url_for('table.table'))

        selected_tournament = get_tournament_by_id(tournament_id, cur=cur)
        if user_is_deleted == 1 and selected_tournament and selected_tournament.get("is_active"):
            flash("Игрок не участвует в активном турнире", "error")
            return redirect(url_for('table.table', tid=tournament_id))

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
        ranking = get_tournament_ranking(tournament_id, cur=cur)
    finally:
        close_db(conn, cur)

    tournaments = all_tournaments
    current_tournament_name = selected_tournament["name"] if selected_tournament else "Турнир"
    user_row = next((row for row in ranking if str(row.get("user_id")) == str(uid)), None)
    current_place = user_row["place"] if user_row else None
    position_metric = build_profile_position_metric(ranking, uid)

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


@profile_bp.route('/profile/<int:user_id>')
def public_profile(user_id):
    viewer_user_id = session.get("user_id")
    if not viewer_user_id:
        return redirect(url_for("auth.login"))
    context = _profile_tournament_context()
    if str(viewer_user_id) == str(user_id):
        return redirect(url_for("profile.profile", tid=context["tournament_id"]))
    user = _public_user(user_id)
    ranking = get_tournament_ranking(context["tournament_id"])
    row = next((item for item in ranking if str(item.get("user_id")) == str(user_id)), None)
    return render_template(
        "profile.html",
        username=user["username"],
        profile_subject_username=user["username"],
        public_profile_user_id=user_id,
        public_tournament_switch_base=url_for("profile.public_profile", user_id=user_id),
        is_own_profile=False,
        titles=user["titles"],
        current_place=row["place"] if row else None,
        position_metric=build_profile_position_metric(ranking, user_id),
        current_tournament_id=context["tournament_id"],
        current_tournament_name=context["tournament_name"],
        tournaments=context["tournaments"],
        active_tournaments=context["active_tournaments"],
        **context["tournament_state"],
    )


@profile_bp.route('/profile/stats')
def profile_stats():
    uid = session.get('user_id')
    if not uid:
        flash("Сессия не найдена", "error")
        return redirect(url_for('auth.login'))

    conn = get_db()
    cur = conn.cursor()
    try:
        all_tournaments = get_all_tournaments(cur=cur)
        active_tournaments = [t for t in all_tournaments if t.get("is_active")]
        tournament_state = get_tournament_state_flags(all_tournaments)
        tournament_id = get_selected_tournament_id(request.args.get('tid', type=int), cur=cur)
        if not tournament_id:
            flash("Активный турнир не найден", "error")
            return redirect(url_for('table.table'))

        selected_tournament = get_tournament_by_id(tournament_id, cur=cur)
        ranking = get_tournament_ranking(tournament_id, cur=cur)
        user_row = next((row for row in ranking if str(row.get("user_id")) == str(uid)), None)

        cur.execute(
            "SELECT username FROM users WHERE id = %s AND COALESCE(is_deleted, 0) = 0",
            (uid,),
        )
        row = cur.fetchone()
        if not row:
            flash("Пользователь не найден", "error")
            return redirect(url_for('auth.login'))

        stats = get_profile_stats(uid, tournament_id, cur=cur)
    except ProfileStatsIntegrityError:
        flash("Статистика временно недоступна: обнаружены некорректные данные очков", "error")
        return redirect(url_for('profile.profile', tid=tournament_id))
    finally:
        close_db(conn, cur)

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
        format_profile_points=format_profile_points,
    )


@profile_bp.route('/profile/<int:user_id>/stats')
def public_profile_stats(user_id):
    viewer_user_id = session.get("user_id")
    if not viewer_user_id:
        return redirect(url_for("auth.login"))
    context = _profile_tournament_context()
    if str(viewer_user_id) == str(user_id):
        return redirect(url_for("profile.profile_stats", tid=context["tournament_id"]))
    user = _public_user(user_id)
    ranking = get_tournament_ranking(context["tournament_id"])
    row = next((item for item in ranking if str(item.get("user_id")) == str(user_id)), None)
    try:
        stats = get_profile_stats(user_id, context["tournament_id"])
    except ProfileStatsIntegrityError:
        abort(500)
    return render_template(
        "profile_stats.html",
        username=user["username"],
        stats=stats,
        current_place=row["place"] if row else None,
        current_tournament_id=context["tournament_id"],
        current_tournament_name=context["tournament_name"],
        tournaments=context["tournaments"],
        active_tournaments=context["active_tournaments"],
        public_profile_user_id=user_id,
        public_tournament_switch_base=url_for("profile.public_profile_stats", user_id=user_id),
        is_own_profile=False,
        format_profile_points=format_profile_points,
        **context["tournament_state"],
    )


@profile_bp.route('/profile/<int:user_id>/predictions')
def public_profile_predictions(user_id):
    viewer_user_id = session.get("user_id")
    if not viewer_user_id:
        return redirect(url_for("auth.login"))
    context = _profile_tournament_context()
    if str(viewer_user_id) == str(user_id):
        return redirect(url_for("predictions.my_predictions", tid=context["tournament_id"]))
    user = _public_user(user_id)
    conn = cur = None
    try:
        conn = get_db()
        cur = conn.cursor()
        # Public history is intentionally finished-only and cannot load live bets.
        cur.execute(
            """
            SELECT m.id, m.kickoff_time, m.home_team, m.away_team, m.home_score,
                   m.away_score, p.home_goals, p.away_goals, COALESCE(p.points, 0)
            FROM predictions p
            JOIN matches m ON m.id = p.match_id AND m.tournament_id = p.tournament_id
            WHERE p.user_id = %s
              AND p.tournament_id = %s
              AND m.status = 'FINISHED'
            ORDER BY m.kickoff_time DESC, m.id DESC
            """,
            (user_id, context["tournament_id"]),
        )
        finished = [
            {
                "id": row[0], "date": cached_to_msk(row[1]), "home_team": row[2],
                "away_team": row[3], "home_score": row[4], "away_score": row[5],
                "home_goals": row[6], "away_goals": row[7], "points": row[8],
            }
            for row in cur.fetchall()
        ]
    finally:
        if conn is not None:
            close_db(conn, cur)
    return render_template(
        "my_predictions.html",
        finished=finished,
        current_filter="finished",
        current_tournament_id=context["tournament_id"],
        current_tournament_name=context["tournament_name"],
        tournaments=context["tournaments"],
        active_tournaments=context["active_tournaments"],
        profile_subject_username=user["username"],
        public_profile_user_id=user_id,
        public_tournament_switch_base=url_for("profile.public_profile_predictions", user_id=user_id),
        is_public_history=True,
        to_msk=cached_to_msk,
        **context["tournament_state"],
    )
