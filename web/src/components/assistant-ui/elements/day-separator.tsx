"use client";

import type { ComponentProps, FC } from "react";
import { useAuiState } from "@assistant-ui/react"; // cladex: the live-thread variant reads the message scope
import { currentLocale, useLocale, type Locale } from "@/i18n"; // cladex: labels in the interface language
import { dayLabel, sameDay } from "@/lib/dates"; // cladex: Today / Yesterday / a locale date
import { cn } from "@/lib/utils";
import { mono } from "./surfaces";

export interface DatedMessage {
  id: string;
  day: string;
  time: string;
  role: "user" | "assistant";
  text: string;
}

// cladex: the rule–label–rule header on its own, so the live thread can draw
// it above a message without laying the transcript out as a flat list.
export function DaySeparatorLabel({
  className,
  children,
  ...props
}: ComponentProps<"div">) {
  return (
    <div
      data-slot="day-separator-label"
      className={cn("flex items-center gap-2.5 py-1", className)}
      {...props}
    >
      <span className="bg-foreground/[0.08] h-px flex-1" />
      <span className={cn(mono, "text-foreground/30")}>{children}</span>
      <span className="bg-foreground/[0.08] h-px flex-1" />
    </div>
  );
}

export function DaySeparator({
  messages,
  className,
  ...props
}: Omit<ComponentProps<"div">, "children" | "messages"> & {
  messages: readonly DatedMessage[];
}) {
  let lastDay = "";

  return (
    <div
      data-slot="day-separator"
      className={cn("flex w-full max-w-sm flex-col gap-2", className)}
      {...props}
    >
      {messages.map((message) => {
        const newDay = message.day !== lastDay;
        // eslint-disable-next-line react-hooks/immutability -- cladex: upstream carries the previous day in a render-local let
        lastDay = message.day;

        return (
          <div key={message.id} className="flex flex-col gap-2">
            {newDay && (
              // cladex: upstream inlines this header; see DaySeparatorLabel.
              <DaySeparatorLabel>{message.day}</DaySeparatorLabel>
            )}
            <div
              className={cn(
                "group flex items-baseline gap-2",
                message.role === "user" && "flex-row-reverse",
              )}
            >
              <span
                className={cn(
                  "max-w-[80%] text-[13.5px] leading-relaxed break-words",
                  message.role === "user"
                    ? "bg-foreground/[0.05] rounded-2xl px-3.5 py-2"
                    : "text-foreground/75",
                )}
              >
                {message.text}
              </span>
              <span
                className={cn(
                  mono,
                  "text-foreground/0 group-hover:text-foreground/30 shrink-0 tabular-nums transition-colors",
                )}
              >
                {message.time}
              </span>
            </div>
          </div>
        );
      })}
    </div>
  );
}

// cladex: what a message's header says — null when it continues the previous
// message's day, otherwise "Today", "Yesterday" or the date, in the interface
// language. The comparison looks only at the message before, like upstream's
// `day !== lastDay`, so the list must be chronological.
export function daySeparatorLabel(
  createdAt: Date,
  previous: Date | undefined,
  now = new Date(),
  locale: Locale = currentLocale(),
): string | null {
  if (previous && sameDay(previous, createdAt)) return null;
  return dayLabel(createdAt, now, locale);
}

// cladex: the live thread's counterpart to DaySeparator. Upstream lays a flat
// `DatedMessage[]` out as a compact transcript and tells you to keep the real
// conversation on ThreadPrimitive.Messages, so this renders per message,
// inside the message scope, and draws the header when the calendar day of
// `createdAt` differs from the message before it.
export const MessageDaySeparator: FC<{ className?: string | undefined }> = ({
  className,
}) => {
  const locale = useLocale();
  const createdAt = useAuiState((s) => s.message.createdAt);
  const previous = useAuiState((s) =>
    s.message.index > 0
      ? s.thread.messages[s.message.index - 1]?.createdAt
      : undefined,
  );
  const label = daySeparatorLabel(createdAt, previous, new Date(), locale);
  if (label === null) return null;
  return (
    <DaySeparatorLabel data-slot="aui_message-day-separator" className={className}>
      {label}
    </DaySeparatorLabel>
  );
};
