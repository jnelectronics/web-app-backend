# Shared pytest fixtures. TestClient runs the real FastAPI app in-process
# (no uvicorn needed) - requests to it go through the actual routes,
# dependencies, and the real Neon database, same as testing via /docs would.

import pytest
from fastapi.testclient import TestClient

from database import SessionLocal
from main import app


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def db():
    # A direct DB session, separate from the one each API request opens for
    # itself - lets a test set up/tear down rows the API has no endpoint
    # for (e.g. seeding inventory to an exact quantity for a scenario).
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def unwrap(response):
    # Every successful response now comes back wrapped in the standard
    # envelope ({"success", "message", "data"} - see envelope.py), so tests
    # need to reach into "data" to get the actual resource instead of
    # reading the top level directly.
    return response.json()["data"]
