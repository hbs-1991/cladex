import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { expect, it } from "vitest";
import type { RoutineWire } from "@/api/types";
import { useStore } from "@/store";
import { Routines } from "./Routines";

const PROMPT = "Read the inbox, summarise every thread that changed since yesterday and draft replies for the ones that need one.";

const routine: RoutineWire = {
  id: 1, name: "Morning digest", kind: "cron", value: "0 9 * * *", schedule: "0 9 * * *", prompt: PROMPT,
  state: "active", next_run_at: null, last_run_at: null, last_status: null, timeout_seconds: null,
  thread_id: "cron:1", created_at: "2026-09-01T09:00:00Z", model: null, effort: null,
};

it("clamps a routine's prompt to two lines in the list", () => {
  useStore.setState({ routines: [routine], routinesLoaded: true, loadRoutines: async () => {} });
  render(<MemoryRouter><Routines /></MemoryRouter>);
  const prompt = screen.getByText(PROMPT);
  // `line-clamp-*` works through `display: -webkit-box`; a display utility
  // next to it wins the cascade and the clamp never happens.
  expect(prompt).toHaveClass("line-clamp-2");
  expect(prompt).not.toHaveClass("block");
  expect(prompt).not.toHaveClass("inline");
  expect(prompt).not.toHaveClass("flex");
  expect(prompt.className.split(/\s+/)).not.toContain("inline-block");
});

it("links each task to its page under the calendar and names an on-demand one", () => {
  const manual: RoutineWire = { ...routine, id: 7, name: "Publisher", kind: "manual", value: "", schedule: "manual" };
  useStore.setState({ routines: [routine, manual], routinesLoaded: true, loadRoutines: async () => {} });
  render(<MemoryRouter><Routines /></MemoryRouter>);
  expect(screen.getByRole("link", { name: /Morning digest/ })).toHaveAttribute("href", "/calendar/tasks/1");
  const row = screen.getByRole("link", { name: /Publisher/ });
  expect(row).toHaveAttribute("href", "/calendar/tasks/7");
  expect(row).toHaveTextContent("On demand");
  expect(row).not.toHaveTextContent("next");                 // no clock, so no next run to promise
});

it("offers the owner a content publisher template that opens on demand with a 30-minute timeout", async () => {
  useStore.setState({ routines: [], routinesLoaded: true, loadRoutines: async () => {}, models: [], modelsStatus: "ready",
    me: { user_id: 1, role: "owner", default_model: null, default_effort: null } });
  render(<MemoryRouter><Routines /></MemoryRouter>);
  await userEvent.click(screen.getByRole("button", { name: /Content publisher/ }));
  expect(screen.getByRole("combobox", { name: "Frequency" })).toHaveTextContent("On demand");
  expect(screen.getByLabelText("Timeout, seconds")).toHaveValue(1800);
  expect(screen.getByLabelText(/Instructions/)).toHaveValue(
    "Load the content-publish skill and publish every due item in the content plan. You are unattended: ask nothing.");
});

it("shows no templates, the publisher included, to someone who is not the owner", () => {
  useStore.setState({ routines: [], routinesLoaded: true, loadRoutines: async () => {},
    me: { user_id: 2, role: "user", default_model: null, default_effort: null } });
  render(<MemoryRouter><Routines /></MemoryRouter>);
  expect(screen.queryByRole("button", { name: /Content publisher/ })).toBeNull();
});

it("offers the owner a content drafter template: on demand, 30 minutes, the content-draft skill", async () => {
  useStore.setState({ routines: [], routinesLoaded: true, loadRoutines: async () => {}, models: [], modelsStatus: "ready",
    me: { user_id: 1, role: "owner", default_model: null, default_effort: null } });
  render(<MemoryRouter><Routines /></MemoryRouter>);
  await userEvent.click(screen.getByRole("button", { name: /Content drafter/ }));
  expect(screen.getByRole("combobox", { name: "Frequency" })).toHaveTextContent("On demand");
  expect(screen.getByLabelText("Timeout, seconds")).toHaveValue(1800);
  expect(screen.getByLabelText(/Instructions/)).toHaveValue(
    "Load the content-draft skill, claim one due item from the content plan and draft it. You are unattended: ask nothing.");
});
