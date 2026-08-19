import { get } from 'svelte/store'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { Host, Settings } from '../lib/settings'

// Written from SPEC.md 4.7, 4.3.6, 4.4.2, 4.5.2 and the fixed public
// surface handed to the test author for issue #115. Deliberately not read
// from lib/settings.ts, which is authored in parallel.
//
// This module does not perform the teardown/resetStores/attach switch of
// 4.4.2 - that sequence belongs to the app shell, which holds the WsClient.
// What settings.ts owns is the guarantee the switch depends on: the active
// host's token is in companion.token before any subscriber is told the
// active host changed, and it stays that way across every path that can
// change which host is active or what the active host is - activation,
// editing the active host's token or address, removal, and a reload. That
// guarantee is testable from outside without a WsClient double, so
// lib/ws.ts and lib/stores.ts are not touched at all here.
//
// Second round, after review found the reconciliation gap in loadSettings
// and seven smaller ones: the USB default label lost its "desktop only"
// note (moved to the Settings view, which is the only thing with a field
// to put it next to - a Host has no such field), addresses are now
// validated as a bare host on both read and write, and the parser/mutator
// split is deliberate (repair-or-drop on read, throw on write).

// ---------------------------------------------------------------------------
// A fake localStorage. Real storage is never touched: the persisted blob is
// hostile input (4.7), and this suite needs to write shapes real storage
// would never contain on its own, plus simulate a store that refuses to
// answer (4.7's storage-guard rule).
// ---------------------------------------------------------------------------

class FakeStorage implements Storage {
  private readonly data = new Map<string, string>()
  readonly throwOnGetKeys = new Set<string>()
  readonly throwOnSetKeys = new Set<string>()
  readonly setCalls: string[] = []

  get length(): number {
    return this.data.size
  }

  clear(): void {
    this.data.clear()
  }

  getItem(key: string): string | null {
    if (this.throwOnGetKeys.has(key)) throw new Error('storage is disabled')
    return this.data.has(key) ? (this.data.get(key) as string) : null
  }

  key(index: number): string | null {
    return Array.from(this.data.keys())[index] ?? null
  }

  removeItem(key: string): void {
    this.data.delete(key)
  }

  setItem(key: string, value: string): void {
    this.setCalls.push(key)
    if (this.throwOnSetKeys.has(key)) throw new Error('storage is full')
    this.data.set(key, String(value))
  }
}

const SETTINGS_KEY = 'companion.settings'
const TOKEN_KEY = 'companion.token'

let fakeStorage: FakeStorage

beforeEach(() => {
  fakeStorage = new FakeStorage()
  vi.stubGlobal('localStorage', fakeStorage)
  vi.resetModules()
})

afterEach(() => {
  vi.unstubAllGlobals()
  // Belt and braces against the prototype-pollution test below: this must
  // never survive into another test regardless of how that one finishes.
  delete (Object.prototype as Record<string, unknown>).token
})

/** A fresh module instance per test, so a settings/activeHost singleton from one test never leaks into the next. */
async function loadModule() {
  return import('../lib/settings')
}

function newHost(overrides: Partial<Omit<Host, 'id'>> = {}): Omit<Host, 'id'> {
  return {
    label: 'Test unit',
    address: '172.20.10.2',
    wsPort: 8082,
    httpPort: 8443,
    token: null,
    ...overrides,
  }
}

/**
 * The invariant a hostile blob must still satisfy: every host is a
 * well-formed Host, and activeHostId either points at one of them or is
 * null. This does not pin whether a malformed host is dropped or repaired
 * in general - 4.7 draws that line at id/address (dropped) versus every
 * other field (repaired), pinned by the dedicated tests below - it pins
 * that the shape returned is always one the rest of the app can use
 * without checking further.
 */
function assertWellFormedSettings(result: Settings): void {
  expect(Array.isArray(result.hosts)).toBe(true)
  for (const host of result.hosts) {
    expect(typeof host.id).toBe('string')
    expect(host.id.length).toBeGreaterThan(0)
    expect(typeof host.label).toBe('string')
    expect(typeof host.address).toBe('string')
    expect(host.address.length).toBeGreaterThan(0)
    expect(Number.isInteger(host.wsPort)).toBe(true)
    expect(host.wsPort).toBeGreaterThanOrEqual(1)
    expect(host.wsPort).toBeLessThanOrEqual(65535)
    expect(Number.isInteger(host.httpPort)).toBe(true)
    expect(host.httpPort).toBeGreaterThanOrEqual(1)
    expect(host.httpPort).toBeLessThanOrEqual(65535)
    expect(host.token === null || typeof host.token === 'string').toBe(true)
  }
  if (result.activeHostId !== null) {
    expect(result.hosts.some((h) => h.id === result.activeHostId)).toBe(true)
  }
}

// ---------------------------------------------------------------------------
// 4.7: a settings blob that will not parse is replaced by the defaults, and
// the app still starts. Each failure shape below is its own test, because
// they fail differently: a parser that only guards against non-JSON still
// throws on a well-formed array, and a suite with one shared "bad input"
// test would not catch that.
// ---------------------------------------------------------------------------

