# FatSecret Nutrition Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** claude-hermes gains nutrition tracking as a catalog extension — four action-dispatch tools instead of twenty, four skills carrying the procedure, one credential-free chart script — and the standalone `nutrition_agent` bot is retired.

**Architecture:** The FatSecret MCP server stays an MCP server; it is repackaged so `uvx --from git+…@<sha> fatsecret-mcp` can launch it, and its 20 thin tools collapse into `fatsecret_food` / `fatsecret_diary` / `fatsecret_exercise` / `fatsecret_weight`, each dispatching on a required `action` enum. Credentials reach only that subprocess, via a hermes catalog manifest with four `auth` entries. Procedural knowledge (serving semantics, confirm-before-write, report format) moves into seeded skills; the one Bash-callable script renders a chart from JSON on stdin and never sees a credential.

**Tech Stack:** Python 3.12, FastMCP, `requests-oauthlib` (OAuth 1.0), pytest, matplotlib (chart entry point only), `uv`/`uvx` (already in the hermes image, `Dockerfile:14`).

Spec: `docs/superpowers/specs/2026-07-25-fatsecret-nutrition-integration-design.md`

**Two repositories.** Tasks 1–6 are in `D:/projects/fatsecret_mcp` (remote `https://github.com/hbs-1991/fitmeai.git`, branch `main`, currently clean at `8be7105`). Tasks 7–10 are in `D:/projects/claude-hermes`. Task 6 produces the release sha that Task 7 consumes — do not start Task 7 before Task 6 is pushed.

## Global Constraints

- **Credential boundary:** anything needing FatSecret credentials runs inside the MCP server subprocess. Skills carry procedure. Scripts do local computation and never read a secret, never call the API.
- **Docstring budget:** the four tools' combined docstrings must stay ≤ 4800 characters (~1.2k tokens), down from 18,522 today. Enforced by a test, not by intention.
- **Manifest rules** (`src/claude_hermes/extensions/catalog.py:211`): `command` must be one of `npx`/`uvx`/`python`; `args` a list of strings, never a shell string; every `${VAR}` in `args`/`env` must be declared as an `auth[].name`.
- **Version pinning:** the manifest references a full 40-hex commit sha, never a branch or tag. A moving ref means the agent silently gets different code.
- **Env var names are fixed by existing code** (`src/fatsecret_mcp/config.py:17-18`, `auth/oauth_manager.py:48-49`): `FATSECRET_CLIENT_ID`, `FATSECRET_CLIENT_SECRET`, `FATSECRET_ACCESS_TOKEN`, `FATSECRET_ACCESS_SECRET`.
- **Skill descriptions:** one sentence, ends with a period, **≤60 characters including the period** (`skills/skill-authoring/SKILL.md:43`). Longer is silently truncated and never routes.
- **Error style:** tools return `{"error": "..."}` dicts, never raise — matches the existing handlers (`diary_tools.py:85-90`) and keeps one dead call from ending the turn.

---

### Task 1: Packaging — make the server installable from anywhere

**Repo:** `fatsecret_mcp`

**Files:**
- Modify: `pyproject.toml`
- Create: `src/fatsecret_mcp/__main__.py`
- Test: `tests/test_packaging.py`

**Interfaces:**
- Produces: console script `fatsecret-mcp` → `fatsecret_mcp.__main__:main`; importable `fatsecret_mcp` package with no `src.` prefix anywhere.

- [ ] **Step 1: Write the failing test** — create `tests/test_packaging.py`:

```python
"""The packaging contract: this is what lets `uvx --from git+... fatsecret-mcp` work."""
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _pyproject() -> dict:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def test_build_backend_is_a_real_backend():
    """`setuptools.build_backend` does not exist; the name is `setuptools.build_meta`.
    With the typo, every `pip install .` and every uvx launch fails at build time."""
    assert _pyproject()["build-system"]["build-backend"] == "setuptools.build_meta"


def test_console_script_entry_points_exist():
    scripts = _pyproject()["project"]["scripts"]
    assert scripts["fatsecret-mcp"] == "fatsecret_mcp.__main__:main"
    assert scripts["fatsecret-chart"] == "fatsecret_mcp.cli.chart:main"


def test_runtime_dependencies_carry_no_bot_packages():
    """aiogram/openai/claude-agent-sdk belong to the retired bot; pyzbar imports only
    with libzbar0, which python:3.12-slim does not ship. Any of them in the runtime
    deps breaks the container install."""
    deps = " ".join(_pyproject()["project"]["dependencies"]).lower()
    for banned in ("aiogram", "openai", "claude-agent-sdk", "pyzbar", "pillow"):
        assert banned not in deps, f"{banned} must not be a runtime dependency"


def test_package_discovery_is_explicit():
    """Without this, setuptools auto-discovery sees src/, tests/ and nutrition_agent/
    and refuses to guess."""
    assert _pyproject()["tool"]["setuptools"]["packages"]["find"]["where"] == ["src"]


def test_entrypoint_module_imports_without_the_src_prefix():
    """`from src.fatsecret_mcp...` resolves only from the repo root. An installed
    package has no `src` top-level module at all."""
    body = (ROOT / "src" / "fatsecret_mcp" / "__main__.py").read_text(encoding="utf-8")
    assert "from src." not in body and "import src." not in body
```

- [ ] **Step 2: Run to verify it fails** — `python -m pytest tests/test_packaging.py -q`. Expected: FAIL (build backend typo, no `[project.scripts]`, bot deps present, no `__main__.py`).

- [ ] **Step 3: Rewrite `pyproject.toml`'s project and build sections:**

```toml
[project]
name = "fatsecret-mcp"
version = "0.2.0"
description = "Model Context Protocol server for FatSecret Platform API"
readme = "README.md"
requires-python = ">=3.10"
authors = [
    {name = "FatSecret MCP", email = "example@example.com"}
]
dependencies = [
    "fastmcp>=3.0.0b1",
    "requests>=2.31.0",
    "requests-oauthlib>=1.3.0",
    "pydantic>=2.5.0",
    "python-dotenv>=1.0.0",
]

[project.scripts]
fatsecret-mcp = "fatsecret_mcp.__main__:main"
fatsecret-chart = "fatsecret_mcp.cli.chart:main"

[project.optional-dependencies]
chart = ["matplotlib>=3.8.0"]
local = ["keyring>=24.0.0"]
dev = [
    "pytest>=7.4.0",
    "pytest-asyncio>=0.21.0",
    "black>=23.0.0",
    "ruff>=0.1.0",
    "responses>=0.24.0",
]

[build-system]
requires = ["setuptools>=61.0"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
where = ["src"]
```

Leave `[tool.black]`, `[tool.ruff]` and `[tool.pytest.ini_options]` exactly as they are.

- [ ] **Step 4: Guard the now-optional keyring import.** `src/fatsecret_mcp/auth/oauth_manager.py:5` does a bare `import keyring`, which now fails in a container. Replace that line with:

```python
try:
    import keyring
except ImportError:  # container install: env vars are the only token source
    keyring = None
```

and make the three keyring call sites tolerate its absence — in `store_tokens` (line ~34) and `clear_tokens` (line ~63) raise/return early, in `get_stored_tokens` skip the fallback:

```python
    def store_tokens(self, access_token: str, access_secret: str) -> None:
        """Store OAuth 1.0 access token + secret in keyring."""
        if keyring is None:
            raise TokenError("keyring is not installed; install the 'local' extra "
                             "to run the interactive OAuth setup")
        ...unchanged body...
```

```python
        # Keyring fallback (local dev)
        if keyring is None:
            return None, None
        try:
            ...unchanged body...
```

```python
    def clear_tokens(self) -> None:
        """Remove stored tokens from keyring."""
        if keyring is None:
            return
        ...unchanged body...
```

- [ ] **Step 5: Create `src/fatsecret_mcp/__main__.py`** — `main.py`'s logic with absolute imports:

```python
"""Console entry point: `fatsecret-mcp`.

Started as a child process by an MCP client (claude-hermes spawns it via uvx) and
speaks MCP over stdin/stdout. Not a network server: it listens on no port.
"""

from .server import create_server
from .auth import OAuthManager
from .utils import get_logger, ConfigurationError, AuthenticationError

logger = get_logger(__name__)


def main() -> None:
    logger.info("Starting FatSecret MCP Server (authenticated mode)")
    oauth = OAuthManager()
    access_token = oauth.get_valid_access_token()
    if not access_token:
        raise AuthenticationError(
            "No FatSecret access token. Set FATSECRET_ACCESS_TOKEN and "
            "FATSECRET_ACCESS_SECRET, or run setup_oauth.py on a machine with a browser."
        )
    create_server(access_token=access_token, server_name="FatSecret").run()


if __name__ == "__main__":
    main()
```

Then delete `main.py` and `main_noauth.py` from the repo root — `__main__.py` replaces both, and `main.py`'s `src.`-prefixed imports are exactly the defect this task removes. (`ConfigurationError` stays imported: `create_server` raises it, and the import keeps the symbol reachable for callers that catch it.)

- [ ] **Step 6: Run the packaging test, then prove a real install** —

```bash
python -m pytest tests/test_packaging.py -q
pip install .
fatsecret-mcp --help || true    # exits nonzero without creds; the point is that it RESOLVES
```

Expected: tests PASS; `pip install .` succeeds (it cannot today — the build backend name is invalid); the `fatsecret-mcp` command exists on PATH.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml src/fatsecret_mcp/__main__.py src/fatsecret_mcp/auth/oauth_manager.py tests/test_packaging.py
git rm main.py main_noauth.py
git commit -m "fix(packaging): real build backend, console entry points, server-only deps"
```

---

### Task 2: Action dispatch + `fatsecret_food`

**Repo:** `fatsecret_mcp`

**Files:**
- Create: `src/fatsecret_mcp/tools/dispatch.py`
- Create: `src/fatsecret_mcp/tools/food.py`
- Modify: `src/fatsecret_mcp/api/foods.py` (add the real barcode endpoint)
- Test: `tests/test_tool_food.py`

**Interfaces:**
- Produces: `dispatch.ActionError`; `dispatch.require(params: dict, action: str, *names: str) -> tuple`; `dispatch.guard(fn)` decorator turning `ActionError`/`APIError`/`Exception` into `{"error": str}`; `food.register_food_tool(mcp, client) -> None` registering one tool `fatsecret_food`; `FoodsAPI.find_id_for_barcode(barcode: str) -> str | None`.
- Consumes: `FoodsAPI.search(search_expression, max_results, page_number)`, `.get(food_id)`, `.create(...)`, `RecipesAPI.search(...)`, `.get(recipe_id)` — all already exist.

- [ ] **Step 1: Write the failing tests** — create `tests/test_tool_food.py`:

```python
"""fatsecret_food: one tool, six actions. Tests drive a fake API layer — no network."""
import pytest

