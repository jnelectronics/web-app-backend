# Shared pagination math for every collection endpoint (see schemas.py's
# PaginatedResponse for the response SHAPE this fills in) - one place for
# the page/total_pages arithmetic instead of repeating it in every router
# that lists something.

import math


def build_pagination_meta(skip: int, limit: int, total: int) -> dict:
    # "page" is 1-indexed for a human ("page 1" not "page 0") - skip=0
    # with any limit is page 1, skip=limit is page 2, and so on. This
    # assumes the caller used a CONSISTENT limit across requests (skip
    # jumping by something other than the current limit would produce a
    # page number that doesn't quite line up - an accepted approximation,
    # not something this project's simple skip/limit pagination tries to
    # solve more rigorously).
    page = (skip // limit) + 1 if limit > 0 else 1
    total_pages = math.ceil(total / limit) if limit > 0 else 1
    return {
        "page": page,
        "page_size": limit,
        "total_records": total,
        "total_pages": total_pages,
    }
