import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from scripts import auto_result_runtime as runtime, auto_result_sources as sources, monitor_production
from scripts.auto_result_policy import quorum

worker = runtime.dry
FIXTURES = Path(__file__).parent / 'fixtures' / 'auto_results_v2'
IDENTITY = dict(home='Крылья Советов', away='Краснодар', match_date='2026-09-05')


def obs(name, value):
    return worker.Observation(name, 'finished', home_score=value[0], away_score=value[1]) if isinstance(value, tuple) else worker.Observation(name, value)


@pytest.mark.parametrize('values,expected', [
    ([(2,2),(2,2),(2,2)], 'would_write'),
    ([(2,2),'source_unavailable',(2,2)], 'would_write'),
    ([(2,2),'not_found',(2,2)], 'would_write'),
    (['source_unavailable',(2,2),(2,2)], 'would_write'),
    ([(2,2),(3,2),'source_unavailable'], 'score_conflict'),
    ([(2,2),(3,2),(2,2)], 'would_write'),
    ([(2,2),'not_found','not_finished'], 'one_source_confirmed'),
    (['source_unavailable']*3, 'waiting'),
    ([(2,2),'parser_error','not_found'], 'one_source_confirmed'),
    ([(2,2),(3,2),(4,2)], 'score_conflict'),
])
def test_quorum_matrix(values, expected):
    observations = [obs(n,v) for n,v in zip('ABC', values)]
    decision = quorum(observations)
    assert decision['decision'] == expected
    if expected == 'would_write':
        assert decision['score'] == (2,2)
    if values == [(2,2),(3,2),(2,2)]:
        assert decision['conflict'] and decision['votes']['B'] == (3,2)
        assert 'разные счета' in runtime._reason(observations, decision)


def test_same_source_cannot_vote_twice():
    assert quorum([obs('A',(2,2))]*2)['decision'] != 'would_write'


def calendar():
    return (FIXTURES / 'sportbox_rpl_calendar.html').read_text()


def game():
    return json.loads((FIXTURES / 'sportbox_game_1380958245.json').read_text())


def parse(data, **changes):
    return sources.parse_sportbox_rpl_json(json.dumps(data), tournament_id='23915', **{**IDENTITY, **changes})


def test_real_sportbox_fixture_identity_and_finished():
    candidate = sources.sportbox_rpl_candidate(calendar(), **IDENTITY)
    assert candidate == {'game_id':'1380958245', 'tournament_id':'23915'}
    result = parse(game())
    assert result.status == 'finished' and (result.home_score,result.away_score) == (2,2)


@pytest.mark.parametrize('changes', [dict(home='Краснодар',away='Крылья Советов'), dict(home='Крылья'), dict(match_date='2025-09-05')])
def test_sportbox_calendar_exact_identity(changes):
    assert sources.sportbox_rpl_candidate(calendar(), **{**IDENTITY,**changes}) is None


@pytest.mark.parametrize('change', ['teams','tournament','score','timeline','missing','captcha'])
def test_sportbox_json_changed_or_ambiguous_structure_fails_closed(change):
    d=game()
    if change == 'teams': d['head_html'] = d['head_html'].replace('Крылья Советов','Краснодар')
    if change == 'tournament': d['head_html'] = d['head_html'].replace('turnir_23915','turnir_1')
    if change == 'score': d['score']='3 : 2'
    if change == 'timeline': d['football_timeline_html']='<div>unknown</div>'
    if change == 'missing': d.pop('head_html')
    if change == 'captcha': d={'captcha':True}
    with pytest.raises(sources.SourceError): parse(d)


def test_sportbox_full_year_and_live_finality():
    assert parse(game(), match_date='2025-09-05').status == 'not_found'
    d=game(); d['live']=1
    assert parse(d).status == 'not_finished'
    d=game(); d['football_timeline_html']=d['football_timeline_html'].replace('b-timeline-end','')
    assert parse(d).status == 'not_finished'


def test_sportbox_duplicate_candidate_and_partial_html_rejected():
    for html in (calendar()+calendar(), calendar().replace('Крылья Советов</span>','</span>'), '<html>CAPTCHA</html>'):
        with pytest.raises(sources.SourceError): sources.sportbox_rpl_candidate(html, **IDENTITY)


