import { defineConfig, devices } from '@playwright/test'

/**
 * End-to-end harness for the geometry and behaviour invariants of SPEC 10.5.
 *
 * Two rules from that section shape everything below:
 *
 * 1. The suite runs against the built `dist/`, not the dev server. The dev
 *    server resolves modules differently, serves no service worker, and is not
 *    what install-on-pi.sh unpacks onto the unit.
 * 2. There are no screenshot baselines and no `toHaveScreenshot`. Baselines are
 *    platform-specific, CI is amd64 and the review host is arm64, so a shared
 *    baseline fails on font rendering alone. Screenshots are captured as
 *    artifacts for a human to look at, never compared by the machine.
 */

// This config runs under Node, but the frontend carries no @types/node: nothing
// else in it touches a Node global, and a types-only package pulled in for one
// property would be checked by `npm run typecheck` and used nowhere else.
// Only the property actually read is declared.
declare const process: { env: Record<string, string | undefined> }

// The reference device profile of SPEC 4.2.1, where these numbers are recorded
// alongside the measurements they came from. Deliberately not a device name:
// the profile is what the suite tests against, and hardware belongs in the
// section that documents how it was measured, not in a config comment.
const PORTRAIT = { width: 402, height: 874 }
const LANDSCAPE = { width: 874, height: 402 }

const DEVICE = {
  deviceScaleFactor: 3,
  isMobile: true,
  hasTouch: true,
}

const PORT = 4173
const BASE_URL = `http://localhost:${PORT}`

export default defineConfig({
  testDir: './tests/e2e',

  // Artifacts land here: screenshots, traces on a retry, the HTML report input.
  // Ignored by git and uploaded by CI.
  outputDir: './test-results',

  // Chromium and WebKit only (SPEC 10.5). Firefox is neither engine the app is
  // used on, and every browser in the matrix costs minutes on every run.
  //
  // Both orientations are expressed as separate projects rather than a fixture
  // or a per-test setViewportSize, for three reasons. Every spec then runs in
  // both orientations without the author remembering to ask for it, which is
  // the point of an orientation invariant. A failure names the orientation it
  // happened in, instead of hiding it inside a parametrised test. And a spec
  // that genuinely only applies to one of them can read
  // `test.info().project.metadata.orientation` and skip, which is cheaper than
  // rotating a viewport mid-test and waiting for the layout to settle.
  projects: [
    {
      name: 'chromium-portrait',
      metadata: { orientation: 'portrait' },
      use: { ...devices['Desktop Chrome'], ...DEVICE, viewport: PORTRAIT },
    },
    {
      name: 'chromium-landscape',
      metadata: { orientation: 'landscape' },
      use: { ...devices['Desktop Chrome'], ...DEVICE, viewport: LANDSCAPE },
    },
    {
      name: 'webkit-portrait',
      metadata: { orientation: 'portrait' },
      use: { ...devices['Desktop Safari'], ...DEVICE, viewport: PORTRAIT },
    },
    {
      name: 'webkit-landscape',
      metadata: { orientation: 'landscape' },
      use: { ...devices['Desktop Safari'], ...DEVICE, viewport: LANDSCAPE },
    },
  ],

  use: {
    baseURL: BASE_URL,
    // On the first retry only: a trace of every passing run is a lot of
    // megabytes for information nobody reads.
    trace: 'on-first-retry',
  },

  // A geometry assertion that only fails sometimes is a defect report nobody
  // can act on, so a green run here has to mean the same thing twice.
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [['github'], ['list']] : 'list',

  webServer: {
    // `vite preview` serves exactly the directory that becomes dist.tgz,
    // including the SPA fallback the plugin's static server also performs.
    command: `npm run build && npm run preview -- --port ${PORT} --strictPort`,
    url: BASE_URL,
    // The review host is an arm64 Pi and a cold Vite build there is not fast.
    timeout: 180_000,
    // Locally, reuse a preview server that is already up; in CI there is never
    // one to reuse and silently attaching to a stale build would be worse.
    reuseExistingServer: !process.env.CI,
    stdout: 'ignore',
    stderr: 'pipe',
  },
})