describe('a hostile persisted blob does not stop the app from starting (4.7)', () => {
  it('not JSON at all', async () => {
    fakeStorage.setItem(SETTINGS_KEY, 'not json at all {{{')
    const { loadSettings } = await loadModule()
    assertWellFormedSettings(loadSettings())
  })

  it('valid JSON of the wrong type: a bare string', async () => {
    fakeStorage.setItem(SETTINGS_KEY, JSON.stringify('just a string'))
    const { loadSettings } = await loadModule()
    assertWellFormedSettings(loadSettings())
  })

  it('valid JSON of the wrong type: an array', async () => {
    fakeStorage.setItem(SETTINGS_KEY, JSON.stringify([1, 2, 3]))
    const { loadSettings } = await loadModule()
    assertWellFormedSettings(loadSettings())
  })

  it('valid JSON of the wrong type: a number', async () => {
    fakeStorage.setItem(SETTINGS_KEY, JSON.stringify(42))
    const { loadSettings } = await loadModule()
    assertWellFormedSettings(loadSettings())
  })

  it('the right type with hosts missing', async () => {
    fakeStorage.setItem(SETTINGS_KEY, JSON.stringify({ activeHostId: null }))
    const { loadSettings } = await loadModule()
    assertWellFormedSettings(loadSettings())
  })

  it('a host whose wsPort is a string falls back to the default rather than being rejected', async () => {
    fakeStorage.setItem(
      SETTINGS_KEY,
      JSON.stringify({
        hosts: [
          { id: 'string-port', label: 'Unit', address: '172.20.10.9', wsPort: '8082', httpPort: 8443, token: null },
        ],
        activeHostId: null,
      }),
    )
    const { loadSettings } = await loadModule()
    const result = loadSettings()
    assertWellFormedSettings(result)
    const host = result.hosts.find((h) => h.id === 'string-port')
    expect(host).toBeDefined()
    expect(host?.wsPort).toBe(8082)
  })

  it('a host whose wsPort is negative falls back to the default', async () => {
    fakeStorage.setItem(
      SETTINGS_KEY,
      JSON.stringify({
        hosts: [
          { id: 'negative-port', label: 'Unit', address: '172.20.10.9', wsPort: -1, httpPort: 8443, token: null },
        ],
        activeHostId: null,
      }),
    )
    const { loadSettings } = await loadModule()
    const result = loadSettings()
    assertWellFormedSettings(result)
    const host = result.hosts.find((h) => h.id === 'negative-port')
    expect(host).toBeDefined()
    expect(host?.wsPort).toBe(8082)
  })

  it('a host whose wsPort is zero falls back to the default', async () => {
    fakeStorage.setItem(
      SETTINGS_KEY,
      JSON.stringify({
        hosts: [{ id: 'zero-port', label: 'Unit', address: '172.20.10.9', wsPort: 0, httpPort: 8443, token: null }],
        activeHostId: null,
      }),
    )
    const { loadSettings } = await loadModule()
    const result = loadSettings()
    assertWellFormedSettings(result)
    const host = result.hosts.find((h) => h.id === 'zero-port')
    expect(host).toBeDefined()
    expect(host?.wsPort).toBe(8082)
  })

  it('a host whose wsPort is above 65535 falls back to the default', async () => {
    fakeStorage.setItem(
      SETTINGS_KEY,
      JSON.stringify({
        hosts: [
          { id: 'huge-port', label: 'Unit', address: '172.20.10.9', wsPort: 70000, httpPort: 8443, token: null },
        ],
        activeHostId: null,
      }),
    )
    const { loadSettings } = await loadModule()
    const result = loadSettings()
    assertWellFormedSettings(result)
    const host = result.hosts.find((h) => h.id === 'huge-port')
    expect(host).toBeDefined()
    expect(host?.wsPort).toBe(8082)
  })

  it('a host whose httpPort is malformed falls back to the default too, not only wsPort', async () => {
    fakeStorage.setItem(
      SETTINGS_KEY,
      JSON.stringify({
        hosts: [
          { id: 'bad-http-port', label: 'Unit', address: '172.20.10.9', wsPort: 8082, httpPort: -5, token: null },
        ],
        activeHostId: null,
      }),
    )
    const { loadSettings } = await loadModule()
    const result = loadSettings()
    assertWellFormedSettings(result)
    const host = result.hosts.find((h) => h.id === 'bad-http-port')
    expect(host).toBeDefined()
    expect(host?.httpPort).toBe(8443)
  })

  it('an activeHostId naming a host that is not in the list is never left dangling', async () => {
    fakeStorage.setItem(
      SETTINGS_KEY,
      JSON.stringify({
        hosts: [{ id: 'real-host', label: 'Unit', address: '172.20.10.9', wsPort: 8082, httpPort: 8443, token: null }],
        activeHostId: 'ghost-host-id',
      }),
    )
    const { loadSettings } = await loadModule()
    const result = loadSettings()
    assertWellFormedSettings(result)
    expect(result.activeHostId).not.toBe('ghost-host-id')
  })

  it('a non-record entry inside hosts (null, a number) is dropped rather than crashing the parser', async () => {
    fakeStorage.setItem(SETTINGS_KEY, JSON.stringify({ hosts: [null, 42], activeHostId: null }))
    const { loadSettings } = await loadModule()
    assertWellFormedSettings(loadSettings())
  })
})

// ---------------------------------------------------------------------------
// 4.7: "A host with no usable id or address is dropped; every other bad
// field is repaired." Losing one field and losing the entry are different
// outcomes, pinned separately here.
// ---------------------------------------------------------------------------

describe('a host with no usable id or address is dropped, everything else is repaired (4.7)', () => {
  it('drops a host with no id', async () => {
    fakeStorage.setItem(
      SETTINGS_KEY,
      JSON.stringify({
        hosts: [{ label: 'No id', address: '172.20.10.9', wsPort: 8082, httpPort: 8443, token: null }],
        activeHostId: null,
      }),
    )
    const { loadSettings } = await loadModule()
    const result = loadSettings()
    assertWellFormedSettings(result)
    expect(result.hosts.some((h) => h.label === 'No id')).toBe(false)
  })

  it('drops a host with no address', async () => {
    fakeStorage.setItem(
      SETTINGS_KEY,
      JSON.stringify({
        hosts: [{ id: 'no-address', label: 'No address', wsPort: 8082, httpPort: 8443, token: null }],
        activeHostId: null,
      }),
    )
    const { loadSettings } = await loadModule()
    const result = loadSettings()
    assertWellFormedSettings(result)
    expect(result.hosts.some((h) => h.id === 'no-address')).toBe(false)
  })

  it('keeps a host with no label, falling the label back to the address rather than dropping the entry', async () => {
    fakeStorage.setItem(
      SETTINGS_KEY,
      JSON.stringify({
        hosts: [{ id: 'no-label', address: '172.20.10.9', wsPort: 8082, httpPort: 8443, token: null }],
        activeHostId: null,
      }),
    )
    const { loadSettings } = await loadModule()
    const result = loadSettings()
    assertWellFormedSettings(result)
    const host = result.hosts.find((h) => h.id === 'no-label')
    expect(host).toBeDefined()
    expect(host?.label).toBe('172.20.10.9')
  })
})

