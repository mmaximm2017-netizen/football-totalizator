from pathlib import Path


def replace_one(path, old, new):
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected one match, found {count}: {old[:80]!r}")
    p.write_text(text.replace(old, new, 1), encoding="utf-8")


# 1) Source parser failures must become source-health failures.
path = "scripts/auto_result_worker.py"
replace_one(
    path,
    'FINAL_GRACE_MINUTES = 185\nMOSCOW_TZ = ZoneInfo("Europe/Moscow")',
    'FINAL_GRACE_MINUTES = 195\nMOSCOW_TZ = ZoneInfo("Europe/Moscow")',
)
replace_one(
    path,
    '''def _fetch_detail(cache: PageCache, source_name: str, url: str) -> str:\n    try:\n        return fetch_text(url)\n    except SourceError as exc:\n        cache.mark_error(source_name, str(exc))\n        raise\n''',
    '''def _fetch_detail(cache: PageCache, source_name: str, url: str) -> str:\n    try:\n        return fetch_text(url)\n    except SourceError as exc:\n        cache.mark_error(source_name, str(exc))\n        raise\n\n\ndef _guard_observation(cache: PageCache, source_name: str, callback):\n    try:\n        return callback()\n    except SourceError as exc:\n        cache.mark_error(source_name, str(exc))\n        raise\n''',
)
replace_one(
    path,
    '''    if scope == "rpl":\n        return (\n            find_livesport_result(\n                cache.page("livesport_rpl"),\n                home=match["home_team"],\n                away=match["away_team"],\n                match_date=match["match_date"],\n            ),\n            find_sports_rpl_result(\n                cache.page("sports_rpl"),\n                home=match["home_team"],\n                away=match["away_team"],\n                match_date=match["match_date"],\n            ),\n        )\n    if scope == "cup":\n        return (\n            find_livesport_result(\n                cache.page("livesport_cup"),\n                home=match["home_team"],\n                away=match["away_team"],\n                match_date=match["match_date"],\n            ),\n            _rfs_cup_observation(cache, match),\n        )\n    if scope == "national":\n        return (\n            _sportbox_national_observation(cache, match),\n            _rfs_national_observation(cache, match),\n        )\n''',
    '''    if scope == "rpl":\n        return (\n            _guard_observation(\n                cache,\n                "livesport_rpl",\n                lambda: find_livesport_result(\n                    cache.page("livesport_rpl"),\n                    home=match["home_team"],\n                    away=match["away_team"],\n                    match_date=match["match_date"],\n                ),\n            ),\n            _guard_observation(\n                cache,\n                "sports_rpl",\n                lambda: find_sports_rpl_result(\n                    cache.page("sports_rpl"),\n                    home=match["home_team"],\n                    away=match["away_team"],\n                    match_date=match["match_date"],\n                ),\n            ),\n        )\n    if scope == "cup":\n        return (\n            _guard_observation(\n                cache,\n                "livesport_cup",\n                lambda: find_livesport_result(\n                    cache.page("livesport_cup"),\n                    home=match["home_team"],\n                    away=match["away_team"],\n                    match_date=match["match_date"],\n                ),\n            ),\n            _guard_observation(\n                cache, "rfs_cup", lambda: _rfs_cup_observation(cache, match)\n            ),\n        )\n    if scope == "national":\n        return (\n            _guard_observation(\n                cache,\n                "sportbox_national",\n                lambda: _sportbox_national_observation(cache, match),\n            ),\n            _guard_observation(\n                cache,\n                "rfs_national",\n                lambda: _rfs_national_observation(cache, match),\n            ),\n        )\n''',
)
replace_one(
    path,
    '        if window_state(match["kickoff_time"], now) in {"active", "expired_grace"}:',
    '        if window_state(match["kickoff_time"], now) == "active":',
)

