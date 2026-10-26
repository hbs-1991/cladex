/** One thing on the calendar.
 *
 * A content item is a button (it opens the item dialog) edged and marked in its
 * channel's colour; a routine fire or run is a link to that routine and never
 * draggable, drawn quieter so the plan stays the figure and the machinery the
 * ground. `compact` is the month/week size — one line, truncated, and on a
 * phone just the mark: a seventh of 390px holds an icon, not a word, so the
 * words move into the accessible name. The agenda gets the full chip with its
 * status and a line of the text.
 *
 * Colour is never the only signal (DESIGN.md §2): the status has a label, and
 * the attention badge is an icon with a name, not a red tint. */

import type { DragEvent } from "react";
import { Link } from "react-router";
import { AlertTriangle, Clock, Lock, Repeat } from "lucide-react";
import type { ContentItemWire, ContentStatus } from "@/api/types";
import { useLocale, useT } from "@/i18n";
import { describeSchedule } from "@/lib/schedule";
import { fmtTime } from "@/lib/dates";
import { DRAG_TYPE, isApproximate, isLocked, itemLabel, type CalEvent, type DenseMarker } from "@/lib/calendar";
import { cn } from "@/lib/utils";
import { channelInfo } from "./channels";
import { ChannelIcon } from "./ChannelIcon";

/** `planned` is an approved theme, not approved content: the selection colour as
 *  an outline, never the filled pill `scheduled` wears. `drafting` is work in
 *  progress, so it carries the running dot (DESIGN.md: a signal dot plus a label). */
const STATUS_TONE: Record<ContentStatus, string> = {
  idea: "bg-muted text-muted-foreground",
  planned: "border border-dashed border-selected-foreground text-selected-foreground",
  drafting: "bg-muted text-foreground",
  draft: "bg-muted text-muted-foreground",
  review: "bg-warning text-warning-foreground",
  scheduled: "bg-selected text-selected-foreground",
  queued: "bg-selected text-selected-foreground",
  publishing: "bg-selected text-selected-foreground",
  published: "bg-success text-success-foreground",
  failed: "bg-error text-error-foreground",
  stuck: "bg-error text-error-foreground",
  skipped: "bg-muted text-muted-foreground line-through",
};

export function StatusPill({ status, className }: { status: ContentStatus; className?: string }) {
  const t = useT();
  return (
    <span className={cn("inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-[11px] font-medium", STATUS_TONE[status], className)}>
      {status === "drafting" && <i aria-hidden className="inline-block size-1.5 animate-pulse rounded-full bg-signal" />}
      {t(`calendar.status.${status}`)}
    </span>
  );
}

export function AttentionBadge({ className }: { className?: string }) {
  const t = useT();
  return (
    <span role="img" aria-label={t("calendar.needsAttention")} title={t("calendar.needsAttention")}
      className={cn("inline-flex shrink-0 items-center justify-center rounded-full bg-warning p-0.5 text-warning-foreground", className)}>
      <AlertTriangle className="size-3" strokeWidth={2} />
    </span>
  );
}

type Props = {
  event: CalEvent;
  compact?: boolean;
  /** Whether a content item may be picked up (desktop, content the owner may move). */
  draggable?: boolean;
  onOpenItem?: (item: ContentItemWire) => void;
  /** A backlog item has no time to show. */
  undated?: boolean;
};

