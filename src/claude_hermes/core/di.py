"""Dependency injection for the HTTP layer: the bundle on `app.state`, and providers.

`build_web_app` stores the `WebDeps` it was handed on `app.state.deps`; every
collaborator a router needs is an attribute of that object. `provide(name)` turns an
attribute name into a FastAPI dependency, so a module's `presentation/dependencies.py`
declares one typed provider per collaborator and a handler says
`threads: ThreadService = Depends(get_threads)` instead of closing over a bundle
captured when the routes were built.

Two consequences worth keeping in mind:

* **A test swaps a collaborator by overriding the provider**, never by rebuilding the
  bundle: `app.dependency_overrides[get_graph] = lambda: fake`. FastAPI keys those
  overrides by the callable *object*, so a module must build its providers once at
  module scope — `provide("wiki_root")` called twice returns two different callables
  and an override of one would not reach the other.
* **The 503 is a programming-error guard, not a feature switch.** The shell does not
  mount a router whose bundle attribute is `None` (no wiki on disk → no wiki routes,
  and the `/api/*` catch-all answers the 404 the tests pin). A required attribute that
  is missing *here* means the shell and the module disagree about that condition, and
  saying so is better than an `AttributeError` rendered as an opaque 500.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from starlette.requests import Request

from .exceptions import ApplicationException

__all__ = ["bundle", "provide"]


def bundle(request: Request) -> Any:
    """The dependency bundle `build_web_app` was handed (`http.deps.WebDeps`).

    Duck-typed on purpose: core may not import the HTTP composition root, and the
    bundle is an object whose attribute names are the whole contract. A module reads
    attributes off it through `provide`, never this function directly.
    """
    return request.app.state.deps


def provide(name: str, *, required: bool = True) -> Callable[[Request], Any]:
    """A dependency returning `app.state.deps.<name>`.

    `required=False` lets the attribute be `None` and hands the `None` to the
    handler — for a collaborator whose absence is a documented answer (a deployment
    without a voice backend) rather than a mounting mistake.
    """

    def dependency(request: Request) -> Any:
        value = getattr(bundle(request), name, None)
        if value is None and required:
            raise ApplicationException(503, "unavailable", f"{name} is not configured")
        return value

    dependency.__name__ = f"provide_{name}"
    dependency.__doc__ = f"`app.state.deps.{name}`" + ("" if required else " (may be None)")
    return dependency
