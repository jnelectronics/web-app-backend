# Wraps every successful JSON response in the standard envelope the API
# spec requires (§2.3): {"success": true, "message": ..., "data": ...}.
# A custom APIRoute class - not a per-endpoint change - means any router
# built with route_class=EnvelopeRoute gets this automatically, without
# touching each route's return statement or response_model.
#
# Simplification: the docs' examples show a custom message per endpoint
# ("Registration successful."). Reproducing that exactly would mean
# touching every single route, so this uses one generic message per HTTP
# method instead - same envelope SHAPE, generic wording.
#
# The matching error envelope ({"success": false, "message", "error_code"})
# is handled separately, in main.py's exception handlers - errors already
# skip this wrapper (see the status_code check below), since FastAPI builds
# error responses via exception handlers, not by returning through here.

import json
from collections.abc import Callable

from fastapi import Request, Response
from fastapi.routing import APIRoute

_METHOD_MESSAGES = {
    "GET": "Request successful.",
    "POST": "Created successfully.",
    "PUT": "Updated successfully.",
    "PATCH": "Updated successfully.",
    "DELETE": "Deleted successfully.",
}


class EnvelopeRoute(APIRoute):
    def get_route_handler(self) -> Callable:
        original_handler = super().get_route_handler()

        async def envelope_handler(request: Request) -> Response:
            response = await original_handler(request)

            # Only successful JSON responses WITH a body get wrapped - a
            # 204 (soft-delete endpoints) has no body to put inside "data",
            # so it's left exactly as-is.
            if response.status_code < 400 and response.media_type == "application/json" and response.body:
                data = json.loads(response.body)
                wrapped = {
                    "success": True,
                    "message": _METHOD_MESSAGES.get(request.method, "Request successful."),
                    "data": data,
                }
                body = json.dumps(wrapped).encode("utf-8")
                response.body = body
                response.headers["content-length"] = str(len(body))

            return response

        return envelope_handler
