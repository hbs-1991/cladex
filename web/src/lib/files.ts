/** What a delivered file is, as the chat card and the file panel describe it: a
 *  kind for the label and the icon, the extension shown beside the name, and
 *  whether its bytes are text the panel can render and copy. */

import { toast } from "sonner";
import type { FileWire } from "@/api/types";
import { t as translate } from "@/i18n";

export type FileKind = "document" | "code" | "image" | "spreadsheet" | "archive" | "file";

const DOCUMENT = new Set(["md", "markdown", "txt", "rst", "pdf", "doc", "docx", "rtf", "odt"]);
const SHEET = new Set(["csv", "tsv", "xls", "xlsx", "ods"]);
const ARCHIVE = new Set(["zip", "tar", "gz", "tgz", "bz2", "xz", "7z", "rar"]);

/** The lowercase extension, or "" for a name without one (`Dockerfile`). */
export function extOf(name: string): string {
  const dot = name.lastIndexOf(".");
  return dot > 0 ? name.slice(dot + 1).toLowerCase() : "";
}

export function isTextFile(f: Pick<FileWire, "media_type">): boolean {
  return f.media_type.startsWith("text/") || f.media_type === "application/json";
}

export function isMarkdown(f: Pick<FileWire, "media_type" | "name">): boolean {
  return f.media_type === "text/markdown" || ["md", "markdown"].includes(extOf(f.name));
}

export function kindOf(f: Pick<FileWire, "media_type" | "name">): FileKind {
  const ext = extOf(f.name);
  if (f.media_type.startsWith("image/")) return "image";
  if (SHEET.has(ext)) return "spreadsheet";
  if (ARCHIVE.has(ext)) return "archive";
  if (DOCUMENT.has(ext) || f.media_type === "application/pdf" || f.media_type === "text/markdown") return "document";
  if (isTextFile(f)) return "code";
  return "file";
}

/** "MD", "PY", "PDF" — what follows the kind in "Document · MD". */
export function extLabel(f: Pick<FileWire, "name">): string {
  return extOf(f.name).toUpperCase();
}

/** One entry per file id, in the order the thread delivered them: a file sent
 *  twice (a retry, a re-send) is still one file to choose from. */
export function uniqueFiles(files: readonly FileWire[]): FileWire[] {
  const seen = new Map<string, FileWire>();
  for (const f of files) seen.set(f.file_id, f);
  return [...seen.values()];
}

/** Copies a text file's bytes, fetched fresh: neither the card nor the panel's
 *  header holds them. */
export async function copyFileText(file: Pick<FileWire, "url">, t: typeof translate = translate) {
  try {
    const text = await (await fetch(file.url, { credentials: "same-origin" })).text();
    await navigator.clipboard.writeText(text);
    toast(t("common.copied"));
  } catch { toast(t("common.copyFailed")); }
}

/** A file size in the interface language. */
export const fmtSize = (n: number, t: typeof translate = translate) =>
  n < 1024 ? t("files.bytes", { n }) : n < 1048576 ? t("files.kb", { n: (n / 1024).toFixed(1) }) : t("files.mb", { n: (n / 1048576).toFixed(1) });

/** "Document · MD", or just "Document" for a name with no extension. */
export function fileKindLabel(f: Pick<FileWire, "media_type" | "name">, t: typeof translate = translate): string {
  const ext = extLabel(f);
  const kind = t(`files.kind.${kindOf(f)}`);
  return ext ? `${kind} · ${ext}` : kind;
}
