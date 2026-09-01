import { expect, test } from '@playwright/test'

import {
  EPSILON,
  ROUTES,
  type ViewName,
  currentView,
  expectedLayout,
  gotoView,
  nav,
  orientationOf,
  rectOf,
  shell,
  viewportOf,
  viewsContentBox,
  viewsRegion,
} from './helpers'

/**
 * The geometry invariants of SPEC 10.5: "nothing painted below the content area
 * in either orientation, the map never owning the full width or height of the
 * views area so a drag always has somewhere to scroll the app".
 *
 * These are the checks jsdom structurally cannot make. Every number below comes
 * from the engine's own layout, never from a stylesheet the test read back.
 *
 * One boundary, stated once here rather than repeated at every assertion. The
 * dead band SPEC 4.2.1 records is an iOS-standalone effect: the engine declares
 * a viewport shorter than the screen, and no desktop engine can be put into
 * that mode. What is checkable here is everything on this side of that
 * declaration: the shell covers the viewport the engine does declare, the
 * document does not scroll behind it, the views area and the navigation account
 * for the shell between them, and every point of the viewport is painted by
 * something inside the shell rather than by the page underneath. The iOS
 * arithmetic itself stays a hardware check, as SPEC 4.2.1 says it must.
 */

const VIEWS = Object.keys(ROUTES) as ViewName[]

// SPEC 2.15.1/4.5.2.7: `https://*.tile.openstreetmap.org` is the one external
// origin this app's CSP admits, "a real weakening of the policy rather than a
// formality". Once the Map view carries a real `[data-map-surface]` element
// (SPEC 4.5.2.7's own hook), the per-view loop below - which visits every
// route, `/map` included - and the map-specific describes further down both
// bring a live map onto the page, and Leaflet requests tiles from that origin
// on every one of the four Playwright projects, on every run. A suite that
// only measures a rectangle must not depend on a third-party service's
// availability or usage policy for that measurement to run at all, so every
// tile request in this file is answered locally rather than reaching the
// network. Registered once, at the top of the file, rather than only inside
// the map-specific describes: the per-view loop's own `map` iteration would
// otherwise still call out.
//
// The body is empty rather than a real decoded image: nothing this file
// measures depends on a tile actually painting (the surface's own box is
// CSS-driven, not image-driven), and an empty body needs no binary encoding
// here - this project deliberately carries no @types/node (playwright.config.ts's
// own comment on `declare const process`), so a Buffer literal would be the
// one place that pulled it in for a single test file.
test.beforeEach(async ({ page }) => {
  await page.route('https://*.tile.openstreetmap.org/**', async (route) => {
    await route.fulfill({ status: 200, contentType: 'image/png', body: '' })
  })
})

/**
 * Hit-test the whole viewport on a grid, plus every edge and corner. Box
 * arithmetic cannot see a strip that no element covers, because the missing
 * element has no box to measure; `elementFromPoint` returns whatever the user's
 * finger would land on, which is exactly the question. This is complementary to
 * the edge arithmetic above it, not a repeat of it: the edges catch a region
 * that stops in the wrong place, the grid catches a pixel nobody owns.
 */
async function pointsNotOwnedByTheShell(page: import('@playwright/test').Page) {
  return page.evaluate(() => {
    const shellRoot = document.querySelector('[data-layout]')
    const navRoot = document.querySelector('nav[data-layout]')
    const w = window.innerWidth
    const h = window.innerHeight

    const xs = new Set<number>([0, 1, Math.floor(w / 2), w - 2, w - 1])
    const ys = new Set<number>([0, 1, Math.floor(h / 2), h - 2, h - 1])
    for (let i = 1; i < 12; i += 1) {
      xs.add(Math.floor((w * i) / 12))
      ys.add(Math.floor((h * i) / 12))
    }

    const orphans: Array<{ x: number; y: number; tag: string | null }> = []
    for (const x of xs) {
      for (const y of ys) {
        const element = document.elementFromPoint(x, y)
        if (!element || !shellRoot || !shellRoot.contains(element)) {
          orphans.push({
            x,
            y,
            tag: element ? element.tagName.toLowerCase() : null,
          })
        }
      }
    }

    // The element on the very last row at the horizontal centre, reported so a
    // portrait failure can name what took the navigation bar's place.
    const bottomCentre = document.elementFromPoint(Math.floor(w / 2), h - 1)
    return {
      orphans,
      bottomCentreInsideNav: !!(
        bottomCentre &&
        navRoot &&
        navRoot.contains(bottomCentre)
      ),
      bottomCentreTag: bottomCentre ? bottomCentre.tagName.toLowerCase() : null,
    }
  })
}

