import { expect, test } from '@playwright/test'

/**
 * SPEC 2.15.1 (issue #91): the service worker's navigation fallback must
 * serve a precached network response, never a synthesised `Response`,
 * because a synthesised one carries no headers and the security headers of
 * this section would be silently absent from a deep link. The copy of the
 * policy below is pinned to the plugin's by tests/test_csp.py.
 *
 * The exact `Content-Security-Policy` string asserted below is the one
 * `content_security_policy()` (plugin/companion.py) produces for the fake
 * unit `tests/e2e_unit.py` runs for the frontend e2e suite (SPEC 10.5,
 * issue #185) - bound address `["127.0.0.1"]`, `ws_port = 8082` - the shape
 * the preview server is pinned to reproduce per SPEC 2.15.1, so equality is
 * the stronger and more exact check available here, not a paraphrase of it.
 *
 * Playwright documents `fromServiceWorker()` only as "fulfilled by a Service
 * Worker's Fetch Handler", with no word on which engines answer it, so the
 * second test was first run in every project. Measured in CI (Playwright
 * 1.62.1, both WebKit projects, both attempts): the worker registers and
 * takes control, and the offline navigation then fails inside the engine
 * with "page.goto: WebKit encountered an internal error" before any
 * response exists to ask. Playwright's WebKit context applies offline to
 * the page and not to the worker, so the step that makes the assertion
 * load-bearing is also the step that engine cannot take. The second test
 * therefore skips in WebKit with that measurement as its reason, and the
 * positive control still runs there (SPEC 10.5).
 *
 * Every test below gets Playwright's default fresh browser context, which
 * matters here specifically: the worker's precache is per origin and per
 * context, so a worker installed by an earlier test cannot leak into this
 * one and mask a fallback that never registered.
 */

const EXPECTED_CSP =
  "default-src 'self'; " +
  "script-src 'self'; " +
  "style-src 'self'; " +
  "img-src 'self' data: https://*.tile.openstreetmap.org; " +
  "connect-src 'self' wss://127.0.0.1:8082; " +
  "font-src 'self'; " +
  "object-src 'none'; " +
  "base-uri 'none'; " +
  "form-action 'none'; " +
  "frame-ancestors 'none'"

function assertSecurityHeaders(headers: Record<string, string>): void {
  // Playwright lower-cases header names in response.headers().
  expect(headers['content-security-policy']).toBe(EXPECTED_CSP)
  expect(headers['x-content-type-options']).toBe('nosniff')
  expect(headers['referrer-policy']).toBe('no-referrer')
}

test('positive control: the first navigation, served by the preview server, carries all three headers', async ({
  page,
}) => {
  const response = await page.goto('/')
  if (!response) {
    throw new Error('page.goto("/") returned no response')
  }

  // A header the origin never sent cannot be missing from a worker's replay
  // of it, so this must not be a worker response: it is the control that
  // gives the second test's assertion meaning.
  expect(response.fromServiceWorker()).toBe(false)

  assertSecurityHeaders(response.headers())
})

test('a deep link with no matching file is served by the worker and still carries all three headers', async ({
  page,
}, testInfo) => {
  test.skip(
    testInfo.project.name.startsWith('webkit'),
    'Measured in CI: WebKit fails the offline navigation with "page.goto: ' +
      'WebKit encountered an internal error" before the worker can answer ' +
      'it, because Playwright applies offline to the page there and not to ' +
      'the worker. See the file comment.',
  )

  await page.goto('/')

  // A first-load page is not controlled by a worker that only just
  // installed, so wait for it to be ready and then reload once: only after
  // that reload can the fallback below be attributed to the worker rather
  // than to the network having answered it directly.
  await page.evaluate(async () => {
    await navigator.serviceWorker.ready
  })
  await page.reload()
  const controller = await page.evaluate(
    () => navigator.serviceWorker.controller !== null,
  )
  expect(controller).toBe(true)

  // Offline, so the only way the worker can answer is from its precache: a
  // fallback that reached for the network on a miss would also report
  // fromServiceWorker() and also carry the headers, and would pass here for
  // the wrong reason with the network up. Chromium only, by measurement:
  // see the file comment for what WebKit does with this navigation.
  await page.context().setOffline(true)

  const response = await page.goto('/nowhere/deep/link')
  if (!response) {
    throw new Error('page.goto("/nowhere/deep/link") returned no response')
  }

  // Asserted before the headers on purpose: a run where the worker never
  // took control of the navigation must fail here, on the fact the fallback
  // did not come from the worker at all, rather than on a header check that
  // would pass for the wrong reason if the network happened to answer with
  // the same document and the same headers.
  expect(response.fromServiceWorker()).toBe(true)

  assertSecurityHeaders(response.headers())
})
