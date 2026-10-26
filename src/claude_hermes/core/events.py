"""What one turn did, as seen from outside the turn.

`turns` fills this in while the SDK streams blocks; `learning` reads it to decide
whether a wiki nudge or a review pass is due. Neither module may import the other,
so the shape they agree on lives here.

Moved verbatim from `learning/observe.py` in Wave 2.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class TurnStats:
    tool_uses: int = 0
    wrote_wiki: bool = False
    wrote_skill: bool = False
    skill_invocations: list[str] = field(default_factory=list)
    skill_patches: list[str] = field(default_factory=list)


# The name the learning side reads it under: to `learning` this is an observation
# of a finished turn, not the turn's own statistics. One class, two vocabularies —
# an alias rather than a second dataclass that could drift.
TurnObservation = TurnStats
