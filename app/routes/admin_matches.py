from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from flask import Blueprint, flash, redirect, request, url_for

from app.db import close_db, get_db
from app.routes.admin_common import admin_required
from app.services.scoring_recalculation_service import recalc_match_points
from app.services.wc_playoff_service import (
    is_wc2026_playoff_match,
    normalize_playoff_stage,
)


admin_matches_bp = Blueprint("admin_matches", __name__, url_prefix="/admin")
MSK = ZoneInfo("Europe/Moscow")


def validate_score(home_score, away_score):
    try:
        home_score = int(home_score)
        away_score = int(away_score)

        if home_score < 0 or away_score < 0:
            return None, None

        return home_score, away_score

    except ValueError:
        return None, None


def build_manual_deadline_utc(match_date, match_time, deadline_date, deadline_time):
    dt_msk = datetime.strptime(
        f"{match_date} {match_time}",
        "%Y-%m-%d %H:%M",
    ).replace(tzinfo=MSK)

    kickoff_utc = dt_msk.astimezone(timezone.utc)

    if deadline_date or deadline_time:
        if not deadline_date or not deadline_time:
            raise ValueError("������� � ����, � ����� ��������")

        deadline_msk = datetime.strptime(
            f"{deadline_date} {deadline_time}",
            "%Y-%m-%d %H:%M",
        ).replace(tzinfo=MSK)
    else:
        deadline_msk = dt_msk.replace(hour=11, minute=0, second=0, microsecond=0)

    deadline_utc = deadline_msk.astimezone(timezone.utc)
    return kickoff_utc, deadline_utc


def get_match_tournament_id(cur, match_id):
    cur.execute(
        """
        SELECT tournament_id
        FROM matches
        WHERE id = %s
        """,
        (match_id,),
    )
    row = cur.fetchone()
    if not row:
        return None, False
    return row[0], True


def get_tournament_name(cur, tournament_id):
    cur.execute(
        """
        SELECT name
        FROM tournaments
        WHERE id = %s
        """,
        (tournament_id,),
    )
    row = cur.fetchone()
    return row[0] if row else None


def handle_add_match(conn, cur):
    try:
        home = request.form["home_team"].strip()
        away = request.form["away_team"].strip()
        league = request.form.get("league", "other").strip()
        tournament_id = request.form.get("tournament_id", type=int)

        match_date = request.form["match_date"]
        match_time = request.form["match_time"]
        deadline_date = request.form.get("deadline_date", "").strip()
        deadline_time = request.form.get("deadline_time", "").strip()

        kickoff_utc, deadline_utc = build_manual_deadline_utc(
            match_date,
            match_time,
            deadline_date,
            deadline_time,
        )

        cur.execute(
            """
            SELECT id
            FROM matches
            WHERE home_team = %s
            AND away_team = %s
            AND kickoff_time = %s
            """,
            (
                home,
                away,
                kickoff_utc,
            ),
        )

        existing = cur.fetchone()

        if existing:
            flash("����� ���� ��� ����������", "error")
            return redirect(url_for("admin.admin"))

        if not tournament_id:
            flash("Выберите турнир для матча", "error")
            return redirect(url_for("admin.admin_matches"))

        tournament_name = get_tournament_name(cur, tournament_id)
        playoff_stage = None
        if is_wc2026_playoff_match(tournament_name, league, kickoff_utc):
            playoff_stage = normalize_playoff_stage(request.form.get("playoff_stage"))

        cur.execute(
            """
            INSERT INTO matches (
                home_team,
                away_team,
                kickoff_time,
                deadline,
                status,
                league,
                tournament_id,
                playoff_stage
            )
            VALUES (%s, %s, %s, %s, 'SCHEDULED', %s, %s, %s)
            """,
            (
                home,
                away,
                kickoff_utc,
                deadline_utc,
                league,
                tournament_id,
                playoff_stage,
            ),
        )

        conn.commit()

        flash(
            f"���� {home} � {away} ��������",
            "success",
        )

    except Exception as e:
        conn.rollback()
        flash(f"������: {e}", "error")

    return redirect(url_for("admin.admin"))


