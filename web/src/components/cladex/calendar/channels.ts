/** The publishing channels the calendar knows by face: a label, a brand colour
 * and the formats the item dialog offers for each.
 *
 * The list mirrors the server's `content_channels` default. A deploy that adds a
 * channel of its own still works — the server is the validator — and its items
 * are drawn with the generic mark and their raw name. Brand names are proper
 * nouns, so they stay out of the dictionaries. */

export const CHANNELS = ["linkedin", "telegram", "x", "threads", "instagram", "youtube"] as const;
export type KnownChannel = (typeof CHANNELS)[number];

/** `color` paints the chip's edge and the mark. X and Threads are black marks,
 *  so they follow the text colour instead — a black edge would vanish in the
 *  dark theme. */
type ChannelInfo = { label: string; color: string; formats: readonly string[] };

const INFO: Record<KnownChannel, ChannelInfo> = {
  linkedin: { label: "LinkedIn", color: "#0A66C2", formats: ["post", "article", "carousel", "video"] },
  telegram: { label: "Telegram", color: "#26A5E4", formats: ["post", "photo", "video"] },
  x: { label: "X", color: "var(--foreground)", formats: ["post", "thread", "video"] },
  threads: { label: "Threads", color: "var(--foreground)", formats: ["post", "photo"] },
  instagram: { label: "Instagram", color: "#FF0069", formats: ["reel", "photo", "carousel", "story"] },
  youtube: { label: "YouTube", color: "#FF0000", formats: ["video", "short"] },
};

const FALLBACK: Omit<ChannelInfo, "label"> = { color: "var(--muted-foreground)", formats: ["post"] };

export const isKnownChannel = (c: string): c is KnownChannel => (CHANNELS as readonly string[]).includes(c);

export function channelInfo(channel: string): ChannelInfo {
  return isKnownChannel(channel) ? INFO[channel] : { ...FALLBACK, label: channel };
}

/** The formats the dialog translates; anything else is shown as the server spells it. */
export const FORMATS = ["post", "article", "carousel", "reel", "photo", "story", "video", "short", "thread"] as const;
export type KnownFormat = (typeof FORMATS)[number];
export const isKnownFormat = (f: string): f is KnownFormat => (FORMATS as readonly string[]).includes(f);
