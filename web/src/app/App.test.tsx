import { render, screen } from "@testing-library/react";
import { expect, test, vi } from "vitest";

vi.mock("@/api/client", () => ({ api: {
  me: vi.fn(async () => ({ user_id: 1, role: "owner", default_model: null, default_effort: null })),
  threads: { list: vi.fn(async () => ({ threads: [], next: null })), events: vi.fn(async () => ({ events: [] })) },
} }));
vi.mock("@/api/sse", () => ({ subscribeEvents: vi.fn(() => vi.fn()) }));

import { App } from "./App";

test("renders the product name", async () => {
  render(<App />);
  // the logo is inline SVG now: its accessible name comes from <title>, not alt
  expect((await screen.findAllByRole("img", { name: "CladeX" })).length).toBeGreaterThan(0);
});
