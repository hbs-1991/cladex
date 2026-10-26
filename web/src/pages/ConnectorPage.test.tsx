import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";
import { beforeEach, expect, it, vi } from "vitest";

vi.mock("@/api/client", () => ({ ApiError: class extends Error {}, api: { connectors: { list: vi.fn(), test: vi.fn() } } }));

import { api } from "@/api/client";
import type { ConnectorWire } from "@/api/types";
import { useStore } from "@/store";
import { ConnectorPage } from "./ConnectorPage";

const github: ConnectorWire = {
  name: "github-pat", label: "GitHub (token)", description: "Repos with a token. Fine-grained tokens work best.", source: "catalog",
  transport: "http", auth: "secret", secrets: [{ name: "GITHUB_PAT", prompt: "A token", set: true }], state: "connected", tools: 3,
  tool_names: ["create_issue", "search_code", "get_pull_request"], last_error: null, scopes: ["turn", "cron"],
  installed_at: "2026-09-09T10:00:00+00:00", endpoint: "https://api.githubcopilot.com/mcp/",
};

beforeEach(() => {
  useStore.setState(useStore.getInitialState());
  useStore.setState({ me: { user_id: 1, role: "owner", default_model: null, default_effort: null } });
  vi.mocked(api.connectors.list).mockResolvedValue({ connectors: [github] });
});

const mount = (name: string) => render(
  <MemoryRouter initialEntries={[`/customize/connectors/${name}`]}>
    <Routes><Route path="/customize/connectors/:name" element={<ConnectorPage />} /></Routes>
  </MemoryRouter>,
);

it("shows the connector in full: state, the whole description, its tools and where it lives", async () => {
  mount("github-pat");
  expect(await screen.findByRole("heading", { name: /GitHub \(token\)/ })).toBeInTheDocument();
  expect(screen.getByText("Connected")).toBeInTheDocument();
  expect(screen.getByText("Repos with a token. Fine-grained tokens work best.")).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Tools" })).toBeInTheDocument();
  for (const n of github.tool_names) expect(screen.getByText(n)).toBeInTheDocument();
  expect(screen.getByText("https://api.githubcopilot.com/mcp/")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "api.githubcopilot.com ↗" })).toHaveAttribute("href", "https://api.githubcopilot.com");
  expect(screen.getByText("chat, scheduled")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: /Connectors/ })).toHaveAttribute("href", "/customize/connectors");
  expect(screen.getByRole("button", { name: "Actions" })).toBeInTheDocument();
});

it("says so when the name is unknown", async () => {
  mount("nope");
  expect(await screen.findByText("No such connector.")).toBeInTheDocument();
});
