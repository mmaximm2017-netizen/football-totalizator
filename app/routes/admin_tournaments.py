from flask import Blueprint, flash, redirect, request, url_for
from markupsafe import escape

from app.db import close_db, get_db
from app.routes.admin_common import admin_required
from app.services.admin_tournament_mutation_service import (
    create_tournament,
    delete_archived_tournament,
    set_tournament_active,
    translate_match_names,
)
from app.services.scoring_recalculation_service import (
    recalc_match_points,
    recalc_tournament_points,
)
from app.utils import translate_name


admin_tournaments_bp = Blueprint("admin_tournaments", __name__, url_prefix="/admin")


@admin_tournaments_bp.route("/debug_match", methods=["POST"])
@admin_required
def debug_match():
    match_id = request.form.get("match_id", type=int)

    if not match_id:
        flash("Матч не указан", "error")
        return redirect(url_for("admin.admin"))

    conn = get_db()
    cur = conn.cursor()

    try:
        cur.execute(
            """
            SELECT id,
                   home_score,
                   away_score
            FROM matches
            WHERE id = %s
            """,
            (match_id,),
        )

        match = cur.fetchone()

        if not match:
            return "Матч не найден", 404

        summary = recalc_match_points(match_id, conn=conn, cur=cur)
        updated = summary.get("updated", 0)

        conn.commit()

        cur.execute(
            """
            SELECT u.username,
                   p.home_goals,
                   p.away_goals,
                   p.points
            FROM predictions p
            JOIN users u
            ON p.user_id = u.id
            JOIN matches m
            ON m.id = p.match_id
            AND m.tournament_id = p.tournament_id
            WHERE m.id = %s
            """,
            (match_id,),
        )

        preds = cur.fetchall()

        result = f"""
        <h3>
            Матч #{match[0]}:
            счёт {match[1]}:{match[2]}
            (пересчитано {updated} прогнозов)
        </h3>
        """

        result += """
        <table border='1'>
            <tr>
                <th>Игрок</th>
                <th>Прогноз</th>
                <th>Очки</th>
            </tr>
        """

        for p in preds:
            username = escape(p[0])

            result += f"""
            <tr>
                <td>{username}</td>
                <td>{p[1]}:{p[2]}</td>
                <td>{p[3]}</td>
            </tr>
            """

        result += "</table>"

        return result

    finally:
        close_db(conn, cur)


@admin_tournaments_bp.route("/recalc_all", methods=["POST"])
@admin_required
def recalc_all():
    conn = get_db()
    cur = conn.cursor()

    try:
        tournament_id = request.form.get("tournament_id", type=int)

        if not tournament_id:
            flash("Выберите турнир для пересчёта", "error")
            return redirect(url_for("admin.admin"))

        cur.execute(
            """
            SELECT id
            FROM tournaments
            WHERE id = %s
            """,
            (tournament_id,),
        )
        if not cur.fetchone():
            flash("Турнир не найден", "error")
            return redirect(url_for("admin.admin"))

        summary = recalc_tournament_points(tournament_id, conn=conn, cur=cur)
        total_updated = summary.get("updated", 0)

        conn.commit()

        flash(
            f"Пересчитано {total_updated} прогнозов для турнира #{tournament_id}",
            "success",
        )

    except Exception as e:
        conn.rollback()
        flash(f"Ошибка пересчёта: {e}", "error")

    finally:
        close_db(conn, cur)

    return redirect(url_for("admin.admin"))


@admin_tournaments_bp.route("/translate", methods=["POST"])
@admin_required
def admin_translate():
    conn = get_db()
    cur = conn.cursor()

    try:
        updated = translate_match_names(cur, translate_name)

        conn.commit()

        flash(
            f"Обновлено матчей: {updated}",
            "success",
        )

    except Exception as e:
        conn.rollback()
        flash(f"Ошибка перевода: {e}", "error")

    finally:
        close_db(conn, cur)

    return redirect(url_for("admin.admin"))


@admin_tournaments_bp.route("/new_tournament", methods=["POST"])
@admin_required
def admin_new_tournament():
    name = request.form.get("name", "").strip()
    start_date = request.form.get("start_date")

    if not name:
        flash("Укажите название турнира", "error")
        return redirect(url_for("admin.admin_tournaments"))

    conn = get_db()
    cur = conn.cursor()

    try:
        created = create_tournament(cur, name, start_date)

        if not created:
            flash("Турнир с таким названием уже существует", "error")
            return redirect(url_for("admin.admin_tournaments"))


        conn.commit()

        flash(
            f"Турнир «{name}» создан",
            "success",
        )

    except Exception as e:
        conn.rollback()
        flash(f"Ошибка создания турнира: {e}", "error")

    finally:
        close_db(conn, cur)

    return redirect(url_for("admin.admin_tournaments"))


def handle_archive_tournament(tid):
    conn = get_db()
    cur = conn.cursor()

    try:
        updated = set_tournament_active(cur, tid, False)

        if not updated:
            flash("Турнир не найден", "error")
            return redirect(url_for("admin.admin_tournaments"))

        conn.commit()
        flash(f"Турнир #{tid} отправлен в архив", "success")
    except Exception as e:
        conn.rollback()
        flash(f"Ошибка: {e}", "error")
    finally:
        close_db(conn, cur)

    return redirect(url_for("admin.admin_tournaments"))


def handle_activate_tournament(tid):
    conn = get_db()
    cur = conn.cursor()

    try:
        updated = set_tournament_active(cur, tid, True)

        if not updated:
            flash("Турнир не найден", "error")
            return redirect(url_for("admin.admin_tournaments"))

        conn.commit()
        flash(f"Турнир #{tid} активирован", "success")
    except Exception as e:
        conn.rollback()
        flash(f"Ошибка: {e}", "error")
    finally:
        close_db(conn, cur)

    return redirect(url_for("admin.admin_tournaments"))


@admin_tournaments_bp.route("/delete_tournament", methods=["POST"])
@admin_required
def delete_tournament():
    tid = request.form.get("tid", type=int)

    if not tid:
        flash("Турнир не указан", "error")
        return redirect(url_for("admin.admin_tournaments"))

    conn = get_db()
    cur = conn.cursor()

    try:
        delete_status = delete_archived_tournament(cur, tid)
        if delete_status == "missing":
            flash("Турнир не указан", "error")
            return redirect(url_for("admin.admin_tournaments"))
        if delete_status == "active":
            flash("Нельзя удалить активный турнир", "error")
            return redirect(url_for("admin.admin_tournaments"))
        if delete_status == "has_predictions":
            flash("Нельзя удалить турнир: существуют связанные прогнозы", "error")
            return redirect(url_for("admin.admin_tournaments"))

        conn.commit()

        flash(f"Турнир #{tid} удалён", "success")

    except Exception as e:
        conn.rollback()
        flash(f"Ошибка: {e}", "error")

    finally:
        close_db(conn, cur)

    return redirect(url_for("admin.admin_tournaments"))
