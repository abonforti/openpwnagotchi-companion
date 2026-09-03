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
    // The fake unit's TLS chain (tools/gen-ca.sh, tools/gen-cert.sh) is
    // generated fresh for this run and never installed as a trusted root, the
    // same way a real unit's is until the owner installs the CA (SPEC 10.5,
    // issue #185). A test that wants to prove the chain itself uses its own
    // context instead; this is the default every other spec inherits.
    ignoreHTTPSErrors: true,
  },

  // A geometry assertion that only fails sometimes is a defect report nobody
  // can act on, so a green run here has to mean the same thing twice.
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [['github'], ['list']] : 'list',

  // Two servers: the built frontend under `vite preview`, and the fake unit
  // that gives the suite something real to connect to (SPEC 10.5, issue
  // #185). `webServer` as an array requires `use.baseURL` to be set
  // explicitly even with a single HTTP entry, which it already is above.
  webServer: [
    {
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
    {
      // The real Router and Listeners from plugin/companion.py, run standalone
      // against fixtures (SPEC 10.5, issue #185). `cwd: '..'` because this
      // config lives in frontend/ and the module is addressed from the
      // repository root, the same root `tests/e2e_unit.py`'s own docstring
      // documents the command from. Plain `python3`, not `uv run`: nothing in
      // .github/workflows/ci.yml's `e2e` job installs uv, only tests/requirements.txt
      // into whatever `python3` resolves to there - a local run needs the same
      // (an already-activated virtualenv with those requirements installed).
      command: 'python3 -m tests.e2e_unit --ws-port 8082 --http-port 8443',
      cwd: '..',
      // A WSS server answers no HTTP `url` Playwright could poll; `port`
      // only waits for a TCP accept. tests/e2e_unit.py's own readiness check
      // is stricter than this - it asserts on what Listeners actually bound
      // before it ever gets this far - so a process that reaches this port
      // check is one that already passed its own.
      port: 8082,
      // The review host is an arm64 Pi; CA and certificate generation there is
      // not instant, the same reason its neighbour gets this timeout.
      timeout: 180_000,
      // A stale fake unit still bound to 8082 is not a server worth reusing:
      // its fixtures, certificate and process lifetime all belong to a run
      // that already ended, unlike the preview build above.
      reuseExistingServer: false,
      // Without this, Playwright SIGKILLs the process group on teardown and
      // tests/e2e_unit.py's own `finally: shutil.rmtree(...)` never runs,
      // leaking its temporary CA and certificate on every run. SIGTERM first,
      // giving the script's signal handler a chance to stop the listeners and
      // clean up; SIGKILL only if it has not exited within the timeout.
      gracefulShutdown: { signal: 'SIGTERM', timeout: 5000 },
      // Piped, not ignored: tests/e2e_unit.py prints exactly one ready line
      // and nothing else on stdout, so this is cheap and gives CI something
      // to show if the process never gets there.
      stdout: 'pipe',
      stderr: 'pipe',
    },
  ],
})
