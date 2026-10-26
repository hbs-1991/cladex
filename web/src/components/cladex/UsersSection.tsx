/** Settings → Users: who may talk to this agent, and what each of them may use.
 *
 * One list, owner first. A row the `users` table knows shows whatever Telegram has
 * told us about them — a name, a @handle, the day they were added; an id that is
 * only in `allowed_users` on the host shows as that, because nothing has ever met
 * them. Revoking is a row write, so it bites on the person's next request rather
 * than when their cookie expires, and it is reversible: threads, files and their
 * workspace are left where they are.
 *
 * Adding takes a numeric Telegram id and nothing else. A bot can turn an id into a
 * name but never a @username into an id, so a field that accepted handles would be
 * a field that quietly failed.
 *
 * Everyone but the owner carries two grants: Bash (a shell, and with it Agent and
 * Task) and Learning (reviews of their conversations and the curator's weekly pass
 * over their tree, paid from the owner's account). A switch shows the value the
 * server resolved, marked "default" while the person has no choice of their own
 * and follows `config.yaml`. Only a row can hold a choice, so an id that is only in
 * `allowed_users` shows its switches disabled until their first message makes one —
 * a grant that created the row would enrol somebody nobody has met. Turning Bash
 * on asks first, because one container and one system user mean a shell reaches
 * every user's files and credentials, not only this person's; everything else
 * applies at once, since it narrows what they can reach or only spends tokens.
 * A group chat runs on the owner's lane whoever writes in it, so the note under
 * the list says the switches are no boundary there rather than letting them imply one.
 */

import { useEffect, useRef, useState } from "react";
import { Info, Loader2, Plus, ShieldOff, ShieldCheck, UserRound } from "lucide-react";
import { toast } from "sonner";
import { ApiError } from "@/api/client";
import type { MemberGrants, MemberWire } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { useT, type Key } from "@/i18n";
import { dayLabel } from "@/lib/dates";
import { useStore } from "@/store";
import { ConfirmDialog } from "./AskDialog";

const CODES = ["user_exists", "owner_immutable", "not_found", "bad_request", "forbidden"] as const;
type Code = (typeof CODES)[number];
const known = (c: string): c is Code => (CODES as readonly string[]).includes(c);

type T = (k: Key, p?: Record<string, string | number>) => string;

function errorText(t: T, e: unknown): string {
  if (e instanceof ApiError && known(e.code)) return t(`users.errors.${e.code}`);
  return e instanceof Error ? e.message : t("common.error");
}

/** What to call somebody: their name, else their handle, else their id. */
function memberName(m: MemberWire): string {
  return m.display_name || (m.username ? `@${m.username}` : String(m.user_id));
}

type Grant = keyof MemberGrants;

/** Everything one grant's switch reads, so the two switches cannot drift apart. */
const GRANTS = {
  shell: { label: "users.shell", labelFor: "users.shellFor", inherited: "shell_inherited" },
  learning: { label: "users.learning", labelFor: "users.learningFor", inherited: "learning_inherited" },
} as const satisfies Record<Grant, { label: Key; labelFor: Key; inherited: keyof MemberWire }>;

/** The id of the legend line under the list that explains a grant — every row's
 *  switch points at it with `aria-describedby` instead of repeating it. */
const hintId = (g: Grant) => `users-hint-${g}`;

function GrantSwitch({ m, grant, disabled, onChange }: {
  m: MemberWire; grant: Grant; disabled: boolean; onChange: (on: boolean) => void;
}) {
  const t = useT();
  const spec = GRANTS[grant];
  const id = `grant-${grant}-${m.user_id}`;
  const inherited = m[spec.inherited];
  return (
    <div className="flex items-center gap-2">
      <Switch id={id} checked={m[grant]} disabled={disabled} onCheckedChange={onChange}
        aria-label={t(spec.labelFor, { name: memberName(m) })}
        aria-describedby={inherited ? `${id}-inherited ${hintId(grant)}` : hintId(grant)} />
      {/* The label widens the touch target: tapping the word flips the switch. */}
      <Label htmlFor={id} className="font-normal">{t(spec.label)}</Label>
      {inherited && (
        <span id={`${id}-inherited`} title={t("users.inheritedHint")}
          className="rounded-sm bg-muted px-1.5 py-0.5 text-xs text-muted-foreground">
          {t("users.inherited")}
        </span>
      )}
    </div>
  );
}