def handle_set_result(conn, cur):
    match_id = request.form.get("match_id")

    home_score, away_score = validate_score(
        request.form.get("home_score"),
        request.form.get("away_score"),
    )

    if home_score is None:
        flash("������������ ����", "error")
        return redirect(url_for("admin.admin"))

    try:
        cur.execute(
            """
            UPDATE matches
            SET status = 'FINISHED',
                home_score = %s,
                away_score = %s
            WHERE id = %s
            """,
            (
                home_score,
                away_score,
                match_id,
            ),
        )

        if cur.rowcount == 0:
            flash("���� �� ������", "error")
            return redirect(url_for("admin.admin"))

        recalc_match_points(match_id, conn=conn, cur=cur)

        conn.commit()

        flash(
            "��������� �����, ���� �����������",
            "success",
        )

    except Exception as e:
        conn.rollback()
        flash(f"������: {e}", "error")

    return redirect(url_for("admin.admin"))


@admin_matches_bp.route("/force_finish", methods=["POST"])
@admin_required
def force_finish():
    match_id = request.form.get("match_id", type=int)
    h = request.form.get("home_score", type=int)
    a = request.form.get("away_score", type=int)

    if match_id is None or h is None or a is None:
        flash("������������ ������ �����", "error")
        return redirect(url_for("admin.admin"))

    conn = get_db()
    cur = conn.cursor()

    try:
        if h < 0 or a < 0:
            flash("���� �� ����� ���� �������������", "error")
            return redirect(url_for("admin.admin"))

        tournament_id, match_found = get_match_tournament_id(cur, match_id)

        if not match_found:
            flash("���� �� ������", "error")
            return redirect(url_for("admin.admin"))

        if not tournament_id:
            flash("Турнир матча не определён", "error")
            return redirect(url_for("admin.admin"))

        cur.execute(
            """
            UPDATE matches
            SET status = 'FINISHED',
                home_score = %s,
                away_score = %s
            WHERE id = %s
            """,
            (
                h,
                a,
                match_id,
            ),
        )

        recalc_match_points(
            match_id,
            tournament_id=tournament_id,
            conn=conn,
            cur=cur,
        )

        conn.commit()

        flash(
            f"���� #{match_id} ��������: {h}:{a}",
            "success",
        )

    except Exception as e:
        conn.rollback()
        flash(f"������: {e}", "error")

    finally:
        close_db(conn, cur)

    return redirect(url_for("admin.admin"))


@admin_matches_bp.route("/fix_result", methods=["POST"])
@admin_required
def admin_fix_result():
    match_id = request.form.get("match_id")

    home_score, away_score = validate_score(
        request.form.get("home_score"),
        request.form.get("away_score"),
    )

    if home_score is None:
        flash("������������ ����", "error")
        return redirect(url_for("admin.admin"))

    conn = get_db()
    cur = conn.cursor()

    try:
        tournament_id, match_found = get_match_tournament_id(cur, match_id)

        if not match_found:
            flash("���� �� ������", "error")
            return redirect(url_for("admin.admin"))

        if not tournament_id:
            flash("Турнир матча не определён", "error")
            return redirect(url_for("admin.admin"))

        cur.execute(
            """
            UPDATE matches
            SET home_score = %s,
                away_score = %s
            WHERE id = %s
            """,
            (
                home_score,
                away_score,
                match_id,
            ),
        )

        recalc_match_points(
            match_id,
            tournament_id=tournament_id,
            conn=conn,
            cur=cur,
        )

        conn.commit()

        flash(
            f"��������� �������: {home_score}:{away_score}",
            "success",
        )

    except Exception as e:
        conn.rollback()
        flash(f"������: {e}", "error")

    finally:
        close_db(conn, cur)

    return redirect(url_for("admin.admin"))


