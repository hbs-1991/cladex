import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { beforeEach, expect, it, vi } from "vitest";

vi.mock("@/api/client", () => ({
  ApiError: class extends Error {
    status: number; code: string;
    constructor(status: number, code: string, message: string) { super(message); this.status = status; this.code = code; }
  },
  api: { skills: { list: vi.fn(), create: vi.fn() } },
}));
vi.mock("sonner", () => ({ toast: Object.assign(vi.fn(), { error: vi.fn() }) }));

import { api } from "@/api/client";
import type { SkillWire } from "@/api/types";
import { useStore } from "@/store";
import { SkillsTab } from "./SkillsTab";

const base: SkillWire = {
  name: "weekly-report", title: "Weekly report", description: "How to put the Friday report together.",
  broken: false, source: "user", state: "active", pinned: false, use_count: 2, patch_count: 0,
  created_at: "2026-09-01T09:00:00+00:00", last_used: null, last_patched: null, files: 1, bytes: 512,
};
const invoices: SkillWire = { ...base, name: "invoices", title: "Invoices", description: "Chasing unpaid invoices.", source: "agent" };
const gone: SkillWire = { ...base, name: "old-crm", title: "Old CRM", description: "The CRM we left.", state: "archived" };

beforeEach(() => {
  useStore.setState(useStore.getInitialState());
  useStore.setState({ me: { user_id: 1, role: "owner", default_model: null, default_effort: null } });
  vi.mocked(api.skills.list).mockResolvedValue({ skills: [base, invoices, gone] });
});

const mount = () => render(<MemoryRouter><SkillsTab /></MemoryRouter>);

it("shows the active skills first and switches to the archive on demand", async () => {
  mount();
  expect(await screen.findByText("Weekly report")).toBeInTheDocument();
  expect(screen.getByText("Invoices")).toBeInTheDocument();
  expect(screen.queryByText("Old CRM")).not.toBeInTheDocument();

  await userEvent.click(screen.getByRole("button", { name: "Archived" }));
  expect(screen.getByText("Old CRM")).toBeInTheDocument();
  expect(screen.queryByText("Weekly report")).not.toBeInTheDocument();

  await userEvent.click(screen.getByRole("button", { name: "All" }));
  expect(screen.getByText("Old CRM")).toBeInTheDocument();
  expect(screen.getByText("Weekly report")).toBeInTheDocument();
});

it("filters on the name and on the description", async () => {
  mount();
  await screen.findByText("Weekly report");
  const box = screen.getByRole("searchbox", { name: "Search skills" });
  await userEvent.type(box, "invoic");                       // matches the name
  expect(screen.getByText("Invoices")).toBeInTheDocument();
  expect(screen.queryByText("Weekly report")).not.toBeInTheDocument();

  await userEvent.clear(box);
  await userEvent.type(box, "friday");                       // matches the description, case-insensitively
  expect(screen.getByText("Weekly report")).toBeInTheDocument();
  expect(screen.queryByText("Invoices")).not.toBeInTheDocument();

  await userEvent.clear(box);
  await userEvent.type(box, "zzz");
  expect(screen.getByText("No skill matches that.")).toBeInTheDocument();
});

it("offers New skill to the owner only, and creates one", async () => {
  mount();
  await screen.findByText("Weekly report");
  const created: SkillWire = { ...base, name: "expenses", title: "Expenses", description: "Filing expenses." };
  vi.mocked(api.skills.create).mockResolvedValue(created);
  await userEvent.click(screen.getByRole("button", { name: "New skill" }));
  // The dialog asks which way first: with the agent, or by hand. This is the by-hand form.
  await userEvent.click(screen.getByRole("button", { name: /Write it myself/ }));
  await userEvent.type(screen.getByLabelText("Name"), "expenses");
  await userEvent.clear(screen.getByLabelText("SKILL.md"));
  await userEvent.type(screen.getByLabelText("SKILL.md"), "---{enter}name: expenses{enter}description: Filing expenses.{enter}---{enter}{enter}Steps.");
  await userEvent.click(screen.getByRole("button", { name: "Create" }));
  await waitFor(() => expect(api.skills.create).toHaveBeenCalledWith({
    name: "expenses",
    content: "---\nname: expenses\ndescription: Filing expenses.\n---\n\nSteps.",
  }));
  expect(await screen.findByText("Expenses")).toBeInTheDocument();
});

it("offers New skill to a plain user too — the list is their own library", async () => {
  useStore.setState({ me: { user_id: 2, role: "user", default_model: null, default_effort: null } });
  mount();
  await screen.findByText("Weekly report");
  expect(screen.getByRole("button", { name: "New skill" })).toBeInTheDocument();
});
