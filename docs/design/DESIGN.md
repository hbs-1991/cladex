# CladeX design system

Updated 2026-09-09. Visual direction: the supplied CladeX ribbon mark and outlined wordmark.

## 1. Direction summary

**A calm agent workspace with a vivid, recognizable identity.** The CladeX mark combines an orange-to-blue folded ribbon with two blue arms. Ink-colored typography and neutral reading surfaces give it room. Solid electric blue identifies primary actions in light mode; a lighter violet from the ribbon provides the corresponding contrast in dark mode.

The brand name is **CladeX**, with this exact capitalization. The supplied wordmark is custom vector artwork; do not recreate it by typing the name in a font. UI text uses Inter. The visual language carries the mark's rounded joins into compact controls and the composer, while answers remain open, unboxed prose.

This document defines the target visual system for the existing sidebar, thread, composer, tool groups, inline questions, file cards, file-preview panel, routines, menus, dialogs, and toasts. It supersedes the earlier Open Channel logo and monochrome primary-action direction. The [web UI specification](../superpowers/specs/2026-09-08-cladex-web-ui-design.md) remains the source for interaction and layout behavior where this document does not override presentation.

**Delivery boundary:** this revision updates the design specification, its [token reference](globals.css), and [brand assets](#7-logo). The favicon is connected in `web/index.html`; applying the broader visual system to the running SPA is a separate implementation change. Existing `claude-hermes` and `claude_hermes` paths and package names and the `cl-*` CSS namespace remain compatibility identifiers.

| Layer | Visual rule |
|---|---|
| Identity | Orange, coral, violet, and electric blue inside the ribbon artwork |
| Reading | Neutral canvas, ink or near-white prose, generous line-height |
| Action | Solid blue primary controls; light violet equivalent in dark mode |
| Selection | Subtle violet surface, contrasting label, and persistent check/weight |
| Feedback | Named success, warning, and error colors with a label and icon |

## 2. Color

### Brand palette

These anchors come from the SVG artwork. The SVGs retain their individual gradients and highlights; the palette is not a replacement for the logo geometry.

| Token | Hex | Role |
|---|---|---|
| `cl-brand-ink` | `#0D0D12` | Light-theme wordmark and primary reading text; dark sidebar |
| `cl-brand-orange` | `#FF9347` | Warm top of the ribbon; brand decoration only |
| `cl-brand-coral` | `#FF6837` | Warm outer ribbon face; brand decoration only |
| `cl-brand-violet` | `#6542F8` | Transition through the front ribbon face |
| `cl-brand-blue` | `#2D3CFF` | Ribbon's blue anchor; light-theme primary action |
| `cl-brand-lavender` | `#DFD4FF` | Highlight on the lower fold; brand decoration only |

`cl-brand-gradient` is a companion accent for a short, 2px decorative rule on a login or empty-thread surface: `135deg`, `#FF9347 0%`, `#FB875B 34%`, `#BC63BE 61%`, `#6542F8 82%`, `#2D3CFF 100%`. Use it at most once per surface, in addition to the logo. The actual mark uses its bundled SVG gradients, including the lower highlight and separate blue arms.

Keep gradients out of button fills, text, selected rows, status labels, and reading backgrounds. Brand orange does not mean warning, and the ribbon's red underside does not mean failure.

### Neutral scale

Achromatic grays: no blue or brown undertone. OKLCH values are converted from the listed sRGB hex values.

| Step | Hex | OKLCH | Named usage |
|---|---|---|---|
| N50 | `#FAFAFA` | `oklch(0.985104 0 0)` | Light thread canvas; dark assistant text |
| N100 | `#F5F5F5` | `oklch(0.970151 0 0)` | Light sidebar, user message, code block |
| N200 | `#E5E5E5` | `oklch(0.921906 0 0)` | Light tool-group borders; hovered sidebar rows |
| N300 | `#D4D4D4` | `oklch(0.869944 0 0)` | Dark sidebar text; secondary-action labels |
| N400 | `#A3A3A3` | `oklch(0.715474 0 0)` | Dark tool timings and composer placeholder |
| N500 | `#737373` | `oklch(0.555527 0 0)` | Composer and routine-form input borders |
| N600 | `#525252` | `oklch(0.438605 0 0)` | Light tool timings and composer placeholder |
| N700 | `#404040` | `oklch(0.371495 0 0)` | Dark menu hover and surface separators |
| N800 | `#262626` | `oklch(0.268618 0 0)` | Dark composer, tool groups, file-preview panel |
| N900 | `#171717` | `oklch(0.204627 0 0)` | Dark thread canvas |
| N950 | `#0A0A0A` | `oklch(0.144788 0 0)` | Deep neutral utility; dark sidebar uses brand ink |

`#FFFFFF` is used for light panel surfaces and filled-action text. Brand ink is separate from the achromatic gray scale.

### Semantic tokens

Use the existing [shadcn semantic names](https://ui.shadcn.com/docs/theming), without the leading `--` in this table. Generic `accent` remains the neutral hover surface; persistent selection has its own pair.

| Token | Light | Dark | Usage |
|---|---|---|---|
| `background` | `#FAFAFA` | `#171717` | Thread and routine-page canvas |
| `foreground` | `#0D0D12` | `#FAFAFA` | Assistant answers; headings |
| `card` | `#FFFFFF` | `#262626` | Composer, tool groups, file cards, preview panel |
| `card-foreground` | `#0D0D12` | `#FAFAFA` | Text on card surfaces |
| `popover` | `#FFFFFF` | `#262626` | Menus, dialogs, toasts |
| `popover-foreground` | `#0D0D12` | `#FAFAFA` | Menu, dialog, toast text |
| `primary` | `#2D3CFF` | `#A392FF` | Send; confirm answer; save routine; New chat |
| `primary-foreground` | `#FFFFFF` | `#0D0D12` | Primary-action labels and icons |
| `secondary` | `#F5F5F5` | `#262626` | Secondary choices and controls |
| `secondary-foreground` | `#0D0D12` | `#D4D4D4` | Secondary-action labels |
| `muted` | `#F5F5F5` | `#262626` | User messages, code blocks, tool output |
| `muted-foreground` | `#525252` | `#A3A3A3` | Timings, costs, placeholders, helper text |
| `accent` | `#E5E5E5` | `#404040` | Neutral hover on unselected menu/tool rows |
| `accent-foreground` | `#0D0D12` | `#FAFAFA` | Hovered-row text and icons |
| `destructive` | `#B91C1C` | `#B91C1C` | Filled Delete routine action |
| `border` | `#E5E5E5` | `#404040` | Decorative dividers and surface separators |
| `input` | `#737373` | `#737373` | Composer and form-control boundaries |
| `ring` | `#2D3CFF` | `#B8ADFF` | Keyboard focus |
| `sidebar` | `#F5F5F5` | `#0D0D12` | Sidebar surface |
| `sidebar-foreground` | `#0D0D12` | `#D4D4D4` | Conversation and routine names |
| `sidebar-primary` | `#2D3CFF` | `#A392FF` | New chat action |
| `sidebar-primary-foreground` | `#FFFFFF` | `#0D0D12` | New chat label and icon |
| `sidebar-accent` | `#EEECFF` | `#30294D` | Selected conversation; retain on hover |
| `sidebar-accent-foreground` | `#2126FF` | `#DED8FF` | Selected conversation label and icon |
| `sidebar-border` | `#E5E5E5` | `#262626` | Sidebar separators |
| `sidebar-ring` | `#2D3CFF` | `#B8ADFF` | Sidebar keyboard focus |

**User-message text uses `foreground`; metadata uses `muted-foreground`.** On dark `accent` surfaces, metadata uses `accent-foreground`. On a selected surface, both label and metadata use its selected foreground. Selected rows retain that surface on hover.

### State and interaction tokens

`cl-*` properties are application-owned CladeX tokens. The prefix is retained for compatibility.

| Token | Light | Dark | Usage |
|---|---|---|---|
| `cl-signal` | `#2D3CFF` | `#B8ADFF` | Links, running indicators, focus |
| `cl-selected` | `#EEECFF` | `#30294D` | Selected question choice, tab, or radio item |
| `cl-selected-foreground` | `#2126FF` | `#DED8FF` | Selected text, icon, and check |
| `cl-success` | `#DCFCE7` | `#142B1B` | Completed status background |
| `cl-success-foreground` | `#166534` | `#86EFAC` | Completed label and check |
| `cl-warning` | `#FEF3C7` | `#35280D` | Warning or approval-needed background |
| `cl-warning-foreground` | `#92400E` | `#FCD34D` | Warning label and triangle |
| `cl-error` | `#FEE2E2` | `#3B1515` | Failed status background |
| `cl-error-foreground` | `#991B1B` | `#FCA5A5` | Failure text; invalid-input border |
| `cl-on-destructive` | `#FFFFFF` | `#FFFFFF` | Destructive-action label |
| `cl-primary-hover` | `#2126FF` | `#C4B8FF` | Primary hover and pressed state |

The modal backdrop uses `cl-scrim = rgb(0 0 0 / 40%)` in light mode and `rgb(0 0 0 / 64%)` in dark mode.

| Interaction state | Rule |
|---|---|
| Hover | Neutral `accent` for ordinary rows; `cl-primary-hover` for primary actions |
| Pressed | Retain hover color; no scale or extra shadow |
| Selected | `cl-selected` pair, weight 600, and a check/indicator where applicable; navigation exposes its current state |
| Focus | Opaque 2px `ring`, 2px offset from the control; never `ring/50` |
| Disabled | Opacity `0.5` on the disabled control; helper text stays fully opaque |
| Running | `cl-signal` dot or spinner with an explicit tool/status label |
| Completed / warning / failed | Status label plus corresponding icon; surrounding surface stays neutral |

### Applying the identity to existing components

| Surface | Application |
|---|---|
| Sidebar | Full CladeX logo at the top; selected conversation uses the sidebar selection pair; ordinary row hover stays neutral |
| Composer | Neutral card with a clear input boundary; solid primary Send button; neutral attachment, model, and voice controls |
| Assistant answer | Open prose without a colored bubble; signal-colored underlined links; fenced code is syntax-highlighted on a neutral code surface |
| User message | Muted neutral background; no brand gradient |
| Tool group | Neutral rows and disclosure; only the running indicator receives signal color |
| Inline question | Selected choice uses the selection pair and a check; confirmation uses primary |
| File card / preview | Neutral surfaces; file type icons use normal text color; links use signal |
| Routines / forms | One principal primary action per form or action group; destructive actions remain red |
| User menu | Neutral popover; selected theme/language uses the selection pair and radio/check state |
| Login / empty thread | Full logo or standalone mark, neutral heading; optional short brand rule, no full-page gradient |

### Syntax highlighting

Fenced code and text file previews are the **only** multi-hue surface in the
product, and the one place where color does not come from the tokens above. Code
is read, not skimmed, and an unhighlighted screenful of TypeScript or YAML costs
the reader more than the palette costs the interface.

| Rule | Value |
|---|---|
| Highlighter | Shiki, through `react-shiki` |
| Themes | `github-light-default` / `github-dark-default` |
| Theme switch | Tokens are emitted as `light-dark()`, so they follow `color-scheme`; no re-tokenizing, no second copy of the block |
| While streaming | No highlighting — the same plain code in the same container, so settling is a color change and not a layout shift |
| Unknown language | Renders as plaintext; never an error and never a blank block |

The palette stops at the tokens. The code surface itself — border, background,
radius, padding, 13/20px mono — stays on the tokens in this document, syntax
colors never carry meaning about state, and highlighting never spreads to inline
code, tool rows, diffs, or any other surface. Syntax tokens are also not a use of
`cl-signal`: the blue that appears in them belongs to the code theme.

### Computed contrast

Ratios use WCAG sRGB linearization and `(Llighter + 0.05) / (Ldarker + 0.05)`. Values below are computed from the specified opaque pairs and displayed to two decimals; thresholds are checked before rounding. This system requires **4.5:1 for normal text** and **3:1 for essential control boundaries and state indicators**. See [WCAG text contrast](https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum.html) and [non-text contrast](https://www.w3.org/WAI/WCAG22/Understanding/non-text-contrast.html).

| Text / surface | Light: foreground / background → ratio | Dark: foreground / background → ratio |
|---|---|---|
| Assistant / thread | `#0D0D12 / #FAFAFA` → **18.57:1** | `#FAFAFA / #171717` → **17.18:1** |
| Panel, composer, toast | `#0D0D12 / #FFFFFF` → **19.38:1** | `#FAFAFA / #262626` → **14.50:1** |
| Sidebar | `#0D0D12 / #F5F5F5` → **17.78:1** | `#D4D4D4 / #0D0D12` → **13.08:1** |
| User message / code | `#0D0D12 / #F5F5F5` → **17.78:1** | `#FAFAFA / #262626` → **14.50:1** |
| Muted text / thread | `#525252 / #FAFAFA` → **7.49:1** | `#A3A3A3 / #171717` → **7.11:1** |
| Muted text / supporting surface | `#525252 / #F5F5F5` → **7.17:1** | `#A3A3A3 / #262626` → **6.00:1** |
| Primary action | `#FFFFFF / #2D3CFF` → **6.56:1** | `#0D0D12 / #A392FF` → **7.49:1** |
| Primary hover/press | `#FFFFFF / #2126FF` → **7.54:1** | `#0D0D12 / #C4B8FF` → **10.75:1** |
| Secondary action | `#0D0D12 / #F5F5F5` → **17.78:1** | `#D4D4D4 / #262626` → **10.21:1** |
| Hovered menu/tool row | `#0D0D12 / #E5E5E5` → **15.39:1** | `#FAFAFA / #404040` → **9.93:1** |
| Selected sidebar row | `#2126FF / #EEECFF` → **6.49:1** | `#DED8FF / #30294D` → **9.92:1** |
| Selected question / radio item | `#2126FF / #EEECFF` → **6.49:1** | `#DED8FF / #30294D` → **9.92:1** |
| Link / thread | `#2D3CFF / #FAFAFA` → **6.28:1** | `#B8ADFF / #171717` → **8.93:1** |
| Link / file preview | `#2D3CFF / #FFFFFF` → **6.56:1** | `#B8ADFF / #262626` → **7.54:1** |
| Link / muted surface | `#2D3CFF / #F5F5F5` → **6.02:1** | `#B8ADFF / #262626` → **7.54:1** |
| Success badge | `#166534 / #DCFCE7` → **6.49:1** | `#86EFAC / #142B1B` → **10.75:1** |
| Warning badge | `#92400E / #FEF3C7` → **6.37:1** | `#FCD34D / #35280D` → **9.97:1** |
| Error badge | `#991B1B / #FEE2E2` → **6.80:1** | `#FCA5A5 / #3B1515` → **8.48:1** |
| Inline form error | `#991B1B / #FFFFFF` → **8.31:1** | `#FCA5A5 / #262626` → **7.97:1** |
| Destructive action | `#FFFFFF / #B91C1C` → **6.47:1** | `#FFFFFF / #B91C1C` → **6.47:1** |

| Non-text pair | Light | Dark |
|---|---|---|
| Input boundary / composer | `#737373 / #FFFFFF` → **4.74:1** | `#737373 / #262626` → **3.19:1** |
| Focus ring / hover surface | `#2D3CFF / #E5E5E5` → **5.21:1** | `#B8ADFF / #404040` → **5.16:1** |
| Focus ring / selection surface | `#2D3CFF / #EEECFF` → **5.65:1** | `#B8ADFF / #30294D` → **6.75:1** |
| Primary button / card | `#2D3CFF / #FFFFFF` → **6.56:1** | `#A392FF / #262626` → **5.85:1** |
| Running indicator / tool row | `#2D3CFF / #FFFFFF` → **6.56:1** | `#B8ADFF / #262626` → **7.54:1** |
| Selection indicator / selected fill | `#2126FF / #EEECFF` → **6.49:1** | `#DED8FF / #30294D` → **9.92:1** |

Decorative separators are not the sole control boundary. Selection does not rely on its low-contrast tinted fill alone: keep the contrasting check/indicator and selected text. The artwork's orange and pale gradient stops are not approved text or status colors.

## 3. Typography

| Family | Load | Usage | Verified license |
|---|---|---|---|
| **Inter** | Normal 400, 500, 600; italic 400, 600 | Sidebar, assistant prose, composer, routines, dialogs | [SIL OFL 1.1](https://raw.githubusercontent.com/rsms/inter/master/LICENSE.txt) |
| **JetBrains Mono** | Normal 400, 500 | Code, paths, tool input/output | [SIL OFL 1.1](https://raw.githubusercontent.com/JetBrains/JetBrainsMono/master/OFL.txt) |

Load fonts through the self-hosted [font entry point](../../web/src/styles/fonts.css); do not add a Google Fonts request. Its current Inter import registers the normal family as `Inter Variable`; use that name first in the CSS stack. When integrating the specified italic styles, also load the package's `@fontsource-variable/inter/italic.css` entry. The outlined CladeX wordmark is separate from UI typography. Use system sans/mono fallbacks. Disable code ligatures. Use tabular numerals for timings and costs. No display font.

| Scale / Tailwind name | Size | Line-height | Letter-spacing | Usage |
|---|---:|---:|---:|---|
| Metadata / `xs` | 12px | 16px | `0.01em` | Tool duration, file size, routine timestamps |
| Code / `code` | 13px | 20px | `0` | Markdown code; tool payloads |
| UI / `sm` | 14px | 20px | `0` | Sidebar, buttons, tool rows, table cells |
| Body / `base` | 16px | 26px | `0` | Assistant prose, composer, inline questions |
| Heading 3 / `lg` | 18px | 26px | `-0.01em` | Assistant subsections |
| Heading 2 / `xl` | 20px | 28px | `-0.015em` | File-preview title; section heading |
| Heading 1 / `2xl` | 24px | 32px | `-0.02em` | Routine-page title; assistant H1 |

Weights: **400** reading, **500** controls, **600** headings/selection/emphasis.

### Assistant markdown rules

| Content | Rule |
|---|---|
| Headings | H1→24, H2→20, H3→18, H4–H6→16px; weight 600. Preserve semantic heading levels. Space 32px before / 12px after |
| Paragraphs | 16/26px; 16px between paragraphs; no paragraph containers |
| Lists | 24px indent; 4px between items; 8px around nested lists. Keep native bullets and numbering |
| Inline code | Mono 13px; `muted` background, `foreground` text; 2px vertical / 4px horizontal padding; 4px radius |
| Fenced code | Mono 13/20px; 16px padding; 8px radius. Horizontal scrolling; syntax colors per §2 "Syntax highlighting" |
| Tables | 14/20px; cells 8px vertical / 12px horizontal; 1px row separators; horizontal scrolling within the message |
| Links | Signal color; always underlined, 1px thickness / 3px offset. Hover retains color |
| Blockquotes | Normal body text; 2px neutral left rule; 16px inset; no italic-by-default |
| Long strings | Wrap prose URLs/paths; preserve code whitespace. Never widen the page |
| File-preview markdown | Reuse these exact rules |

## 4. Layout tokens

Dimensions below are sizing rules for the existing surfaces.

### Spacing

Base unit: **4px**. Only these steps are approved.

| Step | Value | Usage |
|---|---:|---|
| `0` | 0 | Unboxed assistant text |
| `0.5` | 2px | Inline-code vertical padding |
| `1` | 4px | List-item gap; compact control inset |
| `2` | 8px | Tool icon/label gap; question-choice gap |
| `3` | 12px | Tool-row padding; heading-to-content gap |
| `4` | 16px | Composer and code-block padding |
| `6` | 24px | Desktop thread gutters; routine section padding |
| `8` | 32px | Gap between conversation turns |
| `12` | 48px | Empty-thread top spacing |

### Shape and elevation

| Token | Value | Usage |
|---|---|---|
| `radius-none` | 0 | Thread canvas; docked file-preview panel |
| `radius-sm` | 4px | Inline code; keyboard hints |
| `radius-md` | 6px | Sidebar rows; buttons |
| `radius-lg` / base `radius` | 8px | Tool groups; file cards; code blocks |
| `radius-xl` | 12px | Composer; dialogs; toasts |
| `radius-full` | 9999px | Status dots only |
| `cl-border-width` | 1px | Surface boundaries and table separators |
| `cl-border-strong` | 2px | Focus outline; blockquote rule |
| `cl-shadow-none` | `none` | Sidebar, thread, composer, docked panel |
| `cl-shadow-floating` | `0 8px 24px rgb(0 0 0 / 10%)` light; `/36%` dark | Menus, dialogs, toasts, overlay panels |

### Layering

| Token | Z-index | Usage |
|---|---:|---|
| `cl-z-base` | 0 | Thread messages; routine content |
| `cl-z-sticky` | 10 | Thread header and composer |
| `cl-z-panel` | 20 | Docked file-preview panel |
| `cl-z-scrim` | 40 | Dialog/drawer backdrop |
| `cl-z-dialog` | 50 | Routine dialog; mobile sidebar/file panel |
| `cl-z-popover` | 60 | Composer menu; dialog field menus |
| `cl-z-toast` | 70 | Toasts |
| `cl-z-tooltip` | 80 | Tool/action tooltips |

### Widths and breakpoints

| Token | Value | Usage |
|---|---:|---|
| `cl-sidebar-width` | 256px | Expanded desktop sidebar |
| `cl-sidebar-collapsed` | 48px | Collapsed sidebar |
| `cl-sidebar-drawer` | 288px maximum | Mobile sidebar; clamp to viewport minus 32px |
| `cl-thread-max` | 768px | Thread and composer shared maximum width |
| `cl-thread-min` | 512px preferred | Comfortable thread width beside file preview; never enforce as overflow |
| `cl-panel-width` | 432px | Docked file-preview panel |
| `cl-control-height` | 36px | Desktop composer/routine controls |
| `cl-touch-target` | 44px | Touch targets in composer, sidebar, dialogs |

| Breakpoint | Value | Existing-surface behavior |
|---|---:|---|
| Base | Below 640px | 16px thread gutters; sidebar and file preview as overlays |
| `sm` | 640px | 24px thread gutters |
| `md` | 768px | Sidebar may dock; file preview remains an overlay |
| `lg` | 1024px | Full sidebar; thread grows toward 768px |
| `xl` | 1280px | File preview may dock if thread retains ≥512px; otherwise overlay |

Use `min-width: 0` on flexible columns. Keep the composer aligned with the thread. At coarse-pointer sizes, use 44px targets and 16px form text.

For Telegram later: map its light/dark preference to these two themes; retain CladeX colors. Respect Telegram’s reported safe areas and viewport when positioning the composer. [Telegram Mini Apps](https://core.telegram.org/bots/webapps)

## 5. Motion

| Token | Value | Usage |
|---|---|---|
| `cl-motion-instant` | 0ms | Streaming text insertion |
| `cl-motion-fast` | 120ms | Control hover; panel/toast exit |
| `cl-motion-standard` | 180ms | Tool-group expansion; toast entry |
| `cl-motion-slow` | 240ms | File-preview panel entry |
| `cl-motion-loop` | 1200ms | Every looping animation: dot pulse, header spin, label shimmer |
| `cl-ease-standard` | `cubic-bezier(0.2, 0, 0, 1)` | Tool-group and control transitions |
| `cl-ease-enter` | `cubic-bezier(0, 0, 0.2, 1)` | Panel and toast entry |
| `cl-ease-exit` | `cubic-bezier(0.4, 0, 1, 1)` | Panel and toast exit |

| Animation | Behavior | Reduced motion |
|---|---|---|
| **Tool-group expand** | Measured height 0↔content height, 180ms; chevron rotates 90°. Preserve scroll position | Immediate state change |
| **Streaming text** | Append available content immediately. No character fade, stagger, or typewriter replay | Same |
| **Panel open** | Reserve docked width immediately; panel opacity 0→1 and translateX 8→0px over 240ms. Exit 120ms | Immediate; no slide or animated column resize |
| **Running mark** | One mark per level, one tempo (`cl-motion-loop`), and it **names the wait**: at the head of the work (the step-group header, or the same line standing in for it before the group exists) the running tool's own icon beside a shimmering label, and a spinner in its place only while no call is in flight — turning means "between calls, no tool picked yet", never mere decoration; a `cl-signal` dot pulsing opacity 0.45↔1 on the live run or tool row inside it. The head keeps the mark for as long as the turn runs, not only while one call is in flight, and never names work that already returned. No bare mark without words, no second shimmer under a shimmering label, no second tempo, no third idiom | Static dot and label; no spin |
| **Toast** | Opacity 0→1, translateY 4→0px over 180ms; exit 120ms | Immediate; retain normal reading time |

Toast reading time is **5 seconds minimum**, paused on hover/focus. Errors and toasts containing actions remain until dismissed. Timing must not depend on an animation finishing.

## 6. Iconography

Use **Lucide**, with rounded caps/joins, `currentColor`, and **1.75px absolute stroke width**. Lucide supports `strokeWidth` and `absoluteStrokeWidth`. [Official implementation](https://raw.githubusercontent.com/lucide-icons/lucide/main/packages/lucide-react/src/Icon.ts)

| Size token | Value | Usage |
|---|---:|---|
| `cl-icon-sm` | 16px | Tool rows, metadata, compact sidebar actions |
| `cl-icon-md` | 20px | Sidebar navigation; composer actions |
| `cl-icon-lg` | 24px | File-preview empty state |
| `cl-icon-stroke` | 1.75 | Stroke width across those surfaces |

| Tool | Lucide icon |
|---|---|
| Read | `FileText` |
| Glob | `FolderSearch` |
| Grep | `Search` |
| Write | `FilePenLine` |
| Edit | `Pencil` |
| Bash | `Terminal` |
| WebSearch | `Search` |
| WebFetch | `Globe` |
| Agent | `Network` |
| TodoWrite | `ListChecks` |
| Skill | `BookOpen` |
| MCP tools | `Plug`; readable label includes server/tool name |

Use a chevron for disclosure and a separate status indicator. Tool categories never get individual colors. Hide decorative icons from assistive technology; label icon-only controls.

## 7. Logo

The supplied **CladeX ribbon** is the selected identity. Its warm left loop, diamond-shaped aperture, blue front fold, and two separate blue arms form one mark. Preserve all of these parts and their relative spacing.

![CladeX full logo](logo/cladex-logo.svg)

| Asset | Purpose | Geometry / sizes |
|---|---|---|
| [Standalone mark](logo/cladex-icon.svg) | Collapsed sidebar, small brand placement, empty-thread identity | `180 328 890 640` |
| [Full logo](logo/cladex-logo.svg) | Light-theme sidebar, login, product identification | `120 390 1208 320` |
| [Full logo, dark background](logo/cladex-logo-dark.svg) | Dark-theme sidebar and login | `120 390 1208 320` |
| [Favicon SVG](../../web/public/favicon.svg) | Browser tab; square, transparent canvas | viewBox `180 202 890 890` |
| [Favicon ICO](../../web/public/favicon.ico) | Raster fallback for browser tabs | 16×16, 32×32, 48×48 frames |

The two original SVGs are vector reconstructions of the supplied references, with smooth paths, native SVG gradients, transparent backgrounds, and outlined letters. The dark-background variant changes only the wordmark from `#0D0D12` to `#FAFAFA`; the mark retains the same colors. These repository files are the source assets for this design. No PNG is embedded, and no font is required to render the wordmark.

### Sizing and placement

| Placement | Size and rule |
|---|---|
| Expanded sidebar | Full logo 144px wide, automatic height (about 38px); align within the sidebar's 16px gutter |
| Collapsed sidebar | Mark 32px wide, automatic height, centered in the 48px rail |
| Mobile header | Mark 32px wide in a 44px target; use the full logo only when there is room for at least 120px width |
| Login | Full logo 240px wide, capped by the available content width |
| Empty thread | Standalone mark 64px wide above a neutral heading; omit duplicate branding if the header already occupies that position |
| Minimum regular display | Full logo 120px wide; standalone mark 24px wide. Use the mark when the full name would be too small |
| Favicon | Use the committed square SVG or ICO fallback from `web/public`; both are linked in `web/index.html`. Preserve the complete mark and its aspect ratio |
| Clear space | At least one quarter of the displayed mark's height around its visible bounds; 4px minimum. Preserve the supplied mark-to-wordmark gap |

The favicon is the small-format exception to regular-display size and clear-space minima. Its square viewBox adds vertical space around the unchanged mark, with no background tile or wordmark. Standalone [16px](../../web/public/favicon-16x16.png), [32px](../../web/public/favicon-32x32.png), and [48px](../../web/public/favicon-48x48.png) PNG exports match the ICO frames. Inspect these at actual size on both light and dark backgrounds whenever the source mark changes.

Do not crop the blue arms, stretch the mark into a square, add a permanent background tile, change the gradient direction, trace over its paths, or reconstruct the wordmark in Inter. Do not animate, rotate, add a glow, or apply a CSS color filter. Use the appropriate wordmark variant for the theme; use the same standalone mark in both themes.

When used as an image, supply `alt="CladeX"`. A decorative duplicate next to the same accessible brand label has empty alt text. When the brand links to the main page, label the link clearly and apply the normal keyboard-focus treatment to its control area. Preserve viewBox and `height: auto`; an icon's rectangular artwork must retain its proportions even inside a square slot. When inlining several SVG instances, prefix their gradient, clip, and title IDs per instance to avoid collisions.

## 8. Token file

The block below and [globals.css](globals.css) are the synchronized **target token reference** for the new identity. The application's current [stylesheet](../../web/src/styles/globals.css) is implementation evidence and may still contain the previous theme. Integrating this reference must preserve any application-specific utilities and existing behavior.

Use the existing Tailwind v4 and shadcn setup. [Tailwind `@theme inline`](https://tailwindcss.com/docs/theme#referencing-other-variables) maps semantic variables to utilities. `cl-*` properties are application tokens, including the companion decorative gradient. Existing assistant-ui primitives are styled through the application's classes; no component API is introduced.

Font families are provided by the existing self-hosted font imports in `web/src/styles/fonts.css`; load that entry point once when integrating. The token reference does not contact a font CDN.

```css
@import "tailwindcss";

@custom-variant dark (&:where(.dark, .dark *));

:root {
  color-scheme: light;

  /* Brand primitives: shared by themes; full logo uses its own SVG gradients. */
  --cl-brand-ink: #0d0d12;
  --cl-brand-orange: #ff9347;
  --cl-brand-coral: #ff6837;
  --cl-brand-violet: #6542f8;
  --cl-brand-blue: #2d3cff;
  --cl-brand-lavender: #dfd4ff;
  --cl-brand-gradient: linear-gradient(135deg, #ff9347 0%, #fb875b 34%, #bc63be 61%, #6542f8 82%, #2d3cff 100%);


  /* Neutral primitives: usages listed in the neutral-scale table. */
  --cl-neutral-50: #fafafa;
  --cl-neutral-100: #f5f5f5;
  --cl-neutral-200: #e5e5e5;
  --cl-neutral-300: #d4d4d4;
  --cl-neutral-400: #a3a3a3;
  --cl-neutral-500: #737373;
  --cl-neutral-600: #525252;
  --cl-neutral-700: #404040;
  --cl-neutral-800: #262626;
  --cl-neutral-900: #171717;
  --cl-neutral-950: #0a0a0a;

  /* Thread, composer, tool groups, file preview, dialogs, toasts. */
  --background: #fafafa;
  --foreground: #0d0d12;
  --card: #ffffff;
  --card-foreground: #0d0d12;
  --popover: #ffffff;
  --popover-foreground: #0d0d12;

  --primary: #2d3cff;
  --primary-foreground: #ffffff;
  --secondary: #f5f5f5;
  --secondary-foreground: #0d0d12;
  --muted: #f5f5f5;
  --muted-foreground: #525252;
  --accent: #e5e5e5;
  --accent-foreground: #0d0d12;
  --destructive: #b91c1c;
  --border: #e5e5e5;
  --input: #737373;
  --ring: #2d3cff;

  /* Sidebar and conversation/routine navigation. */
  --sidebar: #f5f5f5;
  --sidebar-foreground: #0d0d12;
  --sidebar-primary: #2d3cff;
  --sidebar-primary-foreground: #ffffff;
  --sidebar-accent: #eeecff;
  --sidebar-accent-foreground: #2126ff;
  --sidebar-border: #e5e5e5;
  --sidebar-ring: #2d3cff;

  /* CladeX extensions: status badges, interaction, modal backdrop. */
  --cl-selected: #eeecff;
  --cl-selected-foreground: #2126ff;
  --cl-signal: #2d3cff;
  --cl-success: #dcfce7;
  --cl-success-foreground: #166534;
  --cl-warning: #fef3c7;
  --cl-warning-foreground: #92400e;
  --cl-error: #fee2e2;
  --cl-error-foreground: #991b1b;
  --cl-on-destructive: #ffffff;
  --cl-primary-hover: #2126ff;
  --cl-scrim: rgb(0 0 0 / 40%);
  --cl-disabled-opacity: 0.5;

  /* Thread/composer spacing; base corner radius is 8px. */
  --cl-space-unit: 0.25rem;
  --radius: 0.5rem;
  --cl-border-width: 1px;
  --cl-border-strong: 2px;

  /* Only floating surfaces receive a shadow. */
  --cl-shadow-none: none;
  --cl-shadow-floating: 0 8px 24px rgb(0 0 0 / 10%);

  /* Existing columns and interactive controls. */
  --cl-sidebar-width: 16rem;
  --cl-sidebar-collapsed: 3rem;
  --cl-sidebar-drawer: 18rem;
  --cl-thread-max: 48rem;
  --cl-thread-min: 32rem;
  --cl-panel-width: 27rem;
  --cl-control-height: 2.25rem;
  --cl-touch-target: 2.75rem;

  /* Layer ordering: thread through floating feedback. */
  --cl-z-base: 0;
  --cl-z-sticky: 10;
  --cl-z-panel: 20;
  --cl-z-scrim: 40;
  --cl-z-dialog: 50;
  --cl-z-popover: 60;
  --cl-z-toast: 70;
  --cl-z-tooltip: 80;

  /* Tool group, stream, panel, thinking indicator, toast. */
  --cl-motion-instant: 0ms;
  --cl-motion-fast: 120ms;
  --cl-motion-standard: 180ms;
  --cl-motion-slow: 240ms;
  --cl-motion-loop: 1200ms;
  --cl-ease-standard: cubic-bezier(0.2, 0, 0, 1);
  --cl-ease-enter: cubic-bezier(0, 0, 0.2, 1);
  --cl-ease-exit: cubic-bezier(0.4, 0, 1, 1);

  /* Tool rows, sidebar/composer actions, preview empty state. */
  --cl-icon-sm: 1rem;
  --cl-icon-md: 1.25rem;
  --cl-icon-lg: 1.5rem;
  --cl-icon-stroke: 1.75;
}

.dark {
  color-scheme: dark;

  --background: #171717;
  --foreground: #fafafa;
  --card: #262626;
  --card-foreground: #fafafa;
  --popover: #262626;
  --popover-foreground: #fafafa;

  --primary: #a392ff;
  --primary-foreground: #0d0d12;
  --secondary: #262626;
  --secondary-foreground: #d4d4d4;
  --muted: #262626;
  --muted-foreground: #a3a3a3;
  --accent: #404040;
  --accent-foreground: #fafafa;
  --destructive: #b91c1c;
  --border: #404040;
  --input: #737373;
  --ring: #b8adff;

  --sidebar: #0d0d12;
  --sidebar-foreground: #d4d4d4;
  --sidebar-primary: #a392ff;
  --sidebar-primary-foreground: #0d0d12;
  --sidebar-accent: #30294d;
  --sidebar-accent-foreground: #ded8ff;
  --sidebar-border: #262626;
  --sidebar-ring: #b8adff;

  --cl-selected: #30294d;
  --cl-selected-foreground: #ded8ff;
  --cl-signal: #b8adff;
  --cl-success: #142b1b;
  --cl-success-foreground: #86efac;
  --cl-warning: #35280d;
  --cl-warning-foreground: #fcd34d;
  --cl-error: #3b1515;
  --cl-error-foreground: #fca5a5;
  --cl-on-destructive: #ffffff;
  --cl-primary-hover: #c4b8ff;
  --cl-scrim: rgb(0 0 0 / 64%);
  --cl-shadow-floating: 0 8px 24px rgb(0 0 0 / 36%);
}

@theme inline {
  /* shadcn semantic colors. */
  --color-background: var(--background);
  --color-foreground: var(--foreground);
  --color-card: var(--card);
  --color-card-foreground: var(--card-foreground);
  --color-popover: var(--popover);
  --color-popover-foreground: var(--popover-foreground);
  --color-primary: var(--primary);
  --color-primary-foreground: var(--primary-foreground);
  --color-secondary: var(--secondary);
  --color-secondary-foreground: var(--secondary-foreground);
  --color-muted: var(--muted);
  --color-muted-foreground: var(--muted-foreground);
  --color-accent: var(--accent);
  --color-accent-foreground: var(--accent-foreground);
  --color-destructive: var(--destructive);
  --color-border: var(--border);
  --color-input: var(--input);
  --color-ring: var(--ring);
  --color-sidebar: var(--sidebar);
  --color-sidebar-foreground: var(--sidebar-foreground);
  --color-sidebar-primary: var(--sidebar-primary);
  --color-sidebar-primary-foreground: var(--sidebar-primary-foreground);
  --color-sidebar-accent: var(--sidebar-accent);
  --color-sidebar-accent-foreground: var(--sidebar-accent-foreground);
  --color-sidebar-border: var(--sidebar-border);
  --color-sidebar-ring: var(--sidebar-ring);

  /* CladeX utilities; these are not component APIs. */
  --color-brand-ink: var(--cl-brand-ink);
  --color-brand-orange: var(--cl-brand-orange);
  --color-brand-coral: var(--cl-brand-coral);
  --color-brand-violet: var(--cl-brand-violet);
  --color-brand-blue: var(--cl-brand-blue);
  --color-brand-lavender: var(--cl-brand-lavender);
  --color-selected: var(--cl-selected);
  --color-selected-foreground: var(--cl-selected-foreground);
  --color-signal: var(--cl-signal);
  --color-success: var(--cl-success);
  --color-success-foreground: var(--cl-success-foreground);
  --color-warning: var(--cl-warning);
  --color-warning-foreground: var(--cl-warning-foreground);
  --color-error: var(--cl-error);
  --color-error-foreground: var(--cl-error-foreground);
  --color-on-destructive: var(--cl-on-destructive);
  --color-primary-hover: var(--cl-primary-hover);
  --color-scrim: var(--cl-scrim);

  /* Prose/UI and tool-output typography. */
  --font-sans: "Inter Variable", "Inter", ui-sans-serif, system-ui, sans-serif;
  --font-mono: "JetBrains Mono", ui-monospace, monospace;

  --text-xs: 0.75rem;
  --text-xs--line-height: 1rem;
  --text-xs--letter-spacing: 0.01em;

  --text-code: 0.8125rem;
  --text-code--line-height: 1.25rem;
  --text-code--letter-spacing: 0;

  --text-sm: 0.875rem;
  --text-sm--line-height: 1.25rem;
  --text-sm--letter-spacing: 0;

  --text-base: 1rem;
  --text-base--line-height: 1.625rem;
  --text-base--letter-spacing: 0;

  --text-lg: 1.125rem;
  --text-lg--line-height: 1.625rem;
  --text-lg--letter-spacing: -0.01em;

  --text-xl: 1.25rem;
  --text-xl--line-height: 1.75rem;
  --text-xl--letter-spacing: -0.015em;

  --text-2xl: 1.5rem;
  --text-2xl--line-height: 2rem;
  --text-2xl--letter-spacing: -0.02em;

  /* Approved numeric spacing: 0, 0.5, 1, 2, 3, 4, 6, 8, 12. */
  --spacing: var(--cl-space-unit);
  --spacing-sidebar: var(--cl-sidebar-width);
  --spacing-sidebar-collapsed: var(--cl-sidebar-collapsed);
  --spacing-sidebar-drawer: var(--cl-sidebar-drawer);
  --spacing-panel: var(--cl-panel-width);
  --spacing-control: var(--cl-control-height);
  --spacing-touch: var(--cl-touch-target);
  --spacing-icon-sm: var(--cl-icon-sm);
  --spacing-icon-md: var(--cl-icon-md);
  --spacing-icon-lg: var(--cl-icon-lg);
  --container-thread: var(--cl-thread-max);

  --radius-none: 0;
  --radius-sm: calc(var(--radius) - 0.25rem);
  --radius-md: calc(var(--radius) - 0.125rem);
  --radius-lg: var(--radius);
  --radius-xl: calc(var(--radius) + 0.25rem);
  --radius-full: 9999px;

  --shadow-none: var(--cl-shadow-none);
  --shadow-floating: var(--cl-shadow-floating);

  /* Literal breakpoints; do not replace with runtime var() values. */
  --breakpoint-sm: 40rem;
  --breakpoint-md: 48rem;
  --breakpoint-lg: 64rem;
  --breakpoint-xl: 80rem;

  --ease-ui: var(--cl-ease-standard);
  --ease-enter: var(--cl-ease-enter);
  --ease-exit: var(--cl-ease-exit);
  --animate-thinking: cl-thinking var(--cl-motion-loop) ease-in-out infinite;
}

@keyframes cl-thinking {
  0%, 100% { opacity: 0.45; }
  50% { opacity: 1; }
}

@layer base {
  * {
    border-color: var(--border);
  }

  body {
    margin: 0;
    background: var(--background);
    color: var(--foreground);
    font-family: "Inter Variable", "Inter", ui-sans-serif, system-ui, sans-serif;
    font-size: 0.875rem;
    line-height: 1.25rem;
    font-synthesis: none;
  }

  code, pre, kbd, samp {
    font-family: "JetBrains Mono", ui-monospace, monospace;
    font-variant-ligatures: none;
  }

  :focus-visible {
    outline: var(--cl-border-strong) solid var(--ring);
    outline-offset: 2px;
  }
}

@media (prefers-reduced-motion: reduce) {
  :root, .dark {
    --cl-motion-instant: 0ms;
    --cl-motion-fast: 0ms;
    --cl-motion-standard: 0ms;
    --cl-motion-slow: 0ms;
    --cl-motion-loop: 0ms;
  }

  *, *::before, *::after {
    animation: none !important;
    transition: none !important;
    scroll-behavior: auto !important;
  }
}
```

## 9. Do-not list

1. Do not use Open Channel, Folded C, or Relay for current CladeX branding.
2. Do not redraw, distort, animate, filter, or add a tile behind the CladeX artwork; only the documented dark wordmark substitution is allowed.
3. Do not apply gradients to controls, body text, message backgrounds, or status badges. The logo and optional short identity rule are the approved gradient uses.
4. Do not use brand orange/coral for warnings or errors; use the semantic status pairs and explicit labels.
5. Do not use electric blue as the dark-theme action fill; use the specified light-violet primary pair and its matching hover state.
6. Do not add glow, glass effects, background textures, or shadows on docked surfaces. Floating surfaces use the documented shadow.
7. Do not box ordinary assistant paragraphs or assign separate colors to tool categories, models, or MCP providers. Syntax tokens inside code are the documented exception (§2).
8. Do not reduce opacity on readable metadata/placeholders or communicate focus, selection, or status through color alone.
9. Do not add typewriter replay, word fades, bouncing indicators, or motion that ignores reduced-motion preferences.
10. Do not replace the outlined wordmark with a font, add unrelated font families, introduce pill-shaped cards/buttons, or rename internal compatibility identifiers as part of a visual change.
