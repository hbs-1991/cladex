/**
 * The fake API the smoke test runs against: no backend, no network — every
 * `/api/*` call is fulfilled by Playwright, and `/stream` answers with a
 * ready-made SSE body so one page load replays a whole turn.
 */

import type { Page, Route } from "@playwright/test";

export const THREAD_ID = "web:1:x";

export const thread = {
  id: THREAD_ID,
  title: null,
  channel: "web",
  archived: false,
  running: false,
  last_event_at: new Date().toISOString(),
  created_at: "",
};

/** One `text/event-stream` frame in the shape turns.py writes. */
export const ev = (id: number, turn: string, type: string, payload: unknown) =>
  `id: ${id}\nevent: turn\ndata: ${JSON.stringify({
    id,
    session_key: THREAD_ID,
    turn_id: turn,
    seq: id,
    ts: new Date().toISOString(),
    type,
    payload,
  })}\n\n`;

/** The models this fake deployment offers, and what a lane runs on by default. */
export const MODELS = [
  { id: "claude-sonnet-5", label: "Sonnet 5", window: 1_000_000, price: { in: 3, out: 15 },
    cache_write: 1.25, efforts: ["low", "medium", "high"], default_effort: "medium" },
  { id: "claude-opus-5", label: "Opus 5", window: 1_000_000, price: { in: 15, out: 75 },
    cache_write: 1.25, efforts: ["medium", "high", "max"], default_effort: "high" },
  { id: "claude-haiku-4-5", label: "Haiku 4.5", window: 200_000, price: { in: 1, out: 5 },
    cache_write: 1.25, efforts: [], default_effort: null },
];
export const DEPLOY_DEFAULT: { model: string; effort: string | null } = { model: "claude-sonnet-5", effort: null };
type ThreadModel = { model: string | null; effort: string | null; effective_model: string; effective_effort: string | null };

/** A finished turn: one tool step folded into a group, then the answer. It reports
 *  usage and the model that ran, which is what the meter, the answer label and the
 *  picker's cold-re-read estimate are all made of. */
export const oneToolTurn = () =>
  ev(1, "t1", "turn.start", { channel: "web", user_id: 1, model: "claude-sonnet-5", effort: null }) +
  ev(2, "t1", "user.message", { text: "привет", attachments: [] }) +
  ev(3, "t1", "tool.call", { call_id: "c1", name: "Read", input: { file_path: "a.md" } }) +
  ev(4, "t1", "tool.result", { call_id: "c1", output: "x", is_error: false, duration_ms: 5 }) +
  ev(5, "t1", "text.block", { text: "Ответ готов." }) +
  ev(6, "t1", "turn.end", { status: "ok", cost_usd: 0, sdk_session_id: "s", error: null,
    model: "claude-sonnet-5", effort: "medium", fallback_from: null,
    usage: { "claude-sonnet-5": { context_window: 1_000_000, input_tokens: 2_000,
      cache_read_input_tokens: 40_000, cache_creation_input_tokens: 8_000, output_tokens: 300 } } });

