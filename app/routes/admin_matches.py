from zoneinfo import ZoneInfo

from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from app.db import close_db, get_db
from app.routes.admin_common import admin_required
from app.services.rpl_admin_service import (
    RPL_TOURNAMENT_NAME,
    get_rpl_tournament,
    normalize_rpl_match_category,
)
from app.services.russian_cup_admin_service import (
    build_russian_cup_match_form_data,
    get_russian_cup_tournament,
)
from app.services.scoring_recalculation_service import recalc_match_points
from app.services.manual_match_creation_service import (
    ManualMatchCreateData,
    ManualMatchValidationError,
    build_manual_deadline_utc,
    create_manual_match,
)
from app.services.local_tesseract_service import OcrError
from app.services.rpl_screenshot_import_service import (
    ImageValidationError,
    draft_is_valid,
    mark_preview_duplicates,
    run_import,
    validate_confirmed_fields,
)
from app.services.russian_cup_screenshot_import_service import (
    run_import as run_russian_cup_screenshot_import,
    IMPORTER_KEY as RUSSIAN_CUP_IMPORTER_KEY,
    LEAGUE as RUSSIAN_CUP_IMPORT_LEAGUE,
)
from app.services.screenshot_match_import_service import generic_draft_is_valid
from app.services.russian_cup_team_catalog import match_russian_cup_team
from app.models.scoring import has_valid_finished_score
from app.services.wc_playoff_service import (
    is_wc2026_playoff_match,
    normalize_playoff_stage,
)
from app.utils import parse_datetime

import logging
logger = logging.getLogger(__name__)

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


def admin_context_redirect(default_endpoint="admin.admin"):
    """Return to an internal admin list while rejecting open redirects."""
    target = (request.form.get("return_to") or "").strip()
    if (
        target.startswith("/admin/")
        and not target.startswith("//")
        and not target.startswith("/admin/matches")
    ):
        return redirect(target)
    return redirect(url_for(default_endpoint))


def safe_admin_return_to(target, default_endpoint):
    target = (target or "").strip()
    if (
        target.startswith("/admin/")
        and not target.startswith("//")
        and not target.startswith("/admin/matches")
    ):
        return target
    return url_for(default_endpoint)


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


def build_russian_cup_deadline_utc(match_date, match_time, deadline_date, deadline_time):
    return build_manual_deadline_utc(
        match_date,
        match_time,
        deadline_date,
        deadline_time,
        reject_early_auto=True,
    )


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


@admin_matches_bp.route("/russia-2027/matches/<int:match_id>/edit", methods=["GET"])
@admin_required
def admin_russia_2027_edit_form(match_id):
    return_to = safe_admin_return_to(
        request.args.get("return_to"),
        RPL_ADMIN_REDIRECT,
    )
    conn = get_db()
    cur = conn.cursor()
    try:
        tournament = get_required_rpl_tournament(cur)
        cur.execute(
            """
            SELECT id, home_team, away_team, kickoff_time, deadline, status,
                   home_score, away_score, playoff_stage_manual, match_category
            FROM matches
            WHERE id = %s AND tournament_id = %s AND league = 'rpl'
            """,
            (match_id, tournament["id"]),
        )
        row = cur.fetchone()
        if not row:
            flash("Матч РПЛ не найден", "error")
            return redirect(return_to)
        kickoff = parse_datetime(row[3])
        deadline = parse_datetime(row[4])
        kickoff_msk = kickoff.astimezone(MSK) if kickoff else None
        deadline_msk = deadline.astimezone(MSK) if deadline else None
        standard_deadline = kickoff_msk.replace(hour=11, minute=0, second=0, microsecond=0) if kickoff_msk else None
        match = {
            "id": row[0],
            "home_team": row[1],
            "away_team": row[2],
            "match_date_msk": kickoff_msk.strftime("%Y-%m-%d") if kickoff_msk else "",
            "match_time_msk": kickoff_msk.strftime("%H:%M") if kickoff_msk else "",
            "deadline_date_msk": deadline_msk.strftime("%Y-%m-%d") if deadline_msk else "",
            "deadline_time_msk": deadline_msk.strftime("%H:%M") if deadline_msk else "",
            "uses_manual_deadline": bool(
                kickoff_msk and (
                    not deadline_msk
                    or not standard_deadline
                    or deadline_msk != standard_deadline
                    or (kickoff_msk.hour, kickoff_msk.minute) <= (11, 0)
                )
            ),
            "status": row[5],
            "home_score": row[6],
            "away_score": row[7],
            "stage": row[8] or "",
            "match_category": row[9] or "rpl",
        }
        return render_template(
            "admin_rpl_edit.html",
            match=match,
            rpl_statuses=("SCHEDULED", "TIMED", "LIVE", "FINISHED", "POSTPONED", "CANCELLED"),
            rpl_match_categories=(
                ("rpl", "Чемпионат России"),
                ("supercup", "Суперкубок России"),
                ("national_team", "Сборная России"),
            ),
            return_to=return_to,
            current_tournament_name="Чемпионат России 🇷🇺",
            current_tournament_id=tournament["id"],
        )
    finally:
        close_db(conn, cur)


