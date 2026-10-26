/** One content-plan item: create it, edit it, and take the owner's decisions on it.
 *
 * The decisions are the point of the dialog. Approval is a human act — the
 * agent's tool can never reach `scheduled` — so "Approve" lives here and nowhere
 * else, and it binds exactly what the form shows: unsaved edits are saved first,
 * then the server hashes the media and records the approval. "Resolve" is the
 * only way out of `failed`, `stuck` and a `publishing` that never finished, and
 * it makes the owner say which way: post again, or "it did go out, here". The
 * system never re-posts on its own, so there is no third, automatic choice.
 *
 * Two approvals, one after the other. "Approve theme" on an `idea` only lets the
 * drafter routine write the post a day before its time (`planned`); it schedules
 * nothing. The drafted post comes back in `review`, and "Approve" — here, or the
 * button on its Telegram card — is still the only way to `scheduled`. While the
 * drafter holds the item (`drafting`) the form is read-only. "Approve" is greyed
 * out with the reason when the text is empty or the format's media are missing;
 * that copy of the rule is a courtesy, and the server's 400 stays the authority.
 *
 * What the server refuses is shown as the server words it (`ApiError.message`):
 * the content service writes those sentences for a person to read, and a second
 * dictionary here would only drift from them.
 *
 * Times are the device's: the field is a `datetime-local` and the value goes out
 * as UTC with its `Z`. The media are paths on the server, relative to the agent's
 * home; the previews stream from `/api/content/{id}/media/{n}` and never leave
 * the box. */

import { useState, type FormEvent } from "react";
import { Link } from "react-router";
import { toast } from "sonner";
import { api } from "@/api/client";
import type { ContentInput, ContentItemWire, ContentStatus } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetDescription, SheetFooter, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { useLocale, useT } from "@/i18n";
import {
  approvalRefusal, deviceZone, fromLocalInput, isLocked, isMissed, isVideo, itemLabel, toLocalInput,
} from "@/lib/calendar";
import { fmtDateTime } from "@/lib/dates";
import { cn } from "@/lib/utils";
import { useStore } from "@/store";
import { ConfirmDialog } from "../AskDialog";
import { AttentionBadge, StatusPill } from "./CalendarChip";
import { ChannelIcon } from "./ChannelIcon";
import { CHANNELS, channelInfo, isKnownFormat } from "./channels";

type Editable = "idea" | "draft" | "review";
const EDITABLE: readonly ContentStatus[] = ["idea", "draft", "review"];
const APPROVABLE: readonly ContentStatus[] = ["idea", "draft", "review", "failed"];
const RESOLVABLE: readonly ContentStatus[] = ["failed", "stuck", "publishing"];
/** A card that no longer approves anything, or never arrived: worth a warning tint. */
const CARD_TROUBLE: readonly ContentItemWire["card_state"][] = ["failed", "superseded"];

type Draft = {
  channel: string; format: string; title: string; body: string;
  media: string;          // one path per line, as typed
  publishAt: string;      // the datetime-local value, "" for none
  status: Editable;
};

function draftOf(item: ContentItemWire | null, publishAt: string | null): Draft {
  if (!item) {
    return { channel: CHANNELS[0], format: channelInfo(CHANNELS[0]).formats[0]!, title: "", body: "", media: "",
      publishAt: toLocalInput(publishAt), status: "draft" };
  }
  return {
    channel: item.channel, format: item.format, title: item.title, body: item.body,
    media: item.media.map((m) => m.path).join("\n"), publishAt: toLocalInput(item.publish_at),
    status: (EDITABLE.includes(item.status) ? item.status : "draft") as Editable,
  };
}

const paths = (text: string) => text.split("\n").map((p) => p.trim()).filter(Boolean);

/** The body of a create, or only what changed for a PATCH — so saving an
 *  approved item without touching its media does not make the server re-hash. */