from fatsecret_mcp.tools.dispatch import ActionError, require


class FakeMCP:
    """Captures what register_*_tool registers, so tests can call the function
    directly instead of standing up a real FastMCP server."""
    def __init__(self):
        self.tools = {}

    def tool(self, *a, **kw):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn
        return deco


class FakeFoods:
    def __init__(self):
        self.calls = []

    def search(self, search_expression, max_results=50, page_number=0):
        self.calls.append(("search", search_expression, max_results, page_number))
        return {"foods": [{"food_id": "1641", "food_name": search_expression}]}

    def get(self, food_id):
        self.calls.append(("get", food_id))
        return {"food_id": food_id, "servings": [{"serving_id": "50321",
                                                  "serving_description": "100 g"}]}

    def find_id_for_barcode(self, barcode):
        self.calls.append(("barcode", barcode))
        return "1641" if barcode == "0012000161155" else None


def build(monkeypatch, foods=None):
    from fatsecret_mcp.tools import food as food_mod
    mcp, fake = FakeMCP(), foods or FakeFoods()
    monkeypatch.setattr(food_mod, "FoodsAPI", lambda client: fake)
    monkeypatch.setattr(food_mod, "RecipesAPI", lambda client: fake)
    food_mod.register_food_tool(mcp, client=object())
    return mcp.tools["fatsecret_food"], fake


def test_require_names_every_missing_parameter_at_once():
    with pytest.raises(ActionError) as e:
        require({"a": "x", "b": None, "c": ""}, "add", "a", "b", "c")
    assert "b" in str(e.value) and "c" in str(e.value) and "add" in str(e.value)


def test_search_dispatches_to_the_api(monkeypatch):
    tool, fake = build(monkeypatch)
    out = tool(action="search", query="banana")
    assert fake.calls[0][:2] == ("search", "banana")
    assert "error" not in out


def test_an_unknown_action_returns_an_error_listing_the_valid_ones(monkeypatch):
    tool, _ = build(monkeypatch)
    out = tool(action="frobnicate")
    assert "frobnicate" in out["error"] and "search" in out["error"]


def test_a_missing_required_parameter_is_an_error_not_an_exception(monkeypatch):
    """P5: the model must get a correctable message, not a dead turn."""
    tool, _ = build(monkeypatch)
    out = tool(action="get")
    assert "food_id" in out["error"]


def test_barcode_uses_the_real_endpoint_then_fetches_the_food(monkeypatch):
    """The old fatsecret_food_barcode_scan just text-searched the digits
    (foods_tools.py:315) — it was never a barcode lookup."""
    tool, fake = build(monkeypatch)
    out = tool(action="barcode", barcode="0012000161155")
    assert ("barcode", "0012000161155") in fake.calls
    assert out["food_id"] == "1641"


def test_barcode_pads_a_short_upc_to_gtin13(monkeypatch):
    """FatSecret's find_id_for_barcode requires GTIN-13; a 12-digit UPC needs a
    leading zero or the lookup silently misses."""
    tool, fake = build(monkeypatch)
    tool(action="barcode", barcode="012000161155")
    assert ("barcode", "0012000161155") in fake.calls


def test_barcode_not_found_is_a_clean_message(monkeypatch):
    tool, _ = build(monkeypatch)
    out = tool(action="barcode", barcode="9999999999999")
    assert "error" in out and "9999999999999" in out["error"]


def test_the_dropped_v3_and_autocomplete_actions_are_gone(monkeypatch):
    tool, _ = build(monkeypatch)
    for dead in ("search_v3", "autocomplete"):
        assert "error" in tool(action=dead)
```

- [ ] **Step 2: Run to verify they fail** — `python -m pytest tests/test_tool_food.py -q`. Expected: FAIL (`fatsecret_mcp.tools.dispatch` does not exist).

- [ ] **Step 3: Create `src/fatsecret_mcp/tools/dispatch.py`:**

```python
"""Shared plumbing for action-dispatch tools.

Four fat tools replace twenty thin ones: every tool schema lives in the model's
cached prompt prefix and is paid for on every request, whether or not the turn is
about food. Actions cost one enum value; tools cost a whole schema.
"""

from __future__ import annotations

import functools

from ..utils import get_logger, APIError

logger = get_logger(__name__)


class ActionError(Exception):
    """A bad action name or a missing parameter — the caller's mistake, correctable."""


def require(params: dict, action: str, *names: str) -> tuple:
    """Return the named params, or raise naming EVERY missing one.

    All at once, not first-failure: a model that gets one name per round trip
    spends four turns discovering four required fields.
    """
    missing = [n for n in names if params.get(n) in (None, "")]
    if missing:
        raise ActionError(f"action={action!r} requires: {', '.join(missing)}")
    return tuple(params[n] for n in names)


def unknown(action: str, valid: tuple[str, ...]) -> dict:
    return {"error": f"unknown action {action!r}; expected one of: {', '.join(valid)}"}


