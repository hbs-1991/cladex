# CladeX interface language and user menu — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** the web app speaks English by default and Russian on request, switched from a user menu in the sidebar; every interface string lives in one typed dictionary.

**Architecture:** a two-file dictionary (`en.ts` is the source of keys, `ru.ts` is typed against it) behind a tiny `t()`/`useT()` in `web/src/i18n/`; the locale is a client preference in the zustand ui slice next to the theme; helpers that format dates, durations and schedules take the locale as an argument. The sidebar footer becomes a dropdown with language, theme and log out. No server change.

**Tech Stack:** React 19, TypeScript 5.9, zustand, shadcn `DropdownMenu` (radio groups), vitest + Testing Library, Playwright. No i18n library.

Spec: `docs/superpowers/specs/2026-09-09-cladex-i18n-user-menu-design.md`.

## Locked Decisions

- **Locales `en` and `ru`, default `en`.** Type `Locale = "en" | "ru"`; the store field is `locale`; localStorage key `cladex.locale`.
- **Keys are dotted, feature-first, typed from `en.ts`.** `Key` is the union of leaf paths of `en`; `ru` is declared as `Shape<typeof en>` so a missing or extra key fails `tsc`.
- **Plural leaves are `{ one, few, many }` objects** chosen by `plural(n, locale)`; English uses `one` for 1 and `many` otherwise.
- **Interpolation is `{name}`** replaced from a params object; unknown placeholders stay literal (visible, never silent).
- **Tool-step labels follow the model's thought language**; `detectLang(text, fallback)` takes the UI locale as the fallback.
- **Server error messages are shown verbatim**; only codes the UI knows are translated (none in this cut).
- **The theme toggle moves from the sidebar header into the user menu**; `ThemeToggle.tsx` is deleted.

## Global Constraints

- Every user-visible string in `web/src` (outside `i18n/`, `toolkit/labels.ts` and tests) is read through `t()`/`useT()`. A test asserts no Cyrillic remains in source files outside those places.
- Not translated: thread titles, routine names and prompts, agent output, file names, server error messages, the bot username.
- Tests assert English by default; at least one component test switches the store to `ru` and checks a Russian string.
- All existing checks stay green: `npx tsc --noEmit -p tsconfig.app.json`, `npx eslint src`, `npx vitest run --dir src`, `npx playwright test` (all run from `web/`).

## Files

```
web/src/i18n/
  index.ts        Locale, LOCALES, Key, t, useT, plural, applyLocale        (Task 1)
  en.ts           the dictionary of record                                   (Task 1)
  ru.ts           Shape<typeof en>                                            (Task 1)
  i18n.test.ts    parity, interpolation, plurals, no stray Cyrillic         (Task 1, extended in Task 7)
web/src/store/ui.ts                 locale, setLocale                        (Task 1)
web/src/lib/dates.ts                locale-aware labels and durations         (Task 2)
web/src/lib/schedule.ts             locale-aware descriptions, preset labels  (Task 2)
web/src/toolkit/labels.ts           detectLang(text, fallback)                (Task 2)
web/src/components/cladex/StepGroup.tsx, ToolRow.tsx                        (Task 2)
web/src/components/cladex/UserMenu.tsx  new                                  (Task 3)
web/src/app/ThemeToggle.tsx         deleted                                   (Task 3)
web/src/components/cladex/Sidebar.tsx, ThreadRow.tsx, CommandPalette.tsx, ChatHeader.tsx,
  EmptyState.tsx, ConnectionBar.tsx, AskCard.tsx, web/src/lib/titles.ts     (Task 4)
web/src/components/assistant-ui/elements/thread.aui.tsx, FilePanel.tsx, FileCard.tsx,
  FilePreview.tsx                                                             (Task 5)
web/src/pages/Routines.tsx, Routine.tsx, RoutineForm.tsx, RoutineHeader.tsx, RunStrip.tsx,
  routineTemplates.ts                                                         (Task 6)
web/src/pages/Login.tsx, web/e2e/smoke.spec.ts, web/e2e/fake-server.ts        (Task 7)
```

---

### Task 1: Dictionary, `t()`, locale in the store

**Files:**
- Create: `web/src/i18n/index.ts`, `web/src/i18n/en.ts`, `web/src/i18n/ru.ts`
- Modify: `web/src/store/ui.ts`
- Test: `web/src/i18n/i18n.test.ts`

**Interfaces:**
- Reuses: `useStore` (`web/src/store/index.ts`), the `theme` persistence pattern in `web/src/store/ui.ts` (`readTheme`, `setTheme`, localStorage under `cladex.*`).
- Produces: `type Locale`, `LOCALES: readonly Locale[]`, `type Key`, `t(key: Key, params?: Record<string, string | number>, locale?: Locale): string`, `useT(): (key, params?) => string`, `plural(n: number, locale: Locale): "one" | "few" | "many"`, `applyLocale(l: Locale): void`; store fields `locale: Locale`, `setLocale(l: Locale)`.

- [ ] **Step 1: Write the failing test**

`web/src/i18n/i18n.test.ts`:

```ts
import { expect, it } from "vitest";
import { en } from "./en";
import { ru } from "./ru";
import { plural, t } from "./index";
import { useStore } from "@/store";

type Tree = Record<string, unknown>;
const leaves = (o: Tree, p = ""): string[] =>
  Object.entries(o).flatMap(([k, v]) =>
    typeof v === "string" || (v && typeof v === "object" && "one" in (v as Tree)) ? [p + k] : leaves(v as Tree, `${p}${k}.`));

it("has the same keys in both languages", () => {
  expect(leaves(ru as unknown as Tree).sort()).toEqual(leaves(en as unknown as Tree).sort());
});

it("interpolates, pluralises and leaves an unknown placeholder visible", () => {
  expect(t("sidebar.user", { id: 7 }, "en")).toBe("User 7");
  expect(t("steps.workedSteps", { n: 1 }, "en")).toBe("Worked 1 step");
  expect(t("steps.workedSteps", { n: 3 }, "en")).toBe("Worked 3 steps");
  expect(t("steps.workedSteps", { n: 1 }, "ru")).toBe("Работал 1 шаг");
  expect(t("steps.workedSteps", { n: 3 }, "ru")).toBe("Работал 3 шага");
  expect(t("steps.workedSteps", { n: 11 }, "ru")).toBe("Работал 11 шагов");
  expect(t("chat.files", {}, "en")).toBe("Files: {n}");
});

it("counts the Russian way", () => {
  expect([1, 2, 5, 11, 21, 22, 25].map((n) => plural(n, "ru"))).toEqual(["one", "few", "many", "many", "one", "few", "many"]);
  expect([1, 2].map((n) => plural(n, "en"))).toEqual(["one", "many"]);
});

it("defaults to English and remembers a switch", () => {
  useStore.setState(useStore.getInitialState());
  expect(useStore.getState().locale).toBe("en");
  expect(t("sidebar.newChat")).toBe("New chat");
  useStore.getState().setLocale("ru");
  expect(t("sidebar.newChat")).toBe("Новый чат");
  expect(localStorage.getItem("cladex.locale")).toBe("ru");
  expect(document.documentElement.lang).toBe("ru");
  useStore.getState().setLocale("en");
});
```

- [ ] **Step 2: Run it to see it fail**

Run (from `web/`): `npx vitest run --dir src src/i18n/i18n.test.ts`
Expected: FAIL — cannot resolve `./en`.

- [ ] **Step 3: Write the English dictionary**

`web/src/i18n/en.ts` — the dictionary of record. Every later task reads keys from here; do not add keys elsewhere.

