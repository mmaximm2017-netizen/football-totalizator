import logging
import os
import threading
from queue import Full, Queue

import requests

logger = logging.getLogger(__name__)

# PostHog project keys are public ingestion identifiers, not secret credentials.
# Keep an environment override so the project can rotate without a code change.
POSTHOG_PROJECT_KEY = os.getenv(
    "POSTHOG_PROJECT_KEY",
    "phc_wsowVHb2m7SUGPrTPCca8Rp9f5PH8tQckdRgNGVoAn5A",
)
POSTHOG_API_HOST = os.getenv("POSTHOG_API_HOST", "https://us.i.posthog.com").rstrip("/")
POSTHOG_CAPTURE_URL = f"{POSTHOG_API_HOST}/i/v0/e/"
POSTHOG_TIMEOUT_SECONDS = 2.0


def _deliver_posthog_event(event_name, distinct_id, properties):
    if os.getenv("POSTHOG_DISABLED") == "1":
        return
    payload = {
        "api_key": POSTHOG_PROJECT_KEY,
        "event": event_name,
        "distinct_id": distinct_id,
        "properties": {
            "$process_person_profile": bool((properties or {}).get("totish_user_ref")),
            **(properties or {}),
        },
    }

    try:
        response = requests.post(
            POSTHOG_CAPTURE_URL,
            json=payload,
            timeout=POSTHOG_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
    except Exception:
        # Product analytics must never break or delay the user-facing request.
        logger.warning("posthog_delivery_failed event=%s", event_name, exc_info=True)


class AnalyticsDispatcher:
    """One lazy worker and a bounded queue per process; excess analytics is dropped."""

    def __init__(self, deliver, capacity=128):
        self.deliver = deliver
        self.queue = Queue(maxsize=capacity)
        self.lock = threading.Lock()
        self.worker = None

    def submit(self, event):
        with self.lock:
            if self.worker is None:
                self.worker = threading.Thread(target=self._run, name="posthog-capture", daemon=True)
                self.worker.start()
        try:
            self.queue.put_nowait(event)
            return True
        except Full:
            return False

    def _run(self):
        while True:
            event = self.queue.get()
            try:
                self.deliver(*event)
            except Exception:
                logger.warning("posthog_worker_delivery_failed")
            finally:
                self.queue.task_done()


_dispatcher = AnalyticsDispatcher(_deliver_posthog_event)


def enqueue_posthog_event(event_name, distinct_id, properties=None):
    return _dispatcher.submit((event_name, distinct_id, properties or {}))
