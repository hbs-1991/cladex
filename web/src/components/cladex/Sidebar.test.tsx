import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { beforeEach, expect, it, vi } from "vitest";

vi.mock("@/api/client", () => ({ api: { threads: { list: vi.fn(), create: vi.fn() } } }));
vi.mock("@/api/sse", () => ({ subscribeEvents: vi.fn(() => vi.fn()) }));

import { useStore } from "@/store";
import { Sidebar } from "./Sidebar";

beforeEach(() => {
  useStore.setState(useStore.getInitialState());
  useStore.setState({ loaded: true, me: { user_id: 1, role: "owner", default_model: null, default_effort: null }, list: [
    { id: "tg:private:1", title: null, channel: "telegram", archived: false, running: true, last_event_at: "2026-09-08T10:00:00Z", created_at: "" },
    { id: "web:1:a", title: "План на неделю", channel: "web", archived: false, running: false, last_event_at: "2026-09-09T10:00:00Z", created_at: "" },
    { id: "cron:1:r", title: "Утренний дайджест", channel: "cron", archived: false, running: false, last_event_at: "2026-09-07T10:00:00Z", created_at: "" },
  ] });
});

it("lists recent threads newest first, with a channel icon, a running marker and a channel-named fallback", () => {
  render(<MemoryRouter><Sidebar /></MemoryRouter>);
  const titles = screen.getAllByRole("link").map((a) => a.textContent).filter((s) => s && !/CladeX|Calendar|Memory|Customize/.test(s));
  expect(titles).toEqual(["План на неделю", "Telegram chat", "Утренний дайджест"]);   // an untitled lane is named by its channel
  expect(screen.getByLabelText("Telegram")).toBeInTheDocument();       // an icon, not a "TG" badge
  expect(screen.getByLabelText("Scheduled")).toBeInTheDocument();
  expect(screen.queryByText("TG")).toBeNull();
  expect(screen.getByLabelText("Agent is working")).toBeInTheDocument();
  expect(screen.getByText("Recent")).toBeInTheDocument();
});

it("offers a new chat, the calendar, search through the palette, and names who is signed in", () => {
  render(<MemoryRouter><Sidebar /></MemoryRouter>);
  expect(screen.getByRole("button", { name: /New chat/ })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Calendar" })).toHaveAttribute("href", "/calendar");
  expect(screen.getByRole("link", { name: "Memory" })).toHaveAttribute("href", "/memory");
  expect(screen.getByRole("link", { name: "Customize" })).toHaveAttribute("href", "/customize");
  expect(useStore.getState().paletteOpen).toBe(false);
  screen.getByRole("button", { name: "Search chats" }).click();
  expect(useStore.getState().paletteOpen).toBe(true);
  expect(screen.getByText("Owner")).toBeInTheDocument();
});

it("stands in for the recent list until the first page lands, then never again", () => {
  useStore.setState({ loaded: false, list: [] });
  const { rerender } = render(<MemoryRouter><Sidebar /></MemoryRouter>);
  expect(screen.getByRole("status", { name: "Loading your conversations" })).toBeInTheDocument();

  // A later `load()` — the layout remounts on every navigation — must not throw
  // away rows the reader is already looking at to blink at them.
  useStore.setState({ loaded: false, list: [
    { id: "web:1:a", title: "План на неделю", channel: "web", archived: false, running: false, last_event_at: "2026-09-09T10:00:00Z", created_at: "" },
  ] });
  rerender(<MemoryRouter><Sidebar /></MemoryRouter>);
  expect(screen.queryByRole("status", { name: "Loading your conversations" })).toBeNull();
  expect(screen.getByText("План на неделю")).toBeInTheDocument();
});

it("keeps the calendar selected on its tabs and on an old /routines link", () => {
  for (const path of ["/calendar", "/calendar/tasks/3", "/routines"]) {
    const { unmount } = render(<MemoryRouter initialEntries={[path]}><Sidebar /></MemoryRouter>);
    expect(screen.getByRole("link", { name: "Calendar" })).toHaveClass("bg-sidebar-accent");
    expect(screen.getByRole("link", { name: "Memory" })).not.toHaveClass("bg-sidebar-accent");
    unmount();
  }
});

it("speaks Russian when the locale says so", () => {
  useStore.getState().setLocale("ru");
  render(<MemoryRouter><Sidebar /></MemoryRouter>);
  expect(screen.getByRole("button", { name: /Новый чат/ })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Календарь" })).toHaveAttribute("href", "/calendar");
  expect(screen.getByText("Недавние")).toBeInTheDocument();
  useStore.getState().setLocale("en");
});