def guard(fn):
    """Every tool returns a dict, always. An exception crossing the MCP boundary
    reads to the model as an infrastructure failure it cannot fix; an error string
    reads as something to correct and retry."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except ActionError as e:
            return {"error": str(e)}
        except APIError as e:
            logger.error("FatSecret API error in %s: %s", fn.__name__, e)
            return {"error": f"FatSecret API error: {e}"}
        except Exception as e:  # noqa: BLE001 — the boundary is the point
            logger.exception("unexpected error in %s", fn.__name__)
            return {"error": f"Unexpected error: {e}"}
    return wrapper
```

- [ ] **Step 4: Add the real barcode endpoint** to `src/fatsecret_mcp/api/foods.py`, as a method on `FoodsAPI` (follow the file's existing `search`/`get` style for how it calls `self.client`):

```python
    def find_id_for_barcode(self, barcode: str) -> str | None:
        """FatSecret's food.find_id_for_barcode. Returns a food_id or None.

        The API requires GTIN-13: a 12-digit UPC-A must be zero-padded, an 8-digit
        EAN-8 likewise, or the lookup misses a product that is actually there.
        """
        gtin = barcode.strip().zfill(13)
        response = self.client.get("food.find_id_for_barcode", barcode=gtin)
        food_id = (response.get("food_id") or {}).get("value")
        return food_id or None
```

Note the call shape: `FatSecretClient.get` takes the method name positionally and API
parameters as **keywords** (`api/foods.py:105` — `self.client.get("food.get.v2",
food_id=food_id)`). Passing a dict would send it as one parameter named `params`.

- [ ] **Step 5: Create `src/fatsecret_mcp/tools/food.py`:**

```python
"""fatsecret_food — food and recipe lookup, one tool, six actions."""

from __future__ import annotations

from ..api.foods import FoodsAPI
from ..api.recipes import RecipesAPI
from ..utils import get_logger
from .dispatch import ActionError, guard, require, unknown

logger = get_logger(__name__)

ACTIONS = ("search", "get", "barcode", "create", "recipe_search", "recipe_get")


def _dump(value):
    """The API layer returns pydantic models (`FoodsAPI.get` → `Food`,
    models/food.py:58); MCP needs plain JSON-able data. model_dump() replaces the
    hand-written 25-line serializer the old foods_tools.py carried at lines 150-183."""
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if isinstance(value, list):
        return [_dump(v) for v in value]
    return value


def register_food_tool(mcp, client) -> None:
    foods = FoodsAPI(client)
    recipes = RecipesAPI(client)

    @mcp.tool()
    @guard
    def fatsecret_food(
        action: str,
        query: str | None = None,
        food_id: str | None = None,
        barcode: str | None = None,
        recipe_id: str | None = None,
        max_results: int = 20,
        page: int = 0,
        nutrition: dict | None = None,
    ) -> dict:
        """Look up foods and recipes in the FatSecret database.

        Actions:
          search        — find foods by name. Requires: query.
          get           — full nutrition and the serving list for one food.
                          Requires: food_id. Call this before logging: the serving
                          list is where serving_id comes from.
          barcode       — find a food by its GTIN/UPC/EAN digits. Requires: barcode.
          create        — define a custom food. Requires: query (the food name) and
                          nutrition, an object with serving_size, calories, fat,
                          carbohydrate, protein (extra nutrients optional).
          recipe_search — find recipes by name. Requires: query.
          recipe_get    — full recipe with ingredients. Requires: recipe_id.

        Returns the API payload, or {"error": "..."} — never raises.
        Serving semantics for logging are covered by the nutrition-log skill.
        """
        params = locals()
        if action == "search":
            (q,) = require(params, action, "query")
            return {"foods": _dump(foods.search(q, max_results=max_results,
                                                page_number=page))}
        if action == "get":
            (fid,) = require(params, action, "food_id")
            return _dump(foods.get(fid))
        if action == "barcode":
            (code,) = require(params, action, "barcode")
            found = foods.find_id_for_barcode(code)
            if not found:
                return {"error": f"no food found for barcode {code}; it may be an "
                                 f"unbranded item, or not in the FatSecret database"}
            return _dump(foods.get(found))
        if action == "create":
            name, n = require(params, action, "query", "nutrition")
            for field in ("serving_size", "calories", "fat", "carbohydrate", "protein"):
                if n.get(field) is None:
                    raise ActionError(f"action='create' needs nutrition.{field}")
            return _dump(foods.create(food_name=name, **n))
        if action == "recipe_search":
            (q,) = require(params, action, "query")
            return {"recipes": _dump(recipes.search(q, max_results=max_results,
                                                    page_number=page))}
        if action == "recipe_get":
            (rid,) = require(params, action, "recipe_id")
            return _dump(recipes.get(rid))
        return unknown(action, ACTIONS)

    logger.info("registered fatsecret_food")
```

`foods.create` takes `brand_name`, `brand_type` and `serving_size` as positional-ish keywords (`api/foods.py`); passing `**n` forwards whatever the model supplied. If a required-by-signature argument is absent the `ActionError` loop above catches the four that matter and `guard` catches the rest as a clean error.

- [ ] **Step 6: Run** — `python -m pytest tests/test_tool_food.py -q`. Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/fatsecret_mcp/tools/dispatch.py src/fatsecret_mcp/tools/food.py \
        src/fatsecret_mcp/api/foods.py tests/test_tool_food.py
git commit -m "feat(tools): fatsecret_food action dispatch; real barcode endpoint"
```

---

### Task 3: `fatsecret_diary`, including the computed `report` action

**Repo:** `fatsecret_mcp`

**Files:**
- Create: `src/fatsecret_mcp/tools/diary.py`
- Create: `src/fatsecret_mcp/report.py`
- Test: `tests/test_tool_diary.py`, `tests/test_report.py`

**Interfaces:**
- Produces: `diary.register_diary_tool(mcp, client) -> None` registering `fatsecret_diary`; `report.build(diary_api, start: str, end: str, target_calories: float | None) -> dict`.
- Consumes: `dispatch.require/guard/unknown` (Task 2); `FoodDiaryAPI.get_entries(entry_date)`, `.get_month(year, month)`, `.add_entry(food_id, serving_id, meal, number_of_units, entry_date, entry_name)`, `.edit_entry(food_entry_id, serving_id, number_of_units, meal)`, `.delete_entry(food_entry_id)`.

- [ ] **Step 1: Write the failing report tests** — create `tests/test_report.py`:

```python
"""report.build: the deterministic half. This is arithmetic the model must never do
token-by-token — a week of raw entries is ~30 verbose JSON rows to sum by hand."""
import pytest

from fatsecret_mcp.report import build


class Day:
    def __init__(self, d, cal, carb, prot, fat):
        self.date, self.total_calories = d, cal
        self.total_carbohydrate, self.total_protein, self.total_fat = carb, prot, fat


class Month:
    def __init__(self, year, month, days):
        self.year, self.month, self.days = year, month, days


class FakeDiary:
    def __init__(self, months):
        self._months, self.fetched = months, []

    def get_month(self, year, month):
        self.fetched.append((year, month))
        return self._months.get((year, month), Month(year, month, []))


WEEK = FakeDiary({(2026, 7): Month(2026, 7, [
    Day("2026-07-20", 2000, 200, 150, 60),
    Day("2026-07-21", 2200, 220, 150, 70),
    Day("2026-07-22", 1500, 150, 120, 45),
    Day("2026-07-99", 9999, 0, 0, 0),      # nonsense date: must be filtered out
])})


def test_only_days_inside_the_range_are_counted():
    out = build(WEEK, "2026-07-20", "2026-07-21", None)
    assert out["days_logged"] == 2
    assert out["totals"]["calories"] == 4200
    assert [r["date"] for r in out["rows"]] == ["2026-07-20", "2026-07-21"]


def test_averages_divide_by_days_logged_not_days_in_range():
    """Six days in the range, three logged. Dividing by 6 invents a deficit that
    is really just days the user forgot to log."""
    out = build(WEEK, "2026-07-20", "2026-07-25", None)
    assert out["days_logged"] == 3
    assert out["averages"]["calories"] == pytest.approx(1900.0)


def test_macro_ratios_are_percentages_of_calories_and_sum_to_100():
    out = build(WEEK, "2026-07-20", "2026-07-22", None)
    r = out["macro_ratio_pct"]
    assert sum(r.values()) == pytest.approx(100.0, abs=0.6)
    assert r["protein"] > r["fat"]


def test_a_target_flags_days_deviating_by_more_than_20_percent():
    out = build(WEEK, "2026-07-20", "2026-07-22", 2000.0)
    assert out["off_target_days"] == [{"date": "2026-07-22", "calories": 1500,
                                       "deviation_pct": -25.0}]


def test_no_target_means_no_deviation_keys():
    out = build(WEEK, "2026-07-20", "2026-07-22", None)
    assert "off_target_days" not in out


def test_a_range_spanning_two_months_fetches_each_month_once():
    """One API call per month, not per day — a 30-day report must not be 30 calls."""
    d = FakeDiary({})
    build(d, "2026-06-28", "2026-07-03", None)
    assert d.fetched == [(2026, 6), (2026, 7)]


def test_an_inverted_range_is_rejected():
    with pytest.raises(Exception, match="before"):
        build(WEEK, "2026-07-22", "2026-07-20", None)


def test_an_absurd_range_is_rejected():
    with pytest.raises(Exception, match="92"):
        build(WEEK, "2026-01-01", "2026-12-31", None)


def test_an_empty_range_reports_zero_days_without_dividing_by_zero():
    out = build(FakeDiary({}), "2026-07-20", "2026-07-22", 2000.0)
    assert out["days_logged"] == 0
    assert out["averages"]["calories"] == 0
```

- [ ] **Step 2: Run to verify they fail** — `python -m pytest tests/test_report.py -q`. Expected: FAIL (`fatsecret_mcp.report` does not exist).

- [ ] **Step 3: Create `src/fatsecret_mcp/report.py`:**

```python
"""Period aggregation over the food diary.