function changes(item: ContentItemWire | null, d: Draft): Partial<ContentInput> {
  const next: ContentInput = {
    channel: d.channel, format: d.format, title: d.title, body: d.body, media: paths(d.media),
    publish_at: fromLocalInput(d.publishAt),
    ...(!item || EDITABLE.includes(item.status) ? { status: d.status } : {}),
  };
  if (!item) return next;
  const was = draftOf(item, null);
  const out: Partial<ContentInput> = {};
  if (d.channel !== was.channel) out.channel = next.channel;
  if (d.format !== was.format) out.format = next.format;
  if (d.title !== was.title) out.title = next.title;
  if (d.body !== was.body) out.body = next.body;
  if (paths(d.media).join("\n") !== paths(was.media).join("\n")) out.media = next.media;
  if (d.publishAt !== was.publishAt) out.publish_at = next.publish_at;
  if (next.status && d.status !== item.status) out.status = next.status;
  return out;
}

/** An `ApiError` carries the server's own sentence; anything else gets the generic word. */
const message = (e: unknown, fallback: string) => (e instanceof Error && e.message ? e.message : fallback);

type Props = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** `null` creates a new item. */
  item: ContentItemWire | null;
  /** Where a new item starts (an ISO instant), e.g. the day it was created from. */
  publishAt?: string | null;
};

export function ContentItemDialog({ open, onOpenChange, item, publishAt = null }: Props) {
  // The form lives in a child mounted with the dialog, so reopening shows what the
  // row says now, not what was typed and abandoned last time.
  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      {open && <ItemForm item={item} publishAt={publishAt} close={() => onOpenChange(false)} />}
    </Sheet>
  );
}

