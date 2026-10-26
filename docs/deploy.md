# Deploying claude-hermes to a server

Written against a Ubuntu host with Docker Engine and the Compose plugin already
installed, deploying as an unprivileged user in the `docker` group.

The agent's state lives in two places: PostgreSQL (sessions, threads, jobs, the
extension registry — the `pgdata` volume) and one directory (`CH_HOME`, `/data` in
the container, `./data` on the host: config, the wiki, skills, uploads). That is
the whole backup story: a `pg_dump` plus a snapshot of `./data`, restored onto a
fresh host, restores the agent.

```bash
docker compose exec -T db pg_dump -U hermes hermes | gzip > ~/hermes-$(date +%F).sql.gz
```

One catch that makes a plain `tar data` **incomplete**, and silently: the container
writes as uid 1000, and some of what it creates is mode 0700. If your host login is
a different uid — `deploy` is 1001 on this host — `tar` skips those directories with
a warning nobody reads and still exits 0. Check with `du -sh data`: any
`Permission denied` line is a hole in every backup you have taken. Snapshot through
a container instead, which reads the tree as its owner:

```bash
D=$(date +%F)
docker run --rm --user 0:0   -v "$PWD/data:/data:ro" -v "$HOME:/backup"   claude-hermes-agent   sh -c "tar -czf /backup/hermes-$D.tar.gz -C / data && chown $(id -u):$(id -g) /backup/hermes-$D.tar.gz"
tar -tzf ~/hermes-$D.tar.gz | grep -c '^data/config.yaml$'   # 1, or the backup is wrong
```

Since Wave 4a the CLI's scratch lives under the volume too (`data/tmp` and
`data/users/<id>/tmp`, one per user, so no turn shares `/tmp` with another). It is
scratch: add `--exclude='data/tmp' --exclude='data/users/*/tmp'` to the `tar` above
if you want the archive small, or leave it — restoring it is harmless.

```
```

## 1. Get the code onto the host

The image is built on the host (`build: .`), so the source must be present — you
are not pulling a published image. Check out the commit you intend to run rather
than tracking a branch: `git log --oneline -1` on the server should match what
you tested.

The repository is private, so an anonymous HTTPS clone fails on the server with
`could not read Username for 'https://github.com'` — there is no terminal to
answer the prompt. Give the host a **read-only deploy key** instead: it is scoped
to this one repository, so a compromised server cannot reach the rest of the
account, and it is not a personal token that ends up in plaintext in
`.git/config` where the next person to run `git remote -v` will paste it into a
log.

On the server:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/hermes_deploy -N "" -C "$(hostname) claude-hermes"
cat >> ~/.ssh/config <<'EOF'

Host github-hermes
  HostName github.com
  User git
  IdentityFile ~/.ssh/hermes_deploy
  IdentitiesOnly yes
