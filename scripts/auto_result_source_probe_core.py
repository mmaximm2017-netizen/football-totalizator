"""Pure parsers for the read-only automatic-result source prototype.

No Flask import, database access, environment variables, or mutation logic.
"""

from dataclasses import dataclass
from datetime import datetime
from html import unescape
from html.parser import HTMLParser
import re
from zoneinfo import ZoneInfo


STATUS_FINISHED = "finished"
STATUS_NOT_FOUND = "not_found"


@dataclass(frozen=True)
class SourceObservation:
    source: str
    home_team: str | None = None
    away_team: str | None = None
    match_date: str | None = None
    home_score: int | None = None
    away_score: int | None = None
    status: str = STATUS_NOT_FOUND
    detail: str | None = None


class SourceParseError(ValueError):
    """Raised when a source page no longer has the expected safe structure."""


def normalize_team_text(value):
    """Mirror TOTISH exact-team normalization without importing Flask's app package."""
    value = str(value or "").strip().casefold().replace("ё", "е")
    value = re.sub(r"[‐‑‒–—−-]+", " ", value)
    value = re.sub(r"\s+", " ", value)
    value = re.sub(r"^(?:фк|пфк)\s+", "", value)
    return value.strip(" .,:;|[]()")


def _fields(record: str) -> dict[str, str]:
    result = {}
    for item in record.split("¬"):
        if "÷" not in item:
            continue
        key, value = item.split("÷", 1)
        result[key.lstrip("~")] = value
    return result


def parse_flashscore_feed(html: str) -> list[SourceObservation]:
    """Parse the server-rendered Flashscore feed embedded in tournament HTML.

    Fields used here were verified against real finished RPL matches during the
    prototype: AD kickoff epoch, AB state, AE/AF home/away, AG/AH score.
    Only AB=3 is accepted as finished. Unknown states fail closed by omission.
    """
    if "¬" not in html or "÷" not in html or "~AA÷" not in html:
        raise SourceParseError("flashscore embedded match feed not found")

    observations = []
    for raw_record in html.split("~AA÷")[1:]:
        record = _fields("AA÷" + raw_record)
        if record.get("AB") != "3":
            continue
        home = record.get("AE")
        away = record.get("AF")
        home_score = record.get("AG")
        away_score = record.get("AH")
        timestamp = record.get("AD")
        if not all((home, away, home_score, away_score, timestamp)):
            continue
        if not home_score.isdigit() or not away_score.isdigit() or not timestamp.isdigit():
            continue
        match_date = datetime.fromtimestamp(
            int(timestamp), tz=ZoneInfo("UTC")
        ).astimezone(ZoneInfo("Europe/Moscow")).date().isoformat()
        observations.append(
            SourceObservation(
                source="flashscore",
                home_team=unescape(home).strip(),
                away_team=unescape(away).strip(),
                match_date=match_date,
                home_score=int(home_score),
                away_score=int(away_score),
                status=STATUS_FINISHED,
            )
        )
    return observations


class _AncestorParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, set[str]]] = []

    def handle_starttag(self, tag, attrs):
        self.stack.append((tag, set(dict(attrs).get("class", "").split())))

    def handle_endtag(self, tag):
        if self.stack:
            self.stack.pop()

    def has_ancestor(self, *required):
        wanted = set(required)
        return any(wanted.issubset(classes) for _, classes in self.stack)


class _RfsCupParser(_AncestorParser):
    def __init__(self):
        super().__init__()
        self.current = None
        self.matches = []

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        classes = set(attrs_dict.get("class", "").split())
        super().handle_starttag(tag, attrs)
        if tag == "div" and "tour-match" in classes:
            self.current = {
                "home": [], "away": [], "score": [], "penalty": [], "href": None
            }
        if self.current is not None and tag == "a" and "tour-match__score-block" in classes:
            self.current["href"] = attrs_dict.get("href")

    def handle_endtag(self, tag):
        ended = self.stack[-1] if self.stack else (None, set())
        if ended[0] == "div" and "tour-match" in ended[1] and self.current is not None:
            self.matches.append(self.current)
            self.current = None
        super().handle_endtag(tag)

    def handle_data(self, data):
        if self.current is None:
            return
        text = data.strip()
        if not text:
            return
        if self.has_ancestor("tour-match__penalty"):
            self.current["penalty"].append(text)
            return
        if self.has_ancestor("tour-match__score"):
            self.current["score"].append(text)
            return
        if not self.has_ancestor("tour-match__name"):
            return
        if self.has_ancestor("tour-match__team", "first"):
            self.current["home"].append(text)
        elif self.has_ancestor("tour-match__team", "last"):
            self.current["away"].append(text)


def parse_rfs_cup_listing(html: str) -> list[dict]:
    """Return cup candidates; penalty is exposed only so callers can discard it."""
    parser = _RfsCupParser()
    parser.feed(html)
    if not parser.matches:
        raise SourceParseError("RFS cup match cards not found")
    result = []
    for item in parser.matches:
        home = " ".join(item["home"]).strip()
        away = " ".join(item["away"]).strip()
        score_text = " ".join(item["score"])
        score = re.search(r"(\d+)\s*:\s*(\d+)", score_text)
        if not home or not away or not item["href"]:
            continue
        result.append(
            {
                "home_team": home,
                "away_team": away,
                "match_href": item["href"],
                "home_score": int(score.group(1)) if score else None,
                "away_score": int(score.group(2)) if score else None,
                "penalty": " ".join(item["penalty"]).strip() or None,
            }
        )
    return result


