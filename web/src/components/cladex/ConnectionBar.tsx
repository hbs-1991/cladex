/** A slim bar under the chat header while the SSE stream is reconnecting. */

import { useT } from "@/i18n";
import { useStore } from "@/store";

export function ConnectionBar({ threadId }: { threadId: string }) {
  const t = useT();
  const state = useStore((s) => s.stream[threadId]);
  if (state !== "reconnecting" && state !== "error") return null;
  return (
    <div role="status" className="border-b bg-warning px-4 py-1 text-xs text-warning-foreground md:px-6">
      {state === "error" ? t("chat.loadFailed") : t("chat.reconnecting")}
    </div>
  );
}