Server-side because it needs credentials, and compressed because raw diary JSON is
the single most expensive thing this integration could put in a model's context.
"""

from __future__ import annotations

from datetime import date

from .tools.dispatch import ActionError

MAX_RANGE_DAYS = 92
DEVIATION_THRESHOLD_PCT = 20.0
_KCAL_PER_G = {"protein": 4.0, "fat": 9.0, "carbohydrate": 4.0}


def _months_between(start: date, end: date) -> list[tuple[int, int]]:
    out, y, m = [], start.year, start.month
    while (y, m) <= (end.year, end.month):
        out.append((y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def build(diary_api, start: str, end: str, target_calories: float | None) -> dict:
    """Daily rows plus period totals, averages and macro ratios for [start, end].

    Fetches one month at a time (get_month returns per-day totals in a single call),
    never one call per day.
    """
    try:
        s, e = date.fromisoformat(start), date.fromisoformat(end)
    except ValueError as exc:
        raise ActionError(f"start and end must be YYYY-MM-DD: {exc}") from exc
    if e < s:
        raise ActionError(f"end {end} is before start {start}")
    if (e - s).days + 1 > MAX_RANGE_DAYS:
        raise ActionError(f"range is longer than {MAX_RANGE_DAYS} days; "
                          f"ask for a shorter period")

    rows: list[dict] = []
    for year, month in _months_between(s, e):
        for day in getattr(diary_api.get_month(year, month), "days", []):
            try:
                d = date.fromisoformat(day.date)
            except (ValueError, TypeError):
                continue          # the API has returned malformed dates; skip, don't crash
            if s <= d <= e:
                rows.append({
                    "date": day.date,
                    "calories": day.total_calories,
                    "protein": day.total_protein,
                    "fat": day.total_fat,
                    "carbohydrate": day.total_carbohydrate,
                })
    rows.sort(key=lambda r: r["date"])

    keys = ("calories", "protein", "fat", "carbohydrate")
    totals = {k: round(sum(r[k] or 0 for r in rows), 1) for k in keys}
    n = len(rows)
    # Averages divide by days LOGGED, not days in the range: an unlogged day is
    # missing data, not a zero-calorie day, and averaging it in invents a deficit.
    averages = {k: round(totals[k] / n, 1) if n else 0 for k in keys}

    macro_kcal = {m: totals[m] * f for m, f in _KCAL_PER_G.items()}
    total_macro_kcal = sum(macro_kcal.values())
    ratios = ({m: round(v / total_macro_kcal * 100, 1) for m, v in macro_kcal.items()}
              if total_macro_kcal else {m: 0.0 for m in _KCAL_PER_G})

    out = {
        "start": start, "end": end, "days_in_range": (e - s).days + 1,
        "days_logged": n, "rows": rows, "totals": totals, "averages": averages,
        "macro_ratio_pct": ratios,
    }
    if target_calories:
        out["target_calories"] = target_calories
        out["off_target_days"] = [
            {"date": r["date"], "calories": r["calories"],
             "deviation_pct": round((r["calories"] - target_calories)
                                    / target_calories * 100, 1)}
            for r in rows
            if abs((r["calories"] or 0) - target_calories) / target_calories * 100
            > DEVIATION_THRESHOLD_PCT
        ]
    return out
```

- [ ] **Step 4: Run the report tests** — `python -m pytest tests/test_report.py -q`. Expected: PASS.

- [ ] **Step 5: Write the failing diary-tool tests** — create `tests/test_tool_diary.py`:

```python
"""fatsecret_diary: six actions over the food diary."""
from tests.test_tool_food import FakeMCP


class FakeDiaryAPI:
    def __init__(self):
        self.calls = []

    def get_entries(self, entry_date):
        self.calls.append(("get_entries", entry_date))
        return type("D", (), {"date": entry_date, "entries": [], "total_calories": 0,
                              "total_carbohydrate": 0, "total_protein": 0,
                              "total_fat": 0})()

    def get_month(self, year, month):
        self.calls.append(("get_month", year, month))
        return type("M", (), {"year": year, "month": month, "days": []})()

    def add_entry(self, **kw):
        self.calls.append(("add_entry", kw))
        return "999"

    def edit_entry(self, **kw):
        self.calls.append(("edit_entry", kw))

    def delete_entry(self, food_entry_id):
        self.calls.append(("delete_entry", food_entry_id))


def build(monkeypatch):
    from fatsecret_mcp.tools import diary as diary_mod
    mcp, fake = FakeMCP(), FakeDiaryAPI()
    monkeypatch.setattr(diary_mod, "FoodDiaryAPI", lambda client: fake)
    diary_mod.register_diary_tool(mcp, client=object())
    return mcp.tools["fatsecret_diary"], fake


def test_get_defaults_to_today(monkeypatch):
    tool, fake = build(monkeypatch)
    tool(action="get")
    assert fake.calls[0][1] is not None


def test_add_requires_the_four_identifying_fields(monkeypatch):
    tool, _ = build(monkeypatch)
    out = tool(action="add", food_id="1641")
    for name in ("food_entry_name", "serving_id", "meal"):
        assert name in out["error"]


def test_add_rejects_an_invalid_meal_by_naming_the_valid_ones(monkeypatch):
    tool, _ = build(monkeypatch)
    out = tool(action="add", food_id="1", food_entry_name="x", serving_id="2",
               meal="brunch")
    assert "brunch" in out["error"] and "breakfast" in out["error"]


def test_add_lowercases_the_meal_and_defaults_units_to_one(monkeypatch):
    tool, fake = build(monkeypatch)
    tool(action="add", food_id="1", food_entry_name="x", serving_id="2", meal="Lunch")
    kw = fake.calls[0][1]
    assert kw["meal"] == "lunch" and kw["number_of_units"] == 1.0


def test_delete_requires_an_entry_id(monkeypatch):
    tool, _ = build(monkeypatch)
    assert "food_entry_id" in tool(action="delete")["error"]


def test_report_requires_both_range_ends(monkeypatch):
    tool, _ = build(monkeypatch)
    out = tool(action="report", start="2026-07-20")
    assert "end" in out["error"]


def test_report_returns_the_computed_shape(monkeypatch):
    tool, _ = build(monkeypatch)
    out = tool(action="report", start="2026-07-20", end="2026-07-22")
    assert set(out) >= {"days_logged", "totals", "averages", "macro_ratio_pct"}


def test_an_unknown_action_is_an_error(monkeypatch):
    tool, _ = build(monkeypatch)
    assert "error" in tool(action="summarise")
```

- [ ] **Step 6: Run to verify they fail** — `python -m pytest tests/test_tool_diary.py -q`. Expected: FAIL (module missing).

- [ ] **Step 7: Create `src/fatsecret_mcp/tools/diary.py`:**

```python
"""fatsecret_diary — read, write and summarize the food diary."""

from __future__ import annotations

from datetime import date

from ..api.food_diary import FoodDiaryAPI
from ..report import build as build_report
from ..utils import get_logger
from .dispatch import ActionError, guard, require, unknown

logger = get_logger(__name__)

ACTIONS = ("get", "month", "add", "edit", "delete", "report")
MEALS = ("breakfast", "lunch", "dinner", "other")


def _meal(value: str) -> str:
    if value.lower() not in MEALS:
        raise ActionError(f"meal {value!r} is not valid; use one of: {', '.join(MEALS)}")
    return value.lower()


def register_diary_tool(mcp, client) -> None:
    diary = FoodDiaryAPI(client)

    @mcp.tool()
    @guard
    def fatsecret_diary(
        action: str,
        entry_date: str | None = None,
        year: int | None = None,
        month: int | None = None,
        food_id: str | None = None,
        food_entry_name: str | None = None,
        serving_id: str | None = None,
        meal: str | None = None,
        number_of_units: float | None = None,
        food_entry_id: str | None = None,
        start: str | None = None,
        end: str | None = None,
        target_calories: float | None = None,
    ) -> dict:
        """Read, write and summarize the user's food diary.

        Actions:
          get     — entries and totals for one day. Optional: entry_date (today).
          month   — per-day totals for a month. Optional: year, month (current).
          add     — log a food. Requires: food_id, food_entry_name, serving_id, meal.
                    Optional: number_of_units (default 1.0), entry_date.
          edit    — change an entry. Requires: food_entry_id. Optional: serving_id,
                    number_of_units, meal.
          delete  — remove an entry. Requires: food_entry_id.
          report  — computed totals, averages, macro ratios and per-day rows for a
                    range. Requires: start, end (YYYY-MM-DD, ≤92 days). Optional:
                    target_calories, which adds days deviating over 20%.

        meal is one of: breakfast, lunch, dinner, other. Serving semantics — when
        number_of_units means grams — are in the nutrition-log skill; read it before
        logging. Returns {"error": "..."} rather than raising.
        """
        params = locals()
        if action == "get":
            when = entry_date or date.today().strftime("%Y-%m-%d")
            day = diary.get_entries(when)
            return {
                "date": day.date,
                "entries": [
                    {"food_entry_id": x.food_entry_id, "food_id": x.food_id,
                     "food_name": x.food_entry_name, "meal": x.meal,
                     "serving_id": x.serving_id, "number_of_units": x.number_of_units,
                     "calories": x.calories, "protein": x.protein, "fat": x.fat,
                     "carbohydrate": x.carbohydrate}
                    for x in day.entries
                ],
                "total_calories": day.total_calories,
                "total_protein": day.total_protein,
                "total_fat": day.total_fat,
                "total_carbohydrate": day.total_carbohydrate,
            }
        if action == "month":
            today = date.today()
            data = diary.get_month(year or today.year, month or today.month)
            return {"year": data.year, "month": data.month,
                    "days": [{"date": d.date, "calories": d.total_calories,
                              "protein": d.total_protein, "fat": d.total_fat,
                              "carbohydrate": d.total_carbohydrate}
                             for d in data.days]}
        if action == "add":
            fid, name, sid, ml = require(params, action, "food_id", "food_entry_name",
                                         "serving_id", "meal")
            entry_id = diary.add_entry(
                food_id=fid, serving_id=sid, meal=_meal(ml),
                number_of_units=1.0 if number_of_units is None else number_of_units,
                entry_date=entry_date, entry_name=name)
            return {"food_entry_id": entry_id, "message": f"added to {_meal(ml)}"}
        if action == "edit":
            (eid,) = require(params, action, "food_entry_id")
            diary.edit_entry(food_entry_id=eid, serving_id=serving_id,
                             number_of_units=number_of_units,
                             meal=_meal(meal) if meal else None)
            return {"success": True, "message": "diary entry updated"}
        if action == "delete":
            (eid,) = require(params, action, "food_entry_id")
            diary.delete_entry(eid)
            return {"success": True, "message": "diary entry deleted"}
        if action == "report":
            s, e = require(params, action, "start", "end")
            return build_report(diary, s, e, target_calories)
        return unknown(action, ACTIONS)

    logger.info("registered fatsecret_diary")
```

- [ ] **Step 8: Run both files** — `python -m pytest tests/test_tool_diary.py tests/test_report.py -q`. Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add src/fatsecret_mcp/tools/diary.py src/fatsecret_mcp/report.py \
        tests/test_tool_diary.py tests/test_report.py
git commit -m "feat(tools): fatsecret_diary with a server-side report action"
```

---

### Task 4: `fatsecret_exercise`, `fatsecret_weight`, and retiring the twenty

**Repo:** `fatsecret_mcp`

**Files:**
- Create: `src/fatsecret_mcp/tools/exercise.py`, `src/fatsecret_mcp/tools/weight.py`
- Modify: `src/fatsecret_mcp/server.py`, `src/fatsecret_mcp/tools/__init__.py`
- Delete: `src/fatsecret_mcp/tools/foods_tools.py`, `diary_tools.py`, `exercise_tools.py`, `weight_tools.py`
- Test: `tests/test_tool_exercise_weight.py`, `tests/test_prefix_budget.py`

**Interfaces:**
- Produces: `exercise.register_exercise_tool(mcp, client)`, `weight.register_weight_tool(mcp, client)`; `server.create_server` registering exactly four tools.
- Consumes: `ExerciseAPI.search/get_entries/get_month/add_entry/edit_entry`, `WeightAPI.update(weight_kg, entry_date, comment)` / `.get_month(year, month)`.

- [ ] **Step 1: Write the failing tests** — create `tests/test_tool_exercise_weight.py`:

```python
from tests.test_tool_food import FakeMCP


class FakeExercise:
    def __init__(self):
        self.calls = []

    def search(self, search_expression, max_results=50):
        self.calls.append(("search", search_expression)); return {"exercises": []}

    def get_entries(self, entry_date):
        self.calls.append(("get_entries", entry_date)); return {"entries": []}

    def get_month(self, year, month):
        self.calls.append(("get_month", year, month)); return {"days": []}

    def add_entry(self, exercise_id, minutes, entry_date=None):
        self.calls.append(("add_entry", exercise_id, minutes)); return "77"

    def edit_entry(self, exercise_entry_id, minutes):
        self.calls.append(("edit_entry", exercise_entry_id, minutes))


class FakeWeight:
    def __init__(self):
        self.calls = []

    def update(self, weight_kg, entry_date=None, comment=None):
        self.calls.append(("update", weight_kg, entry_date, comment))

    def get_month(self, year, month):
        self.calls.append(("get_month", year, month)); return {"days": []}


def build_ex(monkeypatch):
    from fatsecret_mcp.tools import exercise as mod
    mcp, fake = FakeMCP(), FakeExercise()
    monkeypatch.setattr(mod, "ExerciseAPI", lambda client: fake)
    mod.register_exercise_tool(mcp, client=object())
    return mcp.tools["fatsecret_exercise"], fake


def build_w(monkeypatch):
    from fatsecret_mcp.tools import weight as mod
    mcp, fake = FakeMCP(), FakeWeight()
    monkeypatch.setattr(mod, "WeightAPI", lambda client: fake)
    mod.register_weight_tool(mcp, client=object())
    return mcp.tools["fatsecret_weight"], fake


def test_exercise_add_requires_id_and_minutes(monkeypatch):
    tool, _ = build_ex(monkeypatch)
    out = tool(action="add")
    assert "exercise_id" in out["error"] and "minutes" in out["error"]


def test_exercise_search_dispatches(monkeypatch):
    tool, fake = build_ex(monkeypatch)
    tool(action="search", query="running")
    assert fake.calls == [("search", "running")]


def test_exercise_unknown_action(monkeypatch):
    tool, _ = build_ex(monkeypatch)
    assert "error" in tool(action="delete")


def test_weight_update_requires_a_weight(monkeypatch):
    tool, _ = build_w(monkeypatch)
    assert "weight_kg" in tool(action="update")["error"]


def test_weight_update_passes_the_comment_through(monkeypatch):
    tool, fake = build_w(monkeypatch)
    tool(action="update", weight_kg=82.4, comment="morning")
    assert fake.calls == [("update", 82.4, None, "morning")]


def test_weight_month_defaults_to_the_current_month(monkeypatch):
    tool, fake = build_w(monkeypatch)
    tool(action="month")
    assert fake.calls[0][0] == "get_month" and fake.calls[0][1] is not None
```

and `tests/test_prefix_budget.py`:

```python
"""The narrow waist, as a test.

