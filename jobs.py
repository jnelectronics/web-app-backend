# Functions that run on the WORKER process (worker.py), not the API
# process. A route calls redis_queue.job_queue.enqueue(send_password_reset_email, ...)
# instead of calling this function directly - that hands the job to Redis
# and returns immediately, rather than making the API request wait for
# whatever this function does.

import logging

logger = logging.getLogger(__name__)


def send_password_reset_email(email: str, reset_token: str) -> None:
    # No real email provider exists in this project (see
    # routers/auth.py's forgot_password - same simplification already
    # used there, and the same one routers/payments.py uses for the
    # missing payment gateway). This log line stands in for "the email was
    # sent". What's real here is the ASYNC JOB PATTERN itself: this
    # function runs on a separate worker process, sometime after the API
    # request that queued it has already returned to its caller - not the
    # email delivery.
    #
    # logger.info (not print) - this is what makes the job's activity show
    # up in Sentry too (see observability.py), not just this process's
    # console. If this raised an exception instead, worker.py's
    # setup_observability() call means Sentry would catch it automatically
    # - no try/except needed here just to report a crash.
    logger.info("Password reset link for %s: token=%s", email, reset_token)