EOF
chmod 600 ~/.ssh/config
cat ~/.ssh/hermes_deploy.pub
```

Register the printed key — GitHub → repo → Settings → Deploy keys, or from a
machine with `gh`:

```bash
gh repo deploy-key add key.pub --repo <owner>/claude-hermes --title "<host> (read-only)"
```

Then clone through that host alias:

```bash
git clone github-hermes:<owner>/claude-hermes.git
cd claude-hermes
```

Leave the key read-only. The server never needs to push, and `git pull` on
upgrade works fine without write access.

## 2. Secrets

```bash
cp .env.example .env
$EDITOR .env
```

Five required values and four optional ones, all secrets — behaviour belongs in
`config.yaml`, not here:

| Variable | Where it comes from |
|---|---|
| `POSTGRES_PASSWORD` | any long random string — `openssl rand -hex 32`; compose derives `CH_DATABASE_URL` from it |
| `TELEGRAM_BOT_TOKEN` | @BotFather |
| `CLAUDE_CODE_OAUTH_TOKEN` | `claude setup-token` — bills against your Claude subscription rather than per-token API pricing |
| `MINIO_ROOT_PASSWORD` | any long random string; compose derives `CH_S3_ACCESS_KEY`/`CH_S3_SECRET_KEY` from it and `MINIO_ROOT_USER` |
| `CH_SECRETS_KEY` | `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` — encrypts the `extension_secrets` table |
| `MINIO_ROOT_USER` | optional; a username, not a credential — defaults to `hermes` |
| `OPENAI_API_KEY` | optional; without it a voice message gets a polite "voice isn't set up" reply |
| `CH_WEB_SECRET` | only with the web UI (step 5a); any long random string — `openssl rand -hex 32` |
| `TUNNEL_TOKEN` | only with the web UI (step 5a); the Cloudflare Tunnel token |
| `CLADEX_HOST` | only with the web UI behind Caddy instead of a tunnel (step 5a); the public hostname |

**One token, one running process — in polling mode.** Telegram allows a single
long-poller per bot: start a second container on the same token and both get
`409 Conflict` and neither answers. Stop the old deployment before starting the
new one, or give the new one its own bot. This is a property of `getUpdates`, not
of the token: in webhook mode (step 5b) Telegram POSTs to a URL instead, N agent
instances behind it all receive updates, and the 409 cannot happen.

`TELEGRAM_BOT_TOKEN`, `OPENAI_API_KEY`, `CH_SECRETS_KEY`, `CH_S3_ACCESS_KEY`,
`CH_S3_SECRET_KEY`, `CH_REDIS_URL`, `CH_DATABASE_URL`, `POSTGRES_PASSWORD`,
`MINIO_ROOT_PASSWORD` and `CH_WEB_SECRET` are popped from the process environment
at startup — in the agent AND in the worker, both of which spawn the CLI — so the
agent's own Bash, its hooks and every extension MCP server run without them in
scope. The last three are popped because `env_file: .env` puts them in the
container under their own names as well as inside the URLs compose derives from
them. `CLAUDE_CODE_OAUTH_TOKEN` is the one credential that is *not* popped: the
CLI subprocess is what it is for. Extension credentials never go in this file —
see step 6.

**`CH_SECRETS_KEY` is required and must not be lost.** It is the Fernet key for
the `extension_secrets` table: the ciphertext is in PostgreSQL and the key is
not, so a stolen database dump is not a stolen credential — and a lost key is
every connector's credentials gone. Back it up where you back up the `.env`, and
keep the old one until every secret has been re-entered if you ever rotate it.
The agent and the worker refuse to start without it rather than sealing rows with
a key that dies with the process.

**Six services now, not two.** `db`, `redis` (the queue, the locks, the turn
event/control channels and the in-flight snapshot), `minio`
(uploads and agent-produced files), `agent` (HTTP, Telegram polling, and
*starting* turns), `worker` (the turns themselves, plus scheduled jobs, learning
reviews and curator passes — N of them are safe)
and `scheduler` (a stateless enqueuer of two periodic tasks; its minute tick is
also what reclaims a lane whose worker died). Only `agent` talks
to Telegram. To run the single-process topology instead, drop `worker` and
`scheduler` and unset `CH_REDIS_URL`: everything runs inside the agent, exactly
as it did before Wave 3a.

**The worker needs `TELEGRAM_BOT_TOKEN` too.** Since Wave 3b the turn runs in the
worker, and whichever process runs a turn is the one that sends its `ask_user`
keyboard and its `send_document`. The compose `x-app` anchor already gives
`worker` the agent's whole environment, so this is automatic — but a hand-rolled
worker service without the token will park a Telegram turn on a question nobody
ever sees, and the startup logs `worker_without_telegram_token` at warning when it
happens.

**A restart of `agent` no longer kills a turn** in this topology: the worker
finishes it, the events are in `turn_events` and the reply is on the `turns` row,
so the web thread shows it. Only the Telegram message is lost — the agent is what
sends it, and after a restart nobody is waiting on that lane. Stopping `worker`
does still kill the turn; the lane is freed again within `turn_lease_seconds`
(60 s) and the row is marked `lost`.

## 3. Optional: behaviour

```bash
cp config.yaml.example data/config.yaml
```

Skip it and the defaults apply. Worth a look before first boot:
`allowed_users` (who may talk to the bot at all), `model`, and the learning-loop
intervals.

**What the other people may do beyond talking is decided per person**, in
**Settings → Users** in the web UI: each row but yours has a **Bash** switch (a
shell, and with it `Agent` and `Task`, on their chats and their routines) and a
**Learning** switch (whether their conversations *teach* the agent). A change
needs no restart — the person's next turn picks it up, in whichever process runs
it — and a chat whose shell moved starts a fresh session and says so in one
line. A switch can only be set on somebody with a `users` row, so an id that is
only in `allowed_users` shows its switches disabled until their first message.
Two `config.yaml` keys are the **defaults** for anybody you have not switched
(marked "default" on the row): `users_shell_enabled` and
`users_learning_enabled`, both `false`. Editing either one means restarting
`agent` **and** `worker` together — each reads its own copy of `config.yaml` at
boot: the agent's is the default Settings shows, the worker's is what turns,
routines and reviews run on, and two workers holding different copies would
reset an inheriting user's chats back and forth.

**Bash is a trust decision.** Everything runs in one container as one system
user, so a shell reaches every user's files and credentials — connector tokens,
`secrets.env`, the Claude login — whatever the read guard says; Settings asks
for a confirmation before it turns one on. **The switches are no boundary in a
group chat**: there anyone allowed to talk to the bot runs on the owner's lane,
with the owner's tools, the shell included.

**Learning is a cost decision.** Their own skills library, `skill_admin` on
their private and web lanes, and the Skills page over their own library are
theirs regardless — a library exists in every `data/users/<id>/` tree from the
first turn, and reading your own is not learning. What the grant adds is the
background work, and its cost is what you are deciding on: for **each** such
user, one review fork every
`wiki_nudge_interval` (4) turns without a wiki write and every
`skill_nudge_interval` (15) tool uses, plus one curator pass per
`curator_interval_hours` (168) over their library and wiki, whose wiki-lint
phase is an LLM fork of up to `curator_max_turns` — all of it on the owner's
Claude account, and nothing meters it per user yet. The one bound that holds
either way is one review fork at a time deploy-wide (`lock:review` in redis
mode, the inline queue's own limit otherwise); the curator sweeps users one at
a time, owner first. Without the grant a non-owner's lane is exactly what it
was before Wave 4b: unobserved, said once in the log; withdrawing it also stops
a review of theirs that is still queued. There is also **no per-user quota** on
skills or wiki pages — accepted knowingly: a skill is at most 64 KiB, and the
tree sits on the volume you already back up.

Wave 3b added four turn knobs, all with working defaults:
`turn_lease_seconds` (60 — the dead-man's switch on a *running* turn; the worker
heartbeats every third of it and the reaper frees the lane once it lapses),
`turn_queue_timeout_seconds` (300 — how long a turn may sit `queued` before the
reaper frees the lane, and the ceiling on how long a second Telegram message
waits for a busy one), `turn_snapshot_ttl_seconds` (3600 — the lifetime of the
in-flight snapshot in Redis, which is what a browser reloading mid-turn reads)
and `turn_retention_days` (30 — finished `turns` rows are pruned after this; the
row holds a third copy of the message and the reply).

### Offering a model

`model` is the deploy default; `models:` is the list a user may switch a chat to
(the composer's picker, `/model` in Telegram, a routine's form). Every allowlisted
user picks from the same list, so it is also a spending decision: a chat switched
to Fable 5.1 costs five times what Sonnet 5 does, on the owner's account.

A model belongs in `models:` only once it has been proved on the **pinned CLI
under the production token** — the CLI can refuse a model it does not know,
substitute another, or ask for a usage-credits consent a fresh config dir has
never given. The proof is `scripts/model_matrix.py`, run in a throwaway image
built from the checkout you are about to deploy, never in the running services:

```bash
git clone -q --depth 1 -b <branch> "$(git -C ~/claude-hermes remote get-url origin)" ~/hermes-matrix
cd ~/hermes-matrix && docker build -t hermes-matrix .
umask 077; grep '^CLAUDE_CODE_OAUTH_TOKEN=' ~/claude-hermes/.env > token.env
docker run --rm --env-file token.env hermes-matrix python scripts/model_matrix.py > out.json
rm -f token.env; docker rmi hermes-matrix; cd ~ && rm -rf ~/hermes-matrix   # after copying out.json off
```

About fifty one-word calls. Read `out.json` for each candidate: `ok`, `substituted`
(must be false), `mentions_credits_or_consent` (must be false), the window in
`model_usage`, and every effort. A model that fails any of them stays out of
`models:`. The facts it records are what `src/claude_hermes/core/model_catalog.py`
holds; update the table there from the output after every SDK/CLI bump.

Removing a model from `models:` moves every chat that was on it to its default at
its next turn — one cold cache write per chat, and nothing is lost.

## 4. Optional: bring an existing deployment's state with you

Skip this for a fresh agent — first boot seeds everything. Do it to move a
running agent to another host with its wiki, its skills, its sessions and its
history intact.

With the old deployment **stopped** (`state.db` is live SQLite; copying it out
from under a running container copies a torn database):

```bash
tar -czf hermes-state.tar.gz data          # on the old host
scp hermes-state.tar.gz <newhost>:~/       # then, on the new one:
tar -xzf ~/hermes-state.tar.gz -C /path/to/claude-hermes
```

Then fix ownership, and do it before the first start:

```bash
docker run --rm -v "$PWD/data:/data" alpine chown -R 1000:1000 /data
```

The container runs as uid 1000 (`hermes`, created in the Dockerfile), but the
extracted files carry the uid of whoever unpacked them — on a typical
second-user host that is 1001, and the agent cannot write its own state
directory. `docker run` is how you fix it without sudo: you are in the `docker`
group, and that container is root.

The failure this prevents is loud but misleading — the container restart-loops
on startup with:

```
PermissionError: [Errno 13] Permission denied: '/data/agent_home/.claude/persona.md.tmp'
```

That path is the managed persona, rewritten on every boot, so it is simply the
first write the agent attempts. The problem is the whole directory, not that
file.

## 4a. Migrating `state.db` to PostgreSQL

Only for a deployment that ran before the PostgreSQL move. A fresh agent skips
this: `alembic upgrade head` runs at every boot and an empty schema is the
correct starting state.

`scripts/import_sqlite.py` copies the old SQLite file into the new database. It
is idempotent — every row it inserts is an upsert that does nothing on conflict,
so a re-run after a half-finished attempt is safe.

With `POSTGRES_PASSWORD` already in `.env` (step 2) and the stack **stopped**:

```bash
docker compose down
docker compose up -d db                     # Postgres alone, so nothing writes turns mid-import
docker compose run --rm agent python scripts/import_sqlite.py --sqlite /data/state.db
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

