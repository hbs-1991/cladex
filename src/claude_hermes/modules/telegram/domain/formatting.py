"""Pure text shaping for the Bot API's size limits and markup.

Domain rather than presentation because `infrastructure/rich_sender.py` is the
caller, and infrastructure and presentation are independent siblings in the layer
contract. `presentation/formatting.py` re-exports both names so the reading order
of the brief still holds.

Moved verbatim from `gateway/telegram.py` (`chunk_text`) and
`gateway/rich.py` (`render_html`) in Wave 2.
"""
from __future__ import annotations

import html


def chunk_text(text: str, limit: int = 4000) -> list[str]:
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    current = ""
    for para in text.split("\n\n"):
        candidate = f"{current}\n\n{para}" if current else para
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            chunks.append(current)
        while len(para) > limit:  # hard-split an oversized paragraph
            chunks.append(para[:limit])
            para = para[limit:]
        current = para
    if current:
        chunks.append(current)
    return chunks


def render_html(text: str) -> str:
    """Escaped HTML fallback for text the markdown renderer rejected."""
    paras = [p.strip() for p in text.split("\n\n") if p.strip()]
    return "".join(f"<p>{html.escape(p)}</p>" for p in paras)


# A title is a heading, not a second body: one this long is already a mistake.
_CARD_TITLE_LIMIT = 256


def card_text(*, title: str, channel: str, fmt: str, publish_at_local: str,
              body: str, cut_note: str, limit: int = 4096) -> str:
    """The text message of a content approval card, as `parse_mode=HTML`.

    Everything the drafter or the owner wrote is escaped: a post body is the
    model's prose and a stray `<` in it must not make Telegram refuse the card.
    The body is what gets cut when the whole would not fit `limit`, and it is cut
    BEFORE escaping — cutting escaped text could split an `&amp;` in half — with
    `cut_note` appended so the owner knows the rest is in the calendar. The
    budget is measured on the escaped HTML, which is never shorter than what
    Telegram counts after parsing the entities, so the result always fits.
    """
    head = [f"<b>{html.escape((title or '').strip()[:_CARD_TITLE_LIMIT])}</b>"]
    kind = " · ".join(html.escape(p) for p in ((channel or "").strip(),
                                               (fmt or "").strip()) if p)
    if kind:
        head.append(kind)
    if publish_at_local:
        head.append(f"🕒 {html.escape(publish_at_local)}")
    header = "\n".join(head)
    body = (body or "").strip()
    if not body:
        return header[:limit]
    whole = f"{header}\n\n{html.escape(body)}"
    if len(whole) <= limit:
        return whole
    note = f"\n\n<i>{html.escape(cut_note)}</i>"
    budget = limit - len(header) - 2 - len(note)
    n = max(0, min(len(body), budget))
    # Escaping only ever lengthens text, so shrink until the escaped prefix fits.
    while n > 0 and len(html.escape(body[:n])) > budget:
        n -= max(1, len(html.escape(body[:n])) - budget)
    return f"{header}\n\n{html.escape(body[:n].rstrip())}{note}"
