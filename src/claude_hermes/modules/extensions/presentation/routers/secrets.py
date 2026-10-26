"""Secrets router: the browser face of `extension_secrets`, owner-only end to end.

One table holds every credential an extension is launched with, and until now the
only ways in were `/secret NAME=value` in a Telegram DM and the per-connector form
under Customize. Neither lists what is stored, and neither can set a variable no
manifest has declared yet — a key the owner has but has not wired to anything.
This router is that list and that door.

Owner-only end to end even after Wave 5 made `extension_secrets` per user: a
list of every credential a deployment holds — including the names of ones no
manifest declares — is not something a plain user may see. So the `owner_id`
every call carries here is always the caller's, and the caller is always the
owner. A non-owner sets their own keys on the Connectors page instead, per
connector, where a name is only ever one they were asked for.

What it deliberately is not: a way to read a value back. `GET` answers with names,
timestamps and who declares them; the ciphertext is never opened here (`names()`,
not `load()`), so a response cannot carry a credential even by mistake, and a
rotated `CH_SECRETS_KEY` still lists what is there instead of hiding it.

The declared-but-unset rows come from the catalog, so the list is the whole truth
about what the deployment needs, not only about what it already has. OAuth entries
are left out of that half on purpose: an access token is mirrored into this table
like any other secret, but it arrives by signing in at the provider, and a field
inviting the owner to type one by hand would be a wrong answer wearing a right one.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from starlette.responses import JSONResponse, Response

from claude_hermes.core.auth import CurrentUser, require_owner
from claude_hermes.core.exceptions import ApplicationException as ApiError

from ...domain.exceptions import SecretNameError
from ...domain.secrets import check_name, check_value
from ..dependencies import ExtensionsDeps, get_extensions
from ..schemas import SecretValue, secrets_to_wire

router = APIRouter(tags=["extensions"], dependencies=[Depends(require_owner)])


def secret_name(name: str) -> str:
    """The name out of the URL, proven to be an environment variable name.

    A route parameter is only guaranteed not to contain '/', and this one is a
    primary key the handler writes or deletes by. The domain rule is the same one
    `/secret` and the connector form apply, so nothing reachable here is a name the
    other two channels would have refused.
    """
    try:
        check_name(name)
    except SecretNameError as e:
        raise ApiError(400, "bad_request", str(e)) from e
    return name


@router.get("/api/secrets", operation_id="extensions_list_secrets")
async def list_secrets(user: CurrentUser = Depends(require_owner),
                       ext: ExtensionsDeps = Depends(get_extensions)) -> JSONResponse:
    """Every stored secret and every declared-but-unset one. Never a value."""
    return JSONResponse({"secrets": secrets_to_wire(
        await ext.secrets.names(owner_id=user.user_id),
        ext.catalog.load_all(ext.catalog_root).values())})


@router.put("/api/secrets/{name}", status_code=204,
            operation_id="extensions_put_secret")
async def put_secret(body: SecretValue, name: str = Depends(secret_name),
                     user: CurrentUser = Depends(require_owner),
                     ext: ExtensionsDeps = Depends(get_extensions)) -> Response:
    """Set one secret, new or existing. The value is written, never echoed."""
    value = str(body.value or "")
    if not value:
        raise ApiError(400, "bad_request", "a secret needs a value")
    try:
        check_value(value)
        await ext.secrets.put(name, value, owner_id=user.user_id)
    except SecretNameError as e:
        raise ApiError(400, "bad_request", str(e)) from e
    return Response(status_code=204)


@router.delete("/api/secrets/{name}", status_code=204,
               operation_id="extensions_delete_secret")
async def delete_secret(name: str = Depends(secret_name),
                        user: CurrentUser = Depends(require_owner),
                        ext: ExtensionsDeps = Depends(get_extensions)) -> Response:
    """Forget one secret; absent is fine, and a name a manifest declares stays in
    the list as unset — the extension that wants it has not stopped wanting it."""
    await ext.secrets.forget(name, owner_id=user.user_id)
    return Response(status_code=204)
