/** `/memory` — what the agent has written down about you, as a graph.
 *
 * Read-only on purpose: `agent_home/wiki` is the one tree whose every byte the
 * model owns, and a browser that could edit it would be a second author. So this
 * page draws and opens; it never writes.
 *
 * The selected page lives in the URL (`/memory?page=user/nutrition`) rather than
 * in component state, which is what makes a wikilink inside the sheet, the back
 * button and a pasted link all the same gesture.
 *
 * Freshness is level 1: the graph is fetched on mount and again whenever the tab
 * becomes visible. The wiki changes at the pace of a conversation, not a token
 * stream, and an SSE channel for it would cost a subscription to save a reload.
 */

import { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "react-router";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api } from "@/api/client";
import type { WikiGraphWire, WikiPageDetail } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { EmptyState } from "@/components/cladex/EmptyState";
import { MemoryGraph } from "@/components/cladex/MemoryGraph";
import { useLocale, useT } from "@/i18n";
import { fmtDateTime } from "@/lib/dates";
import { MD_BODY, linkifyWikilinks, stripFrontmatter, wikiHref } from "@/lib/markdown";

/** The dotted ground under the graph. A token, not a hard-coded grey: the page has
 * to read the same in both themes and `--border` already moves with them. */
const GRID = {
  backgroundImage: "radial-gradient(var(--border) 1px, transparent 1px)",
  backgroundSize: "22px 22px",
} as const;

export function Memory() {
  const t = useT();
  const locale = useLocale();
  const [params, setParams] = useSearchParams();
  const path = params.get("page");
  const [graph, setGraph] = useState<WikiGraphWire | null>(null);
  const [failed, setFailed] = useState(false);
  // The loaded page carries the path it belongs to, so walking to another page
  // drops it without a setState in an effect body — `detail` and `notFound` are
  // derived, and a stale reply for the previous path can never be shown.
  const [loaded, setLoaded] = useState<{ path: string; detail: WikiPageDetail | null } | null>(null);
  const current = loaded?.path === path ? loaded : null;
  const detail = current?.detail ?? null;
  const notFound = current !== null && current.detail === null;

  const load = useCallback(() => {
    api.wiki.graph().then((g) => { setGraph(g); setFailed(false); }, () => setFailed(true));
  }, []);

  useEffect(() => {
    load();
    const onVisible = () => { if (document.visibilityState === "visible") load(); };
    document.addEventListener("visibilitychange", onVisible);
    return () => document.removeEventListener("visibilitychange", onVisible);
  }, [load]);

  // The URL is the input: opening `?page=…` cold and clicking a node take the same
  // path through here, so there is one place that can be wrong.
  useEffect(() => {
    if (!path) return;
    let live = true;
    api.wiki.page(path).then(
      (d) => { if (live) setLoaded({ path, detail: d }); },
      () => { if (live) setLoaded({ path, detail: null }); },
    );
    return () => { live = false; };
  }, [path]);

  const select = (next: string) =>
    setParams((p) => { const q = new URLSearchParams(p); q.set("page", next); return q; });
  const close = () =>
    setParams((p) => { const q = new URLSearchParams(p); q.delete("page"); return q; });

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <header className="px-4 pb-2 pt-5 sm:px-8">
        <h1 className="text-2xl font-medium">{t("memory.title")}</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          {t("memory.subtitle")}
          {graph && graph.pages.length > 0 && <> · {t("memory.pages", { n: graph.pages.length })}</>}
        </p>
      </header>

      {/* The graph owns every pixel below the header: `relative` here and
          `absolute inset-0` on the graph, so the SVG is as big as the page and the
          brain is not a picture in a box. */}
      <div className="relative min-h-0 flex-1" style={GRID}>
        {failed && <p role="alert" className="p-6 text-sm text-muted-foreground">{t("memory.loadFailed")}</p>}
        {!failed && !graph && <p className="p-6 text-sm text-muted-foreground">{t("memory.loading")}</p>}
        {graph && graph.pages.length === 0 && <EmptyState title={t("memory.title")} lead={t("memory.empty")} />}
        {graph && graph.pages.length > 0 && (
          <MemoryGraph graph={graph} selected={path} onSelect={select} className="absolute inset-0" />
        )}
      </div>

      <Sheet open={path !== null} onOpenChange={(open) => { if (!open) close(); }}>
        <SheetContent side="right" className="w-full gap-0 overflow-y-auto p-0 sm:max-w-xl">
          <SheetHeader className="border-b border-border px-5 pb-4 pt-5">
            <SheetTitle className="pr-8 text-lg">{detail?.title ?? path ?? ""}</SheetTitle>
            <SheetDescription>{detail?.summary || t("memory.noSummary")}</SheetDescription>
            <div className="mt-1 flex flex-wrap items-center gap-1.5">
              <code className="rounded-md bg-muted px-1.5 py-0.5 text-xs">{path}</code>
              {detail?.tags.map((tag) => (
                <Badge key={tag} variant="secondary" className="px-1.5 py-0 text-[10px]">{tag}</Badge>
              ))}
              {detail?.updated && (
                <span className="text-xs text-muted-foreground">{t("memory.updated", { when: fmtDateTime(detail.updated, locale) })}</span>
              )}
              {detail && !detail.in_index && (
                <Badge variant="outline" className="px-1.5 py-0 text-[10px] text-warning-foreground">{t("memory.notInIndex")}</Badge>
              )}
            </div>
          </SheetHeader>
          <div className={`px-5 py-5 ${MD_BODY}`}>
            {notFound && <p className="text-sm text-muted-foreground">{t("memory.notFound")}</p>}
            {!notFound && !detail && <p className="text-sm text-muted-foreground">{t("common.loading")}</p>}
            {detail && (stripFrontmatter(detail.content).trim() === ""
              ? <p className="text-sm text-muted-foreground">{t("memory.noContent")}</p>
              : (
                <ReactMarkdown
                  remarkPlugins={[remarkGfm]}
                  components={{
                    // `[[a/b]]` was rewritten to `#wiki:a/b` before rendering; the click
                    // selects that node instead of navigating, so the graph follows the
                    // reader through the wiki the way the agent linked it.
                    a: ({ href, children }) => {
                      const target = wikiHref(href);
                      return target
                        ? <a href={href} onClick={(e) => { e.preventDefault(); select(target); }}>{children}</a>
                        : <a href={href} target="_blank" rel="noreferrer">{children}</a>;
                    },
                  }}
                >
                  {linkifyWikilinks(stripFrontmatter(detail.content))}
                </ReactMarkdown>
              ))}
          </div>
        </SheetContent>
      </Sheet>
    </div>
  );
}
