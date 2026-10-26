/** The `/` and `/t/:id` page: header, runtime and the assistant-ui thread. */

import { useParams } from "react-router";
import { CladeXRuntime } from "@/runtime/provider";
import { Thread, type ThreadComponents } from "@/components/assistant-ui/elements/thread.aui";
import { ChatHeader } from "@/components/cladex/ChatHeader";
import { ComposerDraft } from "@/components/cladex/ComposerDraft";
import { ConnectionBar } from "@/components/cladex/ConnectionBar";
import { EmptyState } from "@/components/cladex/EmptyState";
import { StepGroup } from "@/components/cladex/StepGroup";
import { ToolRow } from "@/components/cladex/ToolRow";
import { useStore } from "@/store";

const COMPONENTS: ThreadComponents = { ToolFallback: ToolRow, ToolGroup: StepGroup, ReasoningGroup: StepGroup, Welcome: EmptyState };

export function Chat() {
  const { id } = useParams();
  const threadId = id ? decodeURIComponent(id) : null;
  const thread = useStore((s) => s.list.find((t) => t.id === threadId));
  if (!threadId) return <EmptyState standalone />;   // "/" — the button there creates a thread and navigates
  return (
    <CladeXRuntime threadId={threadId}>
      <ChatHeader thread={thread} />
      <ConnectionBar threadId={threadId} />
      <ComposerDraft />
      <div className="min-h-0 flex-1"><Thread components={COMPONENTS} /></div>
    </CladeXRuntime>
  );
}
