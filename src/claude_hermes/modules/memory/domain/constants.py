"""The wiki's fixed vocabulary: filenames, seed namespaces, headers, migrations.

Moved verbatim from `wiki.py` in Wave 2. `_NAMESPACES` is SEED-ONLY —
it is the set of folders a fresh volume gets, never a description of what the wiki
*is*. The agent invents its own folders (`content/` exists on the live deployment),
so anything that enumerates the wiki must scan the directory instead (see
`application/services/graph.py`).
"""
from __future__ import annotations

_INDEX = "index.md"
_LOG = "log.md"
_NAMESPACES = ("user", "projects", "topics", "env", "raw")

TRUNCATION_NOTE = (
    "\n\n[index truncated — read wiki/index.md from disk before concluding a page is missing]"
)

_INDEX_HEADER = "# Wiki index\n\nEvery page, one line each: `- <path> — <summary>`. Keep it grouped\nand current; it is what the agent sees at session start.\n"
_LOG_HEADER = "# Wiki log\n\nAppend-only. One line per ingest, query or lint pass.\n"

# (legacy file, destination page, title, summary)
_MIGRATIONS = (
    ("USER.md", "user/profile.md", "User profile",
     "who the user is — carried over from the old flat memory"),
    ("MEMORY.md", "env/notes.md", "Environment and project notes",
     "environment and project facts — carried over from the old flat memory"),
)

# The lint fork's reach. Read/Glob/Grep to find the drift, Write/Edit to fix it —
# and on a dry run the write guard gets an empty root list, so the same tool list is
# read-only by construction (see application/services/lint.py).
WIKI_LINT_TOOLS = ["Read", "Glob", "Grep", "Write", "Edit"]