test.describe('the shell fills the viewport and nothing is painted below it', () => {
  for (const view of VIEWS) {
    test(`${view}: no strip of the viewport is left unowned`, async ({
      page,
    }, testInfo) => {
      const orientation = orientationOf(testInfo)
      await gotoView(page, view)

      const viewport = await viewportOf(page)
      const shellRect = await rectOf(shell(page))
      const navRect = await rectOf(nav(page))
      const viewRect = await rectOf(currentView(page))

      // The shell fills the viewport the engine declares. SPEC 4.2.1 is
      // explicit that this is the correct behaviour, and that the band seen on
      // the device comes from the engine declaring a shorter viewport rather
      // than from the shell failing to fill it.
      expect(shellRect.top).toBeCloseTo(0, 0)
      expect(shellRect.left).toBeCloseTo(0, 0)
      expect(shellRect.width).toBeCloseTo(viewport.innerWidth, 0)
      expect(shellRect.height).toBeCloseTo(viewport.innerHeight, 0)

      // The shell is the whole app: the document does not scroll behind it.
      // A document taller than the viewport is the other shape the band takes,
      // because the navigation then sits above content nobody can reach.
      expect(viewport.scrollHeight).toBeLessThanOrEqual(
        viewport.innerHeight + EPSILON,
      )
      expect(viewport.scrollWidth).toBeLessThanOrEqual(
        viewport.innerWidth + EPSILON,
      )

      // The view is laid out inside the shell horizontally, and starts inside
      // it vertically. SPEC 4.5 says landscape exists for the Log and the Map
      // and that "every other view keeps a reading measure rather than
      // stretching", so a narrow view root is a requirement, not a gap; SPEC
      // 10.5 says a view is also allowed to be *taller* than the viewport,
      // because that is what a scroll container looks like from the outside.
      // What must hold is that it never escapes sideways, where nothing
      // scrolls and there is nowhere legitimate to go, and that it never
      // starts above the shell.
      expect(viewRect.top).toBeGreaterThanOrEqual(shellRect.top - EPSILON)
      expect(viewRect.left).toBeGreaterThanOrEqual(shellRect.left - EPSILON)
      expect(viewRect.right).toBeLessThanOrEqual(shellRect.right + EPSILON)

      // The views area is the region SPEC 10.5 is written about, and the shell
      // declares it rather than leaving it to be inferred from a view root.
      // The current view lives inside it, which is what makes the edges below
      // statements about where content can go and not about an empty box.
      await expect(viewsRegion(page)).toHaveCount(1)
      await expect(
        viewsRegion(page).locator(`[data-view="${view}"]`),
      ).toHaveCount(1)
      const areaRect = await viewsContentBox(page)

      // Where the view's overflow, if any, belongs: to the views area and to
      // nothing else. The area's own `scrollHeight` must account for the full
      // height of the view sitting inside it, which is what makes the content
      // reachable by scrolling the area rather than lost below the fold. This
      // is checked against the area's scroll box, not against the shell or the
      // viewport, precisely because a view taller than either of those is the
      // case SPEC 10.5 says is allowed.
      const areaScrollHeight = await viewsRegion(page).evaluate(
        (element) => element.scrollHeight,
      )
      expect(
        areaScrollHeight,
        `the views area scrolls to ${areaScrollHeight}px but the view is ${viewRect.height}px tall`,
      ).toBeGreaterThanOrEqual(viewRect.height - EPSILON)

      // A header sits above the views area, so the area starts below the top
      // of the shell rather than at it (SPEC 4.2, a sticky top bar; SPEC 4.5,
      // that header collapsing to one line in the rail range).
      expect(areaRect.top).toBeGreaterThan(shellRect.top)
      expect(areaRect.width).toBeGreaterThan(0)
      expect(areaRect.height).toBeGreaterThan(0)

      if (orientation === 'portrait') {
        // The bottom bar is flush with the bottom of the shell, full width
        // (SPEC 4.2: "a bottom navigation bar for the home indicator").
        expect(navRect.bottom).toBeGreaterThanOrEqual(
          shellRect.bottom - EPSILON,
        )
        expect(navRect.left).toBeLessThanOrEqual(shellRect.left + EPSILON)
        expect(navRect.right).toBeGreaterThanOrEqual(shellRect.right - EPSILON)

        // The views area and the bar account for the shell between them: the
        // area ends where the bar begins, nothing in between, nothing beyond,
        // and the area spans the full width.
        expect(
          Math.abs(areaRect.bottom - navRect.top),
          `the views area ends at ${areaRect.bottom} and the bar begins at ${navRect.top}`,
        ).toBeLessThanOrEqual(EPSILON)
        expect(Math.abs(areaRect.left - shellRect.left)).toBeLessThanOrEqual(
          EPSILON,
        )
        expect(Math.abs(areaRect.right - shellRect.right)).toBeLessThanOrEqual(
          EPSILON,
        )
      } else {
        // The rail runs the full height of the shell on the leading edge
        // (SPEC 4.5: "a 72px rail on the leading edge").
        expect(navRect.top).toBeLessThanOrEqual(shellRect.top + EPSILON)
        expect(navRect.bottom).toBeGreaterThanOrEqual(
          shellRect.bottom - EPSILON,
        )
        expect(navRect.left).toBeLessThanOrEqual(shellRect.left + EPSILON)

        // The views area and the rail account for the shell between them: the
        // area begins where the rail ends and reaches the trailing edge and
        // the bottom of the shell.
        expect(
          Math.abs(areaRect.left - navRect.right),
          `the rail ends at ${navRect.right} and the views area begins at ${areaRect.left}`,
        ).toBeLessThanOrEqual(EPSILON)
        expect(Math.abs(areaRect.right - shellRect.right)).toBeLessThanOrEqual(
          EPSILON,
        )
        expect(
          Math.abs(areaRect.bottom - shellRect.bottom),
        ).toBeLessThanOrEqual(EPSILON)
      }

      // And the part no box arithmetic can answer: every sampled point of the
      // viewport is painted by something inside the shell. A dead band shows
      // up here as a run of points resolving to <body> or <html>.
      const probe = await pointsNotOwnedByTheShell(page)
      expect(
        probe.orphans,
        `points of the viewport resolve outside the shell: ${probe.orphans
          .map((o) => `(${o.x}, ${o.y}) -> <${o.tag}>`)
          .join(', ')}`,
      ).toEqual([])

      if (orientation === 'portrait') {
        // In portrait the last row belongs to the navigation bar itself, not
        // to a strip of something else beneath it.
        expect(
          probe.bottomCentreInsideNav,
          `the bottom centre resolved to <${probe.bottomCentreTag}>, outside the navigation`,
        ).toBe(true)
      }
    })
  }
})