# 2) LiveSport structural validation + Sportbox ambiguity fail-closed.
path = "scripts/auto_result_sources.py"
replace_one(
    path,
    '''def find_livesport_result(html, *, home, away, match_date, source="livesport"):\n    text = plain_text(html); target = date.fromisoformat(match_date)\n    month_name = next(k for k,v in MONTHS.items() if v == target.month)\n    hit = _date_pattern(target.day, month_name, target.year).search(text)\n    if not hit: return Observation(source, STATUS_NOT_FOUND, detail="date_not_found")\n''',
    '''def find_livesport_result(html, *, home, away, match_date, source="livesport"):\n    text = plain_text(html); target = date.fromisoformat(match_date)\n    calendar_date = re.compile(\n        rf"\\b\\d{{1,2}}\\s+(?:{MONTH_RE})(?:,\\s*[^,]+)?(?:,\\s*)?20\\d{{2}}\\b",\n        re.I,\n    )\n    if not calendar_date.search(text):\n        raise SourceError("livesport_calendar_dates_missing")\n    month_name = next(k for k,v in MONTHS.items() if v == target.month)\n    hit = _date_pattern(target.day, month_name, target.year).search(text)\n    if not hit: return Observation(source, STATUS_NOT_FOUND, detail="date_not_found")\n''',
)
replace_one(
    path,
    '''def find_sportbox_candidate(candidates, *, home, away, match_date):\n    target=date.fromisoformat(match_date)\n    for x in candidates:\n        if x["day"]==target.day and x["month"]==target.month and team_matches(x["home"],home) and team_matches(x["away"],away): return x\n    return None\n''',
    '''def find_sportbox_candidate(candidates, *, home, away, match_date):\n    target=date.fromisoformat(match_date)\n    matches = [\n        x for x in candidates\n        if x["day"] == target.day\n        and x["month"] == target.month\n        and team_matches(x["home"], home)\n        and team_matches(x["away"], away)\n    ]\n    if len(matches) > 1:\n        raise SourceError("sportbox_candidate_ambiguous")\n    return matches[0] if matches else None\n''',
)

# 3) Re-check complete match identity under row lock before live write.
path = "app/services/auto_result_finalization_service.py"
replace_one(
    path,
    '''    *,\n    tournament_id: int,\n    league: str,\n) -> str:\n''',
    '''    *,\n    tournament_id: int,\n    league: str,\n    expected_home_team: str,\n    expected_away_team: str,\n    expected_kickoff_time,\n    expected_match_category: str,\n) -> str:\n''',
)
replace_one(
    path,
    '''            SELECT status, home_score, away_score, tournament_id, league\n            FROM matches\n''',
    '''            SELECT status, home_score, away_score, tournament_id, league,\n                   home_team, away_team, kickoff_time, COALESCE(match_category, '')\n            FROM matches\n''',
)
replace_one(
    path,
    '''        status, existing_home, existing_away, actual_tournament_id, actual_league = row\n        if actual_tournament_id != tournament_id or actual_league != league:\n            conn.rollback()\n            raise AutoResultFinalizeError("match_scope_changed")\n''',
    '''        (\n            status, existing_home, existing_away, actual_tournament_id, actual_league,\n            actual_home_team, actual_away_team, actual_kickoff_time, actual_match_category,\n        ) = row\n        if actual_tournament_id != tournament_id or actual_league != league:\n            conn.rollback()\n            raise AutoResultFinalizeError("match_scope_changed")\n        if (\n            actual_home_team != expected_home_team\n            or actual_away_team != expected_away_team\n            or actual_kickoff_time != expected_kickoff_time\n            or actual_match_category != expected_match_category\n        ):\n            conn.rollback()\n            raise AutoResultFinalizeError("match_identity_changed")\n''',
)

path = "scripts/auto_result_runtime.py"
replace_one(
    path,
    '''                    tournament_id=match["tournament_id"],\n                    league=match["league"],\n                )\n''',
    '''                    tournament_id=match["tournament_id"],\n                    league=match["league"],\n                    expected_home_team=match["home_team"],\n                    expected_away_team=match["away_team"],\n                    expected_kickoff_time=match["kickoff_time"],\n                    expected_match_category=match["match_category"],\n                )\n''',
)

