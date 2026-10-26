"use client";

// The standalone lane of assistant-ui's "Slash commands" element
// (https://www.assistant-ui.com/elements/composer-slash-commands): useSlashMatches
// filters a command list against the draft, ComposerMenu floats the matches above
// the composer and ComposerCommandItem is one row. Kept upstream-shaped — the
// runtime wiring (draft text, insertion, keys) lives in components/cladex/SlashCommands.tsx.

import type { LucideIcon } from "lucide-react";
import type { ComponentProps, FC } from "react";
import { useMemo } from "react";
import { field, floating } from "@/components/assistant-ui/elements/surfaces";
import { cn } from "@/lib/utils";

export type ComposerCommand = {
  name: string;
  description?: string;
  icon?: LucideIcon;
};

/** The text after the slash while a command is being typed; null once the draft
 *  does not start with `/`, or the command is already followed by arguments. */
export function slashQuery(value: string): string | null {
  if (!value.startsWith("/")) return null;
  const query = value.slice(1);
  return /\s/.test(query) ? null : query;
}

export function matchSlash(
  value: string,
  commands: readonly ComposerCommand[] | undefined,
): ComposerCommand[] {
  const query = slashQuery(value);
  if (query === null || !commands) return [];
  const q = query.toLowerCase();
  return commands.filter((c) => c.name.toLowerCase().startsWith(q));
}

export function useSlashMatches(
  value: string,
  commands: readonly ComposerCommand[] | undefined,
): ComposerCommand[] {
  return useMemo(() => matchSlash(value, commands), [value, commands]);
}

export const ComposerMenu: FC<ComponentProps<"div"> & { open: boolean }> = ({
  open,
  className,
  children,
  ...props
}) => (
  <div
    data-slot="composer-menu"
    data-open={open || undefined}
    role="listbox"
    aria-hidden={!open}
    className={cn(
      floating,
      "absolute bottom-full left-0 z-10 mb-2 flex max-h-64 w-full max-w-72 origin-bottom-left flex-col overflow-y-auto rounded-2xl p-1.5 shadow-lg transition-[opacity,scale] duration-150 ease-[cubic-bezier(0.23,1,0.32,1)] motion-reduce:transition-none",
      open ? "scale-100 opacity-100" : "pointer-events-none scale-[0.97] opacity-0",
      className,
    )}
    {...props}
  >
    {open ? children : null}
  </div>
);

export const ComposerCommandItem: FC<
  ComponentProps<"button"> & { command: ComposerCommand; active: boolean }
> = ({ command, active, className, ...props }) => {
  const Icon = command.icon;
  return (
    <button
      type="button"
      data-slot="composer-menu-item"
      data-active={active || undefined}
      role="option"
      aria-selected={active}
      className={cn(
        "flex w-full items-center gap-2.5 rounded-[10px] px-2.5 py-2 text-start outline-none transition-colors",
        active && field,
        className,
      )}
      {...props}
    >
      {Icon ? <Icon className="text-foreground/45 size-4 shrink-0" /> : null}
      <span className="flex min-w-0 flex-1 flex-col items-start gap-0.5">
        <span className="text-[13.5px] font-medium">/{command.name}</span>
        {command.description ? (
          <span className="text-foreground/45 line-clamp-1 text-xs">{command.description}</span>
        ) : null}
      </span>
      {active ? (
        <span className="text-foreground/35 text-xs" aria-hidden>
          ↵
        </span>
      ) : null}
    </button>
  );
};