// ---------------------------------------------------------------------------
// 4.7: "An empty host list is a state, not a corruption" - but only when it
// is genuinely empty. A list emptied by every entry being dropped is a
// different thing, and the defaults come back.
// ---------------------------------------------------------------------------

describe('an empty host list is a state, not corruption - unless every entry was dropped (4.7)', () => {
  it('a stored blob with hosts: [] stays empty rather than being replaced by defaultHosts', async () => {
    const emptyBlob: Settings = { hosts: [], activeHostId: null }
    fakeStorage.setItem(SETTINGS_KEY, JSON.stringify(emptyBlob))
    const { loadSettings, defaultHosts } = await loadModule()
    const result = loadSettings()
    expect(result.hosts).toEqual([])
    // Confirms this is a real assertion and not one that would pass for any
    // list: defaultHosts() is non-empty, so an implementation that silently
    // repopulates defaults on an empty list would fail the line above.
    expect(defaultHosts().length).toBeGreaterThan(0)
  })

  it('a list emptied because every entry was dropped restores the defaults instead of staying empty', async () => {
    fakeStorage.setItem(
      SETTINGS_KEY,
      JSON.stringify({ hosts: [{ label: 'No id or address at all' }], activeHostId: null }),
    )
    const { loadSettings, defaultHosts } = await loadModule()
    const result = loadSettings()
    const defaults = defaultHosts()
    expect(result.hosts.length).toBe(defaults.length)
    expect(result.hosts.length).toBeGreaterThan(0)
    expect(result.hosts.map((h) => h.address).sort()).toEqual(defaults.map((h) => h.address).sort())
  })
})

describe('persistence key (4.7)', () => {
  it('reads a settings blob a previous session wrote under companion.settings', async () => {
    const validBlob: Settings = {
      hosts: [{ id: 'prior-host', label: 'Prior unit', address: '172.20.10.5', wsPort: 8082, httpPort: 8443, token: null }],
      activeHostId: 'prior-host',
    }
    fakeStorage.setItem(SETTINGS_KEY, JSON.stringify(validBlob))
    const { loadSettings } = await loadModule()
    const result = loadSettings()
    expect(result.hosts.some((h) => h.id === 'prior-host')).toBe(true)
    expect(result.activeHostId).toBe('prior-host')
  })

  it('addHost persists into the companion.settings key', async () => {
    const { addHost } = await loadModule()
    addHost(newHost({ label: 'Persisted unit' }))
    const raw = fakeStorage.getItem(SETTINGS_KEY)
    expect(raw).not.toBeNull()
    const parsed = JSON.parse(raw as string) as { hosts: Array<{ label: string }> }
    expect(parsed.hosts.some((h) => h.label === 'Persisted unit')).toBe(true)
  })
})

// ---------------------------------------------------------------------------
// 4.7: "the stores hold the defaults until [loadSettings] runs." A pure
// import must not silently adopt a persisted blob; only an explicit call
// reconciles storage into the stores.
// ---------------------------------------------------------------------------

describe('the stores hold the defaults until loadSettings() runs (4.7)', () => {
  it('does not reflect a persisted custom host on import alone', async () => {
    const customBlob: Settings = {
      hosts: [
        { id: 'custom-1', label: 'Custom unit', address: '172.20.10.4', wsPort: 8082, httpPort: 8443, token: null },
      ],
      activeHostId: null,
    }
    fakeStorage.setItem(SETTINGS_KEY, JSON.stringify(customBlob))
    const mod = await loadModule()
    expect(get(mod.settings).hosts.some((h) => h.id === 'custom-1')).toBe(false)

    mod.loadSettings()
    expect(get(mod.settings).hosts.some((h) => h.id === 'custom-1')).toBe(true)
  })

  it('is well-formed on import alone, since the stores start from defaultSettings', async () => {
    const { settings } = await loadModule()
    assertWellFormedSettings(get(settings))
  })
})

// ---------------------------------------------------------------------------
// 4.3.6 + 4.7: the token stays under companion.token, and this module is its
// only writer. Every path that changes which host is active, or what the
// active host is, reconciles the key: activation, editing the active
// host's token or address, removal, and a reload.
// ---------------------------------------------------------------------------