```ts
/** The dictionary of record: keys are typed from this file; ru.ts must match it. */
export const en = {
  common: {
    cancel: "Cancel", save: "Save", close: "Close", download: "Download", copy: "Copy", copied: "Copied",
    copyFailed: "Could not copy", loading: "Loading…", error: "Error", nothingFound: "Nothing found.",
    actions: "Actions", rename: "Rename", archive: "Archive", title: "Title", delete: "Delete", edit: "Edit",
  },
  sidebar: {
    newChat: "New chat", scheduled: "Scheduled", recent: "Recent", showMore: "Show more", searchChats: "Search chats",
    owner: "Owner", user: "User {id}", untitled: "Untitled", telegramChat: "Telegram chat", routineChat: "Routine",
    channelTelegram: "Telegram", channelCron: "Scheduled", agentWorking: "Agent is working",
  },
  user: {
    menu: "Account menu", language: "Language", theme: "Theme", themeLight: "Light", themeDark: "Dark",
    themeSystem: "System", logOut: "Log out",
  },
  palette: { title: "Commands", description: "Search chats and actions", placeholder: "Chat or action…", actions: "Actions", chats: "Chats" },
  login: {
    lead: "Sign in with Telegram. Access is open to the bot owner and the users listed in allowed_users.",
    failed: "Sign-in failed. Check that your Telegram account is listed in allowed_users.",
    noBot: "The server did not report the bot name — Telegram sign-in is unavailable.",
  },
  chat: {
    newChat: "New chat", files: "Files: {n}", placeholder: "Message CladeX…", replyIn: "Reply in {name}…",
    hint: "Enter to send · Shift + Enter for a new line", scrollDown: "Scroll down", welcome: "How can I help?",
    welcomeLead: "Ask anything, attach a file or dictate. The history is shared with Telegram.",
    input: "Message input", dictate: "Dictate", startDictation: "Start dictation", stop: "Stop",
    stopDictation: "Stop dictation", send: "Send", sendMessage: "Send message", retry: "Retry", report: "Report",
    assistantWorking: "Assistant is working", again: "Again", readAloud: "Read aloud", stopReading: "Stop reading",
    more: "More", downloadMarkdown: "Download Markdown", previous: "Previous", next: "Next",
    loadingThread: "Loading the conversation", loadFailed: "Could not load the thread. Reload the page.",
    reconnecting: "Connection lost, reconnecting…",
  },
  ask: { chosen: "Chosen: {answer}", timedOut: "Time to answer ran out", cancelled: "Question cancelled" },
  steps: {
    thinking: "Thinking", thought: "Thought", workedFor: "Worked {duration}",
    workedSteps: { one: "Worked {n} step", few: "Worked {n} steps", many: "Worked {n} steps" },
    seconds: "{n} s",
  },
  files: {
    sentToTelegram: "Sent to Telegram", noPreview: "No preview for this file type.", downloadNamed: "Download {name}",
    loadFailed: "Could not load the file", bytes: "{n} B", kb: "{n} KB", mb: "{n} MB",
  },
  dates: { today: "Today", yesterday: "Yesterday", hours: "{n} h", minutes: "{n} min", seconds: "{n} s" },
  schedule: {
    weekdaysAt: "Weekdays at {time}", dailyAt: "Every day at {time}", weekendsAt: "Weekends at {time}",
    onDayAt: "Every {day} at {time}",
    days: { "0": "Sunday", "1": "Monday", "2": "Tuesday", "3": "Wednesday", "4": "Thursday", "5": "Friday", "6": "Saturday", "7": "Sunday" },
    everyDay: { one: "Every day", few: "Every {n} days", many: "Every {n} days" },
    everyHour: { one: "Every hour", few: "Every {n} hours", many: "Every {n} hours" },
    everyMinute: { one: "Every minute", few: "Every {n} minutes", many: "Every {n} minutes" },
    everySecond: { one: "Every second", few: "Every {n} seconds", many: "Every {n} seconds" },
    once: "Once, {date}",
  },
  routines: {
    title: "Scheduled", subtitle: "Tasks on a schedule or on demand.", find: "Find a task", searchTasks: "Search tasks",
    sort: "Sort", sortNext: "by next run", sortName: "by name", newTask: "New task", createInChat: "Create in chat",
    setUpManually: "Set up manually", empty: "No scheduled tasks yet.", active: "active", paused: "paused",
    finished: "finished", next: "next {when}", nextRunAt: "next run: {when}", timeout: "timeout {n} s", runNow: "Run now",
    queued: "Run queued", pause: "Pause", resume: "Resume", editRoutine: "Edit routine",
    deleteConfirm: "Delete the routine “{name}”?", notFound: "No such routine.", dialogNew: "New scheduled task",
    chatDraft: "Set up a scheduled task for me: ", lastRuns: "Last runs:",
    form: {
      name: "Name", namePlaceholder: "Daily briefing", instructions: "Instructions",
      instructionsPlaceholder: "Plan my meals for the week and write the shopping list.", frequency: "Frequency",
      custom: "Custom schedule…", schedule: "Schedule",
      hint: "“every 2h” or “30m” — an interval, “0 8 * * *” — cron, “2026-09-10T09:00” — once",
      timeout: "Timeout, seconds",
    },
    templates: {
      briefing: { name: "Daily briefing", description: "What needs your attention today across calendar, email and messages.",
        prompt: "Put together a morning briefing: today's meetings, emails and messages waiting for a reply, and the three most important things to do. Short, as a list." },
      inbox: { name: "Inbox triage", description: "Sort the inbox and draft replies to anything urgent.",
        prompt: "Go through new incoming email, split it into urgent, awaiting reply and informational. For the urgent ones propose a draft reply." },
      meetings: { name: "Meeting prep", description: "A short brief before each meeting: attendees, context, agenda.",
        prompt: "For every meeting today prepare a short brief: who attends, what was agreed before, what has to be decided." },
      weekly: { name: "Weekly review", description: "A Friday summary of what happened this week.",
        prompt: "Sum up the week: what got done, what stalled, what to carry over. Use the chats, notes and the calendar." },
      ideas: { name: "Content ideas", description: "A few post ideas from the latest news in your industry.",
        prompt: "Find the main news in my industry this week and propose five post ideas: headline, thesis, hook." },
      monitor: { name: "Monitor a topic", description: "Watch for news or mentions of a topic, competitor or keyword.",
        prompt: "Find new mentions of the topic “…” over the last day: news, posts, discussions. Summarise what matters and link to it." },
    },
  },
} as const;
```

- [ ] **Step 4: Write the Russian dictionary**

`web/src/i18n/ru.ts`:

