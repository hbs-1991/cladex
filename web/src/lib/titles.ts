/** What to call a thread that has no title yet: the channel says more than
 * "Untitled" — a Telegram lane is the chat itself, a cron lane a routine's. */

import { currentLocale, t, type Locale } from "@/i18n";
import type { ThreadWire } from "@/api/types";

export function threadTitle(th: Pick<ThreadWire, "title" | "channel">, locale: Locale = currentLocale()): string {
  if (th.title) return th.title;
  return t(th.channel === "telegram" ? "sidebar.telegramChat" : th.channel === "cron" ? "sidebar.routineChat" : "sidebar.untitled", {}, locale);
}
