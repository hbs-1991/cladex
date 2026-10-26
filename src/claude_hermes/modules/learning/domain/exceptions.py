"""Refused reads and writes of the skills library.

Both are `ApplicationException` subclasses in Wave 2, so the central handler in
`web/app.py` renders them without a per-router status table. `SkillError` keeps
its `(code, message)` constructor and its `.code`, because that code is the wire
contract the frontend switches on; the status each code maps to used to live in
`web/skills.py::STATUS` and lives here now — one place, both channels.

`StaleEdit` is the one that cannot render centrally: its body carries the current
text and etag as well, so the router builds that response itself.
"""
from __future__ import annotations

from claude_hermes.core.exceptions import ApplicationException

# `SkillError.code` -> HTTP status. The codes are the library's, the statuses are
# the API's, and this table is the only place the two meet.
_STATUS = {
    "bad_name": 400, "bad_frontmatter": 400, "too_large": 413,
    "name_taken": 409, "not_found": 404,
}


class SkillError(ApplicationException):
    """A refused read or write, carrying the code the web layer maps to a status:
    bad_name | name_taken | too_large | bad_frontmatter | not_found."""

    def __init__(self, code: str, message: str):
        super().__init__(_STATUS.get(code, 400), code, message)


class StaleEdit(ApplicationException):
    """The file changed under an editor holding an older etag — the background
    review pass writes these same files. Carries the current text so the caller
    can show it rather than making the owner go and fetch it."""

    status, code = 409, "stale_edit"

    def __init__(self, content: str, etag: str):
        super().__init__(None, None, "SKILL.md changed since it was read")
        self.content = content
        self.etag = etag