function Row({ m, onToggle, onGrants }: {
  m: MemberWire; onToggle: (m: MemberWire) => void; onGrants: (m: MemberWire, g: MemberGrants) => Promise<void>;
}) {
  const t = useT();
  const [confirming, setConfirming] = useState(false);
  const [confirmingShell, setConfirmingShell] = useState(false);
  // One write at a time per row: the switch follows the re-read list, not the
  // click, so a second flip before that lands would be sent against a stale value.
  // A ref rather than `disabled`, which would throw keyboard focus off the switch.
  const writing = useRef(false);
  const name = memberName(m);
  const note = [
    name === String(m.user_id) ? "" : `ID ${m.user_id}`,
    m.source === "config" ? t("users.fromConfig")
      : m.created_at ? t("users.added", { date: dayLabel(m.created_at) }) : "",
    m.disabled ? t("users.disabled") : "",
  ].filter(Boolean).join(" · ");
  const change = (g: MemberGrants) => {
    if (writing.current) return;
    writing.current = true;
    void onGrants(m, g).finally(() => { writing.current = false; });
  };
  const locked = m.source === "config";
  return (
    // A grid rather than a flex row so the switches get a line of their own under
    // the name at every width — beside it, a phone would squeeze the name to nothing.
    <li className="grid grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-x-3 gap-y-2 border-b border-border py-2.5 last:border-b-0">
      <UserRound aria-hidden strokeWidth={1.75}
        className={`size-4 shrink-0 ${m.disabled ? "text-muted-foreground/50" : "text-muted-foreground"}`} />
      <div className="min-w-0">
        <p className="truncate text-sm">
          {name}
          {m.role === "owner" && (
            <span className="ml-2 rounded-sm bg-muted px-1.5 py-0.5 text-xs text-muted-foreground">
              {t("users.owner")}
            </span>
          )}
        </p>
        {note && <p className="truncate text-xs text-muted-foreground">{note}</p>}
      </div>
      {/* The owner has no control of their own: `is_owner` gates every mutation,
          so revoking it here would be a door that locks from the inside — and their
          reach is the owner's, so there is no grant to give or take either. */}
      {m.role !== "owner" && (
        <>
          <Button variant="ghost" size="icon" aria-label={t(m.disabled ? "users.enable" : "users.disable")}
            onClick={() => (m.disabled ? onToggle(m) : setConfirming(true))}>
            {m.disabled
              ? <ShieldCheck className="size-4" strokeWidth={1.75} />
              : <ShieldOff className="size-4" strokeWidth={1.75} />}
          </Button>
          <div className="col-span-2 col-start-2 flex flex-wrap items-center gap-x-5 gap-y-2">
            <GrantSwitch m={m} grant="shell" disabled={locked}
              onChange={(on) => (on ? setConfirmingShell(true) : change({ shell: false }))} />
            <GrantSwitch m={m} grant="learning" disabled={locked}
              onChange={(on) => change({ learning: on })} />
            {m.source === "config" && (
              <span className="text-xs text-muted-foreground">{t("users.afterFirstMessage")}</span>
            )}
          </div>
        </>
      )}
      <ConfirmDialog open={confirming} onOpenChange={setConfirming} destructive
        title={t("users.disableConfirm", { name })} confirmLabel={t("users.disable")}
        onConfirm={() => onToggle(m)} />
      <ConfirmDialog open={confirmingShell} onOpenChange={setConfirmingShell} destructive
        title={t("users.shellConfirm", { name })} description={t("users.shellConfirmBody", { name })}
        confirmLabel={t("users.shellConfirmAction")} onConfirm={() => change({ shell: true })} />
    </li>
  );
}