export async function installFakeApi(page: Page, stream: string = oneToolTurn()) {
  await page.route("**/api/me", (r) => r.fulfill({ json: { user_id: 1, role: "owner", default_model: null, default_effort: null } }));
  await page.route(/\/api\/threads(\?.*)?$/, (r) => r.fulfill({ json: { threads: [thread], next: null } }));
  await page.route("**/api/threads/web%3A1%3Ax/events*", (r) => r.fulfill({ json: { events: [] } }));
  // The lane's (model, effort) pair, stateful like the Customize routes: what the
  // PUT records is what the next read of the thread answers, which is how the
  // pill survives a close and a re-open.
  let lane: ThreadModel = { model: null, effort: null, effective_model: DEPLOY_DEFAULT.model, effective_effort: DEPLOY_DEFAULT.effort };
  await page.route("**/api/models", (r) => r.fulfill({ json: { models: MODELS, deploy_default: DEPLOY_DEFAULT } }));
  await page.route("**/api/threads/web%3A1%3Ax/model", (r) => {
    const body = r.request().postDataJSON() as { model: string | null; effort: string | null };
    const known = MODELS.find((m) => m.id === body.model);
    if (body.model && !known) {
      return r.fulfill({ status: 400, json: { error: { code: "bad_model", message: "not offered" } } });
    }
    if (known && body.effort && !known.efforts.includes(body.effort)) {
      return r.fulfill({ status: 400, json: { error: { code: "bad_effort", message: "not taken" } } });
    }
    // Clearing the pair drops back to the deploy default; an explicit model IS
    // the effective one, exactly as `resolve()` reads it.
    lane = body.model
      ? { model: body.model, effort: body.effort, effective_model: body.model, effective_effort: body.effort }
      : { model: null, effort: null, effective_model: DEPLOY_DEFAULT.model, effective_effort: DEPLOY_DEFAULT.effort };
    return r.fulfill({ json: lane });
  });
  await page.route("**/api/me/model-default", (r) => {
    const body = r.request().postDataJSON() as { model: string | null; effort: string | null };
    return r.fulfill({ json: { default_model: body.model, default_effort: body.effort } });
  });
  // Rename (PATCH) and the thread detail (GET), which is what the model picker
  // re-reads every time it opens.
  await page.route("**/api/threads/web%3A1%3Ax", async (r) => {
    if (r.request().method() === "GET") return r.fulfill({ json: { ...thread, ...lane } });
    if (r.request().method() !== "PATCH") return r.fallback();
    const title = (r.request().postDataJSON() as { title: string }).title;
    await r.fulfill({ json: { ...thread, title, ...lane } });
  });
  await page.route("**/api/threads/web%3A1%3Ax/messages", (r) =>
    r.fulfill({ status: 202, json: { accepted: true, thread_id: THREAD_ID } }),
  );
  await page.route("**/api/threads/web%3A1%3Ax/stream*", (r) =>
    r.fulfill({ status: 200, headers: { "content-type": "text/event-stream" }, body: stream }),
  );
  await page.route("**/api/routines", (r) => r.fulfill({ json: { routines: [] } }));
  await page.route("**/api/connectors", (r) => r.fulfill({ json: { connectors: [
    { name: "notion", label: "Notion", description: "Pages and databases.", source: "catalog", transport: "http", auth: "oauth",
      secrets: [], state: "available", tools: null, tool_names: [], last_error: null, scopes: ["turn", "cron"],
      installed_at: null, endpoint: "https://mcp.notion.com/mcp" },
    { name: "github-pat", label: "GitHub (token)", description: "Repos with a token.", source: "catalog", transport: "http", auth: "secret",
      secrets: [{ name: "GITHUB_PAT", prompt: "A GitHub token", set: true }], state: "connected", tools: 30,
      tool_names: ["create_issue", "search_code", "get_pull_request"], last_error: null, scopes: ["turn"],
      installed_at: "2026-09-09T10:00:00+00:00", endpoint: "https://api.githubcopilot.com/mcp/" },
  ] } }));
  await installCustomizeApi(page);
  await installSecretsApi(page);
  await installUsersApi(page);
  await installCalendarApi(page);
}

// ---------------------------------------------------------------------------
// The calendar: the scheduler's `/api/routines/calendar` and the content plan.
//
// Everything is planned for TODAY in the test machine's zone — the browser runs
// in the same zone — so the item and the fire are on screen whatever the date:
// in this month's grid, and on the first day of the agenda. The content routes
// are stateful like the Customize ones: an approve or a PATCH changes what the
// next list answers, which is how a walk sees its own action land.
//
// The v2 fixtures: an idea a few minutes ahead (a theme to approve, alone or with
// its week), a post the drafter is writing, and a drafted post far outside any
// range on screen — what a Telegram card's "Open in calendar" link lands on.
// ---------------------------------------------------------------------------

/** Local wall time today, as the UTC instant the server would answer. */
const today = (h: number, m = 0) => { const d = new Date(); d.setHours(h, m, 0, 0); return d.toISOString(); };

export const CONTENT_TITLE = "Launch reel";
export const FIRE_NAME = "Morning digest";
export const IDEA_TITLE = "Five hooks that work";
export const DRAFTING_ID = 4;
export const FAR_ID = 5;

/** A 1x1 transparent PNG: what a media preview is served in the fake. */
const PIXEL = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==", "base64");

