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

// Adjacent boxes share an edge, and at deviceScaleFactor 3 that edge can round
// into a sub-pixel overlap. Anything under a pixel is rounding, not a control
// sitting on another.
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
    // 44px of target. SPEC does not state a spacing requirement, so this
    // asserts only the part that follows from the entries being distinct
    // controls: their hit boxes are disjoint.
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
})
