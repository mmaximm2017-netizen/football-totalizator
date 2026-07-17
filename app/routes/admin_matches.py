from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from flask import Blueprint, flash, redirect, request, url_for

from app.db import close_db, get_db
from app.routes.admin_common import admin_required
from app.services.match_service import RPL_TOURNAMENT_NAME, run_sync_with_lock
from app.services.rpl_admin_service import get_rpl_tournament, normalize_rpl_match_category
from app.services.russian_cup_admin_service import (
    build_russian_cup_match_form_data,
    get_russian_cup_tournament,
)
from app.services.scoring_recalculation_service import recalc_match_points
from app.models.scoring import has_valid_finished_score
from app.services.wc_playoff_service import (
    determine_effective_playoff_stage,
    is_wc2026_playoff_match,
    normalize_playoff_stage,
)


admin_matches_bp = Blueprint("admin_matches", __name__, url_prefix="/admin")
MSK = ZoneInfo("Europe/Moscow")

MANUAL_MATCH_STATUSES = {
    "SCHEDULED",
    "TIMED",
    "LIVE",
    "FINISHED",
    "POSTPONED",
    "CANCELLED",
}

RPL_ADMIN_REDIRECT = "admin.admin_russia_2027"
RUSSIAN_CUP_ADMIN_REDIRECT = "admin.admin_russian_cup"


def normalize_manual_match_status(value, fallback="SCHEDULED"):
    status = (value or fallback or "SCHEDULED").strip().upper()
    return status if status in MANUAL_MATCH_STATUSES else fallback


def validate_score(home_score, away_score):
    try:
        home_score = int(home_score)
        away_score = int(away_score)

        if not has_valid_finished_score("FINISHED", home_score, away_score):
            return None, None

        return home_score, away_score

    except (TypeError, ValueError):
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


def get_wc2026_tournament_id(cur):
    cur.execute(
        """
        SELECT id
        FROM tournaments
        WHERE name = 'ЧМ-2026'
        ORDER BY id DESC
        LIMIT 1
        """
    )
    row = cur.fetchone()
    return row[0] if row else None


def build_wc_kickoff_and_deadline(match_date, match_time):
    kickoff_msk = datetime.strptime(
        f"{match_date} {match_time}",
        "%Y-%m-%d %H:%M",
    ).replace(tzinfo=MSK)
    kickoff_utc = kickoff_msk.astimezone(timezone.utc)
    deadline_utc = (kickoff_msk - timedelta(hours=6)).astimezone(timezone.utc)
    return kickoff_utc, deadline_utc


def get_required_rpl_tournament(cur):
    tournament = get_rpl_tournament(cur)
    if not tournament:
        raise ValueError(f"Турнир {RPL_TOURNAMENT_NAME} не найден")
    return tournament


def get_required_russian_cup_tournament(cur):
    tournament = get_russian_cup_tournament(cur)
    if not tournament:
        raise ValueError("Турнир Кубок России не найден")
    return tournament


def normalize_rpl_source(value):
    return "api" if (value or "").strip().lower() == "api" else "manual"


def build_rpl_api_match_id(source, raw_api_match_id):
    if source != "api":
        return None
    return (raw_api_match_id or "").strip() or None


@admin_matches_bp.route("/russia_2027_import", methods=["POST"])
@admin_required
def admin_russia_2027_import():
    try:
        summary = run_sync_with_lock()
        overall_status = summary.get("status") if isinstance(summary, dict) else None
        sync_summary = summary.get("sync") if isinstance(summary, dict) else summary
        sync_summary = sync_summary or {}
        inserted = sync_summary.get("matches_inserted", 0)
        updated = sync_summary.get("matches_updated", 0)
        skipped_other = sync_summary.get("matches_skipped_missing_tournament", 0)
        errors = sync_summary.get("errors", [])
        understat_matches = sync_summary.get("understat_matches", 0)
        total_changed = inserted + updated

        if overall_status == "scoring_failed":
            flash(
                "Матчи импортированы (добавлено {inserted}, обновлено {updated}), "
                "но пересчёт очков не удался. "
                "Запустите пересчёт вручную.".format(
                    inserted=inserted, updated=updated,
                ),
                "warning",
            )
        elif errors:
            error_summary = "; ".join(errors[:3])
            flash(
                "Импорт с ошибками: добавлено {inserted}, обновлено {updated}. Ошибки: {errors}".format(
                    inserted=inserted, updated=updated, errors=error_summary,
                ),
                "error",
            )
        elif total_changed == 0 and understat_matches == 0:
            flash(
                "Импорт завершён: 0 изменений. Understat не вернул матчей — возможно, сезон ещё не начался или API недоступен.",
                "warning",
            )
        else:
            flash(
                "Импорт завершён: добавлено {inserted}, обновлено {updated}, пропущено без турнира {skipped}".format(
                    inserted=inserted, updated=updated, skipped=skipped_other,
                ),
                "success",
            )
    except Exception as e:
        flash(f"Ошибка импорта РПЛ: {e}", "error")
    return redirect(url_for(RPL_ADMIN_REDIRECT))


