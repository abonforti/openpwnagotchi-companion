import { expect } from 'vitest'

/**
 * SPEC 4.5.3: "no element in the field came from the remote string" -- the
 * property that replaced "the field has no child elements" once the
 * isolation rule (issue #219) started deliberately creating a <bdi> around
 * every remote string.
 *
 * Checking only "no element besides a <bdi>" -- the shape this helper
 * replaces, and which dashboard.spec.ts, log-view.spec.ts, peers-view.spec.ts,
 * settings-view.spec.ts and wifi-view.spec.ts each wrote and maintained a
 * copy of before this file replaced them -- does not prove the isolation
 * rule actually fired. It filters out *every* <bdi>, so an attacker string
 * that happens to contain literal `<bdi>` markup and is rendered unescaped
 * (the exact defect this test exists to catch) produces a real, injected
 * `<bdi>` element that the check discards along with the legitimate one,
 * and the assertion passes regardless.
 *
 * This asserts the exact property instead: exactly one <bdi> in the field
 * whose textContent equals the remote text verbatim, and no element
 * anywhere in the field outside that one <bdi>'s own subtree. `el` may be
 * the <bdi> itself or may contain one -- SPEC does not pin which of the two
 * DOM shapes "rendered inside a <bdi>" takes, and this helper is
 * deliberately tolerant of either.
 */
export function assertRemoteStringIsolated(el: Element, text: string): void {
  const selfIsBdi = el.tagName.toLowerCase() === 'bdi' && el.textContent === text
  const nestedMatches = Array.from(el.querySelectorAll('bdi')).filter((b) => b.textContent === text)
  const matches: HTMLElement[] = selfIsBdi ? [el as HTMLElement, ...nestedMatches] : nestedMatches

  expect(
    matches,
    `expected exactly one <bdi> in the field whose textContent is ${JSON.stringify(text)}`,
  ).toHaveLength(1)
  const bdi = matches[0] as HTMLElement

  const others = Array.from(el.querySelectorAll('*')).filter((node) => node !== bdi && !bdi.contains(node))
  expect(others, 'expected no element in the field other than the isolation <bdi>').toEqual([])
}
