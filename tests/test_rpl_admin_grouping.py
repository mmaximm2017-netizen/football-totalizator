import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from app.services.rpl_admin_service import build_rpl_match_groups


MSK = ZoneInfo("Europe/Moscow")


def match(match_id, when, status, stage=""):
    return {"id": match_id, "kickoff_time": when, "status": status, "stage": stage}


class RplAdminGroupingTests(unittest.TestCase):
    def setUp(self):
        self.today = datetime(2027, 7, 18, tzinfo=MSK).date()

    def groups_by_key(self, matches):
        return {group["key"]: group for group in build_rpl_match_groups(matches, self.today)}

    def test_today_matches_take_priority_over_finished_status(self):
        groups = self.groups_by_key([
            match(1, datetime(2027, 7, 18, 19, 0, tzinfo=MSK), "FINISHED"),
        ])
        self.assertEqual(groups["today"]["count"], 1)
        self.assertNotIn("finished", groups)

    def test_upcoming_matches_sort_from_earlier_to_later(self):
        groups = self.groups_by_key([
            match(2, datetime(2027, 7, 20, 19, 0, tzinfo=MSK), "SCHEDULED"),
            match(1, datetime(2027, 7, 19, 14, 0, tzinfo=MSK), "TIMED"),
        ])
        matches = groups["upcoming"]["sections"][0]["date_groups"][0]["matches"]
        self.assertEqual([item["id"] for item in matches], [1])

    def test_finished_matches_sort_from_newest_to_oldest(self):
        groups = self.groups_by_key([
            match(1, datetime(2027, 7, 15, 19, 0, tzinfo=MSK), "FINISHED"),
            match(2, datetime(2027, 7, 17, 19, 0, tzinfo=MSK), "FINISHED"),
        ])
        section_matches = groups["finished"]["sections"][0]["date_groups"]
        self.assertEqual(section_matches[0]["matches"][0]["id"], 2)

    def test_known_tours_group_matches_together(self):
        groups = self.groups_by_key([
            match(1, datetime(2027, 7, 20, 14, 0, tzinfo=MSK), "SCHEDULED", "Тур 2"),
            match(2, datetime(2027, 7, 21, 18, 0, tzinfo=MSK), "SCHEDULED", "Тур 2"),
        ])
        section = groups["upcoming"]["sections"][0]
        self.assertEqual(section["label"], "Тур 2")
        self.assertEqual([item["id"] for item in section["matches"]], [1, 2])

    def test_no_matches_returns_empty_groups(self):
        self.assertEqual(build_rpl_match_groups([], self.today), [])