@admin_matches_bp.route("/wc_playoff_override", methods=["POST"])
@admin_required
def admin_wc_playoff_override():
    match_id = request.form.get("match_id", type=int)
    tid = request.form.get("tid", type=int)
    redirect_target = url_for("admin.admin_matches", tid=tid) if tid else url_for("admin.admin_matches")

    if not match_id:
        flash("Матч не выбран", "error")
        return redirect(redirect_target)

    conn = get_db()
    cur = conn.cursor()

    try:
        cur.execute(
            """
            SELECT m.id,
                   m.home_team,
                   m.away_team,
                   m.kickoff_time,
                   m.league,
                   m.tournament_id,
                   t.name,
                   COALESCE(m.manual_teams_override, 0),
                   COALESCE(m.manual_result_override, 0)
            FROM matches m
            JOIN tournaments t ON t.id = m.tournament_id
            WHERE m.id = %s
            """,
            (match_id,),
        )
        match = cur.fetchone()

        if not match:
            flash("Матч не найден", "error")
            return redirect(redirect_target)

        if not is_wc2026_playoff_match(match[6], match[4], match[3]):
            flash("Ручное управление доступно только для матчей плей-офф ЧМ-2026", "error")
            return redirect(redirect_target)

        cur.execute(
            """
            SELECT COUNT(1)
            FROM predictions
            WHERE match_id = %s
              AND tournament_id = %s
            """,
            (match_id, match[5]),
        )
        predictions_count = cur.fetchone()[0] or 0

        teams_override_enabled = request.form.get("manual_teams_override") == "1"
        result_override_enabled = request.form.get("manual_result_override") == "1"
        playoff_stage = normalize_playoff_stage(request.form.get("playoff_stage"))

        home_team = (request.form.get("home_team") or "").strip()
        away_team = (request.form.get("away_team") or "").strip()
        home_score = away_score = None

        if teams_override_enabled:
            if not home_team or not away_team:
                flash("Для ручного назначения команд заполните обе команды", "error")
                return redirect(redirect_target)

            teams_changed = home_team != (match[1] or "") or away_team != (match[2] or "")
            if teams_changed and predictions_count > 0 and request.form.get("confirm_team_change") != "1":
                flash(
                    "На этот матч уже есть прогнозы участников. Изменение команд может сделать существующие прогнозы некорректными. Продолжить?",
                    "error",
                )
                return redirect(redirect_target)

        if result_override_enabled:
            home_score, away_score = validate_score(
                request.form.get("home_score"),
                request.form.get("away_score"),
            )
            if home_score is None:
                flash("Для ручного результата укажите корректный счёт", "error")
                return redirect(redirect_target)

        if teams_override_enabled:
            cur.execute(
                """
                UPDATE matches
                SET home_team = %s,
                    away_team = %s,
                    manual_teams_override = 1
                WHERE id = %s
                """,
                (home_team, away_team, match_id),
            )
        else:
            cur.execute(
                """
                UPDATE matches
                SET manual_teams_override = 0
                WHERE id = %s
                """,
                (match_id,),
            )

        cur.execute(
            """
            UPDATE matches
            SET playoff_stage = %s
            WHERE id = %s
            """,
            (playoff_stage, match_id),
        )

        if result_override_enabled:
            cur.execute(
                """
                UPDATE matches
                SET status = 'FINISHED',
                    home_score = %s,
                    away_score = %s,
                    manual_result_override = 1
                WHERE id = %s
                """,
                (home_score, away_score, match_id),
            )

            recalc_match_points(match_id, tournament_id=match[5], conn=conn, cur=cur)
        else:
            cur.execute(
                """
                UPDATE matches
                SET manual_result_override = 0
                WHERE id = %s
                """,
                (match_id,),
            )

        conn.commit()
        flash("Ручные настройки матча плей-офф сохранены", "success")
    except Exception as e:
        conn.rollback()
        flash(f"Ошибка сохранения ручных настроек: {e}", "error")
    finally:
        close_db(conn, cur)

    return redirect(redirect_target)


