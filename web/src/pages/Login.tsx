/** Telegram Login Widget: the way into CladeX from a browser. Inside Telegram the
 * page is a Mini App and signs in with initData instead; a refusal there is final,
 * since the widget would only ask the same account again. */

import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router";
import { api } from "@/api/client";
import { Logo } from "@/components/cladex/Logo";
import { t as translate, useT } from "@/i18n";
import { useStore } from "@/store";
import { loginFromMiniApp, miniAppInitData } from "@/telegram";

declare global { interface Window { onTelegramAuth?: (u: Record<string, unknown>) => void } }

export function Login() {
  const t = useT();
  const ref = useRef<HTMLDivElement>(null);
  const [error, setError] = useState<string | null>(null);
  const navigate = useNavigate();
  const setMe = useStore((s) => s.setMe);
  useEffect(() => {
    let cancelled = false;
    // A valid cookie already? Then this page has nothing to offer — a reload of
    // /login (a bookmark, a back button) must land in the chat, not sit here.
    api.me().then((m) => { if (!cancelled) { setMe(m); navigate("/", { replace: true }); } })
      .catch(() => {});
    if (miniAppInitData()) {
      loginFromMiniApp()
        .then((m) => { if (!cancelled && m) { setMe(m); navigate("/", { replace: true }); } })
        .catch(() => { if (!cancelled) setError(translate("login.failed")); });
      return () => { cancelled = true; };
    }
    window.onTelegramAuth = async (user) => {
      // the login body is identity only; the rest of `Me` comes from /api/me
      try { await api.login(user); setMe(await api.me()); navigate("/", { replace: true }); }
      catch { setError(translate("login.failed")); }   // the plain t: a hook result cannot be an effect dependency
    };
    // Which bot this deployment runs is a RUNTIME fact: the SPA is built once, in
    // the Dockerfile, long before anyone points it at a bot. So ask the server
    // rather than reading a build-time env var — a bundle built without one would
    // render the widget as data-telegram-login="undefined" and nobody could log in.
    void (async () => {
      let bot: string;
      try { bot = (await api.config()).bot_username; } catch { bot = ""; }
      if (cancelled) return;
      if (!bot) {
        setError(translate("login.noBot"));
        return;
      }
      const s = document.createElement("script");
      s.src = "https://telegram.org/js/telegram-widget.js?22";
      s.async = true;
      s.dataset.telegramLogin = bot;
      s.dataset.size = "large";
      s.dataset.onauth = "onTelegramAuth(user)";
      s.dataset.requestAccess = "write";
      ref.current?.replaceChildren(s);
    })();
    return () => { cancelled = true; delete window.onTelegramAuth; };
  }, [navigate, setMe]);
  return (
    <main className="flex min-h-screen flex-col items-center justify-center gap-6 bg-background px-6">
      <Logo width={240} className="max-w-full" />
      <h1 className="sr-only">CladeX</h1>
      <p className="max-w-sm text-center text-sm text-muted-foreground">{t("login.lead")}</p>
      <div ref={ref} />
      {error && <p role="alert" className="text-sm text-error-foreground">{error}</p>}
    </main>
  );
}
