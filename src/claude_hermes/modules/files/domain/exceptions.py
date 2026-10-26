"""Failures this module raises. Rendered centrally by the web app's
ApplicationException handler, so the wire shape and the `code` string are the
contract — not the place the exception is raised from."""
from __future__ import annotations

from claude_hermes.core.exceptions import ApplicationException

__all__ = ["FileTooLarge", "UnknownAttachment"]


class FileTooLarge(ApplicationException):
    """An upload above the configured ceiling. 413 + "too_large" is what the web
    UI already branches on; both are frozen (spec req. 36)."""

    status, code = 413, "too_large"

    def __init__(self, message: str = ""):
        super().__init__(None, None, message)


class UnknownAttachment(ApplicationException):
    """A turn named a file id that is not in the lane it is being sent to.

    400 rather than 404 on purpose: the id is well-formed, the *request* is
    wrong — and answering "not found" would confirm the id exists elsewhere.
    """

    status, code = 400, "bad_request"

    def __init__(self, message: str = ""):
        super().__init__(None, None, message)
