/** The pieces the chat's file card, the thread header and the file panel share:
 *  the glyph for a file's kind, its "Document · MD" line, and the menu rows that
 *  let a reader pick one file out of a chat that delivered several. */

import { Archive, Check, FileCode2, FileImage, FileSpreadsheet, FileText, File as FileIcon, type LucideIcon } from "lucide-react";
import type { FileWire } from "@/api/types";
import { DropdownMenuItem, DropdownMenuLabel } from "@/components/ui/dropdown-menu";
import { useT } from "@/i18n";
import { fileKindLabel, fmtSize, kindOf, type FileKind } from "@/lib/files";
import { cn } from "@/lib/utils";

const GLYPH: Record<FileKind, LucideIcon> = {
  document: FileText, code: FileCode2, image: FileImage, spreadsheet: FileSpreadsheet, archive: Archive, file: FileIcon,
};

export function FileGlyph({ file, className }: { file: Pick<FileWire, "media_type" | "name">; className?: string }) {
  const Icon = GLYPH[kindOf(file)];
  return <Icon className={cn("size-4 shrink-0 text-muted-foreground", className)} strokeWidth={1.75} aria-hidden />;
}

/** The rows of a file chooser: one per file, the open one checked. */
export function FileMenuItems({ files, currentId, onPick }: {
  files: readonly FileWire[]; currentId: string | undefined; onPick: (f: FileWire) => void;
}) {
  const t = useT();
  return (
    <>
      <DropdownMenuLabel className="text-xs font-normal text-muted-foreground">
        {t("files.inThisChat")} · {files.length}
      </DropdownMenuLabel>
      {files.map((f) => (
        <DropdownMenuItem key={f.file_id} onSelect={() => onPick(f)} className="gap-3 py-2"
          data-current={f.file_id === currentId || undefined}>
          <span className="grid size-8 shrink-0 place-items-center rounded-md border bg-muted/50"><FileGlyph file={f} /></span>
          <span className="min-w-0 flex-1">
            <span className="block truncate text-sm">{f.name}</span>
            <span className="block truncate text-xs text-muted-foreground">{fileKindLabel(f, t)} · {fmtSize(f.size, t)}</span>
          </span>
          <Check className={cn("size-4 shrink-0", f.file_id === currentId ? "opacity-100" : "opacity-0")} aria-hidden />
        </DropdownMenuItem>
      ))}
    </>
  );
}
