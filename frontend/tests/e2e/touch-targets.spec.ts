import { expect, test } from '@playwright/test'

import {
  BAR_SLOTS,
  SHEET_SLOTS,
  expectedLayout,
  gotoView,
  openSheet,
  orientationOf,
  rectOf,
} from './helpers'
import type { Rect } from './helpers'

/**
 * SPEC 10.5: "touch targets at 44px". SPEC 4.2 asks for large touch targets,
 * and 44 CSS pixels is the floor Apple's Human Interface Guidelines state for
 * iOS, which is the platform this ships to.
 *
 * Measured from the laid-out box, not read from CSS. A control padded by a
 * pseudo-element, or given its size by a parent's grid track, measures
 * correctly while its own rule says nothing; a control given `min-height: 44px`
 * inside a flex row that shrinks it does not. Only the measurement can tell
 * those two apart, which is why this is here and not in the vitest suite.
 */

const FLOOR = 44

// SPEC 4.5.1: "Touch targets need separation as well as size ... The bar
// entries, and any row of adjacent controls, carry a gap." The approved figure
// is 4 CSS pixels.
const GAP = 4

// Adjacent boxes share an edge, and at deviceScaleFactor 3 that edge can round
// into a sub-pixel overlap. Anything under a pixel is rounding, not a control
// sitting on another. This tolerance is for that shared edge only: the gap
// below is a flex track of a fixed size rather than an edge two boxes share, so
// it measures exactly and is asserted exactly.
const EPSILON_OVERLAP = 1

interface LabeledRect {
  label: string
  rect: Rect
}

/**
 * SPEC 4.5.1's separation rule -- both the GAP between neighbours and the
 * FLOOR each keeps once that gap is outside its own box -- extracted so the
 * navigation bar/rail and the SPEC 4.5.2.2 controls below assert it from one
 * expression rather than two. Sorts `items` along `axis` and walks them
 * pairwise, exactly as the original navigation-only test did.
 */
function assertHeldApart(
  items: LabeledRect[],
  axis: 'horizontal' | 'vertical',
  describeAs: { plural: string; singular: string },
): void {
  const horizontal = axis === 'horizontal'
  const ordered = [...items].sort((a, b) =>
    horizontal ? a.rect.left - b.rect.left : a.rect.top - b.rect.top,
  )

  // Walked pairwise with the previous entry carried forward, rather than by
  // index: the first pass has no predecessor and every later one does, which
  // is a case the loop runs rather than a guard it can never reach.
  let before: LabeledRect | undefined
  for (const after of ordered) {
    if (before === undefined) {
      before = after
      continue
    }
    const gap = horizontal
      ? after.rect.left - before.rect.right
      : after.rect.top - before.rect.bottom
    const axisWord = horizontal ? 'horizontally' : 'vertically'

    expect(
      gap,
      `${describeAs.plural} "${before.label}" and "${after.label}" are ${gap.toFixed(1)}px apart ` +
        `${axisWord}, under the ${GAP}px separation SPEC 4.5.1 requires`,
    ).toBeGreaterThanOrEqual(GAP)

    // The gap is separation *between* controls, not a margin carved out of
    // one. Both boxes are measured hit boxes, so a layout that reached the
    // gap by shrinking its entries fails here on the size floor rather than
    // passing on the separation.
    for (const entry of [before, after]) {
      const extent = horizontal ? entry.rect.width : entry.rect.height
      expect(
        extent,
        `${describeAs.singular} "${entry.label}" measures ${extent.toFixed(1)}px along the axis ` +
          `once the ${GAP}px gap is outside its box, under the ${FLOOR}px floor`,
      ).toBeGreaterThanOrEqual(FLOOR)
    }

    before = after
  }
}

/**
 * Which axis a set of boxes is actually laid out on, read from their spread
 * rather than assumed: this file does not read Dashboard.svelte or
 * lib/controls.ts, so it has no way to know whether the state controls of
 * SPEC 4.5.2.2 stack in a column or sit in a row, the way the navigation
 * test above knows from `expectedLayout`. Whichever spread is larger is the
 * axis the boxes are ordered on.
 */
