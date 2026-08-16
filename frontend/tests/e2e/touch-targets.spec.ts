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
    const entries = []
    for (const slot of BAR_SLOTS) {
      entries.push({ slot, rect: await rectOf(page.locator(`[data-nav="${slot}"]`)) })
    }

    // The bar lays its entries out along x, the rail along y. Ordering is taken
    // from the measured geometry rather than from BAR_SLOTS, so that a bar
    // whose entries are reordered visually is still checked between the pairs
    // that are actually neighbours on screen.
    const horizontal = layout === 'bar'
    const ordered = [...entries].sort((a, b) =>
      horizontal ? a.rect.left - b.rect.left : a.rect.top - b.rect.top,
    )

    // Walked pairwise with the previous entry carried forward, rather than by
    // index: the first pass has no predecessor and every later one does, which
    // is a case the loop runs rather than a guard it can never reach.
    let before: (typeof entries)[number] | undefined
    for (const after of ordered) {
      if (before === undefined) {
        before = after
        continue
      }
      const gap = horizontal
        ? after.rect.left - before.rect.right
        : after.rect.top - before.rect.bottom
      const axis = horizontal ? 'horizontally' : 'vertically'

      expect(
        gap,
        `${layout} entries "${before.slot}" and "${after.slot}" are ${gap.toFixed(1)}px apart ` +
          `${axis}, under the ${GAP}px separation SPEC 4.5.1 requires`,
      ).toBeGreaterThanOrEqual(GAP)

      // The gap is separation *between* controls, not a margin carved out of
      // one. Both boxes are measured hit boxes, so a bar that reached the gap
      // by shrinking its entries fails here on the size floor rather than
      // passing on the separation.
      for (const entry of [before, after]) {
        const extent = horizontal ? entry.rect.width : entry.rect.height
        expect(
          extent,
          `${layout} entry "${entry.slot}" measures ${extent.toFixed(1)}px along the bar axis ` +
            `once the ${GAP}px gap is outside its box, under the ${FLOOR}px floor`,
        ).toBeGreaterThanOrEqual(FLOOR)
      }

      before = after
    }
  })
})