The script prints a row count per table; sessions, messages, threads, jobs, job
runs, turn events, costs, files, the extension registry and `meta`. Ownership
comes across as a row in `users`: `_ensure_users` inserts the owner from the
SQLite `meta.owner_user_id` key before anything that references it, so you do
not re-claim ownership. The `meta` key itself is copied with the rest of the
table and is inert — since Step 5 the `users` table is the only owner record.

`data/state.db` is left on disk untouched and is never read again. Keep it until
you have seen the agent answer, the web UI list its old threads and a scheduled
job fire; then it is just another backup. From here on the database is in the
`pgdata` volume, not in `./data`, so the `tar data` snapshot at the top of this
file no longer captures it — add
`docker compose exec -T db pg_dump -U hermes hermes | gzip > hermes-db-$D.sql.gz`
to whatever you back up with.

## 4b. Migrating `secrets.env`, `catalog-drafts/` and `pending-approvals/`

Only for a deployment that ran before Wave 3a. A fresh agent skips this: the three
tables are created by `alembic upgrade head` and start empty.

Wave 3a moved three things off the data volume and into PostgreSQL —
`data/secrets.env` into `extension_secrets` (Fernet-encrypted with
`CH_SECRETS_KEY`), `data/catalog-drafts/` into `catalog_drafts`, and
`data/pending-approvals/` into `approval_snapshots`. Nothing reads the files any
more, so an upgraded deployment silently loses every stored extension credential
and every queued draft until they are imported.

