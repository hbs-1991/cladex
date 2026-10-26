/** The sidebar's footer: who is signed in, and a menu with the language, the theme,
 * settings and log out. */

import { useNavigate } from "react-router";
import { toast } from "sonner";
import { Bot, ChevronDown, Globe, LogOut, Monitor, Moon, Settings, Sun, SunMoon, type LucideIcon } from "lucide-react";
import { api } from "@/api/client";
import { LOCALES, useT, type Locale } from "@/i18n";
import { effortKey, findModel, keepEffort, modelErrorText, modelLabel } from "@/lib/models";
import { useStore } from "@/store";
import type { Theme } from "@/store/ui";
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel, DropdownMenuRadioGroup, DropdownMenuRadioItem,
  DropdownMenuSeparator, DropdownMenuSub, DropdownMenuSubContent, DropdownMenuSubTrigger, DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

const LANGUAGE_NAMES: Record<Locale, string> = { en: "English", ru: "Русский" };   // each language names itself
// Sentinels rather than empty strings, so a radio item always carries a value
// and no catalog id can ever be spelled like one.
const DEPLOY = "deploy";  // "no personal default" — the deploy default decides
const AUTO = "auto";      // "no effort flag"
const THEMES: Theme[] = ["light", "dark", "system"];
const THEME_ICONS: Record<Theme, LucideIcon> = { light: Sun, dark: Moon, system: Monitor };

/** One cell of the theme segmented control: the indicator dot is hidden, the icon carries the state. */
const SEGMENT = "size-6 justify-center rounded-sm p-0 text-muted-foreground [&>span:first-child]:hidden "
  + "focus:bg-transparent focus:text-foreground data-[state=checked]:bg-popover data-[state=checked]:text-foreground";

function RowIcon({ icon: Icon }: { icon: LucideIcon }) {
  return <Icon className="size-4 shrink-0" strokeWidth={1.75} />;
}

