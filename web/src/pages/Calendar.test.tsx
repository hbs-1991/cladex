import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Navigate, Route, Routes, useLocation } from "react-router";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { CalendarWire, ContentItemWire, RoutineWire } from "@/api/types";

const h = vi.hoisted(() => ({
  calendar: vi.fn(), list: vi.fn(), publisher: vi.fn(), routinesList: vi.fn(), routinesCreate: vi.fn(),
  drafter: vi.fn(), setDrafter: vi.fn(), get: vi.fn(), approveThemes: vi.fn(),
}));
vi.mock("@/api/client", async (orig) => ({
  ...(await orig<typeof import("@/api/client")>()),
  api: {
    routines: { calendar: h.calendar, list: h.routinesList, create: h.routinesCreate },
    content: {
      list: h.list, publisher: h.publisher, drafter: h.drafter, setDrafter: h.setDrafter, get: h.get,
      approveThemes: h.approveThemes, mediaUrl: (id: number, n: number) => `/api/content/${id}/media/${n}`,
    },
  },
}));
vi.mock("@/api/sse", () => ({ subscribeEvents: vi.fn(() => vi.fn()) }));

import { ApiError } from "@/api/client";
import { CalendarBoard } from "@/components/cladex/calendar/CalendarBoard";
import { useStore } from "@/store";
import { Calendar, RoutineRedirect } from "./Calendar";
import { Routines } from "./Routines";

// Local wall times: the grid is the device's calendar, whatever zone the suite runs in.
const iso = (m: number, d: number, h = 0, min = 0) => new Date(2026, m - 1, d, h, min).toISOString();

const item = (over: Partial<ContentItemWire>): ContentItemWire => ({
  id: 1, channel: "instagram", format: "reel", title: "Launch reel", body: "Our new thing.", media: [],
  publish_at: iso(10, 1, 10), status: "scheduled", group_key: null, source_path: null, thread_id: null,
  created_via: "chat", approved_at: iso(9, 29, 12), attempts: 0, published_url: null, error: null,
  needs_attention: false, created_at: "", updated_at: "",
  theme_approved_at: null, draft_attempts: 0, redrafts: 0, draft_comment: null, card_state: "none", ...over,
});
const ROUTINES: CalendarWire = {
  fires: [{ routine_id: 3, name: "Morning digest", at: iso(10, 1, 9) }],
  dense: [{ routine_id: 4, name: "Inbox check", every_seconds: 300 }],
  runs: [],
};
const routine: RoutineWire = {
  id: 3, name: "Morning digest", kind: "cron", value: "0 9 * * *", schedule: "0 9 * * *", prompt: "Digest.",
  state: "active", next_run_at: null, last_run_at: null, last_status: null, timeout_seconds: null,
  thread_id: "cron:3", created_at: "", model: null, effort: null,
};
const owner = { user_id: 1, role: "owner" as const, default_model: null, default_effort: null };

const setPhone = (phone: boolean) => Object.defineProperty(window, "matchMedia", {
  writable: true, configurable: true,
  value: (query: string) => ({
    matches: phone && query.includes("max-width"), media: query, onchange: null,
    addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {}, dispatchEvent: () => false,
  }),
});

/** Where the router is now, so a test can watch a query parameter come and go. */
function Where() {
  const loc = useLocation();
  return <output data-testid="where">{loc.pathname + loc.search}</output>;
}

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Where />
      <Routes>
        <Route path="/calendar" element={<Calendar />}>
          <Route index element={<CalendarBoard />} />
          <Route path="tasks" element={<Routines />} />
          <Route path="tasks/:id" element={<p>routine page</p>} />
        </Route>
        <Route path="/routines" element={<Navigate to="/calendar/tasks" replace />} />
        <Route path="/routines/:id" element={<RoutineRedirect />} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date(2026, 8, 30, 12));        // Wednesday 30 September 2026, local noon
  localStorage.clear();
  setPhone(false);
  useStore.setState(useStore.getInitialState());
  useStore.setState({ me: owner, models: [], modelsStatus: "ready" });
  vi.clearAllMocks();
  h.calendar.mockResolvedValue(ROUTINES);
  h.list.mockResolvedValue({ items: [item({})], backlog: [item({ id: 2, title: "Someday post", publish_at: null, status: "idea" })] });
  h.publisher.mockResolvedValue({ job_id: 7, name: "Content publisher", state: "active" });
  h.drafter.mockResolvedValue({ job_id: null, name: null, state: null });
  h.routinesList.mockResolvedValue({ routines: [routine] });
});

afterEach(() => { vi.useRealTimers(); });