```ts
import type { Shape } from "./index";
import type { en } from "./en";

export const ru: Shape<typeof en> = {
  common: {
    cancel: "Отмена", save: "Сохранить", close: "Закрыть", download: "Скачать", copy: "Копировать", copied: "Скопировано",
    copyFailed: "Не удалось скопировать", loading: "Загрузка…", error: "Ошибка", nothingFound: "Ничего не найдено.",
    actions: "Действия", rename: "Переименовать", archive: "В архив", title: "Название", delete: "Удалить", edit: "Изменить",
  },
  sidebar: {
    newChat: "Новый чат", scheduled: "Запланировано", recent: "Недавние", showMore: "Показать ещё", searchChats: "Поиск по чатам",
    owner: "Владелец", user: "Пользователь {id}", untitled: "Без названия", telegramChat: "Чат в Telegram", routineChat: "Routine",
    channelTelegram: "Telegram", channelCron: "По расписанию", agentWorking: "Агент работает",
  },
  user: {
    menu: "Меню аккаунта", language: "Язык", theme: "Тема", themeLight: "Светлая", themeDark: "Тёмная",
    themeSystem: "Системная", logOut: "Выйти",
  },
  palette: { title: "Команды", description: "Поиск по чатам и действиям", placeholder: "Чат или действие…", actions: "Действия", chats: "Чаты" },
  login: {
    lead: "Войдите через Telegram. Доступ есть у владельца бота и у пользователей из списка allowed_users.",
    failed: "Вход не удался. Проверьте, что ваш Telegram-аккаунт добавлен в allowed_users.",
    noBot: "Сервер не сообщил имя бота — вход через Telegram недоступен.",
  },
  chat: {
    newChat: "Новый чат", files: "Файлы: {n}", placeholder: "Напишите CladeX…", replyIn: "Ответить в {name}…",
    hint: "Enter — отправить · Shift + Enter — новая строка", scrollDown: "Вниз", welcome: "Чем помочь?",
    welcomeLead: "Спросите что угодно, приложите файл или продиктуйте голосом. История общая с Telegram.",
    input: "Поле ввода сообщения", dictate: "Диктовать", startDictation: "Начать диктовку", stop: "Стоп",
    stopDictation: "Остановить диктовку", send: "Отправить", sendMessage: "Отправить сообщение", retry: "Повторить", report: "Отчёт",
    assistantWorking: "Ассистент работает", again: "Ещё раз", readAloud: "Прочитать вслух", stopReading: "Остановить чтение",
    more: "Ещё", downloadMarkdown: "Скачать Markdown", previous: "Назад", next: "Вперёд",
    loadingThread: "Загружаю переписку", loadFailed: "Не удалось загрузить тред. Обновите страницу.",
    reconnecting: "Соединение прервано, переподключаюсь…",
  },
  ask: { chosen: "Выбрано: {answer}", timedOut: "Время ответа истекло", cancelled: "Вопрос отменён" },
  steps: {
    thinking: "Думаю", thought: "Подумал", workedFor: "Работал {duration}",
    workedSteps: { one: "Работал {n} шаг", few: "Работал {n} шага", many: "Работал {n} шагов" },
    seconds: "{n} с",
  },
  files: {
    sentToTelegram: "Отправлен в Telegram", noPreview: "Для этого типа файла нет предпросмотра.", downloadNamed: "Скачать {name}",
    loadFailed: "Не удалось загрузить файл", bytes: "{n} Б", kb: "{n} КБ", mb: "{n} МБ",
  },
  dates: { today: "Сегодня", yesterday: "Вчера", hours: "{n} ч", minutes: "{n} мин", seconds: "{n} с" },
  schedule: {
    weekdaysAt: "По будням в {time}", dailyAt: "Каждый день в {time}", weekendsAt: "По выходным в {time}",
    onDayAt: "По {day} в {time}",
    days: { "0": "воскресеньям", "1": "понедельникам", "2": "вторникам", "3": "средам", "4": "четвергам", "5": "пятницам", "6": "субботам", "7": "воскресеньям" },
    everyDay: { one: "Каждый день", few: "Каждые {n} дня", many: "Каждые {n} дней" },
    everyHour: { one: "Каждый час", few: "Каждые {n} часа", many: "Каждые {n} часов" },
    everyMinute: { one: "Каждую минуту", few: "Каждые {n} минуты", many: "Каждые {n} минут" },
    everySecond: { one: "Каждую секунду", few: "Каждые {n} секунды", many: "Каждые {n} секунд" },
    once: "Один раз, {date}",
  },
  routines: {
    title: "Запланировано", subtitle: "Задачи по расписанию или по требованию.", find: "Найти задачу", searchTasks: "Поиск по задачам",
    sort: "Сортировать", sortNext: "по запуску", sortName: "по названию", newTask: "Новая задача", createInChat: "Создать в чате",
    setUpManually: "Настроить вручную", empty: "Пока нет задач по расписанию.", active: "активна", paused: "на паузе",
    finished: "завершена", next: "следующий {when}", nextRunAt: "следующий запуск: {when}", timeout: "таймаут {n} с", runNow: "Запустить сейчас",
    queued: "Запуск поставлен в очередь", pause: "Пауза", resume: "Возобновить", editRoutine: "Изменить routine",
    deleteConfirm: "Удалить routine «{name}»?", notFound: "Такой routine нет.", dialogNew: "Новая задача по расписанию",
    chatDraft: "Настрой мне задачу по расписанию: ", lastRuns: "Последние запуски:",
    form: {
      name: "Название", namePlaceholder: "Утренняя сводка", instructions: "Инструкции",
      instructionsPlaceholder: "Спланируй меню на неделю и составь список покупок.", frequency: "Частота",
      custom: "Своё расписание…", schedule: "Расписание",
      hint: "«every 2h» или «30m» — интервал, «0 8 * * *» — cron, «2026-09-10T09:00» — один раз",
      timeout: "Таймаут, секунд",
    },
    templates: {
      briefing: { name: "Утренняя сводка", description: "Что требует внимания сегодня: календарь, почта, сообщения.",
        prompt: "Собери утреннюю сводку: встречи на сегодня, письма и сообщения, которые ждут ответа, и три главных дела дня. Коротко, списком." },
      inbox: { name: "Разбор входящих", description: "Рассортировать входящие и набросать ответы на срочное.",
        prompt: "Просмотри новые входящие письма, раздели их на срочные, ждущие ответа и информационные. Для срочных предложи черновик ответа." },
      meetings: { name: "Подготовка к встречам", description: "Краткая справка перед каждой встречей: участники, контекст, повестка.",
        prompt: "Для каждой встречи сегодня подготовь короткую справку: кто участвует, о чём договаривались раньше, что нужно решить." },
      weekly: { name: "Итоги недели", description: "Пятничная сводка того, что произошло за неделю.",
        prompt: "Подведи итоги недели: что сделано, что зависло, что перенести на следующую. Опирайся на чаты, заметки и календарь." },
      ideas: { name: "Идеи для постов", description: "Несколько идей для публикаций из свежих новостей отрасли.",
        prompt: "Найди главные новости моей отрасли за неделю и предложи пять идей для постов: заголовок, тезис, зацепка." },
      monitor: { name: "Мониторинг темы", description: "Следить за новостями и упоминаниями темы, конкурента или ключевого слова.",
        prompt: "Найди новые упоминания темы «…» за последние сутки: новости, посты, обсуждения. Кратко перескажи важное и дай ссылки." },
    },
  },
};
```

- [ ] **Step 5: Write `index.ts` and the store fields**

`web/src/i18n/index.ts`:

