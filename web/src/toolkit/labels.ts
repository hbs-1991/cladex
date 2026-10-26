/**
 * Human labels for tool calls, in the language the model is thinking in: the
 * chain of thought shows its reasoning verbatim, and a Russian "Прочитал файл"
 * under an English thought reads as two voices. `detectLang` picks the
 * language from the reasoning text; without any, the interface language wins.
 */

import { currentLocale } from "@/i18n";
import {
  BookOpen, FileText, FilePenLine, FolderSearch, Globe, Image as ImageIcon, ListChecks, Network, Pencil, Plug, Search,
  Terminal, MessageCircleQuestion, FileOutput, Brain, Clock, Wallet, Wrench, type LucideIcon,
} from "lucide-react";

export type Lang = "ru" | "en";
type Label = { icon: LucideIcon; text: string; target?: string };
type Verb = [running: string, done: string];
type Verbs = Record<Lang, Verb>;

const AGENT_HOME = /^.*\/agent_home\//;
const short = (p: unknown) => (typeof p === "string" ? p.replace(AGENT_HOME, "") : undefined);
const str = (v: unknown, max = 80) => (typeof v === "string" ? (v.length > max ? v.slice(0, max - 1) + "…" : v) : undefined);

const TABLE: Record<string, { icon: LucideIcon; verb: Verbs; target?: (i: Record<string, unknown>) => string | undefined }> = {
  Read:      { icon: FileText,    verb: { ru: ["Читаю файл", "Прочитал файл"], en: ["Reading file", "Read file"] },        target: (i) => short(i.file_path) },
  Glob:      { icon: FolderSearch, verb: { ru: ["Ищу файлы", "Нашёл файлы"], en: ["Finding files", "Found files"] },      target: (i) => str(i.pattern) },
  Grep:      { icon: Search,      verb: { ru: ["Ищу в файлах", "Искал в файлах"], en: ["Searching files", "Searched files"] }, target: (i) => str(i.pattern) },
  Write:     { icon: FilePenLine, verb: { ru: ["Записываю файл", "Записал файл"], en: ["Writing file", "Wrote file"] },   target: (i) => short(i.file_path) },
  Edit:      { icon: Pencil,      verb: { ru: ["Правлю файл", "Поправил файл"], en: ["Editing file", "Edited file"] },    target: (i) => short(i.file_path) },
  Bash:      { icon: Terminal,    verb: { ru: ["Выполняю команду", "Выполнил команду"], en: ["Running command", "Ran command"] }, target: (i) => str(i.command) },
  WebSearch: { icon: Search,      verb: { ru: ["Ищу в интернете", "Искал в интернете"], en: ["Searching the web", "Searched the web"] }, target: (i) => str(i.query) },
  WebFetch:  { icon: Globe,       verb: { ru: ["Открываю страницу", "Открыл страницу"], en: ["Opening page", "Opened page"] }, target: (i) => str(i.url) },
  Agent:     { icon: Network,     verb: { ru: ["Делегирую субагенту", "Делегировал субагенту"], en: ["Delegating to a subagent", "Delegated to a subagent"] }, target: (i) => str(i.description) },
  Task:      { icon: Network,     verb: { ru: ["Делегирую субагенту", "Делегировал субагенту"], en: ["Delegating to a subagent", "Delegated to a subagent"] }, target: (i) => str(i.description) },
  TodoWrite: { icon: ListChecks,  verb: { ru: ["Планирую", "Обновил план"], en: ["Planning", "Updated the plan"] } },
  Skill:     { icon: BookOpen,    verb: { ru: ["Использую навык", "Использовал навык"], en: ["Using skill", "Used skill"] }, target: (i) => str(i.skill ?? i.name) },
  mcp__hermes__session_search: { icon: Brain,  verb: { ru: ["Ищу в памяти", "Искал в памяти"], en: ["Searching memory", "Searched memory"] }, target: (i) => str(i.query) },
  mcp__hermes__cron_manage:    { icon: Clock,  verb: { ru: ["Работаю с расписанием", "Изменил расписание"], en: ["Managing the schedule", "Changed the schedule"] } },
  mcp__hermes__cost_report:    { icon: Wallet, verb: { ru: ["Считаю расходы", "Посчитал расходы"], en: ["Computing costs", "Computed costs"] } },
  mcp__hermes__skill_admin:    { icon: Wrench, verb: { ru: ["Правлю навыки", "Изменил навыки"], en: ["Editing skills", "Changed skills"] } },
  mcp__hermes__ext_manage:     { icon: Plug,   verb: { ru: ["Управляю расширениями", "Изменил расширения"], en: ["Managing extensions", "Changed extensions"] } },
  mcp__hermes__send_document:  { icon: FileOutput, verb: { ru: ["Отправляю файл", "Отправил файл"], en: ["Sending file", "Sent file"] }, target: (i) => short(i.path) },
  mcp__hermes__ask_user:       { icon: MessageCircleQuestion, verb: { ru: ["Спрашиваю вас", "Спросил вас"], en: ["Asking you", "Asked you"] } },
};

const CALL: Verbs = { ru: ["Вызываю", "Вызвал"], en: ["Calling", "Called"] };

/** The gpt-image-2 skill draws through Bash, so a generation looks like any other
 * command until the command line is read: `.../gen.sh --prompt "..." --out ...`. */
const IMAGE_GEN = /gpt-image-2[/\\]scripts[/\\]gen\.sh/;
const IMAGE_PROMPT = /--prompt\s+(?:"((?:[^"\\]|\\.)*)"|'([^']*)')/;
const IMAGE: Verbs = { ru: ["Рисую изображение", "Нарисовал изображение"], en: ["Generating an image", "Generated an image"] };

/** The prompt of an image generation, `""` when the command hides it in a variable
 * or a heredoc, and `null` when this call draws nothing at all. */
export function imagePrompt(name: string, input: Record<string, unknown> | undefined): string | null {
  const command = input?.command;
  if (name !== "Bash" || typeof command !== "string" || !IMAGE_GEN.test(command)) return null;
  const m = IMAGE_PROMPT.exec(command);
  return m ? (m[1] ?? m[2] ?? "").replace(/\\(.)/g, "$1") : "";
}

export function labelFor(name: string, input: Record<string, unknown> | undefined, done: boolean, lang: Lang = currentLocale()): Label {
  const row = TABLE[name];
  const i = input ?? {};
  const prompt = imagePrompt(name, i);
  if (prompt !== null) return { icon: ImageIcon, text: IMAGE[lang][done ? 1 : 0], target: str(prompt) };
  if (row) return { icon: row.icon, text: row.verb[lang][done ? 1 : 0], target: row.target?.(i) };
  const call = CALL[lang][done ? 1 : 0];
  const mcp = /^mcp__([^_]+(?:_[^_]+)*)__(.+)$/.exec(name);
  if (mcp) return { icon: Plug, text: `${call} ${mcp[1]} · ${mcp[2]}` };
  return { icon: Wrench, text: `${call} ${name}` };
}

/** The language a thought is written in: Cyrillic wins ties, Latin means English,
 * and text with no letters at all (or none) keeps the interface language. */
export function detectLang(text: string | undefined | null, fallback: Lang = currentLocale()): Lang {
  if (!text) return fallback;
  const cyr = (text.match(/[Ѐ-ӿ]/g) ?? []).length;
  const lat = (text.match(/[A-Za-z]/g) ?? []).length;
  if (cyr === 0 && lat === 0) return fallback;
  return lat > cyr ? "en" : "ru";
}
