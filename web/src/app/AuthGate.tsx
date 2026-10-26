/** Resolves the session once and sends anonymous visitors to /login — unless the
 * page is open inside Telegram, where initData signs them in on the spot. */

import { useEffect, useState } from "react";
import { Navigate, Outlet } from "react-router";
import { api } from "@/api/client";
import { useStore } from "@/store";
import { loginFromMiniApp } from "@/telegram";

export function AuthGate() {
  const me = useStore((s) => s.me);
  const setMe = useStore((s) => s.setMe);
  const [state, setState] = useState<"loading" | "ok" | "anon">(me ? "ok" : "loading");
  useEffect(() => {
    if (me) return;
    api.me()
      .catch(() => loginFromMiniApp())      // null outside Telegram, rejects when refused
      .then((m) => { if (m) { setMe(m); setState("ok"); } else setState("anon"); })
      .catch(() => setState("anon"));
  }, [me, setMe]);
  if (state === "loading") return null;
  if (state === "anon") return <Navigate to="/login" replace />;
  return <Outlet />;
}