export function UserMenu() {
  const t = useT();
  const me = useStore((s) => s.me);
  const setMe = useStore((s) => s.setMe);
  const locale = useStore((s) => s.locale);
  const setLocale = useStore((s) => s.setLocale);
  const theme = useStore((s) => s.theme);
  const setTheme = useStore((s) => s.setTheme);
  const setSettingsOpen = useStore((s) => s.setSettingsOpen);
  const models = useStore((s) => s.models);
  const deployDefault = useStore((s) => s.deployDefault);
  const loadModels = useStore((s) => s.loadModels);
  const navigate = useNavigate();
  if (!me) return null;
  const who = me.role === "owner" ? t("sidebar.owner") : t("sidebar.user", { id: me.user_id });
  // The session may already be gone (an expired cookie, a server that is down);
  // the client state is cleared either way, so the rejection is not an error here.
  const logout = async () => { try { await api.logout(); } catch { /* already signed out */ } finally { setMe(null); navigate("/login", { replace: true }); } };
  const themeName = (v: Theme) => t(v === "light" ? "user.themeLight" : v === "dark" ? "user.themeDark" : "user.themeSystem");

  // The personal default: what a NEW lane, and a lane's next session, start on.
  // It never moves a session that is already running — that is an explicit,
  // priced switch on the lane itself, through the model pill or Telegram.
  const defaultModel = me.default_model ?? null;
  const effortsOf = findModel(models, defaultModel ?? deployDefault?.model ?? null);
  const effortName = (effort: string) => { const k = effortKey(effort); return k ? t(k) : effort; };
  const saveDefault = async (model: string | null, effort: string | null) => {
    try {
      const r = await api.setMyModelDefault({ model, effort });
      setMe({ ...me, default_model: r.default_model, default_effort: r.default_effort });
    } catch (e) { toast.error(modelErrorText(t, e)); }
  };
  return (
    <DropdownMenu>
      <DropdownMenuTrigger aria-label={t("user.menu")}
        className="flex w-full items-center gap-2 border-t border-sidebar-border px-4 py-3 text-left text-sm hover:bg-sidebar-accent data-[state=open]:bg-sidebar-accent">
        <span aria-hidden className="flex size-6 shrink-0 items-center justify-center rounded-full bg-foreground text-[11px] font-medium text-background">{who.charAt(0)}</span>
        <span className="truncate">{who}</span>
        <ChevronDown aria-hidden className="ml-auto size-4 shrink-0 text-muted-foreground" strokeWidth={1.75} />
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" side="top" className="w-64 shadow-floating">
        <DropdownMenuLabel className="truncate font-normal text-muted-foreground">{who}</DropdownMenuLabel>
        <DropdownMenuSeparator />

        <DropdownMenuSub>
          <DropdownMenuSubTrigger>
            <RowIcon icon={Globe} />
            {t("user.language")}
            <span className="ml-auto text-muted-foreground">{LANGUAGE_NAMES[locale]}</span>
          </DropdownMenuSubTrigger>
          <DropdownMenuSubContent>
            <DropdownMenuRadioGroup value={locale} onValueChange={(v) => setLocale(v as Locale)}>
              {LOCALES.map((l) => <DropdownMenuRadioItem key={l} value={l}>{LANGUAGE_NAMES[l]}</DropdownMenuRadioItem>)}
            </DropdownMenuRadioGroup>
          </DropdownMenuSubContent>
        </DropdownMenuSub>

        {/* Open to every user, not just the owner: non-owners pick from the same
            offered list. The catalog is fetched when the submenu is first opened
            — the account menu itself must not put a request on the wire. */}
        <DropdownMenuSub onOpenChange={(o) => { if (o) void loadModels(); }}>
          <DropdownMenuSubTrigger>
            <RowIcon icon={Bot} />
            {t("models.defaultTitle")}
            <span className="ml-auto truncate pl-2 text-muted-foreground">
              {defaultModel ? modelLabel(models, defaultModel) : t("models.deployDefault")}
            </span>
          </DropdownMenuSubTrigger>
          <DropdownMenuSubContent className="w-64">
            <DropdownMenuLabel className="font-normal text-muted-foreground">{t("models.model")}</DropdownMenuLabel>
            <DropdownMenuRadioGroup value={defaultModel ?? DEPLOY}
              onValueChange={(v) => void saveDefault(v === DEPLOY ? null : v, keepEffort(findModel(models, v), me.default_effort ?? null))}>
              {/* Like the theme row: a pick keeps the menu open, so the effort
                  levels below it become the new model's without a second trip. */}
              <DropdownMenuRadioItem value={DEPLOY} onSelect={(e) => e.preventDefault()}>{t("models.deployDefault")}</DropdownMenuRadioItem>
              {models.map((m) => <DropdownMenuRadioItem key={m.id} value={m.id} onSelect={(e) => e.preventDefault()}>{m.label}</DropdownMenuRadioItem>)}
            </DropdownMenuRadioGroup>
            <DropdownMenuSeparator />
            <DropdownMenuLabel className="font-normal text-muted-foreground">{t("models.effort")}</DropdownMenuLabel>
            {/* An effort on its own is not a pair, so it is saved with the model
                it applies to — which pins that model as the default. */}
            <DropdownMenuRadioGroup value={me.default_effort ?? AUTO}
              onValueChange={(v) => void saveDefault(defaultModel ?? deployDefault?.model ?? null, v === AUTO ? null : v)}>
              <DropdownMenuRadioItem value={AUTO} onSelect={(e) => e.preventDefault()}>
                {effortsOf?.default_effort ? t("models.autoWith", { effort: effortName(effortsOf.default_effort) }) : t("models.auto")}
              </DropdownMenuRadioItem>
              {(effortsOf?.efforts ?? []).map((e) => <DropdownMenuRadioItem key={e} value={e} onSelect={(ev) => ev.preventDefault()}>{effortName(e)}</DropdownMenuRadioItem>)}
            </DropdownMenuRadioGroup>
            <p className="px-2 pt-1.5 text-xs text-muted-foreground">{t("models.defaultCaption")}</p>
          </DropdownMenuSubContent>
        </DropdownMenuSub>

        {/* The theme stays a row of its own: switching it keeps the menu open, so the result is visible at once. */}
        <div className="flex items-center justify-between gap-2 px-2 py-1">
          <span className="flex items-center gap-2 text-sm">
            <SunMoon aria-hidden className="size-4 shrink-0 text-muted-foreground" strokeWidth={1.75} />
            {t("user.theme")}
          </span>
          <DropdownMenuRadioGroup aria-label={t("user.theme")} value={theme} onValueChange={(v) => setTheme(v as Theme)}
            className="flex items-center gap-0.5 rounded-md bg-accent p-0.5">
            {THEMES.map((v) => (
              <DropdownMenuRadioItem key={v} value={v} className={SEGMENT} aria-label={themeName(v)}
                onSelect={(e) => e.preventDefault()}>
                <RowIcon icon={THEME_ICONS[v]} />
              </DropdownMenuRadioItem>
            ))}
          </DropdownMenuRadioGroup>
        </div>

        <DropdownMenuSeparator />
        {/* Settings holds what only the owner may see (secrets), so a plain user is
            not offered a door that opens on a refusal. */}
        {me.role === "owner" && (
          <DropdownMenuItem onSelect={() => setSettingsOpen(true)}>
            <RowIcon icon={Settings} />
            {t("user.settings")}
          </DropdownMenuItem>
        )}
        <DropdownMenuItem onSelect={() => void logout()}>
          <RowIcon icon={LogOut} />
          {t("user.logOut")}
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
