import { render, screen } from "@testing-library/react";
import { AssistantRuntimeProvider, ThreadPrimitive, useExternalStoreRuntime, type ThreadMessageLike } from "@assistant-ui/react";
import { describe, expect, it } from "vitest";
import { DaySeparator, MessageDaySeparator, daySeparatorLabel } from "./day-separator";

const NOW = new Date("2026-07-12T15:00:00");   // local time: the labels compare calendar days in the browser's zone
const at = (s: string) => new Date(s);

describe("daySeparatorLabel", () => {
  it("says nothing while the day continues", () => {
    expect(daySeparatorLabel(at("2026-07-12T10:00:00"), at("2026-07-12T09:00:00"), NOW, "en")).toBeNull();
    expect(daySeparatorLabel(at("2026-07-12T23:59:59"), at("2026-07-12T00:00:00"), NOW, "en")).toBeNull();
  });

  it("opens a new day with Today / Yesterday / the date, in the interface language", () => {
    expect(daySeparatorLabel(at("2026-07-12T10:00:00"), undefined, NOW, "en")).toBe("Today");
    expect(daySeparatorLabel(at("2026-07-12T10:00:00"), at("2026-07-11T23:00:00"), NOW, "en")).toBe("Today");
    expect(daySeparatorLabel(at("2026-07-11T12:00:00"), at("2026-07-10T12:00:00"), NOW, "en")).toBe("Yesterday");
    expect(daySeparatorLabel(at("2026-07-01T09:00:00"), at("2026-06-30T09:00:00"), NOW, "en")).toBe("1 July");
    expect(daySeparatorLabel(at("2025-12-31T09:00:00"), undefined, NOW, "en")).toBe("31 December 2025");
    expect(daySeparatorLabel(at("2026-07-12T10:00:00"), undefined, NOW, "ru")).toBe("Сегодня");
    expect(daySeparatorLabel(at("2026-07-11T12:00:00"), undefined, NOW, "ru")).toBe("Вчера");
    expect(daySeparatorLabel(at("2026-07-01T09:00:00"), undefined, NOW, "ru")).toBe("1 июля");
  });
});

describe("DaySeparator (flat list)", () => {
  it("draws one header per day change and none inside a day", () => {
    render(
      <DaySeparator messages={[
        { id: "1", day: "Yesterday", time: "16:04", role: "user", text: "first" },
        { id: "2", day: "Yesterday", time: "16:05", role: "assistant", text: "second" },
        { id: "3", day: "Today", time: "09:12", role: "user", text: "third" },
      ]} />,
    );
    const headers = document.querySelectorAll('[data-slot="day-separator-label"]');
    expect([...headers].map((h) => h.textContent)).toEqual(["Yesterday", "Today"]);
  });
});

/** A thread of fixed messages, rendered the way thread.aui.tsx renders it. */
function Transcript({ messages }: { messages: ThreadMessageLike[] }) {
  const runtime = useExternalStoreRuntime<ThreadMessageLike>({
    messages, convertMessage: (m) => m, onNew: async () => {},
  });
  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <ThreadPrimitive.Root>
        <ThreadPrimitive.Viewport>
          <ThreadPrimitive.Messages>
            {() => <><MessageDaySeparator /><Row /></>}
          </ThreadPrimitive.Messages>
        </ThreadPrimitive.Viewport>
      </ThreadPrimitive.Root>
    </AssistantRuntimeProvider>
  );
}
function Row() { return <p data-testid="row" />; }

const msg = (id: string, role: "user" | "assistant", createdAt: string): ThreadMessageLike =>
  ({ id, role, createdAt: at(createdAt), content: [{ type: "text", text: id }] });

describe("MessageDaySeparator (live thread)", () => {
  it("puts a header above the first message and above each message that starts a new day", () => {
    const yesterday = new Date(); yesterday.setDate(yesterday.getDate() - 1);
    const today = new Date();
    const iso = (d: Date, h: number) => { const x = new Date(d); x.setHours(h, 0, 0, 0); return x.toISOString(); };
    render(
      <Transcript messages={[
        msg("t1:u", "user", iso(yesterday, 10)),
        msg("t1:a", "assistant", iso(yesterday, 10)),
        msg("t2:u", "user", iso(today, 9)),
        msg("t2:a", "assistant", iso(today, 9)),
      ]} />,
    );
    expect(screen.getAllByTestId("row")).toHaveLength(4);
    const headers = document.querySelectorAll('[data-slot="aui_message-day-separator"]');
    expect([...headers].map((h) => h.textContent)).toEqual(["Yesterday", "Today"]);
  });

  it("draws nothing between messages of one day", () => {
    const today = new Date();
    const iso = (h: number) => { const x = new Date(today); x.setHours(h, 0, 0, 0); return x.toISOString(); };
    render(<Transcript messages={[msg("t1:u", "user", iso(9)), msg("t1:a", "assistant", iso(9)), msg("t2:u", "user", iso(11))]} />);
    expect(document.querySelectorAll('[data-slot="aui_message-day-separator"]')).toHaveLength(1);
    expect(screen.getByText("Today")).toBeInTheDocument();
  });
});
