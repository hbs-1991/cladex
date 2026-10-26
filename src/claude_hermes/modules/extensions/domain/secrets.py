"""What a secret NAME is, and how a declared `${VAR}` is expanded. No I/O: the file
on disk is `infrastructure/env_file_secrets.py`, and Wave 3 swaps that adapter for
PostgreSQL without touching a line here."""
from __future__ import annotations

import re
from collections.abc import Iterable, Mapping

from .exceptions import SecretNameError

SECRETS_FILENAME = "secrets.env"

# The bot's own tokens live in .env on the host, which docker-compose reads via
# env_file at container start. The agent process can neither write that file nor
# make it reload, so extension secrets live here instead: on the /data volume,
# ours to write, read fresh every time a server is spawned.
_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
_VAR_RE = re.compile(r"\$\{([A-Z][A-Z0-9_]*)\}")

__all__ = ["SECRETS_FILENAME", "SecretNameError", "check_name", "check_value",
           "interpolate", "missing"]


def check_name(name: str) -> None:
    if not _NAME_RE.match(name or ""):
        raise SecretNameError(f"not an environment variable name: {name!r}")


def check_value(value: str) -> None:
    if "\n" in value or "\r" in value:
        raise SecretNameError("a secret value may not contain a newline")


def interpolate(text: str, secrets: Mapping[str, str]) -> str:
    """Expand ${VAR}. An unknown var keeps its placeholder: a server that fails to
    connect with a visible ${VAR} in its config is debuggable; one that silently got
    an empty key is not."""
    return _VAR_RE.sub(lambda m: secrets.get(m.group(1), m.group(0)), text)


def missing(names: Iterable[str], secrets: Mapping[str, str]) -> list[str]:
    return [n for n in names if n not in secrets]
