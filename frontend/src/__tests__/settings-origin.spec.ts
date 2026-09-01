import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

// Written from SPEC.md 4.7's "Derived from the origin on first run" paragraph
// (issue #134), plus 4.5.1's IPv4-only rule and 4.4.2's "loading settings
// reconciles [companion.token]" clause.
//
// SEAM: loadSettings() takes an optional `getHostname: () => string`
// parameter, defaulting to reading window.location.hostname, discovered
// through this file's own failing runs against the shared tree rather than
// by reading lib/settings.ts's derivation logic - see the report for how
// that happened and why it is disclosed rather than left implicit. Every
// test below drives that parameter directly and deterministically instead
// of stubbing the global `location` object, except the one test that pins
// the default wiring itself. Only the surface pinned by SPEC.md 4.7
// (loadSettings, addHost, updateHost, removeHost, activateHost,
// defaultHosts, settings, activeHost) is used; the derivation's *internal*
// shape (firstRunSettings, isUsableAddress reuse, etc.) was not read and is
// not asserted against here, only loadSettings()'s observable return value
// and its effect on storage.

class FakeStorage implements Storage {
  private readonly data = new Map<string, string>()

  get length(): number {
    return this.data.size
  }

  clear(): void {
    this.data.clear()
  }

  getItem(key: string): string | null {
    return this.data.has(key) ? (this.data.get(key) as string) : null
  }

  key(index: number): string | null {
    return Array.from(this.data.keys())[index] ?? null
  }

  removeItem(key: string): void {
    this.data.delete(key)
  }

  setItem(key: string, value: string): void {
    this.data.set(key, String(value))
  }
}

const TOKEN_KEY = 'companion.token'

let fakeStorage: FakeStorage

beforeEach(() => {
  fakeStorage = new FakeStorage()
  vi.stubGlobal('localStorage', fakeStorage)
  vi.resetModules()
})

afterEach(() => {
  vi.unstubAllGlobals()
})

/** A fresh module instance per test, so a settings/activeHost singleton from one test never leaks into the next. */
async function loadModule() {
  return import('../lib/settings')
}

function hostnameOf(value: string): () => string {
  return () => value
}

// ---------------------------------------------------------------------------
// First run: no companion.settings blob exists yet. SPEC 4.7 says the
// origin entry is minted at exactly that point, alongside the two prefilled
// entries, active.
// ---------------------------------------------------------------------------

describe('first run derives the active entry from the origin (SPEC 4.7, issue #134)', () => {
  it('mints an "origin" host from the given hostname, active, with default ports, beside the two prefilled entries', async () => {
    const { loadSettings, defaultHosts } = await loadModule()

    const result = loadSettings(hostnameOf('172.20.10.9'))

    const origin = result.hosts.find((h) => h.id === 'origin')
    expect(origin, 'expected a host with the stable id "origin"').toBeDefined()
    expect(origin?.address).toBe('172.20.10.9')
    expect(origin?.wsPort).toBe(8082)
    expect(origin?.httpPort).toBe(8443)
    expect(result.activeHostId).toBe('origin')

    // The two prefilled entries are present, unchanged, and not active.
    const defaults = defaultHosts()
    for (const expected of defaults) {
      const found = result.hosts.find((h) => h.id === expected.id)
      expect(found, `expected the prefilled host ${expected.id}`).toEqual(
        expected,
      )
    }
    expect(result.hosts).toHaveLength(3)
  })

  it('is the whole point of issue #134: a hotspot address that is not .2 becomes the active host with nobody editing localStorage', async () => {
    // The Personal Hotspot subnet is always 172.20.10.0/28 (SPEC 2.3.1), and
    // the host part is assigned by the phone's DHCP server, so a unit that
    // did not get .2 is exactly the case the literal default got wrong.
    const { loadSettings } = await loadModule()

    const result = loadSettings(hostnameOf('172.20.10.11'))

    const active = result.hosts.find((h) => h.id === result.activeHostId)
    expect(active?.address).toBe('172.20.10.11')
    expect(active?.id).toBe('origin')
    // Not the bluetooth default's address: a fresh install must not fall
    // back to .2 just because it is in the same /28.
    expect(active?.address).not.toBe('172.20.10.2')
  })

  it('only the address is derived: the ports stay 8082/8443 regardless of what a caller supplies for "port"', async () => {
    // location.port is the HTTPS port and is knowable; SPEC 4.7 says
    // explicitly that it is not used, because nothing on the page knows the
    // WebSocket port and a pair where one half was measured and the other
    // guessed is worse than two defaults. loadSettings() takes only a
    // hostname getter, so there is no port channel to derive from in the
    // first place - this test pins that absence at the return value.
    const { loadSettings } = await loadModule()

    const result = loadSettings(hostnameOf('172.20.10.9'))

    const origin = result.hosts.find((h) => h.id === 'origin')
    expect(origin?.wsPort).toBe(8082)
    expect(origin?.httpPort).toBe(8443)
  })

  it('reconciles companion.token to the derived origin host, which has no token, on the very first load', async () => {
    const { loadSettings } = await loadModule()

    loadSettings(hostnameOf('172.20.10.9'))

    // The origin host is minted with token: null (nothing was typed for
    // it), and SPEC 4.4.2/4.7 require the token key to be reconciled on
    // every load, including this one.
    expect(fakeStorage.getItem(TOKEN_KEY)).toBeNull()
  })

  it('defaults to reading window.location.hostname when no getHostname is supplied', async () => {
    // jsdom's default document location is http://localhost/, so the
    // default wiring is exercised through the same "not an IPv4 literal"
    // fallback the table below covers explicitly, without asserting
    // anything about jsdom's own default beyond that it is not a dotted
    // quad.
    const { loadSettings } = await loadModule()

    const result = loadSettings()

    expect(result.hosts.some((h) => h.id === 'origin')).toBe(false)
    expect(result.activeHostId).toBe('bluetooth')
  })
})

