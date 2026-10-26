/** Posts without a date — the backlog the server answers next to the range.
 *
 * On a desktop each one can be dragged onto a month cell or a week slot, which
 * gives it that day at 09:00 (or the slot's hour); anywhere else it is opened
 * and given a time in the dialog. */

import type { ContentItemWire } from "@/api/types";
import { useT } from "@/i18n";
import { CalendarChip } from "./CalendarChip";

type Props = {
  items: ContentItemWire[];
  dnd: boolean;
  onOpenItem: (item: ContentItemWire) => void;
};

export function BacklogPanel({ items, dnd, onOpenItem }: Props) {
  const t = useT();
  return (
    <section aria-labelledby="calendar-backlog" className="min-w-0">
      <h2 id="calendar-backlog" className="flex items-center gap-2 text-sm font-semibold">
        {t("calendar.backlog")}
        <span className="rounded bg-muted px-1.5 py-0.5 text-xs font-normal tabular-nums text-muted-foreground">{items.length}</span>
      </h2>
      <p className="mt-1 text-xs text-muted-foreground">
        {items.length === 0 ? t("calendar.backlogEmpty") : dnd ? t("calendar.backlogLead") : t("calendar.backlogLeadTouch")}
      </p>
      {items.length > 0 && (
        <ul className="mt-3 grid min-w-0 gap-1.5 sm:grid-cols-2 lg:grid-cols-3">
          {items.map((item) => (
            <li key={item.id} className="min-w-0">
              <CalendarChip event={{ kind: "item", key: `backlog:${item.id}`, at: new Date(), item }}
                compact undated draggable={dnd} onOpenItem={onOpenItem} />
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