This is the migration that is *not* an Alembic migration: it needs the encryption
key, which a schema migration does not have. With `CH_SECRETS_KEY` already in
`.env` (step 2) and the stack **stopped**:

```bash
docker compose down
docker compose up -d db
docker compose run --rm agent python scripts/import_data_dir.py --home /data
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

It prints a count per kind and is idempotent — every write is an upsert keyed the
way the table is, so a re-run after a half-finished attempt is safe. It **deletes
nothing**: check the counts, confirm `/secret list` and the drafts queue look
right, and only then remove the three paths by hand.

Drafts that no longer validate are carried across too, as `status='rejected'` with
the validator's own words as the reason. A draft filed under a laxer validator is
still a request a human owes an answer to; dropping it here would turn it into one
that was never filed.

From here on the operator reads the queue **through the app**, because it is a
table now:

```bash
docker compose exec -T agent python -m claude_hermes.main drafts list
docker compose exec -T agent python -m claude_hermes.main drafts show <name>
docker compose exec -T agent python -m claude_hermes.main drafts delete <name>
```

`scripts/approve_draft.py` on the host wraps exactly those commands (override the
wrapper with `CH_DRAFTS_CMD` for a non-compose host) and still writes `catalog/`
itself from outside the container. That split is the security model, not a
convenience: the agent can queue anything it likes, and the one thing it cannot do
is make a queued manifest installable.

## 5. Start

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
docker compose logs -f agent
```

Expect `claude-hermes polling as @<botname>` and `Run polling for bot`. First boot
also seeds `data/agent_home/` — the wiki tree, the bundled skills, the persona and
`state.db`. A carried-over state directory is left alone: the wiki refuses to
re-seed once `index.md` exists, and the bundled skills, agents and
`settings.json` are seeded through a per-tree manifest
(`agent_home/.claude/.seeded.json`, the sha256 of each file at seed time) that
refreshes only what you never touched — see "Upgrading" for the rules.

Transient `TelegramServerError: Bad Gateway` in the log is Telegram, not you —
aiogram backs off and reconnects on its own.

The production overlay is not optional on a shared host: it caps the log driver
and the container's memory and CPU. See the comments in
`docker-compose.prod.yml` for what each one is protecting against.

## 5a. Optional: the web UI (CladeX)

Skip this and the agent is Telegram-only, exactly as before — `web_enabled`
defaults to false and the HTTP server is never constructed.

The browser client is served by the agent process itself (the image builds
`web/` in a Node stage and copies the result to `/app/web_dist`), and it is
reached through an **outbound** Cloudflare Tunnel. No port is published on the
host — `docker-compose.yml` has no `ports:` anywhere, and that is deliberate
(PRD P8).

1. **Create the tunnel.** Cloudflare dashboard → Zero Trust → Networks →
   Tunnels → Create a tunnel (type *Cloudflared*). Copy the token it shows.
2. **Route it at the container.** In the tunnel's *Public Hostname* tab, add
   your hostname (say `cladex.example.com`) with service
   `HTTP` → `agent:8080`. `agent` is the compose service name and 8080 is
   `web_port`; the two containers share the compose network, so nothing leaves
   the host unencrypted and nothing listens on a host interface.
3. **Secrets** in `.env`:

   ```bash
   echo "TUNNEL_TOKEN=<token from step 1>" >> .env
   echo "CH_WEB_SECRET=$(openssl rand -hex 32)" >> .env
   ```

   `CH_WEB_SECRET` signs the `cladex_session` cookie. Rotating it logs everyone
   out; losing it is harmless beyond that. The process **refuses to start** with
   `web_enabled: true` and no secret rather than signing with an empty key.
4. **Behaviour** in `data/config.yaml`:

   ```yaml
   web_enabled: true
   web_public_origin: https://cladex.example.com   # exact scheme + host, no trailing slash
   ```

   `web_public_origin` is the `Origin` every mutating request must carry. Get it
   wrong and the UI loads but every POST comes back `403 bad_origin`.
5. **Tell BotFather about the domain.** `/setdomain` → pick the bot → send the
   hostname. Telegram's Login Widget is the only way in — there is no password,
   you sign in as your Telegram account — and it refuses to render on a domain
   the bot does not know.
6. `docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build`,
   then `docker compose logs cloudflared` (expect `Registered tunnel connection`)
   and `docker compose logs agent | grep "web channel"`.
7. **Claim ownership in Telegram first** (step 6 below). The login screen never
   claims it: on a deploy with no owner every web login is rejected, so a
   stranger who finds the hostname cannot become the owner. Afterwards only the
   owner and `allowed_users` get past the login screen.

The tunnel makes step 6 below reachable from a browser as well as from Telegram;
ownership is one flag in `meta`, and only a private chat with the bot sets it.

