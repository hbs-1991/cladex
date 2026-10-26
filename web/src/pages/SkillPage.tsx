/** `/customize/skills/:name` — one skill in full: the rendered SKILL.md, what
 * the sidecar knows about it, the other files in its directory, and the owner's
 * actions. Editing swaps the rendered body for the raw text; the save carries
 * the etag it was read with, so a curator or review pass that rewrote the file
 * underneath fails the save loudly instead of silently winning it. */

import { useEffect, useState } from "react";
import { ArrowLeft, MoreVertical, Pin } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Link, useParams } from "react-router";
import { toast } from "sonner";
import { ApiError } from "@/api/client";
import type { SkillWire } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { ConfirmDialog } from "@/components/cladex/AskDialog";
import { useLocale, useT, type Key } from "@/i18n";
import { fmtDateTime } from "@/lib/dates";
import { MD_BODY, stripFrontmatter } from "@/lib/markdown";
import { useStore } from "@/store";

const LIST = "/customize/skills";

const SOURCE: Record<SkillWire["source"], Key> = { bundled: "skills.bundled", agent: "skills.fromAgent", user: "skills.yours" };
const STATE: Record<SkillWire["state"], Key> = { active: "skills.active", stale: "skills.stale", archived: "skills.archived" };

/** The codes the skills routes answer with; `stale_edit` is handled on the page
 * itself, so it never reaches this. */
const KNOWN = ["not_found", "forbidden", "pinned", "restore_failed", "too_large", "bad_frontmatter"] as const;
const known = (c: string): c is (typeof KNOWN)[number] => (KNOWN as readonly string[]).includes(c);
const errorText = (t: ReturnType<typeof useT>, e: unknown) =>
  e instanceof ApiError && known(e.code) ? t(`skills.errors.${e.code}`) : e instanceof Error ? e.message : t("common.error");

/** A file size in the interface language; `t` is passed in because this is not a component. */
const fmtBytes = (n: number, t: ReturnType<typeof useT>) =>
  n < 1024 ? t("files.bytes", { n }) : n < 1048576 ? t("files.kb", { n: (n / 1024).toFixed(1) }) : t("files.mb", { n: (n / 1048576).toFixed(1) });

