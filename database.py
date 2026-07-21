# This file's job: set up the connection to our Postgres database (hosted on Neon)
# so the rest of the app has a ready-to-use way to talk to it.

import os
import uuid
from datetime import datetime

# python-dotenv reads the .env file and loads its values as environment variables,
# so os.getenv() below can actually see them.
from dotenv import load_dotenv

# create_engine builds the connection pool SQLAlchemy uses to talk to Postgres.
# func lets us call SQL functions like now() from Python code.
from sqlalchemy import DateTime, create_engine, func

# Postgres's real UUID column type (stores it efficiently, not as plain text).
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

# DeclarativeBase is the parent class every table/model in our app will inherit from.
# sessionmaker builds "Session" objects — the object we actually use to run queries.
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

# Actually load the .env file's contents into the environment now.
load_dotenv()

# Read the connection string we saved in .env (never hardcode secrets directly in code).
DATABASE_URL = os.getenv("DATABASE_URL")

# The engine doesn't connect immediately — it's configured and ready,
# and opens real connections only when something asks it to.
engine = create_engine(DATABASE_URL)

# SessionLocal is a factory: calling SessionLocal() gives us a new Session,
# which is what we'll use to add/query/update rows in the database.
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


# Every SQLAlchemy model (table) in this project must inherit from this Base class.
# It's how SQLAlchemy (and later, Alembic) knows which Python classes represent
# real database tables.
class Base(DeclarativeBase):
    pass


# A "mixin" - a class that only exists to be inherited alongside Base,
# adding these columns to whatever model uses it. Per the DB Design Document,
# every table's primary key is a UUID named "id" (not an auto-incrementing
# integer) - defining that rule once here means every model gets it free
# just by inheriting this class, instead of retyping it per table.
class UUIDPrimaryKeyMixin:
    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )


# Another mixin: every table also needs created_at/updated_at per the docs.
# server_default=func.now() means Postgres itself fills this in when a row
# is inserted (rather than relying on Python to do it), and onupdate=func.now()
# makes Postgres refresh updated_at automatically whenever the row changes.
class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


# A FastAPI "dependency": a function whose job is to prepare something a
# route needs. Splitting it with `yield` means: everything before yield runs
# BEFORE the route, everything after yield runs AFTER - even if the route
# raises an error. That's what guarantees every database session gets
# closed properly instead of leaking connections.
# Lives here (not in a router file) because every domain (products,
# categories, ...) needs the exact same one.
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
