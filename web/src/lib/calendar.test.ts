import { describe, expect, it } from "vitest";
import type { CalendarWire, ContentItemWire } from "@/api/types";
import {
  AGENDA_DAYS, agendaGroups, approvalRefusal, bucketDays, dayKey, fromLocalInput, isApproximate, isLocked, isMissed,
  monthGrid, moveTo, rangeTitle, shiftAnchor, startOfWeek, themeWeek, themesIn, toLocalInput, viewDays, viewRange,
  weekDays, weekdayNames,
} from "./calendar";

// Every instant below is built from LOCAL wall time, so the assertions hold in
// whatever zone the suite runs in — which is the property under test: the grid
// is the device's calendar, not UTC's.
const local = (y: number, m: number, d: number, h = 0, min = 0) => new Date(y, m - 1, d, h, min);
const iso = (y: number, m: number, d: number, h = 0, min = 0) => local(y, m, d, h, min).toISOString();

const item = (over: Partial<ContentItemWire>): ContentItemWire => ({
  id: 1, channel: "instagram", format: "reel", title: "Launch", body: "", media: [], publish_at: null, status: "draft",
  group_key: null, source_path: null, thread_id: null, created_via: "web", approved_at: null, attempts: 0,
  published_url: null, error: null, needs_attention: false, created_at: "", updated_at: "",
  theme_approved_at: null, draft_attempts: 0, redrafts: 0, draft_comment: null, card_state: "none", ...over,
});

describe("grids", () => {
  it("starts weeks on Monday", () => {
    expect(dayKey(startOfWeek(local(2026, 10, 4)))).toBe("2026-09-28");    // a Sunday belongs to the week before
    expect(dayKey(startOfWeek(local(2026, 9, 28)))).toBe("2026-09-28");    // a Monday is its own start
    expect(weekDays(local(2026, 10, 1)).map(dayKey)).toEqual([
      "2026-09-28", "2026-09-29", "2026-09-30", "2026-10-01", "2026-10-02", "2026-10-03", "2026-10-04"]);
  });

  it("lays a month out as whole Monday-to-Sunday weeks", () => {
    const weeks = monthGrid(local(2026, 10, 15));
    expect(weeks).toHaveLength(5);
    expect(dayKey(weeks[0]![0]!)).toBe("2026-09-28");
    expect(dayKey(weeks[4]![6]!)).toBe("2026-11-01");
    expect(weeks.every((w) => w.length === 7 && w[0]!.getDay() === 1)).toBe(true);
    // February 2027 starts on a Monday and ends on a Sunday: exactly four rows.
    expect(monthGrid(local(2027, 2, 10))).toHaveLength(4);
  });

  it("asks the server for a half-open range inside its 62-day limit", () => {
    const { start, end } = viewRange("month", local(2026, 10, 15));
    expect(dayKey(start)).toBe("2026-09-28");
    expect(dayKey(end)).toBe("2026-11-02");
    for (const v of ["month", "week", "agenda"] as const) {
      const r = viewRange(v, local(2026, 3, 31));
      expect((r.end.getTime() - r.start.getTime()) / 86_400_000).toBeLessThanOrEqual(62);
    }
    expect(viewDays("agenda", local(2026, 9, 30, 15))).toHaveLength(AGENDA_DAYS);
    expect(dayKey(viewDays("agenda", local(2026, 9, 30, 15))[0]!)).toBe("2026-09-30");   // the agenda opens on today
  });

  it("steps by a month without skipping a short one, by a week and by the agenda's length", () => {
    expect(dayKey(shiftAnchor("month", local(2026, 1, 31), 1))).toBe("2026-02-01");
    expect(dayKey(shiftAnchor("month", local(2026, 1, 31), -1))).toBe("2025-12-01");
    expect(dayKey(shiftAnchor("week", local(2026, 9, 28), 1))).toBe("2026-10-05");
    expect(dayKey(shiftAnchor("agenda", local(2026, 9, 28), 1))).toBe("2026-10-12");
  });
});