test.describe('the navigation takes the form its orientation requires', () => {
  test('shell and navigation agree on bar in portrait and rail in landscape', async ({
    page,
  }, testInfo) => {
    const orientation = orientationOf(testInfo)
    const expected = expectedLayout(orientation)
    await gotoView(page, 'dashboard')

    // SPEC 4.5: the rail applies at exactly `(orientation: landscape) and
    // (max-height: 560px)`. The landscape project is 874x402, inside that
    // range; the portrait project is 402x874 and keeps the bottom bar.
    await expect(shell(page)).toHaveAttribute('data-layout', expected)
    await expect(nav(page)).toHaveAttribute('data-layout', expected)
  })

  test('the rail is 72px of content on the leading edge', async ({
    page,
  }, testInfo) => {
    const orientation = orientationOf(testInfo)
    test.skip(
      orientation !== 'landscape',
      'the rail exists only in the landscape range',
    )
    await gotoView(page, 'dashboard')

    const navRect = await rectOf(nav(page))
    const insets = await page.evaluate(() => {
      const probe = document.createElement('div')
      probe.style.cssText =
        'position:fixed;top:0;left:0;padding-left:env(safe-area-inset-left);' +
        'padding-right:env(safe-area-inset-right);visibility:hidden'
      document.body.appendChild(probe)
      const style = getComputedStyle(probe)
      const value = {
        left: parseFloat(style.paddingLeft) || 0,
        right: parseFloat(style.paddingRight) || 0,
      }
      probe.remove()
      return value
    })

    // SPEC 4.5: "the 72px is content width **outside**
    // env(safe-area-inset-left/right)". A desktop engine reports both insets
    // as zero, so here the total width and the content width coincide; that
    // the rail clears a non-zero inset is only observable on the device, and
    // SPEC 4.2.1 records it as a hardware check for that reason.
    expect(
      insets.left,
      'a desktop engine is expected to report a zero leading inset',
    ).toBe(0)
    expect(navRect.width - insets.left).toBeCloseTo(72, 0)
  })
})