Every tool docstring lives in the model's cached prompt prefix and is paid for on
every request, in every session, including cron passes that will never log a meal.
The twenty tools this replaced carried 18,522 characters. Nothing enforces the
saving except this assertion — docstrings grow back one helpful paragraph at a time.
"""
import ast
from pathlib import Path

BUDGET_CHARS = 4800          # ~1.2k tokens
SRC = Path(__file__).resolve().parents[1] / "src" / "fatsecret_mcp" / "tools"
TOOL_FILES = ("food.py", "diary.py", "exercise.py", "weight.py")


def _tool_docstrings():
    for name in TOOL_FILES:
        tree = ast.parse((SRC / name).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name.startswith("fatsecret_"):
                yield node.name, ast.get_docstring(node) or ""


def test_exactly_four_tools_are_exposed():
    assert sorted(n for n, _ in _tool_docstrings()) == [
        "fatsecret_diary", "fatsecret_exercise", "fatsecret_food", "fatsecret_weight"]


def test_combined_docstrings_stay_within_the_prefix_budget():
    total = sum(len(d) for _, d in _tool_docstrings())
    assert total <= BUDGET_CHARS, (
        f"tool docstrings total {total} chars, budget is {BUDGET_CHARS}. "
        f"Move procedural detail into a skill instead of the schema.")


def test_every_tool_documents_its_actions():
    for name, doc in _tool_docstrings():
        assert "Actions:" in doc, f"{name} must list its actions"
```

- [ ] **Step 2: Run to verify they fail** — `python -m pytest tests/test_tool_exercise_weight.py tests/test_prefix_budget.py -q`. Expected: FAIL (modules missing).

- [ ] **Step 3: Create `src/fatsecret_mcp/tools/exercise.py`:**

```python
"""fatsecret_exercise — exercise lookup and the activity log."""

from __future__ import annotations

from datetime import date

from ..api.exercise import ExerciseAPI
from ..utils import get_logger
from .dispatch import guard, require, unknown

logger = get_logger(__name__)

ACTIONS = ("search", "get", "month", "add", "edit")


def register_exercise_tool(mcp, client) -> None:
    api = ExerciseAPI(client)

    @mcp.tool()
    @guard
    def fatsecret_exercise(
        action: str,
        query: str | None = None,
        entry_date: str | None = None,
        year: int | None = None,
        month: int | None = None,
        exercise_id: str | None = None,
        minutes: float | None = None,
        exercise_entry_id: str | None = None,
        max_results: int = 20,
    ) -> dict:
        """Look up exercises and manage the activity log.

        Actions:
          search — find exercises by name. Requires: query.
          get    — entries for one day. Optional: entry_date (today).
          month  — per-day totals for a month. Optional: year, month (current).
          add    — log activity. Requires: exercise_id, minutes. Optional: entry_date.
          edit   — change duration. Requires: exercise_entry_id, minutes.

        FatSecret's day is fully allocated: adding an exercise reduces the minutes
        credited to the default resting activity. Returns {"error": "..."} on failure.
        """
        params = locals()
        if action == "search":
            (q,) = require(params, action, "query")
            return {"exercises": api.search(q, max_results=max_results)}
        if action == "get":
            return {"entries": api.get_entries(
                entry_date or date.today().strftime("%Y-%m-%d"))}
        if action == "month":
            today = date.today()
            return {"month": api.get_month(year or today.year, month or today.month)}
        if action == "add":
            eid, mins = require(params, action, "exercise_id", "minutes")
            return {"exercise_entry_id": api.add_entry(
                exercise_id=eid, minutes=mins, entry_date=entry_date),
                "message": f"logged {mins} minutes"}
        if action == "edit":
            eid, mins = require(params, action, "exercise_entry_id", "minutes")
            api.edit_entry(exercise_entry_id=eid, minutes=mins)
            return {"success": True, "message": "exercise entry updated"}
        return unknown(action, ACTIONS)

    logger.info("registered fatsecret_exercise")
```

- [ ] **Step 4: Create `src/fatsecret_mcp/tools/weight.py`:**

```python
"""fatsecret_weight — weight log."""

from __future__ import annotations

from datetime import date

from ..api.weight import WeightAPI
from ..utils import get_logger
from .dispatch import guard, require, unknown

logger = get_logger(__name__)

ACTIONS = ("update", "month")


def register_weight_tool(mcp, client) -> None:
    api = WeightAPI(client)

    @mcp.tool()
    @guard
    def fatsecret_weight(
        action: str,
        weight_kg: float | None = None,
        entry_date: str | None = None,
        comment: str | None = None,
        year: int | None = None,
        month: int | None = None,
    ) -> dict:
        """Record and read the user's weight history.

        Actions:
          update — set the weight for a date. Requires: weight_kg (kilograms).
                   Optional: entry_date (today), comment.
          month  — weight entries for a month. Optional: year, month (current).

        Returns {"error": "..."} on failure rather than raising.
        """
        params = locals()
        if action == "update":
            (kg,) = require(params, action, "weight_kg")
            api.update(kg, entry_date, comment)
            return {"success": True, "message": f"weight set to {kg} kg"}
        if action == "month":
            today = date.today()
            return {"month": api.get_month(year or today.year, month or today.month)}
        return unknown(action, ACTIONS)

    logger.info("registered fatsecret_weight")
```

- [ ] **Step 5: Rewrite `create_server` in `src/fatsecret_mcp/server.py`** — replace the four `register_*_tools` imports and calls:

```python
from .tools.food import register_food_tool
from .tools.diary import register_diary_tool
from .tools.exercise import register_exercise_tool
from .tools.weight import register_weight_tool
```

and the registration block (currently `server.py:52-60`):

```python
    # Food lookup works on the app credentials alone; the diary, exercise and weight
    # tools need a user access token, so they are only registered when one is present.
    register_food_tool(mcp, client)
    if access_token:
        register_diary_tool(mcp, client)
        register_exercise_tool(mcp, client)
        register_weight_tool(mcp, client)
        logger.info("Registered authenticated tools (diary, exercise, weight)")
```

Then rewrite `src/fatsecret_mcp/tools/__init__.py`, which currently re-exports all four
deleted `register_*_tools` functions by name and would raise `ImportError` on import:

```python
"""MCP tool implementations for the FatSecret API.

Four action-dispatch tools, not twenty thin ones: every tool schema lives in the
model's cached prompt prefix and is paid for on every request.
"""

from .food import register_food_tool
from .diary import register_diary_tool
from .exercise import register_exercise_tool
from .weight import register_weight_tool

__all__ = [
    "register_food_tool",
    "register_diary_tool",
    "register_exercise_tool",
    "register_weight_tool",
]
```

Then delete the four old modules:

```bash
git rm src/fatsecret_mcp/tools/foods_tools.py src/fatsecret_mcp/tools/diary_tools.py \
       src/fatsecret_mcp/tools/exercise_tools.py src/fatsecret_mcp/tools/weight_tools.py
```

- [ ] **Step 6: Run the full suite** — `python -m pytest -q`. Expected: the new tool/report/budget tests PASS. The bot tests (`test_agent.py`, `test_barcode.py`, `test_session_manager.py`, `test_whisper.py`, `test_markdown_html.py`, `test_config.py`) may already fail on the trimmed dependencies — that is expected and Task 6 removes them. Note which fail; do not fix them here.

- [ ] **Step 7: Commit**

```bash
git add -A src/fatsecret_mcp tests/test_tool_exercise_weight.py tests/test_prefix_budget.py
git commit -m "feat(tools): fatsecret_exercise and fatsecret_weight; retire the 20 thin tools"
```

---

### Task 5: `fatsecret-chart` — the one script, credential-free

**Repo:** `fatsecret_mcp`

**Files:**
- Create: `src/fatsecret_mcp/cli/__init__.py`, `src/fatsecret_mcp/cli/chart.py`
- Test: `tests/test_chart.py`

**Interfaces:**
- Produces: console script `fatsecret-chart`; `chart.render(series: list[dict], out_path: str, title: str, ylabel: str) -> str` returning the written path; `chart.main() -> int`.
- Input contract: JSON on stdin, a list of `{"date": "YYYY-MM-DD", "value": <number>}`. Nothing else. No API call, no credential, no network.

- [ ] **Step 1: Write the failing test** — create `tests/test_chart.py`:

```python
"""fatsecret-chart: JSON in, PNG out. The credential-free half of the design —
if this ever needs a FatSecret key, it belongs in the MCP server instead."""
import json
import subprocess
import sys

import pytest

matplotlib = pytest.importorskip("matplotlib")

from fatsecret_mcp.cli.chart import render

SERIES = [{"date": "2026-07-20", "value": 82.4},
          {"date": "2026-07-21", "value": 82.1},
          {"date": "2026-07-22", "value": 81.7}]


def test_render_writes_a_png(tmp_path):
    out = render(SERIES, str(tmp_path / "w.png"), "Weight", "kg")
    assert out.endswith("w.png")
    assert (tmp_path / "w.png").stat().st_size > 1000


def test_render_rejects_an_empty_series(tmp_path):
    with pytest.raises(ValueError, match="empty"):
        render([], str(tmp_path / "w.png"), "Weight", "kg")


def test_render_rejects_a_malformed_point(tmp_path):
    with pytest.raises(ValueError, match="date"):
        render([{"day": "2026-07-20", "value": 1}], str(tmp_path / "w.png"), "", "")


def test_the_cli_reads_stdin_and_prints_the_path(tmp_path):
    """Verbatim invocation shape the nutrition-analysis skill uses."""
    dest = tmp_path / "chart.png"
    proc = subprocess.run(
        [sys.executable, "-m", "fatsecret_mcp.cli.chart", "--out", str(dest),
         "--title", "Weight", "--ylabel", "kg"],
        input=json.dumps(SERIES), text=True, capture_output=True, check=True)
    assert proc.stdout.strip() == str(dest)
    assert dest.exists()


def test_the_module_never_imports_the_api_or_config_layer():
    """A credential-free script is only credential-free while nobody imports the
    thing that reads credentials."""
    body = (__import__("pathlib").Path(__file__).resolve().parents[1]
            / "src" / "fatsecret_mcp" / "cli" / "chart.py").read_text(encoding="utf-8")
    for banned in ("from ..api", "from ..config", "from ..auth", "os.environ"):
        assert banned not in body
```

- [ ] **Step 2: Run to verify it fails** — `python -m pytest tests/test_chart.py -q`. Expected: FAIL (module missing) or SKIP if matplotlib is absent — install it first with `pip install -e ".[chart,dev]"`.

- [ ] **Step 3: Create `src/fatsecret_mcp/cli/__init__.py`** (empty file) **and `src/fatsecret_mcp/cli/chart.py`:**

```python
"""Render a dated series to a PNG. Reads JSON on stdin, prints the output path.

