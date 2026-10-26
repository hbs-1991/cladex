"""Names a caller does not get to choose, as this module's domain vocabulary.

Both functions are pure invariants, not storage details: `safe_name` is the
containment rule for anything a user typed, and `lane_token` is the one string
that identifies a session lane in a path and in an object key — so they have to
be derived from one regex in exactly one place, or old files stop resolving.

That place is `core.naming` (Step 5): the telegram module applies `safe_name` to
a downloaded document before this module ever sees it, and modules may not import
each other. Re-exported here so `application/service.py` and the layout keep
reading them as their own.
"""
from __future__ import annotations

from claude_hermes.core.naming import _UNSAFE, lane_token, safe_name

__all__ = ["_UNSAFE", "lane_token", "safe_name"]