async function installCalendarApi(page: Page) {
  const content = (over: Json): Json => ({
    id: 0, channel: "instagram", format: "reel", title: "", body: "", media: [], publish_at: null, status: "draft",
    group_key: null, source_path: null, thread_id: null, created_via: "web", approved_at: null, attempts: 0,
    published_url: null, error: null, needs_attention: false, created_at: today(8), updated_at: today(8),
    theme_approved_at: null, draft_attempts: 0, redrafts: 0, draft_comment: null, card_state: "none", ...over,
  });
  // Five minutes ahead: still today and still this week, unless the walk runs in
  // the last five minutes of a Sunday.
  const soon = new Date(Date.now() + 5 * 60_000).toISOString();
  const far = new Date(Date.now() + 120 * 86_400_000).toISOString();
  let items: Json[] = [
    content({ id: 1, title: CONTENT_TITLE, body: "Our autumn launch, in thirty seconds.", publish_at: today(10),
      media: [{ path: "content/img/cover.png", sha256: null }, { path: "content/video/launch.mp4", sha256: null }],
      source_path: "content/briefs/launch.md", created_via: "chat" }),
    content({ id: 2, channel: "linkedin", format: "post", title: "Someday post", status: "idea" }),
    content({ id: 3, channel: "instagram", format: "carousel", title: IDEA_TITLE, status: "idea", publish_at: soon, created_via: "chat" }),
    content({ id: DRAFTING_ID, channel: "youtube", format: "short", title: "Being written", status: "drafting", publish_at: today(21),
      theme_approved_at: today(8), created_via: "chat" }),
    content({ id: FAR_ID, channel: "linkedin", format: "post", title: "Drafted far ahead", body: "The drafted text.", status: "review",
      publish_at: far, theme_approved_at: today(8), card_state: "sent", created_via: "cron" }),
  ];
  let publisher: Json = { job_id: 7, name: "Content publisher", state: "active" };
  let drafter: Json = { job_id: null, name: null, state: null };
  const future = (i: Json) => Boolean(i.publish_at) && new Date(String(i.publish_at)).getTime() > Date.now();
  const refuse = (r: Route, code: string, message: string) => r.fulfill({ status: 400, json: { error: { code, message } } });
  const find = (url: string) => items.find((i) => i.id === Number(seg(url)[2]));
  const put = (next: Json) => { items = items.map((i) => (i.id === next.id ? next : i)); return next; };

  await page.route(/\/api\/routines\/calendar(\?.*)?$/, (r) => r.fulfill({ json: {
    fires: [{ routine_id: 3, name: FIRE_NAME, at: today(23, 30) }],
    dense: [{ routine_id: 4, name: "Inbox check", every_seconds: 300 }],
    runs: [{ routine_id: 3, name: FIRE_NAME, at: today(0, 5), status: "ok" }],
  } }));

  await page.route(/\/api\/content(\?.*)?$/, (r) => {
    if (r.request().method() === "POST") {
      const body = r.request().postDataJSON() as Json & { media?: string[] };
      const fresh = content({ ...body, id: Math.max(0, ...items.map((i) => Number(i.id))) + 1,
        media: (body.media ?? []).map((path) => ({ path, sha256: null })), status: body.status ?? "draft" });
      items = [...items, fresh];
      return r.fulfill({ status: 201, json: fresh });
    }
    const q = new URL(r.request().url()).searchParams;
    const from = q.get("from") ?? "", to = q.get("to") ?? "";
    const at = (i: Json) => new Date(String(i.publish_at)).getTime();
    return r.fulfill({ json: {
      items: items.filter((i) => i.publish_at && at(i) >= new Date(from).getTime() && at(i) < new Date(to).getTime()),
      backlog: items.filter((i) => !i.publish_at && !["published", "skipped"].includes(String(i.status))),
    } });
  });
  await page.route(/\/api\/content\/publisher$/, (r) => {
    if (r.request().method() === "PUT") {
      const { job_id } = r.request().postDataJSON() as { job_id: number };
      publisher = { job_id, name: `Task ${job_id}`, state: "active" };
    }
    return r.fulfill({ json: publisher });
  });
  await page.route(/\/api\/content\/drafter$/, (r) => {
    if (r.request().method() === "PUT") {
      const { job_id } = r.request().postDataJSON() as { job_id: number };
      if (job_id === publisher.job_id) return refuse(r, "bad_drafter", "That task already publishes the plan.");
      drafter = { job_id, name: `Task ${job_id}`, state: "active" };
    }
    return r.fulfill({ json: drafter });
  });
  await page.route(/\/api\/content\/approve-themes$/, (r) => {
    const { from, to } = r.request().postDataJSON() as { from: string; to: string };
    const inRange = (i: Json) => {
      const at = new Date(String(i.publish_at)).getTime();
      return at >= new Date(from).getTime() && at < new Date(to).getTime();
    };
    const hit = items.filter((i) => i.status === "idea" && future(i) && inRange(i));
    const done = hit.map((i) => put({ ...i, status: "planned", theme_approved_at: new Date().toISOString() }));
    return r.fulfill({ json: { approved: done.length, items: done } });
  });
  await page.route(/\/api\/content\/\d+$/, (r) => {
    const it = find(r.request().url());
    if (!it) return r.fulfill({ status: 404, json: { error: { code: "not_found", message: "no such item" } } });
    const method = r.request().method();
    if (method === "DELETE") { items = items.filter((i) => i !== it); return r.fulfill({ status: 204, body: "" }); }
    if (method === "PATCH") {
      if (["queued", "publishing", "published"].includes(String(it.status))) {
        return r.fulfill({ status: 409, json: { error: { code: "locked", message: "The publisher has this item." } } });
      }
      const body = r.request().postDataJSON() as Json & { media?: string[] };
      const media = body.media ? body.media.map((path) => ({ path, sha256: null })) : it.media;
      return r.fulfill({ json: put({ ...it, ...body, media }) });
    }
    return r.fulfill({ json: it });
  });
  await page.route(/\/api\/content\/\d+\/(approve|unapprove|retry|approve-theme|unapprove-theme|retry-drafting)$/, (r) => {
    const it = find(r.request().url());
    if (!it) return r.fulfill({ status: 404, json: { error: { code: "not_found", message: "no such item" } } });
    const action = seg(r.request().url())[3];
    if (action === "approve-theme") {
      if (it.status !== "idea") return refuse(r, "not_an_idea", "Only an idea's theme can be approved.");
      if (!future(it)) return refuse(r, "no_publish_at", "Give it a time in the future first.");
      return r.fulfill({ json: put({ ...it, status: "planned", theme_approved_at: new Date().toISOString() }) });
    }
    if (action === "unapprove-theme") {
      if (it.status !== "planned") return refuse(r, "not_planned", "The theme is not approved.");
      return r.fulfill({ json: put({ ...it, status: "idea", theme_approved_at: null }) });
    }
    if (action === "retry-drafting") {
      if (it.status !== "planned" || !it.error) return refuse(r, "not_retryable", "Nothing to retry.");
      return r.fulfill({ json: put({ ...it, error: null, draft_attempts: 0, needs_attention: false }) });
    }
    if (action === "approve") {
      if (!it.publish_at) return r.fulfill({ status: 400, json: { error: { code: "no_publish_at", message: "Give it a time first." } } });
      if (!String(it.body).trim()) return refuse(r, "empty_body", "The post has no text.");
      return r.fulfill({ json: put({ ...it, status: "scheduled", approved_at: new Date().toISOString(),
        media: (it.media as Json[]).map((m) => ({ ...m, sha256: "a".repeat(64) })) }) });
    }
    if (action === "unapprove") return r.fulfill({ json: put({ ...it, status: "review", approved_at: null }) });
    const body = r.request().postDataJSON() as { mode: string; url?: string };
    return r.fulfill({ json: put(body.mode === "repost"
      ? { ...it, status: "queued", attempts: 0, error: null, needs_attention: false }
      : { ...it, status: "published", published_url: body.url, needs_attention: false }) });
  });
  await page.route(/\/api\/content\/\d+\/media\/\d+$/, (r) =>
    r.fulfill({ status: 200, headers: { "content-type": "image/png" }, body: PIXEL }));
}