```ts
/**
 * Interface strings in two languages. `en` is the dictionary of record: `Key`
 * is typed from it and `ru` must have the same shape, so a language can never
 * fall behind silently. Components use `useT()`; plain functions take the
 * locale as an argument and default to the store's value.
 */

import { useStore } from "@/store";
import { en } from "./en";
import { ru } from "./ru";

export type Locale = "en" | "ru";
export const LOCALES: readonly Locale[] = ["en", "ru"];
export const DEFAULT_LOCALE: Locale = "en";

type PluralLeaf = { one: string; few: string; many: string };
type Leaf = string | PluralLeaf;
/** The same tree with every leaf widened to a plain string / plural object. */
export type Shape<T> = { [K in keyof T]: T[K] extends string ? string : T[K] extends PluralLeaf ? PluralLeaf : Shape<T[K]> };
type Paths<T, P extends string = ""> = {
  [K in keyof T & string]: T[K] extends Leaf ? `${P}${K}` : Paths<T[K], `${P}${K}.`>;
}[keyof T & string];
export type Key = Paths<typeof en>;
type Params = Record<string, string | number>;

const DICTS: Record<Locale, Shape<typeof en>> = { en, ru };

export function plural(n: number, locale: Locale): keyof PluralLeaf {
  const a = Math.abs(Math.trunc(n));
  if (locale === "en") return a === 1 ? "one" : "many";
  const m10 = a % 10, m100 = a % 100;
  if (m10 === 1 && m100 !== 11) return "one";
  if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return "few";
  return "many";
}

function lookup(locale: Locale, key: string): Leaf | undefined {
  let node: unknown = DICTS[locale];
  for (const part of key.split(".")) {
    if (!node || typeof node !== "object") return undefined;
    node = (node as Record<string, unknown>)[part];
  }
  return node as Leaf | undefined;
}

function fill(text: string, params?: Params): string {
  if (!params) return text;
  return text.replace(/\{(\w+)\}/g, (m, name: string) => (name in params ? String(params[name]) : m));
}

export function currentLocale(): Locale { return useStore.getState().locale; }

export function t(key: Key, params?: Params, locale: Locale = currentLocale()): string {
  const leaf = lookup(locale, key) ?? lookup(DEFAULT_LOCALE, key);
  if (leaf === undefined) return key;
  if (typeof leaf === "string") return fill(leaf, params);
  const n = typeof params?.n === "number" ? params.n : Number(params?.n ?? 0);
  return fill(leaf[plural(n, locale)], params);
}

/** Re-renders with the locale: components call this, not `t` directly. */
export function useT() {
  const locale = useStore((s) => s.locale);
  return (key: Key, params?: Params) => t(key, params, locale);
}

export function useLocale(): Locale { return useStore((s) => s.locale); }

export function applyLocale(l: Locale) { document.documentElement.lang = l; }
```

`web/src/store/ui.ts` — add the locale next to the theme:

```ts
import type { Locale } from "@/i18n";          // type-only import: no cycle at runtime

const LOCALE_KEY = "cladex.locale";

export type UiSlice = {
  theme: Theme; setTheme: (t: Theme) => void;
  locale: Locale; setLocale: (l: Locale) => void;
  openFile: FileWire | null; setOpenFile: (f: FileWire | null) => void;
  me: Me | null; setMe: (m: Me | null) => void;
  paletteOpen: boolean; setPaletteOpen: (v: boolean) => void;
};

function readLocale(): Locale {
  try { const v = localStorage.getItem(LOCALE_KEY); if (v === "en" || v === "ru") return v; } catch { /* private mode */ }
  return "en";
}

export function applyLocale(l: Locale) { document.documentElement.lang = l; }

// inside createUiSlice, after theme:
  locale: readLocale(),
  setLocale(l) { try { localStorage.setItem(LOCALE_KEY, l); } catch { /* ignore */ } applyLocale(l); set({ locale: l }); },
```

Keep `applyLocale` in `ui.ts` (the store owns side effects, as it does for the theme) and re-export it from `i18n/index.ts` instead of defining it twice: replace the last line of `index.ts` with `export { applyLocale } from "@/store/ui";`. `Layout.tsx` calls `applyLocale(locale)` in the same effect that calls `applyTheme(theme)`:

```tsx
const locale = useStore((s) => s.locale);
useEffect(() => { applyTheme(theme); applyLocale(locale); }, [theme, locale]);
```

- [ ] **Step 6: Run the test and the typecheck**

Run: `npx vitest run --dir src src/i18n/i18n.test.ts && npx tsc --noEmit -p tsconfig.app.json`
Expected: 4 tests pass; `tsc` clean. Deliberately break `ru.ts` (delete one key) and confirm `tsc` reports it, then restore it.

- [ ] **Step 7: Commit**

```bash
git add web/src/i18n web/src/store/ui.ts web/src/app/Layout.tsx
git commit -m "feat(web): two-locale dictionary with typed keys; locale in the store, English by default"
```

---

### Task 2: Locale-aware dates, durations, schedules and step labels

**Files:**
- Modify: `web/src/lib/dates.ts`, `web/src/lib/schedule.ts`, `web/src/toolkit/labels.ts`, `web/src/components/cladex/StepGroup.tsx`, `web/src/components/cladex/ToolRow.tsx`
- Test: `web/src/lib/dates.test.ts`, `web/src/lib/schedule.test.ts`, `web/src/toolkit/labels.test.ts`, `web/src/components/cladex/StepGroup.test.tsx`

**Interfaces:**
- Reuses: `dayLabel`, `groupByDay`, `fmtTime`, `fmtDateTime`, `fmtDuration` (`dates.ts`); `describeSchedule`, `PRESETS` (`schedule.ts`); `labelFor`, `detectLang`, `Lang` (`labels.ts`); `runLabel`, `groupLabel` (`StepGroup.tsx`).
- Consumes: `t`, `plural`, `currentLocale`, `Locale`, `Key` from Task 1.
- Produces: `dayLabel(iso, now?, locale?)`, `fmtTime(iso, locale?)`, `fmtDateTime(iso, locale?)`, `fmtDuration(ms, locale?)`; `describeSchedule(text, locale?)`; `PRESETS: { value: string; label: Key }[]` (labels are keys, resolved with `t`); `detectLang(text, fallback?: Lang)`; `runLabel(calls, lang)`, `groupLabel(parts, running, durationMs?, lang)` unchanged signatures, English output when `lang === "en"`.

- [ ] **Step 1: Update the tests to the new contracts**

`web/src/lib/dates.test.ts` — replace the file:

```ts
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
```

`web/src/lib/schedule.test.ts` — replace the file:

```ts
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

it("leaves what it cannot name as it is", () => {
  expect(describeSchedule("*/5 * * * *", "en")).toBe("*/5 * * * *");
  expect(describeSchedule("0 8 1 * *", "en")).toBe("0 8 1 * *");
  expect(describeSchedule("2026-09-10T09:00:00+00:00", "en")).toMatch(/^Once, 10 September/);
  expect(describeSchedule("2026-09-10T09:00:00+00:00", "ru")).toMatch(/^Один раз, 10 сентября/);
});
```

`web/src/toolkit/labels.test.ts` — change the third test:

```ts
it("detects the language of a thought and falls back to the interface language when unsure", () => {
  expect(detectLang("I'll check the repository's design context first.")).toBe("en");
  expect(detectLang("Проверю контекст дизайна в репозитории.")).toBe("ru");
  expect(detectLang("Нужно прочитать файл README.md")).toBe("ru");   // Cyrillic outweighs the Latin file name
  expect(detectLang("", "en")).toBe("en");
  expect(detectLang(undefined, "ru")).toBe("ru");
  expect(detectLang("1234 …", "en")).toBe("en");
  expect(detectLang("")).toBe("en");                                    // the store's default locale
});
```

`web/src/components/cladex/StepGroup.test.tsx` — replace the first test and extend the second:

```ts
it("labels a running group with the current step and a finished one with the time it took", () => {
  expect(groupLabel([tool("WebSearch", "running", { query: "x" })] as never, true, undefined, "en")).toBe("Searching the web");
  expect(groupLabel([thought("hm")] as never, true, undefined, "en")).toBe("Thinking");
  const done = [tool("Read", "complete"), tool("Bash", "complete")];
  expect(groupLabel(done as never, false, 661_000, "en")).toBe("Worked 11 min 1 s");
  expect(groupLabel(done as never, false, undefined, "en")).toBe("Worked 2 steps");        // no timing yet: count
  expect(groupLabel([done[0]!] as never, false, undefined, "en")).toBe("Worked 1 step");
  expect(groupLabel([thought("hm")] as never, false, undefined, "en")).toBe("Thought");
  expect(groupLabel(done as never, false, 661_000, "ru")).toBe("Работал 11 мин 1 с");
  expect(groupLabel(new Array(5).fill(done[0]) as never, false, undefined, "ru")).toBe("Работал 5 шагов");
  expect(groupLabel([thought("hm")] as never, true, undefined, "ru")).toBe("Думаю");
});
```

