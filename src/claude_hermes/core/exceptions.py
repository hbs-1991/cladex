"""The application error hierarchy. Services raise these; the HTTP layer renders
them as {"error": {"code", "message"}} with `.status`. Nothing here imports a web
framework — the mapping to a response lives in the presentation layer."""
from __future__ import annotations


class ApplicationException(Exception):
    status: int = 500
    code: str = "internal"

    def __init__(self, status: int | None = None, code: str | None = None,
                 message: str = ""):
        if status is not None:
            self.status = status
        if code is not None:
            self.code = code
        self.message = message
        super().__init__(message)


class _Typed(ApplicationException):
    def __init__(self, message: str = "", *, code: str | None = None):
        super().__init__(None, code, message)


class NotFound(_Typed):
    status, code = 404, "not_found"


class Forbidden(_Typed):
    status, code = 403, "forbidden"


class Unauthorized(_Typed):
    status, code = 401, "unauthorized"


class Conflict(_Typed):
    status, code = 409, "conflict"


class InvalidInput(_Typed):
    status, code = 422, "invalid"
