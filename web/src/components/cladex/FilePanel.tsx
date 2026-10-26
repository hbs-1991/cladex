/**
 * The file panel, driven by store `openFile`. Docked beside the thread from `xl`,
 * an overlay over it from `md`, and the whole screen on a phone; "Expand" makes
 * it the whole screen anywhere.
 *
 * The header reads left to right as the document's own toolbar: a preview/source
 * toggle for Markdown, the file's name — a chooser over every file the chat has
 * delivered when there is more than one — then Copy (Download for a file with no
 * text) with the rest of the actions behind its chevron, Expand and Close.
 *
 * From `md` up the panel is resizable: its left edge is a separator the reader
 * drags (or moves with the arrow keys), double-click restores the default, and
 * the width is remembered per browser. It never gets narrower than 20rem, and
 * docked it always leaves the thread 20rem beside the sidebar.
 */

import { useCallback, useMemo, useState, type KeyboardEvent, type PointerEvent } from "react";
import { ChevronDown, Code2, Copy, Download, Eye, Maximize2, Minimize2, Send, X } from "lucide-react";
import type { FileWire } from "@/api/types";
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel, DropdownMenuSeparator, DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { useT } from "@/i18n";
import { copyFileText, extLabel, extOf, isMarkdown, isTextFile, uniqueFiles } from "@/lib/files";
import { cn } from "@/lib/utils";
import { useStore } from "@/store";
import { FileGlyph, FileMenuItems } from "./FileParts";
import { FilePreview, type FileView } from "./FilePreview";

const NO_FILES: FileWire[] = [];
const WIDTH_KEY = "cladex.filePanelWidth";
const MIN_WIDTH = 320;            // 20rem
const DOCKED_RESERVE = 256 + 320; // the sidebar and 20rem of thread, from `xl`
const OVERLAY_RESERVE = 48;       // a strip of the thread stays visible between `md` and `xl`
const STEP = 32;

function readWidth(): number | null {
  try { const v = Number(localStorage.getItem(WIDTH_KEY)); return Number.isFinite(v) && v >= MIN_WIDTH ? v : null; }
  catch { return null; }   // private mode
}

/** The widest the panel may be right now: the window decides, not the saved value. */
function maxWidth(): number {
  const docked = window.matchMedia?.("(min-width: 1280px)").matches ?? false;
  return Math.max(MIN_WIDTH, window.innerWidth - (docked ? DOCKED_RESERVE : OVERLAY_RESERVE));
}

const clampWidth = (w: number) => Math.round(Math.min(Math.max(w, MIN_WIDTH), maxWidth()));

/** The panel's width: null is the responsive default, a number is the reader's choice. */
function usePanelWidth() {
  const [width, setWidthState] = useState<number | null>(readWidth);
  const setWidth = useCallback((w: number | null) => {
    const next = w === null ? null : clampWidth(w);
    setWidthState(next);
    try { if (next === null) localStorage.removeItem(WIDTH_KEY); else localStorage.setItem(WIDTH_KEY, String(next)); }
    catch { /* private mode */ }
  }, []);
  return [width, setWidth] as const;
}
const ICON_BUTTON = "grid size-8 shrink-0 place-items-center rounded-md text-muted-foreground hover:bg-accent hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";
const SEGMENT = "inline-flex h-8 items-center gap-1.5 text-sm font-medium hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";

export function FilePanel() {
  const file = useStore((s) => s.openFile);
  const threadId = useStore((s) => s.openFileThread);
  const delivered = useStore((s) => (threadId ? s.folded(threadId).files : NO_FILES));
  const files = useMemo(() => uniqueFiles(delivered), [delivered]);
  const [expanded, setExpanded] = useState(false);
  const [width, setWidth] = usePanelWidth();
  const [resizing, setResizing] = useState(false);
  if (!file) return null;
  const close = () => { setExpanded(false); useStore.getState().setOpenFile(null); };
  return (
    <>
      {/* Between `md` and `xl` the panel covers the thread; a tap beside it closes it. */}
      {!expanded && <div aria-hidden onClick={close} className="fixed inset-0 z-[var(--cl-z-panel)] hidden bg-black/30 md:block xl:hidden" />}
      <aside role="complementary" aria-label={file.name} data-slot="file-panel" data-expanded={expanded || undefined}
        data-resizing={resizing || undefined}
        style={width ? { ["--file-panel-w" as string]: `${width}px` } : undefined}
        className={cn(
          "fixed inset-0 z-[var(--cl-z-panel)] flex flex-col bg-background pt-[env(safe-area-inset-top)] pb-[env(safe-area-inset-bottom)]",
          !expanded && [
            "md:left-auto md:w-[var(--file-panel-w,min(44rem,88vw))] md:max-w-[calc(100vw-3rem)] md:min-w-80 md:border-l md:shadow-xl",
            "xl:relative xl:inset-auto xl:z-auto xl:w-[var(--file-panel-w,clamp(28rem,42vw,56rem))] xl:max-w-[calc(100vw-var(--cl-sidebar-width)-20rem)] xl:shrink-0 xl:shadow-none xl:pt-0 xl:pb-0",
          ],
          // A drag that crosses a PDF's iframe must not lose the pointer to it, nor select text.
          "data-[resizing]:select-none data-[resizing]:[&_iframe]:pointer-events-none",
        )}>
        {!expanded && <ResizeHandle width={width} onResize={setWidth} onDrag={setResizing} />}
        {/* Keyed by file: a switch starts on the preview, not on the last file's source. */}
        <PanelBody key={file.file_id} file={file} files={files} expanded={expanded}
          onExpand={() => setExpanded((v) => !v)} onClose={close} />
      </aside>
    </>
  );
}

