/** Wire types for the CladeX backend API: turn events, threads, routines, files,
 *  connectors, plugins, drafts, skills and the content plan. */

export type Channel = "web" | "telegram" | "cron";
export type TurnStatus = "ok" | "error" | "cancelled";

export type AttachmentWire = {
  file_id: string | null; name: string; media_type: string; size: number; url: string | null;
};
export type FileWire = {
  file_id: string; name: string; media_type: string; size: number; url: string;
  telegram_sent?: boolean;
};

/** One model's share of a `turn.end`: the LAST API call's prompt (what sits in
 *  the window), the window itself, and the turn's output. `context_window` is
 *  null when the SDK did not report the model's size. */
export type ModelUsageWire = {
  context_window: number | null; input_tokens: number; cache_read_input_tokens: number;
  cache_creation_input_tokens: number; output_tokens: number;
};

/** One offered model, as `GET /api/models` describes it. `id` is the API id the
 *  database stores and every event names — never the CLI spelling, which may
 *  carry a `[1m]` suffix and never leaves the server. `window` is 0 when the
 *  pinned CLI has no catalog entry for the model, and a window of 0 is unknown,
 *  not empty: the picker never disables a model on it. `price` is USD per 1M
 *  tokens and `cache_write` the multiplier a cold prefix costs (1.25). */
export type ModelWire = {
  id: string; label: string; window: number;
  price: { in: number; out: number };
  cache_write: number; efforts: string[]; default_effort: string | null;
};
/** `deploy_default` is what a lane runs on when nothing — thread, session, user
 *  or job — has recorded a pair; it is always one of `models`. */
export type ModelsWire = {
  models: ModelWire[];
  deploy_default: { model: string; effort: string | null };
};
/** The pair a lane resolves to. `model`/`effort` are the EXPLICIT pair on the
 *  thread row (null when the lane never chose one); `effective_*` are what the
 *  server resolved through the session, the personal default and the deploy
 *  default. An explicit model is always the effective one, which is why the
 *  `thread.model` event can carry the explicit pair alone. */
export type ThreadModelWire = {
  model: string | null; effort: string | null;
  effective_model: string; effective_effort: string | null;
};

export type Payloads = {
  // `model`/`effort` are what the turn REQUESTED (`turn.start`) and what actually
  // answered (`turn.end`); `fallback_from` names the model that was asked for when
  // another one ran. All three are absent on a turn recorded before this feature,
  // which is why the reader treats them as optional rather than "always present".
  "turn.start": { channel: Channel; user_id: number; job_id?: number; run_id?: string;
    model?: string | null; effort?: string | null };
  "user.message": { text: string; attachments: AttachmentWire[] };
  "text.delta": { delta: string };
  "text.block": { text: string };
  "thinking.delta": { delta: string };
  thinking: { text: string };
  "tool.call": { call_id: string; name: string; input: Record<string, unknown> };
  "tool.result": { call_id: string; output: string; is_error: boolean; duration_ms: number };
  "ask.open": { qid: string; question: string; options: string[] };
  "ask.answer": { qid: string; answer: string | null; source: "user" | "timeout" | "cancelled" };
  file: FileWire;
  "thread.title": { title: string };
  /** The lane's EXPLICIT pair changed — from Telegram, or another tab. Persisted
   *  and replayed like `thread.title`, so it carries the explicit pair only: a
   *  cleared pair (`model: null`) tells the reader to re-read `effective_*`. */
  "thread.model": { model: string | null; effort: string | null };
  "turn.end": {
    status: TurnStatus; cost_usd: number | null; sdk_session_id: string | null; error: string | null;
    usage?: Record<string, ModelUsageWire> | null;   // absent on a cancelled/error turn and a cron delivery
    model?: string | null; effort?: string | null; fallback_from?: string | null;
  };
};
export type EventType = keyof Payloads;

export type TurnEvent<T extends EventType = EventType> = {
  id: number | null; session_key: string; turn_id: string; seq: number; ts: string;
  type: T; payload: Payloads[T];
};

