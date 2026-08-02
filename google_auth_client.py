# Wraps verifying a Google "Sign in with Google" ID token - the piece that
# backs routers/auth.py's POST /auth/google. Same wrapper-module idea as
# cloudinary_client.py/pesapal_client.py/email_client.py: every
# Google-specific detail lives here, so the router only ever deals with a
# plain dict of {email, email_verified, name} - it never touches the
# `google.auth` package directly.
#
# What an "ID token" actually is: after a customer signs into Google on
# the FRONTEND (Nyson's app, using Google's own JavaScript library), Google
# hands the frontend a signed document - a JWT, the same kind of token
# format this project already issues itself in security.py, just signed by
# Google instead of by us. The frontend forwards that token to our
# POST /auth/google endpoint. This file is what checks the signature is
# genuinely Google's (not forged) and that the token was issued FOR this
# app specifically (the "audience" check, GOOGLE_CLIENT_ID below) - not
# some other app's token being replayed here.

import os

from dotenv import load_dotenv
from google.auth.exceptions import GoogleAuthError
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token

load_dotenv()

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")

# Reused across calls rather than built fresh each time - it's just a thin
# wrapper around the `requests` library used to fetch Google's public
# signing keys, no per-call state that would make sharing it unsafe.
_google_request = google_requests.Request()


def is_configured() -> bool:
    # Same "degrade gracefully instead of crashing" idea as the other
    # wrapper modules' is_configured() - lets routers/auth.py check up
    # front whether Google sign-in is set up at all.
    return bool(GOOGLE_CLIENT_ID)


class GoogleTokenError(Exception):
    # Raised for ANY reason a token fails verification - forged signature,
    # expired, issued for a different app, malformed, etc. The router
    # doesn't need to distinguish why; an invalid token is an invalid
    # token, always a 401 either way.
    pass


def verify_id_token(id_token: str) -> dict:
    # Returns {"email": ..., "email_verified": ..., "name": ...}.
    # verify_oauth2_token does THREE checks in one call: the signature is
    # really Google's (fetching Google's current public keys itself, via
    # _google_request above), the token hasn't expired, and - critically -
    # that GOOGLE_CLIENT_ID matches the token's "aud" (audience) claim, so
    # a token meant for some completely different app can't be replayed
    # against this one.
    try:
        id_info = google_id_token.verify_oauth2_token(id_token, _google_request, GOOGLE_CLIENT_ID)
    except (ValueError, GoogleAuthError) as exc:
        raise GoogleTokenError(f"Invalid Google ID token: {exc}") from exc

    return {
        "email": id_info.get("email"),
        # Google only sets this true once IT has confirmed the person
        # really owns this email address - routers/auth.py relies on this
        # specifically before auto-linking to an existing password account.
        "email_verified": id_info.get("email_verified", False),
        "name": id_info.get("name"),
    }
