import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { beforeEach, expect, it, vi } from "vitest";

vi.mock("@/api/client", () => ({ api: {
  logout: vi.fn(async () => {}), listModels: vi.fn(), setMyModelDefault: vi.fn(),
} }));

import { api } from "@/api/client";
import type { ModelWire } from "@/api/types";
import { useStore } from "@/store";
import { UserMenu } from "./UserMenu";

const model = (over: Partial<ModelWire>): ModelWire => ({
  id: "", label: "", window: 1_000_000, price: { in: 3, out: 15 }, cache_write: 1.25,
  efforts: ["low", "medium", "high"], default_effort: "medium", ...over });

const MODELS = [
  model({ id: "claude-sonnet-5", label: "Sonnet 5" }),
  model({ id: "claude-opus-5", label: "Opus 5", efforts: ["medium", "high", "max"], default_effort: "high" }),
];

beforeEach(() => {
  useStore.setState(useStore.getInitialState());
  useStore.setState({ me: { user_id: 1, role: "owner", default_model: null, default_effort: null } });
  useStore.getState().setLocale("en");
  vi.mocked(api.listModels).mockReset().mockResolvedValue({ models: MODELS, deploy_default: { model: "claude-sonnet-5", effort: null } });
  vi.mocked(api.setMyModelDefault).mockReset();
});

it("names who is signed in and switches the language from the submenu", async () => {
  render(<MemoryRouter><UserMenu /></MemoryRouter>);
  expect(screen.getByText("Owner")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Account menu" }));
  await userEvent.click(await screen.findByRole("menuitem", { name: /Language/ }));
  await userEvent.click(await screen.findByRole("menuitemradio", { name: "Русский" }));
  expect(useStore.getState().locale).toBe("ru");
  expect(screen.getByText("Владелец")).toBeInTheDocument();
});

it("opens Settings for the owner, and offers it to nobody else", async () => {
  const { unmount } = render(<MemoryRouter><UserMenu /></MemoryRouter>);
  await userEvent.click(screen.getByRole("button", { name: "Account menu" }));
  await userEvent.click(await screen.findByRole("menuitem", { name: "Settings" }));
  expect(useStore.getState().settingsOpen).toBe(true);
  unmount();

  useStore.setState({ me: { user_id: 2, role: "user", default_model: null, default_effort: null }, settingsOpen: false });
  render(<MemoryRouter><UserMenu /></MemoryRouter>);
  await userEvent.click(screen.getByRole("button", { name: "Account menu" }));
  expect(await screen.findByRole("menuitem", { name: "Log out" })).toBeInTheDocument();
  expect(screen.queryByRole("menuitem", { name: "Settings" })).not.toBeInTheDocument();
});

it("sets a personal default model, and sends an effort with the model it applies to", async () => {
  vi.mocked(api.setMyModelDefault)
    .mockResolvedValueOnce({ default_model: "claude-opus-5", default_effort: null })
    .mockResolvedValueOnce({ default_model: "claude-opus-5", default_effort: "max" });
  render(<MemoryRouter><UserMenu /></MemoryRouter>);
  await userEvent.click(screen.getByRole("button", { name: "Account menu" }));
  await userEvent.click(await screen.findByRole("menuitem", { name: /Default model/ }));
  expect(await screen.findByRole("menuitemradio", { name: "Deploy default" })).toHaveAttribute("aria-checked", "true");

  await userEvent.click(screen.getByRole("menuitemradio", { name: "Opus 5" }));
  expect(api.setMyModelDefault).toHaveBeenLastCalledWith({ model: "claude-opus-5", effort: null });
  expect(useStore.getState().me?.default_model).toBe("claude-opus-5");

  // The menu stays open, and the levels below are now Opus 5's own, with its
  // default naming the Auto row.
  expect(await screen.findByRole("menuitemradio", { name: "Auto (High)" })).toBeInTheDocument();
  await userEvent.click(screen.getByRole("menuitemradio", { name: "Max" }));
  expect(api.setMyModelDefault).toHaveBeenLastCalledWith({ model: "claude-opus-5", effort: "max" });
  expect(screen.getByText(/For new chats and after \/new/)).toBeInTheDocument();
});

it("offers the same list to a plain user", async () => {
  useStore.setState({ me: { user_id: 2, role: "user", default_model: null, default_effort: null } });
  render(<MemoryRouter><UserMenu /></MemoryRouter>);
  await userEvent.click(screen.getByRole("button", { name: "Account menu" }));
  await userEvent.click(await screen.findByRole("menuitem", { name: /Default model/ }));
  expect(await screen.findByRole("menuitemradio", { name: "Sonnet 5" })).toBeInTheDocument();
  expect(screen.getByRole("menuitemradio", { name: "Opus 5" })).toBeInTheDocument();
});

it("changes the theme without closing the menu, then logs out", async () => {
  render(<MemoryRouter><UserMenu /></MemoryRouter>);
  await userEvent.click(screen.getByRole("button", { name: "Account menu" }));
  await userEvent.click(await screen.findByRole("menuitemradio", { name: "Dark" }));
  expect(useStore.getState().theme).toBe("dark");
  await userEvent.click(screen.getByRole("menuitem", { name: "Log out" }));
  expect(api.logout).toHaveBeenCalled();
  expect(useStore.getState().me).toBeNull();
});
