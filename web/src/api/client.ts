/** Typed fetch wrapper over the CladeX REST API. Every server call goes through here. */

import type {
  CalendarWire, CommandWire, ConnectorCreate, ContentInput, ContentItemWire, ContentListWire, ContentRetry, PublisherWire, ApproveThemesWire, ConnectorWire, DraftKind, DraftReview, DraftWire, FileWire, LoginWire, MemberGrants, MemberWire, Me, ModelsWire, PluginDraftCreate, PluginPreview, PluginWire, PublicConfig,
  RejectedDraft, RoutineWire, RunWire, SecretWire, SkillDetail, SkillWire, ThreadDetailWire, ThreadModelWire, ThreadPage, ThreadWire, TurnEvent,
  WikiGraphWire, WikiPageDetail,
} from "./types";

/** The body both model writes take. `model: null` clears the explicit pair —
 *  the effort is ignored then, because a pair without a model is not one. */
export type ModelChoice = { model: string | null; effort: string | null };

export class ApiError extends Error {
  status: number;
  code: string;

  constructor(status: number, code: string, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }
}

const enc = encodeURIComponent;

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(path, { credentials: "same-origin", ...init });
  if (res.status === 204) return undefined as T;
  const ctype = res.headers.get("content-type") ?? "";
  const body = ctype.includes("application/json") ? await res.json() : await res.blob();
  if (!res.ok) {
    // `{"error": …}` is the shell's one error shape; `{"detail": …}` is what a bare
    // FastAPI `HTTPException` would render, read too so no router's refusal ever
    // reaches the reader as a status text instead of its own sentence.
    type Refusal = { code: string; message: string };
    const shaped = body as { error?: Refusal; detail?: Refusal | string };
    const err = shaped?.error ?? (typeof shaped?.detail === "object" ? shaped.detail : undefined);
    throw new ApiError(res.status, err?.code ?? "http_error", err?.message ?? res.statusText);
  }
  return body as T;
}

