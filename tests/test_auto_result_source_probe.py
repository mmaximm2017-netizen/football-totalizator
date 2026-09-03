from scripts.auto_result_source_probe_core import (
    STATUS_FINISHED,
    STATUS_NOT_FOUND,
    SourceParseError,
    find_exact_match,
    parse_flashscore_feed,
    parse_rfs_cup_listing,
    parse_rfs_finished_detail,
    parse_rfs_national_listing,
)


def test_flashscore_feed_reads_finished_match_and_order():
    html = (
        "prefix~AA÷r7uokbnp¬AD÷1787929200¬AB÷3¬AF÷ЦСКА¬AH÷2"
        "¬AE÷Акрон Тольятти¬AG÷2¬suffix"
    )
    matches = parse_flashscore_feed(html)
    assert len(matches) == 1
    match = matches[0]
    assert match.status == STATUS_FINISHED
    assert match.match_date == "2026-08-28"
    assert (match.home_team, match.away_team) == ("Акрон Тольятти", "ЦСКА")
    assert (match.home_score, match.away_score) == (2, 2)


def test_flashscore_feed_ignores_non_finished_state():
    html = "prefix~AA÷x¬AD÷1787929200¬AB÷1¬AF÷ЦСКА¬AH÷2¬AE÷Акрон¬AG÷2¬"
    assert parse_flashscore_feed(html) == []


def test_flashscore_feed_fails_closed_when_structure_missing():
    try:
        parse_flashscore_feed("<html>changed</html>")
    except SourceParseError:
        pass
    else:
        raise AssertionError("changed Flashscore structure must not be accepted")


def test_rfs_cup_listing_keeps_regulation_score_and_separates_penalty():
    html = """
    <div class="tour-match">
      <div class="tour-match__inner">
        <a class="tour-match__team first"><span class="tour-match__name">Краснодар</span></a>
        <a href="/cup/tournament/match/57186" class="tour-match__score-block">
          <div><div class="tour-match__score"><span>1</span><span>:</span><span>1</span></div>
          <div class="tour-match__penalty">5:4</div></div>
        </a>
        <a class="tour-match__team last"><span class="tour-match__name">Динамо</span></a>
      </div>
    </div>
    """
    matches = parse_rfs_cup_listing(html)
    assert matches == [{
        "home_team": "Краснодар",
        "away_team": "Динамо",
        "match_href": "/cup/tournament/match/57186",
        "home_score": 1,
        "away_score": 1,
        "penalty": "5:4",
    }]


def test_rfs_finished_detail_uses_regulation_score_not_time_or_penalty():
    html = """
      <h1>18 августа 2026</h1>
      <p>Начало 19:30</p>
      <div>Матч окончен</div>
      <div>1 : 1</div><div>Пенальти 5:4</div>
    """
    result = parse_rfs_finished_detail(html, regulation_score=(1, 1))
    assert result.status == STATUS_FINISHED
    assert result.match_date == "2026-08-18"
    assert (result.home_score, result.away_score) == (1, 1)


def test_rfs_detail_does_not_accept_unfinished_match():
    result = parse_rfs_finished_detail(
        "<h1>18 августа 2026</h1><div>Матч идет</div>", regulation_score=(1, 0)
    )
    assert result.status == STATUS_NOT_FOUND


def test_rfs_national_listing_reads_order_date_score_and_href():
    html = """
    <div class="calendar__row" onclick="window.location.href='/match/56586'">
      <div class="calendar__row-first"><div class="time">27.03.2026<p>20:00</p></div></div>
      <div class="calendar__row-team team1"><div class="title">Россия</div></div>
      <a href="/match/56586" class="score-box"><div class="score-item only">
        <span>3</span><span>1</span>
      </div></a>
      <div class="calendar__row-team team2"><div class="title">Никарагуа</div></div>
    </div>
    """
    matches = parse_rfs_national_listing(html)
    assert matches == [{
        "home_team": "Россия",
        "away_team": "Никарагуа",
        "match_date": "2026-03-27",
        "match_href": "/match/56586",
        "home_score": 3,
        "away_score": 1,
    }]


def test_exact_match_requires_date_order_and_only_explicit_aliases():
    observation = parse_flashscore_feed(
        "~AA÷x¬AD÷1787929200¬AB÷3¬AF÷ЦСКА Москва¬AH÷1¬AE÷Акрон Тольятти¬AG÷2¬"
    )[0]
    assert find_exact_match(
        [observation],
        home_team="Акрон",
        away_team="ЦСКА",
        match_date="2026-08-28",
        home_aliases=("Акрон Тольятти",),
        away_aliases=("ЦСКА Москва",),
    ) == observation
    assert find_exact_match(
        [observation],
        home_team="ЦСКА",
        away_team="Акрон",
        match_date="2026-08-28",
        home_aliases=("ЦСКА Москва",),
        away_aliases=("Акрон Тольятти",),
    ) is None
    assert find_exact_match(
        [observation],
        home_team="Акрон",
        away_team="ЦСКА",
        match_date="2026-08-29",
        home_aliases=("Акрон Тольятти",),
        away_aliases=("ЦСКА Москва",),
    ) is None
