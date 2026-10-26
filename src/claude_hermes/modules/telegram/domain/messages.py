"""Every literal string the gateway can say back into a chat.

Moved verbatim from `gateway/telegram.py` in Wave 2. The private
underscore names are kept because the tests import them by those exact names,
and because several of them (`_APPROVE_UNVERIFIED` above all)
are security copy whose wording is the point.
"""
from __future__ import annotations

_REFUSAL = "Sorry, this agent is private and you're not on its allowlist."
_RESET_ACK = "Context cleared — this thread starts fresh on your next message."
_VOICE_OFF = "I got a voice message, but voice isn't set up here yet."
_VOICE_FAIL = "I couldn't make out that voice message — try again or send text."
_ATTACH_TOO_BIG = (
    "'{name}' is {mb:.1f}MB — Telegram only lets me download files up to 20MB. "
    "Send a smaller version, or put it somewhere I can fetch."
)
_ATTACH_FAIL = "I couldn't download that file — try sending it again."
_NO_CAPTION = "(sent with no caption)"
_CB_OK = "Got it 👍"
_CB_STALE = "That question is no longer active."
_SECRET_USAGE = (
    "Usage: /secret NAME=value — e.g. /secret EXAMPLE_API_KEY=abc123. "
    "NAME must look like an environment variable (A-Z, digits, underscore)."
)
_SECRET_DENIED = "Only the owner may store secrets."
_APPROVE_USAGE = (
    "Usage: /approve <name>  — review a drafted catalog entry and get a confirm token; "
    "then /approve <name> <token> to add it to your catalog."
)
_APPROVE_DENIED = "Only the owner may approve catalog entries."
_APPROVE_SHADOW = (
    "'{name}' is a reviewed catalog entry and cannot be overridden from chat."
)
_APPROVE_NO_DRAFT = "No draft named '{name}' is waiting. Ask me to draft it first."
_APPROVE_STALE = (
    "That token no longer matches the draft (it changed since you last looked). "
    "Run /approve {name} again to see the current version and a fresh token."
)
_APPROVE_UNVERIFIED = (
    "'{name}' was written, but the catalog cannot see it — so it is NOT installable, and "
    "I won't tell you otherwise. Your draft is still queued, so nothing is lost. This is "
    "a deployment fault: whatever wrote the entry and the catalog that reads it disagree "
    "about where the user-catalog lives. Check CH_HOME."
)

# --- /model ---------------------------------------------------------------
# Everything the model menu says. Popup answers (the `_MODEL_*` that come back
# from a tap) are capped by Telegram at 200 characters, so they stay one line;
# the cache sentence is in every one of them on purpose, because a switch really
# does cost one cold re-read of the whole history and the person pressing the
# button is the one who pays for it.
_MODEL_NO_THREAD = "Send a message here first, then use /model."
_MODEL_NOT_OWNER = "Only the owner can change the model here."
_MODEL_STALE = "That model menu has expired — send /model again."
_MODEL_GONE = "That choice is no longer offered here — send /model again."
_MODEL_OFF = "Model switching isn't configured on this deployment."
_MODEL_FAILED = "Something went wrong changing the model — send /model again."
_MODEL_NO_DEFAULT = "You have no default model yet — pick one above first."
_EFFORT_POINTER = "Effort comes with the model — send /model and pick one there."
_MODEL_HEAD = (
    "Model for this chat: {pair}\n"
    "Pick one — the next reply rebuilds that model's cache."
)
_MODEL_DEFAULT_HEAD = (
    "Your default model for new chats: {pair}\n"
    "This chat keeps what it runs now; use \"Also use it in this chat\" to move it too."
)
_MODEL_DEFAULT_UNSET = "not set (new chats use {label})"
_MODEL_SET = "{pair} — the next reply rebuilds this model's cache."
_MODEL_CLEARED = (
    "Cleared — this chat follows your default again. "
    "The next reply rebuilds that model's cache."
)
_MODEL_DEFAULT_SET = "{pair} — your new chats start here. This chat is unchanged."
_MODEL_DEFAULT_CLEARED = "Cleared — new chats follow the deploy default again."
_MODEL_APPLIED = "{pair} — this chat runs it now; the next reply rebuilds its cache."
# Button labels.
_MODEL_CLEAR_BUTTON = "Clear (use my default)"
_MODEL_DEFAULT_CLEAR_BUTTON = "Clear (use the deploy default)"
_MODEL_APPLY_BUTTON = "Also use it in this chat"
_MODEL_AUTO_BUTTON = "Auto ({effort})"
_MODEL_AUTO_PLAIN = "Auto"

# --- content-plan approval cards -------------------------------------------
# Russian, like the rest of the content plan's Telegram surface: the owner writes
# the plan in Russian and the card is where they read the post. Popup answers are
# capped by Telegram at 200 characters. What an Approve tap answers comes from the
# content module (it knows the publish time); these are the gateway's own answers.
_CONTENT_APPROVE_BUTTON = "Одобрить"
_CONTENT_REDO_BUTTON = "Переделать"
_CONTENT_OPEN_BUTTON = "Открыть в календаре"
_CONTENT_REDO_HINT = "Ответь на эту карточку комментарием — что переделать"
_CONTENT_STALE = "Эта карточка больше не действует — открой пост в календаре."
_CONTENT_FAILED = "Не получилось — попробуй ещё раз или открой календарь."
_CONTENT_CUT = "… текст обрезан — полностью в календаре"

__all__ = [
    "_APPROVE_DENIED", "_APPROVE_NO_DRAFT", "_APPROVE_SHADOW", "_APPROVE_STALE",
    "_APPROVE_UNVERIFIED", "_APPROVE_USAGE", "_ATTACH_FAIL", "_ATTACH_TOO_BIG",
    "_CB_OK", "_CB_STALE", "_CONTENT_APPROVE_BUTTON", "_CONTENT_CUT",
    "_CONTENT_FAILED", "_CONTENT_OPEN_BUTTON", "_CONTENT_REDO_BUTTON",
    "_CONTENT_REDO_HINT", "_CONTENT_STALE", "_EFFORT_POINTER", "_MODEL_APPLIED",
    "_MODEL_APPLY_BUTTON", "_MODEL_AUTO_BUTTON", "_MODEL_AUTO_PLAIN",
    "_MODEL_CLEARED", "_MODEL_CLEAR_BUTTON", "_MODEL_DEFAULT_CLEARED",
    "_MODEL_DEFAULT_CLEAR_BUTTON", "_MODEL_DEFAULT_HEAD", "_MODEL_DEFAULT_SET",
    "_MODEL_DEFAULT_UNSET", "_MODEL_FAILED", "_MODEL_GONE", "_MODEL_HEAD",
    "_MODEL_NO_DEFAULT",
    "_MODEL_NO_THREAD", "_MODEL_NOT_OWNER", "_MODEL_OFF", "_MODEL_SET",
    "_MODEL_STALE", "_NO_CAPTION", "_REFUSAL", "_RESET_ACK",
    "_SECRET_DENIED", "_SECRET_USAGE", "_VOICE_FAIL", "_VOICE_OFF",
]
