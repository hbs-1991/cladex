import { expect, it } from "vitest";
import { describeSchedule } from "./schedule";

it("names the common schedules the way a person would say them", () => {
  expect(describeSchedule("0 8 * * 1-5", "en")).toBe("Weekdays at 08:00");
  expect(describeSchedule("0 9 * * *", "en")).toBe("Every day at 09:00");
  expect(describeSchedule("30 16 * * 5", "en")).toBe("Every Friday at 16:30");
  expect(describeSchedule("0 10 * * 0,6", "en")).toBe("Weekends at 10:00");
  expect(describeSchedule("every 3600s", "en")).toBe("Every hour");
  expect(describeSchedule("every 2h", "en")).toBe("Every 2 hours");
  expect(describeSchedule("30m", "en")).toBe("Every 30 minutes");
  expect(describeSchedule("every 86400s", "en")).toBe("Every day");
  expect(describeSchedule("every 1m", "en")).toBe("Every minute");
  expect(describeSchedule("0 8 * * 1-5", "ru")).toBe("По будням в 08:00");
  expect(describeSchedule("0 9 * * 1", "ru")).toBe("По понедельникам в 09:00");
  expect(describeSchedule("every 2h", "ru")).toBe("Каждые 2 часа");
  expect(describeSchedule("every 1m", "ru")).toBe("Каждую минуту");
});

it("keeps the count when Russian would say it the singular way", () => {
  expect(describeSchedule("every 21d", "ru")).toBe("Каждые 21 день");
  expect(describeSchedule("31m", "ru")).toBe("Каждые 31 минуту");
  expect(describeSchedule("every 101h", "ru")).toBe("Каждые 101 час");
  expect(describeSchedule("21s", "ru")).toBe("Каждые 21 секунду");
  expect(describeSchedule("every 21d", "en")).toBe("Every 21 days");
});

it("names an on-demand routine", () => {
  expect(describeSchedule("manual", "en")).toBe("On demand");
  expect(describeSchedule("manual", "ru")).toBe("По требованию");
});

it("leaves what it cannot name as it is", () => {
  expect(describeSchedule("*/5 * * * *", "en")).toBe("*/5 * * * *");
  expect(describeSchedule("0 8 1 * *", "en")).toBe("0 8 1 * *");
  expect(describeSchedule("2026-09-10T09:00:00+00:00", "en")).toMatch(/^Once, 10 September/);
  expect(describeSchedule("2026-09-10T09:00:00+00:00", "ru")).toMatch(/^Один раз, 10 сентября/);
});
