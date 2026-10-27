# Local test runs: the narrow selection, never the full suite

The full gate (`make gate`, the whole web suite, the whole Playwright run) is CI's
job: `deploy.yml`'s verify job and `web.yml`. Locally, run only what the change can
break, serially, one test process at a time.

## Backend

There is no import-graph selector. Run the test files of the area you touched, by
path, against the test stack:

    make test-db                         # or `make test-stack` when Redis/MinIO code changed
    PYTHONPATH=src python -m pytest -q -p no:cacheprovider -rs <files>

- A module: `tests/modules/<module>/` plus the `tests/test_<area>*.py` files that
  name it.
- Blob store / MinIO / `s3`: `tests/test_integrations_s3.py`,
  `tests/modules/files/test_files_s3.py` — with `make test-stack` up, and check
  `-rs`: these skip, not fail, when MinIO is down.
- Always add `python -m ruff check <changed .py files>`, and `lint-imports` when an
  import changed.

## Web (`web/`)

    npx vitest related <changed src files> --run
    npx playwright test e2e/<area>.spec.ts --workers=1

A `package.json` or `vite.config.ts` change makes Vitest rerun everything: run the
touched specs by path instead. Playwright has no import graph — run the spec file
of the screen you changed, never the whole `e2e/` folder.

## Whole-suite changes

A lockfile, `pyproject.toml`, `tests/conftest.py`, a migration, a CI workflow, the
`Makefile` or `docker-compose*.yml` would select everything. Do not run it: run the
tests of the files you touched plus `lint-imports`, and list in the PR body what
was left to CI.

## Blind spots

Wiring by string — taskiq task names, event names, route paths, mocked HTTP
handlers in `web/e2e/` fixtures — leaves no import edge: grep the string across
`tests/` and `web/` and run what it hits.
