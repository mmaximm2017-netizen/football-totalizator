from pathlib import Path
from unittest.mock import Mock

import pytest

from app.routes.profile import format_profile_points
from app.services import profile_stats_service as stats_service


class Cursor:
    def __init__(self, aggregate, recent_rows):
        self.aggregate = aggregate
        self.recent_rows = recent_rows
        self.calls = []

    def execute(self, query, params):
        self.calls.append((query, params))

    def fetchone(self):
        return self.aggregate

    def fetchall(self):
        return self.recent_rows


def aggregate(points, *, maximum_points=None, quality_counts=(0, 0, 0, 0), unexpected=0):
    return (
        len(points),
        sum(points),
        sum(points) / len(points) if points else 0,
        maximum_points if maximum_points is not None else len(points) * 11,
        *quality_counts,
        *(points.count(value) for value in stats_service.POINT_BUCKETS),
        unexpected,
    )


def service_with(monkeypatch, points, recent_points=None, *, maximum_points=None, quality_counts=(0, 0, 0, 0), unexpected=0):
    cursor = Cursor(aggregate(points, maximum_points=maximum_points, quality_counts=quality_counts, unexpected=unexpected), [(value,) for value in (recent_points if recent_points is not None else points[:10])])
    connection = Mock()
    connection.cursor.return_value = cursor
    monkeypatch.setattr(stats_service, "get_db", lambda: connection)
    monkeypatch.setattr(stats_service, "close_db", lambda conn, cur: None)
    return cursor


def test_stats_are_scoped_to_selected_user_and_tournament(monkeypatch):
    cursor = service_with(monkeypatch, [11, 0], [0, 11])

    result = stats_service.get_profile_stats(7, 42)

    assert result["submitted_count"] == 2
    assert [params for _, params in cursor.calls] == [(7, 42), (7, 42)]


def test_zero_points_are_a_real_bucket_and_missing_predictions_are_not(monkeypatch):
    service_with(monkeypatch, [0])

    result = stats_service.get_profile_stats(7, 42)

    buckets = {item["points"]: item["count"] for item in result["buckets"]}
    assert buckets[0] == 1
    assert sum(buckets.values()) == result["submitted_count"] == 1


def test_bucket_sum_matches_submitted_finished_predictions(monkeypatch):
    points = [11, 10, 8, 7, 5, 3, 2, 0, 0, 5]
    service_with(monkeypatch, points)

    result = stats_service.get_profile_stats(7, 42)

    assert sum(item["count"] for item in result["buckets"]) == result["submitted_count"] == len(points)


def test_unexpected_finished_point_value_violates_the_invariant(monkeypatch):
    service_with(monkeypatch, [0], unexpected=1)

    with pytest.raises(stats_service.ProfileStatsIntegrityError):
        stats_service.get_profile_stats(7, 42)


def test_percentage_and_average_use_each_finished_match_maximum(monkeypatch):
    service_with(monkeypatch, [10, 5], maximum_points=20)

    result = stats_service.get_profile_stats(7, 42)

    assert result["total_points"] == 15
    assert result["maximum_points"] == 20
    assert result["percentage"] == 75.0
    assert result["average_points"] == 7.5


@pytest.mark.parametrize(("maximum_points", "expected"), ((10, 10), (11, 11)))
def test_single_finished_match_uses_its_actual_score_maximum(monkeypatch, maximum_points, expected):
    cursor = service_with(monkeypatch, [expected], maximum_points=maximum_points)

    assert stats_service.get_profile_stats(7, 42)["maximum_points"] == expected
    assert "ABS(m.home_score - m.away_score) >= 3 THEN 11 ELSE 10" in cursor.calls[0][0]


def test_mixed_finished_match_maxima_exclude_missing_predictions(monkeypatch):
    service_with(monkeypatch, [10, 11], maximum_points=21)

    result = stats_service.get_profile_stats(7, 42)

    assert result["submitted_count"] == 2
    assert result["maximum_points"] == 21
    assert result["percentage"] == 100.0


def test_no_predictions_has_zero_percentage_without_division_by_zero(monkeypatch):
    service_with(monkeypatch, [], [])

    result = stats_service.get_profile_stats(7, 42)

    assert result["submitted_count"] == 0
    assert result["percentage"] == 0
    assert result["average_points"] == 0
    assert result["comparison"] is None
    assert all(item["count"] == 0 and item["percent"] == 0 for item in result["quality"].values())


def test_quality_metrics_use_submitted_finished_prediction_denominator(monkeypatch):
    service_with(monkeypatch, [7, 8, 10, 11, 0, 5, 3, 2], quality_counts=(5, 4, 2, 1))

    quality = stats_service.get_profile_stats(7, 42)["quality"]

    assert quality == {
        "correct_outcome": {"count": 5, "percent": 62.5},
        "seven_plus": {"count": 4, "percent": 50.0},
        "exact_score": {"count": 2, "percent": 25.0},
        "zero_points": {"count": 1, "percent": 12.5},
    }


def test_missing_prediction_is_excluded_from_quality_denominator_and_zero_count(monkeypatch):
    # The tournament may have more finished matches, but this service sees only submitted rows.
    service_with(monkeypatch, [7, 0], quality_counts=(1, 1, 0, 1))

    result = stats_service.get_profile_stats(7, 42)

    assert result["submitted_count"] == 2
    assert result["quality"]["zero_points"] == {"count": 1, "percent": 50.0}


