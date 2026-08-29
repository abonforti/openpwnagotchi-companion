// SPEC 4.5.2.5. The Log view's one list rule that is not wording: the text
// filter. Every string that section fixes lives in lib/format.ts, the same
// discipline lib/wifi.ts and lib/peers.ts already follow for their own
// screens.

/**
 * Applied to the buffer the app already holds, never to the request (SPEC
 * 4.5.2.5): the plugin owns the tail and its clamp (SPEC 2.9), so filtering
 * at the source would mean "the last 200 *matching* lines", a different and
 * more expensive question, and it would make the buffer's meaning depend on
 * a control the owner is still typing into.
 *
 * A plain case-insensitive substring match, not a query language: a regular
 * expression typed into a phone in this field is a way to hide lines by
 * accident. An empty query matches every line, including one made only of
 * whitespace -- the field filters on exactly what was typed, nothing
 * trimmed on its behalf.
 */
export function filterLogLines(lines: string[], query: string): string[] {
  if (query === '') return lines
  const needle = query.toLowerCase()
  return lines.filter((line) => line.toLowerCase().includes(needle))
}
