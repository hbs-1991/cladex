import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeAll, expect, it, vi } from "vitest";

const sendMock = vi.fn(async () => ({ accepted: true, thread_id: "web:1:u" }));
vi.mock("@/api/client", async (orig) => {
  const real = await orig<typeof import("@/api/client")>();
  return { ...real, api: new Proxy({}, { get: (_t, k) => {
    if (k === "threads") return { events: vi.fn(async () => ({ events: [] })), send: sendMock, cancel: vi.fn() };
    return new Proxy({}, { get: () => vi.fn(async () => ({})) });
  } }) };
});
vi.mock("@/api/sse", () => ({ subscribeEvents: vi.fn(() => vi.fn()) }));

import { MemoryRouter } from "react-router";
import { TooltipProvider } from "@/components/ui/tooltip";
import { Thread } from "@/components/assistant-ui/elements/thread.aui";
import { useStore } from "@/store";
import { END_OK, ev } from "./fixtures";
import { CladeXRuntime } from "./provider";

beforeAll(() => { Element.prototype.scrollTo ??= () => {}; });

const queued = () => document.querySelectorAll(".aui-composer-queue li").length;

it("Enter while the agent works shows the message in the queue and sends it when the turn ends", async () => {
  const s = useStore.getState();
  s.push("web:1:u", ev("t1", "turn.start", { channel: "web", user_id: 1 }));
  s.push("web:1:u", ev("t1", "user.message", { text: "first", attachments: [] }));
  render(<MemoryRouter><TooltipProvider><CladeXRuntime threadId="web:1:u"><Thread /></CladeXRuntime></TooltipProvider></MemoryRouter>);
  await userEvent.type(await screen.findByRole("textbox"), "second{Enter}");
  await act(async () => {});
  expect(queued()).toBe(1);
  expect(sendMock).not.toHaveBeenCalled();

  await act(async () => { s.push("web:1:u", ev("t1", "turn.end", END_OK)); });
  await act(async () => {});
  expect(sendMock).toHaveBeenCalledWith("web:1:u", expect.objectContaining({ text: "second" }));
  expect(queued()).toBe(0);
});
