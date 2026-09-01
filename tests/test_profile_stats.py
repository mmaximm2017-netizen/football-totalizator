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


def aggregate(points, *, maximum_points=None, unexpected=0):
    return (
        len(points),
        sum(points),
        sum(points) / len(points) if points else 0,
        maximum_points if maximum_points is not None else len(points) * 11,
        *(points.count(value) for value in stats_service.POINT_BUCKETS),
        unexpected,
    )


def service_with(monkeypatch, points, recent_points=None, *, maximum_points=None, unexpected=0):
    cursor = Cursor(aggregate(points, maximum_points=maximum_points, unexpected=unexpected), [(value,) for value in (recent_points if recent_points is not None else points[:10])])
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
