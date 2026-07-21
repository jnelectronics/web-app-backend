# Run this as its own long-running process, separate from the API:
#   python worker.py
#
# This is what actually PROCESSES jobs sitting on the queue - without it
# running, calls like redis_queue.job_queue.enqueue(...) still succeed (the
# job gets stored in Redis fine), but nothing ever picks it up and runs it.
# In production this would run as its own service, always on, alongside
# the API process - not something the API starts itself.
#
# Uses SimpleWorker, not RQ's regular Worker - the regular Worker forks a
# child process (os.fork()) to run each job in isolation, and os.fork()
# doesn't exist on Windows at all (this crashes immediately when a job
# comes in). SimpleWorker runs jobs in this same process instead - no
# fork, so it works on Windows, at the cost of losing that per-job process
# isolation (a job that crashes hard enough could in theory take the
# worker down with it, not just itself). Fine for this project's jobs.

from rq import SimpleWorker
from rq.timeouts import TimerDeathPenalty

from observability import setup_observability
from redis_queue import redis_conn

if __name__ == "__main__":
    # Own call, separate from main.py's - this is a different PROCESS (see
    # observability.py's docstring), so main.py's setup never runs here.
    # Without this, a job crashing would only ever print to this
    # terminal's stderr - Sentry would never hear about it.
    setup_observability()

    worker = SimpleWorker(["default"], connection=redis_conn)
    # RQ's DEFAULT way of enforcing a job's timeout uses signal.SIGALRM,
    # which only exists on Unix - death_penalty_class is a class attribute,
    # not a constructor argument, so it's overridden here on the instance.
    # TimerDeathPenalty does the same job (kill a job that runs too long)
    # using a background Timer thread instead, which works on Windows too.
    worker.death_penalty_class = TimerDeathPenalty
    worker.work()
