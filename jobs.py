# Job functions - what actually runs, regardless of how it gets called.
#
# Currently called via FastAPI's BackgroundTasks (routers/auth.py's
# forgot_password: background_tasks.add_task(send_password_reset_email, ...))
# - runs in the same process as the API, after the response is sent, no
# separate worker needed. Chosen for the pilot launch since Render's
# Background Worker service type has no free tier - see CLAUDE.md's
# "Background workers" section for the full trade-off.
#
# redis_queue.py/worker.py still exist and still work (RQ + a real
# separate worker process) if this project ever needs a job to survive a
# process restart or wants genuine cross-process queueing - nothing about
# this function itself would need to change to switch back; only the
# call site (job_queue.enqueue(...) instead of background_tasks.add_task(...)).

import logging

logger = logging.getLogger(__name__)


def send_password_reset_email(email: str, reset_token: str) -> None:
    # No real email provider exists in this project (see
    # routers/auth.py's forgot_password - same simplification already
    # used there, and the same one routers/payments.py uses for the
    # missing payment gateway). This log line stands in for "the email was
    # sent". What's real here is the ASYNC JOB PATTERN itself: this
    # function runs AFTER the API request that triggered it has already
    # gotten its response - not the email delivery.
    #
    # logger.info (not print) - this is what makes the job's activity show
    # up in Sentry too (see observability.py), not just this process's
    # console. If this raised an exception instead, Sentry would catch it
    # automatically - no try/except needed here just to report a crash.
    logger.info("Password reset link for %s: token=%s", email, reset_token)
