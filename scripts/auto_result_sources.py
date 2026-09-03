"""Strict public-page adapters for automatic result discovery.

Pure parsing/network module: no Flask, database, scoring, Telegram, or writes.
Every adapter fails closed unless the page exposes an explicit finished marker.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from html import unescape
from html.parser import HTMLParser
import json
import re
from urllib.parse import urljoin

import requests

STATUS_FINISHED = "finished"
STATUS_NOT_FOUND = "not_found"
STATUS_NOT_FINISHED = "not_finished"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; TOTISH-AutoResults/1.0)",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.7",
}
TIMEOUT = (5, 15)

RPL_ALIASES = {
    "Спартак": ("Спартак", "Спартак Москва"),
    "Динамо": ("Динамо", "Динамо М", "Динамо Москва"),
    "ЦСКА": ("ЦСКА", "ЦСКА Москва"),
    "Зенит": ("Зенит", "Зенит Санкт-Петербург"),
    "Локомотив": ("Локомотив", "Локомотив Москва"),
    "Краснодар": ("Краснодар",),
    "Ахмат": ("Ахмат", "Ахмат Грозный"),
    "Ростов": ("Ростов",),
    "Рубин": ("Рубин", "Рубин Казань"),
    "Крылья Советов": ("Крылья Советов", "Крылья Советов Самара"),
    "Пари НН": ("Пари НН", "Пари Нижний Новгород", "Нижний Новгород"),
    "Оренбург": ("Оренбург",),
    "Балтика": ("Балтика", "Балтика Калининград"),
    "Сочи": ("Сочи",),
    "Динамо Мх": ("Динамо Мх", "Динамо Махачкала"),
    "Акрон": ("Акрон", "Акрон Тольятти"),
    "Родина": ("Родина", "Родина Москва"),
    "Факел": ("Факел", "Факел Воронеж"),
    "Россия": ("Россия",),
    "Иран": ("Иран",),
}

_RU_MONTHS = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4,
    "мая": 5, "июня": 6, "июля": 7, "августа": 8,
    "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
}


@dataclass(frozen=True)
class Observation:
    source: str
    status: str
    home_team: str | None = None
    away_team: str | None = None
    match_date: str | None = None
    home_score: int | None = None
    away_score: int | None = None
    detail: str | None = None


class SourceError(RuntimeError):
    pass


def normalize_team(value: str | None) -> str:
    value = str(value or "").strip().casefold().replace("ё", "е")
    value = re.sub(r"[‐‑‒–—−-]+", " ", value)
    value = re.sub(r"\s+", " ", value)
    value = re.sub(r"^(?:фк|пфк)\s+", "", value)
    return value.strip(" .,:;|[]()")


def aliases_for(team: str) -> tuple[str, ...]:
    return RPL_ALIASES.get(team, (team,))


def team_matches(source_name: str | None, canonical: str) -> bool:
    key = normalize_team(source_name)
    return key in {normalize_team(x) for x in aliases_for(canonical)}


def _plain_text(html: str) -> str:
    text = re.sub(r"(?is)<script.*?</script>|<style.*?</style>", " ", html)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", unescape(text)).strip()


def fetch_text(url: str, *, session=requests) -> str:
    try:
        response = session.get(url, headers=HEADERS, timeout=TIMEOUT, allow_redirects=True)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise SourceError(f"request_failed:{type(exc).__name__}") from exc
    return response.text


def find_livesport_result(html: str, *, home: str, away: str, match_date: str, source="livesport") -> Observation:
    """Match strict date/order and require LiveSport's explicit 'Ок' final marker."""
    text = _plain_text(html)
    target = date.fromisoformat(match_date)
    month_name = next((name for name, num in _RU_MONTHS.items() if num == target.month), None)
    if month_name is None:
        raise SourceError("invalid_target_month")
    date_pattern = re.compile(rf"\b{target.day}\s+{month_name}\b[^,]*,?\s*{target.year}\b", re.I)
    hit = date_pattern.search(text)
    if not hit:
        return Observation(source, STATUS_NOT_FOUND, detail="date_not_found")
    next_date = re.search(r"\b\d{1,2}\s+(?:" + "|".join(_RU_MONTHS) + rf")\b[^,]*,?\s*{target.year}\b", text[hit.end():], re.I)
    segment_end = hit.end() + next_date.start() if next_date else len(text)
    segment = text[hit.end():segment_end]

    for home_alias in aliases_for(home):
        for away_alias in aliases_for(away):
            pattern = re.compile(
                r"\bОк(?:\s+(?:пен|д\.в\.))?\s+"
                + re.escape(home_alias)
                + r"\s+(\d{1,2})\s*:\s*(\d{1,2})(?:\s+\d{1,2}\s*:\s*\d{1,2})?\s+"
                + re.escape(away_alias)
                + r"(?:\b|\s)",
                re.I,
            )
            match = pattern.search(segment)
            if match:
                return Observation(source, STATUS_FINISHED, home_alias, away_alias, match_date, int(match.group(1)), int(match.group(2)))

    # Exact teams may exist but the final marker may not yet be present.
    if any(normalize_team(x) in normalize_team(segment) for x in aliases_for(home)) and any(
        normalize_team(x) in normalize_team(segment) for x in aliases_for(away)
    ):
        return Observation(source, STATUS_NOT_FINISHED, detail="match_present_without_final_marker")
    return Observation(source, STATUS_NOT_FOUND, detail="match_not_found")