### Without a Cloudflare account: Caddy on the host's 80/443

No Cloudflare, or no `sudo` for a host nginx? `docker-compose.caddy.yml` puts
Caddy in front of the agent instead of the tunnel. It is the one deliberate
departure from "no inbound ports" (PRD P8): Caddy owns 80 and 443, obtains and
renews the Let's Encrypt certificate itself, and proxies to `agent:8080` over
the compose network — the agent still publishes nothing, and the login behind
the TLS is the same. It needs a hostname that already resolves to the host's
public address, and 80/443 free on that host (Caddy answers the ACME challenge
on 80). The sites are in `deploy/Caddyfile`: the apex proxies to the agent and
`www.` redirects to it — add both records, or expect certificate retries for
`www` in the Caddy log while the apex keeps working.

Replace steps 1–3 above with:

```bash
echo "CLADEX_HOST=cladex.example.com" >> .env
echo "CH_WEB_SECRET=$(openssl rand -hex 32)" >> .env
```

and start with the overlay on top of the other two:

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml \
               -f docker-compose.caddy.yml up -d --build
docker compose -f docker-compose.yml -f docker-compose.prod.yml \
               -f docker-compose.caddy.yml logs caddy   # "certificate obtained successfully"
```

The overlay parks `cloudflared` behind a `tunnel` profile, so a plain `up`
leaves it out instead of restart-looping on an empty `TUNNEL_TOKEN`. Pass all
three `-f` files on every upgrade — an `up` with only the first two brings the
tunnel container back and leaves Caddy behind.

### The Mini App

Once `web_public_origin` is an `https://` URL, every boot registers it as the
bot's **menu button** (`setChatMenuButton`, type `web_app`): the button next to
the message field in the bot's chat opens CladeX inside Telegram, and the page
signs in from the `initData` Telegram hands it — the same `allowed_users` gate
as the widget, a different check key (HMAC under `"WebAppData"`), and no
BotFather step. `web_menu_button: false` in `config.yaml` leaves the button
alone for a bot whose menu is managed elsewhere. Telegram refuses a plain-http
URL, so on a `http://localhost` origin nothing is registered.

The Login Widget still needs `/setdomain` (step 5) for a browser visit; the Mini
App does not.

## 5b. Optional: webhook mode

Polling is the default and is what a laptop should stay on. Switch to webhook
mode when you want either of these:

- **more than one agent instance.** Only one process may long-poll a bot token
  (every other gets `409 Conflict`); any number may receive webhook POSTs. This
  is what makes `--scale agent=N` possible — together with `CH_REDIS_URL`, which
  compose already sets, and which is not optional here: in inline mode each
  instance runs its own cron ticker and curator loop;
- **no long-poll connection at all** — the agent stops holding an outbound
  connection open to Telegram and only answers requests.

It needs step 5a first: the route that receives updates is one of the web app's
routes, so `web_enabled` and a public `web_public_origin` are what makes it
reachable. The process refuses to start (with the reason) if either is missing.

1. **Generate the secret** and put it in `.env`:

   ```bash
   echo "CH_TELEGRAM_WEBHOOK_SECRET=$(openssl rand -hex 32)" >> .env
   ```

2. **Turn it on** in `data/config.yaml`:

   ```yaml
   telegram_mode: webhook
   web_enabled: true                               # required
   web_public_origin: https://cladex.example.com   # required, https, reachable
   ```

3. `docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d` and
   check what Telegram itself believes:

   ```bash
   curl -s "https://api.telegram.org/bot<token>/getWebhookInfo"
   ```

   Expect your URL, `"pending_update_count": 0` and no `last_error_message`. A
   `last_error_message` of "wrong response from the webhook" usually means the
   origin does not resolve to this host, or TLS is not up yet.

**Nothing to change in the proxy.** Both the Cloudflare Tunnel and the Caddy
overlay forward *every* path to `agent:8080`, and `/telegram/webhook/…` is just
another one. There is no allowlist to extend and no `/api` prefix to work around.

**Two secrets, not one — and that is deliberate.** The URL carries the secret as
a path segment (Telegram offers nothing else that survives a proxy stripping
headers), which means it is written into every access log the request passes
through, in plaintext, by a component nobody thinks of as a secret store. So the
agent also requires the `X-Telegram-Bot-Api-Secret-Token` header, which Telegram
sends on every delivery because `setWebhook` was given `secret_token`, and which
reaches no log. A request with the right path and no header is `401`. Rotating
the secret is one `.env` edit plus a restart — the new URL is registered at boot.

**A repeated update is dropped.** Telegram redelivers an update it did not get a
2xx for in time, and an accepted update starts a download, possibly a
transcription and then a turn. The route answers `200` immediately, dispatches
in the background, and refuses a `update_id` it has already accepted (a Redis key
with a 10-minute TTL, so the refusal holds across instances). The trade is
explicit: a crash between the `200` and the handler loses one update, rather than
charging twice and answering twice.

**Rolling deploys.** `setWebhook` with the same URL and secret from N instances
is idempotent. The one hazard is a rolling deploy whose old and new instances
disagree about `web_public_origin` — the last one to boot wins, and everything
else silently stops receiving. Change the origin with the deployment stopped.

