from datetime import date, datetime
import os
import re
import tempfile
import time
import uuid
import warnings

from PIL import Image, UnidentifiedImageError

from app.services.local_tesseract_service import extract_text_from_image
from app.services.rpl_team_catalog import RPL_TEAM_ALIASES, match_rpl_team, normalize_team_text


MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_IMAGE_PIXELS = 20_000_000
ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP"}
ALLOWED_MIME_TYPES = {"image/jpeg", "image/png", "image/webp"}
DRAFT_TTL_SECONDS = 30 * 60
DATE_RE = re.compile(r"(?<!\d)(\d{1,2})[.](\d{1,2})(?!\d)")
TIME_RE = re.compile(r"(?<![\d:])(\d{1,2}):(\d{2})(?![\d:])")
IGNORED_TEXT = {
    "футбол", "премьер лига", "россия", "таблица", "все игры", "live",
    "избранное", "турниры", "воскресенье", "суббота", "пятница", "четверг",
    "среда", "вторник", "понедельник",
}


def _catalog_alias_tokens():
    return {
        token
        for aliases in RPL_TEAM_ALIASES.values()
        for alias in aliases
        for token in normalize_team_text(alias).split()
    }


RPL_ALIAS_TOKENS = _catalog_alias_tokens()


class ImageValidationError(ValueError):
    pass


def save_validated_upload(upload):
    if upload is None or not upload.filename:
        raise ImageValidationError("Выберите изображение")
    if upload.mimetype not in ALLOWED_MIME_TYPES:
        raise ImageValidationError("Недопустимый MIME type изображения")
    stream = upload.stream
    stream.seek(0, os.SEEK_END)
    size = stream.tell()
    stream.seek(0)
    if size <= 0 or size > MAX_IMAGE_BYTES:
        raise ImageValidationError("Размер изображения превышает 8 МБ")

    old_limit = Image.MAX_IMAGE_PIXELS
    Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(stream) as image:
                if image.format not in ALLOWED_FORMATS:
                    raise ImageValidationError("Поддерживаются только JPEG, PNG и WEBP")
                image.verify()
            stream.seek(0)
            with Image.open(stream) as image:
                image.load()
                if image.width * image.height > MAX_IMAGE_PIXELS:
                    raise ImageValidationError("Слишком большое разрешение изображения")
                suffix = ".jpg" if image.format == "JPEG" else f".{image.format.lower()}"
    except (UnidentifiedImageError, OSError, SyntaxError, Image.DecompressionBombError,
            Image.DecompressionBombWarning) as exc:
        raise ImageValidationError("Файл не является корректным изображением") from exc
    finally:
        Image.MAX_IMAGE_PIXELS = old_limit
        stream.seek(0)

    handle = tempfile.NamedTemporaryFile(prefix="totish-rpl-", suffix=suffix, delete=False)
    try:
        while chunk := stream.read(64 * 1024):
            handle.write(chunk)
    finally:
        handle.close()
    return handle.name


def _valid_time(text):
    match = TIME_RE.search(text)
    if not match:
        return None
    hour, minute = map(int, match.groups())
    return f"{hour:02d}:{minute:02d}" if hour <= 23 and minute <= 59 else None


def resolve_match_date(day, month, tournament):
    candidates = []
    for field in ("start_date", "end_date"):
        raw = tournament.get(field)
        if raw:
            try:
                parsed = datetime.strptime(str(raw)[:10], "%Y-%m-%d").date()
                candidates.extend((parsed.year - 1, parsed.year, parsed.year + 1))
            except ValueError:
                pass
    start = str(tournament.get("start_date") or "")[:10]
    end = str(tournament.get("end_date") or "")[:10]
    if start and not end:
        try:
            start_date = datetime.strptime(start, "%Y-%m-%d").date()
            year = start_date.year if month >= start_date.month else start_date.year + 1
            candidate = date(year, month, day)
            return candidate.isoformat() if 0 <= (candidate - start_date).days <= 370 else ""
        except ValueError:
            return ""
    valid = []
    for year in sorted(set(candidates)):
        try:
            candidate = date(year, month, day)
        except ValueError:
            continue
        if (not start or candidate.isoformat() >= start) and (not end or candidate.isoformat() <= end):
            valid.append(candidate.isoformat())
    return valid[0] if len(valid) == 1 else ""


