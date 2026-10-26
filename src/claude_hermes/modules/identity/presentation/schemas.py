"""The wire shapes identity's routes speak. Frozen (spec req. 36).

Responses are plain dicts (`user_wire`). The two login **requests** have no model
here on purpose, which makes this the one module in Wave 2b whose bodies are not
Pydantic: the old handlers read `await request.json()` and answered `401
login_failed` for anything that did not verify — a missing field, a wrong type, a
body that was not JSON at all, a body that was JSON but not an object. A model would
hand the last two to the shell's `RequestValidationError` handler, which answers `400
bad_request`, and `client.ts` branches on the code. `dependencies.login_payload`
parses the body instead and spells the failure as the same 401 the handlers spell;
its docstring is the full argument.

Pydantic appears here and only here. `UserCreate` earned it: the owner's user
list has no 401 of its own to answer with, so the shell's `400 bad_request` for a
malformed body is exactly the error that route wants. `UserGrantsPatch` (0009) is
the same argument, with strict booleans on top.
"""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from pydantic import BaseModel, ConfigDict, StrictBool

from ..domain.models import Grants, Profile, Role, User


def user_wire(user_id: int, is_owner: bool, profile: Profile | None = None,
              defaults: tuple[str | None, str | None] | None = None) -> dict:
    """`{"user_id": 7, "role": "owner"}` — what `/api/me` and both login routes return.

    Wave 4a adds `display_name` and `username`, and adds them **only when a profile
    is passed**. `/api/me` passes one (always, even an empty `Profile()`, so the two
    keys are never conditionally absent from that route's shape); the two login
    routes pass none, so their bodies stay byte-identical to what `client.ts` has
    always parsed. The wire contract allows additive keys, not surprises.

    `defaults` is the model switcher's addition and follows exactly that rule:
    `/api/me` always passes a pair — `(None, None)` for somebody who has chosen
    nothing — so `default_model` and `default_effort` are never conditionally
    absent from that route, while the login bodies stay as they were.
    """
    wire = {"user_id": user_id, "role": (Role.OWNER if is_owner else Role.USER).value}
    if profile is not None:
        wire["display_name"] = profile.display_name
        wire["username"] = profile.username
    if defaults is not None:
        wire["default_model"] = defaults[0]
        wire["default_effort"] = defaults[1]
    return wire


def member_wire(u: User, defaults: Grants) -> dict:
    """One row of the owner's user list. Everything here is already visible to the
    owner through Telegram; what is deliberately absent is `workspace_dir`, which
    is an implementation detail of where somebody's tree sits on the volume.

    `shell` / `learning` (0009) are the EFFECTIVE grants, from `Grants.of` — the
    same rule the turn runs on, not a second copy of it — so a switch in Settings
    shows what the person actually gets. `*_inherited` says whether that value is
    the person's own or the config default showing through (the column is NULL);
    the owner's are never inherited, because the owner's grants are not a choice.
    """
    grants = Grants.of(u, defaults)
    return {
        "user_id": u.id, "role": u.role, "display_name": u.display_name,
        "username": u.username, "created_at": u.created_at,
        "last_seen_at": u.last_seen_at, "disabled": u.disabled, "source": "row",
        "shell": grants.shell, "learning": grants.learning,
        "shell_inherited": not u.is_owner and u.shell_enabled is None,
        "learning_inherited": not u.is_owner and u.learning_enabled is None,
    }


def members_wire(rows: Iterable[User], allowlisted: Iterable[int],
                 defaults: Grants) -> list[dict]:
    """The user list: every row, then every `allowed_users` id that has none.

    The second group is not padding. An id in the config file is admitted by
    `check` the moment its owner says hello, so a list of only the rows would show
    the owner less access than the deployment actually grants. Such an entry is
    `source: "config"` and has no name to show yet — nothing has ever met them.
    It carries the default grants, inherited: that is what they will run on, and
    they cannot be given a choice of their own until a row exists (a grant never
    creates one).
    """
    out = [member_wire(u, defaults) for u in rows]
    known = {row["user_id"] for row in out}
    fallback = Grants.of(None, defaults)
    out.extend({"user_id": uid, "role": Role.USER.value, "display_name": "",
                "username": "", "created_at": "", "last_seen_at": "",
                "disabled": False, "source": "config",
                "shell": fallback.shell, "learning": fallback.learning,
                "shell_inherited": True, "learning_inherited": True}
               for uid in sorted(allowlisted) if uid not in known)
    return out


class UserGrantsPatch(BaseModel):
    """`PATCH /api/users/{user_id}` — `{"shell"?: bool, "learning"?: bool}`.

    `StrictBool`, not `bool`: pydantic's lax `bool` would read `"true"`, `1` and
    `"yes"` as True, and a grant that hands somebody a shell is exactly the field
    that must not be switched on by a string. Anything but a JSON boolean is the
    shell's `400 bad_request`. An absent field (or `null`) is "leave that grant
    alone"; a body naming neither is the router's own 400. There is no way to put a
    grant back to "inherit" — booleans only, by design.
    """

    model_config = ConfigDict(extra="ignore")

    shell: StrictBool | None = None
    learning: StrictBool | None = None


class ModelDefaultPut(BaseModel):
    """`PUT /api/me/model-default` — `{"model": str|null, "effort": str|null}`.

    `Any` with a `None` default for both: the refusals are the router's own
    `400 bad_model` / `400 bad_effort`, in the wording every channel shares
    (`core.model_catalog.validate`), and a typed field would answer pydantic's
    `400 bad_request` for `{"model": 5}` instead. `model: null` is the documented
    way to clear the default, so it is a value and not a missing field.
    """

    model_config = ConfigDict(extra="ignore")

    model: Any = None
    effort: Any = None


class UserCreate(BaseModel):
    """`POST /api/users`. One field, because one field is all Telegram gives:
    a bot cannot look an id up from a @username, so an invitation is a number."""

    model_config = ConfigDict(extra="ignore")

    user_id: Any = None