@admin_matches_bp.route("/russia_2027_add", methods=["POST"])
@admin_required
def admin_russia_2027_add():
    conn = get_db()
    cur = conn.cursor()
    try:
        tournament = get_required_rpl_tournament(cur)
        home_team = (request.form.get("home_team") or "").strip()
        away_team = (request.form.get("away_team") or "").strip()
        match_date = (request.form.get("match_date") or "").strip()
        match_time = (request.form.get("match_time") or "").strip()
        stage = (request.form.get("stage") or "").strip()
        match_category = normalize_rpl_match_category(request.form.get("match_category"))
        status = normalize_manual_match_status(request.form.get("status"), "SCHEDULED")
        source = normalize_rpl_source(request.form.get("source"))
        if match_category != "rpl":
            source = "manual"

        if not home_team or not away_team or not match_date or not match_time:
            flash("Заполните команды, дату и время", "error")
            return redirect(url_for(RPL_ADMIN_REDIRECT))
        if home_team == away_team:
            flash("Команды должны отличаться", "error")
            return redirect(url_for(RPL_ADMIN_REDIRECT))
        if status == "FINISHED":
            flash("Для finished сначала создайте матч, затем внесите счёт", "error")
            return redirect(url_for(RPL_ADMIN_REDIRECT))

        kickoff_utc, deadline_utc = build_manual_deadline_utc(
            match_date,
            match_time,
            request.form.get("deadline_date", "").strip(),
            request.form.get("deadline_time", "").strip(),
        )
        api_match_id = build_rpl_api_match_id(source, request.form.get("api_match_id"))
        if source == "api" and not api_match_id:
            flash("Для источника API укажите API ID", "error")
            return redirect(url_for(RPL_ADMIN_REDIRECT))

        cur.execute(
            """
            SELECT id
            FROM matches
            WHERE tournament_id = %s
              AND home_team = %s
              AND away_team = %s
              AND kickoff_time = %s
            """,
            (tournament["id"], home_team, away_team, kickoff_utc),
        )
        if cur.fetchone():
            flash("Такой матч уже существует", "error")
            return redirect(url_for(RPL_ADMIN_REDIRECT))

        cur.execute(
            """
            INSERT INTO matches (
                api_match_id, home_team, away_team, kickoff_time, deadline,
                status, league, tournament_id, playoff_stage_manual, match_category
            )
            VALUES (%s, %s, %s, %s, %s, %s, 'rpl', %s, %s, %s)
            RETURNING id
            """,
            (api_match_id, home_team, away_team, kickoff_utc, deadline_utc, status, tournament["id"], stage, match_category),
        )
        match_id = cur.fetchone()[0]
        conn.commit()
        flash("Матч чемпионата России создан", "success")
    except Exception as e:
        conn.rollback()
        flash(f"Ошибка создания матча: {e}", "error")
    finally:
        close_db(conn, cur)
    return redirect(url_for(RPL_ADMIN_REDIRECT))


