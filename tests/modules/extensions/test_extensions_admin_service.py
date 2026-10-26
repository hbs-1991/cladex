"""`ExtensionAdminService` — the `ext_manage` body, over fakes.

`tests/test_ext_manage.py` still drives the same behaviour through the legacy tool
adapter; this one states the two rules that are about the service itself: the owner gate
fails closed, and the prompt-cache reset fires only AFTER the registry actually changed.
"""
from __future__ import annotations

from pathlib import Path

from claude_hermes.modules.extensions.application.services.admin_service import (
    ExtensionAdminService)

HTTP = {
    "manifest_version": 1, "name": "srv", "label": "Srv", "description": "d",
    "version": "1.0.0", "transport": "http", "url": "https://mcp.example.com/mcp",
    "scopes": ["turn"],
}


class FakeCatalog:
    def __init__(self, entries=None):
        self._entries = entries or {}

    def load_all(self, root=None):
        return dict(self._entries)

    def get(self, name, root=None):
        return self._entries.get(name)

    def is_repo_entry(self, name):
        return True


class FakeStore:
    """Per owner, like the real one: `owner_id` is keyword-only with no default, so
    a branch that forgot to pass it is a TypeError here too."""

    def __init__(self):
        self.rows: dict[tuple[int, str], dict] = {}

    async def list(self, kind=None, *, owner_id: int):
        return [r for (o, _n), r in self.rows.items()
                if o == owner_id and (kind is None or r["kind"] == kind)]

    async def get(self, name, *, owner_id: int):
        return self.rows.get((owner_id, name))

    async def set_state(self, name, state, *, owner_id: int):
        self.rows[(owner_id, name)]["state"] = state


class FakeDrafts:
    """The `DraftStore` port by shape only — async and `home`-free since Wave 3a."""

    def __init__(self):
        self.saved: list[str] = []

    async def pending(self):
        return []

    async def save(self, raw):
        raise AssertionError("a non-owner must never reach the draft queue")

    async def delete(self, kind, name):
        raise AssertionError("a non-owner must never reach the draft queue")


def _service(**kw):
    resets = kw.pop("resets")

    async def on_change(owner_id: int):
        resets.append(owner_id)

    return ExtensionAdminService(
        Path("."), catalog=kw.pop("catalog", FakeCatalog()), store=kw.pop("store", FakeStore()),
        drafts=FakeDrafts(), secrets=_Secrets(), extensions=kw.pop("extensions", None),
        installer=None, probe_fn=None, on_change=on_change, **kw)


class _Secrets:
    async def load(self, *, owner_id: int):
        return {}

    async def put(self, name, value, *, owner_id: int):
        pass

    async def forget(self, name, *, owner_id: int):
        pass


class FakeExtensions:
    def __init__(self, changed=True):
        self.changed = changed
        self.calls: list[str] = []

    async def enable(self, name, *, owner_id: int):
        self.calls.append("enable")
        return self.changed

    async def disable(self, name, *, owner_id: int):
        self.calls.append("disable")
        return self.changed


async def test_a_non_owner_is_refused_before_anything_changes():
    resets: list[int] = []
    svc = _service(resets=resets)
    out = await svc.handle("draft", name="x", manifest="whatever", is_owner=False,
                           owner_id=7)
    assert "Only the owner" in out and "Connectors page" in out
    assert resets == []


async def test_reads_do_not_need_the_owner():
    """`catalog`, `list` and `drafts` are how the agent finds out what exists; gating
    them would make the tool useless to an allowlisted user for no gain."""
    svc = _service(resets=[])
    assert await svc.handle("list", is_owner=False, owner_id=7) == (
        "Nothing installed. Use action='catalog' to see what's available.")
    assert await svc.handle("catalog", is_owner=False, owner_id=7) == "The catalog is empty."
    assert await svc.handle("drafts", is_owner=False, owner_id=7) == (
        "No drafts are waiting for approval.")


async def test_enable_resets_the_prompt_cache_only_when_the_row_changed():
    resets: list[int] = []
    ext = FakeExtensions(changed=False)
    svc = _service(resets=resets, extensions=ext)
    assert await svc.handle("enable", name="srv", is_owner=True,
                            owner_id=1) == "'srv' is not installed."
    assert resets == []

    resets.clear()
    svc = _service(resets=resets, extensions=FakeExtensions(changed=True))
    out = await svc.handle("enable", name="srv", is_owner=True, owner_id=1)
    assert out.startswith("Enabled 'srv'.")
    # The owner the reset names, not a bare "something changed": a connector one
    # person enables must not cost anybody else their cached prefix.
    assert resets == [1]


async def test_an_unknown_name_says_why_installing_from_chat_is_refused():
    """The wording is prompt-cache text: it tells the model not to try to pass a command
    or a URL, which is the whole reason the catalog exists."""
    svc = _service(resets=[])
    out = await svc.handle("add", name="ghost", is_owner=True, owner_id=1)
    assert "is not in the catalog" in out and "refused" in out
