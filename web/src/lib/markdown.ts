/** The one class list for page-scale rendered markdown.
 *
 * DESIGN.md §3 "Assistant markdown rules" at page scale — the file panel keeps the
 * full version for previews; a SKILL.md or a wiki page needs headings, lists, code
 * and tables and nothing else. It lives here rather than in either page because
 * two copies of a typography scale drift the moment one of them is touched.
 */
export const MD_BODY = [
  "text-base leading-[1.625rem] break-words",
  "[&>*:first-child]:mt-0 [&>*:last-child]:mb-0",
  "[&_h1]:mt-8 [&_h1]:mb-3 [&_h1]:text-2xl [&_h1]:font-semibold",
  "[&_h2]:mt-8 [&_h2]:mb-3 [&_h2]:text-xl [&_h2]:font-semibold",
  "[&_h3]:mt-8 [&_h3]:mb-3 [&_h3]:text-lg [&_h3]:font-semibold",
  "[&_h4]:mt-8 [&_h4]:mb-3 [&_h4]:text-base [&_h4]:font-semibold",
  "[&_p]:mb-4",
  "[&_ul]:mb-4 [&_ul]:list-disc [&_ul]:pl-6 [&_ol]:mb-4 [&_ol]:list-decimal [&_ol]:pl-6",
  "[&_li]:mb-1 [&_li>ul]:my-2 [&_li>ol]:my-2",
  "[&_code]:rounded-sm [&_code]:bg-muted [&_code]:px-1 [&_code]:py-0.5 [&_code]:font-mono [&_code]:text-code",
  "[&_pre]:mb-4 [&_pre]:overflow-x-auto [&_pre]:rounded-lg [&_pre]:bg-muted [&_pre]:p-4",
  "[&_pre_code]:bg-transparent [&_pre_code]:p-0",
  "[&_table]:mb-4 [&_table]:block [&_table]:w-full [&_table]:max-w-full [&_table]:overflow-x-auto [&_table]:text-sm",   // a wide table scrolls in place on a phone
  "[&_th]:border-b [&_th]:px-3 [&_th]:py-2 [&_th]:text-left [&_td]:border-b [&_td]:px-3 [&_td]:py-2",
  "[&_a]:text-signal [&_a]:underline [&_a]:underline-offset-[3px]",
  "[&_blockquote]:mb-4 [&_blockquote]:border-l-2 [&_blockquote]:pl-4",
  "[&_hr]:my-8",
].join(" ");

/** The `---\n…\n---` block at the top of a wiki page.
 *
 * The sheet shows the title, summary, tags and date in its own header, so leaving
 * the raw YAML in the body would print all four twice — once as chrome and once as
 * a horizontal rule followed by a line of `title: …`. Only a block that both opens
 * on line 1 and closes is stripped; an unterminated one is just text. */
export function stripFrontmatter(text: string): string {
  const lines = text.split("\n");
  if (lines[0]?.trim() !== "---") return text;
  for (let i = 1; i < lines.length; i++) {
    if (lines[i].trim() === "---" || lines[i].trim() === "...") return lines.slice(i + 1).join("\n");
  }
  return text;
}

/** `[[ns/page]]` and `[[ns/page|Alias]]` become ordinary markdown links to a
 * `#wiki:` href, which the renderer intercepts. Doing it as a text pass rather
 * than a remark plugin keeps the dependency list where it is, and the href is a
 * fragment so a stray click that escapes the handler navigates nowhere. */
export function linkifyWikilinks(text: string): string {
  return text.replace(/\[\[([^[\]\n]+)\]\]/g, (_m, inner: string) => {
    const [rawTarget, alias] = inner.split("|");
    const target = rawTarget.split("#")[0].trim().replace(/\\/g, "/").replace(/^\/+|\/+$/g, "").replace(/\.md$/i, "");
    if (!target) return _m;
    return `[${(alias ?? target).trim()}](#wiki:${target})`;
  });
}

/** The page path inside a `#wiki:` href, or null for any other link. */
export function wikiHref(href: string | undefined): string | null {
  return href && href.startsWith("#wiki:") ? decodeURIComponent(href.slice("#wiki:".length)) : null;
}