(The `runLabel` test already passes `"en"`/default; leave it, its default remains `"ru"` output only when called with `"ru"` — change the two default-language calls to pass `"ru"` explicitly.)

- [ ] **Step 2: Run the four test files to see them fail**

Run: `npx vitest run --dir src src/lib src/toolkit src/components/cladex/StepGroup.test.tsx`
Expected: failures on English expectations and on the new arguments.

- [ ] **Step 3: Implement**

`web/src/lib/dates.ts` — replace the file:

```ts
/** Day labels, day grouping, times and durations — in the interface language. */

import { currentLocale, t, type Locale } from "@/i18n";

const TAG: Record<Locale, string> = { en: "en-GB", ru: "ru-RU" };

function dayKey(d: Date) { return `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`; }

export function dayLabel(iso: string, now = new Date(), locale: Locale = currentLocale()): string {
  const d = new Date(iso);
  const today = dayKey(now);
  const y = new Date(now); y.setDate(y.getDate() - 1);
  if (dayKey(d) === today) return t("dates.today", {}, locale);
  if (dayKey(d) === dayKey(y)) return t("dates.yesterday", {}, locale);
  const opts: Intl.DateTimeFormatOptions = { day: "numeric", month: "long" };
  if (d.getFullYear() !== now.getFullYear()) opts.year = "numeric";
  return d.toLocaleDateString(TAG[locale], opts).replace(/\s?г\.$/, "");   // ru adds "г." after the year
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
```

`web/src/lib/schedule.ts` — replace the file:

```ts
/**
 * Schedules as people read them. The server keeps the canonical string
 * ("0 8 * * 1-5", "every 3600s", an ISO instant); the routines page shows
 * "Weekdays at 08:00". Anything this cannot name is shown as it is.
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
  if (total % 86400 === 0) return t("schedule.everyDay", { n: total / 86400 }, locale);
  if (total % 3600 === 0) return t("schedule.everyHour", { n: total / 3600 }, locale);
  if (total % 60 === 0) return t("schedule.everyMinute", { n: total / 60 }, locale);
  return t("schedule.everySecond", { n: total }, locale);
}

function once(text: string, locale: Locale): string | null {
  if (!/^\d{4}-\d{2}-\d{2}T/.test(text)) return null;
  const d = new Date(text);
  if (Number.isNaN(d.getTime())) return null;
  const date = d.toLocaleString(locale === "en" ? "en-GB" : "ru-RU", { day: "numeric", month: "long", hour: "2-digit", minute: "2-digit" });
  return t("schedule.once", { date }, locale);
}

export function describeSchedule(text: string, locale: Locale = currentLocale()): string {
  return cron(text, locale) ?? interval(text, locale) ?? once(text, locale) ?? text;
}
```

`web/src/toolkit/labels.ts` — `detectLang` takes a fallback and the module default becomes the interface locale:

```ts
import { currentLocale } from "@/i18n";
// …
export function labelFor(name: string, input: Record<string, unknown> | undefined, done: boolean, lang: Lang = currentLocale()): Label {
// …
/** The language a thought is written in: Cyrillic wins ties, Latin means English,
 * and text with no letters at all (or none) keeps the interface language. */
export function detectLang(text: string | undefined | null, fallback: Lang = currentLocale()): Lang {
  if (!text) return fallback;
  const cyr = (text.match(/[Ѐ-ӿ]/g) ?? []).length;
  const lat = (text.match(/[A-Za-z]/g) ?? []).length;
  if (cyr === 0 && lat === 0) return fallback;
  return lat > cyr ? "en" : "ru";
}
```

`web/src/components/cladex/StepGroup.tsx` — delete the local `plural`; the labels go through `t` with the group's language:

```ts
import { t, type Locale } from "@/i18n";
// …
export function runLabel(calls: ToolStep[], lang: Lang = currentLocale()): { text: string; running: boolean } { /* body unchanged */ }

export function groupLabel(parts: Step[], running: boolean, durationMs?: number, lang: Lang = currentLocale()): string {
  const locale: Locale = lang;
  if (running) {
    const current = [...parts].reverse().find((p) => p.status.type === "running") ?? parts[parts.length - 1];
    if (current?.type === "tool-call") return labelFor(current.toolName, current.args as Record<string, unknown>, false, lang).text;
    return t("steps.thinking", {}, locale);
  }
  if (durationMs !== undefined) return t("steps.workedFor", { duration: fmtDuration(durationMs, locale) }, locale);
  const n = parts.filter((p) => p.type === "tool-call").length;
  return n ? t("steps.workedSteps", { n }, locale) : t("steps.thought", {}, locale);
}
```

In the component body, where `lang` is computed from the reasoning text, pass the UI locale as the fallback: `const lang = detectLang(reasoningText, useLocale());`.

`web/src/components/cladex/ToolRow.tsx` — the duration unit: `{ms !== undefined && done ? t("steps.seconds", { n: (ms / 1000).toFixed(1) }, lang) : null}` with `import { t } from "@/i18n"`; the `lang` default becomes `currentLocale()`.

- [ ] **Step 4: Run the tests and the typecheck**

Run: `npx vitest run --dir src && npx tsc --noEmit -p tsconfig.app.json`
Expected: everything passes (component tests that still assert Russian sidebar/routine strings are untouched by this task and keep passing because their components have not moved to the dictionary yet).

- [ ] **Step 5: Commit**

```bash
git add web/src/lib web/src/toolkit web/src/components/cladex/StepGroup.tsx web/src/components/cladex/ToolRow.tsx
git commit -m "feat(web): dates, durations, schedules and step labels follow the interface language"
```

---

### Task 3: The user menu

**Files:**
- Create: `web/src/components/cladex/UserMenu.tsx`
- Delete: `web/src/app/ThemeToggle.tsx`
- Modify: `web/src/components/cladex/Sidebar.tsx` (header and footer)
- Test: `web/src/components/cladex/UserMenu.test.tsx`

**Interfaces:**
- Reuses: `DropdownMenu*` incl. `DropdownMenuRadioGroup`/`DropdownMenuRadioItem`/`DropdownMenuLabel`/`DropdownMenuSeparator` (`web/src/components/ui/dropdown-menu.tsx`); `api.logout` (`web/src/api/client.ts:43`); store `me`, `setMe`, `theme`, `setTheme`, `locale`, `setLocale`.
- Consumes: `useT`, `LOCALES` from Task 1.
- Produces: `<UserMenu />` — renders nothing when `me` is null.

- [ ] **Step 1: Write the failing test**

`web/src/components/cladex/UserMenu.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { beforeEach, expect, it, vi } from "vitest";

vi.mock("@/api/client", () => ({ api: { logout: vi.fn(async () => {}) } }));

import { api } from "@/api/client";
import { useStore } from "@/store";
import { UserMenu } from "./UserMenu";

beforeEach(() => {
  useStore.setState(useStore.getInitialState());
  useStore.setState({ me: { user_id: 1, role: "owner" } });
  useStore.getState().setLocale("en");
});

it("names who is signed in and switches the language from the menu", async () => {
  render(<MemoryRouter><UserMenu /></MemoryRouter>);
  expect(screen.getByText("Owner")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Account menu" }));
  await userEvent.click(await screen.findByRole("menuitemradio", { name: "Русский" }));
  expect(useStore.getState().locale).toBe("ru");
  expect(screen.getByText("Владелец")).toBeInTheDocument();
});

it("changes the theme and logs out", async () => {
  render(<MemoryRouter><UserMenu /></MemoryRouter>);
  await userEvent.click(screen.getByRole("button", { name: "Account menu" }));
  await userEvent.click(await screen.findByRole("menuitemradio", { name: "Dark" }));
  expect(useStore.getState().theme).toBe("dark");
  await userEvent.click(screen.getByRole("button", { name: "Account menu" }));
  await userEvent.click(await screen.findByRole("menuitem", { name: "Log out" }));
  expect(api.logout).toHaveBeenCalled();
  expect(useStore.getState().me).toBeNull();
});
```

