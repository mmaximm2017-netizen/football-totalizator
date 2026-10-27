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
            counts = []
            # Reject before allocating identity state. Otherwise rotating IPs
            # could grow memory precisely while the global bucket is full.
            for key, limit in zip(keys, (total_limit, personal_limit)):
                start, count = self.buckets.get(key, (now, 0))
                if now - start >= 60:
                    start, count = now, 0
                if count >= limit:
                    return False
                counts.append((start, count))
            for key, (start, count) in zip(keys, counts):
                self.buckets[key] = (start, count + 1)
                self.buckets.move_to_end(key)
            while len(self.buckets) > self.capacity:
                self.buckets.popitem(last=False)
            return True