@admin_matches_bp.route("/russia_2027_result", methods=["POST"])
@admin_required
def admin_russia_2027_result():
    match_id = request.form.get("match_id", type=int)
    conn = get_db()
    cur = conn.cursor()
    try:
        tournament = get_required_rpl_tournament(cur)
        if request.form.get("delete_result") == "1":
            cur.execute(
                """
                UPDATE matches
                SET home_score = NULL, away_score = NULL, status = 'SCHEDULED'
                WHERE id = %s AND tournament_id = %s AND league = 'rpl'
                """,
                (match_id, tournament["id"]),
            )
            if getattr(cur, "rowcount", 1) == 0:
                conn.rollback()
                flash("Матч РПЛ не найден", "error")
                return admin_context_redirect(RPL_ADMIN_REDIRECT)
            cur.execute(
                """
                UPDATE predictions
                SET points = 0
                WHERE match_id = %s AND tournament_id = %s
                """,
                (match_id, tournament["id"]),
            )
            flash("Результат РПЛ удалён", "success")
        else:
            home_score, away_score = validate_score(
                request.form.get("home_score"), request.form.get("away_score")
            )
            if home_score is None:
                flash("Укажите корректный счёт", "error")
                return admin_context_redirect(RPL_ADMIN_REDIRECT)
            cur.execute(
                """
                UPDATE matches
                SET home_score = %s, away_score = %s, status = 'FINISHED'
                WHERE id = %s AND tournament_id = %s AND league = 'rpl'
                """,
                (home_score, away_score, match_id, tournament["id"]),
            )
            if getattr(cur, "rowcount", 1) == 0:
                conn.rollback()
                flash("Матч РПЛ не найден", "error")
                return admin_context_redirect(RPL_ADMIN_REDIRECT)
            recalc_match_points(match_id, tournament_id=tournament["id"], conn=conn, cur=cur)
            flash("Результат РПЛ сохранён", "success")
        conn.commit()
    except Exception as e:
        conn.rollback()
        flash(f"Ошибка сохранения результата РПЛ: {e}", "error")
    finally:
        close_db(conn, cur)
    return admin_context_redirect(RPL_ADMIN_REDIRECT)


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
        manual_deadline = request.form.get("manual_deadline") == "1"
        deadline_date = (request.form.get("deadline_date") or "").strip() if manual_deadline else ""
        deadline_time = (request.form.get("deadline_time") or "").strip() if manual_deadline else ""

        if not home_team or not away_team or not match_date or not match_time:
            flash("Заполните команды, дату и время", "error")
            return redirect(url_for(RPL_ADMIN_REDIRECT))
        if home_team == away_team:
            flash("Команды должны отличаться", "error")
            return redirect(url_for(RPL_ADMIN_REDIRECT))
        if status == "FINISHED":
            flash("Для finished сначала создайте матч, затем внесите счёт", "error")
            return redirect(url_for(RPL_ADMIN_REDIRECT))

        create_manual_match(cur, ManualMatchCreateData(
            tournament_id=tournament["id"],
            league="rpl",
            home_team=home_team,
            away_team=away_team,
            match_date=match_date,
            match_time=match_time,
            status=status,
            stage=stage,
            match_category=match_category,
            deadline_date=deadline_date,
            deadline_time=deadline_time,
            reject_early_auto_deadline=True,
        ))
        conn.commit()
        flash("Матч чемпионата России создан", "success")
    except (ManualMatchValidationError, ValueError) as e:
        conn.rollback()
        flash(str(e), "error")
    except Exception as e:
        conn.rollback()
        flash(f"Ошибка создания матча: {e}", "error")
    finally:
        close_db(conn, cur)
    return admin_context_redirect(RPL_ADMIN_REDIRECT)


