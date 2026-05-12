# app/routes/main.py

from datetime import datetime, timezone
from collections import defaultdict
from zoneinfo import ZoneInfo

from flask import (
    Blueprint,
    render_template,
    request,
    redirect,
    url_for,
    flash,
    session
)

from app.db import get_db, close_db, get_active_tournament_id
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


# =========================================================
# INDEX
# =========================================================

@main_bp.route('/', methods=['GET', 'POST'])
def index():

    if 'user_id' not in session:
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

            match_id = request.form.get('match_id')
            tid = get_active_tournament_id()

            try:
                h = int(request.form.get('home_goals', 0))
                a = int(request.form.get('away_goals', 0))
                if h < 0 or a < 0:
                    raise ValueError
            except Exception:
                flash("Некорректный счёт", "error")
                return redirect(url_for('main.index'))

            cur.execute("""
                SELECT id, home_team, away_team, kickoff_time, deadline, status
                FROM matches
                WHERE id = %s
            """, (match_id,))

            match = cur.fetchone()

            if not match:
                flash("Матч не найден", "error")
                return redirect(url_for('main.index'))

            if not is_before_deadline({
                "deadline": match[4]
            }):
                flash("Дедлайн прошёл", "error")
                return redirect(url_for('main.index'))

            cur.execute("""
                SELECT 1 FROM predictions
                WHERE user_id=%s AND match_id=%s AND tournament_id=%s
            """, (session['user_id'], match_id, tid))

            exists = cur.fetchone()

            if exists:
                cur.execute("""
                    UPDATE predictions
                    SET home_goals=%s, away_goals=%s
                    WHERE user_id=%s AND match_id=%s AND tournament_id=%s
                """, (h, a, session['user_id'], match_id, tid))
            else:
                cur.execute("""
                    INSERT INTO predictions (
                        user_id, match_id, tournament_id,
                        home_goals, away_goals
                    )
                    VALUES (%s,%s,%s,%s,%s)
                """, (session['user_id'], match_id, tid, h, a))

            conn.commit()

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

        tid = get_active_tournament_id()

        match_ids = [m["id"] for m in raw_matches]

        user_preds = {}

        if match_ids:

            cur.execute("""
                SELECT match_id, home_goals, away_goals, points
                FROM predictions
                WHERE user_id=%s
                AND tournament_id=%s
                AND match_id = ANY(%s)
            """, (session['user_id'], tid, match_ids))

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
                "label": format_date_ru(day),  # ← ИСПРАВЛЕНО: красивая дата
                "type": t,
                "matches": grouped[day],
                "count": len(grouped[day]),
                "has_open": has_open
            })

        open_day = next((d["key"] for d in days if d["type"] == "today"), None)

        # =================================================
        # GROUP BY MONTH
        # =================================================

        months = defaultdict(list)
        for d in days:
            month_key = d['key'][:7]
            months[month_key].append(d)

        month_names = {
            '01': 'Январь', '02': 'Февраль', '03': 'Март',
            '04': 'Апрель', '05': 'Май', '06': 'Июнь',
            '07': 'Июль', '08': 'Август', '09': 'Сентябрь',
            '10': 'Октябрь', '11': 'Ноябрь', '12': 'Декабрь'
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