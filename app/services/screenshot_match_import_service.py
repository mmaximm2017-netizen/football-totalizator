from datetime import date, datetime
import logging
import os
import re
import tempfile
import time
import uuid
import warnings

from PIL import Image, UnidentifiedImageError

from app.services.rpl_team_catalog import normalize_team_text


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
        for aliases in team_aliases.values()
        for alias in aliases
        for token in normalize_team_text(alias).split()
    }


team_aliases = {}
alias_tokens = set()
team_matcher = lambda value: (None, "needs_review")
logger = logging.getLogger(__name__)


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

    handle = tempfile.NamedTemporaryFile(prefix="totish-screenshot-", suffix=suffix, delete=False)
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


def _exact_team_spans(normalized):
    tokens = normalized.split()
    candidates = []
    for team, aliases in team_aliases.items():
        for alias in aliases:
            alias_tokens = normalize_team_text(alias).split()
            length = len(alias_tokens)
            for start in range(len(tokens) - length + 1):
                if tokens[start:start + length] == alias_tokens:
                    candidates.append((team, start, start + length, length))
    return tokens, candidates


def _candidate_team(line):
    text = re.sub(r"(?:^|\s)\d+(?:[.,]\d+)?(?:\s|$)", " ", line)
    text = re.sub(r"[^A-Za-zА-Яа-яЁё\-\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    normalized = normalize_team_text(text)
    if not text or len(text) < 3 or normalized in IGNORED_TEXT:
        return None
    canonical, status = team_matcher(text)
    if canonical:
        return {"raw": text, "canonical": canonical, "status": status}

    tokens, candidates = _exact_team_spans(normalized)

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
                    token not in alias_tokens
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


def _match_from_parts(home, away, match_date, display_date, kickoff, extra_reasons=()):
    reasons = list(extra_reasons)
    if not home or not home["canonical"]:
        reasons.append(
            f"Домашняя команда «{home['raw']}» не распознана" if home
            else "Домашняя команда не распознана"
        )
    if not away or not away["canonical"]:
        reasons.append(
            f"Гостевая команда «{away['raw']}» не распознана" if away
            else "Гостевая команда не распознана"
        )
    if not match_date:
        reasons.append("Требуется указать полный год даты")
    if not kickoff:
        reasons.append("Время не распознано")
    elif kickoff <= "11:00":
        reasons.append(
            "Матч в 11:00 МСК или раньше требует ручного дедлайна; "
            "добавьте его через ручную форму"
        )
    if home and away and home["canonical"] and home["canonical"] == away["canonical"]:
        reasons.append("Команды должны отличаться")
    return {
        "raw_home_team": home["raw"] if home else "",
        "raw_away_team": away["raw"] if away else "",
        "home_team": (home["canonical"] or home["raw"]) if home else "",
        "away_team": (away["canonical"] or away["raw"]) if away else "",
        "date": match_date, "display_date": display_date, "time": kickoff,
        "status": "needs_review" if reasons else "ready", "reasons": reasons,
    }


def _flat_parse_ocr(result, tournament):
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
        matches.append(_match_from_parts(home, away, match_date, display_date, kickoff))
    return matches


def _full_date(text):
    match = re.fullmatch(r"\s*(\d{1,2})[.](\d{1,2})[.]?\s*", text)
    if not match:
        return None
    day, month = map(int, match.groups())
    try:
        date(2024, month, day)
    except ValueError:
        return None
    return day, month


def _looks_like_date(text):
    return bool(re.fullmatch(r"\s*\d{1,2}[.]\d{1,2}[.]?\s*", text))


def _spatial_parse_ocr(result, tournament):
    words = tuple(getattr(result, "words", ()) or ())
    time_candidates = []
    for word in words:
        value = _valid_time(word.text)
        if value and word.text.strip() == value:
            time_candidates.append((word, value))
    if not time_candidates:
        return None, {"time_anchors": 0, "match_regions": 0, "complete_matches": 0, "review_regions": 0}

    # Schedule times form a vertical column. This removes unrelated status-bar
    # clocks without relying on a resolution-specific X coordinate.
    x_tolerance = max(
        (sum(word.height for word, _ in time_candidates) / len(time_candidates)) * 2,
        (getattr(result, "image_width", 0) or 0) * 0.03,
    )
    x_groups = []
    for candidate in sorted(time_candidates, key=lambda item: item[0].left):
        center_x = candidate[0].left + candidate[0].width / 2
        target = next(
            (group for group in x_groups if abs(center_x - group["mean_x"]) <= x_tolerance),
            None,
        )
        if target is None:
            target = {"mean_x": center_x, "items": []}
            x_groups.append(target)
        target["items"].append(candidate)
        target["mean_x"] = sum(
            word.left + word.width / 2 for word, _ in target["items"]
        ) / len(target["items"])
    largest = max(len(group["items"]) for group in x_groups)
    dominant = [group for group in x_groups if len(group["items"]) == largest]
    if len(dominant) != 1:
        return None, {"time_anchors": 0, "match_regions": 0, "complete_matches": 0, "review_regions": 0}
    anchors = dominant[0]["items"]
    anchors.sort(key=lambda item: (item[0].center_y, item[0].left))

    centers = [word.center_y for word, _ in anchors]
    regions = []
    for index, (anchor, kickoff) in enumerate(anchors):
        if index:
            top = (centers[index - 1] + centers[index]) / 2
        elif len(centers) > 1:
            top = centers[0] - (centers[1] - centers[0]) / 2
        else:
            top = 0
        if index + 1 < len(centers):
            bottom = (centers[index] + centers[index + 1]) / 2
        elif len(centers) > 1:
            bottom = centers[-1] + (centers[-1] - centers[-2]) / 2
        else:
            bottom = result.image_height or float("inf")
        regions.append((top, bottom, anchor, kickoff))

    parsed_regions = []
    for top, bottom, anchor, kickoff in regions:
        region_words = [word for word in words if top <= word.center_y < bottom]
        date_candidates = []
        invalid_date_present = False
        for word in region_words:
            parsed_date = _full_date(word.text)
            if parsed_date and word.left < anchor.left:
                date_candidates.append((word, parsed_date))
            elif _looks_like_date(word.text) and word.left < anchor.left:
                invalid_date_present = True

        grouped = {}
        date_right = max(
            (word.left + word.width for word, _ in date_candidates),
            default=-1,
        )
        for word in region_words:
            if word is anchor or _full_date(word.text) or _valid_time(word.text):
                continue
            if word.left <= date_right or word.left + word.width > anchor.left:
                continue
            grouped.setdefault(word.line_key, []).append(word)

        team_rows = []
        ambiguous_row = False
        for row_words in grouped.values():
            row_words.sort(key=lambda word: word.left)
            text = " ".join(word.text for word in row_words)
            normalized = normalize_team_text(text)
            _, spans = _exact_team_spans(normalized)
            longest = max((length for _, _, _, length in spans), default=0)
            row_teams = {team for team, _, _, length in spans if length == longest}
            if len(row_teams) > 1:
                ambiguous_row = True
                continue
            candidate = _candidate_team(text)
            if candidate:
                top_y = min(word.top for word in row_words)
                team_rows.append((top_y, candidate, frozenset(row_words)))

        team_rows.sort(key=lambda item: item[0])
        unique_rows = []
        used_words = set()
        for top_y, candidate, row_words in team_rows:
            if row_words & used_words:
                continue
            used_words.update(row_words)
            unique_rows.append((top_y, candidate))

        reasons = []
        if invalid_date_present:
            reasons.append("В блоке распознана некорректная дата")
        if ambiguous_row:
            reasons.append("В одной строке обнаружено несколько команд")
        if len(unique_rows) > 2:
            reasons.append("В блоке обнаружено больше двух команд")
        home = unique_rows[0][1] if unique_rows else None
        away = unique_rows[1][1] if len(unique_rows) > 1 else None

        distinct_dates = {candidate for _, candidate in date_candidates}
        local_date = next(iter(distinct_dates)) if len(distinct_dates) == 1 else None
        if len(distinct_dates) > 1:
            reasons.append("В блоке обнаружено несколько дат")
        parsed_regions.append({
            "home": home, "away": away, "kickoff": kickoff,
            "date_parts": local_date, "date_token_present": bool(date_candidates) or invalid_date_present,
            "reasons": reasons,
        })

    # Date inheritance is allowed only for an interior region whose immediate
    # neighbours agree. Edge regions and conflicting neighbours remain review.
    for index, region in enumerate(parsed_regions):
        if (
            region["date_parts"] is None and not region["date_token_present"]
            and 0 < index < len(parsed_regions) - 1
        ):
            previous = parsed_regions[index - 1]["date_parts"]
            following = parsed_regions[index + 1]["date_parts"]
            if previous and previous == following:
                region["date_parts"] = previous

    matches = []
    complete = 0
    for region in parsed_regions:
        parts = region["date_parts"]
        display_date = f"{parts[0]:02d}.{parts[1]:02d}" if parts else ""
        match_date = resolve_match_date(*parts, tournament) if parts else ""
        match = _match_from_parts(
            region["home"], region["away"], match_date, display_date,
            region["kickoff"], region["reasons"],
        )
        if match["status"] == "ready":
            complete += 1
        matches.append(match)
    diagnostics = {
        "time_anchors": len(anchors), "match_regions": len(regions),
        "complete_matches": complete, "review_regions": len(matches) - complete,
    }
    return matches, diagnostics


from dataclasses import dataclass

@dataclass(frozen=True)
class ImportConfig:
    importer_key: str
    league: str
    match_category: str
    team_aliases: dict
    team_matcher: object
    alias_tokens: set
    logger_name: str = "screenshot_match_import"


def parse_ocr(result, tournament, config, diagnostics=None):
    """Parse OCR using the shared spatial/flat engine and a tournament catalog."""
    # The parser functions above resolve catalog through module globals; bind the
    # catalog only for this call and restore it immediately. Routes are synchronous
    # and this keeps the compatibility surface small while adapters evolve.
    global team_aliases, alias_tokens, team_matcher
    previous = (team_aliases, alias_tokens, team_matcher)
    team_aliases, alias_tokens, team_matcher = config.team_aliases, config.alias_tokens, config.team_matcher
    try:
        matches = parse_ocr_result(result, tournament, diagnostics)
    finally:
        team_aliases, alias_tokens, team_matcher = previous
    return matches


def parse_ocr_result(result, tournament, diagnostics=None):
    spatial_matches, details = _spatial_parse_ocr(result, tournament)
    if spatial_matches is not None:
        mode, matches = "spatial", spatial_matches
    else:
        mode, matches = "flat", _flat_parse_ocr(result, tournament)
        details.update({"match_regions": 0, "complete_matches": sum(m["status"] == "ready" for m in matches), "review_regions": sum(m["status"] != "ready" for m in matches)})
    details["parser_mode"] = mode
    if diagnostics is not None: diagnostics.update(details)
    return matches


def make_generic_draft(user_id, tournament_id, matches, importer_key, league, raw_text=""):
    return {"id": uuid.uuid4().hex, "user_id": int(user_id), "tournament_id": int(tournament_id), "created_at": int(time.time()), "importer_key": importer_key, "league": league, "matches": matches}


def generic_draft_is_valid(draft, user_id, tournament_id, importer_key, league):
    return bool(isinstance(draft, dict) and draft.get("user_id") == int(user_id) and draft.get("tournament_id") == int(tournament_id) and draft.get("importer_key") == importer_key and draft.get("league") == league and time.time() - draft.get("created_at", 0) <= DRAFT_TTL_SECONDS)
