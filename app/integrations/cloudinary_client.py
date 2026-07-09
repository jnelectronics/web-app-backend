"""Wraps Cloudinary media storage (product/variant images, banners)."""

import cloudinary
import cloudinary.uploader

from app.core.config import settings

if settings.CLOUDINARY_URL:
    cloudinary.config(cloudinary_url=settings.CLOUDINARY_URL)


def upload_image(file_path: str, *, folder: str) -> dict:
    return cloudinary.uploader.upload(file_path, folder=folder)


def delete_image(public_id: str) -> dict:
    return cloudinary.uploader.destroy(public_id)
