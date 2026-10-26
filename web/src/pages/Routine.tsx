/** `/calendar/tasks/:id` — the routine header above its delivery thread. The old
 * `/routines/:id` redirects here (`RoutineRedirect` in the route table). */

import { useEffect } from "react";
import { useParams } from "react-router";
import { Thread } from "@/components/assistant-ui/elements/thread.aui";
import { ConnectionBar } from "@/components/cladex/ConnectionBar";
import { RoutineHeader } from "@/components/cladex/RoutineHeader";
import { StepGroup } from "@/components/cladex/StepGroup";
import { ToolRow } from "@/components/cladex/ToolRow";
import { useT } from "@/i18n";
import { CladeXRuntime } from "@/runtime/provider";
import { useStore } from "@/store";

const COMPONENTS = { ToolFallback: ToolRow, ToolGroup: StepGroup, ReasoningGroup: StepGroup };

export function Routine() {
  const id = Number(useParams().id);
  const s = useStore();
  const t = useT();
  useEffect(() => { if (!s.routinesLoaded) void s.loadRoutines(); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  const r = s.routines.find((x) => x.id === id);
  if (!r) return <p className="p-6 text-sm text-muted-foreground">{s.routinesLoaded ? t("routines.notFound") : t("common.loading")}</p>;
  return (
    <CladeXRuntime threadId={r.thread_id}>
      <RoutineHeader r={r} />
      <ConnectionBar threadId={r.thread_id} />
      <div className="min-h-0 flex-1"><Thread components={COMPONENTS} placeholder={t("chat.replyIn", { name: r.name })} /></div>
    </CladeXRuntime>
  );
}
