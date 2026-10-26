"""Wire shapes for the wiki API. Frozen: the frontend reads these keys.

`graph` → `{pages: [...], namespaces: [...], missing: [...]}`, each page
`{path, ns, title, summary, tags, updated, links, in_index}`; the page detail adds
`content` and `etag`.

No Pydantic model lives here yet, and that is not an omission: both wiki routes are
`GET` with no body and no query parameter. This file is where a request body model
would go (`BaseModel` with `model_config = ConfigDict(extra="ignore")`) — and the only
place in a module where Pydantic is allowed at all.
"""
from __future__ import annotations


def graph_wire(graph: dict) -> dict:
    return graph


def page_wire(detail: dict, content: bytes, etag: str) -> dict:
    out = dict(detail)
    # The etag is here for the editor this page does not have yet: the moment a
    # write exists it needs a precondition, and shipping it now means the
    # frontend already holds the token that write will demand.
    out["content"] = content.decode("utf-8", "replace")
    out["etag"] = etag
    return out
