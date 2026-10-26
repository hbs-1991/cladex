/** Starting points for a routine: pick one and the create dialog opens filled in.
 * The copy lives in the dictionary; a template carries only its key. */

import { Binoculars, CalendarDays, Inbox, Lightbulb, ListChecks, PenLine, Send, Sunrise, type LucideIcon } from "lucide-react";
import type { Key } from "@/i18n";

export type TemplateKey = "briefing" | "inbox" | "meetings" | "weekly" | "ideas" | "monitor" | "publisher" | "drafter";
/** `ownerOnly` marks a template that only makes sense for the deploy owner — the
 *  content plan exists for them alone, so a publisher or a drafter anyone else
 *  made would have no `content_plan` tool to work with. */
export type RoutineTemplate = { key: TemplateKey; icon: LucideIcon; schedule: string; timeout_seconds?: number; ownerOnly?: boolean };

export const TEMPLATES: RoutineTemplate[] = [
  { key: "briefing", icon: Sunrise, schedule: "0 8 * * 1-5" },
  { key: "inbox", icon: Inbox, schedule: "0 8 * * 1-5" },
  { key: "meetings", icon: CalendarDays, schedule: "0 8 * * 1-5" },
  { key: "weekly", icon: ListChecks, schedule: "0 16 * * 5" },
  { key: "ideas", icon: Lightbulb, schedule: "0 9 * * 1" },
  { key: "monitor", icon: Binoculars, schedule: "0 9 * * *" },
  // On demand, woken by the content tick when an approved item falls due. 30
  // minutes because one run may upload several videos one after another.
  { key: "publisher", icon: Send, schedule: "manual", timeout_seconds: 1800, ownerOnly: true },
  // On demand too, woken when an approved theme is a day from its time. One item
  // per run, and 30 minutes because a rendered reel alone takes 10 to 15.
  { key: "drafter", icon: PenLine, schedule: "manual", timeout_seconds: 1800, ownerOnly: true },
];

/** The publisher template, for the calendar's "create a publisher" shortcut. */
export const PUBLISHER_TEMPLATE = TEMPLATES.find((tpl) => tpl.key === "publisher")!;
/** The drafter template, for the calendar's "create a drafter" shortcut. */
export const DRAFTER_TEMPLATE = TEMPLATES.find((tpl) => tpl.key === "drafter")!;

type TemplateField = "name" | "description" | "prompt";

/** The dictionary key for one field of a template — checked, not cast, so a
 * renamed key or a typo in `TemplateKey` fails the typecheck. */
export const templateKey = (k: TemplateKey, field: TemplateField): Extract<Key, `routines.templates.${TemplateKey}.${TemplateField}`> =>
  `routines.templates.${k}.${field}`;