- [ ] **Step 2: Run it to see it fail**

Run: `npx vitest run --dir src src/components/cladex/UserMenu.test.tsx`
Expected: FAIL — `./UserMenu` not found.

- [ ] **Step 3: Write the component**

`web/src/components/cladex/UserMenu.tsx`:

```tsx
/** The sidebar's footer: who is signed in, and a menu with the language, the theme and log out. */

import { useNavigate } from "react-router";
import { Check } from "lucide-react";
import { api } from "@/api/client";
import { LOCALES, useT, type Locale } from "@/i18n";
import { useStore } from "@/store";
import type { Theme } from "@/store/ui";
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel, DropdownMenuRadioGroup, DropdownMenuRadioItem,
  DropdownMenuSeparator, DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

const LANGUAGE_NAMES: Record<Locale, string> = { en: "English", ru: "Русский" };   // each language names itself
const THEMES: Theme[] = ["light", "dark", "system"];

export function UserMenu() {
  const t = useT();
  const me = useStore((s) => s.me);
  const setMe = useStore((s) => s.setMe);
  const locale = useStore((s) => s.locale);
  const setLocale = useStore((s) => s.setLocale);
  const theme = useStore((s) => s.theme);
  const setTheme = useStore((s) => s.setTheme);
  const navigate = useNavigate();
  if (!me) return null;
  const who = me.role === "owner" ? t("sidebar.owner") : t("sidebar.user", { id: me.user_id });
  const logout = async () => { try { await api.logout(); } finally { setMe(null); navigate("/login", { replace: true }); } };
  const themeName = (v: Theme) => t(v === "light" ? "user.themeLight" : v === "dark" ? "user.themeDark" : "user.themeSystem");
  return (
    <DropdownMenu>
      <DropdownMenuTrigger aria-label={t("user.menu")}
        className="flex w-full items-center gap-2.5 border-t border-sidebar-border px-4 py-3 text-left text-sm hover:bg-sidebar-accent data-[state=open]:bg-sidebar-accent">
        <span aria-hidden className="flex size-6 shrink-0 items-center justify-center rounded-full bg-foreground text-[11px] font-medium text-background">{who.charAt(0)}</span>
        <span className="truncate">{who}</span>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" side="top" className="w-56">
        <DropdownMenuLabel>{t("user.language")}</DropdownMenuLabel>
        <DropdownMenuRadioGroup value={locale} onValueChange={(v) => setLocale(v as Locale)}>
          {LOCALES.map((l) => <DropdownMenuRadioItem key={l} value={l}>{LANGUAGE_NAMES[l]}</DropdownMenuRadioItem>)}
        </DropdownMenuRadioGroup>
        <DropdownMenuSeparator />
        <DropdownMenuLabel>{t("user.theme")}</DropdownMenuLabel>
        <DropdownMenuRadioGroup value={theme} onValueChange={(v) => setTheme(v as Theme)}>
          {THEMES.map((v) => <DropdownMenuRadioItem key={v} value={v}>{themeName(v)}</DropdownMenuRadioItem>)}
        </DropdownMenuRadioGroup>
        <DropdownMenuSeparator />
        <DropdownMenuItem onSelect={() => void logout()}>{t("user.logOut")}</DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
```

(`Check` is unused if the shadcn radio item draws its own indicator — remove the import in that case; keep `eslint` clean.)

In `Sidebar.tsx`: delete the `ThemeToggle` import and element from the header (the header keeps the name and the search button), replace the whole `{me && (<div className="flex items-center gap-2.5 border-t …">…</div>)}` footer with `<UserMenu />`, and remove the now-unused `who` variable. Delete `web/src/app/ThemeToggle.tsx`.

- [ ] **Step 4: Run the tests and the typecheck**

Run: `npx vitest run --dir src src/components/cladex && npx tsc --noEmit -p tsconfig.app.json`
Expected: `UserMenu.test.tsx` passes; `Sidebar.test.tsx`'s "names who is signed in" assertion still finds "Владелец" only if the store locale is `ru` — that test is rewritten in Task 4; for now it fails on that one line, which is expected and fixed next.

- [ ] **Step 5: Commit**

```bash
git add web/src/components/cladex/UserMenu.tsx web/src/components/cladex/UserMenu.test.tsx web/src/components/cladex/Sidebar.tsx
git rm web/src/app/ThemeToggle.tsx
git commit -m "feat(web): user menu in the sidebar footer — language, theme, log out"
```

---

### Task 4: Sidebar, thread rows, palette, chat header, empty state, connection bar, ask card

**Files:**
- Modify: `web/src/components/cladex/Sidebar.tsx`, `ThreadRow.tsx`, `CommandPalette.tsx`, `ChatHeader.tsx`, `EmptyState.tsx`, `ConnectionBar.tsx`, `AskCard.tsx`, `web/src/lib/titles.ts`
- Test: `web/src/components/cladex/Sidebar.test.tsx`, `ConnectionBar.test.tsx`, `AskCard.test.tsx`

**Interfaces:**
- Reuses: the components as they are; `threadTitle` (`lib/titles.ts`).
- Consumes: `useT`, `t`, `currentLocale` from Task 1.
- Produces: `threadTitle(t, locale?)`.

- [ ] **Step 1: Rewrite the tests to English, with one Russian switch**

`Sidebar.test.tsx` — the two assertions on text become:

```tsx
  expect(titles).toEqual(["План на неделю", "Telegram chat", "Утренний дайджест"]);   // an untitled lane is named by its channel
  expect(screen.getByLabelText("Telegram")).toBeInTheDocument();
  expect(screen.getByLabelText("Scheduled")).toBeInTheDocument();
  expect(screen.queryByText("TG")).toBeNull();
  expect(screen.getByLabelText("Agent is working")).toBeInTheDocument();
  expect(screen.getByText("Recent")).toBeInTheDocument();
// second test:
  expect(screen.getByRole("button", { name: /New chat/ })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: /Scheduled/ })).toHaveAttribute("href", "/routines");
  screen.getByRole("button", { name: "Search chats" }).click();
  expect(useStore.getState().paletteOpen).toBe(true);
  expect(screen.getByText("Owner")).toBeInTheDocument();
```

and a third test:

```tsx
it("speaks Russian when the locale says so", () => {
  useStore.getState().setLocale("ru");
  render(<MemoryRouter><Sidebar /></MemoryRouter>);
  expect(screen.getByRole("button", { name: /Новый чат/ })).toBeInTheDocument();
  expect(screen.getByText("Недавние")).toBeInTheDocument();
  useStore.getState().setLocale("en");
});
```

`ConnectionBar.test.tsx` and `AskCard.test.tsx`: replace the Russian expectations with the English dictionary strings (`"Connection lost, reconnecting…"`, `"Could not load the thread. Reload the page."`, `"Chosen: B"`, `"Time to answer ran out"`) — open each test, keep its structure, swap the strings.

- [ ] **Step 2: Run them to see the failures**

Run: `npx vitest run --dir src src/components/cladex`
Expected: the rewritten tests fail on English strings.

- [ ] **Step 3: Move the strings**

Each component gets `const t = useT();` and reads its strings:

