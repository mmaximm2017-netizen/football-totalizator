import logging
import os
import threading

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
    payload = {
        "api_key": POSTHOG_PROJECT_KEY,
        "event": event_name,
        "distinct_id": distinct_id,
        "properties": {
            "$process_person_profile": False,
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


def enqueue_posthog_event(event_name, distinct_id, properties=None):
    worker = threading.Thread(
        target=_deliver_posthog_event,
        args=(event_name, distinct_id, properties or {}),
        name="posthog-capture",
        daemon=True,
    )
    worker.start()
