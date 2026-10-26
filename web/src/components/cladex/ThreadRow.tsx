/** One thread in the sidebar: the title, where it lives (a Telegram plane, a
 * clock for a routine's lane, nothing for the web), a dot while the agent is
 * working, and a rename/archive menu. The menu button takes the channel icon's
 * place on hover with a mouse and simply stands there on a touch screen, which
 * has no hover — on a phone the row was unmanageable without it. */

import { useState } from "react";
import { Link, useParams } from "react-router";
import { Clock, MoreHorizontal, Send, type LucideIcon } from "lucide-react";
import type { ThreadWire } from "@/api/types";
import { useLocale, useT } from "@/i18n";
import { threadTitle } from "@/lib/titles";
import { cn } from "@/lib/utils";
import { useStore } from "@/store";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { ConfirmDialog, PromptDialog } from "./AskDialog";

export function ThreadRow({ t }: { t: ThreadWire }) {
  const tt = useT();
  const locale = useLocale();
  const { id } = useParams();
  const rename = useStore((s) => s.rename);
  const archive = useStore((s) => s.archive);
  const [renaming, setRenaming] = useState(false);
  const [archiving, setArchiving] = useState(false);
  const title = threadTitle(t, locale);
  const active = id !== undefined && decodeURIComponent(id) === t.id;
  const channel: { icon: LucideIcon; label: string } | undefined =
    t.channel === "telegram" ? { icon: Send, label: tt("sidebar.channelTelegram") } :
    t.channel === "cron" ? { icon: Clock, label: tt("sidebar.channelCron") } : undefined;
  return (
    <div className={cn("group flex items-center rounded-md pr-1 text-sm text-sidebar-foreground hover:bg-accent hover:text-accent-foreground",
      active && "bg-sidebar-accent font-medium text-sidebar-accent-foreground hover:bg-sidebar-accent hover:text-sidebar-accent-foreground")}>
      <Link to={`/t/${encodeURIComponent(t.id)}`} className="flex min-w-0 flex-1 items-center gap-2 py-1.5 pl-2.5 pr-1">
        <span className="truncate">{title}</span>
        <span className="ml-auto flex shrink-0 items-center gap-1.5 text-muted-foreground">
          {channel && (
            <channel.icon role="img" aria-label={channel.label} className="size-3.5 md:group-hover:hidden md:group-has-data-[state=open]:hidden" strokeWidth={1.75} />
          )}
          {t.running && <span aria-label={tt("sidebar.agentWorking")} className="size-2 shrink-0 rounded-full bg-signal" />}
        </span>
      </Link>
      <DropdownMenu>
        <DropdownMenuTrigger aria-label={tt("common.actions")}
          className="inline-flex size-6 shrink-0 items-center justify-center rounded text-muted-foreground hover:text-foreground max-md:size-11 md:hidden md:group-hover:inline-flex md:data-[state=open]:inline-flex">
          <MoreHorizontal className="size-4" />
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end">
          <DropdownMenuItem onSelect={() => setRenaming(true)}>{tt("common.rename")}</DropdownMenuItem>
          <DropdownMenuItem onSelect={() => setArchiving(true)}>{tt("common.archive")}</DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
      <PromptDialog open={renaming} onOpenChange={setRenaming} title={tt("common.rename")}
        label={tt("common.title")} initial={t.title ?? ""} submitLabel={tt("common.save")}
        onSubmit={(v) => void rename(t.id, v)} />
      <ConfirmDialog open={archiving} onOpenChange={setArchiving} title={tt("common.archive")}
        description={tt("sidebar.archiveConfirm", { name: title })} confirmLabel={tt("common.archive")}
        onConfirm={() => void archive(t.id)} />
    </div>
  );
}