// ---------------------------------------------------------------------------
// A derived address that is not an address is not used: the fallback is
// exactly what shipped before this decision, the two prefilled entries with
// Bluetooth active - not a third entry with a value the grammar would have
// refused if written into a form.
// ---------------------------------------------------------------------------

describe('a hostname that is not an IPv4 literal falls back to the two prefilled entries, Bluetooth active', () => {
  const INVALID_HOSTNAMES: Array<[label: string, hostname: string]> = [
    ['a desktop dev server, plain "localhost"', 'localhost'],
    ['a unit reached by name', 'pwnagotchi.local'],
    [
      'an IPv6 literal, bracketed the way location.hostname reports one',
      '[fe80::1]',
    ],
    ['the unspecified address', '0.0.0.0'],
    ['the limited broadcast address', '255.255.255.255'],
    ['an octal-looking octet', '010.0.0.2'],
  ]

  it.each(INVALID_HOSTNAMES)(
    '%s (%s) yields exactly the shipped defaults',
    async (_label, hostname) => {
      const { loadSettings, defaultHosts } = await loadModule()

      const result = loadSettings(hostnameOf(hostname))

      expect(result.hosts).toEqual(defaultHosts())
      expect(result.hosts.some((h) => h.id === 'origin')).toBe(false)
      expect(result.activeHostId).toBe('bluetooth')
    },
  )
})

// ---------------------------------------------------------------------------
// "It is derived once and then it is a host like any other." Once the first
// load has persisted a blob, a second load must not derive again, and must
// not lose an owner's edit to the entry it minted.
// ---------------------------------------------------------------------------

describe('the origin entry is derived exactly once (SPEC 4.7)', () => {
  it('does not re-derive on a second load from a different hostname', async () => {
    const first = await loadModule()
    const firstResult = first.loadSettings(hostnameOf('172.20.10.9'))
    expect(firstResult.hosts.find((h) => h.id === 'origin')?.address).toBe(
      '172.20.10.9',
    )

    // Simulate a later visit from a different address on the same
    // transport, e.g. a different DHCP lease from the phone. The storage
    // written by the first load is what the second one now finds.
    vi.resetModules()
    const second = await loadModule()
    const secondResult = second.loadSettings(hostnameOf('172.20.10.13'))

    expect(secondResult.hosts.find((h) => h.id === 'origin')?.address).toBe(
      '172.20.10.9',
    )
    expect(secondResult.hosts.filter((h) => h.id === 'origin')).toHaveLength(1)
  })

  it("keeps an owner's edit to the derived entry's label across a reload", async () => {
    const first = await loadModule()
    first.loadSettings(hostnameOf('172.20.10.9'))
    first.updateHost('origin', { label: 'My Pi Zero' })

    vi.resetModules()
    const second = await loadModule()
    const result = second.loadSettings(hostnameOf('172.20.10.9'))

    const origin = result.hosts.find((h) => h.id === 'origin')
    expect(origin?.label).toBe('My Pi Zero')
  })

  it("keeps an owner's edit to the derived entry's address across a reload, even from a third hostname", async () => {
    const first = await loadModule()
    first.loadSettings(hostnameOf('172.20.10.9'))
    first.updateHost('origin', { address: '172.20.10.20' })

    // A later visit from yet another address must not overwrite the
    // owner's own correction.
    vi.resetModules()
    const second = await loadModule()
    const result = second.loadSettings(hostnameOf('172.20.10.30'))

    const origin = result.hosts.find((h) => h.id === 'origin')
    expect(origin?.address).toBe('172.20.10.20')
  })

  it('does not re-derive an entry after the owner has deleted it: an empty-of-origin list stays that way', async () => {
    // SPEC 4.7's general rule for the host list ("an empty host list is a
    // state, not a corruption") plus "never re-derived" for this entry
    // specifically: once first run has happened and persisted, removing
    // the minted entry is an ordinary deletion, not a reason to derive it
    // again on the next load from whatever hostname is current then.
    // This is one reading among a few the report calls out as ambiguous.
    const first = await loadModule()
    first.loadSettings(hostnameOf('172.20.10.9'))
    first.removeHost('origin')

    vi.resetModules()
    const second = await loadModule()
    const result = second.loadSettings(hostnameOf('172.20.10.9'))

    expect(result.hosts.some((h) => h.id === 'origin')).toBe(false)
  })
})
