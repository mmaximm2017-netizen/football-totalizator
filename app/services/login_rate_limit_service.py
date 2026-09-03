"""Small in-process guard against repeated failed login attempts."""

import threading
import time
from collections import defaultdict, deque


MAX_FAILED_ATTEMPTS = 5
FAILURE_WINDOW_SECONDS = 10 * 60
BLOCK_SECONDS = 15 * 60

_lock = threading.Lock()
_failures = defaultdict(deque)
_blocked_until = {}


def _key(remote_addr, username):
    return ((remote_addr or "unknown").strip(), (username or "").strip().casefold())


def _prune(key, now):
    cutoff = now - FAILURE_WINDOW_SECONDS
    attempts = _failures.get(key)
    if attempts is not None:
        while attempts and attempts[0] <= cutoff:
            attempts.popleft()
        if not attempts:
            _failures.pop(key, None)

    blocked_until = _blocked_until.get(key)
    if blocked_until is not None and blocked_until <= now:
        _blocked_until.pop(key, None)


def is_login_blocked(remote_addr, username, *, now=None):
    now = time.monotonic() if now is None else now
    key = _key(remote_addr, username)
    with _lock:
        _prune(key, now)
        return _blocked_until.get(key, 0) > now


def record_login_failure(remote_addr, username, *, now=None):
    now = time.monotonic() if now is None else now
    key = _key(remote_addr, username)
    with _lock:
        _prune(key, now)
        attempts = _failures[key]
        attempts.append(now)
        if len(attempts) >= MAX_FAILED_ATTEMPTS:
            _blocked_until[key] = now + BLOCK_SECONDS
            _failures.pop(key, None)
            return True
        return False


def clear_login_failures(remote_addr, username):
    key = _key(remote_addr, username)
    with _lock:
        _failures.pop(key, None)
        _blocked_until.pop(key, None)


def reset_login_guard_for_tests():
    with _lock:
        _failures.clear()
        _blocked_until.clear()