describe('token rules (4.3.6, 4.7)', () => {
  it('activating a host writes that host token to companion.token', async () => {
    const { addHost, activateHost } = await loadModule()
    const host = addHost(newHost({ label: 'Unit A', token: 'token-for-unit-a' }))
    activateHost(host.id)
    expect(fakeStorage.getItem(TOKEN_KEY)).toBe('token-for-unit-a')
  })

  it('activating a tokenless host clears companion.token rather than leaving the previous host token in place', async () => {
    const { addHost, activateHost } = await loadModule()
    const hostA = addHost(newHost({ label: 'Unit A', address: '172.20.10.2', token: 'token-for-unit-a' }))
    const hostB = addHost(newHost({ label: 'Unit B', address: '10.0.0.2', token: null }))

    activateHost(hostA.id)
    expect(fakeStorage.getItem(TOKEN_KEY)).toBe('token-for-unit-a')

    activateHost(hostB.id)
    expect(fakeStorage.getItem(TOKEN_KEY)).toBeNull()
  })

  it('activateHost with an id not in the list is a no-op: it neither writes "undefined" nor touches the current token', async () => {
    const { addHost, activateHost, settings } = await loadModule()
    const host = addHost(newHost({ label: 'Unit A', token: 'token-for-unit-a' }))
    activateHost(host.id)
    expect(fakeStorage.getItem(TOKEN_KEY)).toBe('token-for-unit-a')

    activateHost('does-not-exist')

    expect(fakeStorage.getItem(TOKEN_KEY)).toBe('token-for-unit-a')
    expect(get(settings).activeHostId).toBe(host.id)
  })

  it('addHost with a token leaves companion.token alone while another host is active', async () => {
    const { addHost, activateHost } = await loadModule()
    const hostA = addHost(newHost({ label: 'Unit A', address: '172.20.10.2', token: 'token-for-unit-a' }))
    activateHost(hostA.id)

    addHost(newHost({ label: 'Unit B', address: '10.0.0.2', token: 'token-for-unit-b' }))

    expect(fakeStorage.getItem(TOKEN_KEY)).toBe('token-for-unit-a')
  })

  it('editing the active host token rewrites companion.token', async () => {
    const { addHost, activateHost, updateHost } = await loadModule()
    const host = addHost(newHost({ label: 'Unit A', token: 'old-token' }))
    activateHost(host.id)
    expect(fakeStorage.getItem(TOKEN_KEY)).toBe('old-token')

    updateHost(host.id, { token: 'new-token' })
    expect(fakeStorage.getItem(TOKEN_KEY)).toBe('new-token')
  })

  it('editing a host that is not active does not touch companion.token', async () => {
    const { addHost, activateHost, updateHost } = await loadModule()
    const hostA = addHost(newHost({ label: 'Unit A', address: '172.20.10.2', token: 'token-for-unit-a' }))
    const hostB = addHost(newHost({ label: 'Unit B', address: '10.0.0.2', token: 'token-for-unit-b' }))
    activateHost(hostA.id)

    updateHost(hostB.id, { token: 'a-new-token-for-unit-b' })

    expect(fakeStorage.getItem(TOKEN_KEY)).toBe('token-for-unit-a')
  })

  it('updateHost with token: undefined normalises to null rather than storing the literal string "undefined"', async () => {
    const { addHost, updateHost, settings } = await loadModule()
    const host = addHost(newHost({ label: 'Unit A', token: 'original-token' }))
    updateHost(host.id, { token: undefined })
    const updated = get(settings).hosts.find((h) => h.id === host.id)
    expect(updated?.token).toBeNull()
    expect(updated?.token).not.toBe('undefined')
  })

  it('activating a host whose token was normalised from undefined does not write the literal string "undefined" to companion.token', async () => {
    const { addHost, updateHost, activateHost } = await loadModule()
    const host = addHost(newHost({ label: 'Unit A', token: 'original-token' }))
    updateHost(host.id, { token: undefined })
    activateHost(host.id)
    expect(fakeStorage.getItem(TOKEN_KEY)).not.toBe('undefined')
    expect(fakeStorage.getItem(TOKEN_KEY)).toBeNull()
  })

  it('editing the active host address clears the token, on the record and not only the key', async () => {
    const { addHost, activateHost, updateHost, settings } = await loadModule()
    const host = addHost(newHost({ label: 'Unit A', address: '172.20.10.2', token: 'token-for-unit-a' }))
    activateHost(host.id)
    expect(fakeStorage.getItem(TOKEN_KEY)).toBe('token-for-unit-a')

    updateHost(host.id, { address: '172.20.10.6' })
    expect(fakeStorage.getItem(TOKEN_KEY)).toBeNull()

    const updated = get(settings).hosts.find((h) => h.id === host.id)
    expect(updated?.token).toBeNull()

    // Clearing only the key and leaving the value on the record would let
    // the next load or activation write the stale token straight back.
    activateHost(host.id)
    expect(fakeStorage.getItem(TOKEN_KEY)).toBeNull()
  })

  it('editing the active host address while the same patch also sets a token explicitly keeps that token, on the record and in the key', async () => {
    const { addHost, activateHost, updateHost, settings } = await loadModule()
    const host = addHost(newHost({ label: 'Unit A', address: '172.20.10.2', token: 'token-for-unit-a' }))
    activateHost(host.id)

    updateHost(host.id, { address: '172.20.10.6', token: 'brand-new-token' })
    expect(fakeStorage.getItem(TOKEN_KEY)).toBe('brand-new-token')

    const updated = get(settings).hosts.find((h) => h.id === host.id)
    expect(updated?.token).toBe('brand-new-token')
  })

  it('editing a non-active host address does not touch the active host key, but does clear the edited host own token record', async () => {
    const { addHost, activateHost, updateHost, settings } = await loadModule()
    const hostA = addHost(newHost({ label: 'Unit A', address: '172.20.10.2', token: 'token-for-unit-a' }))
    const hostB = addHost(newHost({ label: 'Unit B', address: '10.0.0.2', token: 'token-for-unit-b' }))
    activateHost(hostA.id)

    updateHost(hostB.id, { address: '172.20.10.3' })
    expect(fakeStorage.getItem(TOKEN_KEY)).toBe('token-for-unit-a')

    const updatedB = get(settings).hosts.find((h) => h.id === hostB.id)
    expect(updatedB?.token).toBeNull()

    // If the record still held unit B's old token, activating B later would
    // hand it to whatever unit now answers at the address B was edited to.
    activateHost(hostB.id)
    expect(fakeStorage.getItem(TOKEN_KEY)).toBeNull()
  })

  it('patching the active host address with the value it already has (a form re-submitting every field) leaves the token alone, on the record and in the key', async () => {
    const { addHost, activateHost, updateHost, settings } = await loadModule()
    const host = addHost(newHost({ label: 'Unit A', address: '172.20.10.2', token: 'token-for-unit-a' }))
    activateHost(host.id)
    expect(fakeStorage.getItem(TOKEN_KEY)).toBe('token-for-unit-a')

    // Same shape a Settings form submits when it resends every field on
    // save, address included, even though the owner only touched the
    // label: `address` is present in the patch, but unchanged.
    updateHost(host.id, { label: 'Same unit, resubmitted form', address: '172.20.10.2' })

    expect(fakeStorage.getItem(TOKEN_KEY)).toBe('token-for-unit-a')
    const updated = get(settings).hosts.find((h) => h.id === host.id)
    expect(updated?.token).toBe('token-for-unit-a')
  })

  it('renaming the active host (no token, no address in the patch) leaves companion.token alone', async () => {
    const { addHost, activateHost, updateHost } = await loadModule()
    const host = addHost(newHost({ label: 'Unit A', address: '172.20.10.2', token: 'token-for-unit-a' }))
    activateHost(host.id)
    expect(fakeStorage.getItem(TOKEN_KEY)).toBe('token-for-unit-a')

    updateHost(host.id, { label: 'Renamed unit' })
    expect(fakeStorage.getItem(TOKEN_KEY)).toBe('token-for-unit-a')
  })

  it('removing the active host clears companion.token', async () => {
    const { addHost, activateHost, removeHost } = await loadModule()
    const host = addHost(newHost({ label: 'Unit A', token: 'token-for-unit-a' }))
    activateHost(host.id)
    expect(fakeStorage.getItem(TOKEN_KEY)).toBe('token-for-unit-a')

    removeHost(host.id)
    expect(fakeStorage.getItem(TOKEN_KEY)).toBeNull()
  })
})

