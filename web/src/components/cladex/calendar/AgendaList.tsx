/** The agenda: the days that have something in them, one under another.
 *
 * This is the phone's default view, so it is built to never be wider than the
 * screen: one column, every row `min-w-0`, long titles truncated and the text
 * clamped and allowed to break anywhere (a pasted URL has no spaces to wrap at).
 * Nothing here is draggable — the agenda has no drop targets, and on a phone
 * there is no drag to begin with; a post is moved by opening it. */

import type { ContentItemWire } from "@/api/types";
import { useLocale, useT } from "@/i18n";
import { agendaGroups, dayHeading, dayKey, type DayBucket } from "@/lib/calendar";
import { cn } from "@/lib/utils";
import { CalendarChip, DenseChip } from "./CalendarChip";

type Props = {
  days: Date[];
  today: Date;
  buckets: Map<string, DayBucket>;
  onOpenItem: (item: ContentItemWire) => void;
};

export function AgendaList({ days, today, buckets, onOpenItem }: Props) {
  const t = useT();
  const locale = useLocale();
  const groups = agendaGroups(days, buckets);
  const todayKey = dayKey(today);
  if (groups.length === 0) {
    return <p data-view="agenda" className="rounded-lg border border-dashed border-border px-4 py-10 text-center text-sm text-muted-foreground">{t("calendar.empty")}</p>;
  }
  return (
    <ol data-view="agenda" className="min-w-0 space-y-6">
      {groups.map(({ day, bucket }) => {
        const isToday = dayKey(day) === todayKey;
        return (
          <li key={dayKey(day)} className="min-w-0">
            <h3 className={cn("mb-2 flex items-center gap-2 text-sm font-semibold", isToday && "text-selected-foreground")}>
              {isToday && <span className="rounded bg-selected px-1.5 py-0.5 text-xs">{t("calendar.today")}</span>}
              <span className="min-w-0 truncate">{dayHeading(day, locale)}</span>
            </h3>
            <ul className="min-w-0 space-y-1.5">
              {bucket.events.map((e) => (
                <li key={e.key} className="min-w-0"><CalendarChip event={e} onOpenItem={onOpenItem} /></li>
              ))}
              {bucket.dense.map((m) => (
                <li key={`dense:${m.routineId}`} className="min-w-0"><DenseChip marker={m} /></li>
              ))}
            </ul>
          </li>
        );
      })}
    </ol>
  );
}
