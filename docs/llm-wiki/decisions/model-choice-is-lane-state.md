---
id: decision.model-choice-is-lane-state
kind: decision
status: current
summary: A lane's (model, effort) is state on its `threads` row, resolved once per turn in one order (explicit, live session, owner default, deploy default); config offers the models, a code catalog describes them, and only routes and gateway commands switch one.
verification:
  base_commit: 68c332df85c483d0573ee1cbf0ac29a2ad695626
  evidence_fingerprint: sha256:1b90a3cec8998248c6bb8bc62a4252041248b085a077f4babb80f910c5b5f2da
evidence:
  - path: src/claude_hermes/core/model_catalog.py
  - path: src/claude_hermes/core/config.py
  - path: src/claude_hermes/core/db/migrations/versions/0008_model_choice.py
  - path: src/claude_hermes/core/app_context.py
  - path: src/claude_hermes/integrations/claude_sdk/options.py
  - path: src/claude_hermes/modules/sessions/application/services.py
  - path: src/claude_hermes/modules/turns/application/turn_service.py
  - path: src/claude_hermes/modules/turns/infrastructure/sdk_runner.py
  - path: src/claude_hermes/modules/turns/domain/exceptions.py
  - path: src/claude_hermes/modules/telegram/application/services/model_menu.py
  - path: src/claude_hermes/modules/scheduler/infrastructure/executor.py
  - path: src/claude_hermes/main.py
  - path: scripts/model_matrix.py
  - path: docs/deploy.md
relations:
  - decision.prompt-cache-discipline
  - decision.multi-user-isolation
  - decision.threads-as-session-lanes
  - decision.claude-agent-sdk
  - component.core-runtime
  - component.web
  - component.gateway
  - component.cron
  - contract.state-db
---
# Decision: a lane's model is lane state, chosen in one place

## Context

Until the model switcher the whole deployment ran one model, `cfg.model`, handed
to every `base_options(model=...)` call. Letting a person pick the model and the
reasoning effort per conversation raises four questions whose wrong answers are
expensive to undo: where the choice lives, who decides what a turn runs, which
models may be offered, and who may change one.

The constraints come from elsewhere in the wiki. Prompt caches are model-scoped
([[decision.prompt-cache-discipline]]): a new model re-reads the whole history
once, so a switch is a bill. Tool presence and anything shared are properties of
the lane, never of the sender ([[decision.multi-user-isolation]]). The CLI has
its own opinions — it keeps an embedded model catalog, gives a model it does not
know a 200K window, substitutes models along an availability chain unless
`CLAUDE_CODE_NO_MODEL_FALLBACK=1`, asks for a usage-credits consent before Fable
models on subscription auth, and runs `/model` and `/effort` as its own local
commands in non-interactive mode.

## Decision

**The pair lives on the `threads` row** (`threads.model`, `threads.effort`,
migration `0008`). Not on `sessions`, which `/new`, `reset_all` and
`reset_owned` delete; not on the message, because a per-message model would let
two channels disagree about the same conversation. The web picker and Telegram's
`/model` edit the same row.

**One rule, one gatherer.** `core.model_catalog.resolve()` is the rule and
`SessionService.effective(session_key, owner_id)` (`modules/sessions`) the only
caller that gathers its inputs; the turn, the web routes and the Telegram menu
all ask that one object, built once in `main.build_app`. The order is:

1. the lane's explicit pair;
2. what its **live** session runs (`sessions.model/effort`, written from the
   model that answered) — "live" by `SessionRouter`'s own expiry rule, and a live
   session with no recorded pair (it began before `0008`) counts as the deploy
   default;
3. the lane owner's personal default (`users.default_model/default_effort`),
   never on a shared lane (`lane_is_shared`: a group, a forum topic, a `cron:`
   job);
4. the deploy default, with no effort.

A pair whose model left `models:` is skipped; an effort the model does not take
becomes `None`, which means "send no effort flag", not "the model's default".

**Config offers, the catalog describes.** `models:` in `config.yaml` is only the
offer (`cfg.model` is always in it). What each model IS — window, prices,
efforts, default effort, billing, and the spelling the CLI needs (`cli_id`) —
lives in `core/model_catalog.py`, which is standard library only because every
module imports it. Ids are the API's everywhere a model is stored or sent;
`cli_id()` is the only way one reaches the CLI. A model joins `models:` only after
`scripts/model_matrix.py` has proved it on the pinned CLI under the production
token (`docs/deploy.md`, "Offering a model").

**Only routes and gateway commands switch a model** — `PUT
/api/threads/{id}/model`, `PUT /api/me/model-default`, a routine's form, and the
gateway-intercepted `/model` with its `mdl:` buttons. No model-callable tool can,
`cron_manage` included, and a turn text that is `/model` or `/effort` never
reaches the CLI on any path.

**One announced fallback.** The CLI's own substitution is off
(`workspace_env()`); if the API refuses a lane's model before any tool ran
(`ModelRejected`), the turn runs once more on the deploy default with the same
resume id and the reply says which model answered. The session is never reset for
it, and no turn executes a third time.

**Non-owners pick from the same list** — the owner's explicit choice, which
deliberately differs from the shell and learning grants: those a non-owner has
only when the owner gives them one per person in Settings → Users (else the
default-off `users_shell_enabled` / `users_learning_enabled`). A model is a
preference that reaches only the person's own lanes; a shell reaches everyone's
files, and learning spends the owner's account. Shared lanes are changed by the
deploy owner alone.

## Consequences

- A **changed default waits for the next session**: a live conversation keeps the
  model its cached prefix is scoped to, and moves only on `/new`, an expiry, or an
  explicit switch. Clearing a lane's explicit pair redirects its live session to
  the lane's default (`set_session_pair`), so "clear" behaves as a switch.
- An **explicit switch keeps the session** and costs one cold re-read of the
  history on the new model; the picker and the Telegram confirmation say so.
- A lane **reset because its owner's shell grant moved** (0009,
  [[flow.message-turn]]) keeps the model the old session ran: the turn resolves
  its pair before `route()` finds the stale shell stamp — the same order as the
  resume-retry path — and `bind` then records the pair that answered on the fresh
  session. Accepted: that pair is then "what the live session runs", so a lane
  with no explicit pair keeps it until the next `/new` or expiry, like any
  live session.
- Every `turn.start` names the requested pair and every `turn.end` the pair that
  answered, plus `fallback_from`, so "which model wrote this" is answerable on
  every surface without a log.
- Background forks do not follow the lane: they run `cli_id(review_model or
  model)` with no effort, because a fork's toolset differs from the lane's and it
  never reads the lane's cache anyway.
- Removing a model from `models:` moves every lane on it at its next turn, one
  cold write each. Adding one is gated by the matrix, per SDK/CLI bump.
- A turn that ran before `0008` has no model keys on its events and shows no
  label; nothing was backfilled.