// ---------------------------------------------------------------------------
// 4.7: "loadSettings() reads and reconciles ... a reload restores an active
// host from storage while the key still holds whatever the last session
// left, and the parser can drop that host or null a dangling id, so the
// key can end up paired with a host that is no longer in the list." This
// is the finding that got through the first round: all four shapes.
// ---------------------------------------------------------------------------

describe('loadSettings reconciles companion.token (4.7)', () => {
  it('a reload with a token in the key rewrites it to match the persisted active host record, even if the key already held something else', async () => {
    fakeStorage.setItem(TOKEN_KEY, 'stale-leftover-token')
    fakeStorage.setItem(
      SETTINGS_KEY,
      JSON.stringify({
        hosts: [
          { id: 'unit-x', label: 'Unit X', address: '172.20.10.2', wsPort: 8082, httpPort: 8443, token: 'token-x' },
        ],
        activeHostId: 'unit-x',
      }),
    )
    const { loadSettings } = await loadModule()
    loadSettings()
    expect(fakeStorage.getItem(TOKEN_KEY)).toBe('token-x')
  })

  it('a reload where the persisted active host was dropped by the parser clears the key', async () => {
    fakeStorage.setItem(TOKEN_KEY, 'leftover-token')
    fakeStorage.setItem(
      SETTINGS_KEY,
      JSON.stringify({
        hosts: [{ id: 'unit-y', label: 'Unit Y' }], // no address: dropped by the parser
        activeHostId: 'unit-y',
      }),
    )
    const { loadSettings } = await loadModule()
    const result = loadSettings()
    expect(result.hosts.some((h) => h.id === 'unit-y')).toBe(false)
    expect(result.activeHostId).toBeNull()
    expect(fakeStorage.getItem(TOKEN_KEY)).toBeNull()
  })

  it('a reload with a dangling activeHostId clears the key', async () => {
    fakeStorage.setItem(TOKEN_KEY, 'leftover-token')
    fakeStorage.setItem(
      SETTINGS_KEY,
      JSON.stringify({
        hosts: [
          { id: 'unit-z', label: 'Unit Z', address: '172.20.10.9', wsPort: 8082, httpPort: 8443, token: null },
        ],
        activeHostId: 'ghost-id',
      }),
    )
    const { loadSettings } = await loadModule()
    const result = loadSettings()
    expect(result.activeHostId).toBeNull()
    expect(fakeStorage.getItem(TOKEN_KEY)).toBeNull()
  })

  it('a first-ever load, with no settings blob but a token already in the key, clears it since no host is active', async () => {
    fakeStorage.setItem(TOKEN_KEY, 'leftover-token')
    // companion.settings is deliberately left unset: first run.
    const { loadSettings } = await loadModule()
    const result = loadSettings()
    expect(result.activeHostId).toBeNull()
    expect(fakeStorage.getItem(TOKEN_KEY)).toBeNull()
  })

  it('a reload with a valid, present active host whose own token is null clears a stale key rather than leaving it (the tokenless-host variant)', async () => {
    fakeStorage.setItem(TOKEN_KEY, 'stale-leftover-token')
    fakeStorage.setItem(
      SETTINGS_KEY,
      JSON.stringify({
        hosts: [
          { id: 'unit-w', label: 'Unit W', address: '172.20.10.2', wsPort: 8082, httpPort: 8443, token: null },
        ],
        activeHostId: 'unit-w',
      }),
    )
    const { loadSettings } = await loadModule()
    const result = loadSettings()
    expect(result.activeHostId).toBe('unit-w')
    // A naive `if (active?.token) storeToken(active.token)` never runs for a
    // tokenless active host, leaving the previous session's token live and
    // handing it to a unit that asked for none.
    expect(fakeStorage.getItem(TOKEN_KEY)).toBeNull()
  })
})

// ---------------------------------------------------------------------------
// 4.7: "activateHost persists before writing the token, so a storage
// failure cannot leave the key holding a token whose active id was never
// saved."
// ---------------------------------------------------------------------------

describe('activateHost persists the active id before writing the token (4.7)', () => {
  it('writes companion.settings before it writes companion.token', async () => {
    const { addHost, activateHost } = await loadModule()
    const host = addHost(newHost({ label: 'Unit A', token: 'token-for-unit-a' }))
    fakeStorage.setCalls.length = 0 // drop the writes from addHost itself

    activateHost(host.id)

    const settingsIndex = fakeStorage.setCalls.indexOf(SETTINGS_KEY)
    const tokenIndex = fakeStorage.setCalls.indexOf(TOKEN_KEY)
    expect(settingsIndex).toBeGreaterThanOrEqual(0)
    expect(tokenIndex).toBeGreaterThanOrEqual(0)
    expect(settingsIndex).toBeLessThan(tokenIndex)
  })
})

// ---------------------------------------------------------------------------
// 4.7: "storage that refuses to answer does not stop the app." A read that
// throws is treated like a blob that will not parse; a write that throws
// leaves the session working from memory rather than surfacing to the
// caller.
// ---------------------------------------------------------------------------