@admin_matches_bp.route("/russia_2027_edit", methods=["POST"])
@admin_required
def admin_russia_2027_edit():
    match_id = request.form.get("match_id", type=int)
    conn = get_db()
    cur = conn.cursor()
    try:
        tournament = get_required_rpl_tournament(cur)
        cur.execute(
            """
            SELECT id, status
            FROM matches
            WHERE id = %s
              AND tournament_id = %s
              AND league = 'rpl'
            """,
            (match_id, tournament["id"]),
        )
        existing = cur.fetchone()
        if not existing:
            flash("Матч РПЛ не найден", "error")
            return redirect(url_for(RPL_ADMIN_REDIRECT))

        home_team = (request.form.get("home_team") or "").strip()
        away_team = (request.form.get("away_team") or "").strip()
        match_date = (request.form.get("match_date") or "").strip()
        match_time = (request.form.get("match_time") or "").strip()
        stage = (request.form.get("stage") or "").strip()
        match_category = normalize_rpl_match_category(request.form.get("match_category"))
        status = normalize_manual_match_status(request.form.get("status"), existing[1])
        source = normalize_rpl_source(request.form.get("source"))
        if match_category != "rpl":
            source = "manual"
        delete_score = request.form.get("delete_score") == "1"

        if not home_team or not away_team or not match_date or not match_time:
            flash("Заполните команды, дату и время", "error")
            return redirect(url_for(RPL_ADMIN_REDIRECT))
        if home_team == away_team:
            flash("Команды должны отличаться", "error")
            return redirect(url_for(RPL_ADMIN_REDIRECT))

        kickoff_utc, deadline_utc = build_manual_deadline_utc(
            match_date,
            match_time,
            request.form.get("deadline_date", "").strip(),
            request.form.get("deadline_time", "").strip(),
        )
        api_match_id = build_rpl_api_match_id(source, request.form.get("api_match_id"))
        if source == "api" and not api_match_id:
            flash("Для источника API укажите API ID", "error")
            return redirect(url_for(RPL_ADMIN_REDIRECT))

        home_score = away_score = None
        result_home = (request.form.get("home_score") or "").strip()
        result_away = (request.form.get("away_score") or "").strip()
        has_score = bool(result_home or result_away)
        if delete_score:
            status = "SCHEDULED" if status == "FINISHED" else status
        elif has_score:
            home_score, away_score = validate_score(result_home, result_away)
            if home_score is None:
                flash("Укажите корректный счёт", "error")
                return redirect(url_for(RPL_ADMIN_REDIRECT))
            status = "FINISHED"
        elif status == "FINISHED":
            flash("Для finished укажите счёт", "error")
            return redirect(url_for(RPL_ADMIN_REDIRECT))

        cur.execute(
            """
            UPDATE matches
            SET api_match_id = %s,
                home_team = %s,
                away_team = %s,
                kickoff_time = %s,
                deadline = %s,
                status = %s,
                home_score = %s,
                away_score = %s,
                playoff_stage_manual = %s,
                match_category = %s,
                league = 'rpl',
                tournament_id = %s
            WHERE id = %s
            """,
            (
                api_match_id,
                home_team,
                away_team,
                kickoff_utc,
                deadline_utc,
                status,
                home_score,
                away_score,
                stage,
                match_category,
                tournament["id"],
                match_id,
            ),
        )
        if status == "FINISHED":
            recalc_match_points(match_id, tournament_id=tournament["id"], conn=conn, cur=cur)
        else:
            cur.execute(
                """
                UPDATE predictions
                SET points = 0
                WHERE match_id = %s
                  AND tournament_id = %s
                """,
                (match_id, tournament["id"]),
            )
        conn.commit()
        flash("Матч сохранён", "success")
    except Exception as e:
        conn.rollback()
        flash(f"Ошибка сохранения матча: {e}", "error")
    finally:
        close_db(conn, cur)
    return redirect(url_for(RPL_ADMIN_REDIRECT))


@admin_matches_bp.route("/russia_2027_visibility", methods=["POST"])
@admin_required
def admin_russia_2027_visibility():
    match_id = request.form.get("match_id", type=int)
    action = request.form.get("visibility_action")
    conn = get_db()
    cur = conn.cursor()
    try:
        tournament = get_required_rpl_tournament(cur)
        new_status = "CANCELLED" if action == "hide" else "SCHEDULED"
        cur.execute(
            """
            UPDATE matches
            SET status = %s
            WHERE id = %s
              AND tournament_id = %s
              AND league = 'rpl'
            """,
            (new_status, match_id, tournament["id"]),
        )
        conn.commit()
        flash("Матч скрыт" if action == "hide" else "Матч восстановлен", "success")
    except Exception as e:
        conn.rollback()
        flash(f"Ошибка изменения видимости: {e}", "error")
    finally:
        close_db(conn, cur)
    return redirect(url_for(RPL_ADMIN_REDIRECT))


@admin_matches_bp.route("/russia_2027_delete", methods=["POST"])
@admin_required
def admin_russia_2027_delete():
    match_id = request.form.get("match_id", type=int)
    conn = get_db()
    cur = conn.cursor()
    try:
        tournament = get_required_rpl_tournament(cur)
        cur.execute(
            """
            SELECT 1 FROM predictions
            WHERE match_id = %s
              AND tournament_id = %s
            LIMIT 1
            """,
            (match_id, tournament["id"]),
        )
        if cur.fetchone():
            flash("Нельзя удалить матч: существуют связанные прогнозы", "error")
            return redirect(url_for(RPL_ADMIN_REDIRECT))
        cur.execute(
            """
            DELETE FROM matches
            WHERE id = %s
              AND tournament_id = %s
              AND league = 'rpl'
            """,
            (match_id, tournament["id"]),
        )
        conn.commit()
        flash("Матч удалён", "success")
    except Exception as e:
        conn.rollback()
        flash(f"Ошибка удаления матча: {e}", "error")
    finally:
        close_db(conn, cur)
    return redirect(url_for(RPL_ADMIN_REDIRECT))


