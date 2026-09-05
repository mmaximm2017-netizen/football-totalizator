"""Regressions for source identity, diagnostic state and regulation-time scores."""
from unittest.mock import patch

import pytest

from scripts import auto_result_sources as sources
from scripts import auto_result_worker as worker


def sports_card(home='Зенит', away='ЦСКА', day='2026-09-05', score=(2, 1)):
    return f'''<article data-id="42" class="calendar-card extra-class">
    <a title="Матч {home} - {away}"></a>
    <time datetime="{day}T13:30:00Z"></time><div>Матч окончен</div>
    <span data-score="home" class="calendar-score__score">{score[0]}</span>
    <span class="calendar-score__score">{score[1]}</span></article>'''


def cup_card(home='Зенит', away='Динамо', home_id='34', away_id='3637', href='/match/42'):
    return f'''<div class="tour-match">
    <a class="tour-match__team first" href="/cup/teams/{home_id}"><span class="tour-match__name">{home}</span></a>
    <a class="tour-match__score-block" href="{href}"><span class="tour-match__score">3 : 0</span></a>
    <a class="tour-match__team last" href="/cup/teams/{away_id}"><span class="tour-match__name">{away}</span></a>
    </div>'''


def cup_match(**overrides):
    return {'scope': 'cup', 'home_team': 'Зенит', 'away_team': 'Динамо Мх',
            'match_date': '2026-09-02', **overrides}


def cup_observation(html, match=None, detail='2 сентября 2026 Матч окончен'):
    cache = worker.PageCache()
    cache._pages['rfs_cup'] = html
    with patch.object(worker, 'fetch_text', return_value=detail):
        return worker._rfs_cup_observation(cache, match or cup_match())


def test_invalid_utf8_state_is_discarded(tmp_path):
    path = tmp_path / 'state.json'
    path.write_bytes(b'\xff\xfe{\x00}')
    assert worker._load_state(path) == {}


@pytest.mark.parametrize('requested_home,requested_away,source_home,source_away,found', [
    ('Зенит', 'Динамо', 'Зенит', 'Динамо Мх', False),
    ('Динамо', 'Зенит', 'Динамо Мх', 'Зенит', False),
    ('Зенит', 'Динамо Мх', 'Зенит', 'Динамо', False),
    ('Зенит', 'Динамо Мх', 'Зенит', 'Динамо Махачкала', True),
    ('Динамо Мх', 'Зенит', 'Динамо Махачкала', 'Зенит', True),
    ('Зенит', 'ЦСКА', 'ЗЕНИТ Санкт-Петербург', 'цска Москва', True),
])
def test_livesport_full_home_and_away_names(requested_home, requested_away, source_home, source_away, found):
    html = f'5 сентября, суббота, 2026 Ок {source_home} 2:1 {source_away} 7'
    result = sources.find_livesport_result(html, home=requested_home, away=requested_away, match_date='2026-09-05')
    assert (result.status == 'finished') is found


@pytest.mark.parametrize('canonical,alias', [(key, alias) for key, values in sources.ALIASES.items() for alias in values])
def test_livesport_existing_explicit_aliases(canonical, alias):
    for home, away, sh, sa in [(canonical, 'Контроль', alias, 'Контроль'), ('Контроль', canonical, 'Контроль', alias)]:
        result = sources.find_livesport_result(f'5 сентября, суббота, 2026 Ок {sh} 1:0 {sa}',
                                              home=home, away=away, match_date='2026-09-05')
        assert result.status == 'finished'


def test_livesport_duplicate_candidate_is_ambiguous():
    with pytest.raises(sources.SourceError, match='ambiguous'):
        sources.find_livesport_result('5 сентября, суббота, 2026 Ок Зенит 1:0 ЦСКА 7 Ок Зенит 2:0 ЦСКА 7',
                                     home='Зенит', away='ЦСКА', match_date='2026-09-05')


