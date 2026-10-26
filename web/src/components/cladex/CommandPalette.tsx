/** ⌘K / Ctrl+K palette (also the sidebar's search button): new chat, the calendar and its tasks, and every thread by title. */

import { useEffect } from "react";
import { useNavigate } from "react-router";
import { BookOpen, CalendarDays, Clock, MessageSquare, Plus, Puzzle, SlidersHorizontal } from "lucide-react";
import {
  CommandDialog, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList, CommandSeparator, CommandShortcut,
} from "@/components/ui/command";
import { t as translate, useLocale, useT, type Key } from "@/i18n";
import { threadTitle } from "@/lib/titles";
import { useStore } from "@/store";

/** An item is searchable in either language, whichever one the interface shows. */
const searchable = (key: Key) => `${translate(key, {}, "en")} ${translate(key, {}, "ru")}`;

export function CommandPalette() {
  const t = useT();
  const locale = useLocale();
  const open = useStore((s) => s.paletteOpen);
  const setOpen = useStore((s) => s.setPaletteOpen);
  const list = useStore((s) => s.list);
  const create = useStore((s) => s.create);
  const navigate = useNavigate();

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key.toLowerCase() === "k" && (e.metaKey || e.ctrlKey)) {
        e.preventDefault();
        setOpen(!useStore.getState().paletteOpen);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [setOpen]);

  const go = (run: () => void | Promise<void>) => { setOpen(false); void run(); };

  return (
    <CommandDialog open={open} onOpenChange={setOpen} title={t("palette.title")} description={t("palette.description")}>
      <CommandInput placeholder={t("palette.placeholder")} />
      <CommandList>
        <CommandEmpty>{t("common.nothingFound")}</CommandEmpty>
        <CommandGroup heading={t("palette.actions")}>
          <CommandItem value={searchable("sidebar.newChat")} onSelect={() => go(async () => navigate(`/t/${encodeURIComponent(await create())}`))}>
            <Plus /> {t("sidebar.newChat")} <CommandShortcut>⌘K</CommandShortcut>
          </CommandItem>
          {/* "scheduled" and "routines" stay searchable: the page was called that
              before it became the calendar, and hands remember old words. */}
          <CommandItem value={`${searchable("sidebar.calendar")} content plan posts`} onSelect={() => go(() => navigate("/calendar"))}>
            <CalendarDays /> {t("sidebar.calendar")}
          </CommandItem>
          <CommandItem value={`${searchable("sidebar.tasks")} scheduled routines`} onSelect={() => go(() => navigate("/calendar/tasks"))}>
            <Clock /> {t("sidebar.tasks")}
          </CommandItem>
          <CommandItem value={`${searchable("customize.title")} connectors mcp`} onSelect={() => go(() => navigate("/customize"))}>
            <SlidersHorizontal className="size-4" /> {t("customize.title")}
          </CommandItem>
          {/* The two deep tabs get their own entries: someone hunting for a plugin
              types "plugin", not "customize", and the tab is a plain URL. */}
          <CommandItem value={`${searchable("customize.plugins")} customize`} onSelect={() => go(() => navigate("/customize/plugins"))}>
            <Puzzle className="size-4" /> {t("customize.plugins")}
          </CommandItem>
          <CommandItem value={`${searchable("customize.skills")} customize`} onSelect={() => go(() => navigate("/customize/skills"))}>
            <BookOpen className="size-4" /> {t("customize.skills")}
          </CommandItem>
        </CommandGroup>
        {list.length > 0 && (
          <>
            <CommandSeparator />
            <CommandGroup heading={t("palette.chats")}>
              {list.map((th) => (
                <CommandItem key={th.id} value={`${threadTitle(th, locale)} ${th.id}`}
                  onSelect={() => go(() => navigate(`/t/${encodeURIComponent(th.id)}`))}>
                  <MessageSquare /> {threadTitle(th, locale)}
                </CommandItem>
              ))}
            </CommandGroup>
          </>
        )}
      </CommandList>
    </CommandDialog>
  );
}
