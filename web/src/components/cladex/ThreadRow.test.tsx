import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { beforeEach, expect, it, vi } from "vitest";

vi.mock("@/api/client", () => ({ api: { threads: { list: vi.fn(), rename: vi.fn(), archive: vi.fn() } } }));
vi.mock("@/api/sse", () => ({ subscribeEvents: vi.fn(() => vi.fn()) }));

import { useStore } from "@/store";
import { ThreadRow } from "./ThreadRow";

const thread = {
  id: "web:1:a", title: "План на неделю", channel: "web" as const, archived: false, running: false,
  last_event_at: "2026-09-09T10:00:00Z", created_at: "",
};

const rename = vi.fn(async () => {});
const archive = vi.fn(async () => {});

beforeEach(() => {
  useStore.setState(useStore.getInitialState());
  useStore.setState({ rename, archive });
  rename.mockClear();
  archive.mockClear();
});

const row = () => render(<MemoryRouter><ThreadRow t={thread} /></MemoryRouter>);

it("keeps the actions button in the layout, so a touch screen can reach it", () => {
  row();
  // No `hidden`: on a phone there is no hover to reveal it with, and the menu
  // was the only way to rename or archive a thread.
  const trigger = screen.getByRole("button", { name: "Actions" });
  expect(trigger.className).toContain("inline-flex");
  expect(trigger.className).not.toMatch(/(^|\s)hidden(\s|$)/);
});

it("renames through a dialog, not a native prompt", async () => {
  const user = userEvent.setup();
  const prompt = vi.spyOn(window, "prompt");   // never opens in Telegram's webview
  row();
  await user.click(screen.getByRole("button", { name: "Actions" }));
  await user.click(await screen.findByRole("menuitem", { name: "Rename" }));
  const field = await screen.findByLabelText("Title");
  expect(field).toHaveValue("План на неделю");
  await user.clear(field);
  await user.type(field, "Планы");
  await user.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(rename).toHaveBeenCalledWith("web:1:a", "Планы"));
  expect(prompt).not.toHaveBeenCalled();
});

it("asks before archiving, and archives only on the confirmation", async () => {
  const user = userEvent.setup();
  row();
  await user.click(screen.getByRole("button", { name: "Actions" }));
  await user.click(await screen.findByRole("menuitem", { name: "Archive" }));
  const asked = await screen.findByRole("dialog");
  expect(within(asked).getByText(/План на неделю/)).toBeInTheDocument();
  await user.click(within(asked).getByRole("button", { name: "Cancel" }));
  expect(archive).not.toHaveBeenCalled();

  await user.click(screen.getByRole("button", { name: "Actions" }));
  await user.click(await screen.findByRole("menuitem", { name: "Archive" }));
  const dialog = await screen.findByRole("dialog");
  await user.click(within(dialog).getByRole("button", { name: "Archive" }));
  await waitFor(() => expect(archive).toHaveBeenCalledWith("web:1:a"));
});