function dominantAxis(rects: Rect[]): 'horizontal' | 'vertical' {
  const lefts = rects.map((rect) => rect.left)
  const tops = rects.map((rect) => rect.top)
  const xSpread = Math.max(...lefts) - Math.min(...lefts)
  const ySpread = Math.max(...tops) - Math.min(...tops)
  return xSpread >= ySpread ? 'horizontal' : 'vertical'
}

test.describe('every navigation target is at least 44px', () => {
  test('the bar or rail entries, including More', async ({ page }, testInfo) => {
    const orientation = orientationOf(testInfo)
    const layout = expectedLayout(orientation)
    await gotoView(page, 'dashboard')

    for (const slot of BAR_SLOTS) {
      const entry = page.locator(`[data-nav="${slot}"]`)
      await expect(entry, `the ${layout} is missing the ${slot} entry`).toHaveCount(1)

      const rect = await rectOf(entry)
      expect(
        rect.width,
        `${layout} entry "${slot}" is ${rect.width.toFixed(1)}px wide, under the ${FLOOR}px floor`,
      ).toBeGreaterThanOrEqual(FLOOR)
      expect(
        rect.height,
        `${layout} entry "${slot}" is ${rect.height.toFixed(1)}px tall, under the ${FLOOR}px floor`,
      ).toBeGreaterThanOrEqual(FLOOR)
    }
  })

  test('the More control itself', async ({ page }) => {
    await gotoView(page, 'dashboard')

    // Named separately from the loop above because SPEC 4.5 gives it a job the
    // other four do not have: it opens the sheet, and it is the element focus
    // is returned to on close, so it is the one that must be hittable twice in
    // a row without a miss.
    const rect = await rectOf(page.locator('#nav-more'))
    expect(rect.width).toBeGreaterThanOrEqual(FLOOR)
    expect(rect.height).toBeGreaterThanOrEqual(FLOOR)
  })

  test('the sheet entries', async ({ page }) => {
    await gotoView(page, 'dashboard')
    await openSheet(page)

    for (const slot of SHEET_SLOTS) {
      const entry = page.locator(`[data-sheet="${slot}"]`)
      await expect(entry, `the More sheet is missing the ${slot} entry`).toHaveCount(1)

      const rect = await rectOf(entry)
      expect(
        rect.width,
        `sheet entry "${slot}" is ${rect.width.toFixed(1)}px wide, under the ${FLOOR}px floor`,
      ).toBeGreaterThanOrEqual(FLOOR)
      expect(
        rect.height,
        `sheet entry "${slot}" is ${rect.height.toFixed(1)}px tall, under the ${FLOOR}px floor`,
      ).toBeGreaterThanOrEqual(FLOOR)
    }
  })

  test('no two navigation targets overlap', async ({ page }) => {
    await gotoView(page, 'dashboard')

    // A 44px box that sits on top of its neighbour is 44px of ambiguity, not
    // 44px of target. This asserts the weaker half of SPEC 4.5.1 - the hit
    // boxes are disjoint - and stays separate from the separation test below,
    // so that an overlap and a merely absent gap fail with different names.
    const rects = []
    for (const slot of BAR_SLOTS) {
      rects.push({ slot, rect: await rectOf(page.locator(`[data-nav="${slot}"]`)) })
    }

    for (const a of rects) {
      for (const b of rects) {
        if (a === b) continue
        const overlaps =
          a.rect.left < b.rect.right - EPSILON_OVERLAP &&
          b.rect.left < a.rect.right - EPSILON_OVERLAP &&
          a.rect.top < b.rect.bottom - EPSILON_OVERLAP &&
          b.rect.top < a.rect.bottom - EPSILON_OVERLAP
        expect(overlaps, `entries "${a.slot}" and "${b.slot}" have overlapping hit boxes`).toBe(
          false,
        )
      }
    }
  })

  test('adjacent navigation entries are held apart by the gap', async ({ page }, testInfo) => {
    const orientation = orientationOf(testInfo)
    const layout = expectedLayout(orientation)
    await gotoView(page, 'dashboard')

    // SPEC 4.5.1 separates two failures that a single "44px" rule conflates: a
    // small target is hard to hit, two large targets flush against each other
    // are easy to hit *wrongly*. Disjoint boxes (the test above) rule out only
    // the second half of the first failure; a dead band between them is what
    // makes a mis-hit recoverable, and on this bar the neighbour of a harmless
    // entry can be one that reboots the unit.
    const entries: LabeledRect[] = []
    for (const slot of BAR_SLOTS) {
      entries.push({ label: slot, rect: await rectOf(page.locator(`[data-nav="${slot}"]`)) })
    }

    // The bar lays its entries out along x, the rail along y -- known from
    // `expectedLayout` here, unlike the controls below, which have no such
    // declared layout to read.
    const axis = layout === 'bar' ? 'horizontal' : 'vertical'
    assertHeldApart(entries, axis, { plural: `${layout} entries`, singular: `${layout} entry` })
  })
})