function PanelBody({ file, files, expanded, onExpand, onClose }: {
  file: FileWire; files: FileWire[]; expanded: boolean; onExpand: () => void; onClose: () => void;
}) {
  const t = useT();
  const [view, setView] = useState<FileView>("preview");
  const text = isTextFile(file);
  const wide = file.media_type === "application/pdf";
  return (
    <>
      <header className="flex h-14 shrink-0 items-center gap-1.5 border-b px-2 sm:gap-2 sm:px-3">
        {isMarkdown(file) && <ViewToggle view={view} onChange={setView} />}
        <FileSwitcher file={file} files={files} />
        <div className="ms-auto flex shrink-0 items-center gap-1">
          <div className="inline-flex overflow-hidden rounded-md border">
            {text ? (
              <button type="button" onClick={() => void copyFileText(file, t)} aria-label={t("common.copy")} className={cn(SEGMENT, "px-2.5")}>
                <Copy className="size-4" aria-hidden /><span className="max-sm:hidden">{t("common.copy")}</span>
              </button>
            ) : (
              <a href={file.url} download={file.name} aria-label={t("common.download")} className={cn(SEGMENT, "px-2.5")}>
                <Download className="size-4" aria-hidden /><span className="max-sm:hidden">{t("common.download")}</span>
              </a>
            )}
            <DropdownMenu>
              <DropdownMenuTrigger aria-label={t("files.moreActions")} className={cn(SEGMENT, "border-s px-1.5")}>
                <ChevronDown className="size-4 text-muted-foreground" />
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="min-w-44">
                {text && <DropdownMenuItem onSelect={() => void copyFileText(file, t)}><Copy className="size-4" /> {t("common.copy")}</DropdownMenuItem>}
                <DropdownMenuItem asChild>
                  <a href={file.url} download={file.name}><Download className="size-4" /> {t("common.download")}</a>
                </DropdownMenuItem>
                {file.telegram_sent && (
                  <>
                    <DropdownMenuSeparator />
                    <DropdownMenuLabel className="flex items-center gap-2 text-xs font-normal text-muted-foreground">
                      <Send className="size-3.5" /> {t("files.sentToTelegram")}
                    </DropdownMenuLabel>
                  </>
                )}
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
          {/* A phone already gives the panel the whole screen. */}
          <button type="button" onClick={onExpand} aria-label={expanded ? t("files.collapse") : t("files.expand")}
            title={expanded ? t("files.collapse") : t("files.expand")} className={cn(ICON_BUTTON, "max-md:hidden")}>
            {expanded ? <Minimize2 className="size-4" /> : <Maximize2 className="size-4" />}
          </button>
          <button type="button" onClick={onClose} aria-label={t("common.close")} title={t("common.close")} className={ICON_BUTTON}>
            <X className="size-4" />
          </button>
        </div>
      </header>
      <div className="min-h-0 flex-1 overflow-auto" data-slot="file-panel-body">
        <div className={cn("mx-auto w-full", wide ? "h-full p-3" : "max-w-3xl px-5 py-6 sm:px-8 sm:py-8")}>
          <FilePreview file={file} view={view} />
        </div>
      </div>
    </>
  );
}

/**
 * The panel's left edge as a vertical separator. Dragging sets the width from the
 * pointer's distance to the panel's right edge; pointer capture keeps the drag
 * alive when it crosses the thread or a PDF. Arrows move it by 32px (left widens,
 * since the panel grows leftwards), Home/End go to the extremes, double-click and
 * Enter restore the default. Hidden on a phone, where the panel is the screen.
 */
