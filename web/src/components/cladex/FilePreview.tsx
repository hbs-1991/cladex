/** Preview for one file in the file panel; the renderer is chosen by `media_type`,
 *  and a Markdown file can also be read as its source. */

import { useEffect, useState } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import type { FileWire } from "@/api/types";
import { SyntaxHighlighter } from "@/components/assistant-ui/elements/shiki-highlighter";
import { Button } from "@/components/ui/button";
import { t as translate, useT } from "@/i18n";
import { isMarkdown, isTextFile } from "@/lib/files";
import { stripFrontmatter } from "@/lib/markdown";

/**
 * DESIGN.md §3 "Assistant markdown rules", applied without `@tailwindcss/typography`:
 * headings 24/20/18/16 at weight 600 with 32px before and 12px after, 16/26 paragraphs,
 * 24px list indent, muted inline code, scrollable fenced code and tables, underlined
 * signal-colored links, a 2px rule on blockquotes.
 */
const MD_BODY = [
  "text-base leading-[1.625rem] break-words",
  "[&>*:first-child]:mt-0 [&>*:last-child]:mb-0",
  "[&_h1]:mt-8 [&_h1]:mb-3 [&_h1]:text-2xl [&_h1]:font-semibold",
  "[&_h2]:mt-8 [&_h2]:mb-3 [&_h2]:text-xl [&_h2]:font-semibold",
  "[&_h3]:mt-8 [&_h3]:mb-3 [&_h3]:text-lg [&_h3]:font-semibold",
  "[&_h4]:mt-8 [&_h4]:mb-3 [&_h4]:text-base [&_h4]:font-semibold",
  "[&_h5]:mt-8 [&_h5]:mb-3 [&_h5]:text-base [&_h5]:font-semibold",
  "[&_h6]:mt-8 [&_h6]:mb-3 [&_h6]:text-base [&_h6]:font-semibold",
  "[&_p]:mb-4",
  "[&_ul]:mb-4 [&_ul]:list-disc [&_ul]:pl-6 [&_ol]:mb-4 [&_ol]:list-decimal [&_ol]:pl-6",
  "[&_li]:mb-1 [&_li>ul]:my-2 [&_li>ol]:my-2",
  "[&_code]:rounded-sm [&_code]:bg-muted [&_code]:px-1 [&_code]:py-0.5 [&_code]:font-mono [&_code]:text-code",
  "[&_pre]:mb-4 [&_pre]:overflow-x-auto [&_pre]:rounded-lg [&_pre]:bg-muted [&_pre]:p-4",
  "[&_pre_code]:bg-transparent [&_pre_code]:p-0",
  "[&_table]:w-full [&_table]:text-sm [&_table]:break-normal",
  "[&_th]:border-b [&_th]:px-3 [&_th]:py-2 [&_th]:text-left [&_th]:align-bottom [&_td]:border-b [&_td]:px-3 [&_td]:py-2 [&_td]:align-top",
  "[&_th]:min-w-24 [&_td]:min-w-24",
  "[&_a]:text-signal [&_a]:underline [&_a]:underline-offset-[3px]",
  "[&_blockquote]:mb-4 [&_blockquote]:border-l-2 [&_blockquote]:pl-4",
  "[&_img]:max-w-full [&_img]:rounded-md",
  "[&_hr]:my-8",
].join(" ");

/** A wide table scrolls inside its own box: on a phone the panel is the whole
 *  screen, and a table that widened the panel would drag every paragraph with it. */
const MD_COMPONENTS: Components = {
  table: ({ children, className, style }) => (
    <div data-slot="file-md-table" className="mb-4 overflow-x-auto"><table className={className} style={style}>{children}</table></div>
  ),
};

/** Shiki resolves the usual extensions itself (ts, py, sh, yml) and falls back to
 * plaintext for an id it does not know, so only the extensionless names need a map. */
const NAMED_LANG: Record<string, string> = { dockerfile: "docker", makefile: "make" };

function languageOf(name: string): string {
  const lower = name.toLowerCase();
  const dot = lower.lastIndexOf(".");
  return dot > 0 ? lower.slice(dot + 1) : NAMED_LANG[lower] ?? "plaintext";
}

/** The panel shows a file unframed, so the highlighter's code-card chrome comes back off. */
const CODE_BODY = "[&_pre]:rounded-none [&_pre]:border-0 [&_pre]:bg-transparent! [&_pre]:p-0 [&_pre]:whitespace-pre-wrap";

export type FileView = "preview" | "source";

export function FilePreview({ file, view = "preview" }: { file: FileWire; view?: FileView }) {
  const t = useT();
  // Keyed by url so a switch to another file shows the spinner without a setState inside the effect body.
  const [loaded, setLoaded] = useState<{ url: string; text: string } | null>(null);
  const isText = isTextFile(file);
  useEffect(() => {
    if (!isText) return;
    let cancelled = false;
    const done = (text: string) => { if (!cancelled) setLoaded({ url: file.url, text }); };
    fetch(file.url, { credentials: "same-origin" }).then((r) => r.text()).then(done).catch(() => done(translate("files.loadFailed")));   // the plain t: no hook result in the deps, so a language switch does not refetch
    return () => { cancelled = true; };
  }, [file.url, isText]);
  const text = loaded?.url === file.url ? loaded.text : null;

  if (file.media_type.startsWith("image/")) return <img src={file.url} alt={file.name} className="max-w-full rounded-md" />;
  if (file.media_type === "application/pdf") return <iframe title={file.name} src={file.url} className="h-full min-h-[70dvh] w-full rounded-md border" />;
  if (isText) {
    if (text === null) return <p className="text-sm text-muted-foreground">{t("common.loading")}</p>;
    if (isMarkdown(file) && view === "preview") {
      return <div className={MD_BODY}><ReactMarkdown remarkPlugins={[remarkGfm]} components={MD_COMPONENTS}>{stripFrontmatter(text)}</ReactMarkdown></div>;
    }
    return <SyntaxHighlighter className={CODE_BODY} language={isMarkdown(file) ? "markdown" : languageOf(file.name)} code={text} />;
  }
  return (
    <div className="flex flex-col items-start gap-3 text-sm text-muted-foreground">
      <p>{t("files.noPreview")}</p>
      <Button asChild variant="secondary"><a href={file.url} download={file.name}>{t("files.downloadNamed", { name: file.name })}</a></Button>
    </div>
  );
}
