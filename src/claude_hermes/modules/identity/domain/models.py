"""Who is asking, as the application sees them.

`CurrentUser` and `Role` are **aliases of `core.contracts`'** — the same two objects,
named here because identity owns the concept while every module's router reads one.
The definition sits in core because `core.auth` builds a `CurrentUser` per request
(session cookie plus one owner check) and core may not import a module; this file is
where identity's own code looks for it, and importing `core.auth` here instead would
drag Starlette into a domain layer the import contracts keep pure.

Wave 4a gives identity its first table. `User` below is what the rest of the app is
handed — a frozen snapshot with no session behind it, so it can cross a process, a
cache or a port without a lazy load waiting to fail. The declarative model itself is
in `domain/tables.py` and **deliberately not here**: this module is imported by
`application/services.py` and by `presentation/`, and the layer contracts forbid
SQLAlchemy behind either of them, chains included. That file's docstring is the full
argument.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from claude_hermes.core.contracts import CurrentUser, Role

__all__ = ["CurrentUser", "Grants", "Profile", "Role", "User"]


@dataclass(frozen=True)
class User:
    """A `users` row, detached. Frozen because the workspace resolver caches it per
    process, and a mutable copy would let one caller's edit move another's root.

    `shell_enabled` / `learning_enabled` are the owner's per-person grants (0009)
    exactly as stored: `None` is "no choice of their own", which inherits the
    config default. They are raw columns, not answers — `Grants.of` is the one
    place that turns them into what this person may do."""

    id: int
    role: str
    workspace_dir: str
    display_name: str = ""
    username: str = ""
    created_at: str = ""
    last_seen_at: str = ""
    disabled_at: str | None = None
    shell_enabled: bool | None = None
    learning_enabled: bool | None = None

    @property
    def is_owner(self) -> bool:
        return self.role == Role.OWNER.value

    @property
    def disabled(self) -> bool:
        return bool(self.disabled_at)

    @classmethod
    def from_row(cls, row: Any) -> "User":
        """Build from a SQLAlchemy row mapping — or any dict-like with the columns."""
        return cls(
            id=int(row["id"]), role=str(row["role"]),
            workspace_dir=str(row["workspace_dir"]),
            display_name=str(row["display_name"] or ""),
            username=str(row["username"] or ""),
            created_at=str(row["created_at"] or ""),
            last_seen_at=str(row["last_seen_at"] or ""),
            disabled_at=row["disabled_at"],
            # `.get`, not `[]`: a dict-like built by hand (a test, a REPL) that
            # predates 0009 has no such key, and "no key" means "no choice".
            shell_enabled=_optional_bool(row.get("shell_enabled")),
            learning_enabled=_optional_bool(row.get("learning_enabled")),
        )


def _optional_bool(value: Any) -> bool | None:
    """A nullable BOOLEAN column as Python: `None` stays "no choice"."""
    return None if value is None else bool(value)


@dataclass(frozen=True)
class Grants:
    """What one person may do beyond talking: a shell and the learning loop.

    `shell` is `Bash`/`Agent`/`Task` on their lanes — one container is one uid, so
    it reaches every user's files and credentials, which is why it is a grant and
    not a default. `learning` is the learning loop over their conversations (review
    forks, the curator and the wiki lint of their tree), all of it spend on the
    owner's account. Both are the EFFECTIVE answer, never a raw column.
    """

    shell: bool
    learning: bool

    @classmethod
    def of(cls, user: "User | None", defaults: "Grants") -> "Grants":
        """THE resolution rule — the only one. Every reader (the ACL, the user list
        on the wire) calls this; nothing re-implements it.

        * the owner's row → everything: the deploy owner runs the deployment, and
          their lanes already get the full toolset and the observer;
        * any other row → its own column, else the default (`None` = inherit);
        * no row at all → the defaults. That is fail-closed for the same reason
          `default_of` and `admitted` answer as they do for a stranger: an id with
          no row has no choice of its own to honour.
        """
        if user is None:
            return defaults
        if user.is_owner:
            return cls(shell=True, learning=True)
        return cls(
            shell=defaults.shell if user.shell_enabled is None else user.shell_enabled,
            learning=(defaults.learning if user.learning_enabled is None
                      else user.learning_enabled),
        )


@dataclass(frozen=True)
class Profile:
    """The two display fields a Telegram identity carries, normalised.

    Built from a `from_user` (Telegram sends `first_name`/`last_name`/`username`) or
    from a Login Widget payload, which sends the same three keys. Nothing but
    `/api/me` reads them yet; they exist so the owner can tell two allowlisted ids
    apart without a Telegram client open.

    `of` takes *anything* shaped like a profile — a `Profile`, a mapping, or another
    module's own value object with the same two attributes. That is what lets
    `modules/telegram` hand its sender profile to the ACL without importing identity:
    modules are independent of each other, and structural typing does the matching.
    """

    display_name: str = ""
    username: str = ""

    @classmethod
    def from_telegram(cls, first_name: Any = "", last_name: Any = "",
                      username: Any = "") -> "Profile":
        """`display_name` is `first_name + " " + last_name`, trimmed (open question 2)."""
        name = f"{first_name or ''} {last_name or ''}".strip()
        return cls(display_name=name, username=str(username or "").lstrip("@"))

    @classmethod
    def of(cls, value: Any) -> "Profile | None":
        """Normalise whatever a caller passed, or `None` when they passed nothing.

        `None` means "no news about this person", and the repository then leaves the
        stored names alone — an update without a `from_user` must not blank a display
        name the Login Widget already gave us.
        """
        if value is None:
            return None
        if isinstance(value, cls):
            return value
        if isinstance(value, dict):
            # Telegram's spelling is checked FIRST: a Login Widget payload carries
            # `username` too, and reading it through the `display_name` branch
            # would keep the handle and throw the name away.
            if "first_name" in value or "last_name" in value:
                return cls.from_telegram(value.get("first_name"),
                                         value.get("last_name"),
                                         value.get("username"))
            return cls(display_name=str(value.get("display_name") or ""),
                       username=str(value.get("username") or "").lstrip("@"))
        display = getattr(value, "display_name", None)
        if display is not None or hasattr(value, "username"):
            return cls(display_name=str(display or ""),
                       username=str(getattr(value, "username", "") or "").lstrip("@"))
        return cls.from_telegram(getattr(value, "first_name", ""),
                                 getattr(value, "last_name", ""),
                                 getattr(value, "username", ""))
