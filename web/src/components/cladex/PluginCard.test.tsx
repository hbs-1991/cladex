import { render as rtl, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import type React from "react";
import type { PluginWire } from "@/api/types";
import { PluginCard } from "./PluginCard";

const render = (ui: React.ReactElement) => rtl(<MemoryRouter>{ui}</MemoryRouter>);

const base: PluginWire = {
  name: "marketing", label: "Marketing", description: "Campaigns and content. Long details follow.", source: "catalog",
  repo: "https://github.com/anthropics/knowledge-work-plugins", ref: "11ac5eeaca27c764913a06f1e61a7cb712790b1a", subdir: "marketing",
  version: "1.2.0", scopes: ["turn", "cron"], state: "available", present: false, last_error: null, installed_at: null, components: null,
};

it("installs a catalog entry with one click, then turns the square into the menu", async () => {
  const onAction = vi.fn();
  const { rerender: rr } = render(<PluginCard p={base} owner onAction={onAction} />);
  const rerender = (ui: React.ReactElement) => rr(<MemoryRouter>{ui}</MemoryRouter>);
  expect(screen.getByLabelText("From the catalog")).toBeInTheDocument();     // the check next to the name
  expect(screen.getByText("Campaigns and content.")).toBeInTheDocument();    // the first sentence only
  expect(screen.getByRole("link", { name: /Marketing/ })).toHaveAttribute("href", "/customize/plugins/marketing");
  await userEvent.click(screen.getByRole("button", { name: "Install" }));
  expect(onAction).toHaveBeenCalledWith("install", base);

  const installed = { ...base, state: "installed" as const, present: true, installed_at: "2026-09-10T10:00:00Z" };
  rerender(<PluginCard p={installed} owner onAction={onAction} />);
  expect(screen.getByText("Installed")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Install" })).toBeNull();
  await userEvent.click(screen.getByRole("button", { name: "Actions" }));
  expect(await screen.findByRole("menuitem", { name: "Disable" })).toBeInTheDocument();
  expect(screen.getByRole("menuitem", { name: "Remove" })).toBeInTheDocument();
  await userEvent.click(screen.getByRole("menuitem", { name: "Reinstall" }));
  expect(onAction).toHaveBeenLastCalledWith("install", installed);

  rerender(<PluginCard p={{ ...installed, state: "disabled" }} owner onAction={onAction} />);
  expect(screen.getByText("Disabled")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Actions" }));
  await userEvent.click(await screen.findByRole("menuitem", { name: "Enable" }));
  expect(onAction).toHaveBeenLastCalledWith("enable", { ...installed, state: "disabled" });
});

it("says so when the clone is gone and offers the reinstall", async () => {
  const onAction = vi.fn();
  const gone: PluginWire = { ...base, state: "installed", present: false };
  render(<PluginCard p={gone} owner onAction={onAction} />);
  expect(screen.getByText("Files are missing")).toBeInTheDocument();
  expect(screen.getByText("The plugin's files are no longer on disk. Reinstall it.")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Actions" }));
  await userEvent.click(await screen.findByRole("menuitem", { name: "Reinstall" }));
  expect(onAction).toHaveBeenCalledWith("install", gone);
});

it("shows an error row with the git message and marks a custom entry", async () => {
  render(<PluginCard p={{ ...base, source: "custom", state: "error", last_error: "boom" }} owner onAction={vi.fn()} />);
  expect(screen.getByText("Custom")).toBeInTheDocument();
  expect(screen.getByText("Error: boom")).toBeInTheDocument();
  // The manifest is gone from the catalog, so the server has nothing to install:
  // Remove is the only action that can succeed, so it is the only one offered.
  await userEvent.click(screen.getByRole("button", { name: "Actions" }));
  expect(await screen.findByRole("menuitem", { name: "Remove" })).toBeInTheDocument();
  expect(screen.queryByRole("menuitem", { name: "Reinstall" })).toBeNull();
  expect(screen.queryByRole("menuitem", { name: "Install" })).toBeNull();
});

it("offers a plain user disable but not the clone's own actions", async () => {
  // Enable and disable write the caller's own row; reinstall and remove fetch
  // and delete code every tree loads, so those stay the owner's.
  render(<PluginCard p={{ ...base, state: "installed", present: true }} owner={false} onAction={vi.fn()} />);
  expect(screen.getByText("Installed")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Actions" }));
  expect(await screen.findByRole("menuitem", { name: "Disable" })).toBeInTheDocument();
  expect(screen.queryByRole("menuitem", { name: "Reinstall" })).toBeNull();
  expect(screen.queryByRole("menuitem", { name: "Remove" })).toBeNull();
});

it("shows a plain user no control on a plugin no tree has cloned", () => {
  // Install is the only thing to do with it, and installing is the owner's.
  render(<PluginCard p={base} owner={false} onAction={vi.fn()} />);
  expect(screen.queryByRole("button")).toBeNull();
});
