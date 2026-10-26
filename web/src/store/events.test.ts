import { afterEach, beforeEach, expect, it, vi } from "vitest";

vi.mock("@/api/client", () => ({
  api: { threads: { events: vi.fn(async () => ({ events: [] })), get: vi.fn() } },
}));
vi.mock("@/api/sse", () => ({ subscribeEvents: vi.fn(() => vi.fn()) }));

import { api } from "@/api/client";
import { subscribeEvents } from "@/api/sse";
import { useStore } from "./index";
import { ev, END_OK } from "@/runtime/fixtures";

beforeEach(() => useStore.setState(useStore.getInitialState()));
afterEach(() => vi.clearAllMocks());

it("open() loads history once, subscribes from the cursor, and folds", async () => {
  vi.mocked(api.threads.events).mockResolvedValueOnce({ events: [
    ev("t1", "turn.start", { channel: "web", user_id: 1 }),
    ev("t1", "user.message", { text: "hi", attachments: [] }),
    ev("t1", "text.block", { text: "yo" }),
    ev("t1", "turn.end", END_OK),
  ] });
  await useStore.getState().open("web:1:x");
  await useStore.getState().open("web:1:x");                        // idempotent
  expect(api.threads.events).toHaveBeenCalledTimes(1);
  expect(subscribeEvents).toHaveBeenCalledTimes(1);
  const [, after] = vi.mocked(subscribeEvents).mock.calls[0]!;
  expect(after).toBe(useStore.getState().cursor["web:1:x"]);
  const f = useStore.getState().folded("web:1:x");
  expect(f.messages).toHaveLength(2);
  expect(useStore.getState().folded("web:1:x")).toBe(f);           // memoised
});

it("push() appends, dedupes by id, and advances the cursor", () => {
  const s = useStore.getState();
  const a = ev("t2", "turn.start", { channel: "web", user_id: 1 });
  s.push("web:1:y", a);
  s.push("web:1:y", a);                                           // duplicate after reconnect
  s.push("web:1:y", ev("t2", "text.delta", { delta: "x" }, false));
  expect(useStore.getState().byThread["web:1:y"]).toHaveLength(2);
  expect(useStore.getState().cursor["web:1:y"]).toBe(a.id);
});

it("a close inside the load leaves no stream behind and shares the one request", async () => {
  // StrictMode's mount/cleanup/mount, and a person clicking through the sidebar
  // faster than a long thread loads. The old slot was a no-op reservation the
  // cleanup deleted, so the second open started over and then overwrote the first
  // one's unsubscribe: an EventSource nobody could close, still pushing deltas
  // (which carry no id and so dedupe against nothing) and holding one of the
  // browser's six connections per origin for the life of the tab.
  const key = "web:1:strict";
  let release!: (page: { events: [] }) => void;
  vi.mocked(api.threads.events).mockReturnValueOnce(new Promise((r) => { release = r; }));
  const first = useStore.getState().open(key);          // effect
  useStore.getState().close(key);                       // cleanup
  const second = useStore.getState().open(key);         // effect again
  release({ events: [] });
  await Promise.all([first, second]);
  expect(api.threads.events).toHaveBeenCalledTimes(1);  // one download, not two
  expect(subscribeEvents).toHaveBeenCalledTimes(1);     // one stream, not two
  const stop = vi.mocked(subscribeEvents).mock.results[0]!.value as () => void;
  useStore.getState().close(key);
  expect(stop).toHaveBeenCalled();                      // and the survivor is closed
});

it("pushMany() writes a batch as one update and dedupes inside it", () => {
  const key = "web:1:batch";
  const a = ev("t3", "text.block", { text: "one" });
  const b = ev("t3", "text.block", { text: "two" });
  let writes = 0;
  let seen = useStore.getState().byThread;
  const off = useStore.subscribe((s) => {
    if (s.byThread !== seen) { seen = s.byThread; writes += 1; }
  });
  useStore.getState().pushMany(key, [a, b, a]);          // the third is a replay
  off();
  // One write, not one per event: this is the whole point of the batch, and the
  // per-event version was quadratic in the length of the thread.
  expect(writes).toBe(1);
  expect(useStore.getState().byThread[key]).toHaveLength(2);
  expect(useStore.getState().cursor[key]).toBe(b.id);
});

it("open() surfaces a thread that will not load instead of rejecting", async () => {
  vi.mocked(api.threads.events).mockRejectedValueOnce(new Error("not_found"));
  await useStore.getState().open("tg:private:1");        // must not reject
  expect(useStore.getState().stream["tg:private:1"]).toBe("error");
  expect(subscribeEvents).not.toHaveBeenCalled();
  vi.mocked(api.threads.events).mockResolvedValueOnce({ events: [] });
  await useStore.getState().open("tg:private:1");        // the slot was released, so a retry works
  expect(subscribeEvents).toHaveBeenCalledTimes(1);
});

it("patches a thread.model into the lane's pair, and re-reads only a LIVE clear", async () => {
  const key = "web:1:model";
  vi.mocked(api.threads.get).mockResolvedValue({
    id: key, title: null, channel: "web", archived: false, running: false, last_event_at: "", created_at: "",
    model: null, effort: null, effective_model: "claude-sonnet-5", effective_effort: null });
  useStore.setState({ threadModel: { [key]: {
    model: null, effort: null, effective_model: "claude-sonnet-5", effective_effort: null } } });

  // Telegram pinned the pair. The event carries the explicit pair alone, and an
  // explicit model IS the effective one — so no read is needed.
  useStore.getState().push(key, ev("", "thread.model", { model: "claude-opus-5", effort: "high" }));
  expect(useStore.getState().threadModel[key]).toMatchObject({ model: "claude-opus-5", effort: "high" });
  expect(api.threads.get).not.toHaveBeenCalled();

  // Cleared: what the lane now falls back to is only known to the server.
  useStore.getState().push(key, ev("", "thread.model", { model: null, effort: null }));
  expect(useStore.getState().threadModel[key]).toMatchObject({ model: null, effort: null });
  await vi.waitFor(() => expect(api.threads.get).toHaveBeenCalledTimes(1));

  // The same event replayed as history is not worth a request of its own.
  useStore.getState().pushMany(key, [ev("", "thread.model", { model: null, effort: null })]);
  expect(api.threads.get).toHaveBeenCalledTimes(1);
});