@pytest.mark.parametrize(
    ("predicted", "actual", "expected"),
    (((2, 1), (3, 0), True), ((1, 1), (0, 0), True), ((0, 2), (1, 3), True), ((2, 1), (0, 1), False)),
)
def test_correct_outcome_sign_cases(predicted, actual, expected):
    predicted_sign = (predicted[0] > predicted[1]) - (predicted[0] < predicted[1])
    actual_sign = (actual[0] > actual[1]) - (actual[0] < actual[1])

    assert (predicted_sign == actual_sign) is expected


def test_quality_query_uses_canonical_outcome_exact_and_seven_plus_conditions(monkeypatch):
    cursor = service_with(monkeypatch, [7, 8, 10, 11, 0], quality_counts=(3, 4, 2, 1))

    stats_service.get_profile_stats(7, 42)

    query = cursor.calls[0][0]
    assert "SIGN(p.home_goals - p.away_goals) = SIGN(m.home_score - m.away_score)" in query
    assert "p.points IN (7, 8, 10, 11)" in query
    assert "p.home_goals = m.home_score AND p.away_goals = m.away_score" in query
    assert "p.points = 0" in query


def test_form_is_displayed_oldest_to_newest_and_compares_full_five_match_windows(monkeypatch):
    newest_first = [11, 10, 8, 7, 5, 3, 2, 0, 0, 5]
    service_with(monkeypatch, newest_first, newest_first)

    result = stats_service.get_profile_stats(7, 42)

    assert result["recent_points"] == list(reversed(newest_first))
    assert result["comparison"] == {"latest_five": 41, "previous_five": 10, "difference": 31}


def test_form_does_not_compare_incomplete_periods(monkeypatch):
    newest_first = [11, 10, 8, 7, 5, 3]
    service_with(monkeypatch, newest_first, newest_first)

    assert stats_service.get_profile_stats(7, 42)["comparison"] is None


def test_profile_links_preserve_tid_and_stats_page_is_tournament_aware():
    root = Path(__file__).resolve().parents[1]
    source = (root / "templates" / "profile.html").read_text(encoding="utf-8")
    base = (root / "templates" / "base.html").read_text(encoding="utf-8")

    assert "url_for('profile.profile_stats', tid=current_tournament_id)" in source
    assert "url_for('predictions.my_predictions', tid=current_tournament_id)" in source
    assert "'/profile/stats'" in base


def test_template_splits_full_form_into_previous_and_latest_groups_only_with_comparison():
    source = (Path(__file__).resolve().parents[1] / "templates" / "profile_stats.html").read_text(encoding="utf-8")

    assert "form-groups" in source
    assert "form-divider" in source
    assert "Предыдущие 5" in source
    assert "Последние 5" in source
    assert "stats.recent_points[:5]" in source
    assert "stats.recent_points[5:]" in source
    assert "stats.comparison.previous_five" in source
    assert "stats.comparison.latest_five" in source


@pytest.mark.parametrize(
    ("value", "expected"),
    ((0, "0 очков"), (1, "1 очко"), (2, "2 очка"), (4, "4 очка"), (5, "5 очков"), (11, "11 очков"), (14, "14 очков"), (21, "21 очко"), (22, "22 очка"), (25, "25 очков"), (-1, "1 очко"), (-2, "2 очка"), (-4, "4 очка"), (-5, "5 очков"), (-11, "11 очков"), (-21, "21 очко"), (-22, "22 очка")),
)
def test_form_delta_reuses_russian_points_declension(value, expected):
    assert format_profile_points(value) == expected


def test_template_uses_declined_delta_and_compact_comparison_labels():
    source = (Path(__file__).resolve().parents[1] / "templates" / "profile_stats.html").read_text(encoding="utf-8")

    assert "format_profile_points(stats.comparison.difference)" in source
    assert "Сравнение последних 5 прогнозов с предыдущими 5." in source
    assert "Слева — более старые, справа — более новые." not in source
    assert ".comparison-grid .profile-stats-label" in source
    assert "white-space: nowrap" in source


def test_maximum_explanation_is_in_summary_not_distribution():
    source = (Path(__file__).resolve().parents[1] / "templates" / "profile_stats.html").read_text(encoding="utf-8")
    summary = source.split('<section class="profile-stats-card">', 2)[1]
    distribution = source.split('<section class="profile-stats-card">', 3)[2]

    assert "Максимум зависит от итогового счёта матча: 10 или 11 очков." in summary
    assert "Максимум зависит от итогового счёта матча: 10 или 11 очков." not in distribution


def test_template_renders_quality_grid_for_own_and_public_stats():
    source = (Path(__file__).resolve().parents[1] / "templates" / "profile_stats.html").read_text(encoding="utf-8")

    for label in ("Качество прогнозов", "Верный исход", "7+ очков", "Точный счёт", "0 очков"):
        assert label in source
    assert "stats.quality.correct_outcome" in source
    assert "stats.quality.seven_plus" in source
    assert "stats.quality.exact_score" in source
    assert "stats.quality.zero_points" in source
    for theme in ("body.tournament-rpl", "body.tournament-rcup", "body.tournament-wc2026"):
        assert theme in source