**Going back to polling** is `telegram_mode: polling` and a restart: the process
calls `deleteWebhook` before it starts polling, because `getUpdates` behind a
registered webhook answers `409` forever.

**The health probe.** `GET /healthz` answers `{"ok": true}` with no cookie and no
database query — a compose healthcheck or a proxy check can use it
(`docker-compose.prod.yml` documents the `curl` variant next to the default
asyncpg one). It says "this process is serving HTTP" and deliberately nothing
more.

## 6. Claim ownership, then wire extensions

**The first person to DM the bot becomes its owner** (the one row in the `users`
table with `role = 'owner'`; nothing writes `meta.owner_user_id` any more) and
only the owner can install extensions or set secrets. So the first message must
be yours — send it before telling anyone the bot exists. Because that row is the
only record, **never `alembic downgrade` past `0005` on a claimed deploy**: the
downgrade drops `users`, and an upgrade afterwards finds no `meta` key to rebuild
it from, so the next private DM would claim the agent. If it has happened, put
the row back before the agent starts:

```sql
INSERT INTO users (id, role, workspace_dir, display_name, username, created_at, last_seen_at)
VALUES (<your telegram id>, 'owner', '', '', '', '<now, ISO-8601 UTC>', '<now, ISO-8601 UTC>');
```

Extension credentials go through chat, never through `.env`:

```
/secret FATSECRET_CONSUMER_KEY <value>
```

They land in `data/secrets.env` (mode 600) and are injected only into the
extension that declares them. The catalog is mounted read-only on purpose: the
agent runs as the owner of `/data` and has Bash, so anything it can write to is
not a boundary — `./catalog:/app/catalog:ro` is the one part it cannot forge.

## 7. Verify

```bash
docker compose ps                      # healthy, not just up
ls data/agent_home/wiki/               # index.md, log.md, the five namespaces
docker compose logs agent | grep -i error
```

Then in Telegram: send a message, get a reply; ask it to remember something and
confirm a page appears under `data/agent_home/wiki/user/`.

On the six-service topology, that message also proves the split. While it runs:

```bash
docker compose exec -T db psql -U hermes hermes -c \
  "SELECT id, status, worker_id FROM turns ORDER BY created_at DESC LIMIT 3;"
```

`status` should go `queued` → `running` with a `worker_id` of
`<hostname>:<pid>` — the worker container. A row stuck at `queued` means
`run_turn` is sitting on a Redis list nobody reads (check that `worker` is up and
pointed at the same Redis); a row that says `error` with
`dispatch failed: …` means the agent could not enqueue at all.

**A second user**, if there is one on `allowed_users`: have them DM the bot
once, then:

```bash
ls data/users/<id>/agent_home/.claude/skills      # their own seeded library
docker compose exec -T db psql -U hermes hermes -c \
  "SELECT key FROM meta WHERE key LIKE '%:<id>';"
```

