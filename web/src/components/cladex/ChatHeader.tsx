/** Thread header: title, channel badge and the way into the file panel — straight
 *  to the one file, or a list to choose from when the chat delivered several. */

import { useMemo } from "react";
import { ChevronDown, Paperclip } from "lucide-react";
import { DropdownMenu, DropdownMenuContent, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { useT } from "@/i18n";
import { uniqueFiles } from "@/lib/files";
import { useStore } from "@/store";
import type { FileWire, ThreadWire } from "@/api/types";
import { FileMenuItems } from "./FileParts";

const NO_FILES: FileWire[] = [];
const TRIGGER = "ml-auto inline-flex shrink-0 items-center gap-1 rounded-md px-2 py-1 text-xs text-muted-foreground hover:bg-accent hover:text-foreground";

export function ChatHeader({ thread }: { thread: ThreadWire | undefined }) {
  const t = useT();
  const delivered = useStore((s) => (thread ? s.folded(thread.id).files : NO_FILES));
  const files = useMemo(() => uniqueFiles(delivered), [delivered]);
  const openId = useStore((s) => s.openFile?.file_id);
  const setOpenFile = useStore((s) => s.setOpenFile);
  const open = (f: FileWire) => setOpenFile(f, thread?.id ?? null);
  const label = <><Paperclip className="size-3.5" aria-hidden /> {t("chat.files", { n: files.length })}</>;
  return (
    <header className="flex h-12 items-center gap-3 border-b px-4 md:h-16 md:px-6">
      <h1 className="min-w-0 truncate text-sm font-medium">{thread?.title ?? t("chat.newChat")}</h1>
      {thread?.channel === "telegram" && (
        <span className="shrink-0 rounded bg-muted px-1.5 text-[10px] text-muted-foreground">Telegram</span>
      )}
      {files.length === 1 && (
        <button type="button" className={TRIGGER} onClick={() => open(files[0]!)}>{label}</button>
      )}
      {files.length > 1 && (
        <DropdownMenu>
          <DropdownMenuTrigger className={TRIGGER}>
            {label} <ChevronDown className="size-3.5" aria-hidden />
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="max-h-[min(24rem,70dvh)] w-[min(22rem,calc(100vw-2rem))] overflow-y-auto">
            <FileMenuItems files={files} currentId={openId} onPick={open} />
          </DropdownMenuContent>
        </DropdownMenu>
      )}
    </header>
  );
}