export function SkillPage() {
  const t = useT();
  const locale = useLocale();
  const s = useStore();
  const { name = "" } = useParams();
  // Since Wave 4b this is the caller's OWN library (the API scopes it per
  // user), so a plain user edits and archives here too.
  const canEdit = Boolean(s.me);
  const detail = s.skillDetail[name];
  // The list row is what pin/archive/restore write back, so it is the fresher of
  // the two; the detail stands in when the page was opened by its own URL.
  const w: SkillWire | undefined = s.skills.find((x) => x.name === name) ?? detail;
  // Both pieces of per-skill state carry the name they belong to, so walking to
  // another skill drops them without a setState in the effect body.
  const [failed, setFailed] = useState<string | null>(null);           // the name that answered not_found
  const [edit, setEdit] = useState<{ name: string; draft: string; conflict: boolean } | null>(null);
  const [busy, setBusy] = useState(false);
  const [archiving, setArchiving] = useState(false);
  const editing = edit?.name === name ? edit : null;

  useEffect(() => {
    if (!s.skillsLoaded) void s.loadSkills().catch(() => undefined);   // the badges and the menu read the row
    void s.loadSkill(name).catch((e) => {
      if (e instanceof ApiError && e.code === "not_found") setFailed(name);
      else toast.error(errorText(t, e));
    });
  }, [name]); // eslint-disable-line react-hooks/exhaustive-deps

  const run = (fn: () => Promise<void>, ok: Key) => void (async () => {
    try { await fn(); toast(t(ok, { name })); } catch (e) { toast.error(errorText(t, e)); }
  })();

  const save = async () => {
    if (!editing || !detail) return;
    setBusy(true);
    try {
      await s.saveSkill(name, editing.draft, detail.etag);
      setEdit(null);
      toast(t("skills.savedToast", { name }));
    } catch (e) {
      // The one error the page keeps on screen: the buffer is still the owner's
      // work, and throwing it away is the failure the etag exists to prevent.
      if (e instanceof ApiError && e.code === "stale_edit") setEdit({ name, draft: editing.draft, conflict: true });
      else toast.error(errorText(t, e));
    } finally { setBusy(false); }
  };
  const reload = async () => {
    try { setEdit({ name, draft: (await s.loadSkill(name)).content, conflict: false }); }
    catch (e) { toast.error(errorText(t, e)); }
  };

  const back = <Link to={LIST} className="inline-flex items-center gap-1 rounded-md bg-muted px-2.5 py-1.5 text-sm hover:bg-accent"><ArrowLeft className="size-4" /> {t("customize.skills")}</Link>;
  if (!detail || !w) {
    return (
      <div>
        {back}
        <p className="mt-8 text-sm text-muted-foreground">{failed === name ? t("skills.notFound") : t("common.loading")}</p>
      </div>
    );
  }
  const archived = w.state === "archived";

  return (
    <div>
      <div className="mb-8">{back}</div>

      <header className="flex flex-wrap items-start gap-4">
        <div className="min-w-0 flex-1">
          <h2 className="text-2xl font-medium">{w.title || w.name}</h2>
          <p className="mt-1 text-muted-foreground">{w.description || t("skills.noDescription")}</p>
          <div className="mt-3 flex flex-wrap items-center gap-1.5">
            <Badge variant="secondary" className="px-1.5 py-0 text-[10px]">{t(SOURCE[w.source])}</Badge>
            {w.pinned && <Badge variant="outline" className="px-1.5 py-0 text-[10px]"><Pin className="size-2.5" /> {t("skills.pinned")}</Badge>}
            {w.state !== "active" && <Badge variant="outline" className="px-1.5 py-0 text-[10px] text-muted-foreground">{t(STATE[w.state])}</Badge>}
            <code className="rounded-md bg-muted px-1.5 py-0.5 text-xs">{w.name}</code>
          </div>
        </div>
        {canEdit && (
          <div className="flex shrink-0 items-center gap-2">
            {/* Only the live directory is writable: an archived SKILL.md is restored first. */}
            {!editing && !archived && <Button onClick={() => setEdit({ name, draft: detail.content, conflict: false })}>{t("skills.edit")}</Button>}
            <DropdownMenu>
              <DropdownMenuTrigger asChild><Button variant="ghost" size="icon" aria-label={t("common.actions")}><MoreVertical className="size-4" /></Button></DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                <DropdownMenuItem onSelect={() => (w.pinned ? run(() => s.unpinSkill(name), "skills.unpinnedToast") : run(() => s.pinSkill(name), "skills.pinnedToast"))}>
                  {t(w.pinned ? "skills.unpin" : "skills.pin")}
                </DropdownMenuItem>
                {archived
                  ? <DropdownMenuItem onSelect={() => run(() => s.restoreSkill(name), "skills.restoredToast")}>{t("skills.restore")}</DropdownMenuItem>
                  : !w.pinned && <DropdownMenuItem onSelect={() => setArchiving(true)}>{t("skills.archive")}</DropdownMenuItem>}
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        )}
      </header>

      {w.broken && (
        <p role="alert" className="mt-8 rounded-xl border border-error-foreground/30 bg-error p-4 text-sm text-error-foreground">{t("skills.brokenLead")}</p>
      )}

      {!editing ? (
        <div className={`mt-10 max-w-3xl ${MD_BODY}`}>
          {detail.content.trim() === ""
            ? <p className="text-sm text-muted-foreground">{t("skills.noContent")}</p>
            : <ReactMarkdown remarkPlugins={[remarkGfm]}>{stripFrontmatter(detail.content)}</ReactMarkdown>}
        </div>
      ) : (
        <div className="mt-10 max-w-3xl">
          {editing.conflict && (
            <div role="alert" className="mb-3 rounded-xl border border-warning-foreground/30 bg-warning p-4 text-sm text-warning-foreground">
              <p className="font-medium">{t("skills.conflict")}</p>
              <p className="mt-1">{t("skills.conflictLead")}</p>
              <Button variant="secondary" size="sm" className="mt-3" onClick={() => void reload()}>{t("skills.reload")}</Button>
            </div>
          )}
          <Label htmlFor="skill-md">{t("skills.content")}</Label>
          <Textarea id="skill-md" value={editing.draft} rows={24} spellCheck={false} autoFocus
            className="mt-1.5 font-mono text-xs"
            onChange={(e) => setEdit({ name, draft: e.target.value, conflict: editing.conflict })}
            onKeyDown={(e) => { if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") { e.preventDefault(); void save(); } }} />
          <div className="mt-3 flex items-center gap-2">
            <Button disabled={busy} onClick={() => void save()}>{busy ? t("skills.saving") : t("skills.save")}</Button>
            <Button variant="secondary" onClick={() => setEdit(null)}>{t("skills.cancel")}</Button>
            <span className="text-xs text-muted-foreground">{t("skills.saveHint")}</span>
          </div>
        </div>
      )}

      <h3 className="mt-10 text-lg font-medium">{t("skills.otherFiles")}</h3>
      {detail.tree.length > 0 ? (
        <ul className="mt-3 grid gap-2 sm:grid-cols-2">
          {detail.tree.map((f) => (
            <li key={f.path} className="flex items-center justify-between gap-3 text-sm">
              <code className="truncate rounded-md bg-muted px-2 py-1 text-xs">{f.path}</code>
              <span className="shrink-0 text-xs text-muted-foreground">{fmtBytes(f.bytes, t)}</span>
            </li>
          ))}
        </ul>
      ) : <p className="mt-3 text-sm text-muted-foreground">{t("skills.noOtherFiles")}</p>}
      <p className="mt-3 text-xs text-muted-foreground">{t("skills.readOnlyNote")}</p>

      <dl className="mt-10 grid gap-x-8 gap-y-6 border-t border-border pt-8 text-sm sm:grid-cols-3">
        {([
          ["skills.source", t(SOURCE[w.source])],
          ["skills.state", t(STATE[w.state])],
          ["skills.used", w.use_count > 0 ? t("skills.usedTimes", { n: w.use_count }) : t("skills.neverUsed")],
          ["skills.lastUsedLabel", w.last_used ? fmtDateTime(w.last_used, locale) : t("skills.never")],
          ["skills.patched", t("skills.patchedTimes", { n: w.patch_count })],
          ["skills.lastPatched", w.last_patched ? fmtDateTime(w.last_patched, locale) : t("skills.never")],
          ["skills.created", w.created_at ? fmtDateTime(w.created_at, locale) : "—"],
          ["skills.files", t("skills.filesCount", { n: w.files })],
          ["skills.size", fmtBytes(w.bytes, t)],
        ] as [Key, string][]).map(([k, v]) => (
          <div key={k}>
            <dt className="text-xs uppercase tracking-wide text-muted-foreground">{t(k)}</dt>
            <dd className="mt-1">{v}</dd>
          </div>
        ))}
      </dl>

      {w.pinned && <p className="mt-6 text-xs text-muted-foreground">{t("skills.pinHint")}</p>}

      <ConfirmDialog open={archiving} onOpenChange={setArchiving} title={t("skills.archive")}
        description={t("skills.archiveConfirm", { name })} confirmLabel={t("skills.archive")}
        onConfirm={() => run(() => s.archiveSkill(name), "skills.archivedToast")} />
    </div>
  );
}
