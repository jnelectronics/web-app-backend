# Covers the actual async job pattern: /auth/password/forgot enqueues a
# real RQ job onto real Redis, and a worker (simulated here via RQ's
# SimpleWorker in "burst" mode - process whatever's queued, then stop,
# rather than running forever like worker.py does) picks it up and runs it.

import uuid

from rq import SimpleWorker
from rq.timeouts import TimerDeathPenalty

from conftest import unwrap
from models import Customer, RefreshToken
from redis_queue import job_queue, redis_conn


def test_password_forgot_enqueues_and_processes_a_real_job(client, db, caplog):
    # Clean slate - a previous failed run could leave jobs sitting in the
    # queue and throw off the count assertion below.
    job_queue.empty()

    email = f"jobtest-{uuid.uuid4().hex[:8]}@example.com"
    register_response = client.post(
        "/api/v1/auth/register",
        json={"full_name": "Job Test", "email": email, "password": "Password123"},
    )
    customer_id = uuid.UUID(unwrap(register_response)["customer"]["id"])

    try:
        assert job_queue.count == 0

        response = client.post("/api/v1/auth/password/forgot", json={"email": email})
        assert response.status_code == 200

        # The job is sitting in Redis now, queued but not yet run - this is
        # the part a real API request never waits for.
        assert job_queue.count == 1

        # SimpleWorker(burst=True) processes everything currently queued
        # then stops, standing in for worker.py's real `while True` loop
        # (python worker.py) so the test doesn't need a second process
        # running in the background.
        worker = SimpleWorker(["default"], connection=redis_conn)
        # See worker.py's comment - RQ's default death_penalty_class relies
        # on signal.SIGALRM, which doesn't exist on Windows.
        worker.death_penalty_class = TimerDeathPenalty
        worker.work(burst=True)

        assert job_queue.count == 0
        # jobs.py's send_password_reset_email logs via logging.info - this
        # confirms the job actually RAN (on the "worker"), not just that it
        # was queued. caplog (not capsys) is the correct pytest fixture for
        # this: our logging.basicConfig() runs once at import time and
        # binds its handler to the stderr stream that existed then, so a
        # later test's capsys stdout/stderr substitution never sees it -
        # caplog instead attaches its own handler directly to the logging
        # module, independent of stream redirection.
        assert f"Password reset link for {email}" in caplog.text
    finally:
        job_queue.empty()
        db.query(RefreshToken).filter(RefreshToken.owner_id == customer_id).delete()
        db.commit()
        db.query(Customer).filter(Customer.id == customer_id).delete()
        db.commit()
