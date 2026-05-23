from flask import Blueprint, flash, redirect, request, url_for
from markupsafe import escape

from app.db import close_db, get_db
from app.routes.admin_common import admin_required
from app.services.scoring_recalculation_service import (
    recalc_match_points,
    recalc_tournament_points,
)
from app.services.tournament_service import get_active_tournament_id
from app.utils import translate_name


admin_tournaments_bp = Blueprint("admin_tournaments", __name__, url_prefix="/admin")


@admin_tournaments_bp.route("/debug_match", methods=["POST"])
@admin_required
def debug_match():
    match_id = request.form.get("match_id", type=int)

    if not match_id:
        flash("���� �� ������", "error")
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
            return "���� �� ������", 404

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
            WHERE p.match_id = %s
            """,
            (match_id,),
        )

        preds = cur.fetchall()

        result = f"""
        <h3>
            ���� #{match[0]}:
            ���� {match[1]}:{match[2]}
            (��������� {updated} �������)
        </h3>
        """

        result += """
        <table border='1'>
            <tr>
                <th>�����</th>
                <th>�������</th>
                <th>����</th>
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

        result += "}</table>"

        return result

    finally:
        close_db(conn, cur)


@admin_tournaments_bp.route("/recalc_all", methods=["POST"])
@admin_required
def recalc_all():
    conn = get_db()
    cur = conn.cursor()

    try:
        tournament_id = get_active_tournament_id()

        if not tournament_id:
            flash("�������� ������ �� ������", "error")
            return redirect(url_for("admin.admin"))

        summary = recalc_tournament_points(tournament_id, conn=conn, cur=cur)
        total_updated = summary.get("updated", 0)

        conn.commit()

        flash(
            f"����������� {total_updated} ���������",
            "success",
        )

    except Exception as e:
        conn.rollback()
        flash(f"������ ���������: {e}", "error")

    finally:
        close_db(conn, cur)

    return redirect(url_for("admin.admin"))


@admin_tournaments_bp.route("/translate", methods=["POST"])
@admin_required
def admin_translate():
    conn = get_db()
    cur = conn.cursor()

    try:
        cur.execute(
            """
            SELECT id,
                   home_team,
                   away_team
            FROM matches
            """
        )

        matches = cur.fetchall()

        updated = 0

        for m in matches:
            new_home = translate_name(m[1])
            new_away = translate_name(m[2])

            if (
                new_home != m[1]
                or new_away != m[2]
            ):
                cur.execute(
                    """
                    UPDATE matches
                    SET home_team = %s,
                        away_team = %s
                    WHERE id = %s
                    """,
                    (
                        new_home,
                        new_away,
                        m[0],
                    ),
                )

                updated += 1

        conn.commit()

        flash(
            f"���������� {updated} ������",
            "success",
        )

    except Exception as e:
        conn.rollback()
        flash(f"������ ��������: {e}", "error")

    finally:
        close_db(conn, cur)

    return redirect(url_for("admin.admin"))


@admin_tournaments_bp.route("/new_tournament", methods=["POST"])
@admin_required
def admin_new_tournament():
    name = request.form.get("name", "").strip()
    start_date = request.form.get("start_date")

    if not name:
        flash("������� �������� �������", "error")
        return redirect(url_for("admin.admin_tournaments"))

    conn = get_db()
    cur = conn.cursor()

    try:
        cur.execute(
            """
            SELECT id
            FROM tournaments
            WHERE LOWER(name) = LOWER(%s)
            """,
            (name,),
        )

        existing = cur.fetchone()

        if existing:
            flash("������ � ����� ��������� ��� ����������", "error")
            return redirect(url_for("admin.admin_tournaments"))

        cur.execute(
            """
            INSERT INTO tournaments (
                name,
                is_active,
                start_date
            )
            VALUES (%s, 1, %s)
            """,
            (
                name,
                start_date,
            ),
        )

        conn.commit()

        flash(
            f"������ �{name}� ������",
            "success",
        )

    except Exception as e:
        conn.rollback()
        flash(f"������ �������� �������: {e}", "error")

    finally:
        close_db(conn, cur)

    return redirect(url_for("admin.admin_tournaments"))


def handle_archive_tournament(tid):
    conn = get_db()
    cur = conn.cursor()

    try:
        cur.execute(
            """
            UPDATE tournaments
            SET is_active = 0
            WHERE id = %s
            """,
            (tid,),
        )

        if cur.rowcount == 0:
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
        cur.execute(
            """
            UPDATE tournaments
            SET is_active = 1
            WHERE id = %s
            """,
            (tid,),
        )

        if cur.rowcount == 0:
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
        flash("������ �� ������", "error")
        return redirect(url_for("admin.admin_tournaments"))

    conn = get_db()
    cur = conn.cursor()

    try:
        cur.execute(
            """
            SELECT is_active
            FROM tournaments
            WHERE id = %s
            """,
            (tid,),
        )

        row = cur.fetchone()

        if not row:
            flash("������ �� ������", "error")
            return redirect(url_for("admin.admin_tournaments"))

        if row[0] == 1:
            flash("������ ������� �������� ������", "error")
            return redirect(url_for("admin.admin_tournaments"))

        cur.execute(
            """
            DELETE FROM tournaments
            WHERE id = %s
            """,
            (tid,),
        )

        conn.commit()

        flash(f"������ #{tid} �����", "success")

    except Exception as e:
        conn.rollback()
        flash(f"������: {e}", "error")

    finally:
        close_db(conn, cur)

    return redirect(url_for("admin.admin_tournaments"))
