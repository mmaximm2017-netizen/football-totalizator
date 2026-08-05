import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class RussianCupPredictionColorsTests(unittest.TestCase):
    def test_closed_prediction_uses_existing_rcup_stepper_lime(self):
        css = (ROOT / "static" / "css" / "tournaments" / "russian-cup.css").read_text(encoding="utf-8")

        self.assertIn("--rcup-lime: #C7F21A;", css)
        label_rule = css.split("body.tournament-rcup .match-card--russian-cup .closed-prediction-label {", 1)[1].split("}", 1)[0]
        score_rule = css.split("body.tournament-rcup .match-card--russian-cup .closed-prediction-score {", 1)[1].split("}", 1)[0]

        self.assertIn("color: var(--rcup-lime);", label_rule)
        self.assertIn("opacity: 0.78;", label_rule)
        self.assertIn("color: var(--rcup-lime);", score_rule)
        self.assertNotIn("!important", label_rule + score_rule)
        self.assertNotIn("body.tournament-rpl", label_rule + score_rule)
        self.assertNotIn("body.tournament-wc2026", label_rule + score_rule)


if __name__ == "__main__":
    unittest.main()
