"""The names learning writes down: meta keys, tool lists, caps, on-disk layout.

The seven meta keys are a wire contract with the operator and with `tools.py`
(`skill_admin status` reads three of them by literal). They are NOT renamed —
`curator_paused` in particular is an operator switch that nothing in this code
ever writes, so a rename would silently un-pause every deployment that set it.
"""
from __future__ import annotations

import re

# --- meta keys (never renamed) ------------------------------------------------
WIKI_NUDGE_META_KEY = "turns_since_wiki_review"
SKILL_NUDGE_META_KEY = "iters_since_skill_write"
LAST_RUN_META_KEY = "curator_last_run_at"
PAUSED_META_KEY = "curator_paused"
REPORT_PATH_META_KEY = "curator_last_report_path"
DRY_RUN_META_KEY = "curator_last_dry_run_at"  # throttles dry-run previews only
# Wave 3a: the review lease. An ISO instant, not a boolean — the row says
# "a review holds this until <t>", so a worker killed mid-pass unblocks the
# curator by itself instead of wedging it until someone edits the table.
REVIEW_GATE_META_KEY = "learning_review_until"

# --- fork tool sets -----------------------------------------------------------
# tools RESTRICTS what exists; allowed_tools only auto-approves (SDK semantics).
REVIEW_BUILTIN_TOOLS = ["Read", "Glob", "Grep", "Write", "Edit"]
REVIEW_TOOLS = list(REVIEW_BUILTIN_TOOLS)   # no MCP tools: the wiki is plain files
CONSOLIDATION_TOOLS = ["Read", "Glob", "Grep", "Write", "Edit"]

# --- skills library layout ----------------------------------------------------
SKILL_FILE = "SKILL.md"
ARCHIVE_DIR = ".archive"
USAGE_FILE = ".usage.json"

# 64 KiB is far past any skill worth routing (the shipped ones are 2-8 KiB) and
# small enough that a runaway paste cannot fill the volume through the browser.
MAX_CONTENT_BYTES = 64 * 1024
# The file tree is a reading aid, not a file manager; a skill with more than this
# many extra files is pathological and the page would be unreadable anyway.
MAX_TREE_ENTRIES = 200

# The catalog's own name rule (catalog.py:47), because this is a directory name
# that also becomes the skill's identity in the prompt index.
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")

LIVE_STATES = ("active", "stale")
SOURCES = ("agent", "user")

# The browser's edits get their own snapshot store: one keep-N window per writer,
# so a run of refused saves cannot rotate out the curator's pre-run snapshot.
SNAPSHOT_KIND = "skill_edits"
