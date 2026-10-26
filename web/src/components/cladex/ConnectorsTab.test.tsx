import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router";
import { beforeEach, expect, it, vi } from "vitest";

vi.mock("@/api/client", () => ({
  ApiError: class extends Error {
    status: number; code: string;
    constructor(status: number, code: string, message: string) { super(message); this.status = status; this.code = code; }
  },
  api: { connectors: { list: vi.fn(), connect: vi.fn(), oauthStart: vi.fn(), secrets: vi.fn(), test: vi.fn(), enable: vi.fn(), disable: vi.fn(), remove: vi.fn() } },
}));
vi.mock("sonner", () => ({ toast: Object.assign(vi.fn(), { error: vi.fn() }) }));
vi.mock("@/lib/navigate", () => ({ goTo: vi.fn() }));

import { api } from "@/api/client";
import type { ConnectorWire } from "@/api/types";
import { goTo } from "@/lib/navigate";
import { useStore } from "@/store";
import { toast } from "sonner";
import { ConnectorsTab } from "./ConnectorsTab";

const notion: ConnectorWire = { name: "notion", label: "Notion", description: "d", source: "catalog", transport: "http", auth: "oauth", secrets: [], state: "available", tools: null, tool_names: [], installed_at: null, endpoint: "https://mcp.example.com/mcp", last_error: null, scopes: ["turn"] };
const github: ConnectorWire = { ...notion, name: "github-pat", label: "GitHub (token)", auth: "secret", secrets: [{ name: "GITHUB_PAT", prompt: "A token", set: false }] };

beforeEach(() => {
  useStore.setState(useStore.getInitialState());
  useStore.setState({ me: { user_id: 1, role: "owner", default_model: null, default_effort: null } });
  vi.mocked(api.connectors.list).mockResolvedValue({ connectors: [notion, github] });
  vi.mocked(toast).mockClear();
});

const mount = (path = "/customize/connectors") => render(
  <MemoryRouter initialEntries={[path]}><Routes><Route path="/customize/connectors" element={<ConnectorsTab />} /></Routes></MemoryRouter>,
);

it("starts the OAuth flow for an oauth connector and opens the secrets dialog for a token one", async () => {
  vi.mocked(api.connectors.oauthStart).mockResolvedValue({ url: "https://auth.example/authorize?x=1" });
  mount();
  await userEvent.click((await screen.findAllByRole("button", { name: "Connect" }))[0]!);   // Notion sorts first
  await waitFor(() => expect(goTo).toHaveBeenCalledWith("https://auth.example/authorize?x=1"));
  await userEvent.click(screen.getAllByRole("button", { name: "Connect" })[1]!);
  expect(await screen.findByRole("dialog", { name: "Sign in to GitHub (token)" })).toBeInTheDocument();
  vi.mocked(api.connectors.secrets).mockResolvedValue(undefined);
  vi.mocked(api.connectors.connect).mockResolvedValue({ ...github, state: "connected", tools: 3, secrets: [{ name: "GITHUB_PAT", prompt: "A token", set: true }] });
  await userEvent.type(screen.getByLabelText("A token"), "ghp_x");
  await userEvent.click(screen.getByRole("button", { name: "Save and connect" }));
  await waitFor(() => expect(api.connectors.secrets).toHaveBeenCalledWith("github-pat", { GITHUB_PAT: "ghp_x" }));
  expect(await screen.findByText("Connected · 3 tools")).toBeInTheDocument();
});

it("reads the OAuth return from the query and toasts it", async () => {
  mount("/customize/connectors?connected=notion");
  await waitFor(() => expect(toast).toHaveBeenCalledWith("Notion connected"));
  mount("/customize/connectors?error=state_mismatch");
  await waitFor(() => expect(toast.error).toHaveBeenCalledWith("The sign-in expired or was already used. Try again."));
});
