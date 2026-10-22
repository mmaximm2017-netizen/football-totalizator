"""Bounded durable admin inbox; one data worker, local menus stay responsive."""
from collections import deque
import json
import os
from pathlib import Path
import tempfile
import threading
import time


class UpdateDispatcher:
    def __init__(self, directory, handle, is_local, acknowledge, capacity=32):
        self.directory = Path(directory)
        self.handle = handle
        self.is_local = is_local
        self.acknowledge = acknowledge
        self.capacity = capacity
        self.pending = deque()
        self.latest = {}
        self.known = set()
        self.condition = threading.Condition()
        self.stopping = False
        self.thread = threading.Thread(target=self._run, name="telegram-data-worker", daemon=True)

    def start(self):
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        for path in sorted(self.directory.glob('*.json'), key=lambda p: int(p.stem)):
            self.submit(json.loads(path.read_text()), persist=False)
        self.thread.start()

    def _path(self, update):
        return self.directory / f"{int(update['update_id'])}.json"

    def _persist(self, update):
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        with tempfile.NamedTemporaryFile('w', dir=self.directory, delete=False) as handle:
            json.dump(update, handle)
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        os.chmod(temporary, 0o600)
        os.replace(temporary, self._path(update))

    def submit(self, update, persist=True):
        uid = update['update_id']
        callback = update.get('callback_query') or {}
        source = callback.get('message') or update.get('message') or {}
        chat = (source.get('chat') or {}).get('id')
        local = self.is_local(update)
        with self.condition:
            if uid in self.known:
                return
            # Replace only consecutive navigation clicks; questions retain order.
            replace = self.pending and callback and self.pending[-1].get('callback_query')
            if len(self.pending) >= self.capacity and not replace:
                raise RuntimeError('telegram_admin_queue_full')
            if persist:
                self._persist(update)
            self.known.add(uid)
            self.latest[chat] = uid
            update['_received'] = time.monotonic()
            if replace:
                previous = self.pending.pop()
                self._path(previous).unlink(missing_ok=True)
                self.known.discard(previous['update_id'])
        if callback.get('id'):
            self.acknowledge(callback['id'])
            update['_acknowledged'] = True
        if local:
            self._process(update, chat)
        else:
            with self.condition:
                self.pending.append(update)
                self.condition.notify()

    def _process(self, update, chat):
        uid = update['update_id']
        update['_is_current'] = lambda: self.latest.get(chat) == uid
        try:
            if not update.get("callback_query") or update["_is_current"]():
                self.handle(update)
        finally:
            # An exception leaves the file for replay after restart.
            update.pop('_is_current', None)
        self._path(update).unlink(missing_ok=True)
        with self.condition:
            self.known.discard(uid)

    def _run(self):
        while True:
            with self.condition:
                self.condition.wait_for(lambda: self.pending or self.stopping)
                if self.stopping:
                    return
                update = self.pending.popleft()
            source = (update.get('callback_query') or {}).get('message') or update.get('message') or {}
            try:
                self._process(update, (source.get('chat') or {}).get('id'))
            except Exception as exc:  # Keep durable inbox entry; never print SQL or request text.
                import logging
                logging.getLogger(__name__).error('telegram_admin_worker_failed type=%s', type(exc).__name__)

    def close(self):
        with self.condition:
            self.stopping = True
            self.condition.notify_all()
        if self.thread.is_alive():
            self.thread.join(timeout=20)
        if self.thread.is_alive():
            raise RuntimeError('telegram_admin_worker_shutdown_timeout')
