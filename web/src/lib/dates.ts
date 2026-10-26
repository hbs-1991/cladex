/** Day labels, day grouping, times and durations — in the interface language. */

import { currentLocale, t, type Locale } from "@/i18n";

const TAG: Record<Locale, string> = { en: "en-GB", ru: "ru-RU" };

function dayKey(d: Date) { return `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`; }

/** Whether two instants fall on the same calendar day in the browser's zone. */
export function sameDay(a: Date, b: Date): boolean { return dayKey(a) === dayKey(b); }

export function dayLabel(iso: string | Date, now = new Date(), locale: Locale = currentLocale()): string {
  const d = new Date(iso);
  const today = dayKey(now);
  const y = new Date(now); y.setDate(y.getDate() - 1);
  if (dayKey(d) === today) return t("dates.today", {}, locale);
  if (dayKey(d) === dayKey(y)) return t("dates.yesterday", {}, locale);
  const opts: Intl.DateTimeFormatOptions = { day: "numeric", month: "long" };
  if (d.getFullYear() !== now.getFullYear()) opts.year = "numeric";
  return d.toLocaleDateString(TAG[locale], opts).replace(/\s?\p{Script=Cyrillic}+\.$/u, "");   // ru adds a year marker after the year
}

export function groupByDay<T extends { last_event_at: string }>(items: T[], now = new Date(), locale: Locale = currentLocale()): { label: string; items: T[] }[] {
  const sorted = [...items].sort((a, b) => b.last_event_at.localeCompare(a.last_event_at));
  const out: { label: string; items: T[] }[] = [];
  for (const it of sorted) {
    const label = dayLabel(it.last_event_at, now, locale);
    const last = out[out.length - 1];
    if (last && last.label === label) last.items.push(it); else out.push({ label, items: [it] });
  }
  return out;
}

export function fmtTime(iso: string, locale: Locale = currentLocale()) {
  return new Date(iso).toLocaleTimeString(TAG[locale], { hour: "2-digit", minute: "2-digit" });
}
export function fmtDateTime(iso: string, locale: Locale = currentLocale()) { return `${dayLabel(iso, new Date(), locale)}, ${fmtTime(iso, locale)}`; }

/** "1 h 2 min", "11 min 1 s", "42 s" — how long the agent worked. */
export function fmtDuration(ms: number, locale: Locale = currentLocale()): string {
  const total = Math.max(0, Math.round(ms / 1000));
  const h = Math.floor(total / 3600), m = Math.floor((total % 3600) / 60), s = total % 60;
  const H = t("dates.hours", { n: h }, locale), M = t("dates.minutes", { n: m }, locale), S = t("dates.seconds", { n: s }, locale);
  if (h > 0) return m > 0 ? `${H} ${M}` : H;
  if (m > 0) return s > 0 ? `${M} ${S}` : M;
  return S;
}
