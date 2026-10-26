import { beforeEach, expect, it, vi } from "vitest";

vi.mock("@/api/client", () => ({
  api: { threads: {
    list: vi.fn(async () => ({ threads: [{ id: "web:1:a", title: null, channel: "web", archived: false, running: false, last_event_at: "1", created_at: "1" }], next: null })),
    create: vi.fn(async () => ({ id: "web:1:new", title: null, channel: "web", archived: false, running: false, last_event_at: "2", created_at: "2" })),
    rename: vi.fn(async (id: string, title: string) => ({ id, title, channel: "web", archived: false, running: false, last_event_at: "1", created_at: "1" })),
    archive: vi.fn(async () => undefined),
  } },
}));
vi.mock("@/api/sse", () => ({ subscribeEvents: vi.fn(() => vi.fn()) }));

import { useStore } from "./index";

beforeEach(() => useStore.setState(useStore.getInitialState()));

it("loads, creates at the top, renames locally, archives out of the list", async () => {
  await useStore.getState().load();
  expect(useStore.getState().list.map((t) => t.id)).toEqual(["web:1:a"]);
  const id = await useStore.getState().create();
  expect(id).toBe("web:1:new");
  expect(useStore.getState().list[0]!.id).toBe("web:1:new");
  expect(useStore.getState().currentId).toBe("web:1:new");
  await useStore.getState().rename("web:1:a", "Plan");
  expect(useStore.getState().list.find((t) => t.id === "web:1:a")!.title).toBe("Plan");
  await useStore.getState().archive("web:1:a");
  expect(useStore.getState().list.map((t) => t.id)).toEqual(["web:1:new"]);
});
