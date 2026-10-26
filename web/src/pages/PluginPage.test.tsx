import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router";
import { beforeEach, expect, it, vi } from "vitest";

vi.mock("@/api/client", () => ({
  ApiError: class extends Error {
    status: number; code: string;
    constructor(status: number, code: string, message: string) { super(message); this.status = status; this.code = code; }
  },
  api: { plugins: { list: vi.fn(), install: vi.fn(), confirmInstall: vi.fn(), enable: vi.fn(), disable: vi.fn(), remove: vi.fn() } },
}));
vi.mock("sonner", () => ({ toast: Object.assign(vi.fn(), { error: vi.fn() }) }));

import { ApiError, api } from "@/api/client";
import type { PluginWire } from "@/api/types";
import { useStore } from "@/store";
import { toast } from "sonner";
import { PluginPage } from "./PluginPage";

const REF = "11ac5eeaca27c764913a06f1e61a7cb712790b1a";
const base: PluginWire = {
  name: "marketing", label: "Marketing", description: "Campaigns and content. Brand voice, competitor tracking and reporting.",
  source: "catalog", repo: "https://github.com/anthropics/knowledge-work-plugins", ref: REF, subdir: "marketing",
  version: "1.2.0", scopes: ["turn", "cron"], state: "available", present: false, last_error: null, installed_at: null, components: null,
};
const installed: PluginWire = {
  ...base, state: "installed", present: true, installed_at: "2026-09-10T10:00:00Z",
  components: { commands: ["brief"], skills: ["campaign-plan", "brand-voice"], agents: ["copy-reviewer"], hooks: true, mcp: true, author: "Anthropic", plugin_version: "1.2.0" },
};

const seed = (p: PluginWire) => {
  useStore.setState(useStore.getInitialState());
  useStore.setState({ me: { user_id: 1, role: "owner", default_model: null, default_effort: null }, plugins: [p], pluginsLoaded: true });
};
const mount = () => render(
  <MemoryRouter initialEntries={["/customize/plugins/marketing"]}>
    <Routes><Route path="/customize/plugins/:name" element={<PluginPage />} /></Routes>
  </MemoryRouter>,
);

beforeEach(() => { seed(base); vi.mocked(toast).mockClear(); vi.mocked(toast.error).mockClear(); });

it("installs from the page and says what the plugin would add once it is there", async () => {
  // Install is two calls: the first fetches and the dialog shows what came back,
  // the second registers it against the token bound to those bytes.
  vi.mocked(api.plugins.install).mockResolvedValue({
    name: "marketing", label: "Marketing", token: "tok", servers: [],
    surface: "name:        marketing\nskills:      brand-voice", components: installed.components,
  });
  vi.mocked(api.plugins.confirmInstall).mockResolvedValue(installed);
  mount();
  expect(screen.getByText(base.description)).toBeInTheDocument();
  expect(screen.getByText("Install it to see what it adds.")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Install" }));
  await waitFor(() => expect(api.plugins.install).toHaveBeenCalledWith("marketing"));
  expect(await screen.findByText(/brand-voice/)).toBeInTheDocument();
  await userEvent.click(screen.getAllByRole("button", { name: "Install" }).at(-1)!);
  await waitFor(() => expect(api.plugins.confirmInstall).toHaveBeenCalledWith("marketing", "tok"));
  expect(toast).toHaveBeenCalledWith("Marketing installed");
  expect(await screen.findByText("brand-voice")).toBeInTheDocument();
  expect(screen.getByText("brief")).toBeInTheDocument();
  expect(screen.getByText("copy-reviewer")).toBeInTheDocument();
  expect(screen.getByText("Runs its own hooks on every turn.")).toBeInTheDocument();
  expect(screen.getByText("Brings its own tools — a bundled MCP server.")).toBeInTheDocument();
});

it("links the exact revision it fetched and carries the trust note", () => {
  seed(installed);
  mount();
  expect(screen.getByRole("link", { name: /knowledge-work-plugins/ }))
    .toHaveAttribute("href", `https://github.com/anthropics/knowledge-work-plugins/tree/${REF}/marketing`);
  expect(screen.getByText(/pins the exact revision it fetches/)).toBeInTheDocument();
  expect(screen.getByText("Anthropic")).toBeInTheDocument();
  expect(screen.getByText("chat, scheduled")).toBeInTheDocument();
  expect(screen.getByText("1.2.0")).toBeInTheDocument();
});

it("offers the menu once installed and names a failed re-clone inside the dialog", async () => {
  seed({ ...installed, present: false });
  vi.mocked(api.plugins.install).mockRejectedValue(new ApiError(502, "install_failed", "fatal: could not read from remote"));
  mount();
  expect(screen.getByText("The plugin's files are no longer on disk. Reinstall it.")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Actions" }));
  await userEvent.click(await screen.findByRole("menuitem", { name: "Reinstall" }));
  // The fetch is the dialog's first act, so its failure belongs on the dialog and
  // not in a toast behind it: the owner is looking at the thing that just failed.
  expect(await screen.findByRole("alert"))
    .toHaveTextContent("The install failed: fatal: could not read from remote");
  expect(screen.queryByRole("button", { name: "Install" })).toBeDisabled();
});

it("refuses a plugin that ships a server which would run a command here", async () => {
  vi.mocked(api.plugins.install).mockRejectedValue(
    new ApiError(400, "local_server", "discord would run: bun run start"));
  mount();
  await userEvent.click(screen.getByRole("button", { name: "Install" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("discord would run: bun run start");
  expect(api.plugins.confirmInstall).not.toHaveBeenCalled();
});

it("names every server a plugin brings before anything is registered", async () => {
  vi.mocked(api.plugins.install).mockResolvedValue({
    name: "marketing", label: "Marketing", token: "tok", components: installed.components,
    servers: [{ name: "slack", transport: "http", target: "https://mcp.slack.com/mcp" }],
    surface: "name:        marketing",
  });
  mount();
  await userEvent.click(screen.getByRole("button", { name: "Install" }));
  expect(await screen.findByText("It brings 1 MCP server")).toBeInTheDocument();
  expect(screen.getByText(/mcp.slack.com/)).toBeInTheDocument();
  expect(screen.getByText(/never reviewed them/)).toBeInTheDocument();
  expect(api.plugins.confirmInstall).not.toHaveBeenCalled();      // still nothing registered
});

it("does not link a repository an orphan row does not have", () => {
  // The registry row outlived its manifest, so repo/ref/subdir are empty and
  // `treeUrl` would render '<origin>/tree/' — a link that goes nowhere.
  seed({ ...base, source: "custom", state: "error", present: false, repo: "", ref: "", subdir: "", last_error: "no longer in the catalog" });
  mount();
  expect(screen.queryByRole("link", { name: /knowledge-work-plugins/ })).toBeNull();
  expect(screen.getByText("Repository").parentElement).toHaveTextContent("—");
});

it("offers a plain user disable but keeps the clone's actions away", async () => {
  // Enable and disable are this caller's own row; install and remove fetch and
  // delete a clone every tree loads, so those stay the owner's.
  seed(installed);
  useStore.setState({ me: { user_id: 2, role: "user", default_model: null, default_effort: null } });
  mount();
  expect(screen.queryByRole("button", { name: "Install" })).toBeNull();
  await userEvent.click(screen.getByRole("button", { name: "Actions" }));
  expect(await screen.findByRole("menuitem", { name: "Disable" })).toBeInTheDocument();
  expect(screen.queryByRole("menuitem", { name: "Reinstall" })).toBeNull();
  expect(screen.queryByRole("menuitem", { name: "Remove" })).toBeNull();
});