/** Settings -> Users. Stateful: inviting, revoking and granting change what the
 *  next GET answers, and the owner has no control of their own. The deploy
 *  defaults are off, so a member nobody has chosen for reads `false`, inherited —
 *  the "default" marker the grants walk watches disappear. */
async function installUsersApi(page: Page) {
  type Member = { user_id: number; role: string; display_name: string; username: string;
                  created_at: string; last_seen_at: string; disabled: boolean; source: string;
                  shell: boolean; learning: boolean; shell_inherited: boolean; learning_inherited: boolean };
  const member = (over: Partial<Member>): Member => ({
    user_id: 0, role: "user", display_name: "", username: "", created_at: "2026-09-01T09:00:00+00:00",
    last_seen_at: "2026-09-21T09:00:00+00:00", disabled: false, source: "row",
    shell: false, learning: false, shell_inherited: true, learning_inherited: true, ...over });
  let members: Member[] = [
    member({ user_id: 1, role: "owner", display_name: "Vera", username: "vera",
      shell: true, learning: true, shell_inherited: false, learning_inherited: false }),
    member({ user_id: 2, username: "mate" }),
    // Only in `allowed_users`: no row, so no place to keep a grant.
    member({ user_id: 777, source: "config", created_at: "", last_seen_at: "" }),
  ];

  await page.route(/\/api\/users$/, (r) => {
    if (r.request().method() !== "POST") return r.fulfill({ json: { users: members } });
    const id = Number((r.request().postDataJSON() as { user_id: number }).user_id);
    if (members.some((m) => m.user_id === id)) {
      return r.fulfill({ status: 409, json: { error: { code: "user_exists", message: "already on the list" } } });
    }
    const fresh = member({ user_id: id });
    members = [...members, fresh];
    return r.fulfill({ status: 201, json: fresh });
  });
  await page.route(/\/api\/users\/\d+\/(disable|enable)$/, (r) => {
    const parts = seg(r.request().url());
    const id = Number(parts[2]);
    const disabled = parts[3] === "disable";
    members = members.map((m) => (m.user_id === id ? { ...m, disabled } : m));
    return r.fulfill({ json: members.find((m) => m.user_id === id) });
  });
  // The grants. Anchored at the id, so `/disable` and `/enable` above never reach
  // it, and refused the way the server refuses: no row (a config-only id
  // included) is 404 and never becomes one, the owner is 409, a body with no
  // boolean grant is 400. Only the named grant is written, and writing it ends
  // the inheritance, which is what clears the "default" marker.
  await page.route(/\/api\/users\/\d+$/, (r) => {
    if (r.request().method() !== "PATCH") return r.fallback();
    const id = Number(seg(r.request().url())[2]);
    const target = members.find((m) => m.user_id === id && m.source === "row");
    if (!target) return r.fulfill({ status: 404, json: { error: { code: "not_found", message: "no such user" } } });
    if (target.role === "owner") {
      return r.fulfill({ status: 409, json: { error: { code: "owner_immutable", message: "the owner" } } });
    }
    const body = r.request().postDataJSON() as { shell?: unknown; learning?: unknown };
    const shell = typeof body.shell === "boolean" ? body.shell : undefined;
    const learning = typeof body.learning === "boolean" ? body.learning : undefined;
    if (shell === undefined && learning === undefined) {
      return r.fulfill({ status: 400, json: { error: { code: "bad_request", message: "no grant" } } });
    }
    const next: Member = {
      ...target,
      ...(shell === undefined ? {} : { shell, shell_inherited: false }),
      ...(learning === undefined ? {} : { learning, learning_inherited: false }),
    };
    members = members.map((m) => (m.user_id === id ? next : m));
    return r.fulfill({ json: next });
  });
}