@admin_matches_bp.route("/russian_cup_add", methods=["POST"])
@admin_required
def admin_russian_cup_add():
    conn = get_db()
    cur = conn.cursor()
    try:
        tournament = get_required_russian_cup_tournament(cur)
        form_data = build_russian_cup_match_form_data(request.form, normalize_manual_match_status)
        home_team = form_data["home_team"]
        away_team = form_data["away_team"]
        match_date = form_data["match_date"]
        match_time = form_data["match_time"]
        stage = form_data["stage"]
        status = form_data["status"]

        if not home_team or not away_team or not match_date or not match_time:
            flash("Заполните команды, дату и время", "error")
            return redirect(url_for(RUSSIAN_CUP_ADMIN_REDIRECT))
        if home_team == away_team:
            flash("Команды должны отличаться", "error")
            return redirect(url_for(RUSSIAN_CUP_ADMIN_REDIRECT))
        if status == "FINISHED":
            flash("Для finished сначала создайте матч, затем внесите счёт", "error")
            return redirect(url_for(RUSSIAN_CUP_ADMIN_REDIRECT))

        kickoff_utc, deadline_utc = build_manual_deadline_utc(
            match_date,
            match_time,
            form_data["deadline_date"],
            form_data["deadline_time"],
        )

        cur.execute(
            """
            SELECT id
            FROM matches
            WHERE tournament_id = %s
              AND league = 'rcup'
              AND home_team = %s
              AND away_team = %s
              AND kickoff_time = %s
            """,
            (tournament["id"], home_team, away_team, kickoff_utc),
        )
        if cur.fetchone():
            flash("Такой матч уже существует", "error")
            return redirect(url_for(RUSSIAN_CUP_ADMIN_REDIRECT))

        cur.execute(
            """
            INSERT INTO matches (
                api_match_id, home_team, away_team, kickoff_time, deadline,
                status, league, tournament_id, playoff_stage_manual, match_category
            )
            VALUES (NULL, %s, %s, %s, %s, %s, 'rcup', %s, %s, 'russian_cup')
            """,
            (home_team, away_team, kickoff_utc, deadline_utc, status, tournament["id"], stage),
        )
        conn.commit()
        flash("Матч Кубка России создан", "success")
    except Exception as e:
        conn.rollback()
        flash(f"Ошибка создания матча Кубка России: {e}", "error")
    finally:
        close_db(conn, cur)
    return redirect(url_for(RUSSIAN_CUP_ADMIN_REDIRECT))


@admin_matches_bp.route("/russian_cup_edit", methods=["POST"])
@admin_required
def admin_russian_cup_edit():
    match_id = request.form.get("match_id", type=int)
    conn = get_db()
    cur = conn.cursor()
    try:
        tournament = get_required_russian_cup_tournament(cur)
        cur.execute(
            """
            SELECT id, status
            FROM matches
            WHERE id = %s
              AND tournament_id = %s
              AND league = 'rcup'
            """,
            (match_id, tournament["id"]),
        )
        existing = cur.fetchone()
        if not existing:
            flash("Матч Кубка России не найден", "error")
            return redirect(url_for(RUSSIAN_CUP_ADMIN_REDIRECT))

        form_data = build_russian_cup_match_form_data(request.form, normalize_manual_match_status, existing[1])
        home_team = form_data["home_team"]
        away_team = form_data["away_team"]
        match_date = form_data["match_date"]
        match_time = form_data["match_time"]
        stage = form_data["stage"]
        status = form_data["status"]
        delete_score = request.form.get("delete_score") == "1"

        if not home_team or not away_team or not match_date or not match_time:
            flash("Заполните команды, дату и время", "error")
            return redirect(url_for(RUSSIAN_CUP_ADMIN_REDIRECT))
        if home_team == away_team:
            flash("Команды должны отличаться", "error")
            return redirect(url_for(RUSSIAN_CUP_ADMIN_REDIRECT))

        kickoff_utc, deadline_utc = build_manual_deadline_utc(
            match_date,
            match_time,
            form_data["deadline_date"],
            form_data["deadline_time"],
        )

        home_score = away_score = None
        result_home = (request.form.get("home_score") or "").strip()
        result_away = (request.form.get("away_score") or "").strip()
        has_score = bool(result_home or result_away)
        if delete_score:
            status = "SCHEDULED" if status == "FINISHED" else status
        elif has_score:
            home_score, away_score = validate_score(result_home, result_away)
            if home_score is None:
                flash("Укажите корректный счёт", "error")
                return redirect(url_for(RUSSIAN_CUP_ADMIN_REDIRECT))
            status = "FINISHED"
        elif status == "FINISHED":
            flash("Для finished укажите счёт", "error")
            return redirect(url_for(RUSSIAN_CUP_ADMIN_REDIRECT))

        cur.execute(
            """
            UPDATE matches
            SET api_match_id = NULL,
                home_team = %s,
                away_team = %s,
                kickoff_time = %s,
                deadline = %s,
                status = %s,
                home_score = %s,
                away_score = %s,
                playoff_stage_manual = %s,
                match_category = 'russian_cup',
                league = 'rcup',
                tournament_id = %s
            WHERE id = %s
            """,
            (
                home_team,
                away_team,
                kickoff_utc,
                deadline_utc,
                status,
                home_score,
                away_score,
                stage,
                tournament["id"],
                match_id,
            ),
        )
        if status == "FINISHED":
            recalc_match_points(match_id, tournament_id=tournament["id"], conn=conn, cur=cur)
        else:
            cur.execute(
                """
                UPDATE predictions
                SET points = 0
                WHERE match_id = %s
                  AND tournament_id = %s
                """,
                (match_id, tournament["id"]),
            )
        conn.commit()
        flash("Матч Кубка России сохранён", "success")
    except Exception as e:
        conn.rollback()
        flash(f"Ошибка сохранения матча Кубка России: {e}", "error")
    finally:
        close_db(conn, cur)
    return redirect(url_for(RUSSIAN_CUP_ADMIN_REDIRECT))


