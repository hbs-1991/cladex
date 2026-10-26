"""Limits and wire tokens the Telegram platform imposes on us."""
from __future__ import annotations

# The Bot API's own getFile ceiling. Anything larger fails on Telegram's side
# regardless, so refusing here just buys a message that says why.
MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024

# Telegram's limit on a forum-topic name.
TOPIC_NAME_LIMIT = 128

# callback_data of an ask_user button: "q:<qid>:<option index|c>". Telegram caps
# callback_data at 64 bytes, which is why the question id is a short hex token
# and the answer is an index rather than the label.
CALLBACK_PREFIX = "q"
CANCEL_TOKEN = "c"

# callback_data of a /model button: "mdl:<scope>:<action>:<payload>", four fields
# and never more, because a model id may hold `-` but never `:`. The shapes are
# frozen (the model-switcher story's Locked Decisions) — a button left in a chat
# across a deploy must still parse, and the 64-byte ceiling above means the
# payload is the catalog id itself rather than an index into a list that moves.
#
#   mdl:t:m:<id>        this lane: run this model
#   mdl:t:e:<effort>    this lane: this effort ("auto" = send no effort flag)
#   mdl:t:x:-           this lane: clear the explicit pair
#   mdl:d:m:<id>        my default for new chats
#   mdl:d:e:<effort>    my default's effort
#   mdl:d:x:-           clear my default
#   mdl:a:x:-           copy my default onto this lane as its explicit pair
MODEL_CALLBACK_PREFIX = "mdl"
SCOPE_THREAD = "t"
SCOPE_DEFAULT = "d"
SCOPE_APPLY = "a"
ACTION_MODEL = "m"
ACTION_EFFORT = "e"
ACTION_CLEAR = "x"
AUTO_EFFORT = "auto"        # the button that means "no effort flag"
CLEAR_PAYLOAD = "-"         # the fourth field is never empty: "mdl:t:x:" would
                            # split to a value Telegram may trim on the way back

# How long a /model keyboard stays live. The menu shows a lane's CURRENT pair, and
# an old message's buttons would set a model against a header that has since become
# a lie — so a tap on anything older than this answers "expired" and changes
# nothing, rather than silently switching a lane somebody left scrolled up.
MODEL_MENU_TTL_SECONDS = 600

# callback_data of a content-plan approval card: "cnt:<action>:<item id>:<rev12>",
# four fields. The revision is the first 12 hex chars of the card's content hash
# (the content module computes it), so a tap is bound to the exact text and media
# the owner looked at — and the whole thing stays near 30 bytes of the 64 allowed.
#
#   cnt:a:<id>:<rev>    approve this revision (the gateway, never the model)
#   cnt:r:<id>:<rev>    "redo" — only a hint: the comment arrives as a reply
CONTENT_CALLBACK_PREFIX = "cnt"
CONTENT_APPROVE = "a"
CONTENT_REDO = "r"
CONTENT_REVISION_LEN = 12

# sendMessage's text ceiling. A card's body is cut to fit, with a note that the
# full text is in the calendar.
MESSAGE_TEXT_LIMIT = 4096

# How many photos one sendMediaGroup takes.
MEDIA_GROUP_LIMIT = 10


# The webhook path, spelled once. Two layers need it and they may not import each
# other (`presentation` mounts the route, `infrastructure/transport.py` builds the
# URL it registers with `setWebhook`), and a URL that disagrees with the mounted
# path by one character is a bot that silently receives nothing.
WEBHOOK_PATH = "/telegram/webhook/{secret}"

# The header Telegram sends on every webhook delivery once `set_webhook` was given
# a `secret_token`. It is the credential that never reaches a proxy's access log —
# the path segment does — which is why the route requires it.
SECRET_HEADER = "X-Telegram-Bot-Api-Secret-Token"
