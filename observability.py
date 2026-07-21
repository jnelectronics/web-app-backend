# Logging + Sentry setup, shared between the two separate PROCESSES this
# project runs: the API (main.py, `uvicorn main:app`) and the background
# worker (worker.py, `python worker.py`). They're different Python
# processes entirely - worker.py never imports main.py - so configuring
# this only in main.py would leave the worker silently unmonitored.
# Both call setup_observability() once, at startup, instead.

import logging
import os

import sentry_sdk
from dotenv import load_dotenv

load_dotenv()


def setup_observability() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    # Safe to call even with no DSN configured - the SDK just becomes a
    # no-op instead of crashing. Once SENTRY_DSN is set in .env, unhandled
    # exceptions (real bugs) start showing up in Sentry automatically, with
    # no other code changes needed.
    sentry_sdk.init(
        dsn=os.getenv("SENTRY_DSN", ""),
        environment=os.getenv("ENVIRONMENT", "development"),
        # Fraction of requests/jobs to trace for performance monitoring,
        # not error capture (errors are always captured regardless of this
        # setting) - kept low since the free tier has a monthly quota and
        # this project doesn't need full tracing to be useful for a pilot.
        traces_sample_rate=0.1,
        # Off by default - don't send request/user data (emails, IPs,
        # etc.) to a third party without a deliberate decision to turn it
        # on later. Sentry's own setup snippet defaults this to True;
        # overridden here on purpose given this project handles real
        # customer PII (emails, phone numbers, delivery addresses).
        send_default_pii=False,
        # Lets Sentry also collect structured logs (via Python's standard
        # `logging` module - see logging.basicConfig above), not just
        # unhandled exceptions - one dashboard for both instead of running
        # a separate logging service for this pilot.
        enable_logs=True,
    )
