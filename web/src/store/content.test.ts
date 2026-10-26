import { beforeEach, expect, it, vi } from "vitest";
import type { ContentItemWire } from "@/api/types";

const item = (over: Partial<ContentItemWire>): ContentItemWire => ({
  id: 1, channel: "instagram", format: "reel", title: "Launch", body: "", media: [], publish_at: "2026-10-01T05:00:00+00:00",
  status: "draft", group_key: null, source_path: null, thread_id: null, created_via: "web", approved_at: null, attempts: 0,
  published_url: null, error: null, needs_attention: false, created_at: "", updated_at: "",
  theme_approved_at: null, draft_attempts: 0, redrafts: 0, draft_comment: null, card_state: "none", ...over,
});

const h = vi.hoisted(() => ({
  calendar: vi.fn(), list: vi.fn(), publisher: vi.fn(), setPublisher: vi.fn(), update: vi.fn(), approve: vi.fn(),
  drafter: vi.fn(), setDrafter: vi.fn(), approveTheme: vi.fn(), approveThemes: vi.fn(),
}));
vi.mock("@/api/client", async (orig) => ({
  ...(await orig<typeof import("@/api/client")>()),
  api: {
    routines: { calendar: h.calendar },
    content: {
      list: h.list, publisher: h.publisher, setPublisher: h.setPublisher, update: h.update, approve: h.approve,
      drafter: h.drafter, setDrafter: h.setDrafter, approveTheme: h.approveTheme, approveThemes: h.approveThemes,
    },
  },
}));
vi.mock("@/api/sse", () => ({ subscribeEvents: vi.fn(() => vi.fn()) }));

import { ApiError } from "@/api/client";
import { useStore } from "./index";

const RANGE = { from: "2026-09-27T19:00:00.000Z", to: "2026-11-01T19:00:00.000Z" };
const ROUTINES = { fires: [{ routine_id: 3, name: "Digest", at: "2026-10-01T04:00:00+00:00" }], dense: [], runs: [] };
const owner = { user_id: 1, role: "owner" as const, default_model: null, default_effort: null };

beforeEach(() => {
  useStore.setState(useStore.getInitialState());
  vi.clearAllMocks();
  h.calendar.mockResolvedValue(ROUTINES);
  h.list.mockResolvedValue({ items: [item({ id: 1 })], backlog: [item({ id: 2, publish_at: null })] });
  h.publisher.mockResolvedValue({ job_id: 4, name: "Publisher", state: "active" });
  h.drafter.mockResolvedValue({ job_id: 5, name: "Drafter", state: "active" });
});

it("reads both endpoints for the owner and keeps the backlog apart", async () => {
  useStore.setState({ me: owner });
  await useStore.getState().loadCalendar(RANGE);
  const s = useStore.getState();
  expect(h.calendar).toHaveBeenCalledWith(RANGE.from, RANGE.to);
  expect(h.list).toHaveBeenCalledWith(RANGE.from, RANGE.to);
  expect(s.calStatus).toBe("ready");
  expect(s.contentEnabled).toBe(true);
  expect(s.publisher).toEqual({ job_id: 4, name: "Publisher", state: "active" });
  expect(s.calItems.map((i) => i.id)).toEqual([1]);
  expect(s.calBacklog.map((i) => i.id)).toEqual([2]);
  expect(s.calRoutines).toEqual(ROUTINES);
});

it("never asks the content module on behalf of someone who is not the owner", async () => {
  useStore.setState({ me: { ...owner, user_id: 2, role: "user" } });
  await useStore.getState().loadCalendar(RANGE);
  expect(h.publisher).not.toHaveBeenCalled();
  expect(h.list).not.toHaveBeenCalled();
  expect(useStore.getState().calRoutines).toEqual(ROUTINES);
  expect(useStore.getState().contentEnabled).toBe(false);
});

it("reads a 404 from the publisher route as the content plan being off, and shows routines alone", async () => {
  useStore.setState({ me: owner });
  h.publisher.mockRejectedValue(new ApiError(404, "not_found", "no such endpoint"));
  await useStore.getState().loadCalendar(RANGE);
  expect(useStore.getState().contentEnabled).toBe(false);
  expect(h.list).not.toHaveBeenCalled();
  expect(useStore.getState().calStatus).toBe("ready");
  expect(useStore.getState().calRoutines).toEqual(ROUTINES);
  // …and it is learned once, not asked again on every range.
  await useStore.getState().loadCalendar(RANGE);
  expect(h.publisher).toHaveBeenCalledTimes(1);
});

