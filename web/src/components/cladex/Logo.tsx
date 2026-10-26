/**
 * The CladeX identity, prefiltered from the canonical SVG artwork.
 * Both ribbon and letters have 1x/2x/3x samples at their display sizes.
 * The wordmark is an alpha mask over currentColor, so smoothing also applies
 * to the letters without baking a light or dark background into the image.
 * Whole-pixel layout matches the exported masks. Sizes follow DESIGN.md §7.
 */
import markPng from "@/assets/cladex-mark.png";
import mark49 from "@/assets/cladex-mark-49.png";
import mark64 from "@/assets/cladex-mark-64.png";
import mark82 from "@/assets/cladex-mark-82.png";
import mark98 from "@/assets/cladex-mark-98.png";
import mark128 from "@/assets/cladex-mark-128.png";
import mark147 from "@/assets/cladex-mark-147.png";
import mark164 from "@/assets/cladex-mark-164.png";
import mark192 from "@/assets/cladex-mark-192.png";
import mark246 from "@/assets/cladex-mark-246.png";
import word144 from "@/assets/cladex-wordmark-144.png";
import word288 from "@/assets/cladex-wordmark-288.png";
import word432 from "@/assets/cladex-wordmark-432.png";
import word240 from "@/assets/cladex-wordmark-240.png";
import word480 from "@/assets/cladex-wordmark-480.png";
import word720 from "@/assets/cladex-wordmark-720.png";
import iconArt from "@/assets/cladex-icon.svg?raw";
import logoArt from "@/assets/cladex-logo.svg?raw";
import { cn } from "@/lib/utils";

function part(re: RegExp, what: string) {
  const m = re.exec(logoArt);
  if (!m) throw new Error(`cladex-logo.svg: ${what} not found`);
  return m[1];
}

const VIEWBOX = part(/viewBox="([^"]*)"/, "viewBox");
const PLACE = part(/<g transform="([^"]*)">/, "the mark's placement");
const WORDMARK_SIDEBAR = `image-set(url("${word144}") 1x, url("${word288}") 2x, url("${word432}") 3x)`;
const WORDMARK_LOGIN = `image-set(url("${word240}") 1x, url("${word480}") 2x, url("${word720}") 3x)`;
const MARK_SRCSET = [
  [mark49, 49], [mark64, 64], [mark82, 82], [mark98, 98], [mark128, 128],
  [mark147, 147], [mark164, 164], [mark192, 192], [mark246, 246], [markPng, 384],
].map(([src, width]) => `${src} ${width}w`).join(", ");

const [vx, vy, vw, vh] = VIEWBOX.split(/\s+/).map(Number);
const [mx, my, mw, mh] = /viewBox="([^"]*)"/.exec(iconArt)![1].split(/\s+/).map(Number);
const placement = /^translate\(([-\d.]+)[ ,]+([-\d.]+)\)\s*scale\(([-\d.]+)[ ,]+([-\d.]+)\)$/.exec(PLACE);
if (!placement) throw new Error("cladex-logo.svg: unsupported mark placement");
const [tx, ty, sx, sy] = placement.slice(1).map(Number);
const MARK_FRACTION = mw * sx / vw;
const MARK_STYLE = {
  left: `${(tx + mx * sx - vx) / vw * 100}%`,
  top: `${(ty + my * sy - vy) / vh * 100}%`,
  width: `${MARK_FRACTION * 100}%`,
  height: `${mh * sy / vh * 100}%`,
};

export function Logo({ width = 144, className }: { width?: number; className?: string }) {
  const maskImage = width <= 144 ? WORDMARK_SIDEBAR : WORDMARK_LOGIN;
  return (
    <span role="img" aria-label="CladeX"
      className={cn("relative inline-block shrink-0 text-[#0D0D12] dark:text-[#FAFAFA]", className)}
      style={{ width, height: Math.round(width * vh / vw) }}>
      <img src={markPng} srcSet={MARK_SRCSET} sizes={`${Math.round(width * MARK_FRACTION)}px`}
        alt="" className="absolute" style={MARK_STYLE} />
      <span aria-hidden="true" className="absolute inset-0 bg-current"
        style={{ maskImage, maskSize: "100% 100%", maskRepeat: "no-repeat" }} />
    </span>
  );
}

/** The ribbon mark alone — next to a heading that already names the product. */
export function Mark({ width = 64, className }: { width?: number; className?: string }) {
  return <img src={markPng} srcSet={MARK_SRCSET} sizes={`${width}px`} alt="" width={width} className={cn("h-auto", className)} />;
}