@pytest.mark.parametrize('state,healthy', [('not_found',True),('not_finished',True),('parser_error',False),('source_unavailable',False)])
def test_source_state_is_separate_from_match_availability(state,healthy):
    cache=worker.PageCache()
    def callback():
        if state=='parser_error': raise worker.SourceError('structure')
        if state=='source_unavailable': raise worker.SourceUnavailable('request_failed:Timeout')
        return obs('sports_rpl',state)
    result=worker._guard_observation(cache,'sports_rpl',callback)
    assert result.status == state
    assert cache.source_status()['sports_rpl']['ok'] is healthy


def test_today_incident_retries_after_soft_deadline_then_records_quorum(monkeypatch,tmp_path):
    # Incident times supplied by operator; third-source recovery is deterministic.
    kickoff=datetime(2026,9,5,11,tzinfo=timezone.utc)  # 14:00 MSK
    m={'id':401,'kickoff_time':kickoff,'scope':'rpl','tournament_id':5,'league':'rpl',
       'match_category':'rpl','home_team':IDENTITY['home'],'away_team':IDENTITY['away'],'match_date':IDENTITY['match_date']}
    clock=[kickoff]; saved=[]; notices={}; checks=[]; messages=[]
    class Clock:
        @staticmethod
        def now(tz): return clock[0]
    monkeypatch.setattr(runtime,'datetime',Clock)
    monkeypatch.setattr(worker,'_load_matches',lambda now:[] if saved else [m])
    monkeypatch.setattr(runtime.delivery,'record_check',lambda m,t,reason:checks.append((t,reason)))
    monkeypatch.setattr(runtime.delivery,'last_check',lambda m:checks[-1] if checks else None)
    monkeypatch.setattr(runtime.delivery,'match_identity',lambda m:'incident')
    monkeypatch.setattr(runtime.delivery,'notify_pending',lambda m,k,t:notices.setdefault(k,t) if not saved else None)
    monkeypatch.setattr(runtime.delivery,'notify',lambda k,t:notices.setdefault(k,t))
    monkeypatch.setattr(worker,'_queue_message',lambda out,msg:messages.append(msg))
    from app.services import auto_result_finalization_service
    def finalize(mid,h,a,**kw):
        assert clock[0] < kickoff+timedelta(minutes=360)
        saved.append((h,a)); return 'saved'
    monkeypatch.setattr(auto_result_finalization_service,'finalize_auto_result',finalize)
    stages=[(120,'source_unavailable'),(125,'not_found'),(126,'not_found'),(155,'source_unavailable'),(181,'source_unavailable'),(210,'source_unavailable')]
    for elapsed,sports in stages:
        clock[0]=kickoff+timedelta(minutes=elapsed)
        def load(cache, urls):
            cache._pages.update({name:'fixture' for name in urls})
        monkeypatch.setattr(worker.PageCache,'load_many',load)
        monkeypatch.setattr(worker,'find_livesport_result',lambda *a,**k:obs('livesport',(2,2)))
        def sports_parse(*a,**k):
            if sports == 'source_unavailable': raise worker.SourceUnavailable('request_failed:Timeout')
            return obs('sports','not_found')
        monkeypatch.setattr(worker,'find_sports_rpl_result',sports_parse)
        monkeypatch.setattr(worker,'_sportbox_rpl_observation',lambda *a:obs('sportbox',(2,2) if elapsed==210 else 'not_found'))
        runtime._run_live(clock[0],state_path=tmp_path/'state',outbox=tmp_path)
        assert bool(saved) == (elapsed==210)
    assert saved == [(2,2)]
    assert list(notices).count('delayed:incident') == 1
    assert any('восстановился' in message for message in messages)
    assert 'sports_rpl: ошибка загрузки' in checks[-1][1]
    runtime._run_live(clock[0],state_path=tmp_path/'state',outbox=tmp_path)
    assert saved == [(2,2)]


