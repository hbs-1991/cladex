import { ChevronDown, Copy, Download, Eye } from "lucide-react";
import type { FileWire } from "@/api/types";
import { ImageFilename, ImagePreview, ImageRoot, ImageZoom } from "@/components/assistant-ui/elements/image";
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { useT } from "@/i18n";
import { copyFileText, fileKindLabel, isTextFile } from "@/lib/files";
import { cn } from "@/lib/utils";
import { useThreadId } from "@/runtime/thread-id";
import { useStore } from "@/store";
import { FileGlyph } from "./FileParts";

const SEGMENT = "inline-flex h-8 items-center gap-1.5 text-sm font-medium hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";

export function FileCard({ args }: { args: FileWire }) {
  const t = useT();
  const threadId = useThreadId();
  const setOpenFile = useStore((s) => s.setOpenFile);
  const openId = useStore((s) => s.openFile?.file_id);
  // A picture the agent drew is shown, not filed away: click to zoom, the name
  // underneath. Everything else stays a card that opens in the file panel.
  if (args.media_type.startsWith("image/")) {
    return (
      <ImageRoot className="my-2" size="default">
        <ImageZoom src={args.url} alt={args.name}>
          <ImagePreview src={args.url} alt={args.name} />
        </ImageZoom>
        <ImageFilename>{args.name}</ImageFilename>
      </ImageRoot>
    );
  }
  const open = () => setOpenFile(args, threadId);
  const active = openId === args.file_id;
  return (
    <div data-slot="file-card" data-active={active || undefined}
      className={cn("my-2 flex w-full max-w-md items-center gap-3 rounded-xl border bg-card p-2 pe-2.5 transition-colors",
        "hover:border-foreground/20 data-[active]:border-foreground/25")}>
      {/* The whole left side opens the panel; the name is the button's accessible name. */}
      <button type="button" onClick={open} className="flex min-w-0 flex-1 items-center gap-3 rounded-lg text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
        <span className="grid size-11 shrink-0 place-items-center rounded-lg border bg-muted/60 sm:size-12">
          <FileGlyph file={args} className="size-5" />
        </span>
        <span className="min-w-0 flex-1">
          <span className="block truncate text-sm font-medium">{args.name}</span>
          <span className="block truncate text-xs text-muted-foreground">{fileKindLabel(args, t)}</span>
        </span>
      </button>
      <div className="inline-flex shrink-0 overflow-hidden rounded-md border bg-background">
        <a href={args.url} download={args.name} aria-label={t("files.downloadNamed", { name: args.name })}
          className={cn(SEGMENT, "px-2.5 sm:px-3")}>
          <Download className="size-4 sm:hidden" aria-hidden />
          <span className="max-sm:sr-only">{t("common.download")}</span>
        </a>
        <DropdownMenu>
          <DropdownMenuTrigger aria-label={t("files.moreActions")} className={cn(SEGMENT, "border-s px-1.5")}>
            <ChevronDown className="size-4 text-muted-foreground" />
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="min-w-40">
            <DropdownMenuItem onSelect={open}><Eye className="size-4" /> {t("files.open")}</DropdownMenuItem>
            {isTextFile(args) && (
              <DropdownMenuItem onSelect={() => void copyFileText(args, t)}><Copy className="size-4" /> {t("common.copy")}</DropdownMenuItem>
            )}
            <DropdownMenuItem asChild>
              <a href={args.url} download={args.name}><Download className="size-4" /> {t("common.download")}</a>
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </div>
  );
}
