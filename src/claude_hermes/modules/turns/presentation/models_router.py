"""GET /api/models — what a lane may be switched to, and what each one costs.

Its own router rather than a route on `router.py`, for the same reason
`commands_router.py` is separate: everything there hangs off `/api/threads/{id}`
and is guarded by `owned_thread`, while this is a deployment-wide read with no
lane at all. It must also be mounted BEFORE the `/api/{rest:path}` catch-all, and
a one-route module is what makes that ordering visible in the shell's list.

Any logged-in user gets the same list as the owner — an explicit owner decision
(the story's Constraints): the models on offer are a deployment fact, not a
privilege. `config.yaml` decides WHICH models are offered; `core.model_catalog`
holds what each one IS, so this handler only joins the two.

Dependencies: `get_cfg` (the bundle's `cfg`), read per request so a deployment
that reloads its config does not have to rebuild the app to change the offer.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from starlette.responses import JSONResponse

from claude_hermes.core.config import Config
from claude_hermes.core.model_catalog import ModelInfo, lookup, offered

from .dependencies import get_cfg

router = APIRouter(tags=["turns"])


def _wire(info: ModelInfo) -> dict:
    """One offered model, as the picker reads it.

    `window == 0` is "unknown", never "zero": the picker disables a model whose
    window the current context would not fit, and an unknown window must never
    disable anything. Prices are USD per 1M tokens and `cache_write` is the
    multiple of `price_in` a cold re-read costs — which is what the picker's
    "switching costs about $X" note is computed from, in the browser, so the
    estimate moves with the meter rather than with a round trip.
    """
    return {
        "id": info.id,
        "label": info.label,
        "window": info.window,
        "price": {"in": info.price_in, "out": info.price_out},
        "cache_write": info.cache_write,
        "efforts": list(info.efforts),
        "default_effort": info.default_effort,
    }


@router.get("/api/models", operation_id="turns_list_models")
async def list_models(cfg: Config = Depends(get_cfg)) -> JSONResponse:
    """The offered models in `models:` order, and the deployment's own default.

    `deploy_default` is what a lane runs when nothing — not the thread, not its
    session, not the user — has chosen anything, and it carries no effort: the
    CLI picks the model's own default and a lane that never chose must keep
    getting whatever the CLI picks.
    """
    fork_model = cfg.review_model or cfg.model
    models = offered(cfg.models, cfg.model, fork_model)
    return JSONResponse({
        "models": [_wire(info) for info in models],
        "deploy_default": {"model": lookup(cfg.model).id, "effort": None},
    })
