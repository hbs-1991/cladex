/**
 * The calendar's arithmetic, kept pure so it can be tested without a DOM: which
 * days a view shows, which instants a view asks the server for, and which local
 * day every content item, routine fire and routine run lands on.
 *
 * Everything here works in the DEVICE's zone. The server stores and answers UTC
 * (and evaluates cron in UTC); a post planned for 09:00 in Tashkent is 04:00Z on
 * the wire and 09:00 on the owner's phone, and only this file turns one into the
 * other. Weeks start on Monday, whatever the locale says.
 *
 * Day arithmetic goes through `setDate`/`setMonth`, never `+ 86 400 000`: a day
 * that crosses a daylight-saving change is 23 or 25 hours long, and adding
 * milliseconds would slide every later day off midnight.
 */

import type { CalendarWire, ContentItemWire } from "@/api/types";
import type { Locale } from "@/i18n";

export type CalendarView = "month" | "week" | "agenda";
export const VIEWS: readonly CalendarView[] = ["month", "week", "agenda"];
/** How many days the agenda lists, and how far "next" moves it. */
export const AGENDA_DAYS = 14;
/** What an undated item gets when it is dropped onto a month cell. */
export const DEFAULT_HOUR = 9;

const TAG: Record<Locale, string> = { en: "en-GB", ru: "ru-RU" };

export function startOfDay(d: Date): Date {
  const x = new Date(d);
  x.setHours(0, 0, 0, 0);
  return x;
}

export function addDays(d: Date, n: number): Date {
  const x = new Date(d);
  x.setDate(x.getDate() + n);
  return x;
}

/** Monday 00:00 of the week `d` falls in. `getDay()` counts from Sunday, so
 *  Monday is shifted to 0 and Sunday to 6. */
export function startOfWeek(d: Date): Date {
  const x = startOfDay(d);
  return addDays(x, -((x.getDay() + 6) % 7));
}

/** A local calendar day as a map key. Not `toISOString()`: that is the UTC day,
 *  which east of Greenwich is yesterday until morning. */
