from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

import pytest


def test_unknown_deadline_does_not_publish_predictions():
    from app.routes.predictions import predictions_bp
    from flask import Flask

    app = Flask(__name__)
    app.secret_key = 'test'
    app.register_blueprint(predictions_bp)
    app.add_url_rule('/', 'main.index', lambda: 'home')
    cur = Mock()
    cur.fetchall.return_value = []
    cur.fetchone.return_value = (10, 'Home', 'Away', None, None, 'SCHEDULED', None, None, 5)
    with patch('app.routes.predictions.get_db', return_value=Mock(cursor=lambda: cur)), patch('app.routes.predictions.close_db'):
        response = app.test_client().get('/match/10/predictions')
    assert response.status_code == 302
    cur.fetchall.assert_not_called()


def test_deadline_publication_requires_known_past_time():
    from app.utils import has_deadline_passed

    now = datetime.now(timezone.utc)
    assert not has_deadline_passed({'deadline': None}, now)
    assert not has_deadline_passed({'deadline': 'broken'}, now)
    assert not has_deadline_passed({'deadline': now + timedelta(seconds=1)}, now)
    assert has_deadline_passed({'deadline': now}, now)


def test_request_limiter_bounds_identity_and_total_without_database():
    from app.services.request_limits import RequestLimiter

    limiter = RequestLimiter()
    assert limiter.allow('register', 'a', 2, 3, now=10)
    assert limiter.allow('register', 'a', 2, 3, now=11)
    assert not limiter.allow('register', 'a', 2, 3, now=12)
    assert limiter.allow('register', 'b', 2, 3, now=13)
    assert not limiter.allow('register', 'c', 2, 3, now=14)
    assert limiter.allow('register', 'a', 2, 3, now=71)


def test_denied_rotating_identities_cannot_grow_limiter_memory():
    from app.services.request_limits import RequestLimiter
    limiter = RequestLimiter(capacity=4)
    assert limiter.allow('register', 'a', 1, 2, now=1)
    assert limiter.allow('register', 'b', 1, 2, now=1)
    for index in range(1000):
        assert not limiter.allow('register', str(index), 1, 2, now=2)
    assert len(limiter.buckets) <= 4


def test_analytics_drops_excess_work_without_starting_more_workers():
    from app.services.product_analytics import AnalyticsDispatcher
    import threading

    entered, release = threading.Event(), threading.Event()
    def deliver(*args):
        entered.set()
        release.wait(2)
    dispatcher = AnalyticsDispatcher(deliver, capacity=1)
    try:
        assert dispatcher.submit(('event', 'id', {}))
        assert entered.wait(1)
        assert dispatcher.submit(('event', 'id', {}))
        assert not dispatcher.submit(('event', 'id', {}))
    finally:
        release.set()
        dispatcher.queue.join()


@pytest.mark.parametrize('field', ['API_KEY', 'INVITE_CODE', 'ADMIN_PASSWORD', 'TOTISH_GPT_TOKEN', 'WEB_PUSH_VAPID_PRIVATE_KEY'])
def test_error_notifier_redacts_all_configured_secrets(monkeypatch, field):
    from app.services.telegram_error_notifier import _redact
    monkeypatch.setenv(field, 'private-value-12345')
    assert 'private-value-12345' not in _redact('problem private-value-12345')


def test_push_rejects_arbitrary_public_provider_before_dns():
    from app.services.web_push_service import validate_endpoint_target, UnsafePushEndpointError
    resolver = Mock()
    with pytest.raises(UnsafePushEndpointError):
        validate_endpoint_target('https://attacker.example/push', resolver=resolver)
    resolver.assert_not_called()
