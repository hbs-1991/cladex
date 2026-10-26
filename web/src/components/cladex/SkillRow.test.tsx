import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { expect, it } from "vitest";

import type { SkillWire } from "@/api/types";
import { SkillRow } from "./SkillRow";

const base: SkillWire = {
  name: "weekly-report", title: "Weekly report", description: "How to put the Friday report together.",
  broken: false, source: "user", state: "active", pinned: false, use_count: 0, patch_count: 0,
  created_at: "2026-09-01T09:00:00+00:00", last_used: null, last_patched: null, files: 1, bytes: 512,
};

const mount = (s: SkillWire) => render(<MemoryRouter><ul><SkillRow s={s} /></ul></MemoryRouter>);

it("shows the badges the wire earns and how often the agent used it", () => {
  mount({ ...base, source: "bundled", state: "stale", pinned: true, use_count: 4, last_used: "2026-09-08T09:00:00+00:00" });
  expect(screen.getByText("Bundled")).toBeInTheDocument();
  expect(screen.getByText("Pinned")).toBeInTheDocument();
  expect(screen.getByText("Stale")).toBeInTheDocument();
  expect(screen.getByText(/used 4 times/)).toBeInTheDocument();
  expect(screen.getByRole("link", { name: /Weekly report/ })).toHaveAttribute("href", "/customize/skills/weekly-report");
});

it("says a skill of the owner's own has never been used, and earns no other badge", () => {
  mount(base);
  expect(screen.getByText("Yours")).toBeInTheDocument();
  expect(screen.queryByText("Pinned")).not.toBeInTheDocument();
  expect(screen.queryByText("Stale")).not.toBeInTheDocument();
  expect(screen.getByText(/never used/)).toBeInTheDocument();
});

it("stands in for a description that will not parse rather than hiding the skill", () => {
  mount({ ...base, source: "agent", description: "", broken: true });
  expect(screen.getByText("From the agent")).toBeInTheDocument();
  expect(screen.getByText("Broken frontmatter")).toBeInTheDocument();
  expect(screen.getByText("No description.")).toBeInTheDocument();
});
