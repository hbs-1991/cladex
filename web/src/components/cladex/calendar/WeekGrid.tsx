/** The week view: seven Monday-first columns of 24 hour slots.
 *
 * Events sit in the slot of their hour rather than at a pixel offset for their
 * minute — the publisher does not keep minute precision anyway (inline mode runs
 * one routine at a time), and a slot is also what a drop lands on: the date and
 * the hour move, the minutes stay. Dense routines get the strip above the hours.
 * The body scrolls on its own and opens at 07:00, where a working day starts. */

import { useEffect, useRef, useState, type DragEvent } from "react";
import type { ContentItemWire } from "@/api/types";
import { useLocale } from "@/i18n";
import { DRAG_TYPE, dayKey, weekDays, weekdayNames, type DayBucket } from "@/lib/calendar";
import { cn } from "@/lib/utils";
import { CalendarChip, DenseChip } from "./CalendarChip";

const HOURS = Array.from({ length: 24 }, (_, h) => h);
const FIRST_VISIBLE_HOUR = 7;
const COLS = "grid grid-cols-[2.5rem_repeat(7,minmax(0,1fr))] sm:grid-cols-[3.5rem_repeat(7,minmax(0,1fr))]";

type Props = {
  anchor: Date;
  today: Date;
  buckets: Map<string, DayBucket>;
  dnd: boolean;
  onOpenItem: (item: ContentItemWire) => void;
  onDropItem: (id: number, day: Date, hour: number) => void;
};

export function WeekGrid({ anchor, today, buckets, dnd, onOpenItem, onDropItem }: Props) {
  const locale = useLocale();
  const days = weekDays(anchor);
  const names = weekdayNames(locale);
  const todayKey = dayKey(today);
  const [over, setOver] = useState<string | null>(null);
  const body = useRef<HTMLDivElement>(null);
  const slot = useRef<HTMLDivElement>(null);
  useEffect(() => {
    // `relative` on the body makes it the slot's offset parent, so this is a plain distance.
    if (body.current && slot.current) body.current.scrollTop = slot.current.offsetTop;
  }, []);

  const dropProps = (day: Date, hour: number) => {
    const key = `${dayKey(day)}@${hour}`;
    return dnd ? {
      onDragOver: (e: DragEvent) => {
        if (!e.dataTransfer.types.includes(DRAG_TYPE)) return;
        e.preventDefault();
        e.dataTransfer.dropEffect = "move";
        setOver(key);
      },
      onDragLeave: () => setOver((k) => (k === key ? null : k)),
      onDrop: (e: DragEvent) => {
        const id = Number(e.dataTransfer.getData(DRAG_TYPE));
        setOver(null);
        if (!id) return;
        e.preventDefault();
        onDropItem(id, day, hour);
      },
    } : {};
  };

  const anyDense = days.some((d) => (buckets.get(dayKey(d))?.dense.length ?? 0) > 0);
  return (
    <div data-view="week" className="overflow-hidden rounded-lg border border-border">
      <div className={cn(COLS, "border-b border-border bg-muted/50")}>
        <div />
        {days.map((d, i) => (
          <div key={dayKey(d)} className="min-w-0 px-1 py-1.5 text-center text-xs text-muted-foreground">
            <div className="truncate">{names[i]}</div>
            <div className={cn("mx-auto mt-0.5 flex size-6 items-center justify-center rounded-full text-sm tabular-nums text-foreground",
              dayKey(d) === todayKey && "bg-primary font-semibold text-primary-foreground")}>{d.getDate()}</div>
          </div>
        ))}
      </div>
      {anyDense && (
        <div className={cn(COLS, "border-b border-border")}>
          <div />
          {days.map((d) => (
            <div key={dayKey(d)} className="flex min-w-0 flex-col gap-1 overflow-hidden border-l border-border p-0.5 sm:p-1">
              {(buckets.get(dayKey(d))?.dense ?? []).map((m) => <DenseChip key={m.routineId} marker={m} compact />)}
            </div>
          ))}
        </div>
      )}
      <div ref={body} className="relative max-h-[calc(100dvh-18rem)] min-h-80 overflow-y-auto">
        {HOURS.map((hour) => (
          <div key={hour} ref={hour === FIRST_VISIBLE_HOUR ? slot : undefined} className={cn(COLS, "border-b border-border last:border-b-0")}>
            <div className="px-1 pt-1 text-right text-[11px] tabular-nums text-muted-foreground sm:px-2">{String(hour).padStart(2, "0")}:00</div>
            {days.map((d) => {
              const key = `${dayKey(d)}@${hour}`;
              const events = (buckets.get(dayKey(d))?.events ?? []).filter((e) => e.at.getHours() === hour);
              return (
                <div key={key} data-slot={key} {...dropProps(d, hour)}
                  className={cn("flex min-h-12 min-w-0 flex-col gap-1 overflow-hidden border-l border-border p-0.5 sm:p-1", over === key && "bg-selected")}>
                  {events.map((e) => <CalendarChip key={e.key} event={e} compact draggable={dnd} onOpenItem={onOpenItem} />)}
                </div>
              );
            })}
          </div>
        ))}
      </div>
    </div>
  );
}