def _candidate_team(line):
    text = re.sub(r"(?:^|\s)\d+(?:[.,]\d+)?(?:\s|$)", " ", line)
    text = re.sub(r"[^A-Za-zА-Яа-яЁё\-\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    normalized = normalize_team_text(text)
    if not text or len(text) < 3 or normalized in IGNORED_TEXT:
        return None
    canonical, status = match_rpl_team(text)
    if canonical:
        return {"raw": text, "canonical": canonical, "status": status}

    tokens = normalized.split()
    candidates = []
    for team, aliases in RPL_TEAM_ALIASES.items():
        for alias in aliases:
            alias_tokens = normalize_team_text(alias).split()
            length = len(alias_tokens)
            for start in range(len(tokens) - length + 1):
                if tokens[start:start + length] == alias_tokens:
                    candidates.append((team, start, start + length, length))

    if candidates:
        longest = max(candidate[3] for candidate in candidates)
        strongest = {
            (team, start, end)
            for team, start, end, length in candidates
            if length == longest
        }
        canonical_candidates = {team for team, _, _ in strongest}
        if len(canonical_candidates) == 1:
            team = next(iter(canonical_candidates))
            spans = {(start, end) for candidate, start, end in strongest if candidate == team}
            if len(spans) == 1:
                start, end = next(iter(spans))
                residual = tokens[:start] + tokens[end:]
                safe_residual = all(
                    token not in RPL_ALIAS_TOKENS
                    and token.isalpha()
                    and len(token) <= 3
                    and not (len(token) == 1 and token.isascii())
                    for token in residual
                )
                if residual and safe_residual:
                    return {"raw": text, "canonical": team, "status": "ready"}
    if any(word in normalized for word in IGNORED_TEXT):
        return None
    return {"raw": text, "canonical": "", "status": "needs_review"}


def parse_rpl_ocr(result, tournament):
    raw_text = result.raw_text
    date_match = next(
        (match for match in DATE_RE.finditer(raw_text)
         if 1 <= int(match.group(1)) <= 31 and 1 <= int(match.group(2)) <= 12),
        None,
    )
    display_date = date_match.group(0) if date_match else ""
    match_date = resolve_match_date(int(date_match.group(1)), int(date_match.group(2)), tournament) if date_match else ""
    teams = []
    times = []
    in_schedule = not date_match
    for line in result.lines:
        line_dates = DATE_RE.findall(line.text)
        if date_match and date_match.groups() in line_dates:
            in_schedule = True
            continue
        if "таблица" in normalize_team_text(line.text):
            break
        if not in_schedule:
            continue
        if value := _valid_time(line.text):
            times.append(value)
        candidate = _candidate_team(line.text)
        if candidate:
            teams.append(candidate)

    matches = []
    for index in range(0, len(teams) - 1, 2):
        home, away = teams[index:index + 2]
        kickoff = times[index // 2] if index // 2 < len(times) else ""
        reasons = []
        if not home["canonical"]:
            reasons.append(f"Домашняя команда «{home['raw']}» не распознана")
        if not away["canonical"]:
            reasons.append(f"Гостевая команда «{away['raw']}» не распознана")
        if not match_date:
            reasons.append("Требуется указать полный год даты")
        if not kickoff:
            reasons.append("Время не распознано")
        elif kickoff <= "11:00":
            reasons.append(
                "Матч в 11:00 МСК или раньше требует ручного дедлайна; "
                "добавьте его через ручную форму"
            )
        if home["canonical"] and home["canonical"] == away["canonical"]:
            reasons.append("Команды должны отличаться")
        matches.append({
            "raw_home_team": home["raw"], "raw_away_team": away["raw"],
            "home_team": home["canonical"] or home["raw"],
            "away_team": away["canonical"] or away["raw"],
            "date": match_date, "display_date": display_date, "time": kickoff,
            "status": "needs_review" if reasons else "ready", "reasons": reasons,
        })
    return matches


def make_draft(user_id, tournament_id, matches, raw_text):
    return {
        "id": uuid.uuid4().hex,
        "user_id": int(user_id), "tournament_id": int(tournament_id),
        "created_at": int(time.time()), "matches": matches,
    }


def draft_is_valid(draft, user_id, tournament_id):
    return bool(
        isinstance(draft, dict)
        and draft.get("user_id") == int(user_id)
        and draft.get("tournament_id") == int(tournament_id)
        and time.time() - draft.get("created_at", 0) <= DRAFT_TTL_SECONDS
    )


def validate_confirmed_fields(match):
    reasons = []
    home, home_status = match_rpl_team(match.get("home_team"))
    away, away_status = match_rpl_team(match.get("away_team"))
    if home_status != "ready":
        reasons.append("Домашняя команда не входит в каталог РПЛ")
    if away_status != "ready":
        reasons.append("Гостевая команда не входит в каталог РПЛ")
    if home and away and home == away:
        reasons.append("Команды должны отличаться")
    raw_date = str(match.get("date") or "").strip()
    try:
        datetime.strptime(raw_date, "%Y-%m-%d")
    except ValueError:
        reasons.append("Некорректная дата")
    raw_time = str(match.get("time") or "").strip()
    if _valid_time(raw_time) != raw_time:
        reasons.append("Некорректное время")
    elif raw_time <= "11:00":
        reasons.append(
            "Матч в 11:00 МСК или раньше требует ручного дедлайна; "
            "добавьте его через ручную форму"
        )
    return {
        **match, "home_team": home or str(match.get("home_team") or "").strip(),
        "away_team": away or str(match.get("away_team") or "").strip(),
        "status": "invalid" if reasons else "ready", "reasons": reasons,
    }


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
        if key in seen:
            match["status"] = "invalid"
            match["reasons"] = ["Дубликат внутри черновика"]
            continue
        seen.add(key)
        cur.execute(
            """
            SELECT id FROM matches
            WHERE tournament_id = %s AND league = 'rpl'
              AND home_team = %s AND away_team = %s AND kickoff_time = %s
            """,
            (tournament_id, checked["home_team"], checked["away_team"], kickoff),
        )
        if cur.fetchone():
            match["status"] = "invalid"
            match["reasons"] = ["Такой матч уже существует"]
    return draft


def run_import(upload, tournament, user_id):
    path = save_validated_upload(upload)
    try:
        result = extract_text_from_image(path)
        matches = parse_rpl_ocr(result, tournament)
        if not matches:
            raise ImageValidationError("На изображении не найдено расписание матчей РПЛ")
        return make_draft(user_id, tournament["id"], matches, result.raw_text)
    finally:
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
