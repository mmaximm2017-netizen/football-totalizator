"""Telegram HTTPS with staggered IPv6/IPv4 connects and one connect deadline.

Uses the first TCP connection that succeeds; HTTP requests are sent only once.
The standard HTTPSConnection still performs TLS and hostname verification.
"""
import errno
from http.client import HTTPSConnection
import os
import selectors
import socket
import time
from urllib.request import HTTPSHandler, build_opener

CONNECT_DELAY = 0.25


def _addresses(host, port):
    families = {}
    seen = set()
    for address in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM):
        family, kind, protocol, _, target = address
        key = family, kind, protocol, target
        if key not in seen:
            seen.add(key)
            families.setdefault(family, []).append(address)
    # Interleave address families so a black-holed family cannot block the other.
    result = []
    while any(families.values()):
        for items in families.values():
            if items:
                result.append(items.pop(0))
    return result


def connect_telegram(address, timeout=socket._GLOBAL_DEFAULT_TIMEOUT, source_address=None):
    host, port = address
    if host != 'api.telegram.org':
        # Preserve configured proxy behavior and all unrelated connections.
        return socket.create_connection(address, timeout, source_address)
    if timeout is socket._GLOBAL_DEFAULT_TIMEOUT:
        timeout = socket.getdefaulttimeout()
    candidates = _addresses(host, port)
    if not candidates:
        raise OSError('Telegram DNS returned no addresses')
    start = time.monotonic()
    deadline = None if timeout is None else start + timeout
    next_start = start
    pending = set()
    sockets = set()
    winner = None
    connected = False
    last_error = None
    with selectors.DefaultSelector() as selector:
        try:
            while candidates or pending:
                now = time.monotonic()
                if deadline is not None and now >= deadline:
                    raise TimeoutError('Telegram connect deadline exceeded')
                if candidates and (not pending or now >= next_start):
                    family, kind, protocol, _, target = candidates.pop(0)
                    conn = None
                    try:
                        conn = socket.socket(family, kind, protocol)
                        sockets.add(conn)
                        conn.setblocking(False)
                        if source_address:
                            conn.bind(source_address)
                        status = conn.connect_ex(target)
                        if status in (0, errno.EISCONN):
                            winner = conn
                            break
                        if status not in (errno.EINPROGRESS, errno.EWOULDBLOCK, errno.EALREADY, errno.EINTR):
                            raise OSError(status, os.strerror(status))
                        selector.register(conn, selectors.EVENT_WRITE)
                        pending.add(conn)
                        next_start = now + CONNECT_DELAY
                    except OSError as exc:
                        last_error = exc
                        if conn is not None:
                            conn.close()
                        continue
                if not pending:
                    continue
                now = time.monotonic()
                wait = None if deadline is None else max(0, deadline - now)
                if candidates:
                    until_next = max(0, next_start - now)
                    wait = until_next if wait is None else min(wait, until_next)
                for key, _events in selector.select(wait):
                    conn = key.fileobj
                    selector.unregister(conn)
                    pending.remove(conn)
                    error = conn.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
                    if not error:
                        winner = conn
                        break
                    last_error = OSError(error, os.strerror(error))
                    conn.close()
                if winner is not None:
                    break
            if winner is None:
                raise last_error or OSError('Telegram connection failed')
            winner.settimeout(timeout)
            connected = True
            return winner
        finally:
            for conn in sockets:
                if conn is not winner or not connected:
                    conn.close()


class TelegramHTTPSConnection(HTTPSConnection):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._create_connection = connect_telegram


class TelegramHTTPSHandler(HTTPSHandler):
    def https_open(self, request):
        return self.do_open(TelegramHTTPSConnection, request, context=self._context)


urlopen = build_opener(TelegramHTTPSHandler()).open
