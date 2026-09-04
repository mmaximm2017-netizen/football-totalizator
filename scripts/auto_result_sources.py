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
from urllib.parse import urljoin
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
        raise SourceError(f"request_failed:{type(exc).__name__}") from exc

def _date_pattern(day, month_name, year):
    # LiveSport uses both "28 августа, пятница, 2026" and compact date forms.
    return re.compile(rf"\b{day}\s+{month_name}(?:,\s*[^,]+)?(?:,\s*)?{year}\b", re.I)

def find_livesport_result(html, *, home, away, match_date, source="livesport"):
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
    next_hit = re.search(rf"\b\d{{1,2}}\s+(?:{MONTH_RE})(?:,\s*[^,]+)?(?:,\s*)?{target.year}\b", text[hit.end():], re.I)
    segment = text[hit.end():hit.end()+next_hit.start()] if next_hit else text[hit.end():]
    for ha in aliases_for(home):
        for aa in aliases_for(away):
            m = re.search(r"\bОк(?:\s+(?:пен|д\.в\.))?\s+" + re.escape(ha) + r"\s+(\d{1,2})\s*:\s*(\d{1,2})(?:\s+\d{1,2}\s*:\s*\d{1,2})?\s+" + re.escape(aa) + r"(?:\b|\s)", segment, re.I)
            if m: return Observation(source, STATUS_FINISHED, ha, aa, match_date, int(m.group(1)), int(m.group(2)))
    normalized = normalize_team(segment)
    if any(normalize_team(x) in normalized for x in aliases_for(home)) and any(normalize_team(x) in normalized for x in aliases_for(away)):
        return Observation(source, STATUS_NOT_FINISHED, detail="match_present_without_final_marker")
    return Observation(source, STATUS_NOT_FOUND, detail="match_not_found")

def find_sports_rpl_result(html, *, home, away, match_date):
    cards = re.findall(r"(?is)<article\s+class=\"calendar-card\".*?</article>", html)
    if not cards: raise SourceError("sports_calendar_cards_missing")
    for card in cards:
        tm = re.search(r'title="Матч\s+([^\"]+?)\s+-\s+([^\"]+?)"', card, re.I)
        dm = re.search(r'<time[^>]+datetime="(\d{4}-\d{2}-\d{2})T', card, re.I)
        if not tm or not dm or dm.group(1) != match_date: continue
        sh, sa = map(unescape, tm.groups())
        if not team_matches(sh, home) or not team_matches(sa, away): continue
        if "Матч окончен" not in unescape(card): return Observation("sports", STATUS_NOT_FINISHED, sh, sa, match_date)
        scores = re.findall(r'class="calendar-score__score"[^>]*>\s*(\d{1,2})\s*<', card, re.I)
        if len(scores) < 2: raise SourceError("sports_finished_score_missing")
        return Observation("sports", STATUS_FINISHED, sh, sa, match_date, int(scores[0]), int(scores[1]))
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
        if tag=="div" and "tour-match" in c: self.current={"home":[],"away":[],"score":[],"href":None}
        if self.current is not None and tag=="a" and "tour-match__score-block" in c: self.current["href"]=d.get("href")
    def handle_endtag(self,tag):
        ended=self.stack[-1] if self.stack else (None,set())
        if ended[0]=="div" and "tour-match" in ended[1] and self.current is not None: self.matches.append(self.current); self.current=None
        super().handle_endtag(tag)
    def handle_data(self,data):
        if self.current is None or not data.strip(): return
        t=data.strip()
        if self.has("tour-match__score") and not self.has("tour-match__penalty"): self.current["score"].append(t)
        elif self.has("tour-match__name") and self.has("tour-match__team","first"): self.current["home"].append(t)
        elif self.has("tour-match__name") and self.has("tour-match__team","last"): self.current["away"].append(t)

def rfs_cup_candidates(html):
    p=RfsCupParser(); p.feed(html)
    if not p.matches: raise SourceError("rfs_cup_cards_missing")
    out=[]
    for x in p.matches:
        h,a=" ".join(x["home"]).strip()," ".join(x["away"]).strip(); s=re.search(r"(\d+)\s*:\s*(\d+)"," ".join(x["score"]))
        if h and a and x["href"]: out.append({"home":h,"away":a,"href":x["href"],"score":(int(s.group(1)),int(s.group(2))) if s else None})
    if not out: raise SourceError("rfs_cup_fields_missing")
    return out

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

def parse_rfs_detail(html, *, score=None, source="rfs"):
    text=plain_text(html); dm=re.search(rf"\b(\d{{1,2}})\s+({MONTH_RE})\s+(20\d{{2}})\b",text,re.I)
    if not dm: raise SourceError("rfs_match_date_missing")
    day,month_name,year=dm.groups(); md=date(int(year),MONTHS[month_name.casefold()],int(day)).isoformat()
    if "Матч окончен" not in text: return Observation(source,STATUS_NOT_FINISHED,match_date=md)
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