test.describe('the map leaves somewhere to drag the app', () => {
  test('the map surface covers neither the full width nor the full height of the views area', async ({
    page,
  }) => {
    await gotoView(page, 'map')

    // Measured against the views area, not against the Map view root: SPEC
    // 10.5 says the map must never own "the full width or height of the views
    // area", and a view root free to keep a reading measure (SPEC 4.5) is a
    // different and smaller box. Written this way now so that the assertion
    // which starts running the day Leaflet lands is already the one SPEC
    // means, rather than an approximation nobody revisits at that moment.
    const areaRect = await viewsContentBox(page)

    // SPEC 4.5.1 requires a region where a drag scrolls the app rather than
    // panning the map, always reachable without scrolling first: the map is
    // never as wide or as tall as the views area, and the strip that leaves
    // over is what the gesture needs. SPEC 4.5.2.7's own "hooks and the copy"
    // paragraph is explicit that this is measured against data-map-surface,
    // deliberately not against Leaflet's own .leaflet-container class name:
    // that class is "a library's private vocabulary", and a test against it
    // would break on a Leaflet upgrade that changed nothing this app cares
    // about.
    const surface = page.locator('[data-view="map"] [data-map-surface]')
    const count = await surface.count()

    test.skip(
      count === 0,
      'the Map view carries no data-map-surface element in this branch; the ' +
        'invariant cannot be measured until the real Map lands',
    )

    const surfaceRect = await rectOf(surface.first())
    expect(
      surfaceRect.width,
      `the map surface is ${surfaceRect.width}px wide in a ${areaRect.width}px views area`,
    ).toBeLessThan(areaRect.width - EPSILON)
    expect(
      surfaceRect.height,
      `the map surface is ${surfaceRect.height}px tall in a ${areaRect.height}px views area`,
    ).toBeLessThan(areaRect.height - EPSILON)
  })

  test('the Map is a laid-out view inside the shell, not a full-bleed escape', async ({
    page,
  }) => {
    await gotoView(page, 'map')

    // What is structurally true while the Map is a placeholder: it is a view
    // like any other, laid out inside the shell rather than escaping it. This
    // is the part of the invariant that does not depend on a map existing, and
    // it stops the placeholder from passing the suite by being absent.
    const shellRect = await rectOf(shell(page))
    const navRect = await rectOf(nav(page))
    const viewRect = await rectOf(currentView(page))

    expect(viewRect.width).toBeGreaterThan(0)
    expect(viewRect.height).toBeGreaterThan(0)
    expect(viewRect.top).toBeGreaterThanOrEqual(shellRect.top - EPSILON)
    expect(viewRect.bottom).toBeLessThanOrEqual(shellRect.bottom + EPSILON)
    expect(viewRect.left).toBeGreaterThanOrEqual(shellRect.left - EPSILON)
    expect(viewRect.right).toBeLessThanOrEqual(shellRect.right + EPSILON)

    // The navigation stays hittable on the Map: whatever the view paints, it
    // does not cover the way out of it.
    const covered = await page.evaluate(() => {
      const navRoot = document.querySelector('nav[data-layout]')
      if (!navRoot) return ['no navigation found']
      const bad: string[] = []
      for (const entry of Array.from(navRoot.querySelectorAll('[data-nav]'))) {
        const r = entry.getBoundingClientRect()
        const hit = document.elementFromPoint(
          r.left + r.width / 2,
          r.top + r.height / 2,
        )
        if (!hit || !entry.contains(hit)) {
          bad.push(
            `${entry.getAttribute('data-nav')} -> <${hit ? hit.tagName.toLowerCase() : 'none'}>`,
          )
        }
      }
      return bad
    })
    expect(
      covered,
      'navigation entries covered while the Map view is showing',
    ).toEqual([])
    expect(navRect.width).toBeGreaterThan(0)
  })
})

