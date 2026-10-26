"""Naming constants for a thread title. Pure data — no SDK, no logging."""
from __future__ import annotations

TITLE_PROMPT = """\
You name Telegram topics. You do not answer, help, explain or ask questions.
Given the user's first message in a new topic, reply with a title of at most 6 words
that says what the topic is about, in the language of the message. No quotes, no emoji,
no trailing period. Output the title and nothing else."""

TITLE_MAX_WORDS = 10   # a 6-word ask, with slack; past this it is an answer, not a name

# The cap a generated title is truncated to. It used to be `topics.TOPIC_NAME_LIMIT`
# (Telegram's limit on a forum-topic name), which made this module's DB truncation
# depend on the telegram module. The number is the same on purpose: a title is
# written to `threads.title` AND used as a topic name, so shortening it here would
# silently change what Telegram shows.
TITLE_MAX_CHARS = 128

# The title a browser user types is capped separately, where the route reads it.
MANUAL_TITLE_MAX_CHARS = 120