function ResizeHandle({ width, onResize, onDrag }: {
  width: number | null; onResize: (w: number | null) => void; onDrag: (v: boolean) => void;
}) {
  const t = useT();
  const panel = (el: HTMLElement) => el.parentElement?.getBoundingClientRect();
  const down = (e: PointerEvent<HTMLDivElement>) => {
    if (e.button !== 0) return;
    e.preventDefault();
    e.currentTarget.setPointerCapture(e.pointerId);
    onDrag(true);
  };
  const move = (e: PointerEvent<HTMLDivElement>) => {
    if (!e.currentTarget.hasPointerCapture(e.pointerId)) return;
    onResize((panel(e.currentTarget)?.right ?? window.innerWidth) - e.clientX);
  };
  const up = (e: PointerEvent<HTMLDivElement>) => {
    if (e.currentTarget.hasPointerCapture(e.pointerId)) e.currentTarget.releasePointerCapture(e.pointerId);
    onDrag(false);
  };
  const key = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key === "Enter") { e.preventDefault(); onResize(null); return; }
    const w = panel(e.currentTarget)?.width ?? width ?? MIN_WIDTH;
    const next = e.key === "ArrowLeft" ? w + STEP : e.key === "ArrowRight" ? w - STEP
      : e.key === "Home" ? maxWidth() : e.key === "End" ? MIN_WIDTH : undefined;
    if (next === undefined) return;
    e.preventDefault();
    onResize(next);
  };
  return (
    <div role="separator" aria-orientation="vertical" aria-label={t("files.resize")} title={t("files.resize")} tabIndex={0}
      aria-valuemin={MIN_WIDTH} aria-valuenow={width ?? undefined} data-slot="file-panel-resize"
      onPointerDown={down} onPointerMove={move} onPointerUp={up} onPointerCancel={up}
      onDoubleClick={() => onResize(null)} onKeyDown={key}
      className="group absolute inset-y-0 -left-1.5 z-10 hidden w-3 cursor-col-resize touch-none focus-visible:outline-none md:block">
      {/* The visible line: nothing over the border until hovered, dragged or focused. */}
      <span aria-hidden className="absolute inset-y-0 left-1/2 w-0.5 -translate-x-1/2 bg-transparent transition-colors group-hover:bg-ring group-focus-visible:bg-ring group-active:bg-ring" />
    </div>
  );
}

/** Preview and source, as two icons: the eye reads the file, the brackets show its text. */
function ViewToggle({ view, onChange }: { view: FileView; onChange: (v: FileView) => void }) {
  const t = useT();
  const item = (v: FileView, label: string, Icon: typeof Eye) => (
    <button type="button" aria-pressed={view === v} aria-label={label} title={label} onClick={() => onChange(v)}
      className={cn("grid size-7 place-items-center rounded-[5px] text-muted-foreground transition-colors hover:text-foreground",
        view === v && "bg-accent text-foreground")}>
      <Icon className="size-4" />
    </button>
  );
  return (
    <div role="group" aria-label={`${t("files.preview")} / ${t("files.source")}`} className="inline-flex shrink-0 gap-0.5 rounded-md border p-0.5">
      {item("preview", t("files.preview"), Eye)}
      {item("source", t("files.source"), Code2)}
    </div>
  );
}

/** The name, and — when the chat delivered more than one file — the chooser over all of them. */
function FileSwitcher({ file, files }: { file: FileWire; files: FileWire[] }) {
  const t = useT();
  const setOpenFile = useStore((s) => s.setOpenFile);
  const ext = extLabel(file);
  const base = ext ? file.name.slice(0, file.name.length - extOf(file.name).length - 1) : file.name;
  const title = (
    <>
      <FileGlyph file={file} className="max-sm:hidden" />
      <span className="min-w-0 truncate text-sm font-medium">{base}</span>
      {ext && <span className="shrink-0 text-xs text-muted-foreground max-[400px]:hidden">· {ext}</span>}
    </>
  );
  if (files.length < 2) return <div className="flex min-w-0 items-center gap-1.5 px-1.5">{title}</div>;
  const index = files.findIndex((f) => f.file_id === file.file_id);
  return (
    <DropdownMenu>
      <DropdownMenuTrigger aria-label={`${t("files.choose")}: ${file.name}`}
        className="flex min-w-0 items-center gap-1.5 rounded-md px-1.5 py-1 hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
        {title}
        <span className="shrink-0 rounded bg-muted px-1 font-mono text-[11px] text-muted-foreground tabular-nums max-sm:hidden">
          {index + 1}/{files.length}
        </span>
        <ChevronDown className="size-4 shrink-0 text-muted-foreground" />
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="max-h-[min(24rem,70dvh)] w-[min(22rem,calc(100vw-2rem))] overflow-y-auto">
        <FileMenuItems files={files} currentId={file.file_id} onPick={(f) => setOpenFile(f)} />
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

