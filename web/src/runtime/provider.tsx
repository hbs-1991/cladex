/** Wires the zustand store into assistant-ui through an external-store runtime. */

import { useCallback, useEffect, useMemo, useReducer, useRef, type ReactNode } from "react";
import { useNavigate } from "react-router";
import {
  AssistantRuntimeProvider, AuiConfig, Tools, createMessageQueue, useExternalStoreRuntime,
  type AddToolResultOptions, type AppendMessage, type ThreadMessageLike,
} from "@assistant-ui/react";
import { api } from "@/api/client";
import { isModelCommand } from "@/lib/modelCommand";
import { useStore } from "@/store";
import { toolkit } from "@/toolkit";
import { attachmentAdapter } from "./adapters/attachments";
import { ServerDictationAdapter } from "./adapters/dictation";
import { ServerSpeechAdapter } from "./adapters/speech";
import { ContextUsageContext } from "./context-usage";
import { ThreadIdContext } from "./thread-id";

const dictation = new ServerDictationAdapter();
const speech = new ServerSpeechAdapter();

function textOf(m: AppendMessage): string {
  return m.content.filter((p) => p.type === "text").map((p) => (p as { text: string }).text).join("\n").trim();
}

export function CladeXRuntime({ threadId, children }: { threadId: string; children: ReactNode }) {
  const open = useStore((s) => s.open);
  const close = useStore((s) => s.close);
  useEffect(() => { void open(threadId); return () => close(threadId); }, [threadId, open, close]);

  const events = useStore((s) => s.byThread[threadId]);
  const folded = useStore((s) => s.folded)(threadId);
  void events; // subscribing to the array is what re-renders us; folded() memoises on it
  // What turns the thread's history skeleton on (thread.aui.tsx,
  // `isHistoryLoadingView`). Without it `isLoading` is always false, the view
  // with no messages yet is always "a new chat", and a lane with a year of
  // history greets the reader with the welcome block until its events land.
  //
  // Only the thread's flag, never the thread LIST's: `isHistoryLoadingView`
  // stands down while `threads.isLoading` is true, so reporting the sidebar's
  // fetch here would put the welcome block back on exactly the slowest path
  // there is — a reload straight onto `/t/:id`, where the list and the history
  // are in flight together.
  const isLoading = useStore((s) => s.loading[threadId] ?? false);

  // Returns whether a turn was started, so the queue below knows when a send
  // ended without one (a `/model` command) and must not wait for a falling edge.
  const send = useCallback(async (m: AppendMessage): Promise<boolean> => {
    const text = textOf(m);
    // `/model` and `/effort` are the CLI's own local commands: sent as a turn they
    // would answer "Set model to x" and nothing else. The composer's Enter key
    // catches them first (SlashCommands.tsx) and keeps the draft; this is the
    // path everything else takes — the send button, a suggestion, a reload — and
    // it opens the picker rather than spending a turn on a refusal.
    if (isModelCommand(text)) { useStore.getState().setModelPickerOpen(true); return false; }
    await api.threads.send(threadId, {
      text,
      attachment_ids: (m.attachments ?? []).map((a) => a.id),
    });
    return true;
  }, [threadId]);
  const onNew = useCallback(async (m: AppendMessage) => { await send(m); }, [send]);

  // A message written while the agent works is held here and sent the moment the
  // turn ends, so the owner never has to stop the agent to say something. The
  // driver has no `cancel`: a "steer" only moves the item to the front of the
  // queue and never interrupts the running turn.
  // `send` changes only with the thread, so this is one queue per thread: a
  // lane switch never delivers the old lane's items, and clearing it drops them.
  const queue = useMemo(() => {
    const q = createMessageQueue({
      run: (m) => {
        send(m).then(
          (started) => { if (!started) q.notifyIdle(); },
          () => q.notifyIdle(),
        );
      },
    });
    return q;
  }, [send]);
  useEffect(() => () => queue.clear(), [queue]);
  // The runtime reads the queue only when this component hands it a new store,
  // and nothing in the queue re-renders us — so without this a message sent
  // mid-turn was held and delivered, but never shown in the composer's list.
  const [, queueChanged] = useReducer((n: number) => n + 1, 0);
  useEffect(() => queue.subscribe(queueChanged), [queue]);
  // The run's edges, as the queue needs them. A fresh queue (a lane switch onto a
  // turn already running) starts from "idle", so it is told about the run too.
  const seen = useRef<{ queue: typeof queue | null; running: boolean }>({ queue: null, running: false });
  useEffect(() => {
    const was = seen.current.queue === queue ? seen.current.running : false;
    if (!was && folded.isRunning) queue.notifyBusy();
    if (was && !folded.isRunning) queue.notifyIdle();
    seen.current = { queue, running: folded.isRunning };
  }, [folded.isRunning, queue]);

  const onCancel = useCallback(async () => { await api.threads.cancel(threadId); }, [threadId]);

  const onReload = useCallback(async () => {
    const lastUser = [...folded.messages].reverse().find((x) => x.role === "user");
    queue.clear();
    const text = lastUser ? (lastUser.content as { type: string; text?: string }[]).find((p) => p.type === "text")?.text ?? "" : "";
    if (text) await api.threads.send(threadId, { text, attachment_ids: [] });
  }, [threadId, folded.messages, queue]);

  const onAddToolResult = useCallback(async ({ toolCallId, result }: AddToolResultOptions) => {
    if (toolCallId.startsWith("ask:")) {
      await api.asks.answer(toolCallId.slice(4), String((result as { answer: string }).answer));
    }
  }, []);

  const list = useStore((s) => s.list);
  const create = useStore((s) => s.create);
  const rename = useStore((s) => s.rename);
  const archive = useStore((s) => s.archive);
  const unarchive = useStore((s) => s.unarchive);
  const navigate = useNavigate();
  const threadList = useMemo(() => ({
    threadId,
    threads: list.filter((t) => !t.archived).map((t) => ({ status: "regular" as const, id: t.id, title: t.title ?? undefined })),
    archivedThreads: list.filter((t) => t.archived).map((t) => ({ status: "archived" as const, id: t.id, title: t.title ?? undefined })),
    onSwitchToNewThread: async () => navigate(`/t/${encodeURIComponent(await create())}`),
    onSwitchToThread: (id: string) => navigate(`/t/${encodeURIComponent(id)}`),
    onRename: rename, onArchive: archive, onUnarchive: unarchive, onDelete: archive,
  }), [threadId, list, create, rename, archive, unarchive, navigate]);

  const adapters = useMemo(() => ({
    attachments: attachmentAdapter(threadId), dictation, speech, threadList,
  }), [threadId, threadList]);

  const runtime = useExternalStoreRuntime<ThreadMessageLike>({
    messages: folded.messages,
    isRunning: folded.isRunning,
    isLoading,
    convertMessage: (m) => m,
    onNew, onCancel, onReload, onAddToolResult,
    adapters,
    queue: queue.adapter,
  });
  const config = useMemo(() => AuiConfig({ tools: Tools({ toolkit }) }), []);

  return (
    <AssistantRuntimeProvider runtime={runtime} config={config}>
      <ThreadIdContext.Provider value={threadId}>
        <ContextUsageContext.Provider value={folded.context}>{children}</ContextUsageContext.Provider>
      </ThreadIdContext.Provider>
    </AssistantRuntimeProvider>
  );
}
