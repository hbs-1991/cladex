"""The skills API's wire shapes. Frozen: the frontend switches on these keys.

`SkillWire` itself is `domain.models.SkillInfo.to_wire()`; what lives here is the
two derived shapes the router answers with — the row inside a detail, and the one
error body that carries more than `{code, message}` — plus the two request bodies.

Both request models are deliberately lenient: every field is `Any` with a default,
and the handler keeps its own `str(... or "")`. `SkillLibraryService` owns the
validation and answers with the `SkillError` codes the frontend switches on
(`bad_name`, `bad_frontmatter`, `too_large`, `name_taken`), so a Pydantic 422 in
front of it would replace a status and a code the tests pin.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class SkillCreate(BaseModel):
    """`POST /api/skills`. `{name, content}`."""

    model_config = ConfigDict(extra="ignore")

    name: Any = None
    content: Any = None


class SkillSave(BaseModel):
    """`PUT /api/skills/{name}`. `{content, etag}` — the etag is the precondition
    that turns a lost update into a visible 409 rather than a silent overwrite."""

    model_config = ConfigDict(extra="ignore")

    content: Any = None
    etag: Any = None


def row_of(detail: dict) -> dict:
    """The listing row inside a detail. A detail is a row plus the text, and the
    mutations answer with the row: pinning a skill should not ship its body back."""
    return {k: v for k, v in detail.items() if k not in ("content", "etag", "tree")}


def stale_edit_body(content: str, etag: str) -> dict:
    """The 409 a refused save answers with.

    The current text travels with the refusal — the owner is mid-edit, and making
    them go and fetch what changed is how a conflict turns into a copy-paste that
    loses one side anyway. This is why `StaleEdit` cannot render through the central
    handler, which only knows `{code, message}`.
    """
    return {"error": {
        "code": "stale_edit",
        "message": "the agent changed this skill while you were editing it",
        "content": content, "etag": etag}}
