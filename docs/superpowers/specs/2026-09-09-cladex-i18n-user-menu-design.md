# CladeX web: interface language and the user menu — design

**Goal:** the web app speaks English by default and Russian on request, switched
from a user menu in the sidebar; every interface string lives in one typed
dictionary so later features are written once, in both languages.

**Scope:** the sidebar's user section becomes a dropdown (language, theme, log
out); a two-locale dictionary replaces every hard-coded string in the SPA; dates,
durations and schedule descriptions follow the locale. Nothing on the server
changes. Tool-step labels keep following the language the model thinks in.

## Why this exists

The SPA was written with Russian strings in place. The product is used in
English and Russian, and the next features (Customize: connectors, plugins,
skills) add a lot of copy. A dictionary now means those features are written in
both languages from the first line, and the switch is a menu item rather than a
rewrite.

## Verified constraints

- Strings are inline JSX text and template literals across `web/src` (sidebar,
  login, chat header, empty state, routines page and form, palette, tool rows,
  step groups, file panel, ask cards, toasts). No i18n library is installed.
- `web/src/store/ui.ts` keeps `theme` in localStorage under `cladex.theme`; the
  same pattern fits `locale`.
- `web/src/toolkit/labels.ts` already carries `ru` and `en` verbs for tool
  labels; `labelFor(..., lang)` defaults to `"ru"` when no reasoning text is
  available. `web/src/lib/dates.ts` formats with `"ru-RU"`; `web/src/lib/schedule.ts`
  speaks Russian only.
- The sidebar footer (`web/src/components/cladex/Sidebar.tsx`) shows an avatar
  letter and "Владелец"/"Пользователь N"; the theme toggle sits in the header.
- `Login.tsx` copy is served by the SPA, not the server, so it is translatable
  the same way.

## Locked decisions

- **Two locales, `en` and `ru`, `en` default.** The dictionary keys are the
  contract; a third locale is a new file with the same keys.
- **Locale is a client preference**, stored in localStorage under
  `cladex.locale`, never sent to the server. The agent's own language is the
  model's business and is not affected.
- **Dictionary keys are dotted, feature-first** (`sidebar.newChat`,
  `routines.form.frequency`). Keys are typed from the English file; a missing key
  in the Russian file is a TypeScript error, so a language can never fall behind
  silently.
- **Tool-step labels follow the model's thought language**; the interface locale
  is only the fallback when there is no reasoning text.
- **Server errors are not translated.** The API's `{"error": {code, message}}`
  keeps English messages; the UI translates *codes* it knows and shows the
  message verbatim otherwise.

## Design

### Dictionary and hook

Reuses: the zustand store, `Intl` formatting already used in `dates.ts`.

```
web/src/i18n/
  en.ts        export const en = { sidebar: { newChat: "New chat", ... }, ... } as const
  ru.ts        export const ru: Dict = { ... }            // Dict = typeof en, structurally
  index.ts     t(key, params?), useT(), useLocale(), LOCALES, type Locale, type Key
  i18n.test.ts every key in en has a translation in ru and vice versa
```

`t("routines.count", { n })` interpolates `{n}`; plural forms are written as
three-way keys (`one`/`few`/`many`) chosen by a small `plural(n, locale)` helper,
which also replaces the hand-rolled Russian plural in `schedule.ts`.

`useT()` reads `locale` from the store so components re-render on switch.
Non-component code (`labels.ts`, `schedule.ts`, `dates.ts`) receives the locale
as an argument, with the store's value as the default.

### Store

`ui.ts` gains `locale: Locale` and `setLocale`, read and written like `theme`.
`document.documentElement.lang` is set on apply so screen readers and hyphenation
follow.

### User menu

Reuses: `DropdownMenu` from shadcn, `ThemeToggle`'s cycle logic (becomes three
radio items), `api.logout` and `setMe(null)`.

The sidebar footer's avatar row becomes a `DropdownMenuTrigger`. The menu holds:

| Group | Items |
|---|---|
| Language | English · Русский (radio, current checked) |
| Theme | Light · Dark · System (radio) |
| — | Log out |

The theme button leaves the sidebar header; the header keeps the name and the
search button.

### Locale-aware formatting

- `dates.ts`: `dayLabel`, `fmtTime`, `fmtDateTime`, `fmtDuration` take `locale`;
  "Сегодня/Вчера" and "11 мин 1 с" come from the dictionary.
- `schedule.ts`: `describeSchedule(text, locale)`; presets carry keys, not
  strings.
- `labels.ts`: `detectLang(text, fallback)`; `StepGroup` passes the UI locale as
  the fallback.

### What is not translated

Thread titles, routine names and prompts, the agent's messages, file names,
server error messages, the bot's username.

## Testing

- `i18n.test.ts`: key parity between `en` and `ru`; `t` interpolation and
  plurals.
- Component tests switch to English expectations; one test flips the store to
  `ru` and asserts a rendered string changes.
- Sidebar test: the menu opens, choosing Русский updates the store and
  localStorage.
- Playwright smoke keeps passing with English defaults.

## Open questions

1. **Should the locale default follow `navigator.language` on first visit?**
   Options: always `en` / browser language when it is `ru`. **Default: always
   `en`** — one predictable first screen; the switch is one click away.
2. **Log out in the menu or keep the session until the cookie expires?**
   **Default: include Log out** — `POST /api/auth/logout` already exists and
   there is no other way to reach it from the UI.
