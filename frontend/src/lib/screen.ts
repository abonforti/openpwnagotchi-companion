// SPEC 4.5.2.6/4.5.3. The Mirror view's one rule that is not wording: whether
// the base64 the wire carries is shaped like something that may safely become
// a `data:` URL. Every string that section fixes lives in lib/format.ts, the
// same split lib/log.ts already draws for the Log view's filter.

/**
 * SPEC 4.5.2.6: "the base64 alphabet, padding only at the end, and a length
 * that is a multiple of four." Two rules, for two different reasons, kept
 * separate here the way that section keeps them separate in prose:
 *
 * - **The alphabet is the half that matters for SPEC 4.5.3.** `png` arrives
 *   with no `data:` prefix (`screen_image.json`), so a character outside
 *   `A-Za-z0-9+/=` -- a quote, a space, an angle bracket -- is the one thing
 *   that could let the string leave the `src` attribute it is about to be
 *   placed in.
 * - **The length is checked for a different reason, and it is not a
 *   security rule at all.** A payload of the wrong length is not dangerous,
 *   it is merely unrenderable, and without the check the screen would show a
 *   broken image with nothing saying why -- this is what turns that silent
 *   failure into the "could not be read" sentence instead. Padding only at
 *   the end (`=` or `==`, never a mid-string `=` followed by more data) is
 *   the same kind of check: a base64 payload with padding in the middle does
 *   not decode to anything an `img` can show.
 *
 * **An empty payload is not a frame**, and it fails here with everything
 * else -- not through a guard of its own, but because the pattern below
 * admits only complete groups, and a final group holds between two and four
 * characters. There is no group with zero characters for the empty string to
 * satisfy, so it is refused by the same rule that refuses a payload of the
 * wrong length; a separate `value === ''` check was tried here once and
 * removed, since it changed nothing a test could observe and read as a case
 * needing separate handling that, on inspection, does not exist (SPEC
 * 4.5.2.1's argument against a branch nothing can reach).
 *
 * What is **not** checked, on purpose, is whether the bytes this would
 * decode to are a PNG at all -- no amount of shape-checking establishes
 * that, and this function does not decode the string to find out.
 */
function isBase64Shaped(value: string): boolean {
  return /^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=|[A-Za-z0-9+/]{4})$/.test(
    value,
  )
}

/**
 * The frame's `img` `src`, or `null` when `png` fails `isBase64Shaped`'s
 * check -- one function rather than a check the view calls before building
 * the string by hand, so there is no path from an unchecked `png` to a `src`
 * attribute that skipped the guard. `null` is what tells `views/Mirror.svelte`
 * to render the "could not be read" sentence instead of an `img` (SPEC
 * 4.5.2.6's copy table).
 *
 * `data:` only (SPEC 4.5.2.6: "`data:` only" -- §2.15.1's `img-src` carries
 * `data:` and deliberately not `blob:`, and building a blob URL would fail on
 * the device and nowhere else).
 */
export function screenFrameSrc(png: string): string | null {
  return isBase64Shaped(png) ? `data:image/png;base64,${png}` : null
}