export type ThreadWire = {
  id: string; title: string | null; channel: Channel; archived: boolean; running: boolean;
  last_event_at: string; created_at: string;
};
export type ThreadPage = { threads: ThreadWire[]; next: string | null };
/** `GET /api/threads/{id}` — the list route carries no pair, so the picker is the
 *  only reader of these four keys and re-reads them every time it opens. */
export type ThreadDetailWire = ThreadWire & ThreadModelWire;

/** `manual` is the on-demand kind: always `schedule == "manual"`, `value == ""` and
 *  `next_run_at == null` — it fires only through "Run now" (or the content
 *  publisher's wake), never on a clock. */
export type RoutineWire = {
  id: number; name: string; kind: "interval" | "cron" | "once" | "manual"; value: string; schedule: string;
  prompt: string; state: "active" | "paused" | "done"; next_run_at: string | null;
  last_run_at: string | null; last_status: string | null; timeout_seconds: number | null;
  thread_id: string; created_at: string;
  model: string | null; effort: string | null;   // unset = cron_model, then the deploy default
};
export type RunWire = { ts: string; status: string };
/** `default_model`/`default_effort` are this user's personal default for NEW
 *  lanes and for a lane's next session — never for a session already running. */
export type Me = {
  user_id: number; role: "owner" | "user";
  default_model: string | null; default_effort: string | null;
};
/** What the two login routes answer: identity and role only. Their bodies are a
 *  frozen wire (the server's `user_wire` adds the profile and the model default
 *  for `/api/me` alone), so a login is followed by `api.me()` for the rest. */
export type LoginWire = Pick<Me, "user_id" | "role">;
export type PublicConfig = { bot_username: string };
/** What can follow a `/` in the composer: `name` is inserted after the slash. */
export type CommandSource = "sdk" | "skill" | "plugin";
export type CommandWire = { name: string; description: string; source: CommandSource };

export type ConnectorState = "available" | "connected" | "disabled" | "needs_reauth" | "error";
export type ConnectorWire = {
  name: string; label: string; description: string; source: "catalog" | "custom";
  transport: "http" | "sse" | "stdio"; auth: "none" | "secret" | "oauth";
  secrets: { name: string; prompt: string; set: boolean }[];
  state: ConnectorState; tools: number | null; tool_names: string[]; last_error: string | null; scopes: string[];
  installed_at: string | null; endpoint: string;
};
/** One row of Settings -> Users. `source` is `"row"` for somebody the `users`
 *  table knows and `"config"` for an id that is only in `allowed_users` on the
 *  host — admitted all the same, but nothing has ever met them.
 *
 *  `shell` and `learning` are the grants as the server resolved them, never the
 *  raw columns: `*_inherited` says the person has no choice of their own and
 *  follows the deploy default in `config.yaml`, so the value can move when that
 *  default does. The owner is always `true/true` and never inherited; a config
 *  entry carries the defaults, inherited, because there is no row to hold a choice. */
export type MemberWire = {
  user_id: number; role: "owner" | "user"; display_name: string; username: string;
  created_at: string; last_seen_at: string; disabled: boolean; source: "row" | "config";
  shell: boolean; learning: boolean; shell_inherited: boolean; learning_inherited: boolean;
};
/** The body of `PATCH /api/users/{id}`: only the named grants are written, and a
 *  choice is a boolean — there is no way back to "inherit" from here. */
export type MemberGrants = { shell?: boolean; learning?: boolean };

/** One row of Settings -> Secrets. Never a value: `set` says whether one is stored,
 *  `used_by` names the extensions that declare the variable, and a declared name
 *  nobody has filled in yet is a row with `set: false`. */
export type SecretWire = {
  name: string; set: boolean; updated_at: string | null; used_by: string[];
};
export type ConnectorCreate = {
  name: string; label: string; url: string; auth: "none" | "oauth" | "header";
  header?: string; token?: string; client_id?: string; client_secret?: string;
};

/** A plugin's executable surface, read live from the clone — never stored in the
 *  registry, so it is `null` until the plugin is installed. */
export type PluginComponents = {
  commands: string[]; skills: string[]; agents: string[];
  hooks: boolean; mcp: boolean; author: string; plugin_version: string;
};

