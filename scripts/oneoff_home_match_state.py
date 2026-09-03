from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

main_path = ROOT / "app/routes/main.py"
text = main_path.read_text(encoding="utf-8-sig")
old_import = '''from app.db import close_db, get_db\nfrom app.services.tournament_context_service import ('''
new_import = '''from app.db import close_db, get_db\nfrom app.services.home_match_view_service import apply_home_match_card_state\nfrom app.services.tournament_context_service import ('''
if old_import not in text:
    raise RuntimeError("main import anchor not found")
text = text.replace(old_import, new_import, 1)

old_state = '''            else:\n                m["pred_home"] = ""\n                m["pred_away"] = ""\n                m["my_points"] = 0\n\n            grouped[day].append(m)'''
new_state = '''            else:\n                m["pred_home"] = ""\n                m["pred_away"] = ""\n                m["my_points"] = 0\n\n            apply_home_match_card_state(m)\n            grouped[day].append(m)'''
if old_state not in text:
    raise RuntimeError("main card-state anchor not found")
text = text.replace(old_state, new_state, 1)
main_path.write_text(text, encoding="utf-8")

partial_path = ROOT / "templates/partials/home/_day_block.html"
text = partial_path.read_text(encoding="utf-8")
old_setup = '''                {% set has_prediction = match.pred_home != '' %}\n                {% set card_state = 'active' %}\n                {% set predicted_class = '' %}\n'''
new_setup = '''                {% set has_prediction = match.has_prediction %}\n'''
if old_setup not in text:
    raise RuntimeError("day block setup anchor not found")
text = text.replace(old_setup, new_setup, 1)

old_logic = '''                {% if match.finished %}\n                    {% set card_state = 'finished' %}\n                {% elif match.deadline_passed %}\n                    {% set card_state = 'closed' %}\n                {% elif has_prediction %}\n                    {% set predicted_class = 'predicted' %}\n                {% endif %}\n\n'''
if old_logic not in text:
    raise RuntimeError("day block state logic not found")
text = text.replace(old_logic, "", 1)
text = text.replace("{{ card_state }} {{ predicted_class }}", "{{ match.card_state }} {{ match.predicted_class }}", 1)
text = text.replace(
    'data-finished="{% if match.finished %}1{% else %}0{% endif %}"',
    'data-finished="{{ match.data_finished }}"',
    1,
)
text = text.replace(
    'data-deadline-closed="{% if match.deadline_passed %}1{% else %}0{% endif %}"',
    'data-deadline-closed="{{ match.data_deadline_closed }}"',
    1,
)
partial_path.write_text(text, encoding="utf-8")

print("Home match card state wired through Python view model")