def find_sports_rpl_result(html: str, *, home: str, away: str, match_date: str) -> Observation:
    cards = re.findall(r"(?is)<article\s+class=\"calendar-card\".*?</article>", html)
    if not cards:
        raise SourceError("sports_calendar_cards_missing")
    for card in cards:
        title_match = re.search(r'title="Матч\s+([^\"]+?)\s+-\s+([^\"]+?)"', card, re.I)
        dt_match = re.search(r'<time[^>]+datetime="(\d{4}-\d{2}-\d{2})T', card, re.I)
        if not title_match or not dt_match or dt_match.group(1) != match_date:
            continue
        source_home, source_away = map(unescape, title_match.groups())
        if not team_matches(source_home, home) or not team_matches(source_away, away):
            continue
        if "Матч окончен" not in unescape(card):
            return Observation("sports", STATUS_NOT_FINISHED, source_home, source_away, match_date)
        scores = re.findall(r'class="calendar-score__score"[^>]*>\s*(\d{1,2})\s*<', card, re.I)
        if len(scores) < 2:
            raise SourceError("sports_finished_score_missing")
        return Observation("sports", STATUS_FINISHED, source_home, source_away, match_date, int(scores[0]), int(scores[1]))
    return Observation("sports", STATUS_NOT_FOUND, detail="match_not_found")


class _AncestorParser(HTMLParser):
    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, set[str]]] = []
    def handle_starttag(self, tag, attrs):
        if tag not in self.VOID:
            self.stack.append((tag, set(dict(attrs).get("class", "").split())))
    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                return
    def has(self, *classes):
        wanted = set(classes)
        return any(wanted.issubset(found) for _, found in self.stack)


class _RfsCupParser(_AncestorParser):
    def __init__(self):
        super().__init__(); self.current = None; self.matches = []
    def handle_starttag(self, tag, attrs):
        attrs_d = dict(attrs); classes = set(attrs_d.get("class", "").split()); super().handle_starttag(tag, attrs)
        if tag == "div" and "tour-match" in classes:
            self.current = {"home": [], "away": [], "score": [], "href": None}
        if self.current is not None and tag == "a" and "tour-match__score-block" in classes:
            self.current["href"] = attrs_d.get("href")
    def handle_endtag(self, tag):
        ended = self.stack[-1] if self.stack else (None, set())
        if ended[0] == "div" and "tour-match" in ended[1] and self.current is not None:
            self.matches.append(self.current); self.current = None
        super().handle_endtag(tag)
    def handle_data(self, data):
        if self.current is None or not data.strip(): return
        text = data.strip()
        if self.has("tour-match__score") and not self.has("tour-match__penalty"): self.current["score"].append(text)
        elif self.has("tour-match__name") and self.has("tour-match__team", "first"): self.current["home"].append(text)
        elif self.has("tour-match__name") and self.has("tour-match__team", "last"): self.current["away"].append(text)


def rfs_cup_candidates(html: str) -> list[dict]:
    parser = _RfsCupParser(); parser.feed(html)
    if not parser.matches: raise SourceError("rfs_cup_cards_missing")
    out = []
    for item in parser.matches:
        home, away = " ".join(item["home"]).strip(), " ".join(item["away"]).strip()
        score = re.search(r"(\d+)\s*:\s*(\d+)", " ".join(item["score"]))
        if home and away and item["href"]:
            out.append({"home": home, "away": away, "href": item["href"], "score": (int(score.group(1)), int(score.group(2))) if score else None})
    if not out: raise SourceError("rfs_cup_fields_missing")
    return out


class _RfsNationalParser(_AncestorParser):
    def __init__(self):
        super().__init__(); self.current = None; self.matches = []
    def handle_starttag(self, tag, attrs):
        attrs_d = dict(attrs); classes = set(attrs_d.get("class", "").split()); super().handle_starttag(tag, attrs)
        if tag == "div" and "calendar__row" in classes:
            self.current = {"home": [], "away": [], "date": [], "score": [], "href": None}
        if self.current is not None:
            for value in (attrs_d.get("href", ""), attrs_d.get("onclick", "")):
                match = re.search(r"(/match/\d+)", value)
                if match: self.current["href"] = match.group(1)
    def handle_endtag(self, tag):
        ended = self.stack[-1] if self.stack else (None, set())
        if ended[0] == "div" and "calendar__row" in ended[1] and self.current is not None:
            self.matches.append(self.current); self.current = None
        super().handle_endtag(tag)
    def handle_data(self, data):
        if self.current is None or not data.strip(): return
        text = data.strip()
        if self.has("calendar__row-team", "team1") and self.has("title"): self.current["home"].append(text)
        elif self.has("calendar__row-team", "team2") and self.has("title"): self.current["away"].append(text)
        elif self.has("calendar__row-first"): self.current["date"].append(text)
        elif self.has("score-item"): self.current["score"].append(text)


