import {
  expect,
  type Locator,
  type Page,
  type TestInfo,
} from '@playwright/test'

/**
 * Shared measuring tools for the geometry suite of SPEC 10.5.
 *
 * Nearly nothing here asserts. Every measuring helper returns a number or a
 * rectangle so that the spec that calls it states the invariant in its own
 * words, and a failure message names the thing the specification promised
 * rather than the helper. `waitForConnected` is the one exception: it is a
 * precondition a fake-unit test waits on before its own assertions begin,
 * not a geometry invariant, and its failure message says so rather than
 * pretending to be silent about it.
 */

/** The seven routes of the navigation table in SPEC 4.5, in bar order. */
export const ROUTES = {
  dashboard: '/',
  wifi: '/wifi',
  map: '/map',
  log: '/log',
  peers: '/peers',
  mirror: '/mirror',
  settings: '/settings',
} as const

export type ViewName = keyof typeof ROUTES

/** The four bar slots that are views, plus the More control (SPEC 4.5). */
export const BAR_SLOTS = ['dashboard', 'wifi', 'map', 'log', 'more'] as const

/** The three views the More sheet hosts (SPEC 4.5). */
export const SHEET_SLOTS = ['peers', 'mirror', 'settings'] as const

export interface Rect {
  x: number
  y: number
  width: number
  height: number
  top: number
  right: number
  bottom: number
  left: number
}

/**
 * The orientation of the running project, read from the project metadata the
 * config declares. Deliberately not sniffed from the viewport width: the width
 * is what the assertion is about, so deriving the expectation from it would
 * make every orientation test tautological.
 */
export function orientationOf(testInfo: TestInfo): 'portrait' | 'landscape' {
  const value = testInfo.project.metadata.orientation
  if (value !== 'portrait' && value !== 'landscape') {
    throw new Error(
      `project ${testInfo.project.name} declares no orientation metadata; ` +
        'the geometry suite reads it rather than guessing from the viewport',
    )
  }
  return value
}

/** The layout SPEC 4.5 requires for an orientation at the device profile. */
export function expectedLayout(
  orientation: 'portrait' | 'landscape',
): 'bar' | 'rail' {
  // The rail applies at exactly `(orientation: landscape) and (max-height:
  // 560px)`. The landscape project is 874x402, so 402 <= 560 holds and the
  // rail is required; the portrait project is 402x874 and keeps the bar.
  return orientation === 'landscape' ? 'rail' : 'bar'
}

export async function rectOf(locator: Locator): Promise<Rect> {
  const rect = await locator.evaluate((element) => {
    const r = element.getBoundingClientRect()
    return {
      x: r.x,
      y: r.y,
      width: r.width,
      height: r.height,
      top: r.top,
      right: r.right,
      bottom: r.bottom,
      left: r.left,
    }
  })
  return rect
}

export interface Viewport {
  innerWidth: number
  innerHeight: number
  scrollHeight: number
  scrollWidth: number
}

export async function viewportOf(page: Page): Promise<Viewport> {
  return page.evaluate(() => ({
    innerWidth: window.innerWidth,
    innerHeight: window.innerHeight,
    scrollHeight: document.documentElement.scrollHeight,
    scrollWidth: document.documentElement.scrollWidth,
  }))
}

/** The shell root, which carries `data-layout` per the declared DOM contract. */
export function shell(page: Page): Locator {
  return page.locator('[data-layout]').first()
}

/** The header, SPEC 4.5.1.2's `[data-region="header"]`. */
export function header(page: Page): Locator {
  return page.locator('[data-region="header"]')
}

export interface SeededHost {
  address?: string
  wsPort?: number
  httpPort?: number
  label?: string
}

/**
 * Seeds `companion.settings` (SPEC 4.7) with one active host before the page's
 * own scripts run, so the header (SPEC 4.5.1.2) has a unit to name rather
 * than falling back to "companion" with no connection to describe. Registered
 * with `page.addInitScript`, which runs on every document the page loads
 * after this call, exactly like `gotoView`'s own navigation.
 *
 * Defaults to the fake unit `tests/e2e_unit.py` binds (127.0.0.1, ws 8082,
 * http 8443, issue #185), so a caller that only wants a named host with
 * nothing listening -- the geometry suite's own use -- and a caller that
 * wants a real connection share one call shape.
 */
