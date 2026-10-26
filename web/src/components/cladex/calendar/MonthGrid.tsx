/** The month view: whole Monday-to-Sunday weeks, each day a drop target.
 *
 * A cell shows at most `MAX_CHIPS` events and then "+N more", which opens that
 * week — a day with a dozen fires should read as busy, not push the grid off
 * the screen. Dense routines are one line each, under the events. On a phone
 * the chips shrink to a mark and a truncated label; the grid itself never gets
 * wider than the page. */

import { useState, type DragEvent } from "react";
import type { ContentItemWire } from "@/api/types";
import { useLocale, useT } from "@/i18n";
import { DRAG_TYPE, dayHeading, dayKey, monthGrid, weekdayNames, type DayBucket } from "@/lib/calendar";
import { cn } from "@/lib/utils";
import { CalendarChip, DenseChip } from "./CalendarChip";

const MAX_CHIPS = 3;

type Props = {
  anchor: Date;
  today: Date;
  buckets: Map<string, DayBucket>;
  dnd: boolean;
  onOpenItem: (item: ContentItemWire) => void;
  onDropItem: (id: number, day: Date) => void;
  onShowDay: (day: Date) => void;
};

export function MonthGrid({ anchor, today, buckets, dnd, onOpenItem, onDropItem, onShowDay }: Props) {
  const t = useT();
  const locale = useLocale();
  const [over, setOver] = useState<string | null>(null);
  const weeks = monthGrid(anchor);
  const month = anchor.getMonth();
  const todayKey = dayKey(today);

  const dropProps = (day: Date) => dnd ? {
    onDragOver: (e: DragEvent) => {
      if (!e.dataTransfer.types.includes(DRAG_TYPE)) return;
      e.preventDefault();
      e.dataTransfer.dropEffect = "move";
      setOver(dayKey(day));
    },
    onDragLeave: () => setOver((k) => (k === dayKey(day) ? null : k)),
    onDrop: (e: DragEvent) => {
      const id = Number(e.dataTransfer.getData(DRAG_TYPE));
      setOver(null);
      if (!id) return;
      e.preventDefault();
      onDropItem(id, day);
    },
  } : {};

  return (
    <div data-view="month" className="overflow-hidden rounded-lg border border-border">
      <div className="grid grid-cols-7 border-b border-border bg-muted/50">
        {weekdayNames(locale).map((name) => (
          <div key={name} className="px-1 py-1.5 text-center text-xs font-medium text-muted-foreground sm:px-2 sm:text-left">{name}</div>
        ))}
      </div>
      {weeks.map((week) => (
        <div key={dayKey(week[0]!)} className="grid grid-cols-7 border-b border-border last:border-b-0">
          {week.map((day) => {
            const key = dayKey(day);
            const bucket = buckets.get(key) ?? { events: [], dense: [] };
            const shown = bucket.events.slice(0, MAX_CHIPS);
            const hidden = bucket.events.length - shown.length;
            const isToday = key === todayKey;
            return (
              <div key={key} data-day={key} {...dropProps(day)}
                className={cn("flex min-h-20 min-w-0 flex-col gap-1 overflow-hidden border-r border-border p-1 last:border-r-0 sm:min-h-28 sm:p-1.5",
                  day.getMonth() !== month && "bg-muted/40",
                  over === key && "bg-selected")}>
                <span className={cn("flex size-6 items-center justify-center self-start rounded-full text-xs tabular-nums",
                  day.getMonth() !== month ? "text-muted-foreground" : "text-foreground",
                  isToday && "bg-primary font-semibold text-primary-foreground")}>
                  {day.getDate()}
                </span>
                {shown.map((e) => <CalendarChip key={e.key} event={e} compact draggable={dnd} onOpenItem={onOpenItem} />)}
                {bucket.dense.map((m) => <DenseChip key={`dense:${m.routineId}`} marker={m} compact />)}
                {hidden > 0 && (
                  <button type="button" onClick={() => onShowDay(day)}
                    aria-label={t("calendar.showDay", { date: dayHeading(day, locale) })}
                    className="self-start rounded px-1 text-xs text-muted-foreground hover:bg-accent hover:text-accent-foreground">
                    {t("calendar.more", { n: hidden })}
                  </button>
                )}
              </div>
            );
          })}
        </div>
      ))}
    </div>
  );
}