/**
 * SPEC 4.5.2.2's state controls (issue #184): the same floor and the same
 * separation as navigation, and the same reason SPEC 4.5.1's paragraph
 * gives for asking for both -- "a small target is hard to hit, while two
 * large targets flush against each other are easy to hit *wrongly*, which
 * matters most where the neighbour does something serious." Reboot next to
 * Shut down is the case that sentence is written about.
 *
 * This suite runs against the built app with no unit attached (SPEC 10.5),
 * so `stats` never arrives. Two consequences follow, both from SPEC 4.5.2.2
 * itself rather than assumed here: `capabilities` lives inside `stats`
 * (SPEC 4.4.1), and the PASV control's own rule is `capabilities.pasv`, so
 * it never renders; and the connection never leaves `connecting`, so every
 * rendered control is disabled by `canSendCommand('connecting')` being
 * false. Neither stops a geometry measurement -- a disabled control is
 * still laid out -- but the second is why the armed pair (Confirm next to
 * Cancel) is not measured below; see the note at the end of this file.
 */
test.describe('the state controls of SPEC 4.5.2.2 respect the floor and the gap', () => {
  // Mode, reboot and shutdown are rendered before the first stats frame
  // (SPEC 4.5.2.2); PASV is not, for the reason above.
  const RENDERED_CONTROLS = ['mode', 'reboot', 'shutdown'] as const

  test('every rendered control clears 44px in both dimensions', async ({ page }) => {
    await gotoView(page, 'dashboard')

    for (const control of RENDERED_CONTROLS) {
      const button = page.locator(`[data-control="${control}"] [data-action="request"]`)
      await expect(button, `the ${control} control has no [data-action="request"]`).toHaveCount(1)

      const rect = await rectOf(button)
      expect(
        rect.width,
        `${control} request button is ${rect.width.toFixed(1)}px wide, under the ${FLOOR}px floor`,
      ).toBeGreaterThanOrEqual(FLOOR)
      expect(
        rect.height,
        `${control} request button is ${rect.height.toFixed(1)}px tall, under the ${FLOOR}px floor`,
      ).toBeGreaterThanOrEqual(FLOOR)
    }
  })

  test('adjacent controls, Reboot next to Shut down among them, are held apart by the gap', async ({
    page,
  }) => {
    await gotoView(page, 'dashboard')

    const items: LabeledRect[] = []
    for (const control of RENDERED_CONTROLS) {
      items.push({
        label: control,
        rect: await rectOf(page.locator(`[data-control="${control}"] [data-action="request"]`)),
      })
    }

    const axis = dominantAxis(items.map((item) => item.rect))
    assertHeldApart(items, axis, { plural: 'controls', singular: 'control' })
  })
})

// The armed pair -- Confirm next to Cancel -- is the pair SPEC 4.5.1's
// paragraph is actually about, and it is deliberately not measured above.
// Reaching it means clicking a control's own [data-action="request"], and
// every one of them is disabled for the reason given on the describe block
// above: this suite has no unit to attach, the connection never leaves
// `connecting`, and a disabled button refuses a Playwright click rather
// than accepting one. Forcing the click past actionability would measure a
// state no real tap on this build can ever produce, which is worse than not
// measuring it. Reaching the armed state honestly needs a fake-unit seam
// this e2e layer does not have.
