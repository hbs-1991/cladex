"""Request bodies for the turns API — the only place in this module where Pydantic
may appear.

Every field below is **lenient on purpose**. The hand-written handlers these models
replace read `await request.json()` and coerced whatever came back (`str(body.get(
"text", ""))`), answering with their own status and `code` when the value made no
sense — `400 bad_request "text or attachments required"`, `400 bad_request "answer is
not one of the options"`. A schema that refused the value first would answer `400
bad_request "body.text: Input should be a valid string"` instead, and the frontend
(`web/src/api/client.ts`) branches on the message for none of them but the wire
contract is the message too.

So: every field is `Any` with a default, the model ignores extras exactly as
`dict.get` did, and each handler keeps its own check verbatim. `str(None)` is `"None"`
and always was — that is parity, not a bug to fix here.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class MessageIn(BaseModel):
    """`POST /api/threads/{id:path}/messages`.

    `text` is stringified then stripped; `attachment_ids` is iterated and each id is
    resolved against *this* lane. Neither is required by the schema: "no text and no
    attachments" is the handler's 400, and "an id from another lane" is its other one.
    """

    model_config = ConfigDict(extra="ignore")

    text: Any = ""
    attachment_ids: Any = None


class AnswerIn(BaseModel):
    """`POST /api/asks/{qid}` — the option the user tapped.

    Not validated against the question's options here: the broker owns that list, and
    an answer that is not in it is `400 bad_request "answer is not one of the options"`
    while an expired question is `410 gone`. Only the broker can tell those apart.
    """

    model_config = ConfigDict(extra="ignore")

    answer: Any = ""


class SpeakIn(BaseModel):
    """`POST /api/voice/speak` — the text to read aloud.

    Empty text reaches the voice backend, which is what decides whether it is a
    `VoiceError` (`400 bad_request`); a deployment without a key answers
    `503 voice_disabled` before that.
    """

    model_config = ConfigDict(extra="ignore")

    text: Any = ""
