# Wraps the Cloudinary API - the real image host now backing
# routers/products.py's image endpoints. Same idea as pesapal_client.py
# wrapping PesaPal and email_client.py wrapping Resend: every
# Cloudinary-specific detail (auth, upload call, response shape) lives in
# this one file, so the router only ever deals with plain Python values
# (bytes in, a URL + an ID out) - it never touches the `cloudinary` package
# directly.
#
# Unlike httpx (used for PesaPal and Resend), Cloudinary publishes its own
# official Python SDK - it handles authentication and turns each API call
# into a plain function call, so there's no need to hand-build HTTP
# requests the way pesapal_client.py/email_client.py do.

import os

import cloudinary
import cloudinary.uploader
from dotenv import load_dotenv

load_dotenv()

CLOUDINARY_CLOUD_NAME = os.getenv("CLOUDINARY_CLOUD_NAME")
CLOUDINARY_API_KEY = os.getenv("CLOUDINARY_API_KEY")
CLOUDINARY_API_SECRET = os.getenv("CLOUDINARY_API_SECRET")

# cloudinary.config() sets these once, at import time, in a module-level
# spot the `cloudinary` package reads from internally on every upload/
# destroy call - similar in spirit to how PESAPAL_CONSUMER_KEY gets read
# fresh inside every pesapal_client.py function, just handled for us by
# the SDK instead of us passing credentials into every call by hand.
cloudinary.config(
    cloud_name=CLOUDINARY_CLOUD_NAME,
    api_key=CLOUDINARY_API_KEY,
    api_secret=CLOUDINARY_API_SECRET,
    secure=True,  # secure=True means every URL Cloudinary hands back is https://, not http://.
)


def is_configured() -> bool:
    # Same "degrade gracefully instead of crashing" idea as
    # pesapal_client.is_configured()/email_client.is_configured() - lets a
    # caller check up front whether real credentials exist before
    # attempting an upload, rather than finding out deep inside a failed
    # Cloudinary call.
    return bool(CLOUDINARY_CLOUD_NAME) and bool(CLOUDINARY_API_KEY) and bool(CLOUDINARY_API_SECRET)


class CloudinaryError(Exception):
    # Raised when the upload/destroy call itself fails (bad credentials,
    # Cloudinary unreachable, etc.) - lets callers (routers/products.py)
    # catch one specific exception type instead of needing to know every
    # way the `cloudinary` package itself can fail.
    pass


def upload_image(file_bytes: bytes, filename: str) -> dict:
    # Returns {"secure_url": ..., "public_id": ...}. secure_url is what we
    # store as ProductImage.image_url; public_id is what we store as
    # ProductImage.cloudinary_public_id, and is also what delete_image()
    # below needs to remove the same file later.
    try:
        result = cloudinary.uploader.upload(
            file_bytes,
            filename=filename,
            # Groups every upload from this project under one folder in
            # the Cloudinary dashboard, instead of dumping everything in
            # the account's root - purely organizational, doesn't affect
            # the URL Cloudinary hands back.
            folder="jn_electronics/products",
        )
    except cloudinary.exceptions.Error as exc:
        raise CloudinaryError(f"Failed to upload image to Cloudinary: {exc}") from exc

    return {"secure_url": result["secure_url"], "public_id": result["public_id"]}


def delete_image(public_id: str) -> None:
    # Called when a ProductImage row is deleted/replaced, so a removed
    # image doesn't keep sitting in Cloudinary storage forever with
    # nothing in our own DB pointing at it anymore.
    try:
        cloudinary.uploader.destroy(public_id)
    except cloudinary.exceptions.Error as exc:
        raise CloudinaryError(f"Failed to delete image {public_id} from Cloudinary: {exc}") from exc
