# app/services/point_service.py

from app.services.scoring_recalculation_service import (
    recalc_all_points,
    recalc_match_points,
)


def calculate_points_for_match(match_id):
    return recalc_match_points(match_id)


def calculate_all_points():
    return recalc_all_points()
