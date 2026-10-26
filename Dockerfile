# --- web build: the CladeX SPA (web/) -> static files the Python process serves
# itself. Node lives in this stage only: the runtime image gets `dist`, not a
# node_modules tree, and the frontend never becomes a second thing to deploy.
# Deliberately still Node 20 while the runtime stage below is 24: Vite builds the
# same `dist` on either, and this stage runs no catalog server.
FROM node:20-alpine AS webbuild
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web ./
RUN npm run build

FROM python:3.12-slim

# Node 24, not 20: catalog/mcp/hostinger.yaml runs hostinger-api-mcp under npx and
# that package refuses to start on an older runtime. Bumping here bumps every npx
# stdio server in the catalog, which is the point — one Node for all of them.
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl git ripgrep ca-certificates \
    && curl -fsSL https://deb.nodesource.com/setup_24.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && npm install -g @openai/codex \
    && rm -rf /var/lib/apt/lists/*

# The GitHub CLI, for the shipped-work half of the content pipeline (see
# skills/linkedin-post/references/shipped.md). It is here rather than as a raw
# `curl` against api.github.com because `gh` accepts `--jq` and `--template`
# server-side of the model: one call returns the four fields a post needs instead
# of a 40KB REST payload the agent then pays to read. Its own apt repo, because
# Debian's `gh` package is years behind and lacks `gh search commits`.
RUN mkdir -p -m 755 /etc/apt/keyrings \
    && curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
         -o /etc/apt/keyrings/githubcli-archive-keyring.gpg \
    && chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
         > /etc/apt/sources.list.d/github-cli.list \
    && apt-get update && apt-get install -y --no-install-recommends gh \
    && rm -rf /var/lib/apt/lists/*

# uv provides uvx, the runner for Python-based stdio MCP servers in catalog/ (npx,
# for the Node ones, comes with nodejs above). Both are on the manifest allowlist,
# so both must exist or the catalog entry that names one cannot come up.
RUN pip install --no-cache-dir uv

# python-pptx is for the AGENT, not for claude_hermes — it is what turns a deck the
# agent has written into the .pptx a user asked for, the same way `gh` above is there
# for ship-log.sh. It belongs in the image rather than in a `pip install` the agent
# runs itself: the container filesystem outside /data is rebuilt on every `up --build`,
# so a runtime install disappears at the next deploy and the failure resurfaces as a
# script that ran last week and does not today. Deliberately NOT a project dependency
# in pyproject.toml — nothing under src/ imports it (15.08.2026: the agent wrote a
# 23KB build_decks.py against python-pptx and could not run a line of it).
RUN pip install --no-cache-dir python-pptx

WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir .

# One-shot operator tools, not part of the package: docs/deploy.md step 4a runs
# `docker compose run --rm agent python scripts/import_sqlite.py`, which needs the
# file to exist inside the image — the only state.db -> PostgreSQL path there is.
COPY scripts ./scripts

# The reviewed manifests. pip installs the package into site-packages, so catalog.py
# cannot find them by walking up from its own file the way it does on a src-layout dev
# host — CH_CATALOG tells it where they actually are.
COPY catalog ./catalog
ENV CH_CATALOG=/app/catalog

# The seed assets: the persona (bundle/CLAUDE.md), settings.json, the bundled skills and
# the subagents. Same walk-up problem as the catalog, same fix — and it bites harder here,
# because a container that cannot find these seeds NOTHING and comes up with no operating
# rules at all. It failed in silence until CH_ASSETS existed: paths.py walked to
# /usr/local/lib/python3.12, found no bundle/, and _copy_missing shrugged.
COPY bundle ./bundle
COPY skills ./skills
COPY agents ./agents
ENV CH_ASSETS=/app

# The built SPA. web/dist is copied out of the Node stage above; CH_WEB_DIST is
# what main.dist_dir() reads, so the API mounts it at / and every unknown path
# falls through to index.html (client-side routing). With web_enabled false the
# HTTP server is never started and these files are simply unused.
COPY --from=webbuild /web/dist /app/web_dist
ENV CH_WEB_DIST=/app/web_dist

ENV CH_HOME=/data

# The chat-grown half of the catalog needs no ENV of its own: it is state, so
# paths.user_catalog_dir() derives it from CH_HOME (-> /data/user-catalog) and catalog.py
# merges it with the reviewed /app/catalog, which always wins on a name collision. It once
# had a CH_USER_CATALOG variable; that was a second source of truth for a path the system
# already knew, and outside this image — where only CH_HOME was set — the writer and the
# reader silently disagreed. Soft boundary by design: see
# docs/superpowers/specs/2026-07-15-catalog-from-chat-design.md, "Residual risk".

# The Codex CLI (used by the gpt-image-2 skill) keeps its auth.json, sessions,
# and config.toml here. Pointing it at the /data volume makes the login survive
# container recreation — the dir and config are seeded on the host under
# ./data/.codex. Codex respects CODEX_HOME but requires the dir to already exist.
ENV CODEX_HOME=/data/.codex
# Same reasoning for the GitHub CLI: its hosts.yml holds the login, and the default
# ($HOME/.config/gh) is not on the volume, so every `docker compose up --build` would
# log the agent out. Auth is a one-time host step, never a chat step — the token must
# not pass through the conversation:
#   docker compose exec -T agent gh auth login --with-token < token.txt
ENV GH_CONFIG_DIR=/data/.gh
# CLI session transcripts (needed for query(resume=...)) default to $HOME/.claude,
# which is not on the /data volume. Point the CLI's own config/session dir at the
# volume so transcripts survive container recreation, matching the "all state
# lives under CH_HOME" architecture.
ENV CLAUDE_CONFIG_DIR=/data/.claude-cli

# The CLI refuses --dangerously-skip-permissions (permission_mode=
# "bypassPermissions") under root, so run as an unprivileged user. On a Linux
# host the bind-mounted ./data must be writable by uid 1000 (see README).
RUN useradd --create-home --uid 1000 hermes \
    && mkdir -p /data && chown hermes:hermes /data
USER hermes

# The Claude Code CLI, from the native installer and pinned to the version the SDK
# bundles (pyproject.toml pins the pair; bump both together). As `hermes`, because
# the installer writes under $HOME (~/.local/bin/claude) and a root install would
# land in /root where this user never looks. Its own config dir for the install, so
# nothing is written into /data before the volume mounts over it; and no
# auto-updater, or the pin would drift inside a running container.
ENV DISABLE_AUTOUPDATER=1
RUN curl -fsSL https://claude.ai/install.sh \
        | CLAUDE_CONFIG_DIR=/tmp/claude-install bash -s 2.1.280 \
    && rm -rf /tmp/claude-install "$HOME/.claude/downloads"
ENV PATH="/home/hermes/.local/bin:${PATH}"

VOLUME /data

CMD ["python", "-m", "claude_hermes.main"]
