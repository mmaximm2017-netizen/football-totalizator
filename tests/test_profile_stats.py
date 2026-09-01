from pathlib import Path
from unittest.mock import Mock

import pytest

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


def aggregate(points, *, unexpected=0):
    return (
        len(points),
        sum(points),
        sum(points) / len(points) if points else 0,
        *(points.count(value) for value in stats_service.POINT_BUCKETS),
        unexpected,
    )


def service_with(monkeypatch, points, recent_points=None, *, unexpected=0):
    cursor = Cursor(aggregate(points, unexpected=unexpected), [(value,) for value in (recent_points if recent_points is not None else points[:10])])
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


def test_percentage_and_average_use_only_submitted_finished_predictions(monkeypatch):
    service_with(monkeypatch, [11, 5])

    result = stats_service.get_profile_stats(7, 42)

    assert result["total_points"] == 16
    assert result["maximum_points"] == 22
    assert result["percentage"] == 72.7
    assert result["average_points"] == 8.0


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
