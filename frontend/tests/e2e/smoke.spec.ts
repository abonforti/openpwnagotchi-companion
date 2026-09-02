import { expect, test } from '@playwright/test'

/**
 * Harness smoke test, not a geometry gate. It proves the built app is served,
 * loads, and paints its navigation in every project of the matrix, so that a
 * failure in one of the real invariant specs beside it is a statement about the
 * app rather than about the harness.
 *
 * The screenshot is captured as an artifact and deliberately not compared:
 * SPEC 10.5 rules out baselines because CI is amd64 and the review host arm64.
 */
test('the shell loads and shows five navigation entries', async ({
  page,
}, testInfo) => {
  await page.goto('/')

  const nav = page.getByRole('navigation', { name: 'Primary' })
  await expect(nav).toBeVisible()

  // Four routes plus the More control that opens the sheet (SPEC 4.5).
  await expect(nav.locator('[data-nav]')).toHaveCount(5)

  // Written straight to the test's own output path, so it survives on disk for
  // the CI artifact upload and carries the name of the project that produced it.
  // It is evidence for a human, never an assertion: SPEC 10.5 rules out
  // baselines. Not attached, which would only copy the same bytes a second time
  // into the same directory, for a report this suite does not produce.
  await page.screenshot({ path: testInfo.outputPath('shell.png') })
})