function AddForm() {
  const t = useT();
  const invite = useStore((s) => s.inviteMember);
  const [open, setOpen] = useState(false);
  const [id, setId] = useState("");
  const [busy, setBusy] = useState(false);
  const valid = /^[1-9]\d*$/.test(id.trim());
  const submit = async () => {
    setBusy(true);
    try {
      await invite(Number(id.trim()));
      toast.success(t("users.invited", { name: id.trim() }));
      setId(""); setOpen(false);
    } catch (e) { toast.error(errorText(t, e)); }
    finally { setBusy(false); }
  };
  if (!open) {
    return (
      <div>
        <Button variant="secondary" size="sm" onClick={() => setOpen(true)}>
          <Plus className="size-4" strokeWidth={1.75} />
          {t("users.add")}
        </Button>
      </div>
    );
  }
  return (
    <form className="grid gap-3 rounded-lg border border-border p-3"
      onSubmit={(e) => { e.preventDefault(); void submit(); }}>
      <div className="grid gap-1.5">
        <Label htmlFor="member-id">{t("users.id")}</Label>
        <Input id="member-id" autoFocus inputMode="numeric" autoComplete="off" className="font-mono"
          placeholder="123456789" value={id} onChange={(e) => setId(e.target.value)} />
        <p className="text-xs text-muted-foreground">{t("users.idHint")}</p>
      </div>
      <div className="flex justify-end gap-2">
        <Button type="button" variant="ghost" onClick={() => setOpen(false)}>{t("common.cancel")}</Button>
        <Button type="submit" disabled={!valid || busy}>{t("users.add")}</Button>
      </div>
    </form>
  );
}

export function UsersSection() {
  const t = useT();
  const members = useStore((s) => s.members);
  const loaded = useStore((s) => s.membersLoaded);
  const load = useStore((s) => s.loadMembers);
  const setDisabled = useStore((s) => s.setMemberDisabled);
  const setGrants = useStore((s) => s.setMemberGrants);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => { load().catch((e: unknown) => setError(errorText(t, e))); },
    []);                                     // eslint-disable-line react-hooks/exhaustive-deps

  const toggle = async (m: MemberWire) => {
    try {
      await setDisabled(m.user_id, !m.disabled);
      toast.success(t(m.disabled ? "users.restored" : "users.revoked", { name: memberName(m) }));
    } catch (e) { toast.error(errorText(t, e)); }
  };

  // A row sends one grant per write, so the toast names the one that moved.
  const grant = async (m: MemberWire, g: MemberGrants) => {
    const name = memberName(m);
    try {
      await setGrants(m.user_id, g);
      toast.success(g.shell !== undefined
        ? t(g.shell ? "users.shellOn" : "users.shellOff", { name })
        : t(g.learning ? "users.learningOn" : "users.learningOff", { name }));
    } catch (e) { toast.error(errorText(t, e)); }
  };

  return (
    <div className="grid gap-4">
      <p className="text-sm text-muted-foreground">{t("users.lead")}</p>
      <AddForm />
      {error && <p role="alert" className="text-sm text-error-foreground">{error}</p>}
      {!loaded && !error
        ? <Loader2 aria-label={t("common.loading")} className="size-4 animate-spin text-muted-foreground" />
        : <ul>{members.map((m) => (
            <Row key={m.user_id} m={m} onToggle={(x) => void toggle(x)} onGrants={grant} />
          ))}</ul>}
      {/* Visible rather than only in a tooltip: the owner often manages this from
          the Telegram Mini App, where nothing hovers. */}
      <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-1 text-xs text-muted-foreground">
        <dt className="font-medium text-foreground">{t("users.shell")}</dt>
        <dd id={hintId("shell")}>{t("users.shellHint")}</dd>
        <dt className="font-medium text-foreground">{t("users.learning")}</dt>
        <dd id={hintId("learning")}>{t("users.learningHint")}</dd>
      </dl>
      <p className="flex gap-2 rounded-md bg-muted p-3 text-xs text-muted-foreground">
        <Info aria-hidden strokeWidth={1.75} className="mt-px size-4 shrink-0" />
        {t("users.groupNote")}
      </p>
    </div>
  );
}