@pytest.mark.parametrize('score,expected', [('1:1 10:11', (1, 1)), ('10:11', (10, 11))])
def test_livesport_two_digit_score_is_not_a_kickoff(score, expected):
    html = f'5 сентября, суббота, 2026 Ок пен Зенит {score} ЦСКА C 19:00 Спартак –:– Ростов C'
    result = sources.find_livesport_result(html, home='Зенит', away='ЦСКА', match_date='2026-09-05', regulation_only=True)
    assert result.status == 'finished' and (result.home_score, result.away_score) == expected
    waiting = sources.find_livesport_result(html, home='Спартак', away='Ростов', match_date='2026-09-05')
    assert waiting.status == 'not_finished'


def test_rfs_makhachkala_uses_club_id_and_full_context():
    result = cup_observation(cup_card())
    assert result.status == 'finished' and (result.home_score, result.away_score) == (3, 0)
    assert cup_observation(cup_card(), cup_match(match_date='2026-09-03')).status == 'not_found'
    assert cup_observation(cup_card(), cup_match(home_team='ЦСКА')).status == 'not_found'
    assert cup_observation(cup_card(), cup_match(home_team='Динамо Мх', away_team='Зенит')).status == 'not_found'
    assert not sources.team_matches('Динамо', 'Динамо Мх')  # no global alias change


def test_rfs_moscow_never_becomes_makhachkala():
    assert cup_observation(cup_card(away_id='35')).status == 'not_found'
    assert cup_observation(cup_card(away_id='35'), cup_match(away_team='Динамо')).status == 'finished'
    assert cup_observation(cup_card(), cup_match(away_team='Динамо')).status == 'not_found'


@pytest.mark.parametrize('club_id', ['', '999999'])
def test_rfs_unqualified_dynamo_is_not_a_confirmation(club_id):
    with pytest.raises(worker.SourceError, match='identity_unproven'):
        cup_observation(cup_card(away_id=club_id))


def test_rfs_name_id_contradiction_is_rejected():
    with pytest.raises(worker.SourceError, match='name_id_conflict'):
        cup_observation(cup_card(away='Динамо Москва', away_id='3637'))


def test_rfs_two_matching_fixtures_are_ambiguous():
    with pytest.raises(worker.SourceError, match='candidate_ambiguous'):
        cup_observation(cup_card() + cup_card(href='/match/43'))


def test_sports_valid_absent_match_is_healthy():
    cache = worker.PageCache()
    result = worker._guard_observation(cache, 'sports_rpl', lambda: worker.find_sports_rpl_result(
        sports_card(), home='Спартак', away='ЦСКА', match_date='2026-09-05'))
    assert result.status == 'not_found'
    assert cache.source_status()['sports_rpl'] == {'ok': True}


@pytest.mark.parametrize('bad', [
    sports_card().replace('title=', 'aria-label='),
    sports_card().replace('datetime=', 'data-time='),
    sports_card(day='2026-99-99'),
])
def test_sports_partial_degradation_is_unhealthy_without_recovery(bad):
    cache = worker.PageCache()
    state = {'source_health': {'sports_rpl': False}}
    with pytest.raises(worker.SourceError):
        worker._guard_observation(cache, 'sports_rpl', lambda: worker.find_sports_rpl_result(
            bad, home='Зенит', away='ЦСКА', match_date='2026-09-05'))
    assert not cache.source_status()['sports_rpl']['ok']
    with patch.object(worker, '_queue_message') as notify:
        worker._update_source_health(state, cache.source_status(), None)
    notify.assert_not_called()


def test_sports_valid_current_fields_and_attribute_order():
    result = sources.find_sports_rpl_result(sports_card(), home='Зенит', away='ЦСКА', match_date='2026-09-05')
    assert result.status == 'finished' and (result.home_score, result.away_score) == (2, 1)


def test_sports_team_anchors_are_a_structural_fallback():
    html = sports_card().replace('<a title="Матч Зенит - ЦСКА"></a>',
                                '<a class="calendar-card__home">Зенит<img alt="logo"></a>'
                                '<a class="calendar-card__away">ЦСКА</a>')
    assert sources.find_sports_rpl_result(html, home='Зенит', away='ЦСКА', match_date='2026-09-05').status == 'finished'