export async function seedActiveHost(
  page: Page,
  {
    address = '127.0.0.1',
    wsPort = 8082,
    httpPort = 8443,
    label = 'E2E Unit',
  }: SeededHost = {},
): Promise<void> {
  await page.addInitScript(
    (host) => {
      window.localStorage.setItem(
        'companion.settings',
        JSON.stringify({
          hosts: [
            {
              id: 'e2e-host',
              label: host.label,
              address: host.address,
              wsPort: host.wsPort,
              httpPort: host.httpPort,
              token: null,
            },
          ],
          activeHostId: 'e2e-host',
        }),
      )
    },
    { address, wsPort, httpPort, label },
  )
}

/**
 * Waits for the header's connection state (SPEC 4.5.1.2,
 * `[data-field="connectionState"]`) to read what `formatConnectionState`
 * (`lib/format.ts`) renders for `'connected'`. The string is inlined rather
 * than imported: this suite runs against the built `dist/` output (SPEC
 * 10.5), never against `src/`, so importing the source module would assert
 * something the running app was not actually built from.
 */
export async function waitForConnected(page: Page): Promise<void> {
  await expect(
    header(page).locator('[data-field="connectionState"]'),
  ).toHaveText('Connected')
}

/** The navigation, bar or rail: the `data-layout` carrier that is a `nav`. */
export function nav(page: Page): Locator {
  return page.locator('nav[data-layout]')
}

/**
 * The view currently on screen. Views stay mounted when navigated away from
 * (SPEC 4.5), so this selects by visibility rather than by presence.
 */
export function currentView(page: Page): Locator {
  return page.locator('[data-view]:visible')
}

/** Navigate to a route and wait for its view to be the visible one. */
export async function gotoView(page: Page, view: ViewName): Promise<void> {
  await page.goto(ROUTES[view])
  await page.locator(`[data-view="${view}"]`).waitFor({ state: 'visible' })
}

/** Open the More sheet and wait for its entries to be reachable. */
export async function openSheet(page: Page): Promise<void> {
  await page.locator('#nav-more').click()
  await page.locator('#more-sheet').waitFor({ state: 'visible' })
}

/**
 * Geometry is compared with a tolerance because the engines round fractional
 * CSS pixels differently at deviceScaleFactor 3, and a sub-pixel seam is not
 * the dead band this suite is looking for. One CSS pixel is well below the
 * smallest defect worth reporting and well above the rounding noise.
 */
export const EPSILON = 1

/**
 * The scrolling views area, declared as a seam by the shell for this suite.
 *
 * A test cannot derive this region from the view roots: SPEC 4.5 lets a view
 * keep a reading measure rather than stretching, so a view root is routinely
 * smaller than the area it sits in and says nothing about where that area ends.
 */
export function viewsRegion(page: Page): Locator {
  return page.locator('[data-region="views"]')
}

/**
 * The *content* edges of the views area.
 *
 * The region is pinned to the shell and reserves the header and the navigation
 * as padding, so its border box is the whole shell and only the content box is
 * the area views are laid out in. Measuring the border box here would assert
 * that the shell is the shell, which is true and worthless.
 */
export async function viewsContentBox(page: Page): Promise<Rect> {
  return viewsRegion(page).evaluate((element) => {
    const r = element.getBoundingClientRect()
    const style = getComputedStyle(element)
    const top = r.top + parseFloat(style.paddingTop)
    const bottom = r.bottom - parseFloat(style.paddingBottom)
    const left = r.left + parseFloat(style.paddingLeft)
    const right = r.right - parseFloat(style.paddingRight)
    return {
      x: left,
      y: top,
      width: right - left,
      height: bottom - top,
      top,
      right,
      bottom,
      left,
    }
  })
}
