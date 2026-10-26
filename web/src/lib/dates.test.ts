import { expect, it } from "vitest";
import { fmtDuration, groupByDay } from "./dates";

it("groups threads into Today / Yesterday / date labels, newest first, in either language", () => {
  const now = new Date("2026-07-12T15:00:00Z");
  const t = (id: string, iso: string) => ({ id, last_event_at: iso });
  const items = [t("a", "2026-07-12T10:00:00Z"), t("b", "2026-07-11T12:00:00Z"), t("c", "2026-07-01T09:00:00Z")];
  expect(groupByDay(items, now, "en").map((g) => g.label)).toEqual(["Today", "Yesterday", "1 July"]);
  expect(groupByDay(items, now, "ru").map((g) => g.label)).toEqual(["Сегодня", "Вчера", "1 июля"]);
  expect(groupByDay(items, now, "en")[0]!.items.map((x) => x.id)).toEqual(["a"]);
});

it("formats how long the agent worked", () => {
  expect(fmtDuration(661_000, "en")).toBe("11 min 1 s");
  expect(fmtDuration(42_400, "en")).toBe("42 s");
  expect(fmtDuration(120_000, "en")).toBe("2 min");
  expect(fmtDuration(3_720_000, "en")).toBe("1 h 2 min");
  expect(fmtDuration(-5, "en")).toBe("0 s");
  expect(fmtDuration(661_000, "ru")).toBe("11 мин 1 с");
});
