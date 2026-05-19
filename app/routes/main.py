# app/routes/main.py

from datetime import datetime, timezone
from collections import defaultdict
from zoneinfo import ZoneInfo

from flask import (
    Blueprint,
    g,
    render_template,
    request,
    redirect,
    url_for,
    flash,
    session,
    jsonify
)

from app.db import get_db, close_db
from app.utils import get_flag, get_club_logo, cached_to_msk, is_before_deadline, format_date_ru
from app.config import START_DATE

main_bp = Blueprint('main', __name__)

MSK = ZoneInfo("Europe/Moscow")


# =========================================================
# HELPERS
# =========================================================

def parse_dt(value):
    if not value:
        return None

    if isinstance(value, datetime):
        return value

    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def is_ajax_request():
    return request.headers.get("X-Requested-With") == "XMLHttpRequest"


def ajax_error(message, status=400):
    return jsonify({
        "ok": False,
        "message": message
    }), status


def ajax_success(message, data=None):
    payload = {
        "ok": True,
        "message": message
    }

    if data:
        payload.update(data)

    return jsonify(payload)


# =========================================================
# INDEX
# =========================================================

@main_bp.route('/', methods=['GET', 'POST'])
def index():

    if 'user_id' not in session:
        if is_ajax_request():
            return ajax_error("Нужно войти в аккаунт", 401)

        return redirect(url_for('auth.login'))

    conn = get_db()
    cur = conn.cursor()

    try:

        league = request.args.get('league', 'all')

        start = START_DATE.strftime("%Y-%m-%dT%H:%M:%S")

        # =================================================
        # SAVE PREDICTION
        # =================================================

        if request.method == 'POST':
            if getattr(g, "is_admin", False):
                if is_ajax_request():
                    return ajax_error("Админ не участвует в ставках", 403)

                flash("Админ не участвует в ставках", "error")
                return redirect(url_for('main.index'))

            match_id = request.form.get('match_id')
            home_raw = request.form.get('home_goals')
            away_raw = request.form.get('away_goals')

            if not match_id or home_raw is None or away_raw is None:
                if is_ajax_request():
                    return ajax_error("Не хватает данных прогноза", 400)

                flash("Не хватает данных прогноза", "error")
                return redirect(url_for('main.index'))

            try:
                h = int(str(home_raw).strip())
                a = int(str(away_raw).strip())

                if h < 0 or a < 0 or h > 99 or a > 99:
                    raise ValueError

            except Exception:
                if is_ajax_request():
                    return ajax_error("Некорректный счёт")

                flash("Некорректный счёт", "error")
                return redirect(url_for('main.index'))

            cur.execute("""
SELECT
    id,
    home_team,
    away_team,
    kickoff_time,
    deadline,
    status,
    (
        SELECT t.id
        FROM tournaments t
        WHERE t.start_date::date <= DATE(matches.kickoff_time)
        ORDER BY t.start_date DESC, t.id DESC
        LIMIT 1
    ) AS tournament_id
FROM matches
WHERE id = %s
            """, (match_id,))

            match = cur.fetchone()

            if not match:
                if is_ajax_request():
                    return ajax_error("Матч не найден", 404)

                flash("Матч не найден", "error")
                return redirect(url_for('main.index'))

            tid = match[6]

            if not is_before_deadline({
                "deadline": match[4]
            }):
                if is_ajax_request():
                    return ajax_error("Дедлайн прошёл")

                flash("Дедлайн прошёл", "error")
                return redirect(url_for('main.index'))

            cur.execute("""
                SELECT 1 FROM predictions
                WHERE user_id=%s AND match_id=%s AND tournament_id=%s
            """, (
                session['user_id'],
                match_id,
                tid
            ))

            exists = cur.fetchone()

            if exists:
                cur.execute("""
                    UPDATE predictions
                    SET home_goals=%s, away_goals=%s
                    WHERE user_id=%s AND match_id=%s AND tournament_id=%s
                """, (
                    h,
                    a,
                    session['user_id'],
                    match_id,
                    tid
                ))
            else:
                cur.execute("""
                    INSERT INTO predictions (
                        user_id, match_id, tournament_id,
                        home_goals, away_goals
                    )
                    VALUES (%s,%s,%s,%s,%s)
                """, (
                    session['user_id'],
                    match_id,
                    tid,
                    h,
                    a
                ))

            conn.commit()

            if is_ajax_request():
                return ajax_success(
                    "������� �������",
                    {
                        "match_id": int(match_id),
                        "home_goals": h,
                        "away_goals": a
                    }
                )

            flash("Ставка сохранена", "success")

            return redirect(url_for('main.index', league=league))

        # =================================================
        # LOAD MATCHES
        # =================================================

        cur.execute("""
            SELECT id, home_team, away_team,
                   kickoff_time, deadline,
                   status, league,
                   home_score, away_score
            FROM matches
            WHERE status IN ('SCHEDULED','TIMED','FINISHED')
            AND kickoff_time >= %s
            ORDER BY kickoff_time
        """, (start,))

        rows = cur.fetchall()

        raw_matches = []

        for m in rows:

            raw_matches.append({
                "id": m[0],
                "home_team": m[1],
                "away_team": m[2],
                "kickoff_time": m[3],
                "deadline": m[4],
                "status": m[5],
                "league": m[6],
                "home_score": m[7],
                "away_score": m[8],
            })

        # =================================================
        # USER PREDICTIONS
        # =================================================


        match_ids = [m["id"] for m in raw_matches]

        user_preds = {}

        if match_ids:

            cur.execute("""
                SELECT
                    p.match_id,
                    p.home_goals,
                    p.away_goals,
                    p.points
                FROM predictions p
                WHERE p.user_id = %s
                  AND p.match_id = ANY(%s)
            """, (
                session['user_id'],
                match_ids
            ))

            for r in cur.fetchall():
                user_preds[r[0]] = {
                    "home": r[1],
                    "away": r[2],
                    "points": r[3]
                }

        # =================================================
        # GROUP BY DAY
        # =================================================

        grouped = defaultdict(list)

        today = datetime.now(timezone.utc).astimezone(MSK).strftime("%Y-%m-%d")

        for m in raw_matches:

            dt = parse_dt(m["kickoff_time"])
            if not dt:
                continue

            day = dt.astimezone(MSK).strftime("%Y-%m-%d")

            m["deadline_passed"] = not is_before_deadline({
                "deadline": m["deadline"]
            })

            m["finished"] = (m["status"] == "FINISHED")

            if m["id"] in user_preds:
                m["pred_home"] = user_preds[m["id"]]["home"]
                m["pred_away"] = user_preds[m["id"]]["away"]
                m["my_points"] = user_preds[m["id"]]["points"] if m["finished"] else 0
            else:
                m["pred_home"] = ""
                m["pred_away"] = ""
                m["my_points"] = 0

            grouped[day].append(m)

        # =================================================
        # BUILD DAYS
        # =================================================

        days = []

        for day in sorted(grouped.keys()):

            if day == today:
                t = "today"
            elif day < today:
                t = "past"
            else:
                t = "future"

            has_open = any(
                not x["deadline_passed"]
                for x in grouped[day]
            )

            days.append({
                "key": day,
                "label": format_date_ru(day),
                "type": t,
                "matches": grouped[day],
                "count": len(grouped[day]),
                "has_open": has_open
            })

        # Choose which day should be opened by default:
        # 1) today (MSK), if present
        # 2) otherwise nearest future day
        # 3) otherwise last available day
        open_day = next((d["key"] for d in days if d["key"] == today), None)
        if open_day is None:
            next_future_day = next((d for d in days if d["key"] > today), None)
            if next_future_day:
                open_day = next_future_day["key"]
            elif days:
                open_day = days[-1]["key"]

        # =================================================
        # GROUP BY MONTH
        # =================================================

        months = defaultdict(list)

        for d in days:
            month_key = d['key'][:7]
            months[month_key].append(d)

        month_names = {
            '01': '������',
            '02': '�������',
            '03': '����',
            '04': '������',
            '05': '���',
            '06': '����',
            '07': '����',
            '08': '������',
            '09': '��������',
            '10': '�������',
            '11': '������',
            '12': '�������'
        }

        grouped_months = []

        for mk in sorted(months.keys()):
            year, month = mk.split('-')
            month_label = f"{month_names[month]} {year}"

            grouped_months.append({
                'key': mk,
                'label': month_label,
                'days': months[mk],
                'count': sum(d['count'] for d in months[mk])
            })

    finally:
        close_db(conn, cur)

    return render_template(
        "index.html",
        months=grouped_months,
        open_day=open_day,
        get_flag=get_flag,
        get_club_logo=get_club_logo,
        to_msk=cached_to_msk,
        current_filter=league,
    )


