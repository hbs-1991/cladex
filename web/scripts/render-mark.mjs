/**
 * Render the canonical ribbon SVG at high resolution, then prefilter each
 * display density with Lanczos3. Doing this before delivery avoids depending
 * on the browser's large-ratio image downscaling at the 49px sidebar size.
 * Also export the wordmark as an alpha mask, so its antialiasing is prefiltered
 * while CSS currentColor still follows the theme. Source paths stay untouched.
 * Run after changing the artwork: npm run render:mark
 */
import sharp from "sharp";
import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const assets = path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "src", "assets");
// 49px sidebar, 64px empty thread, 82px login: 1x, 2x and 3x, plus fallback.
const widths = [49, 64, 82, 98, 128, 147, 164, 192, 246, 384];
const svg = await fs.readFile(path.join(assets, "cladex-icon.svg"));
// Materialize first: resizing in the same pipeline can skip supersampling.
const supersampled = await sharp(svg, { density: 288 }).png().toBuffer();
for (const width of widths) {
  const filename = width === 384 ? "cladex-mark.png" : `cladex-mark-${width}.png`;
  const info = await sharp(supersampled)
    .resize({ width, kernel: "lanczos3" })
    .png()
    .toFile(path.join(assets, filename));
  console.log(`${filename}: ${info.width}x${info.height}, ${(info.size / 1024).toFixed(1)} kB`);
}

const logo = await fs.readFile(path.join(assets, "cladex-logo.svg"), "utf8");
const viewBox = /viewBox="([^"]*)"/.exec(logo)?.[1];
const letters = /<g id="cladex-wordmark"[^>]*>([\s\S]*?)<\/g>/.exec(logo)?.[1];
if (!viewBox || !letters) throw new Error("cladex-logo.svg: missing viewBox or wordmark");
const [, , sourceWidth, sourceHeight] = viewBox.split(/\s+/).map(Number);
const wordSvg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="${viewBox}" width="${sourceWidth}" height="${sourceHeight}"><g fill="black">${letters}</g></svg>`;
const wordSamples = await sharp(Buffer.from(wordSvg), { density: 288 }).png().toBuffer();
for (const baseWidth of [144, 240]) {
  // Round at 1x first: every density must fit exactly the same CSS rectangle.
  const baseHeight = Math.round(baseWidth * sourceHeight / sourceWidth);
  for (const density of [1, 2, 3]) {
    const width = baseWidth * density;
    const filename = `cladex-wordmark-${width}.png`;
    const info = await sharp(wordSamples)
      .resize({ width, height: baseHeight * density, fit: "fill", kernel: "lanczos3" })
      .png()
      .toFile(path.join(assets, filename));
    console.log(`${filename}: ${info.width}x${info.height}, ${(info.size / 1024).toFixed(1)} kB`);
  }
}