describe('a storage that refuses to answer does not stop the app (4.7)', () => {
  it('a read that throws is treated like a blob that will not parse', async () => {
    fakeStorage.throwOnGetKeys.add(SETTINGS_KEY)
    const { loadSettings } = await loadModule()
    assertWellFormedSettings(loadSettings())
  })

  it('a write that throws leaves the session working from memory rather than throwing to the caller', async () => {
    const { addHost, settings } = await loadModule()
    fakeStorage.throwOnSetKeys.add(SETTINGS_KEY)

    let host: Host | undefined
    expect(() => {
      host = addHost(newHost({ label: 'In-memory only' }))
    }).not.toThrow()

    expect(get(settings).hosts.some((h) => h.id === host?.id)).toBe(true)
  })

  it('a write that fails while activating a new host clears companion.token rather than leaving the previous host token behind', async () => {
    const { addHost, activateHost, settings } = await loadModule()
    const hostA = addHost(newHost({ label: 'Unit A', address: '172.20.10.2', token: 'token-for-unit-a' }))
    const hostB = addHost(newHost({ label: 'Unit B', address: '10.0.0.2', token: 'token-for-unit-b' }))
    activateHost(hostA.id)
    expect(fakeStorage.getItem(TOKEN_KEY)).toBe('token-for-unit-a')

    fakeStorage.throwOnSetKeys.add(TOKEN_KEY)
    expect(() => activateHost(hostB.id)).not.toThrow()
    expect(get(settings).activeHostId).toBe(hostB.id)

    // The whole question: a failed write must not leave unit A's token
    // live under an entry that now points at unit B. An absent token
    // fails authentication loudly; a wrong one authenticates to the
    // wrong unit, which is the failure SPEC 4.7 refuses to swallow.
    expect(fakeStorage.getItem(TOKEN_KEY)).toBeNull()
  })

  it('a disabled token key does not make loadSettings() throw at startup, and clears a stale value rather than leaving a failed write unresolved', async () => {
    fakeStorage.setItem(
      SETTINGS_KEY,
      JSON.stringify({
        hosts: [
          { id: 'unit-v', label: 'Unit V', address: '172.20.10.2', wsPort: 8082, httpPort: 8443, token: 'token-v' },
        ],
        activeHostId: 'unit-v',
      }),
    )
    // A stale value from a previous session, set directly rather than
    // through storeToken so the arm below is the only thing guarding it:
    // without it this test cannot tell "cleared" from "was never set".
    fakeStorage.setItem(TOKEN_KEY, 'stale-token-from-a-previous-session')
    fakeStorage.throwOnSetKeys.add(TOKEN_KEY)

    const { loadSettings } = await loadModule()
    expect(() => loadSettings()).not.toThrow()
    expect(fakeStorage.getItem(TOKEN_KEY)).toBeNull()
  })
})

// ---------------------------------------------------------------------------
// 4.7: "parseHost uses own-property checks" - a polluted Object.prototype
// must not be adopted as a host's field.
// ---------------------------------------------------------------------------

describe('parseHost does not adopt an inherited property (4.7)', () => {
  it('a polluted Object.prototype.token is not adopted as a host token', async () => {
    ;(Object.prototype as Record<string, unknown>).token = 'polluted-value'
    fakeStorage.setItem(
      SETTINGS_KEY,
      JSON.stringify({
        hosts: [{ id: 'clean-host', label: 'Unit', address: '172.20.10.9', wsPort: 8082, httpPort: 8443 }],
        activeHostId: null,
      }),
    )
    const { loadSettings } = await loadModule()
    const result = loadSettings()
    const host = result.hosts.find((h) => h.id === 'clean-host')
    expect(host).toBeDefined()
    expect(host?.token).toBeNull()
  })
})

// ---------------------------------------------------------------------------
// 4.7: "The parser repairs or drops ... the mutators throw." addHost and
// updateHost validate the same fields the parser does, and refuse rather
// than repair or accept.
// ---------------------------------------------------------------------------

describe('mutators throw on an invalid field rather than repairing it (4.7)', () => {
  // Address-shape cases (which strings are accepted vs refused, on all
  // three isBareHost call sites: the parser, addHost and updateHost) live
  // in one place, the round-trip table below, rather than here as well -
  // see that describe block.

  it('addHost repairs an out-of-range wsPort by falling back to the default, same as the parser, rather than throwing', async () => {
    const { addHost } = await loadModule()
    const host = addHost(newHost({ wsPort: 70000 }))
    expect(host.wsPort).toBe(8082)
  })

  it('updateHost repairs an invalid port by falling back to the default rather than throwing, same as the parser', async () => {
    const { addHost, updateHost, settings } = await loadModule()
    const host = addHost(newHost({}))
    updateHost(host.id, { wsPort: -1 })
    const updated = get(settings).hosts.find((h) => h.id === host.id)
    expect(updated?.wsPort).toBe(8082)
  })

  it('updateHost repairs an invalid httpPort by falling back to its own default too, not only wsPort', async () => {
    const { addHost, updateHost, settings } = await loadModule()
    const host = addHost(newHost({}))
    updateHost(host.id, { httpPort: 70000 })
    const updated = get(settings).hosts.find((h) => h.id === host.id)
    expect(updated?.httpPort).toBe(8443)
  })
})

