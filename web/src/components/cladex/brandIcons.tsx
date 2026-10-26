/** The logo tile of a connector: the brand mark in its own colours for the
 * services we ship, a generic symbol for the local servers, a letter for a
 * custom entry. Always on white, like an app icon, whatever the theme. */

import { SiGithub, SiHostinger, SiNotion } from "@icons-pack/react-simple-icons";
import { FolderOpen, Globe, Utensils, type LucideIcon } from "lucide-react";
import type { ComponentType } from "react";
import { cn } from "@/lib/utils";

/** Figma's mark is five coloured shapes — simple-icons keeps only silhouettes,
 * so the official geometry is drawn here. */
function FigmaMark({ size }: { size: number }) {
  return (
    <svg width={size * 0.68} height={size} viewBox="0 0 38 57" aria-hidden>
      <path fill="#1abcfe" d="M19 28.5a9.5 9.5 0 1 1 19 0 9.5 9.5 0 0 1-19 0z" />
      <path fill="#0acf83" d="M0 47.5A9.5 9.5 0 0 1 9.5 38H19v9.5a9.5 9.5 0 1 1-19 0z" />
      <path fill="#ff7262" d="M19 0v19h9.5a9.5 9.5 0 1 0 0-19H19z" />
      <path fill="#f24e1e" d="M0 9.5A9.5 9.5 0 0 0 9.5 19H19V0H9.5A9.5 9.5 0 0 0 0 9.5z" />
      <path fill="#a259ff" d="M0 28.5A9.5 9.5 0 0 0 9.5 38H19V19H9.5A9.5 9.5 0 0 0 0 28.5z" />
    </svg>
  );
}

type Mark = ComponentType<{ size: number }>;
/** Catalog names → the brand that owns them; `color="default"` is the brand's own colour. */
const BRANDS: Record<string, Mark> = {
  notion: ({ size }) => <SiNotion color="default" size={size} />,
  github: ({ size }) => <SiGithub color="default" size={size} />,
  "github-pat": ({ size }) => <SiGithub color="default" size={size} />,
  hostinger: ({ size }) => <SiHostinger color="default" size={size} />,
  figma: FigmaMark,
};
/** Local (stdio) servers have no brand; a symbol says what they do. */
const GENERIC: Record<string, LucideIcon> = { filesystem: FolderOpen, fetch: Globe, fatsecret: Utensils };

const SIZES = { md: { box: "size-11 rounded-lg", mark: 22, text: "text-base" }, lg: { box: "size-20 rounded-xl", mark: 40, text: "text-3xl" } };

export function BrandTile({ name, label, size = "md", className }: { name: string; label: string; size?: keyof typeof SIZES; className?: string }) {
  const Brand = BRANDS[name];
  const Generic = GENERIC[name];
  const s = SIZES[size];
  return (
    <span aria-hidden className={cn("flex shrink-0 items-center justify-center border border-border bg-white text-neutral-800", s.box, className)}>
      {Brand ? <Brand size={s.mark} />
        : Generic ? <Generic style={{ width: s.mark, height: s.mark }} className="text-neutral-600" strokeWidth={1.75} />
        : <span className={cn("font-semibold", s.text)}>{label.charAt(0).toUpperCase()}</span>}
    </span>
  );
}