def rfs_national_candidates(html: str) -> list[dict]:
    parser = _RfsNationalParser(); parser.feed(html)
    if not parser.matches: raise SourceError("rfs_national_rows_missing")
    out = []
    for item in parser.matches:
        home, away = " ".join(item["home"]).strip(), " ".join(item["away"]).strip()
        dm = re.search(r"(\d{2})\.(\d{2})\.(20\d{2})", " ".join(item["date"]))
        nums = [int(x) for x in re.findall(r"\b\d{1,2}\b", " ".join(item["score"]))]
        if home and away and dm and item["href"]:
            d, m, y = dm.groups()
            out.append({"home": home, "away": away, "date": f"{y}-{m}-{d}", "href": item["href"], "score": (nums[0], nums[1]) if len(nums) >= 2 else None})
    if not out: raise SourceError("rfs_national_fields_missing")
    return out


def parse_rfs_finished_detail(html: str, *, score: tuple[int, int], source="rfs") -> Observation:
    text = _plain_text(html)
    if "Матч окончен" not in text:
        return Observation(source, STATUS_NOT_FINISHED)
    dm = re.search(r"\b(\d{1,2})\s+(" + "|".join(_RU_MONTHS) + r")\s+(20\d{2})\b", text, re.I)
    if not dm: raise SourceError("rfs_finished_date_missing")
    day, month_name, year = dm.groups()
    match_date = date(int(year), _RU_MONTHS[month_name.casefold()], int(day)).isoformat()
    return Observation(source, STATUS_FINISHED, match_date=match_date, home_score=score[0], away_score=score[1])


def find_rfs_candidate(candidates: list[dict], *, home: str, away: str, match_date: str | None = None) -> dict | None:
    for item in candidates:
        if match_date is not None and item.get("date") != match_date: continue
        if team_matches(item.get("home"), home) and team_matches(item.get("away"), away): return item
    return None


class _SportboxCalendarParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True); self.rows = []
    def handle_starttag(self, tag, attrs):
        attrs_d = dict(attrs); classes = set(attrs_d.get("class", "").split())
        if tag == "a" and "game-row" in classes:
            self.rows.append({"href": attrs_d.get("href", ""), "title": attrs_d.get("title", "")})


def sportbox_national_candidates(html: str) -> list[dict]:
    parser = _SportboxCalendarParser(); parser.feed(html)
    if not parser.rows: raise SourceError("sportbox_game_rows_missing")
    out = []
    for row in parser.rows:
        title = unescape(row["title"])
        tm = re.match(r"(.+?)\s+-\s+(.+?)(?:\s+\(\d+\s*:\s*\d+\))?\s+(\d{1,2})\s+(" + "|".join(_RU_MONTHS) + r")\.", title, re.I)
        idm = re.search(r"/game_(\d+)", row["href"])
        if tm and idm:
            home, away, day, month_name = tm.groups()
            out.append({"home": home.strip(), "away": away.strip(), "day": int(day), "month": _RU_MONTHS[month_name.casefold()], "href": row["href"], "game_id": idm.group(1)})
    if not out: raise SourceError("sportbox_calendar_fields_missing")
    return out


def find_sportbox_candidate(candidates: list[dict], *, home: str, away: str, match_date: str) -> dict | None:
    target = date.fromisoformat(match_date)
    for item in candidates:
        if item["day"] != target.day or item["month"] != target.month: continue
        if team_matches(item["home"], home) and team_matches(item["away"], away): return item
    return None


def parse_sportbox_game_json(raw: str, *, match_date: str) -> Observation:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SourceError("sportbox_invalid_json") from exc
    timeline = str(data.get("football_timeline_html") or "")
    score = str(data.get("score") or "")
    sm = re.fullmatch(r"\s*(\d{1,2})\s*:\s*(\d{1,2})\s*", score)
    if "b-timeline-end" not in timeline:
        return Observation("sportbox", STATUS_NOT_FINISHED, match_date=match_date)
    if not sm: raise SourceError("sportbox_finished_score_missing")
    return Observation("sportbox", STATUS_FINISHED, match_date=match_date, home_score=int(sm.group(1)), away_score=int(sm.group(2)))


def absolute_url(base: str, href: str) -> str:
    return urljoin(base, href)