- `Sidebar.tsx`: `aria-label={t("sidebar.searchChats")}`, `t("sidebar.newChat")`, `t("sidebar.scheduled")`, `t("sidebar.recent")`, `t("sidebar.showMore")`.
- `ThreadRow.tsx`: `CHANNEL` becomes a function of `t`: `const channel = t.channel === "telegram" ? { icon: Send, label: tt("sidebar.channelTelegram") } : t.channel === "cron" ? { icon: Clock, label: tt("sidebar.channelCron") } : undefined;` (name the hook result `tt` here because `t` is the thread prop); `aria-label={tt("sidebar.agentWorking")}`, `tt("common.actions")`, `window.prompt(tt("common.title"), …)`, `tt("common.rename")`, `tt("common.archive")`; the title through `threadTitle(t, locale)` with `const locale = useLocale();`.
- `lib/titles.ts`:

```ts
import { currentLocale, t, type Locale } from "@/i18n";
export function threadTitle(th: Pick<ThreadWire, "title" | "channel">, locale: Locale = currentLocale()): string {
  if (th.title) return th.title;
  return t(th.channel === "telegram" ? "sidebar.telegramChat" : th.channel === "cron" ? "sidebar.routineChat" : "sidebar.untitled", {}, locale);
}
```

- `CommandPalette.tsx`: `title={t("palette.title")}`, `description={t("palette.description")}`, `placeholder={t("palette.placeholder")}`, `t("common.nothingFound")`, `heading={t("palette.actions")}`, the two items `t("sidebar.newChat")` / `t("sidebar.scheduled")` (keep `value="new chat Новый чат"` and `value="scheduled Запланировано routines"` so search matches in both languages), `heading={t("palette.chats")}`, `threadTitle(th)`.
- `ChatHeader.tsx`: `thread?.title ?? t("chat.newChat")`, `t("chat.files", { n: files.length })`.
- `EmptyState.tsx`: `t("chat.welcome")`, `t("chat.welcomeLead")`, `t("sidebar.newChat")`.
- `ConnectionBar.tsx`: `t("chat.loadFailed")`, `t("chat.reconnecting")`.
- `AskCard.tsx`: `t("ask.chosen", { answer: result.answer })`, `t("ask.timedOut")`, `t("ask.cancelled")`.

- [ ] **Step 4: Run the tests and the typecheck**

Run: `npx vitest run --dir src && npx tsc --noEmit -p tsconfig.app.json && npx eslint src`
Expected: green.

- [ ] **Step 5: Commit**

```bash
git add web/src/components/cladex web/src/lib/titles.ts
git commit -m "feat(web): sidebar, palette, chat chrome and ask cards read the dictionary"
```

---

### Task 5: The thread element and the file panel

**Files:**
- Modify: `web/src/components/assistant-ui/elements/thread.aui.tsx`, `web/src/components/cladex/FilePanel.tsx`, `FileCard.tsx`, `FilePreview.tsx`
- Test: `web/src/components/cladex/FilePreview.test.tsx`, `web/e2e/smoke.spec.ts` (placeholder only; the rest of the smoke moves in Task 7)

**Interfaces:**
- Reuses: the `Thread` element's `placeholder` prop (`thread.aui.tsx:139`), `TooltipIconButton`, `sonner` `toast`.
- Consumes: `useT` from Task 1.

- [ ] **Step 1: Update the tests**

`FilePreview.test.tsx`: swap Russian strings for `"No preview for this file type."`, `"Download a.pdf"`-style names (`/Download/`), `"Loading…"`.
`e2e/smoke.spec.ts` line 7: `page.getByPlaceholder("Message CladeX…")`.

- [ ] **Step 2: Run to see the failures**

Run: `npx vitest run --dir src src/components/cladex/FilePreview.test.tsx`
Expected: FAIL on English strings.

- [ ] **Step 3: Move the strings**

