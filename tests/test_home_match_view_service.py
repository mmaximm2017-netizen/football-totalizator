import unittest

from app.services.home_match_view_service import apply_home_match_card_state


class HomeMatchViewServiceTests(unittest.TestCase):
    def test_finished_has_priority_over_deadline_and_prediction(self):
        match = {
            "finished": True,
            "deadline_passed": True,
            "pred_home": 2,
        }
        result = apply_home_match_card_state(match)
        self.assertEqual(result["card_state"], "finished")
        self.assertEqual(result["predicted_class"], "")
        self.assertEqual(result["data_finished"], "1")
        self.assertEqual(result["data_deadline_closed"], "1")
        self.assertFalse(result["prediction_editable"])

    def test_closed_unfinished_match_is_not_marked_predicted(self):
        match = {
            "finished": False,
            "deadline_passed": True,
            "pred_home": 1,
        }
        result = apply_home_match_card_state(match)
        self.assertEqual(result["card_state"], "closed")
        self.assertEqual(result["predicted_class"], "")
        self.assertTrue(result["has_prediction"])
        self.assertFalse(result["prediction_editable"])

    def test_open_prediction_gets_predicted_class(self):
        match = {
            "finished": False,
            "deadline_passed": False,
            "pred_home": 0,
        }
        result = apply_home_match_card_state(match)
        self.assertEqual(result["card_state"], "active")
        self.assertEqual(result["predicted_class"], "predicted")
        self.assertTrue(result["has_prediction"])
        self.assertTrue(result["prediction_editable"])

    def test_open_match_without_prediction_is_plain_active(self):
        match = {
            "finished": False,
            "deadline_passed": False,
            "pred_home": "",
        }
        result = apply_home_match_card_state(match)
        self.assertEqual(result["card_state"], "active")
        self.assertEqual(result["predicted_class"], "")
        self.assertFalse(result["has_prediction"])
        self.assertEqual(result["data_finished"], "0")
        self.assertEqual(result["data_deadline_closed"], "0")


if __name__ == "__main__":
    unittest.main()