function json<T>(path: string, method: string, body?: unknown): Promise<T> {
  return request<T>(path, {
    method,
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

export const api = {
  config: () => request<PublicConfig>("/api/config"),
  me: () => request<Me>("/api/me"),
  login: (payload: Record<string, unknown>) => json<LoginWire>("/api/auth/telegram", "POST", payload),
  webappLogin: (initData: string) => json<LoginWire>("/api/auth/webapp", "POST", { init_data: initData }),
  logout: () => json<void>("/api/auth/logout", "POST"),

  /** Which models this deployment offers and what they cost. Open to every
   *  logged-in user — non-owners pick from the same list as the owner. */
  listModels: () => request<ModelsWire>("/api/models"),
  /** This lane's pair. Server-validated (400 `bad_model` / `bad_effort`), and
   *  `model: null` clears the explicit pair AND the session's recorded one, so
   *  the lane falls back to its default on the next turn. */
  setThreadModel: (id: string, body: ModelChoice) =>
    json<ThreadModelWire>(`/api/threads/${enc(id)}/model`, "PUT", body),
  /** The personal default. It applies to new lanes and to a lane's next session,
   *  never to one that is already running. */
  setMyModelDefault: (body: ModelChoice) =>
    json<{ default_model: string | null; default_effort: string | null }>("/api/me/model-default", "PUT", body),

  threads: {
    list: (opts: { before?: string; archived?: boolean; limit?: number } = {}) => {
      const q = new URLSearchParams();
      if (opts.before) q.set("before", opts.before);
      if (opts.archived) q.set("archived", "1");
      if (opts.limit) q.set("limit", String(opts.limit));
      const qs = q.toString();
      return request<ThreadPage>(`/api/threads${qs ? `?${qs}` : ""}`);
    },
    create: () => json<ThreadWire>("/api/threads", "POST", {}),
    get: (id: string) => request<ThreadDetailWire>(`/api/threads/${enc(id)}`),
    rename: (id: string, title: string) => json<ThreadWire>(`/api/threads/${enc(id)}`, "PATCH", { title }),
    archive: (id: string) => json<void>(`/api/threads/${enc(id)}/archive`, "POST"),
    unarchive: (id: string) => json<void>(`/api/threads/${enc(id)}/unarchive`, "POST"),
    events: (id: string, after = 0) =>
      request<{ events: TurnEvent[] }>(`/api/threads/${enc(id)}/events?after=${after}`),
    send: (id: string, body: { text: string; attachment_ids: string[] }) =>
      json<{ accepted: true; thread_id: string }>(`/api/threads/${enc(id)}/messages`, "POST", body),
    cancel: (id: string) => json<void>(`/api/threads/${enc(id)}/cancel`, "POST"),
  },

  asks: {
    answer: (qid: string, answer: string) => json<void>(`/api/asks/${enc(qid)}`, "POST", { answer }),
  },

  uploads: {
    put: (threadId: string, file: File) => {
      const form = new FormData();
      form.append("thread_id", threadId);
      form.append("file", file, file.name);
      return request<FileWire>("/api/uploads", { method: "POST", body: form, headers: {} });
    },
  },

  routines: {
    list: () => request<{ routines: RoutineWire[] }>("/api/routines"),
    get: (id: number) => request<RoutineWire>(`/api/routines/${id}`),
    create: (body: { name: string; schedule: string; prompt: string; timeout_seconds?: number | null;
                     model?: string | null; effort?: string | null }) =>
      json<RoutineWire>("/api/routines", "POST", body),
    update: (id: number, body: Partial<{ name: string; schedule: string; prompt: string; timeout_seconds: number | null;
                                         model: string | null; effort: string | null }>) =>
      json<RoutineWire>(`/api/routines/${id}`, "PATCH", body),
    pause: (id: number) => json<void>(`/api/routines/${id}/pause`, "POST"),
    resume: (id: number) => json<void>(`/api/routines/${id}/resume`, "POST"),
    run: (id: number) => json<void>(`/api/routines/${id}/run`, "POST"),
    remove: (id: number) => json<void>(`/api/routines/${id}`, "DELETE"),
    runs: (id: number) => request<{ runs: RunWire[] }>(`/api/routines/${id}/runs`),
    /** Future fires, dense routines and recorded runs between two instants — the
     *  asking user's routines only. More than 62 days answers 400 `bad_range`. */
    calendar: (from: string, to: string) =>
      request<CalendarWire>(`/api/routines/calendar?from=${enc(from)}&to=${enc(to)}`),
  },

  /** The owner's content plan. Every route is owner-only, and the whole router is
   *  absent when `content_enabled` is off — `publisher()` answering 404 is how the
   *  calendar learns that and shows routines alone. */
  content: {
    list: (from: string, to: string) => request<ContentListWire>(`/api/content?from=${enc(from)}&to=${enc(to)}`),
    get: (id: number) => request<ContentItemWire>(`/api/content/${id}`),
    create: (body: ContentInput) => json<ContentItemWire>("/api/content", "POST", body),
    /** Editing an approved item keeps it approved (the server re-hashes changed
     *  media); `queued`, `publishing` and `published` answer 409 `locked`. */
    update: (id: number, body: Partial<ContentInput>) => json<ContentItemWire>(`/api/content/${id}`, "PATCH", body),
    remove: (id: number) => json<void>(`/api/content/${id}`, "DELETE"),
    /** The human act the publisher waits for: binds the text, the time and a
     *  sha256 per media file. Nothing else can move an item to `scheduled`. */
    approve: (id: number) => json<ContentItemWire>(`/api/content/${id}/approve`, "POST"),
    unapprove: (id: number) => json<ContentItemWire>(`/api/content/${id}/unapprove`, "POST"),
    retry: (id: number, body: ContentRetry) => json<ContentItemWire>(`/api/content/${id}/retry`, "POST", body),
    /** Same-origin and cookie-authenticated, so an `<img>`/`<video>` can use it as is. */
    mediaUrl: (id: number, n: number) => `/api/content/${id}/media/${n}`,
    publisher: () => request<PublisherWire>("/api/content/publisher"),
    setPublisher: (jobId: number) => json<PublisherWire>("/api/content/publisher", "PUT", { job_id: jobId }),
    /** A theme approval: `idea -> planned`. It schedules nothing — the drafter
     *  writes the post, and the post still waits for `approve`. */
    approveTheme: (id: number) => json<ContentItemWire>(`/api/content/${id}/approve-theme`, "POST"),
    unapproveTheme: (id: number) => json<ContentItemWire>(`/api/content/${id}/unapprove-theme`, "POST"),
    /** Every `idea` with `from <= publish_at < to` (at most 62 days). */
    approveThemes: (from: string, to: string) => json<ApproveThemesWire>("/api/content/approve-themes", "POST", { from, to }),
    /** A planned theme the drafter gave up on: its wake budget starts again. */
    retryDrafting: (id: number) => json<ContentItemWire>(`/api/content/${id}/retry-drafting`, "POST"),
    drafter: () => request<PublisherWire>("/api/content/drafter"),
    setDrafter: (jobId: number) => json<PublisherWire>("/api/content/drafter", "PUT", { job_id: jobId }),
  },

  connectors: {
    list: () => request<{ connectors: ConnectorWire[] }>("/api/connectors"),
    create: (body: ConnectorCreate) => json<ConnectorWire>("/api/connectors", "POST", body),
    secrets: (name: string, values: Record<string, string>) => json<void>(`/api/connectors/${enc(name)}/secrets`, "POST", values),
    connect: (name: string) => json<ConnectorWire>(`/api/connectors/${enc(name)}/connect`, "POST"),
    oauthStart: (name: string) => json<{ url: string }>(`/api/connectors/${enc(name)}/oauth/start`, "POST"),
    test: (name: string) => json<{ ok: boolean; tools: string[]; error: string }>(`/api/connectors/${enc(name)}/test`, "POST"),
    enable: (name: string) => json<ConnectorWire>(`/api/connectors/${enc(name)}/enable`, "POST"),
    disable: (name: string) => json<ConnectorWire>(`/api/connectors/${enc(name)}/disable`, "POST"),
    remove: (name: string) => json<void>(`/api/connectors/${enc(name)}`, "DELETE"),
  },

  /** Who may talk to this agent. Owner-only on every path, the read too. */
  users: {
    list: () => request<{ users: MemberWire[] }>("/api/users"),
    invite: (userId: number) => json<MemberWire>("/api/users", "POST", { user_id: userId }),
    disable: (userId: number) => json<MemberWire>(`/api/users/${userId}/disable`, "POST"),
    enable: (userId: number) => json<MemberWire>(`/api/users/${userId}/enable`, "POST"),
    /** Bash (with Agent and Task) and the learning loop, per person. Only a row
     *  can hold a choice: a config-only id answers 404 `not_found` and the owner
     *  409 `owner_immutable`. A shell change restarts that person's lanes on
     *  their next turn — the server does it, not this call. */
    setGrants: (userId: number, grants: MemberGrants) =>
      json<MemberWire>(`/api/users/${userId}`, "PATCH", grants),
  },

  /** Deploy-wide extension credentials, owner-only on every path — the read too. */
  secrets: {
    list: () => request<{ secrets: SecretWire[] }>("/api/secrets"),
    put: (name: string, value: string) => json<void>(`/api/secrets/${enc(name)}`, "PUT", { value }),
    remove: (name: string) => json<void>(`/api/secrets/${enc(name)}`, "DELETE"),
  },

  plugins: {
    list: () => request<{ plugins: PluginWire[] }>("/api/plugins"),
    /** Phase one: fetch and describe. Registers nothing — `confirm` does that.
     *  Also the update path: the clone is removed and re-fetched at the manifest's ref. */
    install: (name: string) => json<PluginPreview>(`/api/plugins/${enc(name)}/install`, "POST"),
    confirmInstall: (name: string, token: string) =>
      json<PluginWire>(`/api/plugins/${enc(name)}/install/confirm`, "POST", { token }),
    enable: (name: string) => json<PluginWire>(`/api/plugins/${enc(name)}/enable`, "POST"),
    disable: (name: string) => json<PluginWire>(`/api/plugins/${enc(name)}/disable`, "POST"),
    remove: (name: string) => json<void>(`/api/plugins/${enc(name)}`, "DELETE"),
  },

  drafts: {
    list: () => request<{ drafts: DraftWire[]; rejected: RejectedDraft[] }>("/api/drafts"),
    /** Files a plugin draft. It is queued, never installed: `review` + `approve`
     *  are still the only path into the catalog. */
    create: (body: PluginDraftCreate) => json<DraftWire>("/api/drafts", "POST", body),
    review: (name: string) => json<DraftReview>(`/api/drafts/${enc(name)}/review`, "POST"),
    /** The token comes from `review` and is bound to the bytes the human read;
     *  a draft rewritten in between answers 409 `stale_draft`. */
    approve: (name: string, token: string) =>
      json<{ name: string; kind: DraftKind }>(`/api/drafts/${enc(name)}/approve`, "POST", { token }),
    discard: (name: string) => json<void>(`/api/drafts/${enc(name)}`, "DELETE"),
  },

  skills: {
    list: () => request<{ skills: SkillWire[] }>("/api/skills"),
    get: (name: string) => request<SkillDetail>(`/api/skills/${enc(name)}`),
    create: (body: { name: string; content: string }) => json<SkillWire>("/api/skills", "POST", body),
    save: (name: string, body: { content: string; etag: string }) => json<SkillDetail>(`/api/skills/${enc(name)}`, "PUT", body),
    pin: (name: string) => json<SkillWire>(`/api/skills/${enc(name)}/pin`, "POST"),
    unpin: (name: string) => json<SkillWire>(`/api/skills/${enc(name)}/unpin`, "POST"),
    archive: (name: string) => json<SkillWire>(`/api/skills/${enc(name)}/archive`, "POST"),
    restore: (name: string) => json<SkillWire>(`/api/skills/${enc(name)}/restore`, "POST"),
  },

  wiki: {
    graph: () => request<WikiGraphWire>("/api/wiki/graph"),
    // `user/nutrition` is one path parameter with a slash in it: each segment is
    // encoded on its own so the separators survive and the names cannot smuggle one.
    page: (path: string) =>
      request<WikiPageDetail>(`/api/wiki/pages/${path.split("/").map(enc).join("/")}`),
  },

  commands: {
    list: () => request<{ commands: CommandWire[] }>("/api/commands"),
  },

  voice: {
    transcribe: (blob: Blob) =>
      request<{ text: string }>("/api/voice/transcribe", {
        method: "POST", body: blob, headers: { "Content-Type": blob.type || "audio/webm" },
      }),
    speak: (text: string) =>
      request<Blob>("/api/voice/speak", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text }),
      }),
  },
};
