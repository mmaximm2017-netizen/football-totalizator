"""Network-route failures are simulated; no real Telegram traffic in tests."""
import errno
from types import SimpleNamespace
import socket
import ssl
from unittest.mock import patch

import pytest

from scripts import telegram_http as http


class Clock:
    now = 0

    def monotonic(self):
        return self.now


class FakeSocket:
    def __init__(self, routes):
        self.routes = routes
        self.closed = False
        self.blocking = True
        self.timeout = None
        self.ready = False
        self.error = 0

    def setblocking(self, value):
        self.blocking = value

    def bind(self, address):
        self.source = address

    def connect_ex(self, target):
        result, self.ready, self.error = self.routes[target[0]]
        return result

    def getsockopt(self, *_args):
        return self.error

    def settimeout(self, value):
        self.timeout = value
        self.blocking = value != 0

    def close(self):
        self.closed = True


class FakeSelector:
    def __init__(self, clock):
        self.clock = clock
        self.connections = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        pass

    def register(self, conn, _event):
        self.connections.append(conn)

    def unregister(self, conn):
        self.connections.remove(conn)

    def select(self, timeout):
        ready = [(SimpleNamespace(fileobj=c), 1) for c in self.connections if c.ready]
        if not ready:
            assert timeout is not None
            self.clock.now += timeout
        return ready


def route(family, host):
    return family, socket.SOCK_STREAM, 6, '', (host, 443)


@pytest.fixture
def network(monkeypatch):
    clock = Clock()
    created = []
    routes = {}

    def factory(*_args):
        conn = FakeSocket(routes)
        created.append(conn)
        return conn

    monkeypatch.setattr(http.time, 'monotonic', clock.monotonic)
    monkeypatch.setattr(http.socket, 'socket', factory)
    monkeypatch.setattr(http.selectors, 'DefaultSelector', lambda: FakeSelector(clock))
    return clock, created, routes


@pytest.mark.parametrize('first,second', [(socket.AF_INET, socket.AF_INET6), (socket.AF_INET6, socket.AF_INET)])
def test_black_holed_first_family_does_not_consume_full_timeout(network, monkeypatch, first, second):
    clock, created, routes = network
    routes.update(dead=(errno.EINPROGRESS, False, 0), good=(errno.EINPROGRESS, True, 0))
    monkeypatch.setattr(http.socket, 'getaddrinfo', lambda *a, **k: [route(first, 'dead'), route(second, 'good')])
    winner = http.connect_telegram(('api.telegram.org', 443), timeout=5)
    assert clock.now == http.CONNECT_DELAY
    assert created[0].closed and winner is created[1] and not winner.closed
    assert winner.timeout == 5 and winner.blocking


def test_one_deadline_covers_all_failed_addresses_and_closes_every_socket(network, monkeypatch):
    clock, created, routes = network
    routes.update(dead=(errno.EINPROGRESS, False, 0))
    monkeypatch.setattr(http.socket, 'getaddrinfo', lambda *a, **k: [route(socket.AF_INET, 'dead'), route(socket.AF_INET6, 'dead')])
    with pytest.raises(TimeoutError):
        http.connect_telegram(('api.telegram.org', 443), timeout=3)
    assert clock.now == 3
    assert len(created) == 2 and all(c.closed for c in created)


def test_working_first_route_does_not_wait_or_open_other_family(network, monkeypatch):
    clock, created, routes = network
    routes.update(good=(errno.EINPROGRESS, True, 0))
    monkeypatch.setattr(http.socket, 'getaddrinfo', lambda *a, **k: [route(socket.AF_INET6, 'good'), route(socket.AF_INET, 'unused')])
    winner = http.connect_telegram(('api.telegram.org', 443), timeout=3)
    assert clock.now == 0 and created == [winner] and not winner.closed