@admin_matches_bp.route("/russia-2027/import-screenshot", methods=["POST"])
@admin_required
def admin_rpl_import_screenshot():
    conn = get_db()
    cur = conn.cursor()
    try:
        logger.info(
            "rpl_screenshot_import upload_received user_id=%s content_length=%s",
            session["user_id"], request.content_length,
        )
        tournament = get_required_rpl_tournament(cur)
        draft = run_import(request.files.get("screenshot"), tournament, session["user_id"])
        mark_preview_duplicates(cur, draft, tournament["id"])
        session["rpl_screenshot_draft"] = draft
        session.modified = True
        logger.info(
            "rpl_screenshot_import extracted=%s user_id=%s tournament_id=%s",
            len(draft["matches"]), session["user_id"], tournament["id"],
        )
        flash(f"Распознано матчей: {len(draft['matches'])}", "success")
    except (ImageValidationError, OcrError, ValueError) as exc:
        session.pop("rpl_screenshot_draft", None)
        logger.warning("rpl_screenshot_import extraction_failed user_id=%s reason=%s", session["user_id"], type(exc).__name__)
        flash(str(exc), "error")
    finally:
        close_db(conn, cur)
    return redirect(url_for(RPL_ADMIN_REDIRECT))


@admin_matches_bp.route("/russia-2027/import-confirm", methods=["POST"])
@admin_required
def admin_rpl_import_confirm():
    conn = get_db()
    cur = conn.cursor()
    draft = session.get("rpl_screenshot_draft")
    try:
        tournament = get_required_rpl_tournament(cur)
        if not draft_is_valid(draft, session["user_id"], tournament["id"]):
            raise ManualMatchValidationError("Черновик импорта истёк. Загрузите скриншот снова")
        if request.form.get("draft_id") != draft.get("id"):
            raise ManualMatchValidationError("Некорректный черновик импорта")

        homes = request.form.getlist("home_team")
        aways = request.form.getlist("away_team")
        dates = request.form.getlist("match_date")
        times = request.form.getlist("match_time")
        if not homes or not (len(homes) == len(aways) == len(dates) == len(times)):
            raise ManualMatchValidationError("Черновик не содержит матчей")

        confirmed = []
        for index, values in enumerate(zip(homes, aways, dates, times), start=1):
            checked = validate_confirmed_fields(dict(zip(
                ("home_team", "away_team", "date", "time"), values,
            )))
            if checked["status"] != "ready":
                raise ManualMatchValidationError(
                    f"Матч {index}: {'; '.join(checked['reasons'])}"
                )
            confirmed.append(checked)

        created = []
        for index, match in enumerate(confirmed, start=1):
            try:
                created.append(create_manual_match(cur, ManualMatchCreateData(
                    tournament_id=tournament["id"], league="rpl",
                    home_team=match["home_team"], away_team=match["away_team"],
                    match_date=match["date"], match_time=match["time"],
                    status="SCHEDULED", stage="", match_category="rpl",
                    reject_early_auto_deadline=True,
                )))
            except ManualMatchValidationError as exc:
                raise ManualMatchValidationError(f"Матч {index}: {exc}") from exc
        conn.commit()
        session.pop("rpl_screenshot_draft", None)
        logger.info(
            "rpl_screenshot_import confirmed=%s user_id=%s tournament_id=%s",
            len(created), session["user_id"], tournament["id"],
        )
        flash(f"Добавлено матчей: {len(created)}", "success")
    except (ManualMatchValidationError, ValueError) as exc:
        conn.rollback()
        flash(str(exc), "error")
    except Exception as exc:
        conn.rollback()
        logger.exception("rpl_screenshot_import confirm failed")
        flash(f"Ошибка импорта матчей: {exc}", "error")
    finally:
        close_db(conn, cur)
    return redirect(url_for(RPL_ADMIN_REDIRECT))


