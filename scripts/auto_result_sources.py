"""Strict public-page adapters for TOTISH automatic result discovery.

Pure parsing/network module: no Flask, DB, scoring, Telegram, or writes.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from html import unescape
from html.parser import HTMLParser
import json
import re
from urllib.parse import urljoin, urlsplit
import requests

STATUS_FINISHED = "finished"
STATUS_NOT_FOUND = "not_found"
STATUS_NOT_FINISHED = "not_finished"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; TOTISH-AutoResults/1.0)", "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.7"}
TIMEOUT = (5, 15)

ALIASES = {
    "Спартак": ("Спартак", "Спартак Москва"), "Динамо": ("Динамо", "Динамо М", "Динамо Москва"),
    "ЦСКА": ("ЦСКА", "ЦСКА Москва"), "Зенит": ("Зенит", "Зенит Санкт-Петербург"),
    "Локомотив": ("Локомотив", "Локомотив Москва"), "Краснодар": ("Краснодар",),
    "Ахмат": ("Ахмат", "Ахмат Грозный"), "Ростов": ("Ростов",), "Рубин": ("Рубин", "Рубин Казань"),
    "Крылья Советов": ("Крылья Советов", "Крылья Советов Самара"),
    "Пари НН": ("Пари НН", "Пари Нижний Новгород", "Нижний Новгород"), "Оренбург": ("Оренбург",),
    "Балтика": ("Балтика", "Балтика Калининград"), "Сочи": ("Сочи",),
    "Динамо Мх": ("Динамо Мх", "Динамо Махачкала"), "Акрон": ("Акрон", "Акрон Тольятти"),
    "Родина": ("Родина", "Родина Москва"), "Факел": ("Факел", "Факел Воронеж"),
    "Россия": ("Россия",), "Иран": ("Иран",),
}
MONTHS = {"января":1,"февраля":2,"марта":3,"апреля":4,"мая":5,"июня":6,"июля":7,"августа":8,"сентября":9,"октября":10,"ноября":11,"декабря":12}
MONTH_RE = "|".join(MONTHS)

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

class SourceError(RuntimeError): pass

class SourceUnavailable(SourceError): pass

def normalize_team(value):
    value = str(value or "").strip().casefold().replace("ё", "е")
    value = re.sub(r"[‐‑‒–—−-]+", " ", value)
    value = re.sub(r"\s+", " ", value)
    value = re.sub(r"^(?:фк|пфк)\s+", "", value)
    return value.strip(" .,:;|[]()")

def aliases_for(team): return ALIASES.get(team, (team,))
def team_matches(source_name, canonical): return normalize_team(source_name) in {normalize_team(x) for x in aliases_for(canonical)}

def plain_text(html):
    html = re.sub(r"(?is)<script.*?</script>|<style.*?</style>", " ", html)
    return re.sub(r"\s+", " ", unescape(re.sub(r"(?s)<[^>]+>", " ", html))).strip()

def fetch_text(url, *, session=requests):
    try:
        r = session.get(url, headers=HEADERS, timeout=TIMEOUT, allow_redirects=True); r.raise_for_status(); return r.text
    except requests.RequestException as exc:
        raise SourceUnavailable(f"request_failed:{type(exc).__name__}") from exc

def _date_pattern(day, month_name, year):
    # LiveSport uses both "28 августа, пятница, 2026" and compact date forms.
    return re.compile(rf"\b{day}\s+{month_name}(?:,\s*[^,]+)?(?:,\s*)?{year}\b", re.I)

def find_livesport_result(html, *, home, away, match_date, source="livesport", regulation_only=False):
    text = plain_text(html); target = date.fromisoformat(match_date)
    calendar_date = re.compile(
        rf"\b\d{{1,2}}\s+(?:{MONTH_RE})(?:,\s*[^,]+)?(?:,\s*)?20\d{{2}}\b",
        re.I,
    )
    if not calendar_date.search(text):
        raise SourceError("livesport_calendar_dates_missing")
    month_name = next(k for k,v in MONTHS.items() if v == target.month)
    hit = _date_pattern(target.day, month_name, target.year).search(text)
    if not hit: return Observation(source, STATUS_NOT_FOUND, detail="date_not_found")
    next_hit = calendar_date.search(text[hit.end():])
    segment = text[hit.end():hit.end()+next_hit.start()] if next_hit else text[hit.end():]
    # Parse complete row fields before comparing names. A word boundary after
    # "Динамо" is not a team boundary ("Динамо Мх" is a different club).
    observations = []
    starts = []
    for marker in re.finditer(r"(?<!\S)(Ок\b|\d{1,2}:\d{2}(?=\s))", segment, re.I):
        if starts and marker[1].casefold() != "ок":
            prefix = segment[starts[-1]:marker.start()].strip()
            # A score such as 10:11 is not a kickoff. It follows the home
            # field, or (for a shootout) the regulation score, not an away field.
            if (re.fullmatch(r"(?:Ок(?:\s+(?:пен|д\.в\.))?|\d{1,2}:\d{2})\s+[^:]+", prefix, re.I)
                    or re.search(r"\d{1,2}\s*:\s*\d{1,2}$", prefix)):
                continue
        starts.append(marker.start())
    for start, end in zip(starts, starts[1:] + [len(segment)]):
        row = segment[start:end]
        row = row.strip()
        # The calendar appends a tour number / cup group after the away team.
        row = re.sub(r"\s+(?:\d{1,2}|[A-DА-Г])$", "", row)
        parsed = re.fullmatch(
            r"(Ок(?:\s+(?:пен|д\.в\.))?|\d{1,2}:\d{2})\s+"
            r"(.+?)\s+(\d{1,2}|[–—-])\s*:\s*(\d{1,2}|[–—-])"
            r"(?:\s+\d{1,2}\s*:\s*\d{1,2})?\s+(.+)", row, re.I,
        )
        if not parsed:
            continue
        marker, sh, hs, aws, sa = parsed.groups()
        if not team_matches(sh, home) or not team_matches(sa, away):
            continue
        if regulation_only and "д.в." in marker.casefold():
            raise SourceError("livesport_90_minute_score_unproven")
        finished = marker.casefold().startswith("ок") and hs.isdigit() and aws.isdigit()
        observations.append(Observation(
            source, STATUS_FINISHED if finished else STATUS_NOT_FINISHED,
            sh, sa, match_date, int(hs) if finished else None, int(aws) if finished else None,
        ))
    if len(observations) > 1:
        raise SourceError("livesport_candidate_ambiguous")
    if observations:
        return observations[0]
    return Observation(source, STATUS_NOT_FOUND, detail="match_not_found")


class _Tags(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.tags = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


def _extra_time_present(html):
    if re.search(
        r"\bд\s*\.\s*в\s*\.|дополнительн\w*\s+(?:врем\w*|тайм\w*)|"
        r"(?:первый|второй)\s+дополнительный|доп\.?\s+врем\w*|"
        r"extra[ -]?time|овертайм|\b(?:105|120)\s*(?:[′’':+]|мин\b)",
        plain_text(html), re.I,
    ):
        return True
    return any(str(attrs.get(key, '')).casefold() in {'true', '1', 'yes'}
               for _, attrs in _Tags(html).tags
               for key in ('overtime', 'data-overtime', 'data-extra-time'))


def find_sports_rpl_result(html, *, home, away, match_date):
    cards = [card for card in re.findall(r"(?is)<article\b[^>]*>.*?</article>", html)
             if "calendar-card" in _Tags(card).tags[0][1].get("class", "").split()]
    if not cards: raise SourceError("sports_calendar_cards_missing")
    openings = [attrs for tag, attrs in _Tags(html).tags
                if tag == "article" and "calendar-card" in attrs.get("class", "").split()]
    if len(openings) != len(cards):
        raise SourceError("sports_calendar_card_incomplete")
    observations = []
    for card in cards:
        tags = _Tags(card).tags
        names = {m.groups() for _, attrs in tags
                 if (m := re.fullmatch(r"Матч\s+(.+?)\s+-\s+(.+)", attrs.get("title", ""), re.I))}
        if not names:
            # Current team anchors are an independent structural representation;
            # attribute order / extra CSS classes do not change their meaning.
            sides = {side: [] for side in ("home", "away")}
            for anchor in re.findall(r"(?is)<a\b[^>]*>.*?</a>", card):
                classes = _Tags(anchor).tags[0][1].get("class", "").split()
                for side in sides:
                    if f"calendar-card__{side}" in classes:
                        sides[side].append(plain_text(anchor))
            if all(len(values) == 1 and values[0] for values in sides.values()):
                names.add((sides["home"][0], sides["away"][0]))
        dates = {attrs["datetime"][:10] for tag, attrs in tags
                 if tag == "time" and re.match(r"\d{4}-\d{2}-\d{2}T", attrs.get("datetime", ""))}
        if len(names) != 1 or len(dates) != 1:
            raise SourceError("sports_calendar_identity_fields_missing_or_ambiguous")
        md = next(iter(dates))
        try:
            date.fromisoformat(md)
        except ValueError as exc:
            raise SourceError("sports_calendar_date_invalid") from exc
        sh, sa = next(iter(names))
        finished = "Матч окончен" in unescape(card)
        scores = [plain_text(span) for span in re.findall(r"(?is)<span\b[^>]*>.*?</span>", card)
                  if "calendar-score__score" in _Tags(span).tags[0][1].get("class", "").split()]
        if finished and (len(scores) != 2 or not all(re.fullmatch(r"\d{1,2}", s) for s in scores)):
            raise SourceError("sports_finished_score_missing_or_ambiguous")
        if md != match_date: continue
        if not team_matches(sh, home) or not team_matches(sa, away): continue
        observations.append(Observation(
            "sports", STATUS_FINISHED if finished else STATUS_NOT_FINISHED, sh, sa, md,
            int(scores[0]) if finished else None, int(scores[1]) if finished else None,
        ))
    # Validate the whole page before reporting health or accepting a candidate.
    if len(observations) > 1:
        raise SourceError("sports_candidate_ambiguous")
    if observations:
        return observations[0]
    return Observation("sports", STATUS_NOT_FOUND, detail="match_not_found")

class AncestorParser(HTMLParser):
    VOID={"area","base","br","col","embed","hr","img","input","link","meta","param","source","track","wbr"}
    def __init__(self): super().__init__(convert_charrefs=True); self.stack=[]
    def handle_starttag(self, tag, attrs):
        if tag not in self.VOID: self.stack.append((tag,set(dict(attrs).get("class","").split())))
    def handle_endtag(self, tag):
        for i in range(len(self.stack)-1,-1,-1):
            if self.stack[i][0]==tag: del self.stack[i:]; return
    def has(self,*classes):
        wanted=set(classes); return any(wanted.issubset(found) for _,found in self.stack)

class RfsCupParser(AncestorParser):
    def __init__(self): super().__init__(); self.current=None; self.matches=[]
    def handle_starttag(self,tag,attrs):
        d=dict(attrs); c=set(d.get("class","").split()); super().handle_starttag(tag,attrs)
        if tag=="div" and "tour-match" in c:
            self.current={"home":[],"away":[],"score":[],"href":None,"home_ids":set(),"away_ids":set(),"markup":[]}
        if self.current is not None:
            self.current["markup"].append(self.get_starttag_text())
        if self.current is not None and tag=="a" and "tour-match__score-block" in c: self.current["href"]=d.get("href")
        if self.current is not None and tag == "a":
            url = urlsplit(d.get("href", ""))
            club = re.fullmatch(r"/cup/teams/(\d+)/?", url.path)
            if club and url.netloc in {"", "www.rfs.ru", "rfs.ru"}:
                for side, css in (("home", "first"), ("away", "last")):
                    if self.has("tour-match__team", css):
                        self.current[f"{side}_ids"].add(club[1])
    def handle_endtag(self,tag):
        ended=self.stack[-1] if self.stack else (None,set())
        if ended[0]=="div" and "tour-match" in ended[1] and self.current is not None: self.matches.append(self.current); self.current=None
        super().handle_endtag(tag)
    def handle_data(self,data):
        if self.current is None or not data.strip(): return
        t=data.strip()
        self.current["markup"].append(t)
        if self.has("tour-match__score") and not self.has("tour-match__penalty"): self.current["score"].append(t)
        elif self.has("tour-match__name") and self.has("tour-match__team","first"): self.current["home"].append(t)
        elif self.has("tour-match__name") and self.has("tour-match__team","last"): self.current["away"].append(t)

def rfs_cup_candidates(html):
    p=RfsCupParser(); p.feed(html)
    if not p.matches: raise SourceError("rfs_cup_cards_missing")
    out=[]
    for x in p.matches:
        h,a=" ".join(x["home"]).strip()," ".join(x["away"]).strip(); s=re.search(r"(\d+)\s*:\s*(\d+)"," ".join(x["score"]))
        if h and a and x["href"]:
            if any(len(x[f"{side}_ids"]) > 1 for side in ("home", "away")):
                raise SourceError("rfs_cup_club_identity_ambiguous")
            out.append({"home":h,"away":a,"href":x["href"],"score":(int(s.group(1)),int(s.group(2))) if s else None,
                        "home_id":next(iter(x["home_ids"]), None), "away_id":next(iter(x["away_ids"]), None),
                        "regulation_unproven":_extra_time_present(" ".join(x["markup"]))
                            or len(re.findall(r"\d+\s*:\s*\d+", " ".join(x["score"]))) > 1})
    if not out: raise SourceError("rfs_cup_fields_missing")
    return out


# Verified public RFS club links, scoped ONLY to the cup adapter:
# https://www.rfs.ru/cup/teams/35   — Динамо, Москва
# https://www.rfs.ru/cup/teams/3637 — Динамо, Махачкала
RFS_CUP_DYNAMO_IDS = {"35": "Динамо", "3637": "Динамо Мх"}


def rfs_cup_team_matches(candidate, side, expected):
    name = candidate[side]
    club_id = candidate.get(f"{side}_id")
    canonical = RFS_CUP_DYNAMO_IDS.get(club_id)
    if canonical:
        if normalize_team(name) != "динамо" and not team_matches(name, canonical):
            raise SourceError("rfs_cup_club_name_id_conflict")
        return canonical == expected
    if normalize_team(name) == "динамо":
        if expected in RFS_CUP_DYNAMO_IDS.values():
            raise SourceError("rfs_cup_dynamo_identity_unproven")
        return False
    return team_matches(name, expected)

class RfsNationalParser(AncestorParser):
    def __init__(self): super().__init__(); self.current=None; self.matches=[]
    def handle_starttag(self,tag,attrs):
        d=dict(attrs); c=set(d.get("class","").split()); super().handle_starttag(tag,attrs)
        if tag=="div" and "calendar__row" in c: self.current={"home":[],"away":[],"date":[],"score":[],"href":None}
        if self.current is not None:
            for value in (d.get("href",""),d.get("onclick","")):
                m=re.search(r"(/match/\d+)",value)
                if m: self.current["href"]=m.group(1)
    def handle_endtag(self,tag):
        ended=self.stack[-1] if self.stack else (None,set())
        if ended[0]=="div" and "calendar__row" in ended[1] and self.current is not None: self.matches.append(self.current); self.current=None
        super().handle_endtag(tag)
    def handle_data(self,data):
        if self.current is None or not data.strip(): return
        t=data.strip()
        if self.has("calendar__row-team","team1") and self.has("title"): self.current["home"].append(t)
        elif self.has("calendar__row-team","team2") and self.has("title"): self.current["away"].append(t)
        elif self.has("calendar__row-first"): self.current["date"].append(t)
        elif self.has("score-item"): self.current["score"].append(t)

def rfs_national_candidates(html):
    p=RfsNationalParser(); p.feed(html)
    if not p.matches: raise SourceError("rfs_national_rows_missing")
    out=[]
    for x in p.matches:
        h,a=" ".join(x["home"]).strip()," ".join(x["away"]).strip(); dm=re.search(r"(\d{2})\.(\d{2})\.(20\d{2})"," ".join(x["date"])); nums=[int(v) for v in re.findall(r"\b\d{1,2}\b"," ".join(x["score"]))]
        if h and a and dm and x["href"]:
            d,m,y=dm.groups(); out.append({"home":h,"away":a,"date":f"{y}-{m}-{d}","href":x["href"],"score":(nums[0],nums[1]) if len(nums)>=2 else None})
    if not out: raise SourceError("rfs_national_fields_missing")
    return out

def parse_rfs_detail(html, *, score=None, source="rfs", regulation_only=False):
    text=plain_text(html); dm=re.search(rf"\b(\d{{1,2}})\s+({MONTH_RE})\s+(20\d{{2}})\b",text,re.I)
    if not dm: raise SourceError("rfs_match_date_missing")
    day,month_name,year=dm.groups(); md=date(int(year),MONTHS[month_name.casefold()],int(day)).isoformat()
    if "Матч окончен" not in text: return Observation(source,STATUS_NOT_FINISHED,match_date=md)
    if regulation_only and _extra_time_present(html):
        # Do not infer 90' from a 120' total or a shootout result. Supporting an
        # explicit regulation breakdown would require a separate verified field.
        raise SourceError("rfs_90_minute_score_unproven")
    if score is None: raise SourceError("rfs_finished_score_missing")
    return Observation(source,STATUS_FINISHED,match_date=md,home_score=score[0],away_score=score[1])

def parse_rfs_finished_detail(html, *, score, source="rfs"): return parse_rfs_detail(html,score=score,source=source)

def find_rfs_candidate(candidates, *, home, away, match_date=None):
    for x in candidates:
        if match_date is not None and x.get("date") != match_date: continue
        if team_matches(x.get("home"),home) and team_matches(x.get("away"),away): return x
    return None

class SportboxCalendarParser(HTMLParser):
    def __init__(self): super().__init__(convert_charrefs=True); self.rows=[]
    def handle_starttag(self,tag,attrs):
        d=dict(attrs)
        if tag=="a" and "game-row" in set(d.get("class","").split()): self.rows.append({"href":d.get("href", ""),"title":d.get("title","")})

def sportbox_national_candidates(html):
    p=SportboxCalendarParser(); p.feed(html)
    if not p.rows: raise SourceError("sportbox_game_rows_missing")
    out=[]
    for row in p.rows:
        title=unescape(row["title"]); tm=re.match(rf"(.+?)\s+-\s+(.+?)(?:\s+\(\d+\s*:\s*\d+\))?\s+(\d{{1,2}})\s+({MONTH_RE})\.",title,re.I); im=re.search(r"/game_(\d+)",row["href"])
        if tm and im:
            h,a,d,m=tm.groups(); out.append({"home":h.strip(),"away":a.strip(),"day":int(d),"month":MONTHS[m.casefold()],"href":row["href"],"game_id":im.group(1)})
    if not out: raise SourceError("sportbox_calendar_fields_missing")
    return out

def find_sportbox_candidate(candidates, *, home, away, match_date):
    target=date.fromisoformat(match_date)
    matches = [
        x for x in candidates
        if x["day"] == target.day
        and x["month"] == target.month
        and team_matches(x["home"], home)
        and team_matches(x["away"], away)
    ]
    if len(matches) > 1:
        raise SourceError("sportbox_candidate_ambiguous")
    return matches[0] if matches else None

def parse_sportbox_game_json(raw, *, match_date):
    try: data=json.loads(raw)
    except json.JSONDecodeError as exc: raise SourceError("sportbox_invalid_json") from exc
    timeline=str(data.get("football_timeline_html") or ""); score=str(data.get("score") or ""); sm=re.fullmatch(r"\s*(\d{1,2})\s*:\s*(\d{1,2})\s*",score)
    if "b-timeline-end" not in timeline: return Observation("sportbox",STATUS_NOT_FINISHED,match_date=match_date)
    if not sm: raise SourceError("sportbox_finished_score_missing")
    return Observation("sportbox",STATUS_FINISHED,match_date=match_date,home_score=int(sm.group(1)),away_score=int(sm.group(2)))

def absolute_url(base,href): return urljoin(base,href)


def sportbox_rpl_candidate(html, *, home, away, match_date):
    """Read the RPL calendar only; detail JSON must independently prove identity."""
    target = date.fromisoformat(match_date).strftime('%d.%m.%Y')
    candidates = []
    rows = 0
    for row in re.findall(r'(?is)<tr\b[^>]*>(.*?)</tr>', html):
        links = re.findall(r'href=["\'](/Vidy_sporta/Futbol/Russia/premier_league/stats/turnir_(\d+)/game_(\d+))["\']', row)
        if not links:
            continue
        rows += 1
        cells = re.findall(r'(?is)<td\b[^>]*>(.*?)</td>', row)
        if len(links) != 1 or len(cells) < 3:
            raise SourceError('sportbox_rpl_calendar_structure')
        teams = re.findall(r'(?is)<span\b[^>]*>(.*?)</span>', cells[1])
        if len(teams) != 2 or not all(plain_text(t) for t in teams):
            raise SourceError('sportbox_rpl_calendar_teams')
        dates = re.findall(r'\b\d{2}\.\d{2}\.\d{4}\b', plain_text(cells[0]))
        # Live rows replace the date with the current minute. Their JSON detail
        # still has to prove the full date; an arbitrary missing date is invalid.
        if len(dates) > 1 or (not dates and 'LIVE' not in plain_text(cells[0])):
            raise SourceError('sportbox_rpl_calendar_date')
        if (team_matches(plain_text(teams[0]), home)
                and team_matches(plain_text(teams[1]), away)
                and (not dates or dates[0] == target)):
            candidates.append({'game_id': links[0][2], 'tournament_id': links[0][1]})
    if not rows:
        raise SourceError('sportbox_rpl_calendar_missing')
    if len(candidates) > 1:
        raise SourceError('sportbox_rpl_candidate_ambiguous')
    return candidates[0] if candidates else None


def parse_sportbox_rpl_json(raw, *, home, away, match_date, tournament_id):
    try:
        data = json.loads(raw)
    except (ValueError, TypeError) as exc:
        raise SourceError('sportbox_rpl_invalid_json') from exc
    if not isinstance(data, dict):
        raise SourceError('sportbox_rpl_json_structure')
    head, timeline = data.get('head_html'), data.get('football_timeline_html')
    if not isinstance(head, str) or not isinstance(timeline, str):
        raise SourceError('sportbox_rpl_detail_missing')
    teams = re.findall(r'(?is)<a\b[^>]*class="b-match__team-title"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', head)
    dates = re.findall(r'(?is)<span\b[^>]*class="match_count_date"[^>]*>(.*?)</span>', head)
    counts = re.findall(r'(?is)<span\b[^>]*class="b-match__monitor__count"[^>]*>(.*?)</span>', head)
    left, right = head.find('b-match__side_left'), head.find('b-match__side_right')
    if (len(teams) != 2 or len(dates) != 1 or len(counts) != 1 or not 0 <= left < right
            or not all(re.search(rf'/turnir_{re.escape(str(tournament_id))}$', href) for href, _ in teams)):
        raise SourceError('sportbox_rpl_identity_structure')
    # Each team must occur on its own side, not merely somewhere in the header.
    if (teams[0][0] not in head[left:right] or teams[1][0] not in head[right:]
            or not team_matches(plain_text(teams[0][1]), home)
            or not team_matches(plain_text(teams[1][1]), away)):
        raise SourceError('sportbox_rpl_team_mismatch')
    parsed_date = re.match(r'^(\d{2}\.\d{2}\.\d{4})\b', plain_text(dates[0]))
    if not parsed_date:
        raise SourceError('sportbox_rpl_detail_date')
    if parsed_date[1] != date.fromisoformat(match_date).strftime('%d.%m.%Y'):
        return Observation('sportbox_rpl', STATUS_NOT_FOUND, detail='date_mismatch')
    timeline_classes = re.search(r'<div\b[^>]*class="([^"]*)"[^>]*id="match_center_timeline"', timeline)
    if not timeline_classes or 'b-timeline' not in timeline_classes[1].split() or type(data.get('live')) is not int:
        raise SourceError('sportbox_rpl_timeline_structure')
    if 'b-timeline-end' not in timeline_classes[1].split() or data['live'] != 0:
        return Observation('sportbox_rpl', STATUS_NOT_FINISHED, match_date=match_date)
    score = re.fullmatch(r'\s*(\d{1,2})\s*:\s*(\d{1,2})\s*', str(data.get('score', '')))
    header_score = re.fullmatch(r'\s*(\d{1,2})\s*:\s*(\d{1,2})\s*', plain_text(counts[0]))
    if not score or not header_score or score.groups() != header_score.groups():
        raise SourceError('sportbox_rpl_score_structure')
    return Observation('sportbox_rpl', STATUS_FINISHED, home, away, match_date, int(score[1]), int(score[2]))
