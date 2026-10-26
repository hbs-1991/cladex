import { beforeEach, expect, it, vi } from "vitest";

const routine = { id: 1, name: "Daily", kind: "interval", value: "3600", schedule: "every 3600s", prompt: "p", state: "active",
  next_run_at: null, last_run_at: null, last_status: null, timeout_seconds: null, thread_id: "tg:private:1", created_at: "" };
vi.mock("@/api/client", () => ({ api: { routines: {
  list: vi.fn(async () => ({ routines: [routine] })), pause: vi.fn(async () => undefined), resume: vi.fn(async () => undefined),
  create: vi.fn(async (b: { name: string }) => ({ ...routine, id: 2, name: b.name })), remove: vi.fn(async () => undefined),
  runs: vi.fn(async () => ({ runs: [{ ts: "1", status: "ok" }] })),
} } }));
vi.mock("@/api/sse", () => ({ subscribeEvents: vi.fn(() => vi.fn()) }));

import { useStore } from "./index";

beforeEach(() => useStore.setState(useStore.getInitialState()));

it("loads, pauses optimistically, creates and removes", async () => {
  await useStore.getState().loadRoutines();
  expect(useStore.getState().routines).toHaveLength(1);
  await useStore.getState().pauseRoutine(1);
  expect(useStore.getState().routines[0]!.state).toBe("paused");
  await useStore.getState().createRoutine({ name: "New", schedule: "every 1h", prompt: "x" });
  expect(useStore.getState().routines.map((r) => r.name)).toEqual(["Daily", "New"]);
  await useStore.getState().removeRoutine(1);
  expect(useStore.getState().routines.map((r) => r.id)).toEqual([2]);
  await useStore.getState().loadRuns(2);
  expect(useStore.getState().runs[2]).toEqual([{ ts: "1", status: "ok" }]);
});
