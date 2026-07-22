# Covers the async job pattern actually wired into the app right now:
# /auth/password/forgot hands the "send an email" job to FastAPI's
# BackgroundTasks, which runs it in this same process after the response
# is sent - not the RQ+Redis+worker.py setup (still in the codebase, kept
# for a possible future switch, but not called from routers/auth.py
# anymore - see CLAUDE.md's "Background workers" section for why).

import uuid

from conftest import unwrap
from models import Customer, RefreshToken


def test_password_forgot_runs_the_email_job(client, db, caplog):
    email = f"jobtest-{uuid.uuid4().hex[:8]}@example.com"
    register_response = client.post(
        "/api/v1/auth/register",
        json={"full_name": "Job Test", "email": email, "password": "Password123"},
    )
    customer_id = uuid.UUID(unwrap(register_response)["customer"]["id"])

    try:
        response = client.post("/api/v1/auth/password/forgot", json={"email": email})
        assert response.status_code == 200

        # TestClient runs the ASGI app through to completion - including
        # any BackgroundTasks - before client.post() returns, so the job
        # has already run by this point (unlike a real deployed server,
        # where it runs after the response reaches the client but the
        # process is still the one that handles it either way).
        assert f"Password reset link for {email}" in caplog.text
    finally:
        db.query(RefreshToken).filter(RefreshToken.owner_id == customer_id).delete()
        db.commit()
        db.query(Customer).filter(Customer.id == customer_id).delete()
        db.commit()
