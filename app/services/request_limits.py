"""Bounded in-process admission control; never wakes the database for public traffic.

Limits apply per Gunicorn worker. The global bucket also caps rotating identities.
Use the nginx configuration in deploy/nginx for a shared edge limit.
"""
import hashlib
import threading
import time
from collections import OrderedDict


class RequestLimiter:
    def __init__(self, capacity=4096):
        self.capacity = capacity
        self.buckets = OrderedDict()
        self.lock = threading.Lock()

    def allow(self, scope, identity, personal_limit, total_limit, *, now=None):
        now = time.monotonic() if now is None else now
        identity = hashlib.sha256(str(identity).encode()).hexdigest()
        keys = [(scope, '*'), (scope, identity)]
        with self.lock:
            for key in keys:
                start, count = self.buckets.get(key, (now, 0))
                if now - start >= 60:
                    start, count = now, 0
                self.buckets[key] = (start, count)
                self.buckets.move_to_end(key)
            if any(self.buckets[key][1] >= limit for key, limit in zip(keys, (total_limit, personal_limit))):
                return False
            for key in keys:
                start, count = self.buckets[key]
                self.buckets[key] = (start, count + 1)
            while len(self.buckets) > self.capacity:
                self.buckets.popitem(last=False)
            return True
