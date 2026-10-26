/** A new skill, two ways.
 *
 * Writing a SKILL.md by hand assumes you already know what a good one looks like —
 * the frontmatter it needs, the description that decides when the agent reaches for
 * it, how specific the steps have to be. The agent has a skill for exactly that
 * (`skill-authoring`), so the first option hands the job to a chat that loads it:
 * the owner describes the intent and the skill gets written with them, in the place
 * where it can be tried immediately.
 *
 * The manual form stays, unchanged, for when the text already exists — a skill
 * pasted from somewhere, or a small fix that does not need a conversation. The
 * server does the real validation either way; the name regex here only keeps the
 * obvious mistake from costing a round trip.
 */

import { useState } from "react";
import { useNavigate } from "react-router";
import { ApiError } from "@/api/client";
import type { SkillWire } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { useT } from "@/i18n";
import { useStore } from "@/store";

/** It is a directory name, so it obeys the catalog's own rule. */
const NAME_RE = /^[a-z0-9][a-z0-9_-]*$/;
/** The codes `POST /api/skills` can answer with; anything else is the server's own words. */
const KNOWN = ["name_taken", "bad_name", "bad_frontmatter", "too_large", "forbidden"] as const;
const known = (c: string): c is (typeof KNOWN)[number] => (KNOWN as readonly string[]).includes(c);

type Mode = "choose" | "agent" | "manual";

export function NewSkillDialog({ open, onOpenChange, onCreated }: {
  open: boolean; onOpenChange: (o: boolean) => void; onCreated?: (s: SkillWire) => void;
}) {
  const t = useT();
  const navigate = useNavigate();
  const createSkill = useStore((s) => s.createSkill);
  const createThread = useStore((s) => s.create);
  const [mode, setMode] = useState<Mode>("choose");
  const [idea, setIdea] = useState("");
  const [name, setName] = useState("");
  const [content, setContent] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const nameOk = NAME_RE.test(name);

  const close = (o: boolean) => {
    onOpenChange(o);
    // Reset on the way out, not on the way in: a dialog that reopens on the step the
    // owner left it on looks like it remembered something it did not.
    if (!o) { setMode("choose"); setIdea(""); setName(""); setContent(""); setError(null); }
  };

  const submit = async () => {
    setBusy(true); setError(null);
    try {
      const w = await createSkill(name, content);
      onCreated?.(w);
      close(false);
    } catch (e) {
      setError(e instanceof ApiError && known(e.code) ? t(`skills.errors.${e.code}`) : e instanceof Error ? e.message : t("common.error"));
    } finally { setBusy(false); }
  };

  /** Open a fresh thread with the request already in the composer, unsent. The owner
   *  reads it and presses send — the same handover the routines page makes. */
  const inChat = async () => {
    setBusy(true); setError(null);
    try {
      const id = await createThread();
      close(false);
      navigate(`/t/${encodeURIComponent(id)}`, { state: { draft: t("skills.chatDraft") + idea.trim() } });
    } catch (e) {
      setError(e instanceof Error ? e.message : t("common.error"));
    } finally { setBusy(false); }
  };

  const lead = mode === "agent" ? t("skills.withAgentLead")
    : mode === "manual" ? t("skills.newLead") : t("skills.newChooseLead");

  return (
    <Dialog open={open} onOpenChange={close}>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>{t("skills.newTitle")}</DialogTitle>
          <DialogDescription>{lead}</DialogDescription>
        </DialogHeader>

        {mode === "choose" && (
          <div className="grid gap-3">
            <button type="button" onClick={() => setMode("agent")}
              className="rounded-xl border border-border p-4 text-left transition-colors hover:bg-muted">
              <span className="block font-medium">{t("skills.withAgent")}</span>
              <span className="mt-0.5 block text-sm text-muted-foreground">{t("skills.withAgentLead")}</span>
            </button>
            <button type="button" onClick={() => setMode("manual")}
              className="rounded-xl border border-border p-4 text-left transition-colors hover:bg-muted">
              <span className="block font-medium">{t("skills.byHand")}</span>
              <span className="mt-0.5 block text-sm text-muted-foreground">{t("skills.byHandLead")}</span>
            </button>
          </div>
        )}

        {mode === "agent" && (
          <form className="grid gap-4" onSubmit={(e) => { e.preventDefault(); void inChat(); }}>
            <div className="grid gap-1.5">
              <Label htmlFor="new-skill-idea">{t("skills.ideaLabel")}</Label>
              <Textarea id="new-skill-idea" rows={5} value={idea} onChange={(e) => setIdea(e.target.value)}
                placeholder={t("skills.ideaPlaceholder")} />
              <p className="text-xs text-muted-foreground">{t("skills.ideaHint")}</p>
            </div>
            {error && <p role="alert" className="text-sm text-error-foreground">{error}</p>}
            <div className="flex justify-end gap-2">
              <Button type="button" variant="ghost" onClick={() => setMode("choose")}>{t("common.back")}</Button>
              <Button type="submit" disabled={busy || idea.trim() === ""}>{t("skills.openInChat")}</Button>
            </div>
          </form>
        )}

        {mode === "manual" && (
          <form className="grid gap-4" onSubmit={(e) => { e.preventDefault(); void submit(); }}>
            <div className="grid gap-1.5">
              <Label htmlFor="new-skill-name">{t("skills.nameLabel")}</Label>
              <Input id="new-skill-name" value={name} onChange={(e) => setName(e.target.value)}
                placeholder={t("skills.namePlaceholder")} aria-invalid={name !== "" && !nameOk} />
              {name !== "" && !nameOk && <p className="text-xs text-error-foreground">{t("skills.nameHint")}</p>}
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="new-skill-content">{t("skills.contentLabel")}</Label>
              <Textarea id="new-skill-content" rows={12} value={content} onChange={(e) => setContent(e.target.value)}
                placeholder={t("skills.contentPlaceholder")} className="font-mono text-xs" />
              <p className="text-xs text-muted-foreground">{t("skills.editHint")}</p>
            </div>
            {error && <p role="alert" className="text-sm text-error-foreground">{error}</p>}
            <div className="flex justify-end gap-2">
              <Button type="button" variant="ghost" onClick={() => setMode("choose")}>{t("common.back")}</Button>
              <Button type="submit" disabled={busy || !nameOk || content.trim() === ""}>{t("skills.create")}</Button>
            </div>
          </form>
        )}
      </DialogContent>
    </Dialog>
  );
}
