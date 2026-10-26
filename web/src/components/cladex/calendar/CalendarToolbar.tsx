/** The row above the calendar: where you are (the period and the arrows), how
 * you look at it (month, week, agenda), what you hide (channels, task runs), and
 * — for the owner with the content plan on — "Approve week's themes", the drafter
 * and the publisher, and "New post".
 *
 * On a phone it wraps into two rows rather than scrolling sideways; the view
 * switch is a pressed-button group so the current view is announced, not only
 * tinted. */

import { ChevronLeft, ChevronRight, Filter, Plus } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu, DropdownMenuCheckboxItem, DropdownMenuContent, DropdownMenuLabel, DropdownMenuSeparator, DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { useLocale, useT } from "@/i18n";
import { VIEWS, rangeTitle, type CalendarFilters, type CalendarView } from "@/lib/calendar";
import { cn } from "@/lib/utils";
import { ChannelIcon } from "./ChannelIcon";
import { channelInfo } from "./channels";
import { ApproveThemesButton } from "./ApproveThemesButton";
import { PublisherControl } from "./PublisherControl";

type Props = {
  view: CalendarView;
  onView: (v: CalendarView) => void;
  anchor: Date;
  onStep: (dir: -1 | 1) => void;
  onToday: () => void;
  filters: CalendarFilters;
  onFilters: (f: CalendarFilters) => void;
  /** The channels the filter lists: the known ones plus any the plan uses. */
  channels: string[];
  /** Owner with the content plan on: the themes, the drafter, the publisher, "New post" and channel filters. */
  content: boolean;
  onNewItem: () => void;
};

export function CalendarToolbar({ view, onView, anchor, onStep, onToday, filters, onFilters, channels, content, onNewItem }: Props) {
  const t = useT();
  const locale = useLocale();
  const hidden = new Set(filters.hiddenChannels);
  const toggleChannel = (c: string, on: boolean) =>
    onFilters({ ...filters, hiddenChannels: on ? filters.hiddenChannels.filter((x) => x !== c) : [...filters.hiddenChannels, c] });
  const filtered = filters.hiddenChannels.length > 0 || !filters.routines;

  return (
    <div className="flex min-w-0 flex-wrap items-center gap-2">
      <div className="flex min-w-0 flex-1 basis-64 items-center gap-1">
        <Button variant="ghost" size="icon" aria-label={t("calendar.previous")} onClick={() => onStep(-1)}><ChevronLeft className="size-4" /></Button>
        <Button variant="secondary" size="sm" onClick={onToday}>{t("calendar.today")}</Button>
        <Button variant="ghost" size="icon" aria-label={t("calendar.next")} onClick={() => onStep(1)}><ChevronRight className="size-4" /></Button>
        <h2 aria-live="polite" className="ml-1 min-w-0 truncate text-lg font-semibold tracking-tight">{rangeTitle(view, anchor, locale)}</h2>
      </div>

      <div className="flex min-w-0 flex-wrap items-center gap-2">
        <div role="group" aria-label={t("calendar.view")} className="flex rounded-md bg-muted p-0.5">
          {VIEWS.map((v) => (
            <button key={v} type="button" aria-pressed={view === v} onClick={() => onView(v)}
              className={cn("rounded px-2.5 py-1 text-sm transition-colors",
                view === v ? "bg-background font-medium text-foreground shadow-xs" : "text-muted-foreground hover:text-foreground")}>
              {t(`calendar.views.${v}`)}
            </button>
          ))}
        </div>

        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant={filtered ? "secondary" : "ghost"} size="icon" aria-label={t("calendar.filters")}>
              <Filter className={cn("size-4", filtered && "text-selected-foreground")} />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="min-w-48">
            {content && channels.length > 0 && (
              <>
                <DropdownMenuLabel>{t("calendar.channels")}</DropdownMenuLabel>
                {channels.map((c) => (
                  <DropdownMenuCheckboxItem key={c} checked={!hidden.has(c)} onSelect={(e) => e.preventDefault()}
                    onCheckedChange={(on) => toggleChannel(c, on === true)}>
                    <ChannelIcon channel={c} /> {channelInfo(c).label}
                  </DropdownMenuCheckboxItem>
                ))}
                <DropdownMenuSeparator />
              </>
            )}
            <DropdownMenuLabel>{t("calendar.routines")}</DropdownMenuLabel>
            <DropdownMenuCheckboxItem checked={filters.routines} onSelect={(e) => e.preventDefault()}
              onCheckedChange={(on) => onFilters({ ...filters, routines: on === true })}>
              {t("calendar.showRoutines")}
            </DropdownMenuCheckboxItem>
          </DropdownMenuContent>
        </DropdownMenu>

        {content && <ApproveThemesButton view={view} anchor={anchor} />}
        {content && <PublisherControl kind="drafter" />}
        {content && <PublisherControl />}
        {content && <Button size="sm" onClick={onNewItem}><Plus className="size-4" /> {t("calendar.newItem")}</Button>}
      </div>
    </div>
  );
}