/** Settings -> Secrets. Stateful like the Customize routes: what PUT and DELETE
 *  change is what the next GET answers, and a value is never in a response. */
async function installSecretsApi(page: Page) {
  let stored: Record<string, string> = { OPENAI_API_KEY: "2026-09-14T10:00:00+00:00" };
  const declared: Record<string, string[]> = { GITHUB_PAT: ["GitHub (token)"] };
  const list = () => [...new Set([...Object.keys(stored), ...Object.keys(declared)])].sort()
    .map((name) => ({ name, set: name in stored, updated_at: stored[name] ?? null,
                      used_by: declared[name] ?? [] }));

  await page.route(/\/api\/secrets$/, (r) => r.fulfill({ json: { secrets: list() } }));
  await page.route(/\/api\/secrets\/[^/]+$/, (r) => {
    const name = seg(r.request().url())[2];
    if (r.request().method() === "DELETE") {
      stored = Object.fromEntries(Object.entries(stored).filter(([k]) => k !== name));
    } else {
      stored = { ...stored, [name]: new Date().toISOString() };
    }
    return r.fulfill({ status: 204, body: "" });
  });
}

// ---------------------------------------------------------------------------
// Plugins, drafts and skills.
//
// Unlike the blocks above, these routes are STATEFUL: installing a plugin,
// approving a draft and saving a SKILL.md each change what the next GET
// answers, which is the entire point of the three Customize walks. The state is
// built fresh inside `installCustomizeApi`, so it is per page and one test
// cannot leak into the next.
// ---------------------------------------------------------------------------