def test_immediate_refusal_tries_next_address_without_waiting(network, monkeypatch):
    clock, created, routes = network
    routes.update(bad=(errno.ECONNREFUSED, False, 0), good=(0, True, 0))
    monkeypatch.setattr(http.socket, 'getaddrinfo', lambda *a, **k: [route(socket.AF_INET, 'bad'), route(socket.AF_INET6, 'good')])
    winner = http.connect_telegram(('api.telegram.org', 443), timeout=3, source_address=('', 0))
    assert clock.now == 0 and created[0].closed and winner.source == ('', 0)


def test_async_connection_refusal_closes_socket_and_tries_next(network, monkeypatch):
    _, created, routes = network
    routes.update(bad=(errno.EINPROGRESS, True, errno.ECONNREFUSED), good=(0, True, 0))
    monkeypatch.setattr(http.socket, 'getaddrinfo', lambda *a, **k: [route(socket.AF_INET, 'bad'), route(socket.AF_INET6, 'good')])
    winner = http.connect_telegram(('api.telegram.org', 443), timeout=3)
    assert created[0].closed and winner is created[1] and not winner.closed


def test_all_immediate_errors_keep_last_error_and_close_sockets(network, monkeypatch):
    _, created, routes = network
    routes.update(bad=(errno.ECONNREFUSED, False, 0))
    monkeypatch.setattr(http.socket, 'getaddrinfo', lambda *a, **k: [route(socket.AF_INET, 'bad')])
    with pytest.raises(ConnectionRefusedError):
        http.connect_telegram(('api.telegram.org', 443), timeout=3)
    assert all(c.closed for c in created)


def test_selector_error_closes_pending_sockets(network, monkeypatch):
    _, created, routes = network
    routes.update(dead=(errno.EINPROGRESS, False, 0))
    monkeypatch.setattr(http.socket, 'getaddrinfo', lambda *a, **k: [route(socket.AF_INET, 'dead')])
    with patch.object(FakeSelector, 'select', side_effect=OSError('selector failed')), pytest.raises(OSError):
        http.connect_telegram(('api.telegram.org', 443), timeout=3)
    assert len(created) == 1 and created[0].closed


def test_empty_dns_result_fails_without_opening_socket(network, monkeypatch):
    _, created, _ = network
    monkeypatch.setattr(http.socket, 'getaddrinfo', lambda *a, **k: [])
    with pytest.raises(OSError):
        http.connect_telegram(('api.telegram.org', 443), timeout=3)
    assert not created


def test_duplicate_addresses_are_removed_and_families_interleaved(monkeypatch):
    v4a, v4b, v6 = route(socket.AF_INET, 'v4a'), route(socket.AF_INET, 'v4b'), route(socket.AF_INET6, 'v6')
    monkeypatch.setattr(http.socket, 'getaddrinfo', lambda *a, **k: [v4a, v4a, v4b, v6])
    assert http._addresses('api.telegram.org', 443) == [v4a, v6, v4b]


def test_proxy_and_other_hosts_keep_standard_connection_behavior():
    with patch.object(http.socket, 'create_connection', return_value='proxy') as connect:
        assert http.connect_telegram(('proxy.example', 443), 7, ('', 0)) == 'proxy'
    connect.assert_called_once_with(('proxy.example', 443), 7, ('', 0))


def test_tls_certificate_and_hostname_verification_remain_enabled():
    connection = http.TelegramHTTPSConnection('api.telegram.org', timeout=3)
    assert connection._context.verify_mode == ssl.CERT_REQUIRED
    assert connection._context.check_hostname
    assert connection._create_connection is http.connect_telegram


def test_https_handler_passes_original_hostname_and_tls_context():
    handler = http.TelegramHTTPSHandler()
    with patch.object(handler, 'do_open', return_value='response') as open_request:
        assert handler.https_open('request') == 'response'
    open_request.assert_called_once_with(http.TelegramHTTPSConnection, 'request', context=handler._context)