The first proves their tree and library exist (and `skill_admin list` on their
private chat lists that library, not yours). The second is empty until they
have learning — their **Learning** switch in Settings → Users, or the
`users_learning_enabled` default — and after a few of their turns it shows
`turns_since_wiki_review:<id>` and friends, and after a review fork
`learning_review_until:<id>`; the owner's rows keep their unsuffixed names.
Their curator reports land under `data/users/<id>/logs/curator/` once the sweep
reaches them (first run defers a full `curator_interval_hours`, like the
owner's). If it never does, check the unsuffixed `curator_paused` row: `1` there
pauses the whole sweep, every user included.

## Upgrading

`scripts/deploy.sh` does everything below, in a better order, and refuses to
continue on a dirty checkout or an unusable backup:

```bash
cd ~/claude-hermes && ./scripts/deploy.sh main
```

**Upgrading a host whose `.env` predates PostgreSQL:** add `POSTGRES_PASSWORD`
to `.env` first. `docker-compose.yml` interpolates it as
`${POSTGRES_PASSWORD:?...}`, so without it the very first `docker compose build`
aborts before anything is built — the deploy fails at the top of the script, not
at the database. Then migrate the old `state.db` with step 4a.

**Upgrading a host whose `.env` predates Wave 3a:** add `MINIO_ROOT_PASSWORD` and
`CH_SECRETS_KEY` the same way, for the same reason — both are interpolated with
`:?` and the compose file will not even parse without them. Then import the old
`secrets.env`, drafts and approval snapshots with step 4b, before you let anyone
use an extension again.

**Upgrading a host to Wave 3b** needs nothing in `.env`: migration `0004_turns`
runs at the agent's boot like every other one, adds the `turns` table and
`sessions.index_mtime`, and no existing row is touched. Two things to know. The
worker command gained `--max-async-tasks 4`, so `docker compose up -d` must
actually recreate the `worker` container (it will — the command changed). And a
rollback *across* this migration is the usual rule: the older image will not
downgrade the schema, so restore the dump.

**Upgrading a host to Wave 4b** needs nothing in `.env` and no migration, but
two things to know. **Drain the queue first on a multi-container deploy**: a 4b
agent enqueues `learning_review` with an `owner_id` argument that a 4a worker
does not accept — the task raises `TypeError` and that review is lost (the other
direction, a 4a payload drained by a 4b worker, is fine: it runs as the owner's).
The stop-everything order below already does this for a single host; a rolling
deploy of `agent` and `worker` separately does not, so stop the old `agent`
before the new one enqueues, or accept a lost review or two. And **every lane's
prompt cache misses once** after the upgrade: the `send_document` tool
description changed (relative paths now resolve against the agent's home, and
the boundary is that home), which is part of every cached prefix, and Telegram
group lanes additionally lose `skill_admin`, `session_search` and `cost_report`
for everybody — the group's library, recall and spend are the deploy owner's,
and a tool that came and went with the sender was itself a cache miss every
time a different person spoke — while a non-owner's private and web lanes gain
`skill_admin` over their own library. One miss per lane, then warm again.

**Upgrading a host to the model switcher** needs nothing in `.env`: migration
`0008_model_choice` runs at boot and only adds nullable columns, and NULL is "the
deploy default", which is what every existing chat already runs. Four things to
know:

- **The image pins a new SDK/CLI pair** — `claude-agent-sdk` 0.2.158 with CLI
  2.1.280, and the image's own `claude` now comes from Anthropic's native
  installer (under `/home/hermes/.local/bin`, auto-updater off) instead of npm.
  `docker compose up -d --build` rebuilds it. A CLI upgrade can change the
  `claude_code` preset text that is part of every cached prefix, so expect every
  lane's prompt cache to miss once after the deploy.
- **Opus 5 now runs with its full 1M window.** CLI 2.1.218 gave plain
  `claude-opus-5` 200K and needed `claude-opus-5[1m]` for 1M; 2.1.280 gives it 1M
  either way. Auto-compaction therefore moves from about 200K to about 1M: a
  long-lived chat keeps more context and re-reads up to five times as many
  tokens per turn (mostly from cache). `/new` resets a chat whenever that is not
  wanted. A `model: claude-opus-5[1m]` line in `data/config.yaml` keeps working
  and is no longer needed.
- **The CLI may no longer swap models on its own** (`CLAUDE_CODE_NO_MODEL_FALLBACK=1`
  on every run). A model the API refuses is answered once more on the deploy
  default, with a notice naming both, and the chat keeps its session.
- **Restart all three** — `agent`, `worker` and `scheduler` each read
  `config.yaml` once at boot, so a `models:` edit reaches all of them only after a
  restart of each; `docker compose up -d` after an edit does not recreate them.

Rolling back across `0008` is the usual rule (restore the dump), although an older
image ignores the added columns and runs regardless.

**Upgrading a host to per-user grants** needs nothing in `.env` and no config
change: migration `0009_user_grants` runs at the agent's boot and only adds three
nullable columns (`users.shell_enabled`, `users.learning_enabled`,
`sessions.shell_enabled`). NULL on a user is "no choice of their own", which
inherits `users_shell_enabled` / `users_learning_enabled` — exactly what everyone
runs on today — and NULL on a session is compared with that same rule, so the
upgrade itself resets no conversation; a person's chats start a fresh session
only when their shell grant later changes. Three things to know:

- **Drain the queue first on a redis-mode deploy.** No task payload changed, but
  an old worker still running beside the new agent runs every turn, routine,
  review and curator sweep it picks up under the old deploy-wide rule: it ignores
  a grant set in Settings → Users (a withdrawn shell is still handed out when
  `users_shell_enabled` is on) and stamps no session. `./scripts/deploy.sh`
  stops the whole stack at once, which covers a single host; rolling `agent` and
  `worker` separately, let the old worker finish what is queued and stop it
  before the new agent starts.
- **The two keys are now defaults.** Anyone you switch in Settings → Users
  ignores them from then on; there is no way back to "inherit" from the UI.
- **Editing either key means restarting `agent` and `worker` together** (see
  section 3); a per-person switch needs no restart at all.

Rolling back across `0009` is the usual rule (restore the dump), although an
older image ignores the added columns and runs on the config keys alone.

**Upgrading a host to Step 5** (the removal of the pre-modular shims) needs
nothing: no migration, no `.env` change, no config key, and no prompt-cache miss
— routes, tool schemas, task payloads and the prompt prefix are byte-identical,
so a rolling deploy of `agent` and `worker` is fine. One thing to know: the
application no longer writes or reads `meta.owner_user_id`. The owner row in
`users` was materialised from that key by migration `0005` when you upgraded to
4a, so the deploy stays claimed; the stale `meta` row is inert and may be left
alone, or removed for tidiness with
`DELETE FROM meta WHERE key = 'owner_user_id';`. Do not downgrade past `0005` on a
claimed deploy (step 6 has the recovery `INSERT`).

`.github/workflows/deploy.yml` is the same script behind a manual button, with
the test gate run first. `docs/llm-wiki/operations.md` records which host this
is and what a deploy costs a turn that is in flight.

The steps by hand, and why the order is what it is — build while the old stack
still serves, stop, back up, start:

```bash
COMPOSE="-f docker-compose.yml -f docker-compose.prod.yml -f docker-compose.caddy.yml"

git pull --ff-only
docker compose $COMPOSE build          # slow, and it does not touch what is running

# Everything that writes state stops: the worker runs jobs and the scheduler
# enqueues them, so an agent stopped alone would be backed up while a worker was
# still rewriting the skill tree behind it.
docker compose $COMPOSE stop agent worker scheduler   # downtime starts here
D=$(date +%F-%H%M)
docker compose $COMPOSE exec -T db pg_dump -U hermes hermes | gzip > ~/hermes-$D.sql.gz
# the bucket: uploads and agent-produced files, which are NOT on the data volume
# when blob_backend is s3. mc runs inside minio's own network namespace, so the
# endpoint is the one minio itself listens on and no port has to be published.
# The credentials are in .env, which this shell has not read. Take ONLY the two
# MinIO names (as scripts/deploy.sh does): sourcing the whole file would put the
# bot token and the Fernet key into the environment of everything that follows.
MINIO_USER=$(sed -n 's/^MINIO_ROOT_USER=//p' .env | head -1); MINIO_PASS=$(sed -n 's/^MINIO_ROOT_PASSWORD=//p' .env | head -1)
docker run --rm --network "container:$(docker compose $COMPOSE ps -q minio)"   -v "$HOME/hermes-$D-blobs:/backup" --entrypoint sh pgsty/mc:RELEASE.2026-10-25T00-00-00Z -c   "mc alias set src http://127.0.0.1:9000 '${MINIO_USER:-hermes}' '$MINIO_PASS' >/dev/null && mc mirror --overwrite src/hermes /backup"
# via a container, not a bare tar: see "the whole backup story" at the top of this
# file for why a plain tar can be incomplete and still exit 0
docker run --rm --user 0:0 -v "$PWD/data:/data:ro" -v "$HOME:/backup"   claude-hermes-agent sh -c "tar -czf /backup/hermes-$D.tar.gz -C / data; chown 1000:1000 /backup/hermes-$D.tar.gz"
tar -tzf ~/hermes-$D.tar.gz | grep -c '^data/config.yaml$'   # 1, or the backup is wrong

docker compose $COMPOSE up -d          # downtime ends here
```

Backing up *before* the stop is the tempting shortcut and it is wrong twice over:
`tar` over a live `data/` exits non-zero with `file changed as we read it`, which
breaks an `&&` chain and leaves a torn archive nobody verified. The database is
not in the tar at all: `pg_dump` takes a consistent snapshot on its own, which is
why PostgreSQL keeps running while the agent is stopped.

A full backup is now three things, and the tar alone is none of them:

| what | where it lives | how it is taken |
|---|---|---|
| sessions, threads, jobs, costs, the registry, **extension secrets and drafts** | the `pgdata` volume | `pg_dump` |
| uploads and agent-produced files (`blob_backend: s3`) | the `miniodata` volume | `mc mirror` |
| the workspace: wiki, skills, agents, `config.yaml`, and under `users/<id>/` each other user's wiki, skills and CLI transcripts; `logs/` and `users/<id>/logs/` hold the skill and wiki snapshots and the curator reports (`curator/<ts>/REPORT.md`) of that tree's owner, which is what `skill_admin rollback` restores from | `./data` | `tar` through a container |

Redis is deliberately not backed up: it holds the queue, and a job that was in
flight is re-claimed from `jobs` on the next tick. `CH_SECRETS_KEY` is not in any
of the three — without it the `pg_dump` restores ciphertext nobody can read.

Two things survive a rebuild that you might expect not to, and one that you might
expect to survive but does not:

- **The persona is rewritten from `bundle/persona.md` on every boot.** Edits made
  directly to `data/agent_home/.claude/persona.md` are lost on restart. Change
  the bundle.
- **Seeded skills, agents and `settings.json` refresh while untouched, and only
  then** (Wave 4b). Each tree carries a manifest,
  `agent_home/.claude/.seeded.json`, recording the sha256 of every file the
  bundle seeded. On every boot, per file: absent and never recorded → copied (a
  new bundled skill reaches an existing deployment); absent but recorded → left
  absent (you or the agent deleted it on purpose); a skill whose name sits under
  `.claude/skills/.archive/` → left alone whatever the manifest says (archived is
  not deleted, and before 4b every boot resurrected it); present and still the
  bytes we shipped → overwritten with the new bundle when it changes; present and
  modified → left alone, logged once at INFO. A skill the agent patched keeps its
  patch while its untouched siblings refresh. `CLAUDE.md` is not in the manifest —
  `persona.md` is the channel for managed rules. **A tree from before 4b has no
  manifest**: on the first boot after the upgrade a seeded file that still
  matches the current bundle is recorded, a pristine copy of an *older* bundle
  (the runtime ships the sha256 of every version it ever bundled and later
  changed) is refreshed right away, and a file that matches no shipped version
  is left alone and logged once at INFO with its path — delete that file to
  re-seed it. An unparseable manifest reads as empty (the agent can write that
  file) and costs one extra refresh, never a failed boot.
- **`data/` is never touched by a rebuild.** The wiki, the skills, the sessions
  and the DB are yours across upgrades.

## Rolling back

```bash
docker compose down
git checkout <previous-commit>
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

Rolling back the *image* is always safe; rolling back the *schema* is not:
`alembic downgrade` past `0005` on a claimed deploy drops the only owner record
(step 6), so restore the `pg_dump` instead of downgrading.

If the state directory itself is suspect, restore the tarball over `data/` while
the stack is down. Skill-level damage does not need this: `skill_admin` keeps its
own snapshots and can roll back a single skill from chat.