# 4) Admin-only visible auto marker.
path = "app/services/admin_view_service.py"
replace_one(
    path,
    '''                       m.status, m.home_score, m.away_score, m.playoff_stage_manual\n                FROM matches m\n''',
    '''                       m.status, m.home_score, m.away_score, m.playoff_stage_manual,\n                       m.result_origin\n                FROM matches m\n''',
)
replace_one(
    path,
    '''                    "stage": row[8] or "", "tournament_id": tournament_id,\n                    "has_result": row[6] is not None and row[7] is not None,\n''',
    '''                    "stage": row[8] or "", "tournament_id": tournament_id,\n                    "result_origin": row[9],\n                    "is_auto_result": row[9] == "auto_result_worker",\n                    "has_result": row[6] is not None and row[7] is not None,\n''',
)
replace_one(
    path,
    '''               m.status, m.home_score, m.away_score, m.playoff_stage_manual\n        FROM matches m\n''',
    '''               m.status, m.home_score, m.away_score, m.playoff_stage_manual,\n               m.result_origin\n        FROM matches m\n''',
)
replace_one(
    path,
    '''            "home_score": row[6], "away_score": row[7], "stage": row[8] or "",\n            "tournament_id": tournament_id,\n''',
    '''            "home_score": row[6], "away_score": row[7], "stage": row[8] or "",\n            "tournament_id": tournament_id,\n            "result_origin": row[9],\n            "is_auto_result": row[9] == "auto_result_worker",\n''',
)

replace_one(
    "templates/admin_russia_2027.html",
    '''<span class="rpl-status-badge {% if is_pending and m.status not in live_statuses %}rpl-status-pending{% else %}rpl-status-{{ m.status|lower }}{% endif %}">{% if is_pending and m.status not in live_statuses %}ОЖИДАЕТ РЕЗУЛЬТАТА{% else %}{{ m.status }}{% endif %}</span>{% if is_pending and m.status in live_statuses %}<span class="rpl-pending-marker">ОЖИДАЕТ РЕЗУЛЬТАТА</span>{% endif %}</span></div>''',
    '''<span class="rpl-status-badge {% if is_pending and m.status not in live_statuses %}rpl-status-pending{% else %}rpl-status-{{ m.status|lower }}{% endif %}">{% if is_pending and m.status not in live_statuses %}ОЖИДАЕТ РЕЗУЛЬТАТА{% else %}{{ m.status }}{% endif %}</span>{% if m.is_auto_result %}<span class="admin-auto-result-badge" title="Результат добавлен автоматически">авто</span>{% endif %}{% if is_pending and m.status in live_statuses %}<span class="rpl-pending-marker">ОЖИДАЕТ РЕЗУЛЬТАТА</span>{% endif %}</span></div>''',
)
replace_one(
    "templates/admin_russian_cup.html",
    '''<span class="rc-status-badge {% if is_pending and m.status not in live_statuses %}rc-status-pending{% else %}rc-status-{{ m.status|lower }}{% endif %}">{% if is_pending and m.status not in live_statuses %}ОЖИДАЕТ РЕЗУЛЬТАТА{% else %}{{ m.status }}{% endif %}</span>{% if is_pending and m.status in live_statuses %}<span class="rc-pending-marker">ОЖИДАЕТ РЕЗУЛЬТАТА</span>{% endif %}</span></div>''',
    '''<span class="rc-status-badge {% if is_pending and m.status not in live_statuses %}rc-status-pending{% else %}rc-status-{{ m.status|lower }}{% endif %}">{% if is_pending and m.status not in live_statuses %}ОЖИДАЕТ РЕЗУЛЬТАТА{% else %}{{ m.status }}{% endif %}</span>{% if m.is_auto_result %}<span class="admin-auto-result-badge" title="Результат добавлен автоматически">авто</span>{% endif %}{% if is_pending and m.status in live_statuses %}<span class="rc-pending-marker">ОЖИДАЕТ РЕЗУЛЬТАТА</span>{% endif %}</span></div>''',
)

p = Path("static/css/admin-match-list.css")
text = p.read_text(encoding="utf-8")
if ".admin-auto-result-badge" in text:
    raise RuntimeError("admin-auto-result-badge already exists")
p.write_text(
    text + '\n.admin-auto-result-badge{display:inline-flex;align-items:center;padding:3px 7px;border-radius:999px;background:#eef3f7;color:#536475;font-size:10px;font-weight:900;line-height:1;white-space:nowrap}\n',
    encoding="utf-8",
)

