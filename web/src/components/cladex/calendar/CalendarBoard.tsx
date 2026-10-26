/** The Calendar tab: the content plan and the routines on one timeline.
 *
 * The view (month, week, agenda) is remembered per browser; the first visit
 * picks agenda on a phone and month everywhere else. The anchor is the day the
 * view is built around, and the range the store reads follows from the two —
 * a new range is a new read of both endpoints, and so is the tab coming back
 * into view, because the publisher and the agent change the plan while nobody
 * is looking.
 *
 * Drag and drop is the desktop's (HTML5 DnD does not start from a touch): an item
 * dropped on a month cell keeps its time of day, on a week slot it takes the
 * slot's hour. Routine fires are the scheduler's and are never draggable, and
 * items the publisher holds (`queued`, `publishing`, `published`) or the drafter
 * is writing (`drafting`) stay put.
 *
 * `/calendar?item=<id>` opens that item's dialog — the "Open in calendar" button
 * on a Telegram approval card lands here. The item is taken from the range on
 * screen when it is there and read by id when it is not; closing the dialog
 * drops the parameter, so a reload does not open it again. */

import { useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router";
import { toast } from "sonner";
import { api } from "@/api/client";
import type { ContentItemWire } from "@/api/types";
import { Button } from "@/components/ui/button";
import { useIsMobile } from "@/hooks/use-mobile";
import { useLocale, useT } from "@/i18n";
import {
  NO_FILTERS, VIEWS, bucketDays, deviceZone, isLocked, moveTo, shiftAnchor, startOfDay, viewDays, viewRange,
  type CalendarFilters, type CalendarView,
} from "@/lib/calendar";
import { fmtDateTime } from "@/lib/dates";
import { useStore } from "@/store";
import { AgendaList } from "./AgendaList";
import { BacklogPanel } from "./BacklogPanel";
import { CalendarToolbar } from "./CalendarToolbar";
import { CHANNELS } from "./channels";
import { ContentItemDialog } from "./ContentItemDialog";
import { MonthGrid } from "./MonthGrid";
import { WeekGrid } from "./WeekGrid";

const VIEW_KEY = "cladex.calendar.view";
const PHONE = "(max-width: 767px)";

/** The remembered view, else agenda on a phone and month above it. Storage can
 *  be missing or throw (a private window, a Mini App webview); then it is simply
 *  the default every time. */
function initialView(): CalendarView {
  try {
    const v = localStorage.getItem(VIEW_KEY);
    if (v && (VIEWS as readonly string[]).includes(v)) return v as CalendarView;
  } catch { /* no storage: fall through to the default */ }
  return window.matchMedia?.(PHONE).matches ? "agenda" : "month";
}

type DialogState = { open: boolean; item: ContentItemWire | null; publishAt: string | null };

export function CalendarBoard() {
  const t = useT();
  const locale = useLocale();
  const isMobile = useIsMobile();
  const me = useStore((s) => s.me);
  const items = useStore((s) => s.calItems);
  const backlog = useStore((s) => s.calBacklog);
  const routines = useStore((s) => s.calRoutines);
  const status = useStore((s) => s.calStatus);
  const enabled = useStore((s) => s.contentEnabled);
  const loadCalendar = useStore((s) => s.loadCalendar);
  const reloadCalendar = useStore((s) => s.reloadCalendar);
  const updateItem = useStore((s) => s.updateItem);

  const [view, setViewState] = useState<CalendarView>(initialView);
  const [anchor, setAnchor] = useState(() => startOfDay(new Date()));
  const [filters, setFilters] = useState<CalendarFilters>(NO_FILTERS);
  const [dialog, setDialog] = useState<DialogState>({ open: false, item: null, publishAt: null });
  const [now] = useState(() => new Date());
  const [params, setParams] = useSearchParams();
  const deepId = Number(params.get("item")) || null;

  const owner = me?.role === "owner";
  const content = owner && enabled === true;
  const dnd = content && !isMobile;

  const setView = (v: CalendarView) => {
    setViewState(v);
    try { localStorage.setItem(VIEW_KEY, v); } catch { /* remembered for this visit only */ }
  };

  const { start, end } = viewRange(view, anchor);
  const from = start.toISOString(), to = end.toISOString();
  useEffect(() => { void loadCalendar({ from, to }); }, [from, to, loadCalendar]);
  useEffect(() => {
    const onVisible = () => { if (document.visibilityState === "visible") void reloadCalendar(); };
    document.addEventListener("visibilitychange", onVisible);
    return () => document.removeEventListener("visibilitychange", onVisible);
  }, [reloadCalendar]);

  const days = useMemo(() => viewDays(view, anchor), [view, anchor]);
  const buckets = useMemo(() => bucketDays(days, items, routines, filters, now), [days, items, routines, filters, now]);
  const channels = useMemo(() => {
    const used = [...items, ...backlog].map((i) => i.channel);
    return [...new Set<string>([...CHANNELS, ...used])];
  }, [items, backlog]);
  const shownBacklog = useMemo(() => backlog.filter((i) => !filters.hiddenChannels.includes(i.channel)), [backlog, filters]);

  const openItem = (item: ContentItemWire) => setDialog({ open: true, item, publishAt: null });

  const dropDeepLink = () => setParams((p) => { p.delete("item"); return p; }, { replace: true });
  // Once the first read of the range has landed, so an item on screen is not read twice.
  const settled = status === "ready" || status === "error";
  const linked = useRef<number | null>(null);   // the link already answered: a re-read of the range must not reopen it
  useEffect(() => {
    if (deepId === null) { linked.current = null; return; }
    if (!content || !settled || linked.current === deepId) return;
    let live = true;
    const { calItems, calBacklog } = useStore.getState();
    const known = [...calItems, ...calBacklog].find((i) => i.id === deepId);
    (known ? Promise.resolve(known) : api.content.get(deepId)).then(
      (item) => { if (live) { linked.current = deepId; setDialog({ open: true, item, publishAt: null }); } },
      (e: unknown) => {
        if (!live) return;
        toast.error(e instanceof Error && e.message ? e.message : t("common.error"));
        dropDeepLink();
      },
    );
    return () => { live = false; };
  }, [deepId, content, settled]); // eslint-disable-line react-hooks/exhaustive-deps

  const newItem = () => setDialog({ open: true, item: null, publishAt: null });

  const dropItem = async (id: number, day: Date, hour?: number) => {
    const item = [...items, ...backlog].find((i) => i.id === id);
    if (!item || isLocked(item)) return;
    const publishAt = moveTo(item.publish_at, day, hour);
    if (item.publish_at && new Date(item.publish_at).getTime() === new Date(publishAt).getTime()) return;
    try {
      await updateItem(id, { publish_at: publishAt });
      toast(t("calendar.moved", { when: fmtDateTime(publishAt, locale) }));
    } catch (e) {
      toast.error(e instanceof Error && e.message ? e.message : t("common.error"));
    }
  };

  const zone = deviceZone();
  return (
    <div className="min-w-0 space-y-4">
      <CalendarToolbar view={view} onView={setView} anchor={anchor}
        onStep={(dir) => setAnchor((a) => shiftAnchor(view, a, dir))} onToday={() => setAnchor(startOfDay(new Date()))}
        filters={filters} onFilters={setFilters} channels={channels} content={content} onNewItem={newItem} />

      {owner && enabled === false && <p className="text-sm text-muted-foreground">{t("calendar.contentOff")}</p>}
      {status === "error" && (
        <div role="alert" className="flex flex-wrap items-center gap-3 rounded-md bg-error px-3 py-2 text-sm text-error-foreground">
          {t("calendar.loadFailed")}
          <Button size="sm" variant="secondary" onClick={() => void reloadCalendar()}>{t("chat.retry")}</Button>
        </div>
      )}

      <div aria-busy={status === "loading"} className="min-w-0">
        {view === "month" && (
          <MonthGrid anchor={anchor} today={now} buckets={buckets} dnd={dnd} onOpenItem={openItem}
            onDropItem={(id, day) => void dropItem(id, day)} onShowDay={(day) => { setAnchor(day); setView("week"); }} />
        )}
        {view === "week" && (
          <WeekGrid anchor={anchor} today={now} buckets={buckets} dnd={dnd} onOpenItem={openItem}
            onDropItem={(id, day, hour) => void dropItem(id, day, hour)} />
        )}
        {view === "agenda" && <AgendaList days={days} today={now} buckets={buckets} onOpenItem={openItem} />}
      </div>
      {zone && <p className="text-xs text-muted-foreground">{t("calendar.localTime", { tz: zone })}</p>}

      {content && <div className="pt-4"><BacklogPanel items={shownBacklog} dnd={dnd} onOpenItem={openItem} /></div>}

      <ContentItemDialog open={dialog.open} item={dialog.item} publishAt={dialog.publishAt}
        onOpenChange={(open) => {
          setDialog((d) => ({ ...d, open }));
          if (!open && deepId !== null) dropDeepLink();
        }} />
    </div>
  );
}