/** One MCP server a plugin brings with it. The CLI loads these from the plugin's own
 *  files, so they never went through the catalog and have no connector row. */
export type BundledServer = { name: string; transport: string; target: string };

/** What `POST /install` answers: the plugin is fetched and inert, and this is what
 *  the owner reads before `POST /install/confirm` registers it. The token is bound
 *  to `surface`, so a tree that changed in between cannot ride in on the approval. */
export type PluginPreview = {
  name: string; label: string; surface: string; token: string;
  servers: BundledServer[]; components: PluginComponents | null;
};
export type PluginState = "available" | "installed" | "disabled" | "error";
export type PluginWire = {
  name: string; label: string; description: string; source: "catalog" | "custom";
  repo: string; ref: string; subdir: string; version: string; scopes: string[];
  state: PluginState;
  /** The clone is on disk and carries `.claude-plugin/plugin.json`. False on an
   *  installed row means the clone vanished behind the app's back — reinstall. */
  present: boolean;
  last_error: string | null; installed_at: string | null;
  components: PluginComponents | null;
};

export type DraftKind = "mcp" | "plugin";
export type DraftWire = {
  name: string; kind: DraftKind; label: string; description: string;
  transport: string | null; repo: string | null; ref: string | null;
};
/** The body of `POST /api/drafts`: the fields a plugin manifest is made of. The
 *  server fills `label` from `name` and `version` from nothing, and refuses a ref
 *  that is a branch — so the form may send what the owner typed and show the
 *  validator's answer. */
export type PluginDraftCreate = {
  name: string; repo: string; ref: string;
  description: string; label?: string; version?: string; subdir?: string;
  scopes?: string[];
};
/** A queued draft that no longer parses: `pending()` drops it, so it is listed
 *  separately or an operator waits forever for an approval that cannot happen. */
export type RejectedDraft = { file: string; reason: string };
/** The reply to `POST /api/drafts/{name}/review`. `surface` is the executable
 *  surface to render verbatim; `token` is bound to the exact bytes it describes.
 *  `local_command` is the ⚠️ LOCAL COMMAND banner as a flag, so the dialog can
 *  draw a warning strip instead of parsing the string. */
export type DraftReview = {
  name: string; kind: DraftKind; label: string; surface: string; token: string; local_command: boolean;
};

export type SkillSource = "bundled" | "agent" | "user";
export type SkillState = "active" | "stale" | "archived";
export type SkillWire = {
  name: string; title: string; description: string; broken: boolean;
  source: SkillSource; state: SkillState; pinned: boolean;
  use_count: number; patch_count: number;
  created_at: string | null; last_used: string | null; last_patched: string | null;
  files: number; bytes: number;
};
export type SkillFile = { path: string; bytes: number };
/** `etag` is `sha256(SKILL.md)`: `PUT` requires it back, so an edit that raced
 *  the background review pass fails loudly (409 `stale_edit`) instead of
 *  silently discarding whatever the agent just learned. */
export type SkillDetail = SkillWire & { content: string; etag: string; tree: SkillFile[] };

/** One page of `agent_home/wiki` as a graph node. `ns` is the first path segment
 *  — `""` for a legacy page that sits at the wiki root — and it is what the hub
 *  ring is built from, so the browser never needs a namespace list of its own.
 *  `in_index` is false when the agent wrote the page and forgot to list it in
 *  `index.md`: the node is drawn anyway, flagged, because that drift is exactly
 *  what someone opening this page wants to see. */
export type WikiPageWire = {
  path: string; ns: string; title: string; summary: string;
  tags: string[]; updated: string; links: string[]; in_index: boolean;
};
/** `namespaces` is ordered and stable, so the ring is positional: the same wiki
 *  draws the same picture twice. `missing` are `[[targets]]` with no page behind
 *  them — ghost nodes on the graph, not an error. */
export type WikiGraphWire = { pages: WikiPageWire[]; namespaces: string[]; missing: string[] };
/** `etag` is `sha256` of the file: the wiki is read-only here, and it ships now
 *  so the editor this page does not have yet already holds its precondition. */
export type WikiPageDetail = WikiPageWire & { content: string; etag: string };