it("re-reads the range after a mutation", async () => {
  useStore.setState({ me: owner });
  await useStore.getState().loadCalendar(RANGE);
  h.update.mockResolvedValue(item({ id: 2, publish_at: "2026-10-03T04:00:00.000Z" }));
  h.list.mockResolvedValue({ items: [item({ id: 1 }), item({ id: 2, publish_at: "2026-10-03T04:00:00.000Z" })], backlog: [] });
  await useStore.getState().updateItem(2, { publish_at: "2026-10-03T04:00:00.000Z" });
  expect(h.update).toHaveBeenCalledWith(2, { publish_at: "2026-10-03T04:00:00.000Z" });
  expect(h.list).toHaveBeenCalledTimes(2);
  expect(useStore.getState().calItems.map((i) => i.id)).toEqual([1, 2]);
  expect(useStore.getState().calBacklog).toEqual([]);
});

it("lets a refused mutation surface its server message and leaves the grid as it was", async () => {
  useStore.setState({ me: owner });
  await useStore.getState().loadCalendar(RANGE);
  h.approve.mockRejectedValue(new ApiError(400, "media_missing", "content/img/x.mp4 is not in the agent's home."));
  await expect(useStore.getState().approveItem(1)).rejects.toThrow("content/img/x.mp4 is not in the agent's home.");
  expect(h.list).toHaveBeenCalledTimes(1);
  expect(useStore.getState().calItems.map((i) => i.id)).toEqual([1]);
});

it("drops an answer that a newer range read overtook", async () => {
  useStore.setState({ me: owner });
  let release!: (v: unknown) => void;
  h.calendar.mockReturnValueOnce(new Promise((r) => { release = r; }));
  const older = useStore.getState().loadCalendar(RANGE);
  const NEXT = { from: "2026-11-01T19:00:00.000Z", to: "2026-12-06T19:00:00.000Z" };
  h.list.mockResolvedValueOnce({ items: [item({ id: 1 })], backlog: [] });   // the older read's content
  h.list.mockResolvedValueOnce({ items: [item({ id: 9 })], backlog: [] });   // the newer read's content
  await useStore.getState().loadCalendar(NEXT);
  release({ fires: [], dense: [], runs: [] });
  await older;
  expect(useStore.getState().calRange).toEqual(NEXT);
  expect(useStore.getState().calItems.map((i) => i.id)).toEqual([9]);
  expect(useStore.getState().calRoutines).toEqual(ROUTINES);
});

it("records the publisher the owner picks", async () => {
  useStore.setState({ me: owner });
  h.setPublisher.mockResolvedValue({ job_id: 8, name: "New publisher", state: "active" });
  await useStore.getState().setPublisher(8);
  expect(h.setPublisher).toHaveBeenCalledWith(8);
  expect(useStore.getState().publisher?.job_id).toBe(8);
});

it("reads the drafter next to the publisher, and a failed drafter read decides nothing", async () => {
  useStore.setState({ me: owner });
  await useStore.getState().loadPublisher();
  expect(useStore.getState().drafter).toEqual({ job_id: 5, name: "Drafter", state: "active" });

  useStore.setState({ contentEnabled: null, drafter: null });
  h.drafter.mockRejectedValue(new ApiError(500, "boom", "down"));
  await useStore.getState().loadPublisher();
  expect(useStore.getState().contentEnabled).toBe(true);
  expect(useStore.getState().drafter).toBeNull();
});

it("records the drafter the owner picks", async () => {
  useStore.setState({ me: owner });
  h.setDrafter.mockResolvedValue({ job_id: 9, name: "New drafter", state: "active" });
  await useStore.getState().setDrafter(9);
  expect(h.setDrafter).toHaveBeenCalledWith(9);
  expect(useStore.getState().drafter?.job_id).toBe(9);
});

it("re-reads the range after a theme approval, one or a week of them", async () => {
  useStore.setState({ me: owner });
  await useStore.getState().loadCalendar(RANGE);
  h.approveTheme.mockResolvedValue(item({ id: 1, status: "planned" }));
  await useStore.getState().approveTheme(1);
  expect(h.approveTheme).toHaveBeenCalledWith(1);
  h.approveThemes.mockResolvedValue({ approved: 2, items: [] });
  expect(await useStore.getState().approveThemes(RANGE.from, RANGE.to)).toEqual({ approved: 2, items: [] });
  expect(h.approveThemes).toHaveBeenCalledWith(RANGE.from, RANGE.to);
  expect(h.list).toHaveBeenCalledTimes(3);
});