@admin_matches_bp.route("/edit_match", methods=["POST"])
@admin_required
def admin_edit_match():
    match_id = request.form.get("match_id")
    home_team = request.form.get("home_team", "").strip()
    away_team = request.form.get("away_team", "").strip()
    match_date = request.form.get("match_date", "").strip()
    match_time = request.form.get("match_time", "").strip()
    deadline_date = request.form.get("deadline_date", "").strip()
    deadline_time = request.form.get("deadline_time", "").strip()

    if not match_id or not home_team or not away_team or not match_date or not match_time:
        flash("��������� ��� ����", "error")
        return redirect(url_for("admin.admin"))

    conn = get_db()
    cur = conn.cursor()

    try:
        cur.execute(
            """
            SELECT m.status,
                   m.league,
                   m.tournament_id,
                   t.name
            FROM matches m
            LEFT JOIN tournaments t ON t.id = m.tournament_id
            WHERE m.id = %s
            """,
            (match_id,),
        )

        row = cur.fetchone()

        if not row:
            flash("���� �� ������", "error")
            return redirect(url_for("admin.admin"))

        status = row[0]
        league = row[1]
        tournament_name = row[3]

        try:
            kickoff_utc, deadline_utc = build_manual_deadline_utc(
                match_date,
                match_time,
                deadline_date,
                deadline_time,
            )
        except ValueError as e:
            flash(str(e), "error")
            return redirect(url_for("admin.admin"))
        except Exception:
            flash("������������ ���� ��� �����", "error")
            return redirect(url_for("admin.admin"))

        playoff_stage = None
        if is_wc2026_playoff_match(tournament_name, league, kickoff_utc):
            playoff_stage = normalize_playoff_stage(request.form.get("playoff_stage"))

        if status == "FINISHED":
            cur.execute(
                """
                UPDATE matches
                SET home_team = %s,
                    away_team = %s,
                    playoff_stage = %s
                WHERE id = %s
                """,
                (
                    home_team,
                    away_team,
                    playoff_stage,
                    match_id,
                ),
            )

            flash("��� FINISHED ����� ��������� kickoff/deadline ��������� ��� ������������", "error")
        else:
            cur.execute(
                """
                UPDATE matches
                SET home_team = %s,
                    away_team = %s,
                    kickoff_time = %s,
                    deadline = %s,
                    playoff_stage = %s
                WHERE id = %s
                """,
                (
                    home_team,
                    away_team,
                    kickoff_utc,
                    deadline_utc,
                    playoff_stage,
                    match_id,
                ),
            )

        if cur.rowcount == 0:
            flash("���� �� ������", "error")
            return redirect(url_for("admin.admin"))

        conn.commit()

        flash(
            f"���� #{match_id} �������",
            "success",
        )

    except Exception as e:
        conn.rollback()
        flash(f"������: {e}", "error")

    finally:
        close_db(conn, cur)

    return redirect(url_for("admin.admin"))


@admin_matches_bp.route("/delete_match", methods=["POST"])
@admin_required
def admin_delete_match():
    match_id = request.form.get("match_id")

    if not match_id:
        flash("�� ������ match_id", "error")
        return redirect(url_for("admin.admin"))

    conn = get_db()
    cur = conn.cursor()

    try:
        cur.execute(
            """
            DELETE FROM predictions
            WHERE match_id = %s
            """,
            (match_id,),
        )

        cur.execute(
            """
            DELETE FROM matches
            WHERE id = %s
            """,
            (match_id,),
        )

        if cur.rowcount == 0:
            flash("���� �� ������", "error")
            return redirect(url_for("admin.admin"))

        conn.commit()

        flash(
            f"���� #{match_id} �����",
            "success",
        )

    except Exception as e:
        conn.rollback()
        flash(f"������ ��������: {e}", "error")

    finally:
        close_db(conn, cur)

    return redirect(url_for("admin.admin"))