describe("bucketing", () => {
  const days = weekDays(local(2026, 10, 1));
  const now = local(2026, 9, 30, 12);

  it("puts items, fires and runs on their local day, sorted, items first at the same minute", () => {
    const cal: CalendarWire = {
      fires: [{ routine_id: 5, name: "Digest", at: iso(2026, 10, 1, 9) }],
      dense: [],
      runs: [{ routine_id: 5, name: "Digest", at: iso(2026, 9, 29, 9), status: "ok" }],
    };
    const b = bucketDays(days, [
      item({ id: 1, publish_at: iso(2026, 10, 1, 9) }),
      item({ id: 2, publish_at: iso(2026, 10, 1, 0, 30) }),     // just after local midnight: still the 1st
      item({ id: 3, publish_at: null }),                         // the backlog, not the grid
    ], cal, undefined, now);
    expect(b.get("2026-10-01")!.events.map((e) => e.key)).toEqual(["item:2", "item:1", `fire:5:${iso(2026, 10, 1, 9)}`]);
    expect(b.get("2026-09-29")!.events.map((e) => e.kind)).toEqual(["run"]);
    expect([...b.values()].flatMap((x) => x.events).some((e) => e.key === "item:3")).toBe(false);
  });

  it("marks a dense routine once a day from today on, folding its runs into a count", () => {
    const cal: CalendarWire = {
      fires: [],
      dense: [{ routine_id: 9, name: "Inbox check", every_seconds: 300 }],
      runs: [
        { routine_id: 9, name: "Inbox check", at: iso(2026, 9, 29, 10), status: "ok" },
        { routine_id: 9, name: "Inbox check", at: iso(2026, 9, 29, 10, 5), status: "ok" },
      ],
    };
    const b = bucketDays(days, [], cal, undefined, now);
    expect(b.get("2026-09-28")!.dense).toEqual([]);                          // past, nothing ran
    expect(b.get("2026-09-29")!.dense).toEqual([{ routineId: 9, name: "Inbox check", everySeconds: 300, runs: 2 }]);
    expect(b.get("2026-09-29")!.events).toEqual([]);                         // the runs are not chips
    expect(b.get("2026-09-30")!.dense).toHaveLength(1);                      // today
    expect(b.get("2026-10-04")!.dense).toHaveLength(1);
  });

  it("hides the channels and the routines the filters switch off", () => {
    const cal: CalendarWire = { fires: [{ routine_id: 5, name: "Digest", at: iso(2026, 10, 1, 9) }],
      dense: [{ routine_id: 9, name: "Inbox", every_seconds: 60 }], runs: [] };
    const items = [item({ id: 1, publish_at: iso(2026, 10, 1, 8) }), item({ id: 2, channel: "youtube", publish_at: iso(2026, 10, 1, 8) })];
    const b = bucketDays(days, items, cal, { hiddenChannels: ["instagram"], routines: false }, now);
    expect(b.get("2026-10-01")!.events.map((e) => e.key)).toEqual(["item:2"]);
    expect(b.get("2026-10-01")!.dense).toEqual([]);
  });

  it("lists only the days that have something in the agenda", () => {
    const b = bucketDays(days, [item({ publish_at: iso(2026, 10, 2, 10) })], null, undefined, now);
    expect(agendaGroups(days, b).map((g) => dayKey(g.day))).toEqual(["2026-10-02"]);
  });
});

describe("moving an item", () => {
  it("keeps the time of day on a month drop and takes the slot's hour on a week drop", () => {
    const at = iso(2026, 10, 1, 14, 45);
    expect(moveTo(at, local(2026, 10, 7))).toBe(iso(2026, 10, 7, 14, 45));
    expect(moveTo(at, local(2026, 10, 7), 9)).toBe(iso(2026, 10, 7, 9, 45));
  });

  it("gives an undated backlog item 09:00, or the slot's hour", () => {
    expect(moveTo(null, local(2026, 10, 7))).toBe(iso(2026, 10, 7, 9));
    expect(moveTo(null, local(2026, 10, 7), 18)).toBe(iso(2026, 10, 7, 18));
  });

  it("locks what the publisher holds and approximates what it will post", () => {
    expect(["drafting", "queued", "publishing", "published"].map((status) => isLocked(item({ status: status as ContentItemWire["status"] }))))
      .toEqual([true, true, true, true]);
    expect(isLocked(item({ status: "scheduled" }))).toBe(false);
    expect(isLocked(item({ status: "planned" }))).toBe(false);
    expect(isApproximate(item({ status: "scheduled" }))).toBe(true);
    expect(isApproximate(item({ status: "draft" }))).toBe(false);
  });
});

describe("the datetime-local field", () => {
  it("round-trips a local wall time through UTC", () => {
    const at = iso(2026, 10, 1, 10, 5);
    expect(toLocalInput(at)).toBe("2026-10-01T10:05");
    expect(fromLocalInput("2026-10-01T10:05")).toBe(at);
    expect(toLocalInput(null)).toBe("");
    expect(fromLocalInput("")).toBeNull();
  });
});

