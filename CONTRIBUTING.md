# Contributing to CladeX

Thanks for wanting to help. CladeX is a self-hosted personal agent on the Claude
Agent SDK, reachable from Telegram and a web UI. Bug reports, fixes, docs and new
connectors in `catalog/` are all welcome.

## Before you start

- **Small fix?** Open a pull request directly.
- **New feature or a behaviour change?** Open an issue first and describe the
  problem it solves. The design rules in `docs/PRD.md` §4 (product principles) and
  the decisions in `docs/llm-wiki/decisions/` are binding — an idea that breaks one
  of them needs that discussion before code.
- **Security issue?** Do not open a public issue — see [SECURITY.md](SECURITY.md).

## Where things are

Read `docs/llm-wiki/index.md` first: it is the map of the system — components,
flows, contracts, invariants and the reasons behind them. The module layout and
the import-linter contracts are described in the README's **Develop** section;
each module under `src/claude_hermes/modules/` has its own `README.md`.

The Python package is still named `claude_hermes` (the project's original name);
the product and the repository are CladeX.

## Development setup

Backend (Python 3.12+, Docker for the test database):

    python -m venv .venv && .venv/bin/pip install -e ".[dev]"   # .venv/Scripts/ on Windows
    make test-stack        # PostgreSQL 55432 + Redis 56379 + MinIO 59000 in Docker
    make gate              # ruff + import-linter + the backend suite

`make test-db` (PostgreSQL alone) is enough for most of the suite; tests that need
Redis or MinIO skip with a hint when those are not up.

Web UI (Node 22+):

    cd web && npm ci
    npm run lint && npm run typecheck && npm run test && npm run build
    npm run e2e            # Playwright

You do not need to run everything locally: run the tests for the area you
changed (`pytest tests/modules/<module>/ tests/test_<area>*.py`, `npx vitest
related <files> --run`), and CI runs the full gate on every pull request.

## Pull requests

- One change per PR, with a description of **what** changed and **why**.
- Add or update tests for behaviour you change. A bug fix comes with the test that
  would have caught it.
- Keep the architecture contracts green (`lint-imports`): a module never imports
  another module — declare a `Protocol` port in its `application/ports.py`.
- If your change makes a page under `docs/llm-wiki/` or a README section wrong,
  update it in the same PR.
- Commit messages follow [Conventional Commits](https://www.conventionalcommits.org/):
  `feat(web): …`, `fix(turns): …`, `docs(wiki): …`.
- Never commit secrets, `.env`, `data/` or real user content.

By contributing you agree that your contributions are licensed under the
project's [MIT License](LICENSE).