export function CalendarChip({ event, compact = false, draggable = false, onOpenItem, undated = false }: Props) {
  const t = useT();
  const locale = useLocale();
  const time = undated ? "" : fmtTime(event.at.toISOString(), locale);

  if (event.kind !== "item") {
    const fire = event.kind === "fire";
    const statusLabel = !fire && ["ok", "error", "timeout", "missed"].includes(event.status)
      ? t(`routines.runStatus.${event.status as "ok"}`) : !fire ? event.status : "";
    const name = fire ? `${event.name} · ${t("calendar.fire", { time })}` : `${time} ${t("calendar.ran", { name: event.name, status: statusLabel })}`;
    return (
      <Link to={`/calendar/tasks/${event.routineId}`} title={name} aria-label={compact ? name : undefined}
        className={cn("flex w-full min-w-0 items-center gap-1.5 rounded-md text-muted-foreground hover:bg-accent hover:text-accent-foreground",
          fire ? "border border-dashed border-border" : "border border-transparent",
          compact ? "justify-center px-1.5 py-0.5 text-xs sm:justify-start" : "px-3 py-2 text-sm")}>
        {fire
          ? <Clock className="size-3.5 shrink-0" strokeWidth={1.75} aria-hidden />
          : <i aria-hidden className={cn("inline-block size-2 shrink-0 rounded-sm",
              event.status === "ok" ? "bg-success-foreground" : event.status === "missed" ? "bg-border" : "bg-error-foreground")} />}
        <span className={cn("shrink-0 tabular-nums", compact && WIDE)}>{time}</span>
        <span className={cn("min-w-0 truncate", compact && WIDE)}>{event.name}</span>
        {!fire && !compact && <span className="ml-auto shrink-0 text-xs">{statusLabel}</span>}
      </Link>
    );
  }

  const { item } = event;
  const info = channelInfo(item.channel);
  const label = itemLabel(item, t("calendar.item.untitled"));
  const when = isApproximate(item) ? t("calendar.approx", { time }) : time;
  const canDrag = draggable && !isLocked(item);
  const onDragStart = (e: DragEvent) => {
    e.dataTransfer.setData(DRAG_TYPE, String(item.id));
    e.dataTransfer.effectAllowed = "move";
  };
  return (
    <button type="button" onClick={() => onOpenItem?.(item)} draggable={canDrag} onDragStart={canDrag ? onDragStart : undefined}
      data-status={item.status}
      title={isLocked(item) && draggable
        ? `${label} · ${t(item.status === "drafting" ? "calendar.notDraggableDrafting" : "calendar.notDraggable")}`
        : `${info.label} · ${label}`}
      aria-label={compact ? [info.label, undated ? "" : when, label, t(`calendar.status.${item.status}`),
        item.needs_attention ? t("calendar.needsAttention") : ""].filter(Boolean).join(" · ") : undefined}
      style={{ borderLeftColor: info.color }}
      className={cn("flex w-full min-w-0 rounded-md border border-l-[3px] bg-card text-left text-card-foreground hover:bg-accent hover:text-accent-foreground",
        canDrag && "cursor-grab active:cursor-grabbing",
        item.status === "skipped" && "opacity-60",
        item.status === "planned" && "border-dashed",
        compact ? "items-center justify-center gap-1 px-1.5 py-0.5 text-xs sm:justify-start" : "flex-col gap-1 px-3 py-2 text-sm")}>
      {compact ? (
        <>
          <ChannelIcon channel={item.channel} size={12} />
          {!undated && <span className="hidden shrink-0 tabular-nums text-muted-foreground sm:inline">{when}</span>}
          <span className={cn("min-w-0 truncate", WIDE)}>{label}</span>
          {item.status === "drafting" && <i aria-hidden className="inline-block size-1.5 shrink-0 animate-pulse rounded-full bg-signal" />}
          {item.needs_attention && <AttentionBadge className="sm:ml-auto" />}
        </>
      ) : (
        <>
          <span className="flex w-full min-w-0 items-center gap-2">
            <ChannelIcon channel={item.channel} size={14} />
            <span className="min-w-0 flex-1 truncate font-medium">{label}</span>
            {item.needs_attention && <AttentionBadge />}
            {isLocked(item) && <Lock className="size-3.5 shrink-0 text-muted-foreground" aria-hidden />}
          </span>
          {item.title.trim() && item.body.trim() && (
            <span className="line-clamp-2 w-full break-words text-xs text-muted-foreground">{item.body}</span>
          )}
          <span className="flex w-full min-w-0 flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground">
            {!undated && <span className="tabular-nums">{when}</span>}
            <span>{info.label}</span>
            <StatusPill status={item.status} />
          </span>
        </>
      )}
    </button>
  );
}

/** Text a compact chip shows only from `sm` up. */
const WIDE = "hidden sm:inline";

/** A dense routine's one marker for the day: "Inbox check · Every 5 minutes". */
export function DenseChip({ marker, compact = false }: { marker: DenseMarker; compact?: boolean }) {
  const t = useT();
  const locale = useLocale();
  const every = describeSchedule(`every ${marker.everySeconds}s`, locale);
  const text = t("calendar.every", { name: marker.name, every });
  return (
    <Link to={`/calendar/tasks/${marker.routineId}`} title={text} aria-label={compact ? text : undefined}
      className={cn("flex w-full min-w-0 items-center gap-1.5 rounded-md border border-dashed border-border text-muted-foreground hover:bg-accent hover:text-accent-foreground",
        compact ? "justify-center px-1.5 py-0.5 text-xs sm:justify-start" : "px-3 py-2 text-sm")}>
      <Repeat className="size-3.5 shrink-0" strokeWidth={1.75} aria-hidden />
      <span className={cn("min-w-0 truncate", compact && WIDE)}>{text}</span>
      {marker.runs > 0 && !compact && <span className="ml-auto shrink-0 text-xs">{t("calendar.runsCount", { n: marker.runs })}</span>}
    </Link>
  );
}
