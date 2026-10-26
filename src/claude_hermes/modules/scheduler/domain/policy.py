"""What a scheduled job is allowed to be: the prompt scan and the timeout bounds.

Both rules are pure predicates over user input and both are security-relevant —
an unattended fork runs the prompt with no human at the keyboard. They live in
the domain so the tool channel and the web channel physically share them.
"""
from __future__ import annotations

import re

_RULES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\brm\s+-[a-z]*rf?[a-z]*\s+/", re.I), "destructive filesystem command"),
    (re.compile(r"\b(mkfs|dd\s+if=)", re.I), "destructive disk command"),
    (re.compile(r"(^|[\s/])\.env\b.*\b(post|send|upload|curl|http)", re.I | re.S),
     "secret exfiltration"),
    # no leading \b: must match API_KEY inside identifiers like ANTHROPIC_API_KEY
    (re.compile(r"(api[_ ]?key|token|password|секрет|пароль).*\b(send|post|перешли|отправь)\b",
     re.I | re.S), "secret exfiltration"),
    (re.compile(r"\b(ignore|disregard|forget)\b.{0,40}\b(previous|prior|above)\b.{0,20}\binstructions\b",
     re.I), "instruction override"),
    (re.compile(r"игнорируй.{0,40}(предыдущие|прошлые).{0,20}инструкции", re.I),
     "instruction override"),
]


def scan_prompt(text: str) -> str | None:
    for pattern, reason in _RULES:
        if pattern.search(text):
            return reason
    return None


# Bounds on a job's own time limit. The floor keeps a typo from creating a job that
# can never finish; the ceiling keeps one from pinning a turn's worth of tokens for
# half an hour unattended. A job that needs more than this wants to be two jobs.
MIN_JOB_TIMEOUT_SECONDS = 30
MAX_JOB_TIMEOUT_SECONDS = 1800


def job_timeout(raw) -> int | None:
    """Validate an optional per-job time limit. None/absent → inherit the global one."""
    if raw is None or raw == "":
        return None
    try:
        seconds = int(raw)
    except (TypeError, ValueError):
        raise ValueError(f"timeout_seconds must be a whole number of seconds, got {raw!r}.")
    if not MIN_JOB_TIMEOUT_SECONDS <= seconds <= MAX_JOB_TIMEOUT_SECONDS:
        raise ValueError(
            f"Rejected: timeout_seconds must be between {MIN_JOB_TIMEOUT_SECONDS} "
            f"and {MAX_JOB_TIMEOUT_SECONDS}; a job needing more should be split."
        )
    return seconds


