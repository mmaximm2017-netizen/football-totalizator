from pathlib import Path

import pytest
from flask import Flask, render_template


@pytest.mark.parametrize("template", ["login.html", "table.html"])
def test_pages_start_with_html_document_without_tool_output(template):
    app = Flask(__name__, template_folder=str(Path(__file__).resolve().parents[1] / "templates"))
    app.add_url_rule("/logout", "auth.logout", lambda: "")
    with app.test_request_context("/login" if template == "login.html" else "/table?tid=6"):
        html = render_template(template, tournaments=[], active_tournaments=[], table=[],
                               top_scorers=[], current_tournament_name="Кубок России")
    assert html.lstrip().startswith("<!DOCTYPE html>")
    assert "Warning: truncated output" not in html
    assert "Total output lines:" not in html
    assert "tokens truncated" not in html
