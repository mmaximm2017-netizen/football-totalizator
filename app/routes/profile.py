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
from app.services.ranking_service import get_tournament_ranking
from app.services.tournament_context_service import (
    get_selected_tournament_id,
    get_tournament_state_flags,
)
from app.services.tournament_service import (
    get_all_tournaments,
    get_tournament_by_id,
)
from app.utils import get_club_logo, get_flag


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
            SELECT COUNT(*)
            FROM users u
            JOIN tournaments t ON t.id = %s
            WHERE u.is_admin = 0
              AND (t.is_active = 0 OR COALESCE(u.is_deleted, 0) = 0)
            """
            ,
            (tournament_id,),
        )
        total_players = cur.fetchone()[0] or 0

        cur.execute(
            """
            SELECT
                COUNT(*) AS total_bets,
                COALESCE(SUM(CASE WHEN p.points >= 10 THEN 1 ELSE 0 END), 0),
                COALESCE(SUM(CASE WHEN p.points BETWEEN 7 AND 8 THEN 1 ELSE 0 END), 0),
                COALESCE(SUM(CASE WHEN p.points = 3 THEN 1 ELSE 0 END), 0),
                COALESCE(SUM(CASE WHEN p.points = 5 THEN 1 ELSE 0 END), 0),
                COALESCE(SUM(CASE WHEN p.points = 0 THEN 1 ELSE 0 END), 0),
                ROUND(COALESCE(AVG(p.points), 0), 1),
                COALESCE(SUM(p.points), 0)
            FROM predictions p
            JOIN matches m ON p.match_id = m.id
                         AND p.tournament_id = m.tournament_id
            WHERE p.user_id = %s
              AND p.tournament_id = %s
              AND m.status = 'FINISHED'
            """,
            (uid, tournament_id),
        )

        row = cur.fetchone() or (0, 0, 0, 0, 0, 0, 0, 0)
        stats = {
            'total_bets': row[0],
            'exact_scores': row[1],
            'exact_diffs': row[2],
            'outcomes': row[3],
            'close_misses': row[4],
            'misses': row[5],
            'avg_points': float(row[6]),
            'total_points': row[7],
        }

        cur.execute(
            """
            SELECT
                m.home_team,
                m.away_team,
                p.home_goals,
                p.away_goals,
                m.home_score,
                m.away_score,
                p.points
            FROM predictions p
            JOIN matches m ON p.match_id = m.id
                         AND p.tournament_id = m.tournament_id
            WHERE p.user_id = %s
              AND p.tournament_id = %s
              AND m.status = 'FINISHED'
            ORDER BY m.kickoff_time DESC
            LIMIT 10
            """,
            (uid, tournament_id),
        )

        recent = [
            {
                'home_team': r[0],
                'away_team': r[1],
                'home_goals': r[2],
                'away_goals': r[3],
                'home_score': r[4],
                'away_score': r[5],
                'points': r[6],
            }
            for r in cur.fetchall()
        ]

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
        stats=stats,
        recent=recent,
        titles=titles,
        current_place=current_place,
        total_players=total_players,
        get_flag=get_flag,
        get_club_logo=get_club_logo,
        tournaments=tournaments,
        active_tournaments=active_tournaments,
        **tournament_state,
        current_tournament_id=tournament_id,
        current_tournament_name=current_tournament_name,
        position_metric=position_metric,
    )
