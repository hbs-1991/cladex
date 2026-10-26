"""One user's install is not another's — the service layer."""
from __future__ import annotations

from claude_hermes.modules.extensions.application.services.extension_service import (
    ExtensionService,
)
from claude_hermes.modules.extensions.domain.manifest import MANIFEST_VERSION, parse
from claude_hermes.modules.extensions.infrastructure.repositories.extension_repository import (  # noqa: E501
    SqlExtensionRepository,
)
from claude_hermes.modules.extensions.infrastructure.repositories.oauth_token_repository import (  # noqa: E501
    SqlOauthTokenRepository,
)

from tests.conftest import ensure_users
from tests.ext_helpers import secrets_for

NOW = "2026-09-22T10:00:00+00:00"


def _manifest(name: str, kind: str = "plugin"):
    body = {"manifest_version": MANIFEST_VERSION, "name": name, "label": name,
            "description": "A test plugin.", "version": "1", "scopes": ["turn"],
            "repo": "https://example.test/x.git", "ref": "a" * 40}
    return parse(body, kind)


class _Catalog:
    """The `CatalogReader` port, over one manifest held in memory."""

    def __init__(self, *manifests):
        self._by_name = {m.name: m for m in manifests}

    def load_all(self, root=None):
        return dict(self._by_name)

    def get(self, name, root=None):
        return self._by_name.get(name)

    def is_repo_entry(self, name):
        return False


class _Installer:
    """Records what it was asked to remove; never touches the disk."""

    def __init__(self):
        self.removed: list[str] = []

    def remove(self, name: str) -> None:
        self.removed.append(name)

    async def clone(self, m, timeout: float = 120.0):
        raise AssertionError("not used in these tests")


def _service(engine, home, installer=None, catalog=None):
    store = SqlExtensionRepository(engine)
    return store, ExtensionService(
        home, store=store, tokens=SqlOauthTokenRepository(engine),
        secrets=secrets_for(engine), catalog=catalog or _Catalog(),
        installer=installer)


async def test_enable_is_about_your_own_row(engine, tmp_path):
    await ensure_users(engine, 7, owner=1)
    store, svc = _service(engine, tmp_path)
    await store.add("fetch", "mcp", "1.0.0", installed_by=1, now=NOW, owner_id=1)
    assert await svc.enable("fetch", owner_id=7) is False
    assert await svc.disable("fetch", owner_id=1) is True
    assert (await store.get("fetch", owner_id=1))["state"] == "disabled"


async def test_a_non_owner_removing_a_plugin_keeps_the_shared_clone(engine, tmp_path):
    """The clone is code every tree loads; only the owner's delete may take it."""
    await ensure_users(engine, 7, owner=1)
    installer = _Installer()
    store, svc = _service(engine, tmp_path, installer=installer)
    await store.add("thing", "plugin", "1.0.0", installed_by=7, now=NOW, owner_id=7)
    assert await svc.remove("thing", owner_id=7) is True
    assert installer.removed == []
    assert await store.get("thing", owner_id=7) is None


async def test_the_owner_may_take_the_clone_with_the_row(engine, tmp_path):
    await ensure_users(engine, owner=1)
    installer = _Installer()
    store, svc = _service(engine, tmp_path, installer=installer)
    await store.add("thing", "plugin", "1.0.0", installed_by=1, now=NOW, owner_id=1)
    assert await svc.remove("thing", owner_id=1, remove_clone=True) is True
    assert installer.removed == ["thing"]


async def test_adopt_writes_a_row_over_an_existing_clone(engine, tmp_path):
    """"Enable it for me" on a plugin somebody else cloned."""
    await ensure_users(engine, 7, owner=1)
    root = tmp_path / "plugins" / "thing" / ".claude-plugin"
    root.mkdir(parents=True)
    (root / "plugin.json").write_text("{}", encoding="utf-8")

    store, svc = _service(engine, tmp_path)
    ok = await svc.adopt(_manifest("thing"), user_id=7, now=NOW, owner_id=7)
    assert ok is True
    assert (await store.get("thing", owner_id=7))["state"] == "enabled"


async def test_adopt_refuses_when_there_is_no_clone_to_adopt(engine, tmp_path):
    """Never a row pointing at a directory that is not there — the route answers 404."""
    await ensure_users(engine, 7, owner=1)
    store, svc = _service(engine, tmp_path)
    assert await svc.adopt(_manifest("thing"), user_id=7, now=NOW, owner_id=7) is False
    assert await store.get("thing", owner_id=7) is None


async def test_enabled_plugin_roots_are_the_ones_this_owner_enabled(engine, tmp_path):
    """The clone is shared; which of them a user loads is not."""
    from claude_hermes.modules.extensions.application.services.queries import (
        ExtensionsQueryService,
    )

    await ensure_users(engine, 7, owner=1)
    root = tmp_path / "plugins" / "thing" / ".claude-plugin"
    root.mkdir(parents=True)
    (root / "plugin.json").write_text("{}", encoding="utf-8")

    store = SqlExtensionRepository(engine)
    await store.add("thing", "plugin", "1.0.0", installed_by=1, now=NOW, owner_id=1)
    q = ExtensionsQueryService(tmp_path, store=store,
                               catalog=_Catalog(_manifest("thing")))
    assert [n for n, _ in await q.enabled_plugin_roots(owner_id=1)] == ["thing"]
    assert await q.enabled_plugin_roots(owner_id=7) == []
