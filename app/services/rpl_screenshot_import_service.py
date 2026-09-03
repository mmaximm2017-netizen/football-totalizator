"""RPL compatibility adapter for the shared screenshot importer."""
from datetime import datetime
import os

from app.services.rpl_team_catalog import RPL_TEAM_ALIASES, match_rpl_team, normalize_team_text
from app.services.screenshot_match_import_service import (
    MAX_IMAGE_BYTES, MAX_IMAGE_PIXELS, ALLOWED_FORMATS, ALLOWED_MIME_TYPES,
    DRAFT_TTL_SECONDS, ImageValidationError, ImportConfig, _valid_time,
    _candidate_team as _generic_candidate_team, _exact_team_spans, _match_from_parts, _spatial_parse_ocr,
    _flat_parse_ocr, resolve_match_date, make_generic_draft,
    generic_draft_is_valid, parse_ocr, save_validated_upload as _generic_save_validated_upload,
)
from app.services.local_tesseract_service import extract_text_from_image

RPL_IMPORTER_KEY = "rpl"
DATE_RE = __import__('re').compile(r"(?<!\d)(\d{1,2})[.](\d{1,2})(?!\d)")
TIME_RE = __import__('re').compile(r"(?<![\d:])(\d{1,2}):(\d{2})(?![\d:])")
RPL_ALIAS_TOKENS = {token for aliases in RPL_TEAM_ALIASES.values() for alias in aliases for token in normalize_team_text(alias).split()}

def save_validated_upload(upload):
    """Compatibility entry point preserving patchable RPL validation limits."""
    from app.services import screenshot_match_import_service as generic
    previous = (generic.MAX_IMAGE_BYTES, generic.MAX_IMAGE_PIXELS)
    generic.MAX_IMAGE_BYTES, generic.MAX_IMAGE_PIXELS = MAX_IMAGE_BYTES, MAX_IMAGE_PIXELS
    try:
        return _generic_save_validated_upload(upload)
    finally:
        generic.MAX_IMAGE_BYTES, generic.MAX_IMAGE_PIXELS = previous
RPL_IMPORT_CONFIG = ImportConfig(
    RPL_IMPORTER_KEY, "rpl", "rpl", RPL_TEAM_ALIASES, match_rpl_team,
    {token for aliases in RPL_TEAM_ALIASES.values() for alias in aliases
     for token in normalize_team_text(alias).split()},
)

# Private names were historically imported by focused tests; keep aliases while
# retaining one implementation in the generic module.
_spatial_parse_rpl_ocr = _spatial_parse_ocr
_flat_parse_rpl_ocr = _flat_parse_ocr

def parse_rpl_ocr(result, tournament, diagnostics=None):
    return parse_ocr(result, tournament, RPL_IMPORT_CONFIG, diagnostics)

def _candidate_team(value):
    """Backward-compatible RPL helper using the shared matcher context."""
    from app.services import screenshot_match_import_service as generic
    previous = (generic.team_aliases, generic.alias_tokens, generic.team_matcher)
    generic.team_aliases = RPL_IMPORT_CONFIG.team_aliases
    generic.alias_tokens = RPL_IMPORT_CONFIG.alias_tokens
    generic.team_matcher = RPL_IMPORT_CONFIG.team_matcher
    try:
        return _generic_candidate_team(value)
    finally:
        generic.team_aliases, generic.alias_tokens, generic.team_matcher = previous

def make_draft(user_id, tournament_id, matches, raw_text=""):
    return make_generic_draft(user_id, tournament_id, matches, RPL_IMPORTER_KEY, "rpl", raw_text)

def draft_is_valid(draft, user_id, tournament_id):
    return generic_draft_is_valid(draft, user_id, tournament_id, RPL_IMPORTER_KEY, "rpl")

def validate_confirmed_fields(match):
    reasons = []
    home, hs = match_rpl_team(match.get("home_team")); away, aws = match_rpl_team(match.get("away_team"))
    if hs != "ready": reasons.append("Домашняя команда не входит в каталог РПЛ")
    if aws != "ready": reasons.append("Гостевая команда не входит в каталог РПЛ")
    if home and away and home == away: reasons.append("Команды должны отличаться")
    try: datetime.strptime(str(match.get("date") or "").strip(), "%Y-%m-%d")
    except ValueError: reasons.append("Некорректная дата")
    raw_time = str(match.get("time") or "").strip()
    if _valid_time(raw_time) != raw_time: reasons.append("Некорректное время")
    elif raw_time <= "11:00": reasons.append("Матч в 11:00 МСК или раньше требует ручного дедлайна; добавьте его через ручную форму")
    return {**match, "home_team": home or str(match.get("home_team") or "").strip(), "away_team": away or str(match.get("away_team") or "").strip(), "status": "invalid" if reasons else "ready", "reasons": reasons}

def mark_preview_duplicates(cur, draft, tournament_id):
    from app.services.manual_match_creation_service import build_manual_deadline_utc
    seen = set()
    for match in draft["matches"]:
        checked = validate_confirmed_fields(match)
        match.update(checked)
        if checked["status"] != "ready":
            continue
        kickoff, _ = build_manual_deadline_utc(checked["date"], checked["time"])
        key = (checked["home_team"], checked["away_team"], kickoff)
        if key in seen: match.update(status="invalid", reasons=["Дубликат внутри черновика"]); continue
        seen.add(key)
        cur.execute("SELECT id FROM matches WHERE tournament_id=%s AND league='rpl' AND home_team=%s AND away_team=%s AND kickoff_time=%s", (tournament_id, checked["home_team"], checked["away_team"], kickoff))
        if cur.fetchone(): match.update(status="invalid", reasons=["Такой матч уже существует"])
    return draft

def run_import(upload, tournament, user_id):
    path = save_validated_upload(upload)
    try:
        result = extract_text_from_image(path); matches = parse_rpl_ocr(result, tournament)
        if not matches: raise ImageValidationError("На изображении не найдено расписание матчей РПЛ")
        return make_draft(user_id, tournament["id"], matches, result.raw_text)
    finally:
        try: os.unlink(path)
        except FileNotFoundError: pass
