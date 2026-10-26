/** The `/` menu of the composer. Typing `/` at the start of the draft lists what
 * GET /api/commands returns — the CLI's own commands, the skills, the plugins —
 * narrowed by every keystroke; picking one leaves `/name ` in the composer, and
 * the message goes to the server as typed, so the CLI does the dispatch. The
 * list is fetched on the first `/` and cached in the store; a failed fetch just
 * leaves the menu empty — typing never waits on it. */

import { useAui, useAuiState } from "@assistant-ui/react";
import type { KeyboardEvent, ReactNode } from "react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ComposerCommandItem, ComposerMenu, slashQuery, useSlashMatches, type ComposerCommand,
} from "@/components/assistant-ui/elements/composer-slash-commands";
import { useT } from "@/i18n";
import { isModelCommand } from "@/lib/modelCommand";
import { useStore } from "@/store";

/** The CLI's own local commands for the model: offering them here would offer a
 *  text that never becomes a turn. The picker is the way to change the pair. */
const NOT_OFFERED = new Set(["model", "effort"]);

export function useSlashCommands(): {
  menu: ReactNode;
  onKeyDown: (e: KeyboardEvent<HTMLTextAreaElement>) => void;
} {
  const t = useT();
  const aui = useAui();
  const text = useAuiState((s) => s.composer.text);
  const wire = useStore((s) => s.commands);
  const loadCommands = useStore((s) => s.loadCommands);
  const setModelPickerOpen = useStore((s) => s.setModelPickerOpen);
  const query = slashQuery(text);
  const typing = query !== null;
  useEffect(() => { if (typing) void loadCommands(); }, [typing, loadCommands]);

  const commands = useMemo<ComposerCommand[]>(
    () => wire.filter((c) => !NOT_OFFERED.has(c.name))
      .map((c) => ({ name: c.name, description: c.description || t(`chat.commandSource.${c.source}`) })),
    [wire, t],
  );
  const matches = useSlashMatches(text, commands);
  // The highlighted row, remembered together with the query it was chosen under:
  // a new query starts again at the top without an effect having to reset it.
  const [sel, setSel] = useState<{ query: string | null; index: number }>({ query: null, index: 0 });
  const index = sel.query === query ? Math.min(sel.index, Math.max(matches.length - 1, 0)) : 0;
  const [dismissed, setDismissed] = useState<string | null>(null);   // the draft Escape closed the menu on
  const open = matches.length > 0 && dismissed !== text;
  const listRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    listRef.current?.querySelector<HTMLElement>("[data-active]")?.scrollIntoView({ block: "nearest" });
  }, [index, open]);

  const pick = useCallback((c: ComposerCommand) => { aui.composer.setText(`/${c.name} `); }, [aui]);

  const onKeyDown = useCallback((e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.nativeEvent.isComposing) return;
    // `/model` and `/effort` run as the CLI's own local commands, so they never
    // become a turn — the server answers `400 model_command`. Enter opens the
    // picker instead and leaves the draft alone, so nothing typed is lost.
    // `/models` is an ordinary command and is sent as written.
    if (e.key === "Enter" && !e.shiftKey && isModelCommand(text)) {
      e.preventDefault(); setModelPickerOpen(true); return;
    }
    if (!open) return;
    const current = matches[index];
    switch (e.key) {
      case "ArrowDown": e.preventDefault(); setSel({ query, index: (index + 1) % matches.length }); return;
      case "ArrowUp": e.preventDefault(); setSel({ query, index: (index - 1 + matches.length) % matches.length }); return;
      case "Escape": e.preventDefault(); setDismissed(text); return;
      case "Tab": e.preventDefault(); if (current) pick(current); return;
      case "Enter":
        if (e.shiftKey || !current) return;
        if (query !== null && current.name.toLowerCase() === query.toLowerCase()) return;   // typed out in full: Enter sends it
        e.preventDefault(); pick(current); return;
    }
  }, [open, matches, index, query, text, pick, setModelPickerOpen]);

  const menu = (
    <ComposerMenu ref={listRef} open={open} aria-label={t("chat.commands")}>
      {matches.map((c, i) => (
        <ComposerCommandItem
          key={c.name} command={c} active={i === index}
          onMouseDown={(e) => e.preventDefault()}   // the textarea keeps its focus
          onClick={() => pick(c)}
        />
      ))}
    </ComposerMenu>
  );
  return { menu, onKeyDown };
}
