import { render, screen } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
vi.mock("@/api/sse", () => ({ subscribeEvents: vi.fn(() => vi.fn()) }));
vi.mock("@/api/client", () => ({ api: {} }));
import { useStore } from "@/store";
import { ConnectionBar } from "./ConnectionBar";

beforeEach(() => useStore.setState(useStore.getInitialState()));

it("shows only while reconnecting", () => {
  const { rerender } = render(<ConnectionBar threadId="web:1:x" />);
  expect(screen.queryByRole("status")).toBeNull();
  useStore.setState({ stream: { "web:1:x": "reconnecting" } });
  rerender(<ConnectionBar threadId="web:1:x" />);
  expect(screen.getByRole("status")).toHaveTextContent("Connection lost, reconnecting…");
  useStore.setState({ stream: { "web:1:x": "error" } });
  rerender(<ConnectionBar threadId="web:1:x" />);
  expect(screen.getByRole("status")).toHaveTextContent("Could not load the thread. Reload the page.");
});
