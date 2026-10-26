"""What a handshake against one MCP server came back with."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ProbeResult:
    status: str                                     # 'connected' | 'failed' | …
    tools: list[str] = field(default_factory=list)
    error: str = ""
