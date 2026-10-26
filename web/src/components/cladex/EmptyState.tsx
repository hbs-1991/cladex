/** Welcome block for a fresh thread; `standalone` adds the "new chat" button on the root route. */

import { useNavigate } from "react-router";
import { Button } from "@/components/ui/button";
import { Mark } from "./Logo";
import { useT } from "@/i18n";
import { useStore } from "@/store";

/** `title`/`lead` override the chat wording — the Memory page reuses the block for
 * a wiki with no pages in it yet, and the shape is the same: mark, line, hint. */
export function EmptyState({ standalone = false, title, lead }: { standalone?: boolean; title?: string; lead?: string }) {
  const t = useT();
  const create = useStore((s) => s.create);
  const navigate = useNavigate();
  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-4 py-12 text-center">
      <Mark width={64} />
      <h2 className="text-2xl font-semibold">{title ?? t("chat.welcome")}</h2>
      <p className="max-w-md text-sm text-muted-foreground">{lead ?? t("chat.welcomeLead")}</p>
      {standalone && (
        <Button onClick={async () => navigate(`/t/${encodeURIComponent(await create())}`)}>{t("sidebar.newChat")}</Button>
      )}
    </div>
  );
}