def test_sports_must_finish_validation_before_accepting_target():
    html = sports_card() + sports_card(home='Спартак').replace('datetime=', 'data-time=')
    with pytest.raises(sources.SourceError):
        sources.find_sports_rpl_result(html, home='Зенит', away='ЦСКА', match_date='2026-09-05')


@pytest.mark.parametrize('marker,score', [('Ок', '1:1'), ('Ок пен', '1:1 5:4')])
def test_cup_livesport_regulation_and_shootout(marker, score):
    result = sources.find_livesport_result(f'5 сентября, суббота, 2026 {marker} Зенит {score} ЦСКА C',
                                          home='Зенит', away='ЦСКА', match_date='2026-09-05', regulation_only=True)
    assert result.status == 'finished' and (result.home_score, result.away_score) == (1, 1)


def test_cup_livesport_extra_time_is_never_regulation_score():
    with pytest.raises(sources.SourceError, match='90_minute_score_unproven'):
        sources.find_livesport_result('5 сентября, суббота, 2026 Ок д.в. Зенит 2:1 ЦСКА C',
                                     home='Зенит', away='ЦСКА', match_date='2026-09-05', regulation_only=True)


@pytest.mark.parametrize('marker', ['д.в.', 'Дополнительное время', 'Второй дополнительный тайм', '120′'])
def test_cup_rfs_extra_time_is_never_regulation_score(marker):
    with pytest.raises(sources.SourceError, match='90_minute_score_unproven'):
        sources.parse_rfs_detail(f'5 сентября 2026 Матч окончен {marker}', score=(2, 1), regulation_only=True)


def test_cup_rfs_penalties_are_separate_from_regulation_score():
    html = cup_card().replace('3 : 0', '1 : 1<span class="tour-match__penalty">5:4</span>')
    candidate = sources.rfs_cup_candidates(html)[0]
    result = sources.parse_rfs_detail('2 сентября 2026 Матч окончен Пенальти 5 : 4',
                                      score=candidate['score'], regulation_only=True)
    assert (result.home_score, result.away_score) == (1, 1)


@pytest.mark.parametrize('html', [
    cup_card().replace('3 : 0', '3 : 0 д.в.'),
    cup_card().replace('3 : 0', '3 : 0 (1 : 0)'),
    cup_card().replace('class="tour-match"', 'class="tour-match" data-overtime="true"'),
])
def test_cup_calendar_period_cannot_be_lost_on_detail_page(html):
    with pytest.raises(worker.SourceError, match='90_minute_score_unproven'):
        cup_observation(html)


def test_cup_runtime_does_not_consense_different_periods():
    from scripts import auto_result_runtime as runtime
    cache = runtime.dry.PageCache()
    cache._pages['livesport_cup'] = '2 сентября, среда, 2026 Ок Зенит 3:0 Динамо Мх C'
    cache._pages['rfs_cup'] = cup_card()
    with patch.object(runtime.dry, 'fetch_text', return_value='2 сентября 2026 Матч окончен д.в.'):
        _, first, second, decision = runtime._observe(cache, cup_match())
    assert first is None and second is None and decision['decision'] == 'source_error'
    assert not cache.source_status()['rfs_cup']['ok']


def test_sports_incomplete_card_does_not_look_like_absent_match():
    with pytest.raises(sources.SourceError, match='incomplete'):
        sources.find_sports_rpl_result(sports_card() + sports_card(home='Спартак').replace('</article>', ''),
                                      home='Зенит', away='ЦСКА', match_date='2026-09-05')


def test_sports_recovery_requires_real_valid_parser_pass():
    state = {'source_health': {'sports_rpl': False}}
    cache = worker.PageCache()
    worker._guard_observation(cache, 'sports_rpl', lambda: worker.find_sports_rpl_result(
        sports_card(), home='Зенит', away='ЦСКА', match_date='2026-09-05'))
    with patch.object(worker, '_queue_message') as notify:
        worker._update_source_health(state, cache.source_status(), None)
        worker._update_source_health(state, cache.source_status(), None)
    assert notify.call_count == 1
    assert 'восстановился' in notify.call_args.args[1]
