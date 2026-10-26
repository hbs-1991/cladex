import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";

vi.mock("@/api/client", () => ({ ApiError: class extends Error {}, api: { connectors: { create: vi.fn() } } }));

import { api } from "@/api/client";
import { AddConnectorDialog } from "./AddConnectorDialog";

// jsdom has no pointer-capture API; radix's Select asks the trigger for it.
Element.prototype.hasPointerCapture ??= () => false;
Element.prototype.releasePointerCapture ??= () => {};

it("validates the name live and posts the form", async () => {
  const onCreated = vi.fn();
  render(<AddConnectorDialog open onOpenChange={() => {}} onCreated={onCreated} />);
  await userEvent.type(screen.getByLabelText("Name"), "My API");
  expect(screen.getByRole("button", { name: "Add" })).toBeDisabled();
  await userEvent.clear(screen.getByLabelText("Name"));
  await userEvent.type(screen.getByLabelText("Name"), "my-api");
  await userEvent.type(screen.getByLabelText("Label"), "My API");
  await userEvent.type(screen.getByLabelText("URL"), "https://api.example.com/mcp");
  await userEvent.click(screen.getByRole("combobox", { name: "Authentication" }));
  await userEvent.click(await screen.findByRole("option", { name: "API token in a header" }));
  await userEvent.type(screen.getByLabelText("Token"), "k-1");
  vi.mocked(api.connectors.create).mockResolvedValue({ name: "my-api", label: "My API", description: "", source: "custom", transport: "http", auth: "secret", secrets: [], state: "available", tools: null, tool_names: [], installed_at: null, endpoint: "https://mcp.example.com/mcp", last_error: null, scopes: [] });
  await userEvent.click(screen.getByRole("button", { name: "Add" }));
  expect(api.connectors.create).toHaveBeenCalledWith({ name: "my-api", label: "My API", url: "https://api.example.com/mcp", auth: "header", header: "Authorization", token: "k-1" });
  expect(onCreated).toHaveBeenCalled();
});
