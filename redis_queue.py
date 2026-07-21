# This file's job: give the rest of the app a ready-to-use way to queue
# background work, the same way database.py gives it a ready-to-use way to
# talk to Postgres. RQ (Redis Queue) is the library - it stores queued jobs
# in Redis, and a separate long-running process (worker.py) pulls them off
# and actually runs them.
#
# Named redis_queue.py, not queue.py - Python already has a built-in
# module called "queue" in its standard library, and a file at the top of
# this project named queue.py would shadow it for every import anywhere in
# the codebase (including inside libraries this project depends on).

import os

from dotenv import load_dotenv
from redis import Redis
from rq import Queue

load_dotenv()

REDIS_URL = os.getenv("REDIS_URL")

# One shared connection object - the API process uses it to PUSH jobs on
# (via job_queue.enqueue(...)), and worker.py uses the exact same kind of
# connection to PULL jobs off. Same Redis, two different roles.
redis_conn = Redis.from_url(REDIS_URL)

# "default" is just a queue name - RQ supports several named queues (e.g.
# splitting urgent work from low-priority work), but this project only
# needs the one.
job_queue = Queue("default", connection=redis_conn)