@admin_matches_bp.route("/russian_cup_visibility", methods=["POST"])
@admin_required
def admin_russian_cup_visibility():
    match_id = request.form.get("match_id", type=int)
    action = request.form.get("visibility_action")
    conn = get_db()
    cur = conn.cursor()
    try:
        tournament = get_required_russian_cup_tournament(cur)
        new_status = "CANCELLED" if action == "hide" else "SCHEDULED"
        cur.execute(
            """
            UPDATE matches
            SET status = %s
            WHERE id = %s
              AND tournament_id = %s
              AND league = 'rcup'
            """,
            (new_status, match_id, tournament["id"]),
        )
        conn.commit()
        flash("Матч скрыт" if action == "hide" else "Матч восстановлен", "success")
    except Exception as e:
        conn.rollback()
        flash(f"Ошибка изменения видимости: {e}", "error")
    finally:
        close_db(conn, cur)
    return redirect(url_for(RUSSIAN_CUP_ADMIN_REDIRECT))


@admin_matches_bp.route("/russian_cup_delete", methods=["POST"])
@admin_required
def admin_russian_cup_delete():
    match_id = request.form.get("match_id", type=int)
    conn = get_db()
    cur = conn.cursor()
    try:
        tournament = get_required_russian_cup_tournament(cur)
        cur.execute(
            """
            SELECT 1 FROM predictions
            WHERE match_id = %s
              AND tournament_id = %s
            LIMIT 1
            """,
            (match_id, tournament["id"]),
        )
        if cur.fetchone():
            flash("Нельзя удалить матч: существуют связанные прогнозы", "error")
            return redirect(url_for(RUSSIAN_CUP_ADMIN_REDIRECT))
        cur.execute(
            """
            DELETE FROM matches
            WHERE id = %s
              AND tournament_id = %s
              AND league = 'rcup'
            """,
            (match_id, tournament["id"]),
        )
        conn.commit()
        flash("Матч Кубка России удалён", "success")
    except Exception as e:
        conn.rollback()
        flash(f"Ошибка удаления матча Кубка России: {e}", "error")
    finally:
        close_db(conn, cur)
    return redirect(url_for(RUSSIAN_CUP_ADMIN_REDIRECT))


@admin_matches_bp.route("/russian_cup_recalc", methods=["POST"])
@admin_required
def admin_russian_cup_recalc():
    conn = get_db()
    cur = conn.cursor()
    recalculated = 0
    try:
        tournament = get_required_russian_cup_tournament(cur)
        cur.execute(
            """
            SELECT id
            FROM matches
            WHERE tournament_id = %s
              AND league = 'rcup'
              AND status = 'FINISHED'
            ORDER BY kickoff_time NULLS LAST, id
            """,
            (tournament["id"],),
        )
        match_ids = [row[0] for row in cur.fetchall()]
        for match_id in match_ids:
            recalc_match_points(match_id, tournament_id=tournament["id"], conn=conn, cur=cur)
            recalculated += 1
        conn.commit()
        flash(f"Очки Кубка России пересчитаны: {recalculated}", "success")
    except Exception as e:
        conn.rollback()
        flash(f"Ошибка пересчёта Кубка России: {e}", "error")
    finally:
        close_db(conn, cur)
    return redirect(url_for(RUSSIAN_CUP_ADMIN_REDIRECT))