class _RfsNationalParser(_AncestorParser):
    def __init__(self):
        super().__init__()
        self.current = None
        self.matches = []

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        classes = set(attrs_dict.get("class", "").split())
        super().handle_starttag(tag, attrs)
        if tag == "div" and "calendar__row" in classes:
            self.current = {
                "home": [], "away": [], "date_time": [], "score": [], "href": None
            }
        if self.current is not None:
            onclick = attrs_dict.get("onclick", "")
            match = re.search(r"['\"](/match/\d+)['\"]", onclick)
            if match:
                self.current["href"] = match.group(1)
            href = attrs_dict.get("href", "")
            if re.fullmatch(r"/match/\d+", href):
                self.current["href"] = href

    def handle_endtag(self, tag):
        ended = self.stack[-1] if self.stack else (None, set())
        if ended[0] == "div" and "calendar__row" in ended[1] and self.current is not None:
            self.matches.append(self.current)
            self.current = None
        super().handle_endtag(tag)

    def handle_data(self, data):
        if self.current is None:
            return
        text = data.strip()
        if not text:
            return
        if self.has_ancestor("calendar__row-team", "team1") and self.has_ancestor("title"):
            self.current["home"].append(text)
        elif self.has_ancestor("calendar__row-team", "team2") and self.has_ancestor("title"):
            self.current["away"].append(text)
        elif self.has_ancestor("calendar__row-first"):
            self.current["date_time"].append(text)
        elif self.has_ancestor("score-item"):
            self.current["score"].append(text)


def parse_rfs_national_listing(html: str) -> list[dict]:
    parser = _RfsNationalParser()
    parser.feed(html)
    if not parser.matches:
        raise SourceParseError("RFS national-team calendar rows not found")
    result = []
    for item in parser.matches:
        home = " ".join(item["home"]).strip()
        away = " ".join(item["away"]).strip()
        date_text = " ".join(item["date_time"])
        date_match = re.search(r"(\d{2})\.(\d{2})\.(20\d{2})", date_text)
        score_numbers = [int(value) for value in re.findall(r"\b\d{1,2}\b", " ".join(item["score"]))]
        if not home or not away or not item["href"] or not date_match:
            continue
        day, month, year = date_match.groups()
        result.append(
            {
                "home_team": home,
                "away_team": away,
                "match_date": f"{year}-{month}-{day}",
                "match_href": item["href"],
                "home_score": score_numbers[0] if len(score_numbers) >= 2 else None,
                "away_score": score_numbers[1] if len(score_numbers) >= 2 else None,
            }
        )
    return result


def _plain_text(html: str) -> str:
    text = re.sub(r"(?is)<script.*?</script>|<style.*?</style>", " ", html)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", unescape(text)).strip()


_RU_MONTHS = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4,
    "мая": 5, "июня": 6, "июля": 7, "августа": 8,
    "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
}


def parse_rfs_finished_detail(
    html: str,
    *,
    regulation_score: tuple[int, int],
    source: str = "rfs",
) -> SourceObservation:
    """Confirm RFS final status/date while reusing the listing's regulation score."""
    text = _plain_text(html)
    if "Матч окончен" not in text:
        return SourceObservation(source=source, status=STATUS_NOT_FOUND, detail="not finished")
    date_match = re.search(
        r"\b(\d{1,2})\s+(января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)\s+(20\d{2})\b",
        text,
        re.IGNORECASE,
    )
    if not date_match:
        raise SourceParseError("RFS finished match date not found")
    day, month_name, year = date_match.groups()
    match_date = datetime(
        int(year), _RU_MONTHS[month_name.casefold()], int(day)
    ).date().isoformat()
    return SourceObservation(
        source=source,
        match_date=match_date,
        home_score=int(regulation_score[0]),
        away_score=int(regulation_score[1]),
        status=STATUS_FINISHED,
    )


def _candidate_keys(primary: str, aliases: tuple[str, ...]) -> set[str]:
    return {normalize_team_text(value) for value in (primary, *aliases)}


def find_exact_match(
    observations: list[SourceObservation],
    *,
    home_team: str,
    away_team: str,
    match_date: str,
    home_aliases: tuple[str, ...] = (),
    away_aliases: tuple[str, ...] = (),
) -> SourceObservation | None:
    """Strict date + ordered home/away lookup with explicit aliases only."""
    home_keys = _candidate_keys(home_team, home_aliases)
    away_keys = _candidate_keys(away_team, away_aliases)
    for item in observations:
        if item.match_date != match_date:
            continue
        if normalize_team_text(item.home_team) not in home_keys:
            continue
        if normalize_team_text(item.away_team) not in away_keys:
            continue
        return item
    return None