Deliberately knows nothing about FatSecret: no API client, no config, no
environment. It is reachable from the agent's Bash, and everything reachable from
Bash is assumed readable by the agent — so it is given nothing worth reading.

    echo '[{"date":"2026-07-20","value":82.4}]' \\
      | fatsecret-chart --out /tmp/w.png --title Weight --ylabel kg
"""

from __future__ import annotations

import argparse
import json
import sys

import matplotlib
matplotlib.use("Agg")          # no display in a container; must precede pyplot
import matplotlib.pyplot as plt  # noqa: E402


def render(series: list[dict], out_path: str, title: str = "", ylabel: str = "") -> str:
    if not series:
        raise ValueError("series is empty; nothing to plot")
    try:
        labels = [str(p["date"]) for p in series]
        values = [float(p["value"]) for p in series]
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(
            f"each point needs a 'date' and a numeric 'value': {exc}") from exc

    fig, ax = plt.subplots(figsize=(9, 4))
    ax.plot(labels, values, marker="o", linewidth=2)
    ax.set_title(title)
    ax.set_ylabel(ylabel)
    ax.grid(True, alpha=0.3)
    # Dense date labels overlap into mush past ~10 points; thin them, keep the last.
    step = max(1, len(labels) // 10)
    ax.set_xticks(range(0, len(labels), step))
    ax.set_xticklabels(labels[::step], rotation=45, ha="right")
    fig.tight_layout()
    fig.savefig(out_path, dpi=120)
    plt.close(fig)
    return out_path


def main() -> int:
    ap = argparse.ArgumentParser(description="Render a dated JSON series to a PNG.")
    ap.add_argument("--out", required=True, help="output PNG path")
    ap.add_argument("--title", default="")
    ap.add_argument("--ylabel", default="")
    args = ap.parse_args()
    try:
        series = json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        print(f"stdin is not valid JSON: {exc}", file=sys.stderr)
        return 2
    try:
        print(render(series, args.out, args.title, args.ylabel))
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run** — `python -m pytest tests/test_chart.py -q`. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/fatsecret_mcp/cli tests/test_chart.py
git commit -m "feat(cli): fatsecret-chart renders a dated series to PNG"
```

---

### Task 6: Retire the bot, verify the uvx launch, tag the release

**Repo:** `fatsecret_mcp`

**Files:**
- Delete: `nutrition_agent/`, `run_bot.py`, `sessions.json`, `export_tokens.py`, `tests/test_agent.py`, `tests/test_barcode.py`, `tests/test_config.py`, `tests/test_markdown_html.py`, `tests/test_session_manager.py`, `tests/test_whisper.py`, `.claude/skills/`
- Modify: `README.md`, `CLAUDE.md`
- Check: `tests/test_integration.py` — keep it if it exercises the server, delete it if it imports `nutrition_agent`

**Interfaces:**
- Produces: **the 40-hex release sha**, which Task 7 pastes into the manifest. Record it in this task's completion note.

- [ ] **Step 1: Delete the bot and its tests.** Everything here is superseded by claude-hermes — sessions, voice, photo intake and memory are the gateway's job now.

```bash
git rm -r nutrition_agent .claude/skills
git rm run_bot.py sessions.json export_tokens.py
git rm tests/test_agent.py tests/test_barcode.py tests/test_config.py \
       tests/test_markdown_html.py tests/test_session_manager.py tests/test_whisper.py
grep -rn "nutrition_agent" tests/test_integration.py && git rm tests/test_integration.py || true
```

The eight skills under `.claude/skills/` are not lost — Tasks 8 and 9 rewrite them into `claude-hermes/skills/`. Keep `setup_oauth.py`: it is the one-time browser flow that produces the token pair, and it must still run on a host with a browser.

- [ ] **Step 2: Confirm nothing still imports the deleted packages**

```bash
grep -rn "nutrition_agent\|from src\.\|import src\." --include=*.py . && echo "STILL REFERENCED" || echo "clean"
```

Expected: `clean`. If `setup_oauth.py` uses `from src.fatsecret_mcp…`, change it to `from fatsecret_mcp…` (it runs after `pip install -e .`).

- [ ] **Step 3: Rewrite the `## Available Tools` section of `README.md`** to describe the four action tools instead of the old twenty, and note that the diary/exercise/weight tools appear only when `FATSECRET_ACCESS_TOKEN`/`FATSECRET_ACCESS_SECRET` are set. Replace the Claude Desktop config block with the uvx form:

````markdown
## Available Tools

Four action-dispatch tools. `fatsecret_food` works on app credentials alone; the
other three appear only when a user access token is present.

| Tool | Actions |
|---|---|
| `fatsecret_food` | `search`, `get`, `barcode`, `create`, `recipe_search`, `recipe_get` |
| `fatsecret_diary` | `get`, `month`, `add`, `edit`, `delete`, `report` |
| `fatsecret_exercise` | `search`, `get`, `month`, `add`, `edit` |
| `fatsecret_weight` | `update`, `month` |

Plus one console script, `fatsecret-chart`: JSON series on stdin, PNG out, no
credentials.

## Running it

```bash
uvx --from git+https://github.com/hbs-1991/fitmeai@<sha> fatsecret-mcp
```

with `FATSECRET_CLIENT_ID`, `FATSECRET_CLIENT_SECRET`, `FATSECRET_ACCESS_TOKEN`
and `FATSECRET_ACCESS_SECRET` in the environment.
````

Also strip `CLAUDE.md` down to the server: the nutrition-agent persona in it now lives in the claude-hermes persona and skills.

- [ ] **Step 4: Run the suite** — `python -m pytest -q`. Expected: PASS, with only the server tests remaining (`test_packaging`, `test_tool_food`, `test_tool_diary`, `test_report`, `test_tool_exercise_weight`, `test_prefix_budget`, `test_chart`).

- [ ] **Step 5: Commit and push**

```bash
git add -A
git commit -m "chore: retire nutrition_agent bot; claude-hermes replaces it"
git push origin main
```

- [ ] **Step 6: Verify the real launch path end to end.** This is the step that proves the whole distribution decision — do not skip it, and do not substitute a local `pip install`:

```bash
git rev-parse HEAD                       # ← record this 40-hex sha for Task 7
uvx --from "git+https://github.com/hbs-1991/fitmeai@$(git rev-parse HEAD)" \
    fatsecret-mcp
```

Expected: uv clones, builds and starts the process. Without credentials it exits with the `AuthenticationError` from `__main__.py` — that is success for this step, because it proves the package resolved, built and ran. A build or entry-point error is a real failure; go back to Task 1.

- [ ] **Step 7: Verify the chart entry point through uvx too**, since it needs the `chart` extra:

```bash
echo '[{"date":"2026-07-20","value":82.4},{"date":"2026-07-21","value":82.1}]' \
  | uvx --from "fatsecret-mcp[chart] @ git+https://github.com/hbs-1991/fitmeai@$(git rev-parse HEAD)" \
        fatsecret-chart --out /tmp/w.png --title Weight --ylabel kg
```

Expected: prints `/tmp/w.png`. If uv rejects the PEP 508 extras syntax, use the fallback and record which form worked — the skill in Task 9 must quote the verbatim command that actually ran:

```bash
uvx --with matplotlib --from "git+https://github.com/hbs-1991/fitmeai@<sha>" fatsecret-chart ...
```

- [ ] **Step 8: Record the sha** in this task's completion note, in the form `FATSECRET_SHA=<40 hex chars>`. Tasks 7 and 9 both need it.

---

### Task 7: The hermes catalog manifest

**Repo:** `claude-hermes`

**Files:**
- Create: `catalog/mcp/fatsecret.yaml`
- Test: `tests/test_catalog.py:114` (extend `test_the_shipped_catalog_is_valid`)

**Interfaces:**
- Consumes: `FATSECRET_SHA` from Task 6.
- Produces: catalog entry `fatsecret`, installable via `ext_manage(action="add", name="fatsecret")`.

- [ ] **Step 1: Extend the failing test** in `tests/test_catalog.py` — add `"fatsecret"` to the existing assertion at line 124-125 and append a dedicated test:

```python
def test_the_fatsecret_entry_pins_an_immutable_ref(tmp_path, monkeypatch):
    """A branch name here means the agent silently gets different code one day.
    Manifest validation does not enforce this for stdio args — this test does."""
    import re
    monkeypatch.setenv("CH_HOME", str(tmp_path))
    m = catalog.load_all()["fatsecret"]
    assert m.command == "uvx"
    ref = next(a for a in m.args if a.startswith("git+"))
    assert re.search(r"@[0-9a-f]{40}$", ref), f"pin a 40-hex sha, got {ref!r}"
    assert set(m.secret_names()) == {
        "FATSECRET_CLIENT_ID", "FATSECRET_CLIENT_SECRET",
        "FATSECRET_ACCESS_TOKEN", "FATSECRET_ACCESS_SECRET"}
    assert set(m.scopes) == {"turn", "cron"}
```

- [ ] **Step 2: Run to verify it fails** — `python -m pytest tests/test_catalog.py -q`. Expected: FAIL (`KeyError: 'fatsecret'`).

- [ ] **Step 3: Create `catalog/mcp/fatsecret.yaml`**, substituting the Task 6 sha for `<FATSECRET_SHA>`:

```yaml
manifest_version: 1
name: fatsecret
label: FatSecret
description: >
  Nutrition tracking against the FatSecret Platform API — food and recipe lookup,
  food diary, exercise log and weight history, as four action-dispatch tools.
  The diary tool computes period reports server-side, so a week of meals reaches
  the model as one table rather than thirty rows of JSON.
version: 0.2.0
transport: stdio
command: uvx
args:
  - "--from"
  - "git+https://github.com/hbs-1991/fitmeai@<FATSECRET_SHA>"
  - "fatsecret-mcp"
scopes: [turn, cron]
env:
  FATSECRET_CLIENT_ID: "${FATSECRET_CLIENT_ID}"
  FATSECRET_CLIENT_SECRET: "${FATSECRET_CLIENT_SECRET}"
  FATSECRET_ACCESS_TOKEN: "${FATSECRET_ACCESS_TOKEN}"
  FATSECRET_ACCESS_SECRET: "${FATSECRET_ACCESS_SECRET}"
auth:
  - name: FATSECRET_CLIENT_ID
    prompt: FatSecret application Client ID from platform.fatsecret.com/api
  - name: FATSECRET_CLIENT_SECRET
    prompt: FatSecret application Client Secret
  - name: FATSECRET_ACCESS_TOKEN
    prompt: OAuth 1.0 access token (run setup_oauth.py on a machine with a browser)
  - name: FATSECRET_ACCESS_SECRET
    prompt: OAuth 1.0 access token secret (from the same setup_oauth.py run)
```

- [ ] **Step 4: Validate with the human-facing wrapper too**

```bash
python scripts/validate_manifest.py catalog/mcp/fatsecret.yaml
```

Expected: `PASS` followed by the rendered executable surface. This is the same `catalog.parse` the loader uses, so a pass here means the entry cannot land broken.

- [ ] **Step 5: Run the catalog suite, then the full suite** — `python -m pytest tests/test_catalog.py -q` then `python -m pytest -q`. Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add catalog/mcp/fatsecret.yaml tests/test_catalog.py
git commit -m "feat(catalog): fatsecret MCP extension pinned to a release sha"
```

---

### Task 8: The `nutrition-log` skill

**Repo:** `claude-hermes`

**Files:**
- Create: `skills/nutrition-log/SKILL.md`
- Test: `tests/test_bundled_skills.py`

**Interfaces:**
- Produces: a seeded skill directory; `tests/test_bundled_skills.py` enforcing the authoring standard on every bundled skill (Task 9's three skills are checked by the same test).

This skill gets written out in full because it carries facts extracted from source — the serving semantics at `fatsecret_mcp/src/fatsecret_mcp/tools/diary_tools.py:171-218`, which are being deleted from the tool docstring in Task 3 and must not be lost.

- [ ] **Step 1: Write the failing test** — create `tests/test_bundled_skills.py`:

```python
"""The authoring standard, enforced on the nutrition skills.