/** `GET /api/routines/calendar` — the scheduler's half of the calendar. `fires` are
 *  FUTURE fires only (from `max(from, now)`), expanded for every active routine
 *  whose period is an hour or longer; a routine that fires more often is named
 *  once in `dense` and never expanded, so an every-5-minutes check cannot bury a
 *  month under 8 640 chips. `runs` are what actually happened in the range. All
 *  instants are ISO-8601 UTC; the SPA buckets them into the device's local days. */
export type CalendarFireWire = { routine_id: number; name: string; at: string };
export type CalendarDenseWire = { routine_id: number; name: string; every_seconds: number };
export type CalendarRunWire = { routine_id: number; name: string; at: string; status: string };
export type CalendarWire = { fires: CalendarFireWire[]; dense: CalendarDenseWire[]; runs: CalendarRunWire[] };

/** A content-plan item's lifecycle. `scheduled` means APPROVED — only the owner's
 *  approve route reaches it; `queued` onwards belong to the publisher routine.
 *  `planned` is an approved THEME waiting for the drafter routine, and `drafting`
 *  is the drafter holding it: neither schedules anything, and the agent sets
 *  neither. */
export type ContentStatus =
  | "idea" | "planned" | "drafting" | "draft" | "review" | "scheduled" | "queued" | "publishing"
  | "published" | "failed" | "stuck" | "skipped";
/** Where the Telegram approval card for a drafted item is: `pending` waits for the
 *  tick to send it, `sent` carries live buttons, `superseded` was overtaken by an
 *  edit (its buttons no longer approve), `failed` gave up after three sends. */
export type CardState = "none" | "pending" | "sending" | "sent" | "failed" | "superseded";
/** A media file is a path relative to the owner's `agent_home`; `sha256` is taken
 *  at approval and re-checked when the publisher claims the item. */
export type ContentMediaWire = { path: string; sha256: string | null };
/** One publication on one channel. `body` is exactly what gets posted;
 *  `source_path` and `thread_id` are links back to where it came from, never a
 *  source of content. `needs_attention` is the server's verdict (failed, stuck,
 *  queued with an error, an unapproved review past its time, a failed card, a
 *  planned theme the drafter gave up on), so the badge never re-derives the rule.
 *  `theme_approved_at` is the theme approval; `approved_at` stays the approval of
 *  the content itself. `draft_comment` is the owner's "redo it like this". */
export type ContentItemWire = {
  id: number; channel: string; format: string; title: string; body: string;
  media: ContentMediaWire[]; publish_at: string | null; status: ContentStatus;
  group_key: string | null; source_path: string | null; thread_id: string | null; created_via: string;
  approved_at: string | null; attempts: number; published_url: string | null; error: string | null;
  needs_attention: boolean; created_at: string; updated_at: string;
  theme_approved_at: string | null; draft_attempts: number; redrafts: number; draft_comment: string | null;
  card_state: CardState;
};
/** `GET /api/content?from=&to=`: dated items inside the range, and the backlog —
 *  every undated item that is not published or skipped, whatever the range. */
export type ContentListWire = { items: ContentItemWire[]; backlog: ContentItemWire[] };
/** The body of `POST /api/content`; `PATCH` takes any subset. `media` is a list of
 *  paths here (the server hashes them at approval), and a new item may start in
 *  `idea`, `draft` or `review` only. */
export type ContentInput = {
  channel: string; format: string; title: string; body: string; media: string[];
  publish_at: string | null; group_key?: string | null; source_path?: string | null;
  status?: "idea" | "draft" | "review";
};
/** "It failed — post again" or "it did go out, here is where". */
export type ContentRetry = { mode: "repost" } | { mode: "published"; url: string };
/** `GET/PUT /api/content/publisher`: the on-demand routine that publishes due items.
 *  `GET/PUT /api/content/drafter` answers the same shape for the routine that
 *  writes the post for a planned theme. */
export type PublisherWire = { job_id: number | null; name: string | null; state: string | null };
/** `POST /api/content/approve-themes`: every `idea` in the range, now `planned`. */
export type ApproveThemesWire = { approved: number; items: ContentItemWire[] };
