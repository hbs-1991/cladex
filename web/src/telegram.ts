/** The Telegram Mini App bridge: is this page open inside Telegram, and what does
 * Telegram vouch for? `telegram-web-app.js` (index.html) exposes the container as
 * `window.Telegram.WebApp`; the fragment Telegram opens the page with carries the
 * same initData, which is the fallback for a page that rendered before the script
 * landed. The signed-in identity comes from the server verifying that string —
 * nothing here trusts it. */

import { api } from "@/api/client";
import type { Me } from "@/api/types";

type WebApp = {
  initData: string;
  colorScheme: "light" | "dark";
  ready(): void;
  expand(): void;
};

declare global { interface Window { Telegram?: { WebApp?: WebApp } } }

/** The raw initData when the page runs inside Telegram, else null. */
export function miniAppInitData(): string | null {
  const fromBridge = window.Telegram?.WebApp?.initData;
  if (fromBridge) return fromBridge;
  const fragment = window.location.hash.replace(/^#/, "");
  return new URLSearchParams(fragment).get("tgWebAppData") || null;
}

/** Sign in with what Telegram handed the page. Resolves to null outside Telegram
 * (nothing to sign in with) and rejects when the server refuses — a stranger's
 * Telegram account is still a stranger. On success the container is told the
 * page is ready and asked for the full height, the way a Mini App is expected to.
 * The login body is identity and role only (a frozen wire), so the full `Me` —
 * profile, model default — is read from `/api/me` once the cookie is set. */
export async function loginFromMiniApp(): Promise<Me | null> {
  const initData = miniAppInitData();
  if (!initData) return null;
  await api.webappLogin(initData);
  const app = window.Telegram?.WebApp;
  app?.ready();
  app?.expand();
  return api.me();
}
