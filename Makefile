# The dev interpreter. Every gate step runs through the venv rather than whatever
# `python` happens to be on PATH, so a shell without the venv activated still runs
# the same checks CI does.
PY := .venv/Scripts/python.exe

# --- llm-wiki start ---
.PHONY: wiki-status wiki-validate wiki-finalize

wiki-status: ## LLM wiki health and sync state
	./.llm-wiki/llm-wiki status

wiki-validate: ## LLM wiki structural check
	./.llm-wiki/llm-wiki validate

wiki-finalize: ## activate lifecycle hooks after the first compile
	./.llm-wiki/llm-wiki finalize-init
# --- llm-wiki end ---

# Every container binds to 127.0.0.1 and to a port nothing else uses, so a test
# stack can sit next to a real one on the same laptop. They are throwaway: no
# volumes, `docker rm -f` on every start.
.PHONY: test-db test-stack
test-db: ## a throwaway PostgreSQL 16 for the test suite (127.0.0.1:55432)
	-docker rm -fv hermes-test-pg >/dev/null 2>&1
	docker run -d --name hermes-test-pg -e POSTGRES_USER=hermes -e POSTGRES_PASSWORD=hermes \
	  -e POSTGRES_DB=hermes -p 127.0.0.1:55432:5432 postgres:16-alpine

test-stack: test-db ## the full test stack: PostgreSQL + Redis + MinIO
	-docker rm -fv hermes-test-redis >/dev/null 2>&1
	docker run -d --name hermes-test-redis -p 127.0.0.1:56379:6379 redis:7-alpine
	-docker rm -fv hermes-test-minio >/dev/null 2>&1
	# MSYS_NO_PATHCONV: Git Bash on Windows rewrites the bare `/data` argument
	# into a Windows path before docker ever sees it, and MinIO then serves out of
	# a directory nobody meant. Harmless no-op everywhere else.
	MSYS_NO_PATHCONV=1 docker run -d --name hermes-test-minio \
	  -e MINIO_ROOT_USER=hermes -e MINIO_ROOT_PASSWORD=hermes-test \
	  -p 127.0.0.1:59000:9000 -p 127.0.0.1:59001:9001 \
	  pgsty/minio:RELEASE.2026-08-04T00-00-00Z server /data --console-address :9001
	@echo "postgres 127.0.0.1:55432  redis 127.0.0.1:56379  minio 127.0.0.1:59000 (console :59001)"

.PHONY: test lint gate
test: ## the backend suite (needs `make test-db` once)
	$(PY) -m pytest -q -p no:cacheprovider

lint: ## ruff + the import-linter architecture contracts
	$(PY) -m ruff check src tests scripts
	.venv/Scripts/lint-imports

gate: lint test ## what CI runs before a deploy
