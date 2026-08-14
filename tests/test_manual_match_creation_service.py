import unittest
from datetime import timezone

from app.services.manual_match_creation_service import (
    DuplicateMatchError,
    ManualMatchCreateData,
    ManualMatchValidationError,
    create_manual_match,
)


class Cursor:
    def __init__(self, results=None):
        self.results = list(results or [])
        self.executed = []

    def execute(self, query, params=None):
        self.executed.append((query, params))

    def fetchone(self):
        return self.results.pop(0) if self.results else None


class ManualMatchCreationServiceTests(unittest.TestCase):
    def make_data(self, **overrides):
        values = {
            "tournament_id": 5,
            "league": "rpl",
            "home_team": "Зенит",
            "away_team": "Динамо",
            "match_date": "2026-08-16",
            "match_time": "14:30",
            "reject_early_auto_deadline": True,
        }
        values.update(overrides)
        return ManualMatchCreateData(**values)

    def test_creates_rpl_match_with_moscow_time_and_standard_deadline(self):
        cursor = Cursor([None, (42,)])

        match_id = create_manual_match(cursor, self.make_data())

        self.assertEqual(match_id, 42)
        duplicate_sql, duplicate_params = cursor.executed[0]
        insert_sql, insert_params = cursor.executed[1]
        self.assertIn("tournament_id = %s", duplicate_sql)
        self.assertIn("league = %s", duplicate_sql)
        self.assertEqual(duplicate_params[:4], (5, "rpl", "Зенит", "Динамо"))
        self.assertIn("INSERT INTO matches", insert_sql)
        self.assertEqual(insert_params[0:2], ("Зенит", "Динамо"))
        self.assertEqual(insert_params[2].tzinfo, timezone.utc)
        self.assertEqual(insert_params[2].strftime("%Y-%m-%d %H:%M"), "2026-08-16 11:30")
        self.assertEqual(insert_params[3].strftime("%Y-%m-%d %H:%M"), "2026-08-16 08:00")
        self.assertEqual(insert_params[5:8], ("rpl", 5, ""))

    def test_duplicate_stops_before_insert(self):
        cursor = Cursor([(9,)])
        with self.assertRaisesRegex(DuplicateMatchError, "уже существует"):
            create_manual_match(cursor, self.make_data())
        self.assertEqual(len(cursor.executed), 1)

    def test_rejects_same_team_before_sql(self):
        cursor = Cursor()
        with self.assertRaisesRegex(ManualMatchValidationError, "отличаться"):
            create_manual_match(cursor, self.make_data(away_team="Зенит"))
        self.assertEqual(cursor.executed, [])

    def test_rejects_finished_creation(self):
        cursor = Cursor()
        with self.assertRaisesRegex(ManualMatchValidationError, "сначала создайте матч"):
            create_manual_match(cursor, self.make_data(status="FINISHED"))

    def test_requires_manual_deadline_for_early_match(self):
        cursor = Cursor()
        with self.assertRaisesRegex(ManualMatchValidationError, "дедлайн вручную"):
            create_manual_match(cursor, self.make_data(match_time="11:00"))

    def test_manual_deadline_has_priority(self):
        cursor = Cursor([None, (43,)])
        create_manual_match(cursor, self.make_data(
            match_time="11:00",
            deadline_date="2026-08-15",
            deadline_time="10:00",
        ))
        insert_params = cursor.executed[1][1]
        self.assertEqual(insert_params[3].strftime("%Y-%m-%d %H:%M"), "2026-08-15 07:00")


if __name__ == "__main__":
    unittest.main()