# Tests: update mandatory identity args and add hardening contracts.
path = "tests/test_auto_result_live_runtime.py"
replace_one(
    path,
    'install_db(monkeypatch, ("SCHEDULED", None, None, 5, "rpl"))',
    'install_db(monkeypatch, ("SCHEDULED", None, None, 5, "rpl", "Зенит", "ЦСКА", "kickoff", "rpl"))',
)
replace_one(
    path,
    'install_db(monkeypatch, ("FINISHED", 1, 0, 5, "rpl"))',
    'install_db(monkeypatch, ("FINISHED", 1, 0, 5, "rpl", "Зенит", "ЦСКА", "kickoff", "rpl"))',
)
replace_one(
    path,
    'install_db(monkeypatch, ("SCHEDULED", None, None, 6, "rcup"))',
    'install_db(monkeypatch, ("SCHEDULED", None, None, 6, "rcup", "Зенит", "ЦСКА", "kickoff", "rpl"))',
)
text = Path(path).read_text(encoding="utf-8")
text = text.replace(
    'service.finalize_auto_result(401, 2, 1, tournament_id=5, league="rpl")',
    'service.finalize_auto_result(401, 2, 1, tournament_id=5, league="rpl", expected_home_team="Зенит", expected_away_team="ЦСКА", expected_kickoff_time="kickoff", expected_match_category="rpl")',
)
Path(path).write_text(text, encoding="utf-8")

hardening_test = Path("tests/test_auto_result_hardening.py")
hardening_test.write_text('''from datetime import datetime, timedelta, timezone\nfrom pathlib import Path\n\nimport pytest\n\nfrom app.services import auto_result_finalization_service as service\nfrom scripts import auto_result_worker as worker\nfrom scripts.auto_result_sources import (\n    SourceError,\n    find_livesport_result,\n    find_sportbox_candidate,\n)\n\n\ndef test_livesport_unparseable_page_is_source_error():\n    with pytest.raises(SourceError, match="livesport_calendar_dates_missing"):\n        find_livesport_result("<html>captcha</html>", home="Зенит", away="ЦСКА", match_date="2026-09-05")\n\n\ndef test_observation_parser_error_marks_source_unhealthy():\n    cache = worker.PageCache()\n    cache._pages["sports_rpl"] = "<html>changed markup</html>"\n    match = {"scope": "rpl", "home_team": "Зенит", "away_team": "ЦСКА", "match_date": "2026-09-05"}\n    cache._pages["livesport_rpl"] = "5 сентября, суббота, 2026 Зенит ЦСКА"\n    with pytest.raises(SourceError):\n        worker.observe_match(cache, match)\n    assert cache.source_status()["sports_rpl"]["ok"] is False\n\n\ndef test_final_notice_window_survives_one_missed_cron():\n    kickoff = datetime(2026, 9, 5, 11, 0, tzinfo=timezone.utc)\n    assert worker.window_state(kickoff, kickoff + timedelta(minutes=190)) == "expired_grace"\n    assert worker.window_state(kickoff, kickoff + timedelta(minutes=196)) == "expired"\n\n\ndef test_sportbox_ambiguous_same_day_match_fails_closed():\n    candidates = [\n        {"home": "Россия", "away": "Иран", "day": 10, "month": 10, "game_id": "1"},\n        {"home": "Россия", "away": "Иран", "day": 10, "month": 10, "game_id": "2"},\n    ]\n    with pytest.raises(SourceError, match="sportbox_candidate_ambiguous"):\n        find_sportbox_candidate(candidates, home="Россия", away="Иран", match_date="2026-10-10")\n\n\ndef test_admin_templates_show_auto_marker_and_list_loads_origin():\n    root = Path(__file__).resolve().parents[1]\n    service_text = (root / "app/services/admin_view_service.py").read_text(encoding="utf-8")\n    assert "m.result_origin" in service_text\n    assert '"is_auto_result": row[9] == "auto_result_worker"' in service_text\n    for name in ("admin_russia_2027.html", "admin_russian_cup.html"):\n        html = (root / "templates" / name).read_text(encoding="utf-8")\n        assert "admin-auto-result-badge" in html\n\n\ndef test_live_finalizer_has_identity_guard():\n    source = Path(service.__file__).read_text(encoding="utf-8")\n    assert "match_identity_changed" in source\n    assert "actual_home_team != expected_home_team" in source\n    assert "actual_kickoff_time != expected_kickoff_time" in source\n''', encoding="utf-8")