describe("labels", () => {
  it("names weekdays Monday first and months in the nominative", () => {
    expect(weekdayNames("en")).toEqual(["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]);
    expect(weekdayNames("ru")[0]).toBe("Пн");
    expect(rangeTitle("month", local(2026, 9, 15), "en")).toBe("September 2026");
    expect(rangeTitle("month", local(2026, 9, 15), "ru")).toBe("Сентябрь 2026");
    expect(rangeTitle("week", local(2026, 10, 1), "en")).toMatch(/^28 Sept? – 4 Oct 2026$/);   // ICU spells it either way
  });
});

describe("the approval rule", () => {
  it("refuses an empty body whatever the format", () => {
    expect(approvalRefusal("post", "  \n ", [])).toEqual({ code: "empty_body" });
    expect(approvalRefusal("post", "Text.", [])).toBeNull();                    // media optional
    expect(approvalRefusal("article", "Text.", ["content/a.png"])).toBeNull();
  });

  it("asks a carousel for 2 to 10 images", () => {
    const imgs = (n: number) => Array.from({ length: n }, (_, i) => `content/drafts/5/1/slide-${i}.png`);
    expect(approvalRefusal("carousel", "Text.", imgs(1))).toEqual({ code: "media_required", need: "carousel" });
    expect(approvalRefusal("carousel", "Text.", imgs(2))).toBeNull();
    expect(approvalRefusal("carousel", "Text.", imgs(10))).toBeNull();
    expect(approvalRefusal("carousel", "Text.", imgs(11))).toEqual({ code: "media_required", need: "carousel" });
  });

  it("asks a reel, a short and a video for exactly one video, and a photo for one image", () => {
    for (const format of ["reel", "short", "video"]) {
      expect(approvalRefusal(format, "Text.", ["content/cover.jpg"])).toEqual({ code: "media_required", need: "video" });
      expect(approvalRefusal(format, "Text.", ["content/cover.jpg", "content/reel.MP4"])).toBeNull();   // a cover is fine
      expect(approvalRefusal(format, "Text.", ["content/a.mp4", "content/b.mov"])).toEqual({ code: "media_required", need: "video" });
    }
    expect(approvalRefusal("photo", "Text.", [])).toEqual({ code: "media_required", need: "photo" });
    expect(approvalRefusal("photo", "Text.", ["content/a.webp"])).toBeNull();
    expect(approvalRefusal("photo", "Text.", ["content/a.webp", "content/b.jpeg"])).toEqual({ code: "media_required", need: "photo" });
  });
});

describe("themes", () => {
  const now = local(2026, 9, 30, 12);          // a Wednesday

  it("acts on the visible week in the week view and on this week otherwise", () => {
    expect(themeWeek("week", local(2026, 10, 8), now)).toEqual({ start: local(2026, 10, 5), end: local(2026, 10, 12) });
    expect(themeWeek("month", local(2026, 11, 1), now)).toEqual({ start: local(2026, 9, 28), end: local(2026, 10, 5) });
    expect(themeWeek("agenda", local(2026, 10, 20), now)).toEqual({ start: local(2026, 9, 28), end: local(2026, 10, 5) });
  });

  it("counts the dated ideas inside the week that are still ahead", () => {
    const { start, end } = themeWeek("month", now, now);
    const items = [
      item({ id: 1, status: "idea", publish_at: iso(2026, 10, 1, 9) }),
      item({ id: 2, status: "idea", publish_at: iso(2026, 9, 29, 9) }),     // already past
      item({ id: 3, status: "idea", publish_at: iso(2026, 10, 5, 9) }),     // next week
      item({ id: 4, status: "planned", publish_at: iso(2026, 10, 2, 9) }),  // already a theme
      item({ id: 5, status: "idea", publish_at: null }),
    ];
    expect(themesIn(items, start, end, now).map((i) => i.id)).toEqual([1]);
  });

  it("calls a post missed when its time came before it was drafted or approved", () => {
    const past = iso(2026, 9, 30, 9), ahead = iso(2026, 10, 1, 9);
    expect(isMissed(item({ status: "planned", publish_at: past }), now)).toBe(true);
    expect(isMissed(item({ status: "drafting", publish_at: past }), now)).toBe(true);
    expect(isMissed(item({ status: "review", publish_at: past }), now)).toBe(true);
    expect(isMissed(item({ status: "planned", publish_at: ahead }), now)).toBe(false);
    expect(isMissed(item({ status: "scheduled", publish_at: past, approved_at: past }), now)).toBe(false);
    expect(isMissed(item({ status: "idea", publish_at: past }), now)).toBe(false);
  });
});
