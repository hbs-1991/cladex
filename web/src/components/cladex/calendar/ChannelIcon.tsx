/** A channel's mark at text size, in its brand colour.
 *
 * simple-icons dropped LinkedIn at the owner's request, so that one mark is drawn
 * here from the published geometry — the same move `brandIcons.tsx` makes for
 * Figma. A channel the calendar does not know gets a megaphone. */

import { SiInstagram, SiTelegram, SiThreads, SiX, SiYoutube } from "@icons-pack/react-simple-icons";
import { Megaphone } from "lucide-react";
import type { ComponentType } from "react";
import { channelInfo, type KnownChannel } from "./channels";

function LinkedInMark({ size, color }: { size: number; color: string }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden fill={color}>
      <path d="M20.45 20.45h-3.55v-5.57c0-1.33-.03-3.04-1.85-3.04-1.86 0-2.14 1.45-2.14 2.94v5.67H9.35V9h3.41v1.56h.05c.48-.9 1.64-1.85 3.37-1.85 3.6 0 4.27 2.37 4.27 5.46v6.28zM5.34 7.43a2.06 2.06 0 1 1 0-4.13 2.06 2.06 0 0 1 0 4.13zm1.78 13.02H3.56V9h3.56v11.45zM22.22 0H1.77C.79 0 0 .77 0 1.73v20.54C0 23.23.79 24 1.77 24h20.45c.98 0 1.78-.77 1.78-1.73V1.73C24 .77 23.2 0 22.22 0z" />
    </svg>
  );
}

type Mark = ComponentType<{ size: number; color: string; title?: string }>;
const MARKS: Record<KnownChannel, Mark> = {
  linkedin: LinkedInMark,
  telegram: SiTelegram,
  x: SiX,
  threads: SiThreads,
  instagram: SiInstagram,
  youtube: SiYoutube,
};

export function ChannelIcon({ channel, size = 14, className }: { channel: string; size?: number; className?: string }) {
  const info = channelInfo(channel);
  const M = (MARKS as Record<string, Mark | undefined>)[channel];
  // `title=""`: a simple-icons mark carries an SVG <title> with the brand name,
  // which would otherwise leak into the chip's text next to the label that names it.
  return (
    <span className={className} aria-hidden style={{ display: "inline-flex", flexShrink: 0 }}>
      {M ? <M size={size} color={info.color} title="" />
        : <Megaphone style={{ width: size, height: size, color: info.color }} strokeWidth={1.75} />}
    </span>
  );
}
