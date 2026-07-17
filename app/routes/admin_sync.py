import logging
from flask import Blueprint, flash, jsonify, redirect, url_for

from app.routes.admin_common import admin_required
from app.services.sync_history_service import (
    get_last_sync,
    get_recent_sync_runs,
    get_sync_health,
)


admin_sync_bp = Blueprint("admin_sync", __name__, url_prefix="/admin")
logger = logging.getLogger(__name__)


def _format_minutes_ago(minutes):
    if minutes is None:
        return "данных пока нет"

    try:
        minutes = int(minutes)
    except (TypeError, ValueError):
        return "данных пока нет"

    if minutes < 1:
        return "только что"

    if 11 <= minutes % 100 <= 14:
        word = "минут"
    elif minutes % 10 == 1:
        word = "минуту"
    elif 2 <= minutes % 10 <= 4:
        word = "минуты"
    else:
        word = "минут"

    return f"{minutes} {word} назад"


def _build_sync_panel_view(sync_health_data, last_sync):
    last_status = sync_health_data.get("last_status")
    is_in_progress = last_status in ("started", "skipped_already_running")

    if is_in_progress:
        status_class = "sync-status-warning"
        status_label = "Сейчас идёт обновление"
    elif not sync_health_data.get("is_healthy"):
        status_class = "sync-status-bad"
        status_label = "Есть проблема с автообновлением"
    else:
        status_class = "sync-status-good"
        status_label = "Автообновление работает"

    if last_sync:
        matches_updated = last_sync.get("matches_updated")
        matches_updated_text = str(matches_updated if matches_updated is not None else 0)
    else:
        matches_updated_text = "данных пока нет"

    return {
        "status_class": status_class,
        "status_label": status_label,
        "last_update_text": _format_minutes_ago(
            sync_health_data.get("minutes_since_last_finished")
        ),
        "matches_updated_text": matches_updated_text,
    }


def build_sync_panel_context():
    sync_health_data = get_sync_health()
    last_sync = get_last_sync()
    recent_sync_runs = get_recent_sync_runs(5)

    return {
        "sync_health": sync_health_data,
        "last_sync": last_sync,
        "recent_sync_runs": recent_sync_runs,
        "sync_panel": _build_sync_panel_view(sync_health_data, last_sync),
    }


def handle_manual_sync_update():
    from app.services.match_service import run_sync_with_lock

    try:
        sync_result = run_sync_with_lock()
        logger.info("admin sync summary: %s", sync_result)
        status = sync_result.get("status") if isinstance(sync_result, dict) else None

        if status == "completed":
            sync_summary = (sync_result.get("sync") or {})
            inserted = sync_summary.get("matches_inserted", 0)
            updated = sync_summary.get("matches_updated", 0)
            errors = sync_summary.get("errors", [])
            scoring = (sync_result.get("scoring") or {})
            recalc = scoring.get("predictions_recalculated", 0)
            if errors:
                flash(
                    "Обновление с ошибками: +{ins} обновлено {upd}, пересчитано {rec} прогнозов. Ошибки: {err}".format(
                        ins=inserted, upd=updated, rec=recalc, err=errors[0],
                    ),
                    "warning",
                )
            else:
                flash(
                    "Матчи и очки обновлены: +{ins} обновлено {upd}, пересчитано {rec}".format(
                        ins=inserted, upd=updated, rec=recalc,
                    ),
                    "success",
                )
        elif status == "scoring_failed":
            sync_summary = (sync_result.get("sync") or {})
            inserted = sync_summary.get("matches_inserted", 0)
            updated = sync_summary.get("matches_updated", 0)
            flash(
                "Матчи обновлены (+{ins} +{upd}), но пересчёт очков не удался. "
                "Запустите пересчёт вручную.".format(ins=inserted, upd=updated),
                "warning",
            )
        elif status == "skipped_already_running":
            flash("Обновление уже выполняется другим процессом", "info")
        else:
            flash("Обновление не выполнено (статус: {status})".format(
                status=status or "unknown",
            ), "error")
    except Exception as e:
        flash(f"Ошибка обновления: {e}", "error")

    return redirect(url_for("admin.admin"))


@admin_sync_bp.route("/sync-health", methods=["GET"])
@admin_required
def sync_health():
    return jsonify(get_sync_health())
