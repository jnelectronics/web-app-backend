# A simple in-memory failed-login lockout, scoped to the two login
# endpoints - not a general-purpose API rate limiter (the docs explicitly
# defer that past the pilot phase). Just enough to stop a brute-force
# password-guessing attack against one known email/account.
#
# In-memory (not DB-backed) - fine for now since this project runs as a
# single instance (Render's free Web Service plan doesn't offer more than
# one anyway). A process restart resets every lockout; acceptable for a
# pilot. If this project ever runs as multiple instances, this in-memory
# dict wouldn't be shared between them and would need to move to
# something shared (Redis, or the database) - revisit then, not now.

import time

MAX_FAILED_ATTEMPTS = 5
LOCKOUT_SECONDS = 15 * 60  # 15 minutes

# key: "customer:<email>" or "staff:<email>" (prefixed so the two separate
# login systems can never collide on the same key by coincidence) ->
# (consecutive failure count, timestamp of the FIRST failure in this streak)
#
# Known, accepted limitation: this dict has no size cap or eviction beyond
# a successful login clearing its own entry. An attacker submitting many
# DISTINCT nonexistent emails could grow it indefinitely (a memory-
# exhaustion angle, not an account-lockout one). Not addressed here - the
# realistic threat this module defends against is repeated guessing
# against ONE known real account, not a distributed attack; a proper fix
# for the broader case is exactly the general API rate limiting the docs
# already defer past the pilot phase.
_failed_attempts: dict[str, tuple[int, float]] = {}


class RateLimitedError(Exception):
    def __init__(self, retry_after_seconds: int):
        self.retry_after_seconds = retry_after_seconds
        super().__init__(f"Too many failed login attempts. Try again in {retry_after_seconds} seconds.")


def check_not_locked_out(key: str) -> None:
    # Called BEFORE checking the password - raises if this identifier has
    # already used up its attempts and the lockout window hasn't passed yet.
    entry = _failed_attempts.get(key)
    if entry is None:
        return

    count, first_failure = entry
    if count < MAX_FAILED_ATTEMPTS:
        return

    elapsed = time.monotonic() - first_failure
    if elapsed >= LOCKOUT_SECONDS:
        # The lockout window has passed - clear it and let this attempt
        # through (it'll count as a fresh streak if it fails again).
        del _failed_attempts[key]
        return

    raise RateLimitedError(retry_after_seconds=int(LOCKOUT_SECONDS - elapsed))


def record_failed_attempt(key: str) -> None:
    count, first_failure = _failed_attempts.get(key, (0, time.monotonic()))
    _failed_attempts[key] = (count + 1, first_failure)


def clear_failed_attempts(key: str) -> None:
    # Called on a SUCCESSFUL login - a real login shouldn't stay one
    # mistyped password away from a lockout forever.
    _failed_attempts.pop(key, None)
