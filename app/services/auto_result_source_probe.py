"""Read-only prototype parsers for future automatic result ingestion.

This module deliberately has no database access and no match-finalization logic.
It only turns public source HTML into normalized match observations so source
feasibility can be tested without any production mutation path.
"""

from dataclasses import dataclass
from datetime import datetime
from html import unescape
from html.parser import HTMLParser
import re
from zoneinfo import ZoneInfo

from app.services.rpl_team_catalog import normalize_team_text


STATUS_FINISHED = "finished"
STATUS_NOT_FOUND = "not_found"
STATUS_SOURCE_ERROR = "source_error"


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

    Field meaning is intentionally limited to values verified by the prototype:
    AD = kickoff epoch, AB = match state, AE/AF = home/away, AG/AH = final score.
    Only AB=3 is accepted as finished; all other states are ignored.
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


class _RfsCupParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, set[str]]] = []
        self.current = None
        self.matches = []

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        classes = set(attrs_dict.get("class", "").split())
        self.stack.append((tag, classes))
        if tag == "div" and "tour-match" in classes:
            self.current = {
                "home": [], "away": [], "score": [], "penalty": [], "href": None
            }
        if self.current is not None and tag == "a" and "tour-match__score-block" in classes:
            self.current["href"] = attrs_dict.get("href")

    def handle_endtag(self, tag):
        if not self.stack:
            return
        ended_tag, ended_classes = self.stack.pop()
        if ended_tag == "div" and "tour-match" in ended_classes and self.current is not None:
            self.matches.append(self.current)
            self.current = None

    def handle_data(self, data):
        if self.current is None:
            return
        text = data.strip()
        if not text:
            return
        ancestors = [classes for _, classes in self.stack]
        if any("tour-match__penalty" in classes for classes in ancestors):
            self.current["penalty"].append(text)
            return
        if any("tour-match__score" in classes for classes in ancestors):
            self.current["score"].append(text)
            return
        if not any("tour-match__name" in classes for classes in ancestors):
            return
        if any("first" in classes and "tour-match__team" in classes for classes in ancestors):
            self.current["home"].append(text)
        elif any("last" in classes and "tour-match__team" in classes for classes in ancestors):
            self.current["away"].append(text)


def parse_rfs_cup_listing(html: str) -> list[dict]:
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
                # Penalty is exposed only to prove it can be discarded explicitly.
                "penalty": " ".join(item["penalty"]).strip() or None,
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


def parse_rfs_finished_detail(html: str, *, source: str = "rfs") -> SourceObservation:
    """Parse an RFS match detail after a candidate match URL has been identified."""
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
    match_date = datetime(int(year), _RU_MONTHS[month_name.casefold()], int(day)).date().isoformat()

    # RFS detail pages render the regulation-time score separately. This parser
    # intentionally takes the first conventional score and never consumes a
    # later penalty shootout value.
    score_match = re.search(r"(?<!\d)(\d{1,2})\s*:\s*(\d{1,2})(?!\d)", text)
    if not score_match:
        raise SourceParseError("RFS finished match score not found")
    return SourceObservation(
        source=source,
        match_date=match_date,
        home_score=int(score_match.group(1)),
        away_score=int(score_match.group(2)),
        status=STATUS_FINISHED,
    )


def find_exact_match(
    observations: list[SourceObservation], *, home_team: str, away_team: str, match_date: str
) -> SourceObservation | None:
    """Strict date + ordered home/away lookup; no fuzzy matching."""
    home_key = normalize_team_text(home_team)
    away_key = normalize_team_text(away_team)
    for item in observations:
        if item.match_date != match_date:
            continue
        if normalize_team_text(item.home_team) != home_key:
            continue
        if normalize_team_text(item.away_team) != away_key:
            continue
        return item
    return None