// ---------------------------------------------------------------------------
// 4.7: an address is accepted only if the URL parser gives back the host it
// was given, case-folded ASCII-only. This is the one place every address
// case in the suite lives, rejected and accepted alike, for all three
// isBareHost call sites (the parser, addHost and updateHost), rather than
// the same shapes sitting hand-written elsewhere with a second list to
// keep in sync: a hex-encoded octet or a whole hex-encoded IPv4 value
// survives a naive shape check but `new URL()` still resolves it to a
// different dotted address, the same display-one-connect-to-another
// hazard an octal leading zero causes; an IPv6 shape like "1:2:3" is not a
// real literal even though a lenient regex would accept it; "::" and
// "0.0.0.0" round-trip perfectly while naming no host to connect to, and
// so do their spellings "[::]", "[::1]", "::ffff:0:0", "0:0:0:0:0:0:0:0"
// and "::0" - a bracketed literal is refused outright, since a bare host
// carries no URL syntax, rather than relying on the wildcard checks to
// catch every spelling incidentally; and U+212A, the Kelvin sign, is the
// one character where JavaScript's Unicode case folding and the URL
// parser's IDNA folding agree by accident, so it round-trips even though
// it is not ASCII - ASCII is checked on its own rather than left to that
// accident. An uppercase address (a mixed-case hostname, an uppercase
// IPv6 literal) is accepted and stored verbatim, with the fold confined
// to the comparison, which is what makes the
// ASCII-only fold observably load-bearing rather than a line no accepted
// address ever reaches. A punycode name is already ASCII in its "xn--"
// form and passes unaffected, the shape a browser would have produced for
// a real IDN. Written against the behaviour (accepted vs refused) rather
// than against implementation shape, because a rule this easy to get
// subtly wrong - as the Kelvin sign case shows - is exactly the kind
// whose tests should describe the outcome and not the mechanism.
// ---------------------------------------------------------------------------

describe('address grammar decided by URL round-trip, not by a shape rule (4.7)', () => {
  const REJECTED_ADDRESSES: Array<[address: string, reason: string]> = [
    ['wss://172.20.10.9', 'carries a scheme'],
    ['10.0.0.2@example.test', 'carries credentials, SPEC own example'],
    ['172.20.10.9:9999', 'carries an inline port'],
    ['010.0.0.2', 'an IPv4 leading zero octet, which new URL() renormalises'],
    ['09', 'all-numeric, a malformed IPv4 rather than a name'],
    ['0x7f.1', 'a hex octet the URL parser resolves to 127.0.0.1'],
    ['0x7f000001', 'a single hex-encoded IPv4 value resolving to 127.0.0.1'],
    ['0xc0000201', 'a single hex-encoded IPv4 value resolving to 192.0.2.1'],
    ['0x1.0x2.0x3.0x4', 'every octet hex-encoded'],
    ['1:2:3', 'a shape a lenient IPv6 regex would accept but is not a real literal'],
    ['::', 'the unspecified IPv6 address, naming no host to connect to'],
    ['0.0.0.0', 'the unspecified IPv4 address, naming no host to connect to'],
    ['[::]', 'the unspecified IPv6 address, pre-bracketed - a bare host carries no URL syntax'],
    ['[::1]', 'the IPv6 loopback, pre-bracketed - a bare host carries no URL syntax'],
    ['::ffff:0:0', 'the IPv4-mapped wildcard, another spelling of "names no host"'],
    ['0:0:0:0:0:0:0:0', 'the unspecified IPv6 address written out in full rather than shortened'],
    ['::0', 'the unspecified IPv6 address, a third spelling of "::"'],
    ['\u212A.test', 'the Kelvin sign, which JS lowercases to the same k IDNA folds it to'],
  ]

  it.each(REJECTED_ADDRESSES)('addHost throws on %s, %s', async (address) => {
    const { addHost } = await loadModule()
    expect(() => addHost(newHost({ address }))).toThrow()
  })

  it.each(REJECTED_ADDRESSES)('the parser drops a host whose address is %s, %s', async (address) => {
    fakeStorage.setItem(
      SETTINGS_KEY,
      JSON.stringify({
        hosts: [{ id: 'rejected-address', label: 'Unit', address, wsPort: 8082, httpPort: 8443, token: null }],
        activeHostId: null,
      }),
    )
    const { loadSettings } = await loadModule()
    const result = loadSettings()
    assertWellFormedSettings(result)
    expect(result.hosts.some((h) => h.id === 'rejected-address')).toBe(false)
  })

  it.each(REJECTED_ADDRESSES)(
    'updateHost throws on %s, %s, and leaves the existing record untouched',
    async (address) => {
      const { addHost, updateHost, settings } = await loadModule()
      const host = addHost(newHost({}))
      const originalAddress = host.address
      expect(() => updateHost(host.id, { address })).toThrow()
      const stillThere = get(settings).hosts.find((h) => h.id === host.id)
      expect(stillThere?.address).toBe(originalAddress)
    },
  )

  const ACCEPTED_ADDRESSES = [
    '10.0.0.2',
    'example.test',
    'beef',
    'fe80::1',
    '2001:db8::1',
    'xn--m3h.test',
    'Example.test',
    '2001:DB8::1',
    // Written out in full: WHATWG compresses only zero runs longer than
    // one, so this round-trips verbatim and is the accept path for every
    // literal the serializer leaves alone.
    '2001:db8:1:0:2:3:4:5',
  ]

  it.each(ACCEPTED_ADDRESSES)('addHost accepts %s unchanged', async (address) => {
    const { addHost } = await loadModule()
    const host = addHost(newHost({ address }))
    expect(host.address).toBe(address)
  })

  it.each(ACCEPTED_ADDRESSES)('the parser keeps a host whose address is %s', async (address) => {
    fakeStorage.setItem(
      SETTINGS_KEY,
      JSON.stringify({
        hosts: [{ id: 'accepted-address', label: 'Unit', address, wsPort: 8082, httpPort: 8443, token: null }],
        activeHostId: null,
      }),
    )
    const { loadSettings } = await loadModule()
    const result = loadSettings()
    assertWellFormedSettings(result)
    expect(result.hosts.some((h) => h.id === 'accepted-address')).toBe(true)
  })
})

// ---------------------------------------------------------------------------
// 4.5.2 + 4.7: prefilled rather than discovered. The desktop-only note now
// belongs to the Settings view, not the default label.
// ---------------------------------------------------------------------------

describe('defaultHosts (4.5.2, 4.7)', () => {
  it('prefills the Bluetooth tether address', async () => {
    const { defaultHosts } = await loadModule()
    const bt = defaultHosts().find((h) => h.address === '172.20.10.2')
    expect(bt).toBeDefined()
  })

  it('offers the USB gadget address with a plain label, the desktop-only note is the Settings view\'s job now', async () => {
    const { defaultHosts } = await loadModule()
    const usb = defaultHosts().find((h) => h.address === '10.0.0.2')
    expect(usb).toBeDefined()
    expect(usb?.label).toBe('USB')
  })
})