def handle_add_match(conn, cur):
    try:
        home = request.form["home_team"].strip()
        away = request.form["away_team"].strip()
        league = request.form.get("league", "other").strip()
        tournament_id = request.form.get("tournament_id", type=int)
        status = normalize_manual_match_status(request.form.get("status"), "SCHEDULED")
        if status == "FINISHED":
            flash("Для finished сначала создайте матч, затем внесите результат", "error")
            return redirect(url_for("admin.admin_matches"))

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
        is_wc2026_match = tournament_name == "ЧМ-2026" or league == "wc2026"
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
                playoff_stage_manual,
                manual_teams_override,
                manual_kickoff_override,
                manual_result_override
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                home,
                away,
                kickoff_utc,
                deadline_utc,
                status,
                league,
                tournament_id,
                playoff_stage,
                1 if is_wc2026_match else 0,
                1 if is_wc2026_match else 0,
                1 if is_wc2026_match and status == "FINISHED" else 0,
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
                away_score = %s,
                manual_result_override = CASE
                    WHEN tournament_id IN (SELECT id FROM tournaments WHERE name = 'ЧМ-2026') THEN 1
                    ELSE manual_result_override
                END
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
    h, a = validate_score(request.form.get("home_score"), request.form.get("away_score"))

    if match_id is None or h is None or a is None:
        flash("������������ ������ �����", "error")
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
            SET status = 'FINISHED',
                home_score = %s,
                away_score = %s,
                manual_result_override = CASE
                    WHEN tournament_id IN (SELECT id FROM tournaments WHERE name = 'ЧМ-2026') THEN 1
                    ELSE manual_result_override
                END
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
                away_score = %s,
                manual_result_override = CASE
                    WHEN tournament_id IN (SELECT id FROM tournaments WHERE name = 'ЧМ-2026') THEN 1
                    ELSE manual_result_override
                END
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
    if request.form.get("next") == "wc_playoff":
        redirect_target = url_for("admin.admin_wc_playoff")
    else:
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
                   m.deadline,
                   m.league,
                   m.tournament_id,
                   t.name,
                   COALESCE(m.manual_teams_override, 0),
                   COALESCE(m.manual_result_override, 0),
                   COALESCE(m.manual_kickoff_override, 0),
                   m.playoff_stage_manual,
                   m.playoff_stage_auto,
                   m.home_score,
                   m.away_score,
                   m.status
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

        if not is_wc2026_playoff_match(match[7], match[5], match[3]):
            flash("Ручное управление доступно только для матчей плей-офф ЧМ-2026", "error")
            return redirect(redirect_target)

        cur.execute(
            """
            SELECT COUNT(1)
            FROM predictions
            WHERE match_id = %s
              AND tournament_id = %s
            """,
            (match_id, match[6]),
        )
        predictions_count = cur.fetchone()[0] or 0

        submitted_stage = normalize_playoff_stage(request.form.get("playoff_stage_manual"))
        home_team = (request.form.get("home_team") or "").strip()
        away_team = (request.form.get("away_team") or "").strip()
        if not home_team or not away_team:
            flash("Заполните обе команды", "error")
            return redirect(redirect_target)
        if home_team == away_team:
            flash("Команды должны отличаться", "error")
            return redirect(redirect_target)

        match_date = (request.form.get("match_date") or "").strip()
        match_time = (request.form.get("match_time") or "").strip()
        if not match_date or not match_time:
            flash("Заполните дату и время матча", "error")
            return redirect(redirect_target)
        try:
            kickoff_utc, deadline_utc = build_wc_kickoff_and_deadline(match_date, match_time)
        except Exception:
            flash("Некорректные дата или время матча", "error")
            return redirect(redirect_target)
        if not is_wc2026_playoff_match("ЧМ-2026", "wc2026", kickoff_utc):
            flash("Дата должна относиться к плей-офф ЧМ-2026", "error")
            return redirect(redirect_target)

        teams_changed = home_team != (match[1] or "") or away_team != (match[2] or "")
        if teams_changed and predictions_count > 0 and request.form.get("confirm_team_change") != "1":
            flash(
                "На этот матч уже есть прогнозы участников. Изменение команд может сделать существующие прогнозы некорректными.",
                "error",
            )
            return redirect(redirect_target)

        existing_effective_stage = determine_effective_playoff_stage(match[11], match[12])
        if submitted_stage and (match[11] or submitted_stage != existing_effective_stage):
            playoff_stage_manual = submitted_stage
        else:
            playoff_stage_manual = match[11]

        manual_teams_override = 1 if (match[8] or teams_changed) else 0
        manual_kickoff_override = 1 if (match[10] or kickoff_utc != match[3]) else 0

        result_home_raw = (request.form.get("home_score") or "").strip()
        result_away_raw = (request.form.get("away_score") or "").strip()
        has_result = bool(result_home_raw or result_away_raw)
        submitted_status = normalize_manual_match_status(request.form.get("status"), match[15] or "SCHEDULED")
        home_score = away_score = None
        manual_result_override = 1 if match[9] else 0
        if has_result:
            home_score, away_score = validate_score(
                result_home_raw,
                result_away_raw,
            )
            if home_score is None:
                flash("Укажите корректный итоговый счёт", "error")
                return redirect(redirect_target)
            result_changed = home_score != match[13] or away_score != match[14]
            manual_result_override = 1 if (match[9] or result_changed) else 0
            submitted_status = "FINISHED"
        elif match[9]:
            home_score = match[13]
            away_score = match[14]

        if submitted_status == "FINISHED" and not has_valid_finished_score(
            submitted_status, home_score, away_score
        ):
            flash("Укажите корректный итоговый счёт", "error")
            return redirect(redirect_target)

        if manual_result_override:
            cur.execute(
                """
                UPDATE matches
                SET home_team = %s,
                    away_team = %s,
                    kickoff_time = %s,
                    deadline = %s,
                    playoff_stage_manual = %s,
                    manual_teams_override = %s,
                    manual_kickoff_override = %s,
                    status = %s,
                    home_score = %s,
                    away_score = %s,
                    manual_result_override = 1
                WHERE id = %s
                """,
                (
                    home_team,
                    away_team,
                    kickoff_utc,
                    deadline_utc,
                    playoff_stage_manual,
                    manual_teams_override,
                    manual_kickoff_override,
                    submitted_status,
                    home_score,
                    away_score,
                    match_id,
                ),
            )
            recalc_match_points(match_id, tournament_id=match[6], conn=conn, cur=cur)
        else:
            cur.execute(
                """
                UPDATE matches
                SET home_team = %s,
                    away_team = %s,
                    kickoff_time = %s,
                    deadline = %s,
                    playoff_stage_manual = %s,
                    manual_teams_override = %s,
                    manual_kickoff_override = %s,
                    status = %s,
                    manual_result_override = %s,
                    home_score = NULL,
                    away_score = NULL
                WHERE id = %s
                """,
                (
                    home_team,
                    away_team,
                    kickoff_utc,
                    deadline_utc,
                    playoff_stage_manual,
                    manual_teams_override,
                    manual_kickoff_override,
                    submitted_status,
                    1 if submitted_status != (match[15] or "SCHEDULED") else 0,
                    match_id,
                ),
            )

        conn.commit()
        flash("Матч сохранён", "success")
    except Exception as e:
        conn.rollback()
        flash(f"Ошибка сохранения ручных настроек: {e}", "error")
    finally:
        close_db(conn, cur)

    return redirect(redirect_target)