skills/skill-authoring/SKILL.md:43 — a description over 60 characters is silently
truncated in the index and the skill never routes. All eight originals in the
nutrition agent broke it, which is why these four are checked mechanically.

Scoped to NUTRITION, not to every bundled skill, and deliberately: the shipped
`catalog-manifest` skill carries a 287-character description and `skill-authoring`
has no `## Procedure` section and spells its heading `## When to use`. Both predate
this work. Widening these assertions would make this task's suite fail on files it
does not touch — fix them in their own change, with their routing re-checked.
"""
from pathlib import Path

import pytest
import yaml

SKILLS = Path(__file__).resolve().parents[1] / "skills"
NUTRITION = ("nutrition-log", "nutrition-analysis", "meal-planning", "training")


def _frontmatter(name: str) -> dict:
    text = (SKILLS / name / "SKILL.md").read_text(encoding="utf-8")
    assert text.startswith("---\n"), f"{name} has no frontmatter"
    return yaml.safe_load(text.split("---\n", 2)[1])


@pytest.mark.parametrize("name", NUTRITION)
def test_frontmatter_has_exactly_name_and_description(name):
    fm = _frontmatter(name)
    assert set(fm) == {"name", "description"}
    assert fm["name"] == name == name.lower()


@pytest.mark.parametrize("name", NUTRITION)
def test_description_is_one_sentence_within_60_characters(name):
    d = _frontmatter(name)["description"]
    assert len(d) <= 60, f"{len(d)} chars: {d!r}"
    assert d.endswith("."), f"must end with a period: {d!r}"
    assert d.count(".") == 1, f"one sentence only: {d!r}"


@pytest.mark.parametrize("name", NUTRITION)
def test_required_sections_appear_in_order(name):
    body = (SKILLS / name / "SKILL.md").read_text(encoding="utf-8")
    required = ["## When to Use", "## Procedure", "## Verification"]
    positions = [body.find(h) for h in required]
    missing = [h for h, p in zip(required, positions) if p < 0]
    assert not missing, f"{name} is missing {missing}"
    assert positions == sorted(positions), f"{name}: sections are out of order"


@pytest.mark.parametrize("name", NUTRITION)
def test_the_skill_is_actually_bundled(name):
    assert (SKILLS / name / "SKILL.md").is_file()
```

- [ ] **Step 2: Run to verify it fails** — `python -m pytest tests/test_bundled_skills.py -q`. Expected: FAIL — every parametrized case errors, because none of the four skill directories exist yet.

- [ ] **Step 3: Create `skills/nutrition-log/SKILL.md`:**

````markdown
---
name: nutrition-log
description: Log food to the FatSecret diary correctly.
---

# Nutrition Log

Logging a meal is three calls, not one: find the food, read its serving list, then
write the entry. The middle step is what makes the numbers right — `serving_id` and
the meaning of `number_of_units` both come from it, and guessing either silently
logs the wrong amount.

## When to Use

- The user says what they ate, in any form ("съел 200г курицы", a photo of a plate,
  a photo of a barcode).
- The user corrects something already logged ("это был не обед, а перекус").
- The user asks what is left for today against their target.

## Prerequisites

- The `fatsecret` extension is installed and enabled (`ext_manage` action `list`).
- The user's calorie and macro targets are in `USER.md`. If they are not, ask once
  and save them with `memory_save` — do not invent a target.

## How to Run

```
fatsecret_food(action="search", query="chicken breast")
fatsecret_food(action="get", food_id="1641")
fatsecret_diary(action="add", food_id="1641", food_entry_name="Куриная грудка",
                serving_id="50321", meal="lunch", number_of_units=200)
```

## Quick Reference

| Need | Call |
|---|---|
| Find a food by name | `fatsecret_food(action="search", query=…)` |
| Find a food by barcode | `fatsecret_food(action="barcode", barcode=…)` |
| Servings and nutrition | `fatsecret_food(action="get", food_id=…)` |
| Write the entry | `fatsecret_diary(action="add", …)` |
| Today so far | `fatsecret_diary(action="get")` |
| Fix an entry | `fatsecret_diary(action="edit", food_entry_id=…, …)` |
| Remove an entry | `fatsecret_diary(action="delete", food_entry_id=…)` |

`meal` is one of `breakfast`, `lunch`, `dinner`, `other`. There is no `snack` —
snacks go to `other`.

## Procedure

1. **Identify the food.** From text, from a photo, or from a barcode. For a barcode
   photo, read the digits printed under the bars and pass them to
   `fatsecret_food(action="barcode", …)` — a 12-digit UPC is fine, the tool pads it
   to GTIN-13. Done when you have one candidate `food_id`, or a short list to ask
   about.

2. **Read the serving list.** Call `fatsecret_food(action="get", food_id=…)`. Done
   when you can name the exact `serving_id` you intend to use and its description.

3. **Work out `number_of_units` — this is where logging goes wrong.**

   - **Gram-based serving** (description contains `g`, e.g. `"100 g"`): the unit is
     **one gram**. `number_of_units` is the number of GRAMS.
     200 g → `number_of_units=200`. **Not** `200/100 = 2`.
   - **Any other serving** (`"1 cup"`, `"1 breast"`, `"1 oz"`): `number_of_units` is
     the number of those servings. Two cups → `number_of_units=2`. Half a breast →
     `0.5`.

   When the user speaks in grams, prefer the gram serving from the list. Done when
   you can state the amount in the serving's own units.

4. **Confirm before writing.** Never write to the diary unprompted. Show the food,
   the serving, the resulting КБЖУ and the meal, then ask:

   ```
   Продукт: {название}
   Порция: {описание порции} × {количество}
   | Ккал | Белки | Жиры | Углеводы |
   |------|-------|------|----------|
   | {k}  | {p} г | {f} г | {c} г   |

   Добавить в {приём пищи}? (да/нет)
   ```

   Done when the user has said yes. A photo is not a yes.

5. **Write it, then close the loop.** Call `fatsecret_diary(action="add", …)`, then
   `fatsecret_diary(action="get")` and report the day's running total against the
   target from `USER.md`. Warn if the day is more than 20% off target. Done when the
   user knows both what was logged and where the day now stands.

## Pitfalls

- **Dividing grams by the serving size.** The single most common error. `"100 g"`
  does not mean "one unit is 100 grams" — it means the serving is measured in grams.
- **Logging without step 2.** A `serving_id` from an earlier food, or from memory,
  belongs to a different food and produces plausible, wrong numbers.
- **Silent multi-item logging.** "Съел омлет с тостом" is two entries; confirm both
  before writing either, not one at a time after the fact.
- **Inventing a target** when `USER.md` has none. Ask.
- **Treating a barcode miss as a dead end.** Unbranded and homemade food has no
  barcode; fall back to `action="search"`, or `action="create"` for a repeat
  homemade dish.

## Verification

The entry is logged correctly when `fatsecret_diary(action="get")` shows it under
the intended meal with the calories you quoted in the confirmation — not merely when
the `add` call returned a `food_entry_id`.
````

- [ ] **Step 4: Run** — `python -m pytest tests/test_bundled_skills.py -q`. Expected: every `nutrition-log` case PASSES; the twelve cases for the other three skills still FAIL until Task 9. That is the intended handoff, not a broken build.

- [ ] **Step 5: Commit**

```bash
git add skills/nutrition-log/SKILL.md tests/test_bundled_skills.py
git commit -m "feat(skills): nutrition-log carries the serving semantics out of the tool schema"
```

---

### Task 9: The remaining three skills

**Repo:** `claude-hermes`

**Files:**
- Create: `skills/nutrition-analysis/SKILL.md`, `skills/meal-planning/SKILL.md`, `skills/training/SKILL.md`

**Interfaces:**
- Consumes: `tests/test_bundled_skills.py` (Task 8) — it already parametrizes over every `skills/*/SKILL.md`, so these three are checked the moment they exist. Also consumes the verbatim `fatsecret-chart` command recorded in Task 6 Step 7.

The frontmatter below is verbatim and non-negotiable — the 60-character limit is the constraint that actually bites. The bodies follow the same section order as `nutrition-log` (`# Title`, intro, `## When to Use`, `## Prerequisites`, `## How to Run`, `## Quick Reference`, `## Procedure`, `## Pitfalls`, `## Verification`), and each numbered Procedure step must end with its own completion criterion. Target 100-200 lines each. Read `skills/skill-authoring/SKILL.md` before writing, and skim `skills/nutrition-log/SKILL.md` so all four read like one library.

- [ ] **Step 1: Create `skills/nutrition-analysis/SKILL.md`.** Frontmatter, verbatim:

```yaml
---
name: nutrition-analysis
description: Report on eating and weight over a period.
---
```

Required content:
- **Absorbs** the old `nutrition-analysis`, `weekly-report` and `weight-dynamics` skills.
- `## How to Run` shows `fatsecret_diary(action="report", start="2026-07-20", end="2026-07-26", target_calories=2000)` and states plainly that the arithmetic is already done server-side — the model formats, it does not recompute. Restating totals it summed by hand is the failure mode this skill exists to prevent.
- A `## Procedure` step for the weight series: `fatsecret_weight(action="month")`, reshape to `[{"date":…,"value":…}]`, then pipe to the chart with the **verbatim command recorded in Task 6 Step 7**, then deliver the PNG with `send_document`.
- The >20% deviation warning: `report` returns `off_target_days` when `target_calories` is passed; surface every one of them, do not summarize them away.
- `## Pitfalls`: averages divide by days **logged**, not days in range — say so, because a user who logged three of seven days has not eaten a 900-calorie average. Ranges are capped at 92 days. `report` needs the target passed in; it is in `USER.md`, not in FatSecret.
- `## Verification`: the report is done when every off-target day is named and the chart file has actually been delivered, not merely rendered.

- [ ] **Step 2: Create `skills/meal-planning/SKILL.md`.** Frontmatter, verbatim:

```yaml
---
name: meal-planning
description: Plan meals against the user's calorie targets.
---
```

Required content:
- **Absorbs** the old `daily-menu` and `meal-planner` skills.
- Plans start from what is already logged today (`fatsecret_diary(action="get")`) and the target in `USER.md` — never from a blank slate, or the plan double-counts breakfast.
- Uses `fatsecret_food(action="search")` / `action="recipe_search"` to ground each proposed item in a real `food_id`, so an accepted plan can be logged with `nutrition-log` without re-searching.
- Honors preferences and dislikes from `MEMORY.md`/`USER.md`; a new stated preference gets written back with `memory_save`.
- `## Pitfalls`: proposing foods that do not exist in FatSecret makes the plan unloggable; ignoring what is already eaten today produces impossible remainders.
- `## Verification`: the plan is done when every item has a real `food_id` and the day's planned total lands within 10% of the remaining target.

- [ ] **Step 3: Create `skills/training/SKILL.md`.** Frontmatter, verbatim:

```yaml
---
name: training
description: Log workouts and plan the next ones.
---
```

Required content:
- **Absorbs** the old `workout-analysis` and `workout-planner` skills.
- Logging: `fatsecret_exercise(action="search")` → `action="add"` with minutes, and the same confirm-before-write rule as `nutrition-log`.
- Reading back: `action="get"` for a day, `action="month"` for a period.
- The FatSecret quirk, stated explicitly: the day is fully allocated, so adding an exercise reduces the minutes credited to the default resting activity — a logged workout does not simply add calories on top.
- Planning uses the weight trend and `nutrition-analysis` output for context, and respects any injury or constraint recorded in `USER.md`.
- `## Verification`: the workout is logged when `action="get"` shows it for the intended date with the intended duration.

- [ ] **Step 4: Run the skill suite** — `python -m pytest tests/test_bundled_skills.py -q`. Expected: PASS, including `test_the_nutrition_skills_are_all_bundled`.

- [ ] **Step 5: Check each skill against the authoring checklist** in `skills/skill-authoring/SKILL.md:39-58` by hand — in particular "every command shown is VERBATIM from something that actually ran". Any command in these three that has not been run in Task 6 must be run now or removed.

- [ ] **Step 6: Run the full suite** — `python -m pytest -q`. Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add skills/nutrition-analysis skills/meal-planning skills/training
git commit -m "feat(skills): nutrition-analysis, meal-planning and training"
```

---

### Task 10: Wiki sync, state migration, deploy, live verification

**Repo:** `claude-hermes`

**Files:**
- Modify: `docs/llm-wiki/components/extensions.md` (the "Shipped catalog" section, currently lines 96-100)
- Modify (live volume, not the repo): `data/USER.md`, `data/MEMORY.md`
- No code changes

**Interfaces:**
- Consumes: the retired bot's `about_me.md` and `memory/*.md`, read out of `fatsecret_mcp` git history (Task 6 deleted them from the working tree).

- [ ] **Step 1: Update `docs/llm-wiki/components/extensions.md`.** Replace the "Shipped catalog" section with:

```markdown
## Shipped catalog

`catalog/mcp/filesystem.yaml` (`npx @modelcontextprotocol/server-filesystem /data`)
and `catalog/mcp/fetch.yaml` (`uvx mcp-server-fetch`), both `scopes: [turn, cron]`,
no auth. `catalog/mcp/fatsecret.yaml` — nutrition tracking, `uvx --from git+…@<sha>
fatsecret-mcp`, four `auth` secrets, `scopes: [turn, cron]`; four action-dispatch
tools rather than the upstream twenty, because every tool schema is cached-prefix
cost on every turn ([[decision.prompt-cache-discipline]]). No plugins ship yet.
```

- [ ] **Step 2: Re-fingerprint and validate**

```bash
.llm-wiki/llm-wiki fingerprint --page components/extensions.md
.llm-wiki/llm-wiki validate
```

Expected: `validate` clean.

- [ ] **Step 3: Commit**

```bash
git add docs/llm-wiki docs/superpowers
git commit -m "docs: spec, plan and wiki for the fatsecret nutrition integration"
```

- [ ] **Step 4: One-time OAuth on a machine with a browser** (not the VPS — the flow opens a browser and listens on `localhost:8080`, `oauth_manager.py:224`):

```bash
cd D:/projects/fatsecret_mcp
python setup_oauth.py
```

Record the resulting access token and secret. OAuth 1.0 tokens do not expire, so this happens once.

- [ ] **Step 5: Build and deploy**

```bash
python -m pytest -q
docker compose build
docker compose up -d
```

Expected: suite green; container healthy.

- [ ] **Step 6: Migrate the bot's state into hermes memory.** Spec §6. Do this *before*
the end-to-end test: `nutrition-log` and `meal-planning` both read the user's targets
from `USER.md` and are instructed not to invent one, so an unmigrated volume makes step 7
stall on a question the old bot could already answer.

Read the four files deleted in Task 6 from git history (they are gone from the working
tree):

```bash
cd D:/projects/fatsecret_mcp
git show HEAD~1:about_me.md
git show HEAD~1:memory/MEMORY.md
git show HEAD~1:memory/favorite_foods.md
git show HEAD~1:memory/eating_patterns.md
git show HEAD~1:memory/corrections.md
git show HEAD~1:memory/weekly_insights.md
```

(`HEAD~1` is the retirement commit from Task 6 Step 5; adjust if later commits landed.)

Then, on the live volume:

- `about_me.md` — parameters, goals, dietary preferences — becomes the body of
  `data/USER.md`. Keep it declarative facts, not prose history (P6).
- The four `memory/*.md` files become entries in `data/MEMORY.md`, one line per durable
  fact. Drop anything that was true only for a past week; `weekly_insights.md` in
  particular is mostly expired by now. From here on the learning loop maintains these —
  nothing writes `memory/*.md` by hand again.

Both files are char-bounded (`MemoryStore`, `src/claude_hermes/memory.py:11`). If the
migrated content does not fit, cut the least durable facts rather than raising the bound.

Done when `data/USER.md` states the calorie and macro targets explicitly, and a fresh
session's system prompt carries them (send any message and check the reply knows the
target without being told).

- [ ] **Step 7: Install from chat.** In Telegram, as the owner, send the four secrets one message at a time:

```
/secret FATSECRET_CLIENT_ID=…
/secret FATSECRET_CLIENT_SECRET=…
/secret FATSECRET_ACCESS_TOKEN=…
/secret FATSECRET_ACCESS_SECRET=…
```

then `add fatsecret`. Expected: `ext_manage` probes the server, reports `connected`, and the reply says the thread is starting fresh (the lane reset — tool schemas are cached prefix).

If any secret is missing, `add` returns the `/secret NAME=` lines and installs nothing. That is the designed failure, not an error to work around.

- [ ] **Step 8: End-to-end proof, in this order.** Anything short of this is not verification:

1. "Найди куриную грудку" → a search result with a `food_id`.
2. "Съел 200 г куриной грудки на обед" → the agent reads the serving list, quotes КБЖУ, and **asks before writing**.
3. Confirm → entry written; `fatsecret_diary(action="get")` shows it under `lunch` with the calories that were quoted.
4. "Отчёт за неделю" → one table from `action="report"`, with off-target days named.
5. "Покажи график веса" → a PNG arrives in the chat.

- [ ] **Step 9: Check the cost of the change.** Run `cost_report` before and after a few turns and confirm the cached-input share still meets NFR-1 (≥60% of input tokens from cache in steady state). Four tools should be invisible in that number; if it moved, the docstrings are bigger than the budget test thinks and something is registering that should not be.

- [ ] **Step 10: Optional — the weekly cron job.** Once the report is proven by hand, schedule it:

```
cron_manage(action="add", schedule="0 9 * * 1",
            prompt="Сделай отчёт по питанию за прошлую неделю: используй навык
                    nutrition-analysis, приложи график веса.")
```

Expected: the job lands in its own topic and fires Monday 09:00. This is capability the standalone bot never had, and it is why the manifest declares `scopes: [turn, cron]`.
