import { render, screen } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";

vi.mock("@/api/client", () => ({ api: {
  threads: { events: vi.fn(async () => ({ events: [] })), send: vi.fn(async () => ({ accepted: true, thread_id: "web:1:x" })), cancel: vi.fn() },
  asks: { answer: vi.fn() }, uploads: { put: vi.fn() }, voice: { transcribe: vi.fn(), speak: vi.fn() },
} }));
vi.mock("@/api/sse", () => ({ subscribeEvents: vi.fn(() => vi.fn()) }));

import { useAui, useAuiState } from "@assistant-ui/react";
import { MemoryRouter } from "react-router";
import { act } from "react";
import { api } from "@/api/client";
import { useStore } from "@/store";
import { useContextUsage } from "./context-usage";
import { ev, END_OK } from "./fixtures";
import { CladeXRuntime } from "./provider";

function Probe() {
  const n = useAuiState((s) => s.thread.messages.length);
  const running = useAuiState((s) => s.thread.isRunning);
  return <div>messages:{n} running:{String(running)}</div>;
}

beforeEach(() => useStore.setState(useStore.getInitialState()));

it("exposes folded messages and running state to assistant-ui", () => {
  const s = useStore.getState();
  s.push("web:1:x", ev("t1", "turn.start", { channel: "web", user_id: 1 }));
  s.push("web:1:x", ev("t1", "user.message", { text: "hi", attachments: [] }));
  s.push("web:1:x", ev("t1", "text.block", { text: "yo" }));
  render(<MemoryRouter><CladeXRuntime threadId="web:1:x"><Probe /></CladeXRuntime></MemoryRouter>);
  expect(screen.getByText("messages:2 running:true")).toBeInTheDocument();
  act(() => { s.push("web:1:x", ev("t1", "turn.end", END_OK)); });
  expect(screen.getByText("messages:2 running:false")).toBeInTheDocument();
});

function LoadProbe() {
  // Exactly the four pieces `thread.aui.tsx`'s `isHistoryLoadingView` reads. The
  // skeleton was written, translated and wired into the view, and it had never
  // rendered once: nothing handed the runtime an `isLoading`, so a lane with a
  // year of history greeted the reader with the welcome block instead.
  const skeleton = useAuiState((s) =>
    s.thread.messages.length === 0 && s.thread.isLoading
    && !s.thread.isDisabled && !s.threads.isLoading);
  return <div>skeleton:{String(skeleton)}</div>;
}

it("shows the history skeleton while a thread's events are on the wire", async () => {
  let release!: (page: { events: [] }) => void;
  vi.mocked(api.threads.events).mockReturnValueOnce(new Promise((r) => { release = r; }));
  render(<MemoryRouter><CladeXRuntime threadId="web:1:load"><LoadProbe /></CladeXRuntime></MemoryRouter>);
  expect(screen.getByText("skeleton:true")).toBeInTheDocument();
  await act(async () => { release({ events: [] }); });
  expect(screen.getByText("skeleton:false")).toBeInTheDocument();
});

function Meter() {
  const c = useContextUsage();
  return <div>context:{c ? `${c.used}/${c.window}` : "unknown"}</div>;
}

it("hands the thread's context figure to whatever sits under the runtime", () => {
  const s = useStore.getState();
  s.push("web:1:x", ev("t1", "turn.start", { channel: "web", user_id: 1 }));
  s.push("web:1:x", ev("t1", "user.message", { text: "hi", attachments: [] }));
  render(<MemoryRouter><CladeXRuntime threadId="web:1:x"><Meter /></CladeXRuntime></MemoryRouter>);
  expect(screen.getByText("context:unknown")).toBeInTheDocument();
  act(() => { s.push("web:1:x", ev("t1", "turn.end", { ...END_OK, usage: { m: { context_window: 200_000,
    input_tokens: 12, cache_read_input_tokens: 1300, cache_creation_input_tokens: 50, output_tokens: 40 } } })); });
  expect(screen.getByText("context:1362/200000")).toBeInTheDocument();
});

function Sender() {
  const queue = useAuiState((s) => s.composer.queue.length);
  const can = useAuiState((s) => s.thread.capabilities.queue);
  return <div>
    <div>queue:{queue} cap:{String(can)}</div>
    <QueueSendButton />
  </div>;
}
function QueueSendButton() {
  const aui = useAui();
  return <button onClick={() => { aui.composer().setText("second"); aui.composer().send(); }}>send</button>;
}

it("a message sent while the agent runs is queued and sent when the turn ends", async () => {
  const s = useStore.getState();
  s.push("web:1:q", ev("t1", "turn.start", { channel: "web", user_id: 1 }));
  s.push("web:1:q", ev("t1", "user.message", { text: "first", attachments: [] }));
  render(<MemoryRouter><CladeXRuntime threadId="web:1:q"><Sender /></CladeXRuntime></MemoryRouter>);
  vi.mocked(api.threads.send).mockClear();
  await act(async () => { screen.getByText("send").click(); });
  await act(async () => {});
  expect(screen.getByText(/queue:1 cap:true/)).toBeInTheDocument();
  expect(api.threads.send).not.toHaveBeenCalled();
  await act(async () => { s.push("web:1:q", ev("t1", "turn.end", END_OK)); });
  await act(async () => {});
  expect(api.threads.send).toHaveBeenCalledWith("web:1:q", expect.objectContaining({ text: "second" }));
});
