import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";

vi.mock("@/api/client", () => ({
  ApiError: class extends Error {
    status: number; code: string;
    constructor(status: number, code: string, message: string) { super(message); this.status = status; this.code = code; }
  },
  api: {
    secrets: { list: vi.fn(), put: vi.fn(), remove: vi.fn() },
    users: { list: vi.fn(), invite: vi.fn(), disable: vi.fn(), enable: vi.fn() },
  },
}));
vi.mock("sonner", () => ({ toast: Object.assign(vi.fn(), { error: vi.fn(), success: vi.fn() }) }));

import { api } from "@/api/client";
import { useStore } from "@/store";
import { SettingsDialog } from "./SettingsDialog";

beforeEach(() => {
  useStore.setState(useStore.getInitialState());
  useStore.setState({ me: { user_id: 1, role: "owner", default_model: null, default_effort: null } });
  useStore.getState().setLocale("en");
  vi.mocked(api.secrets.list).mockReset().mockResolvedValue({ secrets: [] });
  vi.mocked(api.users.list).mockReset().mockResolvedValue({ users: [] });
});

const open = async (v: boolean) => { await act(async () => { useStore.getState().setSettingsOpen(v); }); };

it("opens on the first section and reads it fresh every time", async () => {
  render(<SettingsDialog />);
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  await open(true);
  expect(await screen.findByRole("heading", { name: "Secrets" })).toBeInTheDocument();
  expect(api.secrets.list).toHaveBeenCalledTimes(1);
  await open(false);
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  await open(true);
  // The panel is mounted with the dialog, so reopening re-reads: a secret set from
  // Telegram in the meantime is on screen without a page reload.
  expect(await screen.findByRole("heading", { name: "Secrets" })).toBeInTheDocument();
  expect(api.secrets.list).toHaveBeenCalledTimes(2);
});

it("switches to the Users section, which reads its own data", async () => {
  const user = userEvent.setup();
  render(<SettingsDialog />);
  await open(true);
  await screen.findByRole("heading", { name: "Secrets" });
  expect(api.users.list).not.toHaveBeenCalled();     // a section nobody opened fetches nothing
  await user.click(screen.getByRole("button", { name: "Users" }));
  expect(await screen.findByRole("heading", { name: "Users" })).toBeInTheDocument();
  expect(api.users.list).toHaveBeenCalledTimes(1);
});

it("is not rendered for a plain user", () => {
  useStore.setState({ me: { user_id: 2, role: "user", default_model: null, default_effort: null }, settingsOpen: true });
  render(<SettingsDialog />);
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(api.secrets.list).not.toHaveBeenCalled();
  expect(api.users.list).not.toHaveBeenCalled();
});
