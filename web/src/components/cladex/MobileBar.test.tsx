import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { beforeEach, expect, it, vi } from "vitest";

vi.mock("@/api/client", () => ({ api: { threads: { list: vi.fn(), create: vi.fn() } } }));
vi.mock("@/api/sse", () => ({ subscribeEvents: vi.fn(() => vi.fn()) }));

import { useStore } from "@/store";
import { MobileBar } from "./MobileBar";

beforeEach(() => { useStore.setState(useStore.getInitialState()); });

it("opens the sidebar drawer, links home through the mark and offers a new chat", () => {
  render(<MemoryRouter><MobileBar /></MemoryRouter>);
  expect(useStore.getState().sidebarOpen).toBe(false);
  screen.getByRole("button", { name: "Menu" }).click();
  expect(useStore.getState().sidebarOpen).toBe(true);
  expect(screen.getByRole("link", { name: "CladeX" })).toHaveAttribute("href", "/");
  expect(screen.getByRole("button", { name: "New chat" })).toBeInTheDocument();
});
