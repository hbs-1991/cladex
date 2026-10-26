"""A manifest plus the secrets on disk → what the SDK is handed. Pure."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from .exceptions import ComposeError
from .manifest import Manifest
from .secrets import interpolate, missing


@dataclass(frozen=True)
class Composed:
    mcp_servers: dict[str, dict] = field(default_factory=dict)
    tool_names: list[str] = field(default_factory=list)
    plugins: list[dict] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)


def server_spec(m: Manifest, secret_values: Mapping[str, str]) -> dict:
    """A catalog manifest plus the secrets on disk → the SDK's McpServerConfig.

    Raises ComposeError when a declared secret is absent: spawning a server with a
    literal ${VAR} for its credential is a slower, more confusing failure than
    refusing here.
    """
    absent = missing(m.secret_names(), secret_values)
    if absent:
        raise ComposeError(f"missing secret(s): {', '.join(absent)}")

    if m.transport == "stdio":
        return {
            "type": "stdio",
            "command": m.command,
            "args": [interpolate(a, secret_values) for a in m.args],
            "env": {k: interpolate(v, secret_values) for k, v in m.env.items()},
        }

    headers = {
        a.header: interpolate(a.format, secret_values)
        for a in m.auth if a.header
    }
    spec: dict = {"type": m.transport, "url": interpolate(m.url, secret_values)}
    if headers:
        spec["headers"] = headers
    return spec