const REPO = "https://github.com/anthropics/knowledge-work-plugins";

const INSTALLED_COMPONENTS = (version: string) => ({
  commands: [], skills: ["brand-voice", "campaign-brief"], agents: [],
  hooks: false, mcp: true, author: "Anthropic", plugin_version: version,
});
const REF = "11ac5eeaca27c764913a06f1e61a7cb712790b1a";

type Json = Record<string, unknown>;

const plugin = (over: Json): Json => ({
  name: "", label: "", description: "", source: "catalog",
  repo: REPO, ref: REF, subdir: "", version: "1.0.0", scopes: ["turn", "cron"],
  state: "available", present: false, last_error: null, installed_at: null, components: null,
  ...over,
});

/** The surface a review returns. Deliberately carries a `#` line: the dialog must
 *  show it as written, and this is the assertion that proves nothing renders it. */
export const FINANCE_SURFACE = [
  "plugin: finance",
  `repo: ${REPO}`,
  `ref: ${REF}`,
  "subdir: finance",
  "",
  "# this line is part of the draft, not a heading",
  "skills: budget-review, variance-analysis",
].join("\n");

export const SKILL_MD =
  "---\nname: weekly-report\ndescription: How to put the Friday report together.\n---\n\n" +
  "## Steps\n\nAsk finance for the numbers, then write the summary.\n";

/** The path segments after `/api/`, so a handler can read the name it was called with. */
const seg = (url: string) => new URL(url).pathname.split("/").filter(Boolean);