function ItemForm({ item, publishAt, close }: { item: ContentItemWire | null; publishAt: string | null; close: () => void }) {
  const t = useT();
  const locale = useLocale();
  const s = useStore();
  const [d, setD] = useState<Draft>(() => draftOf(item, publishAt));
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [resolving, setResolving] = useState(false);

  const locked = item !== null && isLocked(item);
  const diff = changes(item, d);
  const dirty = Object.keys(diff).length > 0;
  const info = channelInfo(d.channel);
  const channels = CHANNELS.includes(d.channel as (typeof CHANNELS)[number]) ? [...CHANNELS] : [...CHANNELS, d.channel];
  const formats = info.formats.includes(d.format) ? [...info.formats] : [...info.formats, d.format];
  const formatName = (f: string) => (isKnownFormat(f) ? t(`calendar.formats.${f}`) : f);
  const statusEditable = !item || EDITABLE.includes(item.status);
  const drafting = item?.status === "drafting";

  // The content approval: offered where the server accepts it, greyed out with
  // the reason when the form fails the body/media rule. An idea with nothing
  // written yet is headed for the drafter, so it is offered only the theme.
  const refusal = item && APPROVABLE.includes(item.status) ? approvalRefusal(d.format, d.body, paths(d.media)) : null;
  const refusalText = !refusal ? null
    : refusal.code === "empty_body" ? t("calendar.item.needBody") : t(`calendar.item.needMedia.${refusal.need}`);
  const canApprove = item !== null && APPROVABLE.includes(item.status) && !(item.status === "idea" && refusal);
  const canApproveTheme = item?.status === "idea";
  // A draft that failed was never approved: it is drafted again, not re-posted.
  const draftFailed = item?.status === "failed" && item.theme_approved_at !== null && item.approved_at === null;
  const canRetryDrafting = (item?.status === "planned" && item.error !== null) || draftFailed;
  const decides = canApprove || canApproveTheme || canRetryDrafting;

  /** One server call with the busy flag and the refusal shown verbatim. */
  const run = async (fn: () => Promise<unknown>, done?: string) => {
    setBusy(true); setError(null);
    try { await fn(); if (done) toast(done); close(); }
    catch (e) { setError(message(e, t("common.error"))); }
    finally { setBusy(false); }
  };

  const save = (e?: FormEvent) => {
    e?.preventDefault();
    if (!item) return run(() => s.createItem(diff as ContentInput), t("calendar.item.created"));
    return run(() => s.updateItem(item.id, diff), t("calendar.item.saved"));
  };
  const approve = (id: number) => run(async () => {
    if (dirty) await s.updateItem(id, diff);
    await s.approveItem(id);
  }, t("calendar.item.approved"));
  // The theme is what the form shows, so pending edits are saved first here too.
  const approveTheme = (id: number) => run(async () => {
    if (dirty) await s.updateItem(id, diff);
    await s.approveTheme(id);
  }, t("calendar.item.themeApproved"));
  const pickChannel = (channel: string) => setD((x) => {
    const fmts = channelInfo(channel).formats;
    return { ...x, channel, format: fmts.includes(x.format) ? x.format : fmts[0]! };
  });

  if (item && resolving) return <ResolveForm item={item} back={() => setResolving(false)} close={close} />;

  return (
    <SheetContent side="right" className="w-full gap-0 p-0 sm:max-w-2xl">
      <form onSubmit={save} className="flex h-full min-h-0 min-w-0 flex-col">
        <SheetHeader className="border-b border-border px-5 pb-4 pt-5 text-left">
          <SheetTitle className="flex min-w-0 items-center gap-2 pr-8 text-lg">
            <ChannelIcon channel={d.channel} size={18} />
            <span className="min-w-0 truncate">{item ? itemLabel(item, t("calendar.item.untitled")) : t("calendar.item.newTitle")}</span>
          </SheetTitle>
          <SheetDescription>{t("calendar.item.lead")}</SheetDescription>
        </SheetHeader>

        <div className="grid min-h-0 min-w-0 flex-1 content-start gap-4 overflow-y-auto px-5 py-4">

        {item && (
          <div className="flex min-w-0 flex-wrap items-center gap-2 text-xs text-muted-foreground">
            <StatusPill status={item.status} />
            {item.needs_attention && <AttentionBadge />}
            {item.approved_at && item.status === "scheduled" && <span>{t("calendar.item.approvedAt", { when: fmtDateTime(item.approved_at, locale) })}</span>}
            {item.theme_approved_at && (item.status === "planned" || item.status === "drafting") && (
              <span>{t("calendar.item.themeApprovedAt", { when: fmtDateTime(item.theme_approved_at, locale) })}</span>
            )}
            {item.attempts > 0 && <span>{t("calendar.item.attempts", { n: item.attempts })}</span>}
            {item.draft_attempts > 0 && <span>{t("calendar.item.draftAttempts", { n: item.draft_attempts })}</span>}
            {item.redrafts > 0 && <span>{t("calendar.item.redrafts", { n: item.redrafts })}</span>}
            {item.card_state !== "none" && (
              <span className={cn(CARD_TROUBLE.includes(item.card_state) && "text-warning-foreground")}>
                {t("calendar.item.card")}: {t(`calendar.item.cardState.${item.card_state}`)}
              </span>
            )}
            <span>{t("calendar.item.createdVia", { via: item.created_via === "web" ? t("calendar.item.viaWeb")
              : item.created_via === "chat" ? t("calendar.item.viaChat") : t("calendar.item.viaCron") })}</span>
          </div>
        )}
        {item?.error && (
          <div className="rounded-md bg-warning px-3 py-2 text-sm text-warning-foreground">
            <span className="font-medium">{t("calendar.item.error")}: </span><span className="break-words">{item.error}</span>
          </div>
        )}
        {item?.published_url && (
          <p className="min-w-0 text-sm">
            <a href={item.published_url} target="_blank" rel="noreferrer" className="break-all text-signal underline underline-offset-[3px]">
              {t("calendar.item.publishedAt", { url: item.published_url })}
            </a>
          </p>
        )}
        {item?.draft_comment && (
          <div className="rounded-md bg-muted px-3 py-2 text-sm">
            <span className="font-medium">{t("calendar.item.draftComment")}: </span><span className="whitespace-pre-wrap break-words">{item.draft_comment}</span>
          </div>
        )}
        {item && isMissed(item) && <p className="text-sm text-warning-foreground">{t("calendar.item.missed")}</p>}
        {locked && <p className="text-sm text-muted-foreground">{t(drafting ? "calendar.item.drafting" : "calendar.item.locked")}</p>}

        <fieldset disabled={locked || busy} className="grid min-w-0 gap-4">
          {/* `items-start`: each column is itself a grid, and a stretched one spreads the
              row's spare height between its own rows — a hint under one column
              pushes the other column's label and control down. */}
          <div className="grid min-w-0 items-start gap-4 sm:grid-cols-2">
            <div className="grid min-w-0 gap-1.5">
              <Label htmlFor="content-channel">{t("calendar.item.channel")}</Label>
              <Select value={d.channel} onValueChange={pickChannel} disabled={locked || busy}>
                <SelectTrigger id="content-channel" className="w-full"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {channels.map((c) => (
                    <SelectItem key={c} value={c}><ChannelIcon channel={c} /> {channelInfo(c).label}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="grid min-w-0 gap-1.5">
              <Label htmlFor="content-format">{t("calendar.item.format")}</Label>
              <Select value={d.format} onValueChange={(format) => setD({ ...d, format })} disabled={locked || busy}>
                <SelectTrigger id="content-format" className="w-full"><SelectValue /></SelectTrigger>
                <SelectContent>{formats.map((f) => <SelectItem key={f} value={f}>{formatName(f)}</SelectItem>)}</SelectContent>
              </Select>
            </div>
          </div>

          <div className="grid min-w-0 gap-1.5">
            <Label htmlFor="content-title">{t("calendar.item.title")}</Label>
            <Input id="content-title" value={d.title} maxLength={200} onChange={(e) => setD({ ...d, title: e.target.value })}
              placeholder={t("calendar.item.titlePlaceholder")} />
          </div>
          <div className="grid min-w-0 gap-1.5">
            <Label htmlFor="content-body">{t("calendar.item.body")}</Label>
            <Textarea id="content-body" rows={6} value={d.body} onChange={(e) => setD({ ...d, body: e.target.value })} />
            <p className="text-xs text-muted-foreground">{t("calendar.item.bodyHint")}</p>
          </div>
          <div className="grid min-w-0 gap-1.5">
            <Label htmlFor="content-media">{t("calendar.item.media")}</Label>
            <Textarea id="content-media" rows={2} value={d.media} onChange={(e) => setD({ ...d, media: e.target.value })}
              className="font-mono md:text-code" spellCheck={false} />
            <p className="text-xs text-muted-foreground">{t("calendar.item.mediaHint")}</p>
          </div>

          <div className="grid min-w-0 items-start gap-4 sm:grid-cols-2">
            <div className="grid min-w-0 gap-1.5">
              <Label htmlFor="content-publish-at">{t("calendar.item.publishAt")}</Label>
              <Input id="content-publish-at" type="datetime-local" value={d.publishAt} onChange={(e) => setD({ ...d, publishAt: e.target.value })} />
              <p className="text-xs text-muted-foreground">{t("calendar.item.publishAtHint")} {deviceZone() && t("calendar.localTime", { tz: deviceZone() })}</p>
            </div>
            {statusEditable && (
              <div className="grid min-w-0 gap-1.5">
                <Label htmlFor="content-status">{t("calendar.item.status")}</Label>
                <Select value={d.status} onValueChange={(v) => setD({ ...d, status: v as Editable })} disabled={locked || busy}>
                  <SelectTrigger id="content-status" className="w-full"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {(EDITABLE as Editable[]).map((st) => <SelectItem key={st} value={st}>{t(`calendar.status.${st}`)}</SelectItem>)}
                  </SelectContent>
                </Select>
              </div>
            )}
          </div>
        </fieldset>

        {item && item.media.length > 0 && (
          <ul className="grid min-w-0 grid-cols-2 gap-2 sm:grid-cols-3">
            {item.media.map((m, n) => (
              <li key={`${n}:${m.path}`} className="min-w-0 overflow-hidden rounded-md border border-border bg-muted">
                {isVideo(m.path)
                  ? <video controls preload="metadata" src={api.content.mediaUrl(item.id, n)} className="aspect-square w-full bg-black object-contain" />
                  : <img src={api.content.mediaUrl(item.id, n)} alt={t("calendar.item.media0", { n: n + 1 })} loading="lazy" className="aspect-square w-full object-contain" />}
                <p className="truncate px-2 py-1 font-mono text-[11px] text-muted-foreground" title={m.path}>{m.path}</p>
              </li>
            ))}
          </ul>
        )}

        {item && (item.source_path || item.thread_id || item.group_key) && (
          <dl className="grid min-w-0 gap-1 text-xs text-muted-foreground">
            {item.source_path && (
              <div className="flex min-w-0 gap-2"><dt className="shrink-0">{t("calendar.item.source")}</dt>
                <dd className="min-w-0 break-all font-mono text-foreground">{item.source_path}</dd></div>
            )}
            {item.group_key && <div className="min-w-0 break-all">{t("calendar.item.group", { key: item.group_key })}</div>}
            {item.thread_id && (
              <div><Link to={`/t/${encodeURIComponent(item.thread_id)}`} className="text-signal underline underline-offset-[3px]">{t("calendar.item.conversation")}</Link></div>
            )}
          </dl>
        )}

        {canApproveTheme && <p className="text-xs text-muted-foreground">{t("calendar.item.themeHint")}</p>}
        {canApprove && (refusalText
          ? <p id="content-approve-why" className="text-xs text-warning-foreground">{refusalText}</p>
          : <p className="text-xs text-muted-foreground">{t("calendar.item.approveHint")}</p>)}
        {item?.status === "scheduled" && <p className="text-xs text-muted-foreground">{t("calendar.item.editApproved")}</p>}
        {error && <p role="alert" className="break-words text-sm text-error-foreground">{error}</p>}
        </div>

        <SheetFooter className="flex-row flex-wrap gap-2 border-t border-border px-5 py-4 sm:justify-between">
          <div className="flex flex-wrap gap-2">
            {item && item.status !== "published" && !drafting && (
              <Button type="button" variant="ghost" className="text-error-foreground" disabled={busy} onClick={() => setConfirmDelete(true)}>
                {t("calendar.item.delete")}
              </Button>
            )}
          </div>
          <div className="flex flex-wrap justify-end gap-2">
            {item && RESOLVABLE.includes(item.status) && !draftFailed && (
              <Button type="button" variant="secondary" disabled={busy} onClick={() => setResolving(true)}>{t("calendar.item.retry")}</Button>
            )}
            {item?.status === "scheduled" && (
              <Button type="button" variant="secondary" disabled={busy}
                onClick={() => void run(() => s.unapproveItem(item.id), t("calendar.item.unapproved"))}>{t("calendar.item.unapprove")}</Button>
            )}
            {item?.status === "planned" && (
              <Button type="button" variant="secondary" disabled={busy}
                onClick={() => void run(() => s.unapproveTheme(item.id), t("calendar.item.themeUnapproved"))}>{t("calendar.item.unapproveTheme")}</Button>
            )}
            {!locked && (
              <Button type="submit" variant={decides ? "secondary" : "default"} disabled={busy || (item !== null && !dirty)}>
                {t("calendar.item.save")}
              </Button>
            )}
            {item && canRetryDrafting && (
              <Button type="button" disabled={busy}
                onClick={() => void run(() => s.retryDrafting(item.id), t("calendar.item.retriedDrafting"))}>{t("calendar.item.retryDrafting")}</Button>
            )}
            {item && canApproveTheme && (
              <Button type="button" variant={canApprove ? "secondary" : "default"} disabled={busy}
                onClick={() => void approveTheme(item.id)}>{t("calendar.item.approveTheme")}</Button>
            )}
            {item && canApprove && (
              <Button type="button" disabled={busy || refusal !== null} aria-describedby={refusal ? "content-approve-why" : undefined}
                onClick={() => void approve(item.id)}>{t("calendar.item.approve")}</Button>
            )}
          </div>
        </SheetFooter>
      </form>
      {item && (
        <ConfirmDialog open={confirmDelete} onOpenChange={setConfirmDelete} title={t("calendar.item.delete")}
          description={t("calendar.item.deleteConfirm", { name: itemLabel(item, t("calendar.item.untitled")) })}
          confirmLabel={t("calendar.item.delete")} destructive
          onConfirm={() => void run(() => s.removeItem(item.id), t("calendar.item.deleted"))} />
      )}
    </SheetContent>
  );
}

/** The two-way question after a failure: post it again, or record where it went out. */
function ResolveForm({ item, back, close }: { item: ContentItemWire; back: () => void; close: () => void }) {
  const t = useT();
  const retryItem = useStore((s) => s.retryItem);
  const [mode, setMode] = useState<"repost" | "published">("repost");
  const [url, setUrl] = useState(item.published_url ?? "");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const submit = async (e: FormEvent) => {
    e.preventDefault(); setBusy(true); setError(null);
    try {
      await retryItem(item.id, mode === "repost" ? { mode } : { mode, url: url.trim() });
      toast(mode === "repost" ? t("calendar.status.queued") : t("calendar.status.published"));
      close();
    } catch (err) { setError(message(err, t("common.error"))); }
    finally { setBusy(false); }
  };
  const choice = (value: "repost" | "published", title: string, hint: string) => (
    <label className={cn("flex cursor-pointer items-start gap-3 rounded-lg border p-3",
      mode === value ? "border-selected-foreground bg-selected text-selected-foreground" : "border-border hover:bg-accent")}>
      <input type="radio" name="resolve" value={value} checked={mode === value} onChange={() => setMode(value)} className="mt-1" />
      <span className="min-w-0">
        <span className="block text-sm font-medium">{title}</span>
        <span className={cn("block text-xs", mode === value ? "" : "text-muted-foreground")}>{hint}</span>
      </span>
    </label>
  );
  return (
    <SheetContent side="right" className="w-full gap-0 p-0 sm:max-w-2xl">
      <form onSubmit={submit} className="flex h-full min-h-0 min-w-0 flex-col">
        <SheetHeader className="border-b border-border px-5 pb-4 pt-5">
          <SheetTitle className="pr-8 text-lg">{t("calendar.item.retryTitle")}</SheetTitle>
          <SheetDescription>{t("calendar.item.retryLead")}</SheetDescription>
        </SheetHeader>
        <div className="grid min-h-0 flex-1 content-start gap-4 overflow-y-auto px-5 py-4">
        <div role="radiogroup" className="grid gap-2">
          {choice("repost", t("calendar.item.repost"), t("calendar.item.repostHint"))}
          {choice("published", t("calendar.item.wasPublished"), t("calendar.item.wasPublishedHint"))}
        </div>
        {mode === "published" && (
          <div className="grid gap-1.5">
            <Label htmlFor="content-url">{t("calendar.item.url")}</Label>
            <Input id="content-url" type="url" inputMode="url" required value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://" />
          </div>
        )}
        {error && <p role="alert" className="break-words text-sm text-error-foreground">{error}</p>}
        </div>
        <SheetFooter className="flex-row justify-end gap-2 border-t border-border px-5 py-4">
          <Button type="button" variant="secondary" onClick={back} disabled={busy}>{t("common.back")}</Button>
          <Button type="submit" disabled={busy || (mode === "published" && !url.trim())}>{t("calendar.item.confirm")}</Button>
        </SheetFooter>
      </form>
    </SheetContent>
  );
}
