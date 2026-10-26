/** Store slice: theme, interface language, the open file panel and the signed-in user. */

import type { StateCreator } from "zustand";
import { api } from "@/api/client";
import type { CommandWire, FileWire, Me } from "@/api/types";
import type { Locale } from "@/i18n";          // type-only import: no cycle at runtime

export type Theme = "light" | "dark" | "system";
const KEY = "cladex.theme";
const LOCALE_KEY = "cladex.locale";

export type UiSlice = {
  theme: Theme; setTheme: (t: Theme) => void;
  locale: Locale; setLocale: (l: Locale) => void;
  /** The file in the panel, and the thread it was opened from — the panel's
   *  file switcher lists that thread's files. Switching files within the panel
   *  passes no thread and keeps the one it has. */
  openFile: FileWire | null; openFileThread: string | null;
  setOpenFile: (f: FileWire | null, threadId?: string | null) => void;
  me: Me | null; setMe: (m: Me | null) => void;
  paletteOpen: boolean; setPaletteOpen: (v: boolean) => void;   // the ⌘K palette, also opened from the sidebar
  settingsOpen: boolean; setSettingsOpen: (v: boolean) => void; // the Settings dialog, opened from the user menu
  sidebarOpen: boolean; setSidebarOpen: (v: boolean) => void;   // the sidebar as a drawer, below `md`
  /** The model picker's open state, kept here rather than in the pill because a
   *  `/model` draft has to open it from the composer: the CLI would run that text
   *  as its own local command, so the composer opens the picker instead of
   *  sending it. */
  modelPickerOpen: boolean; setModelPickerOpen: (v: boolean) => void;
  /** Whether the chain of thought of one message is open, when the reader has said
   *  so. Keyed by message id, not by step group: a turn writes as many groups as
   *  it has text blocks between its tools, and closing the thoughts means closing
   *  the thoughts — not the one box that happened to be under the cursor. The
   *  `running` the choice was made under is kept with it, so a choice made mid-turn
   *  lapses when the turn ends and the group collapses on its own. */
  steps: Record<string, { open: boolean; running: boolean }>;
  setSteps: (messageId: string, choice: { open: boolean; running: boolean }) => void;
  /** What can follow a `/` in the composer (GET /api/commands), fetched once on
   *  the first slash. A failed fetch settles as an empty list: the menu has
   *  nothing to show, and typing was never waiting on it. */
  commands: CommandWire[]; commandsStatus: "idle" | "loading" | "ready";
  loadCommands: () => Promise<void>;
};

function readTheme(): Theme {
  try { const v = localStorage.getItem(KEY); if (v === "light" || v === "dark" || v === "system") return v; } catch { /* private mode */ }
  return "system";
}

function readLocale(): Locale {
  try { const v = localStorage.getItem(LOCALE_KEY); if (v === "en" || v === "ru") return v; } catch { /* private mode */ }
  return "en";
}

export function applyTheme(t: Theme) {
  const dark = t === "dark" || (t === "system" && window.matchMedia?.("(prefers-color-scheme: dark)").matches);
  document.documentElement.classList.toggle("dark", dark);
}

export function applyLocale(l: Locale) { document.documentElement.lang = l; }

export const createUiSlice: StateCreator<UiSlice, [], [], UiSlice> = (set, get) => ({
  theme: readTheme(),
  setTheme(t) { try { localStorage.setItem(KEY, t); } catch { /* ignore */ } applyTheme(t); set({ theme: t }); },
  locale: readLocale(),
  setLocale(l) { try { localStorage.setItem(LOCALE_KEY, l); } catch { /* ignore */ } applyLocale(l); set({ locale: l }); },
  steps: {},
  setSteps(messageId, choice) { set((st) => ({ steps: { ...st.steps, [messageId]: choice } })); },
  commands: [], commandsStatus: "idle",
  async loadCommands() {
    if (get().commandsStatus !== "idle") return;
    set({ commandsStatus: "loading" });
    try { set({ commands: (await api.commands.list()).commands, commandsStatus: "ready" }); }
    catch { set({ commandsStatus: "ready" }); }
  },
  openFile: null, openFileThread: null,
  setOpenFile: (f, threadId) => set((st) => ({
    openFile: f, openFileThread: f ? (threadId === undefined ? st.openFileThread : threadId) : null,
  })),
  me: null, setMe: (m) => set({ me: m }),
  paletteOpen: false, setPaletteOpen: (v) => set({ paletteOpen: v }),
  settingsOpen: false, setSettingsOpen: (v) => set({ settingsOpen: v }),
  sidebarOpen: false, setSidebarOpen: (v) => set({ sidebarOpen: v }),
  modelPickerOpen: false, setModelPickerOpen: (v) => set({ modelPickerOpen: v }),
});