export function dayKey(d: Date): string {
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

/** The month around `anchor` as whole Monday-to-Sunday weeks: 4 to 6 rows, the
 *  days of the neighbouring months filling the first and last. */
export function monthGrid(anchor: Date): Date[][] {
  const first = new Date(anchor.getFullYear(), anchor.getMonth(), 1);
  const last = new Date(anchor.getFullYear(), anchor.getMonth() + 1, 0);
  const weeks: Date[][] = [];
  for (let day = startOfWeek(first); day <= last; day = addDays(day, 7)) {
    weeks.push(Array.from({ length: 7 }, (_, i) => addDays(day, i)));
  }
  return weeks;
}

export function weekDays(anchor: Date): Date[] {
  const monday = startOfWeek(anchor);
  return Array.from({ length: 7 }, (_, i) => addDays(monday, i));
}

/** Every day a view shows, in order. The agenda starts on the anchor's own day —
 *  on a phone the first thing worth seeing is today, not last Monday. */
export function viewDays(view: CalendarView, anchor: Date): Date[] {
  if (view === "month") return monthGrid(anchor).flat();
  if (view === "week") return weekDays(anchor);
  const start = startOfDay(anchor);
  return Array.from({ length: AGENDA_DAYS }, (_, i) => addDays(start, i));
}

/** The half-open instant range `[start, end)` a view asks both endpoints for. A
 *  six-week month is 42 days, well inside the server's 62-day limit. */
export function viewRange(view: CalendarView, anchor: Date): { start: Date; end: Date } {
  const days = viewDays(view, anchor);
  return { start: days[0]!, end: addDays(days[days.length - 1]!, 1) };
}

/** Where "previous" / "next" go. A month step lands on the 1st, so stepping from
 *  31 January never skips February. */
export function shiftAnchor(view: CalendarView, anchor: Date, dir: -1 | 1): Date {
  if (view === "month") return new Date(anchor.getFullYear(), anchor.getMonth() + dir, 1);
  return addDays(anchor, dir * (view === "week" ? 7 : AGENDA_DAYS));
}

// ---------------------------------------------------------------------------
// Events: what goes into a day.
// ---------------------------------------------------------------------------

export type CalEvent =
  | { kind: "item"; key: string; at: Date; item: ContentItemWire }
  | { kind: "fire"; key: string; at: Date; routineId: number; name: string }
  | { kind: "run"; key: string; at: Date; routineId: number; name: string; status: string };

/** A routine that fires hourly or more often, drawn once per day instead of
 *  once per fire. `runs` counts that day's recorded runs of it, which are folded
 *  in here for the same reason: a five-minute check leaves 288 of them a day. */
export type DenseMarker = { routineId: number; name: string; everySeconds: number; runs: number };

export type DayBucket = { events: CalEvent[]; dense: DenseMarker[] };

/** `hiddenChannels` are channels the reader switched off; `routines: false` hides
 *  every fire, run and dense marker at once. */
export type CalendarFilters = { hiddenChannels: string[]; routines: boolean };
export const NO_FILTERS: CalendarFilters = { hiddenChannels: [], routines: true };

/** Items sort before routine events at the same minute: the post is the reason
 *  someone opened the calendar. */
const ORDER: Record<CalEvent["kind"], number> = { item: 0, fire: 1, run: 2 };

/**
 * Bucket items, fires and runs into the given local days.
 *
 * Undated items belong to the backlog and are skipped. A dense routine is marked
 * on every day from today on (the server lists it only while it is active and
 * will fire) and on any earlier day that holds runs of it.
 */
export function bucketDays(
  days: Date[],
  items: ContentItemWire[],
  calendar: CalendarWire | null,
  filters: CalendarFilters = NO_FILTERS,
  now: Date = new Date(),
): Map<string, DayBucket> {
  const out = new Map<string, DayBucket>(days.map((d) => [dayKey(d), { events: [], dense: [] }]));
  const put = (e: CalEvent) => out.get(dayKey(e.at))?.events.push(e);

  const hidden = new Set(filters.hiddenChannels);
  for (const item of items) {
    if (!item.publish_at || hidden.has(item.channel)) continue;
    put({ kind: "item", key: `item:${item.id}`, at: new Date(item.publish_at), item });
  }

  if (calendar && filters.routines) {
    const dense = new Map(calendar.dense.map((d) => [d.routine_id, d]));
    for (const f of calendar.fires) {
      if (dense.has(f.routine_id)) continue;
      put({ kind: "fire", key: `fire:${f.routine_id}:${f.at}`, at: new Date(f.at), routineId: f.routine_id, name: f.name });
    }
    const denseRuns = new Map<string, number>();   // `${day}|${routine}` -> runs that day
    for (const r of calendar.runs) {
      const at = new Date(r.at);
      if (dense.has(r.routine_id)) {
        const k = `${dayKey(at)}|${r.routine_id}`;
        denseRuns.set(k, (denseRuns.get(k) ?? 0) + 1);
        continue;
      }
      put({ kind: "run", key: `run:${r.routine_id}:${r.at}`, at, routineId: r.routine_id, name: r.name, status: r.status });
    }
    const today = dayKey(now);
    for (const [key, bucket] of out) {
      for (const d of dense.values()) {
        const runs = denseRuns.get(`${key}|${d.routine_id}`) ?? 0;
        if (key >= today || runs > 0) {
          bucket.dense.push({ routineId: d.routine_id, name: d.name, everySeconds: d.every_seconds, runs });
        }
      }
    }
  }

  for (const bucket of out.values()) {
    bucket.events.sort((a, b) => a.at.getTime() - b.at.getTime() || ORDER[a.kind] - ORDER[b.kind]);
  }
  return out;
}

/** The agenda: only the days that have something in them, in order. */
export function agendaGroups(days: Date[], buckets: Map<string, DayBucket>): { day: Date; bucket: DayBucket }[] {
  return days
    .map((day) => ({ day, bucket: buckets.get(dayKey(day)) ?? { events: [], dense: [] } }))
    .filter(({ bucket }) => bucket.events.length > 0 || bucket.dense.length > 0);
}

// ---------------------------------------------------------------------------
// Drag and drop: where a dropped item lands.
// ---------------------------------------------------------------------------

/**
 * The new `publish_at` for an item dropped on `day`.
 *
 * On a month cell (`hour` undefined) the date moves and the time of day stays; on
 * a week slot the date and the hour move and the minutes stay. An undated item
 * from the backlog has no time to keep, so it takes 09:00 (or the slot's hour).
 * The answer is UTC ISO with its `Z`, so the server never has to guess the zone.
 */
export function moveTo(publishAt: string | null, day: Date, hour?: number): string {
  const from = publishAt ? new Date(publishAt) : null;
  const h = hour ?? from?.getHours() ?? DEFAULT_HOUR;
  const m = from ? from.getMinutes() : 0;
  return new Date(day.getFullYear(), day.getMonth(), day.getDate(), h, m, 0, 0).toISOString();
}

/** The MIME type a dragged item travels under, so a drop of anything else (a file,
 *  a link, text from another tab) is ignored rather than read as an id. */
export const DRAG_TYPE = "application/x-cladex-content-item";

/** What a chip calls an item: its working title, else the first line of its text. */
export function itemLabel(item: ContentItemWire, untitled: string): string {
  return item.title.trim() || item.body.trim().split("\n")[0]!.slice(0, 80) || untitled;
}

/** An item the publisher holds, or has already posted, stays where it is — and so
 *  does one the drafter is writing: its folder, time and format are what the
 *  drafter's run was started for, and the server refuses the edit anyway. */
export const LOCKED_STATUSES: readonly ContentItemWire["status"][] = ["drafting", "queued", "publishing", "published"];
export const isLocked = (item: ContentItemWire) => LOCKED_STATUSES.includes(item.status);

/** A post whose time came while it still waited — for the drafter, or for the
 *  owner's approval of the drafted text. Nothing went out; the server flags it
 *  and the dialog says why. */
export function isMissed(item: ContentItemWire, now: Date = new Date()): boolean {
  if (!item.publish_at || new Date(item.publish_at).getTime() > now.getTime()) return false;
  return item.status === "planned" || item.status === "drafting" || (item.status === "review" && !item.approved_at);
}

/** "≈ 09:00": approved and queued items go out when the publisher gets to them,
 *  and inline mode runs one routine at a time — never promise the minute. */
export const isApproximate = (item: ContentItemWire) => item.status === "scheduled" || item.status === "queued";

// ---------------------------------------------------------------------------
// The approval rule: what a post needs before anyone may approve it.
// ---------------------------------------------------------------------------

const IMAGE = /\.(png|jpe?g|webp|gif)$/i;
const VIDEO = /\.(mp4|mov|webm)$/i;
export const isVideo = (path: string) => VIDEO.test(path);

/** What a format needs in its media. Formats not listed take any media, or none. */
export type MediaNeed = "carousel" | "video" | "photo";
const NEEDS: Record<string, MediaNeed> = { carousel: "carousel", reel: "video", short: "video", video: "video", photo: "photo" };

export type ApprovalRefusal = { code: "empty_body" } | { code: "media_required"; need: MediaNeed };

/**
 * Why the owner may not approve this text and these files yet, or `null`.
 *
 * The server's gate (400 `empty_body` / `media_required`) is the authority; this
 * copy only greys the button out with a reason before the round trip. It counts
 * files of the needed kind and ignores the rest (a reel may carry a cover image),
 * so where it is unsure it lets the owner try and the server words the refusal.
 */
export function approvalRefusal(format: string, body: string, media: string[]): ApprovalRefusal | null {
  if (!body.trim()) return { code: "empty_body" };
  const need = NEEDS[format];
  if (!need) return null;
  const images = media.filter((p) => IMAGE.test(p)).length;
  const videos = media.filter((p) => VIDEO.test(p)).length;
  const ok = need === "carousel" ? images >= 2 && images <= 10 : need === "video" ? videos === 1 : images === 1;
  return ok ? null : { code: "media_required", need };
}

/** The week "Approve this week's themes" acts on: the visible one in the week
 *  view, and the week of `now` in the others — a month holds four or five weeks
 *  and the agenda two, and guessing which one was meant is worse than saying. */
export function themeWeek(view: CalendarView, anchor: Date, now: Date = new Date()): { start: Date; end: Date } {
  const start = startOfWeek(view === "week" ? anchor : now);
  return { start, end: addDays(start, 7) };
}

/** The ideas a theme approval over `[start, end)` would reach: dated inside the
 *  range and still ahead of `now`, because the server refuses a theme whose time
 *  has passed — the drafter would have no day left to write it in. */
export function themesIn(items: ContentItemWire[], start: Date, end: Date, now: Date = new Date()): ContentItemWire[] {
  return items.filter((i) => {
    if (i.status !== "idea" || !i.publish_at) return false;
    const at = new Date(i.publish_at).getTime();
    return at >= start.getTime() && at < end.getTime() && at > now.getTime();
  });
}

// ---------------------------------------------------------------------------
// <input type="datetime-local"> in and out.
// ---------------------------------------------------------------------------

/** `2026-10-01T05:00:00+00:00` -> `2026-10-01T10:00` on a UTC+5 device. */
export function toLocalInput(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const p = (n: number) => String(n).padStart(2, "0");
  return `${dayKey(d)}T${p(d.getHours())}:${p(d.getMinutes())}`;
}

/** The field's local wall time as a UTC instant; empty means "no date". */
export function fromLocalInput(value: string): string | null {
  if (!value) return null;
  const d = new Date(value);      // a date-time without an offset is read as local time
  return Number.isNaN(d.getTime()) ? null : d.toISOString();
}

// ---------------------------------------------------------------------------
// Labels.
// ---------------------------------------------------------------------------

const cap = (s: string) => s.charAt(0).toLocaleUpperCase() + s.slice(1);

/** Monday-first short weekday names: "Mon" … "Sun", "Пн" … "Вс". */
export function weekdayNames(locale: Locale, width: "short" | "narrow" = "short"): string[] {
  const monday = new Date(2024, 0, 1);            // a Monday
  return Array.from({ length: 7 }, (_, i) =>
    cap(addDays(monday, i).toLocaleDateString(TAG[locale], { weekday: width }).replace(/\.$/, "")));
}

/** "September 2026" / "Сентябрь 2026" for a month; "29 Sep – 5 Oct 2026" otherwise. */
export function rangeTitle(view: CalendarView, anchor: Date, locale: Locale): string {
  if (view === "month") {
    // `{ month: "long" }` alone is the nominative in Russian ("сентябрь"); with a
    // day next to it, it would be the genitive ("сентября").
    const month = anchor.toLocaleDateString(TAG[locale], { month: "long" });
    return `${cap(month)} ${anchor.getFullYear()}`;
  }
  const days = viewDays(view, anchor);
  const a = days[0]!, b = days[days.length - 1]!;
  const short = (d: Date) => d.toLocaleDateString(TAG[locale], { day: "numeric", month: "short" }).replace(/\.$/, "");
  return `${short(a)} – ${short(b)} ${b.getFullYear()}`;
}

/** "Monday, 28 September" — an agenda day heading. */
export function dayHeading(d: Date, locale: Locale): string {
  return cap(d.toLocaleDateString(TAG[locale], { weekday: "long", day: "numeric", month: "long" }));
}

/** The browser's zone, for the "times are in {tz}" note; empty if it will not say. */
export function deviceZone(): string {
  try { return Intl.DateTimeFormat().resolvedOptions().timeZone ?? ""; } catch { return ""; }
}
