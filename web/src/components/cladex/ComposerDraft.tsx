/** Fills the composer with a draft another page handed over through router
 * state (the routines page's "Создать в чате"), then clears the state so a
 * reload does not paste it again. Renders nothing. */

import { useEffect } from "react";
import { useLocation, useNavigate } from "react-router";
import { useAui } from "@assistant-ui/react";

export function ComposerDraft() {
  const { state, pathname } = useLocation();
  const navigate = useNavigate();
  const aui = useAui();
  const draft = (state as { draft?: unknown } | null)?.draft;
  useEffect(() => {
    if (typeof draft !== "string" || !draft) return;
    aui.composer.setText(draft);
    navigate(pathname, { replace: true, state: null });
  }, [draft, pathname, navigate, aui]);
  return null;
}