`thread.aui.tsx`: every component in the file that renders copy gets `const t = useT();` (they all render inside the store's React tree). Replace, by line from the inventory: `Загружаю переписку` → `t("chat.loadingThread")`; the `placeholder` default becomes `placeholder` prop with fallback `t("chat.placeholder")` computed inside the component (a default parameter cannot call a hook: use `const ph = placeholder ?? t("chat.placeholder")`); hint → `t("chat.hint")`; `tooltip="Вниз"` → `t("chat.scrollDown")`; welcome `Чем помочь?` → `t("chat.welcome")`; `aria-label` of the input → `t("chat.input")`; dictation tooltips/labels → `chat.dictate`, `chat.startDictation`, `chat.stop`, `chat.stopDictation`; send → `chat.send`, `chat.sendMessage`; stop run → `chat.stop`; `Повторить` → `chat.retry`; `Отчёт ·` → `t("chat.report")`; `aria-label="Ассистент работает"` → `chat.assistantWorking`; action-bar tooltips → `common.copy`, `chat.again`, `chat.readAloud`, `chat.stopReading`, `chat.more`, `chat.downloadMarkdown`; branch picker → `chat.previous`, `chat.next`.

`FilePanel.tsx`: `toast(t("common.copied"))`, `toast(t("common.copyFailed"))`, `aria-label={t("common.close")}`, `t("common.download")`, `t("common.copy")`, `t("files.sentToTelegram")`.

`FileCard.tsx`: `fmt` takes `t`: `const fmt = (n: number, t: ReturnType<typeof useT>) => n < 1024 ? t("files.bytes", { n }) : n < 1048576 ? t("files.kb", { n: (n / 1024).toFixed(1) }) : t("files.mb", { n: (n / 1048576).toFixed(1) });`

`FilePreview.tsx`: `done(t("files.loadFailed"))` (compute the string before the fetch, `t` is a hook result), `t("common.loading")`, `t("files.noPreview")`, `t("files.downloadNamed", { name: file.name })`.

- [ ] **Step 4: Run the tests, the typecheck and the smoke**

Run: `npx vitest run --dir src && npx tsc --noEmit -p tsconfig.app.json && npx playwright test`
Expected: vitest and tsc green; the smoke fails only on its second assertion (`Работал…`), which Task 7 rewrites.

- [ ] **Step 5: Commit**

```bash
git add web/src/components/assistant-ui/elements/thread.aui.tsx web/src/components/cladex/FilePanel.tsx web/src/components/cladex/FileCard.tsx web/src/components/cladex/FilePreview.tsx web/src/components/cladex/FilePreview.test.tsx web/e2e/smoke.spec.ts
git commit -m "feat(web): thread element and file panel read the dictionary"
```

---

### Task 6: Routines pages, form, header, templates

**Files:**
- Modify: `web/src/pages/Routines.tsx`, `web/src/pages/Routine.tsx`, `web/src/components/cladex/RoutineForm.tsx`, `RoutineHeader.tsx`, `RunStrip.tsx`, `routineTemplates.ts`
- Test: `web/src/components/cladex/RoutineForm.test.tsx`

**Interfaces:**
- Reuses: `PRESETS`, `presetLabel`, `describeSchedule` (Task 2); `RoutineForm`'s `RoutineInput`; `CHAT_DRAFT` export in `Routines.tsx`.
- Consumes: `useT`, `t`, `currentLocale`, `Key`.
- Produces: `TEMPLATES` entries carry `key: "briefing" | …` and resolve `name`/`description`/`prompt` through `t` at render time; `CHAT_DRAFT` becomes `chatDraft(locale?)`.

- [ ] **Step 1: Rewrite the form test to English**

`RoutineForm.test.tsx`: labels `/Name/`, `/Instructions/`, `"Timeout, seconds"`; combobox name `"Frequency"`; text `"Weekdays at 08:00"`; `"Custom schedule…"`; raw field label `"Schedule"`; button `"Save"`.

- [ ] **Step 2: Run it to see it fail**

Run: `npx vitest run --dir src src/components/cladex/RoutineForm.test.tsx`
Expected: FAIL on English labels.

- [ ] **Step 3: Move the strings**

`RoutineForm.tsx`: `const t = useT();`; labels `t("routines.form.name")`, `t("routines.form.instructions")`, `t("routines.form.frequency")`, `t("routines.form.custom")`, `t("routines.form.schedule")`, `t("routines.form.hint")`, `t("routines.form.timeout")`; placeholders `t("routines.form.namePlaceholder")`, `t("routines.form.instructionsPlaceholder")`; preset items `{presetLabel(p, locale)}` with `const locale = useLocale();`; buttons `t("common.cancel")`, `t("common.save")`; error fallback `t("common.error")`.

`routineTemplates.ts`:

```ts
import type { Key } from "@/i18n";
export type TemplateKey = "briefing" | "inbox" | "meetings" | "weekly" | "ideas" | "monitor";
export type RoutineTemplate = { key: TemplateKey; icon: LucideIcon; schedule: string };
export const TEMPLATES: RoutineTemplate[] = [
  { key: "briefing", icon: Sunrise, schedule: "0 8 * * 1-5" },
  { key: "inbox", icon: Inbox, schedule: "0 8 * * 1-5" },
  { key: "meetings", icon: CalendarDays, schedule: "0 8 * * 1-5" },
  { key: "weekly", icon: ListChecks, schedule: "0 16 * * 5" },
  { key: "ideas", icon: Lightbulb, schedule: "0 9 * * 1" },
  { key: "monitor", icon: Binoculars, schedule: "0 9 * * *" },
];
export const templateKey = (k: TemplateKey, field: "name" | "description" | "prompt") => `routines.templates.${k}.${field}` as Key;
```

`Routines.tsx`: `SORTS` labels become keys (`routines.sortNext`, `routines.sortName`) resolved with `t` at render; `CHAT_DRAFT` → `export const chatDraft = (locale = currentLocale()) => t("routines.chatDraft", {}, locale);`; the `inChat` handler passes `chatDraft()`; `fromTemplate` builds `{ name: t(templateKey(k, "name")), prompt: t(templateKey(k, "prompt")), schedule }`; page copy through `routines.title`, `routines.subtitle`, `routines.find`, `routines.searchTasks`, `routines.sort`, `routines.newTask`, `routines.createInChat`, `routines.setUpManually`, `routines.empty`, `routines.paused`/`routines.finished`, `routines.next` with `{ when: fmtDateTime(r.next_run_at) }`, `common.nothingFound`, `routines.dialogNew`; `a.name.localeCompare(b.name, locale)`.

`Routine.tsx`: `t("routines.notFound")`, `t("common.loading")`, `placeholder={t("chat.replyIn", { name: r.name })}`.

`RoutineHeader.tsx`: state badge `routines.active`/`routines.paused`/`routines.finished`; `describeSchedule(r.schedule)` instead of the raw string after the clock; `t("routines.nextRunAt", { when })`; `t("routines.timeout", { n })`; `toast(t("routines.queued"))`; `routines.runNow`, `routines.pause`, `routines.resume`, `common.edit`, `common.delete`, `window.confirm(t("routines.deleteConfirm", { name: r.name }))`, `routines.editRoutine`.

`RunStrip.tsx`: `t("routines.lastRuns")`.

- [ ] **Step 4: Run the tests and the typecheck**

Run: `npx vitest run --dir src && npx tsc --noEmit -p tsconfig.app.json && npx eslint src`
Expected: green.

- [ ] **Step 5: Commit**

```bash
git add web/src/pages web/src/components/cladex
git commit -m "feat(web): routines pages, form and templates read the dictionary"
```

---

### Task 7: Login page, the smoke test, and the no-stray-Cyrillic guard

**Files:**
- Modify: `web/src/pages/Login.tsx`, `web/e2e/smoke.spec.ts`, `web/e2e/fake-server.ts`
- Test: `web/src/i18n/i18n.test.ts` (extended), `web/src/pages/Login.test.tsx`

**Interfaces:**
- Reuses: `Login.tsx`'s error state, the fake API's `oneToolTurn`.
- Consumes: `useT`.

- [ ] **Step 1: Extend the parity test with a source scan**

Append to `web/src/i18n/i18n.test.ts`:

```ts
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";

const SRC = join(__dirname, "..");
const ALLOWED = [join("i18n", "ru.ts"), join("toolkit", "labels.ts"), join("components", "cladex", "UserMenu.tsx")];
function walk(dir: string): string[] {
  return readdirSync(dir).flatMap((f) => { const p = join(dir, f); return statSync(p).isDirectory() ? walk(p) : [p]; });
}

it("keeps every Russian string in the dictionary", () => {
  const offenders = walk(SRC)
    .filter((p) => /\.(ts|tsx)$/.test(p) && !/\.test\.tsx?$/.test(p) && !ALLOWED.some((a) => p.endsWith(a)))
    .filter((p) => /[А-Яа-яЁё]/.test(readFileSync(p, "utf8").replace(/\/\*[\s\S]*?\*\/|\/\/.*$/gm, "")));   // comments may explain in any language
  expect(offenders.map((p) => p.slice(SRC.length + 1))).toEqual([]);
});
```

- [ ] **Step 2: Run it to see it fail**

Run: `npx vitest run --dir src src/i18n/i18n.test.ts`
Expected: FAIL listing `pages/Login.tsx` (and anything a previous task missed — fix those too).

- [ ] **Step 3: Move the login strings, update the smoke**

`Login.tsx`: `const t = useT();` — `setError(t("login.failed"))`, `setError(t("login.noBot"))`, the lead paragraph `t("login.lead")`. `Login.test.tsx` keeps its assertions (they test roles, not copy).

`e2e/smoke.spec.ts`: `getByPlaceholder("Message CladeX…")`, `page.getByText("Ответ готов.")` stays (agent output is data), the group label becomes `page.getByText(/^Worked (\d+ s|1 step)$/)`.

`e2e/fake-server.ts`: leave the agent's reply text as it is; rename nothing else (Russian in the fake reply is content, not UI, and the file is outside `src/`).

- [ ] **Step 4: Run everything**

Run: `npx vitest run --dir src && npx tsc --noEmit -p tsconfig.app.json && npx eslint src && npx playwright test`
Expected: all green. Then start the local container (`docker compose up -d --build agent` from the repo root), open `http://localhost:8080`, confirm the sidebar is in English, switch to Русский from the footer menu, reload and confirm it stuck.

- [ ] **Step 5: Commit**

```bash
git add web/src/pages/Login.tsx web/src/i18n/i18n.test.ts web/e2e/smoke.spec.ts
git commit -m "feat(web): login page reads the dictionary; guard against stray Russian strings"
```

---

### Task 8: Documentation and wiki

**Files:**
- Modify: `docs/llm-wiki/components/web.md` (a short "Interface language" paragraph: the dictionary, the store field, the fallback rule for tool labels), `README.md` if it describes the sidebar footer or theme toggle.

- [ ] **Step 1: Write the paragraph and refresh the fingerprint**

Run from the repo root: `wsl.exe -d Ubuntu -- bash -lc 'cd /mnt/d/projects/claude-hermes && ./.llm-wiki/llm-wiki fingerprint -json -page docs/llm-wiki/components/web.md'` and copy `base_commit` and `evidence_fingerprint` into the page's frontmatter; then `./.llm-wiki/llm-wiki validate`.
Expected: `"errors": null`.

- [ ] **Step 2: Commit**

```bash
git add docs/llm-wiki README.md
git commit -m "docs(wiki): interface language and the user menu"
```

## Open Questions

1. **Should `Русский` and `English` be shown in their own language regardless of the current locale?** Options: self-named / translated. **Default: self-named** — a person who cannot read the current language must still find their own.
2. **Should the routine templates' prompts follow the interface language?** Options: yes / always English. **Default: yes** — the prompt goes to the agent as the person's own words, and they wrote them in the language they read the page in.