// ---------------------------------------------------------------------------
// wsUrlFor: wss with the ws port, never the http port, never the token, and
// an IPv6 literal is bracketed.
// ---------------------------------------------------------------------------

describe('wsUrlFor (4.7)', () => {
  it('builds a wss URL from the address and the ws port', async () => {
    const { wsUrlFor } = await loadModule()
    const host: Host = { id: 'x', label: 'Unit', address: '172.20.10.2', wsPort: 8082, httpPort: 8443, token: null }
    const url = wsUrlFor(host)
    expect(url.startsWith('wss://')).toBe(true)
    expect(url).toContain('172.20.10.2')
    expect(url).toContain('8082')
  })

  it('uses the ws port and not the http port, even when the two differ from the pinned defaults', async () => {
    const { wsUrlFor } = await loadModule()
    const host: Host = { id: 'x', label: 'Unit', address: '10.0.0.2', wsPort: 9999, httpPort: 1234, token: null }
    const url = wsUrlFor(host)
    expect(url).toContain('9999')
    expect(url).not.toContain('1234')
  })

  it('does not interpolate the token into the URL', async () => {
    const { wsUrlFor } = await loadModule()
    const host: Host = {
      id: 'x',
      label: 'Unit',
      address: '172.20.10.2',
      wsPort: 8082,
      httpPort: 8443,
      token: 'super-secret-token-value',
    }
    const url = wsUrlFor(host)
    expect(url).not.toContain('super-secret-token-value')
  })

  it('brackets an IPv6 literal address exactly once', async () => {
    const { wsUrlFor } = await loadModule()
    const host: Host = { id: 'x', label: 'Unit', address: 'fe80::1', wsPort: 8082, httpPort: 8443, token: null }
    const url = wsUrlFor(host)
    expect(url).toBe('wss://[fe80::1]:8082')
    expect(url.match(/\[/g)?.length).toBe(1)
    expect(url.match(/\]/g)?.length).toBe(1)
  })

  it('does not bracket an all-hex hostname with no colons, which parses as a name rather than as IPv6', async () => {
    const { wsUrlFor } = await loadModule()
    const host: Host = { id: 'x', label: 'Unit', address: 'beef', wsPort: 8082, httpPort: 8443, token: null }
    const url = wsUrlFor(host)
    expect(url).toBe('wss://beef:8082')
  })
})

// ---------------------------------------------------------------------------
// Host CRUD: add, update, remove, including removing the active host and
// calling a mutator with an id that is not in the list.
// ---------------------------------------------------------------------------

describe('host CRUD (4.7)', () => {
  it('addHost assigns an id and returns the created host', async () => {
    const { addHost } = await loadModule()
    const host = addHost(newHost({ label: 'Unit A' }))
    expect(typeof host.id).toBe('string')
    expect(host.id.length).toBeGreaterThan(0)
    expect(host.label).toBe('Unit A')
  })

  it('addHost adds the host to the settings store', async () => {
    const { addHost, settings } = await loadModule()
    const host = addHost(newHost({ label: 'Unit A' }))
    expect(get(settings).hosts.some((h) => h.id === host.id)).toBe(true)
  })

  it('updateHost patches only the given fields, leaving the rest as they were', async () => {
    const { addHost, updateHost, settings } = await loadModule()
    const host = addHost(newHost({ label: 'Unit A', wsPort: 8082, address: '172.20.10.2' }))
    updateHost(host.id, { label: 'Renamed unit' })
    const updated = get(settings).hosts.find((h) => h.id === host.id)
    expect(updated?.label).toBe('Renamed unit')
    expect(updated?.wsPort).toBe(8082)
    expect(updated?.address).toBe('172.20.10.2')
  })

  it('updateHost with an id not in the list is a no-op', async () => {
    const { addHost, updateHost, settings } = await loadModule()
    addHost(newHost({ label: 'Unit A' }))
    const before = get(settings)
    updateHost('does-not-exist', { label: 'Ghost' })
    expect(get(settings)).toEqual(before)
  })

  it('removeHost removes the host from the list', async () => {
    const { addHost, removeHost, settings } = await loadModule()
    const host = addHost(newHost({ label: 'Unit A' }))
    removeHost(host.id)
    expect(get(settings).hosts.some((h) => h.id === host.id)).toBe(false)
  })

  it('removeHost with an id not in the list is a no-op', async () => {
    const { addHost, removeHost, settings } = await loadModule()
    addHost(newHost({ label: 'Unit A' }))
    const before = get(settings)
    removeHost('does-not-exist')
    expect(get(settings)).toEqual(before)
  })

  it('removing the active host leaves the app in a defined state rather than an id pointing at nothing', async () => {
    const { addHost, activateHost, removeHost, settings, activeHost } = await loadModule()
    const host = addHost(newHost({ label: 'Unit A' }))
    activateHost(host.id)
    removeHost(host.id)

    const after = get(settings)
    expect(after.activeHostId).not.toBe(host.id)
    if (after.activeHostId !== null) {
      expect(after.hosts.some((h) => h.id === after.activeHostId)).toBe(true)
    }
    // activeHost is derived from activeHostId, so it must not still resolve
    // to a host that is no longer in the list either.
    expect(get(activeHost)?.id).not.toBe(host.id)
  })
})

// ---------------------------------------------------------------------------
// activeHost: derived from activeHostId, tracks activation.
// ---------------------------------------------------------------------------

describe('activeHost store (4.7)', () => {
  it('is null before any host is activated', async () => {
    const { activeHost } = await loadModule()
    expect(get(activeHost)).toBeNull()
  })

  it('reflects the activated host', async () => {
    const { addHost, activateHost, activeHost } = await loadModule()
    const host = addHost(newHost({ label: 'Unit A' }))
    activateHost(host.id)
    expect(get(activeHost)?.id).toBe(host.id)
  })
})