async function installCustomizeApi(page: Page) {
  const plugins: Json[] = [
    plugin({ name: "marketing", label: "Marketing", subdir: "marketing", version: "1.2.0",
      description: "Content, campaigns and performance analysis. Skills only; adds no tools." }),
    plugin({ name: "design", label: "Design", subdir: "design", version: "1.1.0",
      description: "Design reviews and specs.", state: "installed", present: true,
      installed_at: "2026-09-09T10:00:00+00:00",
      components: { commands: ["spec"], skills: ["design-review"], agents: [], hooks: false, mcp: false,
        author: "Anthropic", plugin_version: "1.1.0" } }),
  ];
  let drafts: Json[] = [
    { name: "finance", kind: "plugin", label: "Finance", repo: REPO, ref: REF, transport: null,
      description: "Budgets, forecasts and variance analysis. Skills only; adds no tools." },
  ];
  const skill: Json = {
    name: "weekly-report", title: "Weekly report", description: "How to put the Friday report together.",
    broken: false, source: "user", state: "active", pinned: false, use_count: 3, patch_count: 1,
    created_at: "2026-09-01T09:00:00+00:00", last_used: "2026-09-09T12:00:00+00:00",
    last_patched: "2026-09-05T12:00:00+00:00", files: 2, bytes: 1024,
  };
  let content = SKILL_MD;
  let etag = "sha-1";
  const detail = () => ({ ...skill, content, etag, tree: [{ path: "references/tone.md", bytes: 812 }] });

  await page.route(/\/api\/plugins$/, (r) => r.fulfill({ json: { plugins } }));
  // Phase one: fetched and described, nothing registered. The surface is what the
  // dialog shows and the token is bound to it.
  await page.route(/\/api\/plugins\/[^/]+\/install$/, (r) => {
    const [, , name] = seg(r.request().url());
    const i = plugins.findIndex((p) => p.name === name);
    if (i < 0) return r.fulfill({ status: 404, json: { error: { code: "not_found", message: "no such plugin" } } });
    return r.fulfill({ json: {
      name, label: String(plugins[i].label),
      surface: `name:        ${name}
skills:      brand-voice, campaign-brief
hooks:       no`,
      token: `tok-${name}`,
      servers: [{ name: "slack", transport: "http", target: "https://mcp.slack.com/mcp" }],
      components: INSTALLED_COMPONENTS(String(plugins[i].version)),
    } });
  });
  await page.route(/\/api\/plugins\/[^/]+\/install\/confirm$/, (r) => {
    const [, , name] = seg(r.request().url());
    const i = plugins.findIndex((p) => p.name === name);
    plugins[i] = { ...plugins[i], state: "installed", present: true,
      installed_at: "2026-09-10T12:00:00+00:00",
      components: INSTALLED_COMPONENTS(String(plugins[i].version)) };
    return r.fulfill({ json: plugins[i] });
  });
  await page.route(/\/api\/plugins\/[^/]+\/(enable|disable)$/, (r) => {
    const [, , name, action] = seg(r.request().url());
    const i = plugins.findIndex((p) => p.name === name);
    if (i < 0) return r.fulfill({ status: 404, json: { error: { code: "not_found", message: "no such plugin" } } });
    plugins[i] = { ...plugins[i], state: action === "disable" ? "disabled" : "installed" };
    return r.fulfill({ json: plugins[i] });
  });
  await page.route(/\/api\/plugins\/[^/]+$/, (r) => r.fulfill({ status: 204, body: "" }));

  // One path, two methods: GET is the queue, POST files a plugin manifest the owner
  // typed. The fake echoes it the way the server does — queued, never installed.
  await page.route(/\/api\/drafts$/, (r) => {
    if (r.request().method() !== "POST") return r.fulfill({ json: { drafts, rejected: [] } });
    const body = r.request().postDataJSON() as Record<string, string>;
    const d: Json = { name: body.name, kind: "plugin", label: body.label || body.name,
      description: body.description, transport: null, repo: body.repo, ref: body.ref };
    drafts = [...drafts, d];
    return r.fulfill({ status: 201, json: d });
  });
  await page.route(/\/api\/drafts\/[^/]+\/review$/, (r) => r.fulfill({ json: {
    name: "finance", kind: "plugin", label: "Finance", surface: FINANCE_SURFACE,
    token: "tok-finance-1", local_command: false,
  } }));
  await page.route(/\/api\/drafts\/[^/]+\/approve$/, (r) => {
    const body = r.request().postDataJSON() as { token: string };
    if (body.token !== "tok-finance-1") {
      return r.fulfill({ status: 409, json: { error: { code: "stale_draft", message: "the draft changed" } } });
    }
    drafts = [];
    plugins.push(plugin({ name: "finance", label: "Finance", subdir: "finance", source: "custom",
      description: "Budgets, forecasts and variance analysis. Skills only; adds no tools." }));
    return r.fulfill({ json: { name: "finance", kind: "plugin" } });
  });
  await page.route(/\/api\/drafts\/[^/]+$/, (r) => { drafts = []; return r.fulfill({ status: 204, body: "" }); });

  await page.route(/\/api\/skills$/, (r) => r.fulfill({ json: { skills: [skill] } }));
  await page.route(/\/api\/skills\/[^/]+\/(pin|unpin|archive|restore)$/, (r) => {
    const action = seg(r.request().url())[3];
    if (action === "pin" || action === "unpin") skill.pinned = action === "pin";
    else skill.state = action === "archive" ? "archived" : "active";
    return r.fulfill({ json: skill });
  });
  await page.route(/\/api\/skills\/[^/]+$/, (r) => {
    if (r.request().method() !== "PUT") return r.fulfill({ json: detail() });
    const body = r.request().postDataJSON() as { content: string; etag: string };
    // The etag is the whole point of the route: a save against text nobody read
    // is refused rather than silently winning.
    if (body.etag !== etag) {
      return r.fulfill({ status: 409, json: { error: { code: "stale_edit", message: "changed under you" } } });
    }
    content = body.content;
    etag = `sha-${Number(etag.slice(4)) + 1}`;
    skill.patch_count = Number(skill.patch_count) + 1;
    return r.fulfill({ json: detail() });
  });
}
