from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
path = ROOT / "tests/test_bets_sheet.py"
text = path.read_text(encoding="utf-8")

old = '''        self.assertIn("card_state = 'finished'", template)\n        self.assertIn("rpl_class = 'match-card--rpl' if match.is_rpl_category and not is_rcup_match", template)\n        self.assertIn("match-card-v2 {{ card_state }}", template)'''
new = '''        view_service = (ROOT / "app" / "services" / "home_match_view_service.py").read_text(encoding="utf-8")\n        self.assertIn('card_state = "finished"', view_service)\n        self.assertIn("rpl_class = 'match-card--rpl' if match.is_rpl_category and not is_rcup_match", template)\n        self.assertIn("match-card-v2 {{ match.card_state }}", template)'''
if old not in text:
    raise RuntimeError("finished card assertion block not found")
text = text.replace(old, new, 1)

old = '''        self.assertIn("russian_cup_class = 'match-card--russian-cup' if is_rcup_match", home_template)\n        self.assertIn("card_state = 'finished'", home_template)\n        self.assertIn("card_state = 'closed'", home_template)\n        self.assertIn("class=\\\"match-card {{ russian_cup_class }} match-card-v2 {{ card_state }}", home_template)'''
new = '''        view_service = (ROOT / "app" / "services" / "home_match_view_service.py").read_text(encoding="utf-8")\n        self.assertIn("russian_cup_class = 'match-card--russian-cup' if is_rcup_match", home_template)\n        self.assertIn('card_state = "finished"', view_service)\n        self.assertIn('card_state = "closed"', view_service)\n        self.assertIn("class=\\\"match-card {{ russian_cup_class }} match-card-v2 {{ match.card_state }}", home_template)'''
if old not in text:
    raise RuntimeError("Russian Cup card assertion block not found")
text = text.replace(old, new, 1)

path.write_text(text, encoding="utf-8")
print("Updated card-state tests for Python view model")