@admin_matches_bp.route("/wc_playoff_create", methods=["POST"])
@admin_required
def admin_wc_playoff_create():
    redirect_target = url_for("admin.admin_wc_playoff")

    stage = normalize_playoff_stage(request.form.get("playoff_stage_manual"))
    home_team = (request.form.get("home_team") or "").strip()
    away_team = (request.form.get("away_team") or "").strip()
    match_date = (request.form.get("match_date") or "").strip()
    match_time = (request.form.get("match_time") or "").strip()
    status = normalize_manual_match_status(request.form.get("status"), "SCHEDULED")
    if status == "FINISHED":
        flash("Для finished сначала создайте матч, затем внесите результат", "error")
        return redirect(redirect_target)

    if not stage:
        flash("Выберите стадию матча", "error")
        return redirect(redirect_target)
    if not home_team or not away_team:
        flash("Выберите обе команды", "error")
        return redirect(redirect_target)
    if home_team == away_team:
        flash("Команды должны отличаться", "error")
        return redirect(redirect_target)
    if not match_date or not match_time:
        flash("Выберите дату и время матча", "error")
        return redirect(redirect_target)

    conn = get_db()
    cur = conn.cursor()

    try:
        tournament_id = get_wc2026_tournament_id(cur)
        if not tournament_id:
            flash("Турнир ЧМ-2026 не найден", "error")
            return redirect(redirect_target)

        kickoff_utc, deadline_utc = build_wc_kickoff_and_deadline(match_date, match_time)
        if not is_wc2026_playoff_match("ЧМ-2026", "wc2026", kickoff_utc):
            flash("Дата должна относиться к плей-офф ЧМ-2026", "error")
            return redirect(redirect_target)

        cur.execute(
            """
            SELECT id
            FROM matches
            WHERE tournament_id = %s
              AND home_team = %s
              AND away_team = %s
              AND kickoff_time = %s
            """,
            (tournament_id, home_team, away_team, kickoff_utc),
        )
        if cur.fetchone():
            flash("Такой матч уже существует", "error")
            return redirect(redirect_target)

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
                manual_teams_override,
                manual_kickoff_override,
                playoff_stage_manual
            )
            VALUES (%s, %s, %s, %s, %s, 'wc2026', %s, 1, 1, %s)
            """,
            (home_team, away_team, kickoff_utc, deadline_utc, status, tournament_id, stage),
        )

        conn.commit()
        flash("Матч успешно создан.", "success")
    except Exception as e:
        conn.rollback()
        flash(f"Ошибка создания матча: {e}", "error")
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
    submitted_status = normalize_manual_match_status(request.form.get("status"), None)

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
                   t.name,
                   m.home_score,
                   m.away_score
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
        tournament_id = row[2]
        tournament_name = row[3]
        home_score = row[4]
        away_score = row[5]
        submitted_status = normalize_manual_match_status(submitted_status, status)

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

        is_wc2026_match = tournament_name == "ЧМ-2026" or league == "wc2026"
        if submitted_status == "FINISHED" and not has_valid_finished_score(
            submitted_status, home_score, away_score
        ):
            flash("Для статуса FINISHED сначала укажите результат матча", "error")
            return redirect(url_for("admin.admin"))

        manual_teams_override_sql = ", manual_teams_override = 1" if is_wc2026_match else ""
        manual_kickoff_override_sql = ", manual_kickoff_override = 1" if is_wc2026_match else ""
        manual_result_override_sql = ", manual_result_override = 1" if is_wc2026_match and submitted_status != status else ""

        if status == "FINISHED" and submitted_status == "FINISHED":
            cur.execute(
                f"""
                UPDATE matches
                SET home_team = %s,
                    away_team = %s,
                    status = %s,
                    playoff_stage_manual = %s
                    {manual_teams_override_sql}
                    {manual_result_override_sql}
                WHERE id = %s
                """,
                (
                    home_team,
                    away_team,
                    submitted_status,
                    playoff_stage,
                    match_id,
                ),
            )

            flash("��� FINISHED ����� ��������� kickoff/deadline ��������� ��� ������������", "error")
        else:
            cur.execute(
                f"""
                UPDATE matches
                SET home_team = %s,
                    away_team = %s,
                    kickoff_time = %s,
                    deadline = %s,
                    status = %s,
                    playoff_stage_manual = %s
                    {manual_teams_override_sql}
                    {manual_kickoff_override_sql}
                    {manual_result_override_sql}
                WHERE id = %s
                """,
                (
                    home_team,
                    away_team,
                    kickoff_utc,
                    deadline_utc,
                    submitted_status,
                    playoff_stage,
                    match_id,
                ),
            )

            if status == "FINISHED" and submitted_status != "FINISHED" and tournament_id:
                recalc_match_points(match_id, tournament_id=tournament_id, conn=conn, cur=cur)

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
    redirect_target = (
        url_for("admin.admin_wc_playoff")
        if request.form.get("next") == "wc_playoff"
        else url_for("admin.admin")
    )

    if not match_id:
        flash("�� ������ match_id", "error")
        return redirect(redirect_target)

    conn = get_db()
    cur = conn.cursor()

    try:
        cur.execute(
            """
            SELECT 1 FROM predictions
            WHERE match_id = %s
            LIMIT 1
            """,
            (match_id,),
        )
        if cur.fetchone():
            flash("Нельзя удалить матч: существуют связанные прогнозы", "error")
            return redirect(redirect_target)

        cur.execute(
            """
            DELETE FROM matches
            WHERE id = %s
            """,
            (match_id,),
        )

        if cur.rowcount == 0:
            flash("���� �� ������", "error")
            return redirect(redirect_target)

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

    return redirect(redirect_target)
