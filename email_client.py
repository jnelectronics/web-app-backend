# Wraps sending an email through Gmail's SMTP server - the real email
# provider now backing jobs.py's send_password_reset_email. Same idea as
# pesapal_client.py wrapping the PesaPal API: every Gmail-specific detail
# (server address, port, login, message format) lives in this one file, so
# jobs.py only ever calls send_email(to, subject, body) with plain strings -
# it doesn't need to know anything about SMTP itself.
#
# SMTP (Simple Mail Transfer Protocol) is the protocol email servers use to
# receive a message and pass it along toward its destination. smtplib is
# Python's built-in module for speaking SMTP - no extra dependency needed,
# unlike pesapal_client.py which needed httpx for a JSON API.

import os
import smtplib
from email.mime.text import MIMEText

from dotenv import load_dotenv

load_dotenv()

GMAIL_ADDRESS = os.getenv("GMAIL_ADDRESS")
# This MUST be a Gmail "App Password" (a 16-character code Google generates
# specifically for one app, from myaccount.google.com/apppasswords) - NOT
# the account's real login password. Gmail stopped accepting a script
# signing in with the actual account password a while back, so an App
# Password is the only credential that works here.
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")


def is_configured() -> bool:
    # Same "degrade gracefully instead of crashing" idea as
    # pesapal_client.is_configured() - lets a caller check up front whether
    # real credentials exist before attempting to send anything, rather
    # than finding out deep inside a failed SMTP login.
    return bool(GMAIL_ADDRESS) and bool(GMAIL_APP_PASSWORD)


class EmailError(Exception):
    # Raised when the SMTP send itself fails (bad login, Gmail unreachable,
    # etc.) - lets callers (jobs.py) catch one specific exception type
    # instead of needing to know every way smtplib itself can fail.
    pass


def send_email(to_email: str, subject: str, body: str) -> None:
    # MIMEText builds a properly-formatted email message body - "MIME" is
    # the standard that lets an email have a subject, sender, recipient,
    # and body all packed into one block of text the way email servers
    # expect. This project only ever sends plain text, so MIMEText is all
    # that's needed (no attachments, no HTML formatting).
    message = MIMEText(body)
    message["Subject"] = subject
    message["From"] = GMAIL_ADDRESS
    message["To"] = to_email

    try:
        # SMTP_SSL immediately encrypts the connection (port 465 is
        # Gmail's dedicated encrypted-from-the-start port) - the login and
        # the email content both travel over an already-encrypted
        # connection, never in plain text. The `with` block makes sure the
        # connection to Gmail's server is closed automatically once
        # sending is done, even if something goes wrong partway through.
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
            server.sendmail(GMAIL_ADDRESS, to_email, message.as_string())
    except smtplib.SMTPException as exc:
        raise EmailError(f"Failed to send email to {to_email}: {exc}") from exc
