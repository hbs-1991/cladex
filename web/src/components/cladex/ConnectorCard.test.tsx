import { render as rtl, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import type React from "react";
import type { ConnectorWire } from "@/api/types";
import { ConnectorCard } from "./ConnectorCard";

const render = (ui: React.ReactElement) => rtl(<MemoryRouter>{ui}</MemoryRouter>);

const base: ConnectorWire = {
  name: "notion", label: "Notion", description: "Pages and databases. Long details follow.", source: "catalog", transport: "http", auth: "oauth",
  secrets: [], state: "available", tools: null, tool_names: [], installed_at: null, endpoint: "https://mcp.example.com/mcp", last_error: null, scopes: ["turn", "cron"],
};

it("connects a catalog entry with one click, then turns the square into the menu", async () => {
  const onAction = vi.fn();
  const { rerender: rr } = render(<ConnectorCard c={base} owner onAction={onAction} />);
  const rerender = (ui: React.ReactElement) => rr(<MemoryRouter>{ui}</MemoryRouter>);
  expect(screen.getByLabelText("From the catalog")).toBeInTheDocument();     // the check next to the name
  expect(screen.getByText("Pages and databases.")).toBeInTheDocument();      // the first sentence only
  expect(screen.getByRole("link", { name: /Notion/ })).toHaveAttribute("href", "/customize/connectors/notion");
  await userEvent.click(screen.getByRole("button", { name: "Connect" }));
  expect(onAction).toHaveBeenCalledWith("connect", base);
  rerender(<ConnectorCard c={{ ...base, state: "connected", tools: 12 }} owner onAction={onAction} />);
  expect(screen.getByText("Connected · 12 tools")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Connect" })).toBeNull();
  await userEvent.click(screen.getByRole("button", { name: "Actions" }));
  expect(await screen.findByRole("menuitem", { name: "Disable" })).toBeInTheDocument();
  expect(screen.getByRole("menuitem", { name: "Disconnect" })).toBeInTheDocument();
  await userEvent.keyboard("{Escape}");                                // an open menu hides the rest of the page from the a11y tree
  rerender(<ConnectorCard c={{ ...base, state: "needs_reauth" }} owner onAction={onAction} />);
  expect(screen.getByText("Needs reconnect")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Actions" }));
  await userEvent.click(await screen.findByRole("menuitem", { name: "Reconnect" }));
  expect(onAction).toHaveBeenLastCalledWith("connect", { ...base, state: "needs_reauth" });
  rerender(<ConnectorCard c={{ ...base, state: "error", last_error: "boom" }} owner onAction={onAction} />);
  expect(screen.getByText("Error: boom")).toBeInTheDocument();
});

it("lets the owner remove a custom entry that never connected", async () => {
  const onAction = vi.fn();
  const c: ConnectorWire = { ...base, name: "my-api", label: "My API", source: "custom", state: "available" };
  render(<ConnectorCard c={c} owner onAction={onAction} />);
  expect(screen.getByText("Custom")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Actions" }));
  await userEvent.click(await screen.findByRole("menuitem", { name: "Remove" }));
  expect(onAction).toHaveBeenCalledWith("disconnect", c);
});

it("shows a plain user the card without controls", () => {
  render(<ConnectorCard c={{ ...base, state: "disabled", source: "custom" }} owner={false} onAction={vi.fn()} />);
  expect(screen.getByText("Disabled")).toBeInTheDocument();
  expect(screen.queryByRole("button")).toBeNull();
});
