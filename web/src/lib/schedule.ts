/**
 * Schedules as people read them. The server keeps the canonical string
 * ("0 8 * * 1-5", "every 3600s", an ISO instant, "manual"); the routines page
 * shows "Weekdays at 08:00". Anything this cannot name is shown as it is.
 */

import { currentLocale, t, type Key, type Locale } from "@/i18n";

export type Preset = { value: string; label: Key };

/** The frequencies the form offers outright; anything else is a custom schedule. */
export const PRESETS: Preset[] = [
  { value: "0 8 * * 1-5", label: "schedule.weekdaysAt" },
  { value: "0 9 * * *", label: "schedule.dailyAt" },
  { value: "0 9 * * 1", label: "schedule.onDayAt" },
  { value: "0 16 * * 5", label: "schedule.onDayAt" },
  { value: "every 1h", label: "schedule.everyHour" },
  // No clock at all: the routine fires only through "Run now" — or, for the content
  // publisher, through the content tick's wake. `cron_manage` never offers it, so
  // the form and the template are the only ways one is made.
  { value: "manual", label: "schedule.manual" },
];
/** A preset's label as text: presets describe themselves through `describeSchedule`. */
export function presetLabel(p: Preset, locale: Locale = currentLocale()): string { return describeSchedule(p.value, locale); }

const pad = (n: string) => n.padStart(2, "0");

function cron(text: string, locale: Locale): string | null {
  const f = text.split(/\s+/);
  if (f.length !== 5) return null;
  const [min, hour, dom, mon, dow] = f as [string, string, string, string, string];
  if (!/^\d{1,2}$/.test(min) || !/^\d{1,2}$/.test(hour) || dom !== "*" || mon !== "*") return null;
  const time = `${pad(hour)}:${pad(min)}`;
  if (dow === "*") return t("schedule.dailyAt", { time }, locale);
  if (dow === "1-5") return t("schedule.weekdaysAt", { time }, locale);
  if (dow === "0,6" || dow === "6,0" || dow === "6,7") return t("schedule.weekendsAt", { time }, locale);
  if (!/^[0-7]$/.test(dow)) return null;
  return t("schedule.onDayAt", { day: t(`schedule.days.${dow}` as Key, {}, locale), time }, locale);
}

function interval(text: string, locale: Locale): string | null {
  const m = /^(?:every\s+)?(\d+)\s*(s|m|h|d)$/i.exec(text.trim());
  if (!m) return null;
  const n = Number(m[1]);
  const unit = m[2]!.toLowerCase();
  const total = unit === "s" ? n : unit === "m" ? n * 60 : unit === "h" ? n * 3600 : n * 86400;
  // "every 1d" is "Every day", never "Every 1 day" — and the bare form is a key of
  // its own, because a plural `one` also covers 21, 31, 101 in Russian.
  const every = (count: number, bare: Key, counted: Key) => (count === 1 ? t(bare, {}, locale) : t(counted, { n: count }, locale));
  if (total % 86400 === 0) return every(total / 86400, "schedule.daily", "schedule.everyDay");
  if (total % 3600 === 0) return every(total / 3600, "schedule.hourly", "schedule.everyHour");
  if (total % 60 === 0) return every(total / 60, "schedule.minutely", "schedule.everyMinute");
  return every(total, "schedule.secondly", "schedule.everySecond");
}

function once(text: string, locale: Locale): string | null {
  if (!/^\d{4}-\d{2}-\d{2}T/.test(text)) return null;
  const d = new Date(text);
  if (Number.isNaN(d.getTime())) return null;
  const date = d.toLocaleString(locale === "en" ? "en-GB" : "ru-RU", { day: "numeric", month: "long", hour: "2-digit", minute: "2-digit" });
  return t("schedule.once", { date }, locale);
}

function manual(text: string, locale: Locale): string | null {
  return /^manual$/i.test(text.trim()) ? t("schedule.manual", {}, locale) : null;
}

export function describeSchedule(text: string, locale: Locale = currentLocale()): string {
  return manual(text, locale) ?? cron(text, locale) ?? interval(text, locale) ?? once(text, locale) ?? text;
}
