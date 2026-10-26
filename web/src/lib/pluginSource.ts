/** What a pasted repository link says about a plugin.
 *
 * People do not have a manifest in hand; they have the page they were just looking
 * at — `…/tree/<sha>/marketing`. That URL carries three of the four fields the form
 * needs, and typing a 40-character sha by hand is how a pin ends up wrong. So the
 * link is parsed, never trusted: everything it yields lands in a visible field the
 * owner can correct, and the server runs the real validator over the result.
 */

/** The catalog's own name rule: it becomes the directory the clone lives in. */
const NAME_RE = /^[a-z0-9][a-z0-9_-]*$/;
/** The catalog's own ref rule — a full sha or a release tag. A branch is not a pin:
 *  it moves after the review, so `main` in the URL yields no ref at all and the
 *  owner is asked for one rather than handed something that will be refused. */
const PIN_RE = /^(?:[0-9a-f]{40}|v?\d+(?:\.\d+)*[A-Za-z0-9.\-+_]*)$/;

export type PluginSource = { repo: string; ref: string; subdir: string; name: string };

/** A repository name or path segment turned into a catalog name, or "" if nothing
 *  usable is left — the field stays empty rather than filled with something invalid. */
export function nameFrom(segment: string): string {
  const name = segment.toLowerCase().replace(/\.git$/, "").replace(/[^a-z0-9_-]+/g, "-")
    .replace(/^-+|-+$/g, "");
  return NAME_RE.test(name) ? name : "";
}

/** Read an https repository URL, with or without a `/tree/<ref>/<path>` tail.
 *  Returns null when it is not a URL we can make a manifest out of; the form then
 *  leaves what the owner typed alone. */
export function parseSource(raw: string): PluginSource | null {
  let url: URL;
  try {
    url = new URL(raw.trim());
  } catch {
    return null;
  }
  if (url.protocol !== "https:") return null;
  const parts = url.pathname.split("/").filter(Boolean);
  if (parts.length < 2) return null;

  const [owner, repoName, ...rest] = parts;
  const repo = `https://${url.host}/${owner}/${repoName.replace(/\.git$/, "")}`;
  // `/tree/<ref>/<subdir>` on GitHub, `/-/tree/…` on GitLab; anything else after the
  // repository is a page (issues, releases) that says nothing about which code to pin.
  const at = rest.indexOf("tree");
  const ref = at >= 0 && PIN_RE.test(rest[at + 1] ?? "") ? rest[at + 1] : "";
  const subdir = at >= 0 ? rest.slice(at + 2).join("/") : "";
  const last = subdir.split("/").filter(Boolean).at(-1) ?? repoName;
  return { repo, ref, subdir, name: nameFrom(last) };
}