@admin_matches_bp.route("/russia-2027/import-cancel", methods=["POST"])
@admin_required
def admin_rpl_import_cancel():
    session.pop("rpl_screenshot_draft", None)
    flash("Черновик импорта удалён", "success")
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
        delete_score = request.form.get("delete_score") == "1"
        manual_deadline = request.form.get("manual_deadline") == "1"
        deadline_date = (request.form.get("deadline_date") or "").strip() if manual_deadline else ""
        deadline_time = (request.form.get("deadline_time") or "").strip() if manual_deadline else ""

        if not home_team or not away_team or not match_date or not match_time:
            flash("Заполните команды, дату и время", "error")
            return redirect(url_for(RPL_ADMIN_REDIRECT))
        if home_team == away_team:
            flash("Команды должны отличаться", "error")
            return redirect(url_for(RPL_ADMIN_REDIRECT))

        kickoff_utc, deadline_utc = build_manual_deadline_utc(
            match_date,
            match_time,
            deadline_date,
            deadline_time,
            reject_early_auto=True,
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
                return redirect(url_for(RPL_ADMIN_REDIRECT))
            status = "FINISHED"
        elif status == "FINISHED":
            flash("Для finished укажите счёт", "error")
            return redirect(url_for(RPL_ADMIN_REDIRECT))

        cur.execute(
            """
            UPDATE matches
            SET home_team = %s,
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
              AND tournament_id = %s
              AND league = 'rpl'
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
                match_category,
                tournament["id"],
                match_id,
                tournament["id"],
            ),
        )
        if getattr(cur, "rowcount", 1) == 0:
            conn.rollback()
            flash("Матч РПЛ не найден", "error")
            return admin_context_redirect(RPL_ADMIN_REDIRECT)
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
    except ValueError as e:
        conn.rollback()
        flash(str(e), "error")
    except Exception as e:
        conn.rollback()
        flash(f"Ошибка сохранения матча: {e}", "error")
    finally:
        close_db(conn, cur)
    return admin_context_redirect(RPL_ADMIN_REDIRECT)


@admin_matches_bp.route("/russia_2027_visibility", methods=["POST"])
@admin_required
def admin_russia_2027_visibility():
    match_id = request.form.get("match_id", type=int)
    action = request.form.get("visibility_action")
    conn = get_db()
    cur = conn.cursor()
    try:
        tournament = get_required_rpl_tournament(cur)
        cur.execute(
            """
            UPDATE matches
            SET status = CASE
                WHEN %s = 'hide' THEN 'CANCELLED'
                WHEN home_score BETWEEN 0 AND 99 AND away_score BETWEEN 0 AND 99 THEN 'FINISHED'
                ELSE 'SCHEDULED'
            END
            WHERE id = %s
              AND tournament_id = %s
              AND league = 'rpl'
            """,
            (action, match_id, tournament["id"]),
        )
        if cur.rowcount:
            recalc_match_points(match_id, tournament_id=tournament["id"], conn=conn, cur=cur)
        conn.commit()
        flash("Матч скрыт" if action == "hide" else "Матч восстановлен", "success")
    except Exception as e:
        conn.rollback()
        flash(f"Ошибка изменения видимости: {e}", "error")
    finally:
        close_db(conn, cur)
    return admin_context_redirect(RPL_ADMIN_REDIRECT)


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
            return admin_context_redirect(RPL_ADMIN_REDIRECT)
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
    return admin_context_redirect(RPL_ADMIN_REDIRECT)


@admin_matches_bp.route("/russia_2027_recalc", methods=["POST"])
@admin_required
def admin_russia_2027_recalc():
    conn = get_db()
    cur = conn.cursor()
    recalculated = 0
    try:
        tournament = get_required_rpl_tournament(cur)
        cur.execute(
            """
            SELECT id
            FROM matches
            WHERE tournament_id = %s AND league = 'rpl'
            ORDER BY id
            """,
            (tournament["id"],),
        )
        for row in cur.fetchall():
            recalc_match_points(row[0], tournament_id=tournament["id"], conn=conn, cur=cur)
            recalculated += 1
        conn.commit()
        flash(f"Очки Чемпионата России пересчитаны: {recalculated}", "success")
    except Exception as e:
        conn.rollback()
        flash(f"Ошибка пересчёта очков: {e}", "error")
    finally:
        close_db(conn, cur)
    return admin_context_redirect(RPL_ADMIN_REDIRECT)


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
        stage = ""
        status = "SCHEDULED"

        if not home_team or not away_team or not match_date or not match_time:
            flash("Заполните команды, дату и время", "error")
            return redirect(url_for(RUSSIAN_CUP_ADMIN_REDIRECT))
        if home_team == away_team:
            flash("Команды должны отличаться", "error")
            return redirect(url_for(RUSSIAN_CUP_ADMIN_REDIRECT))
        if status == "FINISHED":
            flash("Для finished сначала создайте матч, затем внесите счёт", "error")
            return redirect(url_for(RUSSIAN_CUP_ADMIN_REDIRECT))

        create_manual_match(cur, ManualMatchCreateData(
            tournament_id=tournament["id"],
            league="rcup",
            home_team=home_team,
            away_team=away_team,
            match_date=match_date,
            match_time=match_time,
            status=status,
            stage=stage,
            match_category="russian_cup",
            deadline_date=form_data["deadline_date"],
            deadline_time=form_data["deadline_time"],
            reject_early_auto_deadline=True,
        ))
        conn.commit()
        flash("Матч Кубка России создан", "success")
    except Exception as e:
        conn.rollback()
        flash(f"Ошибка создания матча Кубка России: {e}", "error")
    finally:
        close_db(conn, cur)
    return admin_context_redirect(RUSSIAN_CUP_ADMIN_REDIRECT)


@admin_matches_bp.route("/russian-cup/import-screenshot", methods=["POST"])
@admin_required
def admin_russian_cup_import_screenshot():
    conn = get_db(); cur = conn.cursor()
    try:
        tournament = get_required_russian_cup_tournament(cur)
        draft = run_russian_cup_screenshot_import(request.files.get("screenshot"), tournament, session["user_id"])
        draft["matches"] = _mark_russian_cup_preview_duplicates(cur, draft, tournament["id"])
        session["russian_cup_screenshot_draft"] = draft
        session.modified = True
        flash(f"Распознано матчей: {len(draft['matches'])}", "success")
    except Exception as exc:
        session.pop("russian_cup_screenshot_draft", None)
        flash(str(exc), "error")
    finally:
        close_db(conn, cur)
    return redirect(url_for(RUSSIAN_CUP_ADMIN_REDIRECT))


def _mark_russian_cup_preview_duplicates(cur, draft, tournament_id):
    from app.services.manual_match_creation_service import build_manual_deadline_utc
    seen = set()
    for match in draft["matches"]:
        home, hs = match_russian_cup_team(match.get("home_team"))
        away, aws = match_russian_cup_team(match.get("away_team"))
        reasons = list(match.get("reasons") or [])
        if hs != "ready": reasons.append("Домашняя команда не входит в каталог Кубка России")
        if aws != "ready": reasons.append("Гостевая команда не входит в каталог Кубка России")
        match.update({"home_team": home or match.get("home_team", ""), "away_team": away or match.get("away_team", ""), "status": "needs_review" if reasons else "ready", "reasons": reasons})
        if reasons: continue
        kickoff, _ = build_manual_deadline_utc(match["date"], match["time"])
        key = (home, away, kickoff)
        if key in seen:
            match.update(status="invalid", reasons=["Дубликат внутри черновика"]); continue
        seen.add(key)
        cur.execute("SELECT id FROM matches WHERE tournament_id=%s AND league='rcup' AND home_team=%s AND away_team=%s AND kickoff_time=%s", (tournament_id, home, away, kickoff))
        if cur.fetchone(): match.update(status="invalid", reasons=["Такой матч уже существует"])
    return draft["matches"]


@admin_matches_bp.route("/russian-cup/import-cancel", methods=["POST"])
@admin_required
def admin_russian_cup_import_cancel():
    session.pop("russian_cup_screenshot_draft", None)
    flash("Черновик импорта удалён", "success")
    return redirect(url_for(RUSSIAN_CUP_ADMIN_REDIRECT))

@admin_matches_bp.route("/russian-cup/import-delete-row", methods=["POST"])
@admin_required
def admin_russian_cup_import_delete_row():
    draft = session.get("russian_cup_screenshot_draft")
    try:
        index = int(request.form.get("index", "-1"))
    except ValueError:
        index = -1
    if isinstance(draft, dict) and 0 <= index < len(draft.get("matches", [])):
        draft["matches"].pop(index)
        session["russian_cup_screenshot_draft"] = draft
        session.modified = True
    return redirect(url_for(RUSSIAN_CUP_ADMIN_REDIRECT))


@admin_matches_bp.route("/russian-cup/import-confirm", methods=["POST"])
@admin_required
def admin_russian_cup_import_confirm():
    conn = get_db(); cur = conn.cursor(); draft = session.get("russian_cup_screenshot_draft")
    try:
        tournament = get_required_russian_cup_tournament(cur)
        if not generic_draft_is_valid(draft, session["user_id"], tournament["id"], RUSSIAN_CUP_IMPORTER_KEY, RUSSIAN_CUP_IMPORT_LEAGUE):
            raise ManualMatchValidationError("Черновик импорта истёк. Загрузите скриншот снова")
        homes, aways = request.form.getlist("home_team"), request.form.getlist("away_team")
        dates, times = request.form.getlist("match_date"), request.form.getlist("match_time")
        if not homes or not (len(homes) == len(aways) == len(dates) == len(times)):
            raise ManualMatchValidationError("Черновик не содержит матчей")
        checked = []
        for i, (home, away, day, kickoff) in enumerate(zip(homes, aways, dates, times), 1):
            h, hs = match_russian_cup_team(home); a, aws = match_russian_cup_team(away)
            if hs != "ready" or aws != "ready": raise ManualMatchValidationError(f"Матч {i}: команда не входит в каталог Кубка России")
            if h == a: raise ManualMatchValidationError(f"Матч {i}: команды должны отличаться")
            checked.append((h, a, day, kickoff))
        for i, (home, away, day, kickoff) in enumerate(checked, 1):
            create_manual_match(cur, ManualMatchCreateData(tournament_id=tournament["id"], league="rcup", home_team=home, away_team=away, match_date=day, match_time=kickoff, status="SCHEDULED", stage="", match_category="russian_cup", reject_early_auto_deadline=True))
        conn.commit(); session.pop("russian_cup_screenshot_draft", None); flash(f"Добавлено матчей: {len(checked)}", "success")
    except Exception as exc:
        conn.rollback(); flash(str(exc), "error")
    finally: close_db(conn, cur)
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

        try:
            kickoff_utc, deadline_utc = build_russian_cup_deadline_utc(
                match_date,
                match_time,
                form_data["deadline_date"],
                form_data["deadline_time"],
            )
        except ValueError as e:
            flash(str(e), "error")
            return redirect(url_for(RUSSIAN_CUP_ADMIN_REDIRECT))

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
    return admin_context_redirect(RUSSIAN_CUP_ADMIN_REDIRECT)


@admin_matches_bp.route("/russian-cup/matches/<int:match_id>/edit", methods=["GET"])
@admin_required
def admin_russian_cup_edit_form(match_id):
    conn = get_db()
    cur = conn.cursor()
    requested_return_to = (request.args.get("return_to") or "").strip()
    return_to = requested_return_to if requested_return_to.startswith("/admin/") and not requested_return_to.startswith("//") else url_for(RUSSIAN_CUP_ADMIN_REDIRECT)
    try:
        tournament = get_required_russian_cup_tournament(cur)
        cur.execute(
            """
            SELECT id, home_team, away_team, kickoff_time, deadline, status,
                   home_score, away_score, playoff_stage_manual
            FROM matches
            WHERE id = %s AND tournament_id = %s AND league = 'rcup'
            """,
            (match_id, tournament["id"]),
        )
        row = cur.fetchone()
        if not row:
            flash("Матч Кубка России не найден", "error")
            return redirect(return_to)
        kickoff = parse_datetime(row[3])
        deadline = parse_datetime(row[4])
        match = {
            "id": row[0], "home_team": row[1], "away_team": row[2],
            "match_date_msk": kickoff.astimezone(MSK).strftime("%Y-%m-%d") if kickoff else "",
            "match_time_msk": kickoff.astimezone(MSK).strftime("%H:%M") if kickoff else "",
            "deadline_date_msk": deadline.astimezone(MSK).strftime("%Y-%m-%d") if deadline else "",
            "deadline_time_msk": deadline.astimezone(MSK).strftime("%H:%M") if deadline else "",
            "status": row[5], "home_score": row[6], "away_score": row[7],
            "stage": row[8] or "",
        }
        return render_template(
            "admin_russian_cup_edit.html",
            match=match,
            russian_cup_stages=(
                ("Групповой этап", "Групповой этап"),
                ("Плей-офф", "Плей-офф"),
                ("1/4 финала", "1/4 финала"),
                ("1/2 финала", "1/2 финала"),
                ("Финал", "Финал"),
                ("Другое", "Другое вручную"),
            ),
            russian_cup_statuses=("SCHEDULED", "TIMED", "LIVE", "FINISHED", "POSTPONED", "CANCELLED"),
            return_to=return_to,
            current_tournament_name="Кубок России",
            current_tournament_id=tournament["id"],
            is_rcup_tournament=True,
        )
    finally:
        close_db(conn, cur)


@admin_matches_bp.route("/russian_cup_result", methods=["POST"])
@admin_required
def admin_russian_cup_result():
    match_id = request.form.get("match_id", type=int)
    conn = get_db()
    cur = conn.cursor()
    try:
        tournament = get_required_russian_cup_tournament(cur)
        if request.form.get("delete_result") == "1":
            cur.execute(
                """
                UPDATE matches
                SET home_score = NULL, away_score = NULL, status = 'SCHEDULED'
                WHERE id = %s AND tournament_id = %s AND league = 'rcup'
                """,
                (match_id, tournament["id"]),
            )
            if cur.rowcount == 0:
                conn.rollback()
                flash("Матч Кубка России не найден", "error")
                return admin_context_redirect(RUSSIAN_CUP_ADMIN_REDIRECT)
            cur.execute(
                """
                UPDATE predictions
                SET points = 0
                WHERE match_id = %s AND tournament_id = %s
                """,
                (match_id, tournament["id"]),
            )
            flash("Результат Кубка России удалён", "success")
        else:
            home_score, away_score = validate_score(
                request.form.get("home_score"), request.form.get("away_score")
            )
            if home_score is None:
                flash("Укажите корректный счёт", "error")
                return admin_context_redirect(RUSSIAN_CUP_ADMIN_REDIRECT)
            cur.execute(
                """
                UPDATE matches
                SET home_score = %s, away_score = %s, status = 'FINISHED'
                WHERE id = %s AND tournament_id = %s AND league = 'rcup'
                """,
                (home_score, away_score, match_id, tournament["id"]),
            )
            if cur.rowcount == 0:
                conn.rollback()
                flash("Матч Кубка России не найден", "error")
                return admin_context_redirect(RUSSIAN_CUP_ADMIN_REDIRECT)
            recalc_match_points(match_id, tournament_id=tournament["id"], conn=conn, cur=cur)
            flash("Результат Кубка России сохранён", "success")
        conn.commit()
    except Exception as e:
        conn.rollback()
        flash(f"Ошибка сохранения результата: {e}", "error")
    finally:
        close_db(conn, cur)
    return admin_context_redirect(RUSSIAN_CUP_ADMIN_REDIRECT)


@admin_matches_bp.route("/russian_cup_visibility", methods=["POST"])
@admin_required
def admin_russian_cup_visibility():
    match_id = request.form.get("match_id", type=int)
    action = request.form.get("visibility_action")
    conn = get_db()
    cur = conn.cursor()
    try:
        tournament = get_required_russian_cup_tournament(cur)
        cur.execute(
            """
            UPDATE matches
            SET status = CASE
                WHEN %s = 'hide' THEN 'CANCELLED'
                WHEN home_score BETWEEN 0 AND 99 AND away_score BETWEEN 0 AND 99 THEN 'FINISHED'
                ELSE 'SCHEDULED'
            END
            WHERE id = %s
              AND tournament_id = %s
              AND league = 'rcup'
            """,
            (action, match_id, tournament["id"]),
        )
        if cur.rowcount:
            recalc_match_points(match_id, tournament_id=tournament["id"], conn=conn, cur=cur)
        conn.commit()
        flash("Матч скрыт" if action == "hide" else "Матч восстановлен", "success")
    except Exception as e:
        conn.rollback()
        flash(f"Ошибка изменения видимости: {e}", "error")
    finally:
        close_db(conn, cur)
    return admin_context_redirect(RUSSIAN_CUP_ADMIN_REDIRECT)


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
    return admin_context_redirect(RUSSIAN_CUP_ADMIN_REDIRECT)


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
            ORDER BY id
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
    return admin_context_redirect(RUSSIAN_CUP_ADMIN_REDIRECT)


def handle_add_match(conn, cur):
    try:
        home = request.form["home_team"].strip()
        away = request.form["away_team"].strip()
        league = request.form.get("league", "other").strip()
        tournament_id = request.form.get("tournament_id", type=int)
        status = normalize_manual_match_status(request.form.get("status"), "SCHEDULED")
        if status == "FINISHED":
            flash("Для finished сначала создайте матч, затем внесите результат", "error")
            return redirect(url_for("admin.admin"))

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
            flash("Такой матч уже существует", "error")
            return admin_context_redirect()

        if not tournament_id:
            flash("Выберите турнир для матча", "error")
            return redirect(url_for("admin.admin"))

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
            f"Матч {home} — {away} добавлен",
            "success",
        )

    except Exception as e:
        conn.rollback()
        flash(f"Ошибка: {e}", "error")

    return admin_context_redirect()


def handle_set_result(conn, cur):
    match_id = request.form.get("match_id")

    home_score, away_score = validate_score(
        request.form.get("home_score"),
        request.form.get("away_score"),
    )

    if home_score is None:
        flash("Некорректный счёт", "error")
        return admin_context_redirect()

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
            flash("Матч не найден", "error")
            return admin_context_redirect()

        recalc_match_points(match_id, conn=conn, cur=cur)

        conn.commit()

        flash(
            "Результат сохранён, очки пересчитаны",
            "success",
        )

    except Exception as e:
        conn.rollback()
        flash(f"Ошибка: {e}", "error")

    return admin_context_redirect()


@admin_matches_bp.route("/force_finish", methods=["POST"])
@admin_required
def force_finish():
    match_id = request.form.get("match_id", type=int)
    h, a = validate_score(request.form.get("home_score"), request.form.get("away_score"))

    if match_id is None or h is None or a is None:
        flash("Некорректные данные матча", "error")
        return admin_context_redirect()

    conn = get_db()
    cur = conn.cursor()

    try:
        tournament_id, match_found = get_match_tournament_id(cur, match_id)

        if not match_found:
            flash("Матч не найден", "error")
            return admin_context_redirect()

        if not tournament_id:
            flash("Турнир матча не определён", "error")
            return admin_context_redirect()

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
            f"Матч #{match_id} завершён: {h}:{a}",
            "success",
        )

    except Exception as e:
        conn.rollback()
        flash(f"Ошибка: {e}", "error")

    finally:
        close_db(conn, cur)

    return admin_context_redirect()


@admin_matches_bp.route("/fix_result", methods=["POST"])
@admin_required
def admin_fix_result():
    match_id = request.form.get("match_id")

    home_score, away_score = validate_score(
        request.form.get("home_score"),
        request.form.get("away_score"),
    )

    if home_score is None:
        flash("Некорректный счёт", "error")
        return admin_context_redirect()

    conn = get_db()
    cur = conn.cursor()

    try:
        tournament_id, match_found = get_match_tournament_id(cur, match_id)

        if not match_found:
            flash("Матч не найден", "error")
            return admin_context_redirect()

        if not tournament_id:
            flash("Турнир матча не определён", "error")
            return admin_context_redirect()

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
            f"Результат исправлен: {home_score}:{away_score}",
            "success",
        )

    except Exception as e:
        conn.rollback()
        flash(f"Ошибка: {e}", "error")

    finally:
        close_db(conn, cur)

    return admin_context_redirect()


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
        flash("Заполните все поля", "error")
        return admin_context_redirect()

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
            flash("Матч не найден", "error")
            return admin_context_redirect()

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
            return admin_context_redirect()
        except Exception:
            flash("Некорректные дата или время", "error")
            return admin_context_redirect()

        playoff_stage = None
        if is_wc2026_playoff_match(tournament_name, league, kickoff_utc):
            playoff_stage = normalize_playoff_stage(request.form.get("playoff_stage"))

        is_wc2026_match = tournament_name == "ЧМ-2026" or league == "wc2026"
        if submitted_status == "FINISHED" and not has_valid_finished_score(
            submitted_status, home_score, away_score
        ):
            flash("Для статуса FINISHED сначала укажите результат матча", "error")
            return admin_context_redirect()

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

            flash("Для FINISHED нельзя менять дату или дедлайн без корректного результата", "error")
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

        if cur.rowcount == 0:
            flash("Матч не найден", "error")
            return admin_context_redirect()

        recalc_match_points(match_id, tournament_id=tournament_id, conn=conn, cur=cur)
        conn.commit()

        flash(
            f"Матч #{match_id} обновлён",
            "success",
        )

    except Exception as e:
        conn.rollback()
        flash(f"Ошибка: {e}", "error")

    finally:
        close_db(conn, cur)

    return admin_context_redirect()


@admin_matches_bp.route("/delete_match", methods=["POST"])
@admin_required
def admin_delete_match():
    match_id = request.form.get("match_id")
    redirect_target = url_for("admin.admin")

    if not match_id:
        flash("Не указан match_id", "error")
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
            flash("Матч не найден", "error")
            return redirect(redirect_target)

        conn.commit()

        flash(
            f"Матч #{match_id} удалён",
            "success",
        )

    except Exception as e:
        conn.rollback()
        flash(f"Ошибка удаления: {e}", "error")

    finally:
        close_db(conn, cur)

    return redirect(redirect_target)