def test_hard_deadline_never_fetches_or_writes(monkeypatch,tmp_path):
    now=datetime.now(timezone.utc)
    m={'id':1,'kickoff_time':now-timedelta(minutes=361),'home_team':'A','away_team':'B'}
    monkeypatch.setattr(worker,'_load_matches',lambda now:[m])
    monkeypatch.setattr(worker.PageCache,'load_many',lambda self,urls:assert_empty(urls))
    monkeypatch.setattr(runtime.delivery,'last_check',lambda m:(now,'A: подтвердил 2:2; B: ошибка загрузки'))
    monkeypatch.setattr(runtime.delivery,'match_identity',lambda m:'hard')
    notice=Mock(); monkeypatch.setattr(runtime.delivery,'notify_pending',notice)
    runtime._run_live(now,state_path=tmp_path/'state',outbox=None)
    assert 'нужен ручной результат' in notice.call_args.args[2]
    assert 'B: ошибка загрузки' in notice.call_args.args[2]


def assert_empty(urls):
    assert urls == {}


@pytest.mark.parametrize('failure,kind,code', [('timeout','timeout','unknown'),('exit','DB failure','1'),('shell','shell failure','unknown')])
def test_monitor_safe_actionable_diagnostics(monkeypatch,failure,kind,code):
    monkeypatch.setenv('SECRET_KEY','special-private-value')
    stdout='checked\nDATABASE_URL=postgresql://user:pass@host/db\n'+'x'*1000
    stderr='OperationalError\npassword=hidden\nTOKEN=123456789:abcdefghijklmnopqrstuvwxyz\nspecial-private-value'
    def run(*a,**k):
        if failure=='timeout': raise subprocess.TimeoutExpired('monitor',45,output=stdout.encode(),stderr=stderr.encode())
        if failure=='shell': raise OSError('secret exception text')
        return SimpleNamespace(returncode=1,stdout=stdout,stderr=stderr)
    monkeypatch.setattr(monitor_production,'run',run)
    alert=Mock(); monkeypatch.setattr(monitor_production,'alert',alert)
    assert monitor_production.check_auto_results() is False
    key,details=alert.call_args.args
    assert key=='auto_results:monitor_failed' and kind in details and f'exit_code={code}' in details
    assert 'stdout:' in details and 'stderr:' in details and len(details)<1500
    for secret in ('user:pass','hidden','abcdefghijklmnopqrstuvwxyz','special-private-value','secret exception text'):
        assert secret not in details
    assert 'неизвестно' in details


def test_monitor_success_no_alert(monkeypatch):
    monkeypatch.setattr(monitor_production,'run',lambda *a,**k:SimpleNamespace(returncode=0))
    alert=Mock(); monkeypatch.setattr(monitor_production,'alert',alert)
    assert monitor_production.check_auto_results()
    alert.assert_not_called()


def test_monitor_does_not_fetch_football_sources(monkeypatch):
    monkeypatch.setenv('AUTO_RESULTS_ENABLED','true'); monkeypatch.setenv('AUTO_RESULTS_DRY_RUN','false')
    monkeypatch.setattr(runtime.delivery,'enabled_since',lambda active:datetime.now(timezone.utc))
    monkeypatch.setattr(worker,'_load_matches',lambda *a,**k:[])
    fetch=Mock(side_effect=AssertionError('monitor must not fetch football'))
    monkeypatch.setattr(worker,'fetch_text',fetch)
    assert runtime.monitor(datetime.now(timezone.utc))['pending_matches']==0
    fetch.assert_not_called()


@pytest.mark.parametrize('gid,identity', [
    ('1380958247',dict(home='Ростов',away='Факел',match_date='2026-09-05')),
    ('1380958249',dict(home='Оренбург',away='Акрон',match_date='2026-09-06')),
])
def test_real_sportbox_live_and_scheduled_are_healthy_nonvotes(gid,identity):
    data=json.loads((FIXTURES/f'sportbox_game_{gid}.json').read_text())
    result=parse(data,**identity)
    assert result.status=='not_finished'
    assert result.home_score is None and result.away_score is None
    assert quorum([obs('A',(2,2)),result])['decision']=='one_source_confirmed'


def test_source_outage_notice_does_not_claim_quorum_write_impossible(monkeypatch):
    messages=[]
    monkeypatch.setattr(worker,'_queue_message',lambda out,message:messages.append(message))
    worker._update_source_health({}, {'sports_rpl':{'ok':False}}, None)
    assert 'невозможна' not in messages[0]
    assert 'два согласованных подтверждения' in messages[0]
