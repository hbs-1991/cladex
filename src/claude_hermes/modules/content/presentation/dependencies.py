"""What the content router asks for, and where it comes from.

One provider, because the router has one collaborator. Built once at module scope
out of `core.di.provide`: FastAPI keys `dependency_overrides` by the callable
*object*, so a provider rebuilt per request could not be overridden.

Bundle attribute read by this module: **`content`** (`ContentService`). The owner
gate is `core.auth.require_owner`; the shell mounts the router only when the
attribute is not `None` (`content_enabled`).
"""
from __future__ import annotations

from starlette.requests import Request

from claude_hermes.core.di import provide

from ..application.service import ContentService

__all__ = ["get_content"]

_content = provide("content")


def get_content(request: Request) -> ContentService:
    """`deps.content` — the same service the `content_plan` tool and the tick call.

    `provide`'s 503 is unreachable in a correctly assembled app: the shell does not
    mount this router when the attribute is `None`.
    """
    return _content(request)
