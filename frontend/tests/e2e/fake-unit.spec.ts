import { expect, test } from '@playwright/test'

import { gotoView, header, seedActiveHost, waitForConnected } from './helpers'

/**
 * The positive control for issue #185: proof that this suite can actually
 * face a real unit, not only the built app's empty shell. Every other test
 * in this file's neighbours either measures an idle, unconnected screen
 * (SPEC 10.5's own note in `touch-targets.spec.ts`) or a static asset; this
 * one is the one place the fake unit `tests/e2e_unit.py` runs is asserted
 * to be on the other end of the socket at all.
 *
 * `seedActiveHost` is called with an empty label on purpose. SPEC 4.5.1.2's
 * precedence puts a non-empty, non-address `Host.label` ahead of
 * `stats.name`, and this test's whole point is that the name on screen came
 * from the unit rather than from a label typed into `companion.settings`
 * beforehand -- an empty label is what lets `stats.name` win and be the
 * thing under test.
 */

// tests/fakes/pwnagotchi_stub/pwnagotchi/__init__.py's default `_NAME`,
// which `tests/e2e_unit.py` never overrides: this is `pwnagotchi.name()`
// (F33), carried onto the wire as `stats.name` (SPEC 2.6, 4.5.1.2).
const FAKE_UNIT_NAME = 'pwnagotchi'

test('the fake unit is actually faced: the header names it and the connection reaches connected', async ({
  page,
}) => {
  await seedActiveHost(page, { label: '' })
  await gotoView(page, 'dashboard')
  await waitForConnected(page)

  await expect(header(page).locator('[data-field="unitName"]')).toHaveText(
    FAKE_UNIT_NAME,
  )
})

// tests/fakes/fixtures/handshakes/ holds four capture files: three .pcapng
// (Ocean_Buoy_998877665544, TestNet_001_aabbccddeeff, and the deliberately
// malformed no-bssid-here, which SPEC 2.7 still counts and lists even though
// it cannot derive an ssid/bssid from the name) and one .pcap
// (Legacy_Net_112233445566). SPEC 2.6 defines `stats.handshakes` as the
// narrower *.pcapng-only count that matches the unit's own display, and
// `stats.handshakesTotal` as *.pcapng plus *.pcap -- always >= handshakes.
// Dashboard's `[data-field="handshakes"]` row renders `handshakesTotal`
// (SPEC 4.5.1.1), so the number this test expects is the full four, not the
// narrower three a `/^\d+$/` match would also have accepted, including from
// a unit reporting zero.
const FIXTURE_HANDSHAKE_TOTAL = 4

test('the Dashboard shows the fixture handshake count once stats arrive', async ({
  page,
}) => {
  await seedActiveHost(page)
  await gotoView(page, 'dashboard')
  await waitForConnected(page)

  // `data-empty="true"` (SPEC 4.5.1.1) is the row's own signal for the dash
  // placeholder (`lib/format.ts`'s `DASH`) -- checked instead of the
  // rendered text so this test states the fact the field itself carries,
  // not a string that happens to match what `DASH` is today.
  const handshakes = page.locator('[data-field="handshakes"]')
  await expect(handshakes).toBeVisible()
  await expect(handshakes).not.toHaveAttribute('data-empty', 'true')
  await expect(handshakes).toHaveText(String(FIXTURE_HANDSHAKE_TOTAL))
})
