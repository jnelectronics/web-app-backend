"""Generic file storage helper, separate from Cloudinary (e.g. for exports/reports)."""


def build_public_url(path: str) -> str:
    # TODO: return a signed/public URL for the configured storage backend.
    raise NotImplementedError("Wire up the storage backend's URL builder here.")