it("titles the page Calendar with Calendar and Tasks tabs", async () => {
  renderAt("/calendar");
  expect(screen.getByRole("heading", { level: 1, name: "Calendar" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Calendar" })).toHaveAttribute("href", "/calendar");
  expect(screen.getByRole("link", { name: "Tasks" })).toHaveAttribute("href", "/calendar/tasks");
  await screen.findByText("Launch reel");
});

it("shows the month by default on a desktop, with the post, the fire and a dense marker on their days", async () => {
  const { container } = renderAt("/calendar");
  expect(screen.getByRole("heading", { level: 2, name: "September 2026" })).toBeInTheDocument();   // October 1st is in its last row
  const day = await waitFor(() => {
    const cell = container.querySelector<HTMLElement>('[data-day="2026-10-01"]');
    expect(cell).not.toBeNull();
    expect(within(cell!).getByText("Launch reel")).toBeInTheDocument();
    return cell!;
  });
  expect(within(day).getByText("Morning digest").closest("a")).toHaveAttribute("href", "/calendar/tasks/3");
  expect(within(day).getByText("≈ 10:00")).toBeInTheDocument();          // approved: the minute is not promised
  expect(within(day).getByText("Inbox check · Every 5 minutes")).toBeInTheDocument();
  // A dense routine is one marker a day, not a chip per fire, and not before today.
  expect(within(container.querySelector<HTMLElement>('[data-day="2026-09-29"]')!).queryByText(/Inbox check/)).toBeNull();
  expect(screen.getByRole("heading", { name: /Unscheduled/ })).toBeInTheDocument();
  expect(screen.getByText("Someday post")).toBeInTheDocument();
});

it("opens the agenda by default on a phone, and remembers a switched view", async () => {
  setPhone(true);
  const { container, unmount } = renderAt("/calendar");
  expect(await screen.findByText("Launch reel")).toBeInTheDocument();
  expect(container.querySelector('[data-view="agenda"]')).not.toBeNull();
  expect(screen.getByRole("button", { name: "Agenda" })).toHaveAttribute("aria-pressed", "true");
  await userEvent.click(screen.getByRole("button", { name: "Week" }));
  expect(container.querySelector('[data-view="week"]')).not.toBeNull();
  expect(localStorage.getItem("cladex.calendar.view")).toBe("week");
  unmount();
  const again = renderAt("/calendar");
  expect(again.container.querySelector('[data-view="week"]')).not.toBeNull();
});

it("marks an item that needs attention", async () => {
  h.list.mockResolvedValue({ items: [item({ status: "failed", needs_attention: true, error: "Media ID is not available" })], backlog: [] });
  renderAt("/calendar");
  expect(await screen.findByRole("img", { name: "Needs attention" })).toBeInTheDocument();
});

it("opens the item dialog from a chip", async () => {
  renderAt("/calendar");
  await userEvent.click(await screen.findByRole("button", { name: /Launch reel/ }));
  const dialog = await screen.findByRole("dialog");
  expect(within(dialog).getByLabelText("Text")).toHaveValue("Our new thing.");
  expect(within(dialog).getByRole("button", { name: "Withdraw approval" })).toBeInTheDocument();
});

it("hides a channel or the routines through the filters", async () => {
  const { container } = renderAt("/calendar");
  await screen.findByText("Launch reel");
  await userEvent.click(screen.getByRole("button", { name: "Filters" }));
  await userEvent.click(await screen.findByRole("menuitemcheckbox", { name: /Instagram/ }));
  await userEvent.click(screen.getByRole("menuitemcheckbox", { name: "Task runs" }));
  await userEvent.keyboard("{Escape}");
  const day = container.querySelector<HTMLElement>('[data-day="2026-10-01"]')!;
  expect(within(day).queryByText("Launch reel")).toBeNull();
  expect(within(day).queryByText("Morning digest")).toBeNull();
  expect(within(day).queryByText(/Inbox check/)).toBeNull();
});

it("shows routines alone, and says why, when the content plan is off", async () => {
  h.publisher.mockRejectedValue(new ApiError(404, "not_found", "no such endpoint"));
  renderAt("/calendar");
  expect(await screen.findByText(/content plan is turned off/)).toBeInTheDocument();
  expect(await screen.findByText("Morning digest")).toBeInTheDocument();
  expect(h.list).not.toHaveBeenCalled();
  expect(screen.queryByRole("button", { name: /New post/ })).toBeNull();
  expect(screen.queryByRole("heading", { name: /Unscheduled/ })).toBeNull();
});

it("shows a non-owner their routines and nothing of the plan", async () => {
  useStore.setState({ me: { ...owner, user_id: 2, role: "user" } });
  renderAt("/calendar");
  expect(await screen.findByText("Morning digest")).toBeInTheDocument();
  expect(h.publisher).not.toHaveBeenCalled();
  expect(h.list).not.toHaveBeenCalled();
  expect(screen.queryByText(/content plan is turned off/)).toBeNull();
});

it("lists the routines under Tasks, and sends the old /routines links there", async () => {
  renderAt("/routines");
  expect(await screen.findByRole("link", { name: /Morning digest/ })).toHaveAttribute("href", "/calendar/tasks/3");
  expect(screen.getByRole("link", { name: "Tasks" })).toHaveAttribute("aria-current", "page");
});

it("redirects an old routine link to the routine under the calendar", () => {
  renderAt("/routines/3");
  expect(screen.getByText("routine page")).toBeInTheDocument();
  expect(screen.queryByRole("heading", { level: 1, name: "Calendar" })).toBeNull();   // a routine gets the whole page
});

it("opens the item a /calendar?item= link names, reading it by id when it is not on screen, and forgets the link on close", async () => {
  h.get.mockResolvedValue(item({ id: 42, title: "From Telegram", body: "Drafted text.", status: "review", publish_at: iso(12, 1, 9) }));
  renderAt("/calendar?item=42");
  const dialog = await screen.findByRole("dialog");
  expect(within(dialog).getByLabelText("Text")).toHaveValue("Drafted text.");
  expect(h.get).toHaveBeenCalledWith(42);
  await userEvent.keyboard("{Escape}");
  await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent(/^\/calendar$/));
  expect(screen.queryByRole("dialog")).toBeNull();
});

it("opens a linked item that is already on screen without asking for it again", async () => {
  renderAt("/calendar?item=1");
  const dialog = await screen.findByRole("dialog");
  expect(within(dialog).getByLabelText("Text")).toHaveValue("Our new thing.");
  expect(h.get).not.toHaveBeenCalled();
});

it("puts the drafter next to the publisher, never offering the publisher's own task for it", async () => {
  const publisherTask: RoutineWire = { ...routine, id: 7, name: "Content publisher", kind: "manual", value: "", schedule: "manual" };
  const spare: RoutineWire = { ...routine, id: 8, name: "Spare task", kind: "manual", value: "", schedule: "manual" };
  h.routinesList.mockResolvedValue({ routines: [routine, publisherTask, spare] });
  h.setDrafter.mockResolvedValue({ job_id: 8, name: "Spare task", state: "active" });
  renderAt("/calendar");
  await userEvent.click(await screen.findByRole("button", { name: "Drafter: No drafter" }));
  const dialog = await screen.findByRole("dialog", { name: "Drafter" });
  await userEvent.click(await within(dialog).findByRole("combobox", { name: "An existing on-demand task" }));
  expect((await screen.findAllByRole("option")).map((o) => o.textContent)).toEqual(["Spare task"]);
  await userEvent.click(screen.getByRole("option", { name: "Spare task" }));
  await userEvent.click(within(dialog).getByRole("button", { name: "Use this task" }));
  await waitFor(() => expect(h.setDrafter).toHaveBeenCalledWith(8));
  expect(await screen.findByRole("button", { name: "Drafter: Spare task" })).toBeInTheDocument();
});

it("creates the drafter from its template: on demand, 30 minutes, the content-draft skill", async () => {
  h.routinesCreate.mockResolvedValue({ ...routine, id: 9, name: "Content drafter", kind: "manual", schedule: "manual" });
  h.setDrafter.mockResolvedValue({ job_id: 9, name: "Content drafter", state: "active" });
  renderAt("/calendar");
  await userEvent.click(await screen.findByRole("button", { name: "Drafter: No drafter" }));
  await userEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Create a drafter task" }));
  await waitFor(() => expect(h.setDrafter).toHaveBeenCalledWith(9));
  expect(h.routinesCreate).toHaveBeenCalledWith({
    name: "Content drafter", schedule: "manual", timeout_seconds: 1800,
    prompt: "Load the content-draft skill, claim one due item from the content plan and draft it. You are unattended: ask nothing.",
  });
});

it("approves this week's themes after saying how many ideas it reaches", async () => {
  const ideas = [
    item({ id: 3, title: "Hook ideas", status: "idea", approved_at: null, publish_at: iso(10, 2, 9) }),
    item({ id: 4, title: "Old idea", status: "idea", approved_at: null, publish_at: iso(9, 29, 9) }),   // past
  ];
  h.list.mockResolvedValue({ items: [item({}), ...ideas], backlog: [] });
  h.approveThemes.mockResolvedValue({ approved: 1, items: [] });
  renderAt("/calendar");
  await screen.findAllByText("Launch reel");
  await userEvent.click(screen.getByRole("button", { name: "Approve week's themes" }));
  const dialog = await screen.findByRole("dialog", { name: "Approve this week's themes" });
  expect(dialog).toHaveTextContent(/28 Sept? – 4 Oct 2026/);                       // this week, from the month view
  expect(await within(dialog).findByText("1 idea will be approved.")).toBeInTheDocument();
  await userEvent.click(within(dialog).getByRole("button", { name: "Approve themes" }));
  await waitFor(() => expect(h.approveThemes).toHaveBeenCalledWith(new Date(2026, 8, 28).toISOString(), new Date(2026, 9, 5).toISOString()));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
});

it("draws planned and drafting items with their own labels", async () => {
  setPhone(true);
  h.list.mockResolvedValue({ items: [
    item({ id: 5, title: "Theme only", status: "planned", approved_at: null, publish_at: iso(9, 30, 15) }),
    item({ id: 6, title: "Being written", status: "drafting", approved_at: null, publish_at: iso(9, 30, 16) }),
  ], backlog: [] });
  renderAt("/calendar");
  expect(await screen.findByText("Theme approved")).toBeInTheDocument();
  expect(screen.getByText("Drafting")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /Theme only/ }).closest("[data-status]")).toHaveAttribute("data-status", "planned");
});
