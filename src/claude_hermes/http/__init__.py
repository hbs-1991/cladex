"""The HTTP composition root, sibling of `workers/`.

`app.build_web_app` assembles the FastAPI app out of every module's
`presentation/router.py`, the identity cookie and the transport policy; `deps.WebDeps`
is the bundle those routers' providers read. Like `workers/`, this package imports
downwards only — the modules, `integrations` and `core` — and nothing below it
imports it back. The name shadows nothing: the stdlib `http` is reached by absolute
import everywhere, and this package is only ever `claude_hermes.http`.
"""