test.describe('the log fills what is left and stops where the views area does (issue #38)', () => {
  // SPEC 4.5.2.5's own "Geometry" paragraph closes issue #38 -- the mockup's
  // log box overflowing its own container at 874x402, the landscape project
  // this suite already runs (SPEC 4.5: landscape exists for the Log and the
  // Map) -- and its own DOM-hooks paragraph says why this is measured in
  // both orientations here rather than left to the generic per-view check
  // above: the log lines container is a scrolling box of its own inside the
  // view, not the view root, so a container that escapes its own parent
  // while the view root stays put is a defect the generic check cannot see.
  // Issue #38's own acceptance criteria name the log box bottom against the
  // views area specifically, with the log both empty and full; this suite
  // has no unit to attach (see the second test below), so only the empty
  // half is measured here.
  test('the log lines container never exceeds the views area, empty buffer', async ({
    page,
  }) => {
    await gotoView(page, 'log')

    // SPEC 4.5.2.5: "the container is always present, empty buffer or not" --
    // named there specifically because a container that appears only once
    // lines arrive would leave this, the state a fresh launch spends the
    // most time in, unmeasured. Required, not optional, so its absence is a
    // failure here rather than a skip: unlike the Map's Leaflet surface
    // (still a placeholder view, genuinely absent today), the Log view and
    // this container both exist, and a guard that skipped on a zero count
    // would hide exactly the defect this test exists to catch.
    const linesLocator = page.locator('[data-log-lines]')
    await expect(
      linesLocator,
      'SPEC 4.5.2.5 requires this container to always be present',
    ).toHaveCount(1)

    const areaRect = await viewsContentBox(page)
    const linesRect = await rectOf(linesLocator)

    expect(
      linesRect.bottom,
      `the log lines container bottom is ${linesRect.bottom}px, the views area ends at ${areaRect.bottom}px`,
    ).toBeLessThanOrEqual(areaRect.bottom + EPSILON)
    expect(linesRect.top).toBeGreaterThanOrEqual(areaRect.top - EPSILON)
    expect(linesRect.left).toBeGreaterThanOrEqual(areaRect.left - EPSILON)
    expect(linesRect.right).toBeLessThanOrEqual(areaRect.right + EPSILON)
  })

  // The other half of issue #38's own criteria: the same containment with the
  // log full rather than empty, which is the state the mockup's defect was
  // actually found in (a log box that only overflows once it has content to
  // overflow with). Recorded as a skip rather than left absent: this e2e
  // layer runs against the built app with nothing behind the WebSocket (this
  // config's own webServer note), so the log store never holds a line and
  // there is no way to reach a full buffer to measure against an overflowing
  // container. Reaching it honestly needs the fake-unit seam issue #185 is
  // about, the same gap touch-targets.spec.ts already names for the armed
  // two-step controls.
  test('the log lines container never exceeds the views area, full buffer', async () => {
    test.skip(
      true,
      'unreachable from this e2e layer: no unit is attached, so the log store never holds a ' +
        'line to measure a full buffer against -- needs the fake-unit seam issue #185 is about',
    )
  })
})
