import { get } from 'svelte/store'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { AccessPoint, OutgoingAccessPoints, OutgoingLogLines, OutgoingMessage, OutgoingPeersList, Peer } from '../lib/protocol'
import type { Host } from '../lib/settings'
import type { ConnectionState, Diagnostics, UnauthorizedReason, WsClient, WsClientOptions } from '../lib/ws'

// Written from SPEC.md 4.8, 4.4.2 and 4.7, and the fixed public surface
// handed to the test author for issue #119. Deliberately not read from
// lib/session.ts, which is authored in parallel.
//
// session.ts is the join of three layers that otherwise know nothing of
// each other (4.8): lib/ws.ts (faked here through SessionDeps.createClient,
// never a real socket), lib/stores.ts (real: connectStores/resetStores are
// the ones this suite's assertions actually depend on) and lib/settings.ts
// (real: activeHost is driven through addHost/updateHost/activateHost, not
// poked directly).
//
// A fresh module instance per test for all three modules together, so a
// singleton in one (attachedClient in stores.ts, settingsWritable in
// settings.ts) never leaks from one test into the next, and so that the
// settings.ts instance session.ts subscribes to is the same instance the
// test drives.

// ---------------------------------------------------------------------------
// A fake localStorage, same shape as settings.spec.ts's: settings.ts and
// ws.ts (storeToken/readStoredToken) both touch window.localStorage, and
// real storage is never touched.
// ---------------------------------------------------------------------------

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

let fakeStorage: FakeStorage

const SETTINGS_KEY = 'companion.settings'
const T = 1700000000

beforeEach(() => {
  fakeStorage = new FakeStorage()
  vi.stubGlobal('localStorage', fakeStorage)
  vi.resetModules()
})

afterEach(() => {
  vi.unstubAllGlobals()
})

/** All three modules loaded together after the same resetModules(), so they share their singletons for the test. */
async function loadModules() {
  const settings = await import('../lib/settings')
  const stores = await import('../lib/stores')
  const session = await import('../lib/session')
  return { ...settings, ...stores, ...session }
}

function newHost(overrides: Partial<Omit<Host, 'id'>> = {}): Omit<Host, 'id'> {
  return {
    label: 'Test unit',
    address: '172.20.10.9',
    wsPort: 8082,
    httpPort: 8443,
    token: null,
    ...overrides,
  }
}

/**
 * Reaches "no host active" deliberately, by persisting a settings blob whose
 * `hosts` is already a valid empty array (SPEC 4.7: "An empty host list is a
 * state, not a corruption ... a stored blob whose hosts is a valid empty
 * array is left empty"). Since issue #134, `loadSettings()` on a *missing*
 * blob derives an active entry (falling back to the prefilled Bluetooth
 * default) rather than leaving nothing active, so most of this file's tests
 * -- which are about the session's own behaviour once it holds a host list
 * it built through addHost()/activateHost(), not about what a fresh install
 * does -- need to reach their "nothing active yet" starting point this way
 * instead of relying on that default by accident.
 */
function seedNoActiveHost(): void {
  fakeStorage.setItem(SETTINGS_KEY, JSON.stringify({ hosts: [], activeHostId: null }))
}

// ---------------------------------------------------------------------------
// Fixture builders for the messages the "real data through the client" tests
// push, matching the shapes stores.spec.ts already validated against
// docs/schemas/. Realistic shapes, synthetic content only.
// ---------------------------------------------------------------------------

function accessPoint(overrides: Partial<AccessPoint> = {}): AccessPoint {
  return {
    bssid: 'AA:BB:CC:DD:EE:01',
    hostname: 'TestNet_001',
    channel: 6,
    rssi: -55,
    encryption: 'WPA2',
    vendor: 'TestVendor',
    clients: 0,
    ...overrides,
  }
}

function peer(overrides: Partial<Peer> = {}): Peer {
  return {
    name: 'unit-alpha',
    fingerprint: 'fingerprint-aaaa1111',
    fullName: 'unit-alpha@fingerprint-aaaa1111',
    rssi: -60,
    channel: 6,
    firstSeen: T,
    prevSeen: T,
    firstMet: T,
    lastSeen: T + 0.5,
    encounters: 3,
    pwndRun: 1,
    pwndTotal: 12,
    version: '1.2.3',
    uptime: 3600,
    face: '(^_^)',
    ...overrides,
  }
}

function accessPointsEnvelope(list: AccessPoint[]): OutgoingAccessPoints {
  return { type: 'access_points', timestamp: T, data: list }
}

function peersListEnvelope(entries: Peer[]): OutgoingPeersList {
  return { type: 'peers_list', timestamp: T, data: { entries } }
}

function logLinesEnvelope(lines: string[], path = '/tmp/pwnagotchi.log'): OutgoingLogLines {
  return { type: 'log_lines', timestamp: T, data: { lines, path } }
}

/** Starts a session with one host already active, for the tests below that drive a further mutation and check whether the client is rebuilt. Shared by the rebuild-trigger and active-host-disappearing suites. */
async function withOneClient() {
  const mods = await loadModules()
  const { loadSettings, addHost, activateHost, startSession } = mods
  seedNoActiveHost()
  loadSettings()
  const host = addHost(newHost({ label: 'Original label', address: '172.20.10.9', token: 'tok-a' }))
  const created: FakeWsClient[] = []
  const stop = startSession({
    createClient: (options) => {
      const c = new FakeWsClient(options)
      created.push(c)
      return asClient(c)
    },
  })
  activateHost(host.id)
  expect(created).toHaveLength(1)
  return { ...mods, host, created, stop }
}

// ---------------------------------------------------------------------------
// The double. Records connect()/close() calls and the options each client
// was built with, lets a test seed lastStats() before the store layer's
// attach-time seeding runs (SPEC 4.4.2), and otherwise never does anything
// on its own: no socket, no timers.
// ---------------------------------------------------------------------------

class FakeWsClient {
  readonly options: WsClientOptions
  connectCalls = 0
  closeCalls = 0
  private currentState: ConnectionState = 'offline'
  private reasonValue: UnauthorizedReason | null = null
  private lastStatsValue: unknown = null
  private readonly stateHandlers = new Set<(state: ConnectionState) => void>()
  private readonly messageHandlers = new Set<(message: OutgoingMessage) => void>()

  constructor(options: WsClientOptions) {
    this.options = options
  }

  connect(): void {
    this.connectCalls += 1
  }

  close(): void {
    this.closeCalls += 1
    // Mirrors the real client (lib/ws.ts): close() transitions to offline
    // and notifies onState synchronously. Nothing in this suite depends on
    // that ordering relative to the stores-teardown unsubscribe -- SPEC
    // 4.4/4.4.2's own rule (connectStores' teardown sets `connection` to
    // `offline` unconditionally once no client is attached) is what a
    // reader should look to below, not this method's call order.
    this.emitState('offline')
  }

  state(): ConnectionState {
    return this.currentState
  }

  unauthorizedReason(): UnauthorizedReason | null {
    return this.reasonValue
  }

  lastStats(): unknown {
    return this.lastStatsValue
  }

  onState(handler: (state: ConnectionState) => void): () => void {
    this.stateHandlers.add(handler)
    return () => this.stateHandlers.delete(handler)
  }

  onMessage(handler: (message: OutgoingMessage) => void): () => void {
    this.messageHandlers.add(handler)
    return () => this.messageHandlers.delete(handler)
  }

  request(): Promise<never> {
    return new Promise(() => {
      // Never settles: nothing in this suite awaits a read.
    })
  }

  command(): Promise<never> {
    return Promise.reject(new Error('session.spec: no test in this file issues a command'))
  }

  sendGps(): void {
    // Not exercised: no test in this file drives GPS.
  }

  diagnostics(): Diagnostics {
    // Not exercised: no test in this file asserts on diagnostics content.
    return { lastError: null, latencyMs: null }
  }

  onDiagnostics(): () => void {
    // Not exercised: no test in this file drives diagnostics.
    return () => {}
  }

  /** Test-only: what connectStores' attach-time seeding (SPEC 4.4.2) will read for lastStats(). */
  primeLastStats(stats: unknown): void {
    this.lastStatsValue = stats
  }

  /** Test-only: simulates the client entering a new state, exactly as onState would deliver it. */
  emitState(state: ConnectionState, reason: UnauthorizedReason | null = null): void {
    this.currentState = state
    this.reasonValue = reason
    for (const handler of [...this.stateHandlers]) handler(state)
  }

  /** Test-only: simulates a frame arriving from the plugin, the way stores.spec.ts's own double does -- this is what "real data through the client" means for the stop()-clears-real-data test below. */
  emitMessage(message: OutgoingMessage): void {
    for (const handler of [...this.messageHandlers]) handler(message)
  }
}

/** as WsClient: the double implements the runtime shape session.ts/connectStores actually use; the generic request/command signatures are typed against ws.ts internals this suite never calls through. */
function asClient(fake: FakeWsClient): WsClient {
  return fake as unknown as WsClient
}

// ---------------------------------------------------------------------------
// 4.8 + 4.4.2: nothing else creates a client, and currentClient() reflects it.
// ---------------------------------------------------------------------------

describe('currentClient() and who is allowed to create one (4.8)', () => {
  it('is null before any start', async () => {
    const { currentClient } = await loadModules()
    expect(currentClient()).toBeNull()
  })

  it('a fresh install already has an active host, and startSession() builds exactly one client for it (SPEC 4.7, issue #134)', async () => {
    // No companion.settings blob at all: a genuine first-ever load. Before
    // #134, defaultSettings() left activeHostId null and this test asserted
    // that startSession() built nothing; #134 reverses that -- the active
    // entry is now derived from the origin, falling back to the prefilled
    // Bluetooth default when the origin is not a usable IPv4 literal. The
    // fallback is forced deterministically here with the getHostname
    // override rather than relied on via jsdom's own default location,
    // which settings-origin.spec.ts already owns testing in full; this test
    // only pins that the session reacts to that default the same way it
    // reacts to any other active host.
    const { loadSettings, startSession, currentClient } = await loadModules()
    loadSettings(() => 'not-an-ip-address')

    const created: FakeWsClient[] = []
    const stop = startSession({
      createClient: (options) => {
        const c = new FakeWsClient(options)
        created.push(c)
        return asClient(c)
      },
    })

    expect(created).toHaveLength(1)
    expect(currentClient()).toBe(asClient(created[0]!))
    stop()
  })

  it('creates exactly one client when a host becomes active after start, and currentClient() is it', async () => {
    const { loadSettings, addHost, activateHost, startSession, currentClient } = await loadModules()
    seedNoActiveHost() // reached deliberately (4.7): nothing active until this test activates its own host
    loadSettings()
    const host = addHost(newHost({ label: 'Unit A' }))

    const created: FakeWsClient[] = []
    const stop = startSession({
      createClient: (options) => {
        const c = new FakeWsClient(options)
        created.push(c)
        return asClient(c)
      },
    })
    expect(created).toHaveLength(0)
    // Dropped when this test was repurposed for the fresh-install case
    // above; put back here, where "nothing active yet" is reached
    // deliberately rather than by accident (SPEC 4.7's seedNoActiveHost()
    // above), because "builds no client" alone does not pin that
    // currentClient() itself reads null while nothing is active.
    expect(currentClient()).toBeNull()

    activateHost(host.id)

    expect(created).toHaveLength(1)
    expect(currentClient()).toBe(asClient(created[0]!))
    expect(created[0]!.connectCalls).toBe(1)
    stop()
  })

  it('is null again after stop(), and stop() releases the client', async () => {
    const { loadSettings, addHost, activateHost, startSession, currentClient, connection } = await loadModules()
    seedNoActiveHost() // reached deliberately (4.7): nothing active until this test activates its own host
    loadSettings()
    const host = addHost(newHost())
    const created: FakeWsClient[] = []
    const stop = startSession({
      createClient: (options) => {
        const c = new FakeWsClient(options)
        created.push(c)
        return asClient(c)
      },
    })
    activateHost(host.id)
    expect(created).toHaveLength(1)
    created[0]!.emitState('connected')
    expect(get(connection).state).toBe('connected')

    stop()

    expect(currentClient()).toBeNull()
    expect(created[0]!.closeCalls).toBe(1)
    // SPEC 4.4.2: with no client attached, connection reads offline -- not
    // the stale 'connected' the client last reported before stop() tore it
    // down. Enforced by connectStores' own teardown, called through this
    // module's teardown().
    expect(get(connection).state).toBe('offline')
  })

  it('calling the same stop twice is harmless: no second teardown, no throw, the client released exactly once', async () => {
    const { loadSettings, addHost, activateHost, startSession, currentClient } = await loadModules()
    seedNoActiveHost() // reached deliberately (4.7): nothing active until this test activates its own host
    loadSettings()
    const host = addHost(newHost())
    const created: FakeWsClient[] = []
    const stop = startSession({
      createClient: (options) => {
        const c = new FakeWsClient(options)
        created.push(c)
        return asClient(c)
      },
    })
    activateHost(host.id)
    expect(created).toHaveLength(1)

    stop()
    expect(() => stop()).not.toThrow()

    expect(currentClient()).toBeNull()
    expect(created[0]!.closeCalls).toBe(1)
  })

  it('a stale stop from a previous session does not tear down the session started after it', async () => {
    const { loadSettings, addHost, activateHost, startSession, currentClient } = await loadModules()
    seedNoActiveHost() // reached deliberately (4.7): nothing active until this test activates its own host
    loadSettings()
    const hostA = addHost(newHost({ label: 'Unit A', address: '172.20.10.9' }))
    const hostB = addHost(newHost({ label: 'Unit B', address: '172.20.10.3' }))
    const created: FakeWsClient[] = []
    const createClient = (options: WsClientOptions) => {
      const c = new FakeWsClient(options)
      created.push(c)
      return asClient(c)
    }

    const firstStop = startSession({ createClient })
    activateHost(hostA.id)
    expect(created).toHaveLength(1)
    firstStop()
    expect(currentClient()).toBeNull()

    // The second session's own subscribe() replays the still-active hostA
    // first (startSession does not touch which host is active, only stop()
    // does not either), then activateHost(hostB) switches it again -- so
    // two more clients are built here, not one.
    const secondStop = startSession({ createClient })
    expect(created).toHaveLength(2)
    activateHost(hostB.id)
    expect(created).toHaveLength(3)
    const secondSessionClient = created[2]!
    expect(currentClient()).toBe(asClient(secondSessionClient))

    // The stale reference: calling the first session's stop a second time,
    // after its own session already ended and a new one is live. Without
    // the per-closure guard this would run teardown() again and take the
    // second session down instead of doing nothing.
    firstStop()

    expect(currentClient()).toBe(asClient(secondSessionClient))
    expect(secondSessionClient.closeCalls).toBe(0)

    secondStop()
  })

  it('stop() clears real data the session accumulated, not just an attach-time seed (SPEC 4.4.2: the caller resets after an explicit close())', async () => {
    const { loadSettings, addHost, activateHost, startSession, peers, accessPoints, log } = await loadModules()
    loadSettings()
    const host = addHost(newHost())
    let latest: FakeWsClient | null = null
    const stop = startSession({
      createClient: (options) => {
        latest = new FakeWsClient(options)
        return asClient(latest)
      },
    })
    activateHost(host.id)

    // Real data, arrived the way a live session actually gets it: pushed
    // through the client's message stream, which connectStores (SPEC 4.4)
    // writes into the stores. Priming lastStats before activation only
    // proves the attach-time seed (covered above); it says nothing about
    // whether stop() clears data the session has genuinely accumulated
    // since, which is the failure this test exists to catch: closing the
    // client releases the socket but never called resetStores(), so the
    // previous unit's access points, peers and log stayed on screen.
    latest!.emitMessage(accessPointsEnvelope([accessPoint()]))
    latest!.emitMessage(peersListEnvelope([peer()]))
    latest!.emitMessage(logLinesEnvelope(['line one', 'line two']))
    expect(get(accessPoints)).toHaveLength(1)
    expect(get(peers)).toHaveLength(1)
    expect(get(log)).toEqual(['line one', 'line two'])

    stop()

    expect(get(peers)).toEqual([])
    expect(get(accessPoints)).toEqual([])
    expect(get(log)).toEqual([])
  })

  it('starting twice refuses loudly rather than building a second client, matching connectStores', async () => {
    const { loadSettings, addHost, activateHost, startSession } = await loadModules()
    seedNoActiveHost() // reached deliberately (4.7): nothing active until this test activates its own host
    loadSettings()
    const host = addHost(newHost())
    const created: FakeWsClient[] = []
    const deps = {
      createClient: (options: WsClientOptions) => {
        const c = new FakeWsClient(options)
        created.push(c)
        return asClient(c)
      },
    }
    const stop = startSession(deps)
    activateHost(host.id)
    expect(created).toHaveLength(1)

    expect(() => startSession(deps)).toThrow()
    expect(created).toHaveLength(1)

    stop()
  })
})

// ---------------------------------------------------------------------------
// 4.8 (issue #134): "loadSettings() runs before the subscription, and since
// issue #134 that ordering decides whether a socket opens." SPEC 4.7 has
// defaultSettings() activate `bluetooth`, so settingsWritable's
// module-level initial value -- before anything has read storage -- already
// names an active host. A subscription set up ahead of the load would fire
// once for that default and build a client for it, then again once the
// load overwrote it with whatever was actually stored: a socket opened to
// 172.20.10.2 on every start, including one whose stored settings name a
// completely different unit, and a second client built right behind it.
// Nothing in the public surface enforces the order directly, so this pins
// it by observing what the session actually builds rather than by naming
// an internal call order.
// ---------------------------------------------------------------------------

describe("loadSettings() runs before the activeHost subscription (4.8, issue #134)", () => {
  it('a stored host that is not the Bluetooth default is what gets built -- not a transient client for the module default the wrong order would open first', async () => {
    const { startSession, currentClient } = await loadModules()
    // Written straight to storage, deliberately without this test calling
    // loadSettings() itself first: the whole point is what startSession()
    // does on its own, and a test that pre-loads would no longer be able
    // to tell the two orderings apart.
    fakeStorage.setItem(
      SETTINGS_KEY,
      JSON.stringify({
        hosts: [{ id: 'unit-real', label: 'Unit A', address: '172.20.10.9', wsPort: 8082, httpPort: 8443, token: null }],
        activeHostId: 'unit-real',
      }),
    )

    const created: FakeWsClient[] = []
    const stop = startSession({
      createClient: (options) => {
        const c = new FakeWsClient(options)
        created.push(c)
        return asClient(c)
      },
    })

    // Exactly one client, ever: a subscription set up before loadSettings()
    // would additionally build one for the module's own default (Bluetooth,
    // 172.20.10.2, active since #134) before the load overwrote it, and
    // that first client is counted here even though a correctly-ordered
    // rebuild would go on to tear it down again.
    expect(created).toHaveLength(1)
    // The count alone cannot rule out a wrong-order implementation whose
    // rebuild happens to collapse back to a single client with the right
    // address by chance, so the assertion names the unit: the one client
    // built must be for the stored host's address, not the default's.
    expect(created[0]!.options.url).toContain('172.20.10.9')
    expect(created[0]!.options.url).not.toContain('172.20.10.2')
    expect(currentClient()).toBe(asClient(created[0]!))

    stop()
  })
})

// ---------------------------------------------------------------------------
// 4.4.2 + 4.8: the switch is teardown, then resetStores(), then attach --
// in that order, not merely all three eventually happening.
// ---------------------------------------------------------------------------

describe('switching hosts: teardown, then resetStores(), then attach, in that order (4.4.2, 4.8)', () => {
  it('pins the order: reordering either boundary is independently observable', async () => {
    const { loadSettings, addHost, activateHost, startSession, stats } = await loadModules()
    seedNoActiveHost() // reached deliberately (4.7): nothing active until this test activates its own host
    loadSettings()
    const hostA = addHost(newHost({ label: 'Unit A', address: '172.20.10.9' }))
    const hostB = addHost(newHost({ label: 'Unit B', address: '172.20.10.3' }))

    const created: FakeWsClient[] = []
    const statsByIndex = [{ uptime: 111 }, { uptime: 222 }]
    const stop = startSession({
      createClient: (options) => {
        const c = new FakeWsClient(options)
        // Primed at creation time, i.e. available to connectStores' seeding
        // the moment attach happens -- before any push a real client could
        // not yet have received. This is what makes "attach before reset"
        // observable: a premature attach would seed this value, and a
        // reset that then runs after would wipe it again.
        c.primeLastStats(statsByIndex[created.length])
        created.push(c)
        return asClient(c)
      },
    })
    activateHost(hostA.id)
    expect(created).toHaveLength(1)

    const order: string[] = []
    const unsubscribe = stats.subscribe((value) => {
      order.push(value === null ? 'stats:null' : `stats:${(value as { uptime: number }).uptime}`)
    })
    order.length = 0 // drop the immediate replay of the current value

    activateHost(hostB.id)

    unsubscribe()

    expect(created).toHaveLength(2)
    // teardown (close on A) happens before resetStores() (stats -> null)
    // happens before attach (stats seeded from B's primed lastStats).
    // Either reordering changes this sequence, not just its endpoint: an
    // attach-before-reset bug would still end on null (the reset wipes B's
    // seed straight back out) but stats:222 would appear before stats:null
    // instead of after, or not at all.
    expect(order).toEqual(['stats:null', 'stats:222'])
    expect(created[0]!.closeCalls).toBe(1)

    stop()
  })

  it('the old client is fully detached: a state it emits after the switch reaches nobody', async () => {
    const { loadSettings, addHost, activateHost, startSession, connection } = await loadModules()
    seedNoActiveHost() // reached deliberately (4.7): nothing active until this test activates its own host
    loadSettings()
    const hostA = addHost(newHost({ label: 'Unit A', address: '172.20.10.9' }))
    const hostB = addHost(newHost({ label: 'Unit B', address: '172.20.10.3' }))
    const created: FakeWsClient[] = []
    const stop = startSession({
      createClient: (options) => {
        const c = new FakeWsClient(options)
        created.push(c)
        return asClient(c)
      },
    })
    activateHost(hostA.id)
    activateHost(hostB.id)
    expect(created).toHaveLength(2)

    created[0]!.emitState('connected')

    expect(get(connection).state).not.toBe('connected')
    stop()
  })
})

// ---------------------------------------------------------------------------
// 4.8: "It survives a Settings screen that changes nothing." The trigger is
// the connection-shaped identity (address, ports, token), not the fact that
// the host record was written.
// ---------------------------------------------------------------------------

describe('rebuild trigger: connection-shaped fields only (4.8)', () => {
  it('renaming the active host leaves the client alone', async () => {
    const { updateHost, host, created, currentClient, stop } = await withOneClient()

    updateHost(host.id, { label: 'Renamed' })

    expect(created).toHaveLength(1)
    expect(currentClient()).toBe(asClient(created[0]!))
    expect(created[0]!.closeCalls).toBe(0)
    stop()
  })

  it('changing the address rebuilds', async () => {
    const { updateHost, host, created, currentClient, stop } = await withOneClient()

    updateHost(host.id, { address: '172.20.10.6' })

    expect(created).toHaveLength(2)
    expect(currentClient()).toBe(asClient(created[1]!))
    expect(created[0]!.closeCalls).toBe(1)
    stop()
  })

  it('changing the ws port rebuilds', async () => {
    const { updateHost, host, created, stop } = await withOneClient()

    updateHost(host.id, { wsPort: 9001 })

    expect(created).toHaveLength(2)
    stop()
  })

  it('changing the http port leaves the client alone: httpPort is where the app was served from, not part of the socket', async () => {
    const { updateHost, host, created, currentClient, stop } = await withOneClient()

    updateHost(host.id, { httpPort: 9443 })

    expect(created).toHaveLength(1)
    expect(currentClient()).toBe(asClient(created[0]!))
    expect(created[0]!.closeCalls).toBe(0)
    stop()
  })

  it('changing the token rebuilds', async () => {
    const { updateHost, host, created, stop } = await withOneClient()

    updateHost(host.id, { token: 'tok-b' })

    expect(created).toHaveLength(2)
    stop()
  })

  it('editing an inactive host never rebuilds the active one', async () => {
    const { addHost, updateHost, created, currentClient, stop } = await withOneClient()
    const other = addHost(newHost({ label: 'Unit B', address: '172.20.10.4' }))

    updateHost(other.id, { address: '172.20.10.5', wsPort: 9999, token: 'tok-other' })

    expect(created).toHaveLength(1)
    expect(currentClient()).toBe(asClient(created[0]!))
    stop()
  })
})

// ---------------------------------------------------------------------------
// 4.7 + 4.8, per the clarification on this issue: removing the active host,
// or emptying the list, is a switch to no host. It tears down and resets
// exactly as a switch between two real hosts does, and then attaches
// nothing -- it is not a case the session gets to skip.
// ---------------------------------------------------------------------------

describe('the active host disappearing (4.7, 4.8)', () => {
  it('removing the active host tears down, resets the stores, and attaches nothing', async () => {
    const { removeHost, host, created, currentClient, connection, stats, peers, stop } = await withOneClient()
    const client = created[0]!
    client.primeLastStats({ uptime: 1 })
    client.emitState('connected')

    removeHost(host.id)

    expect(created).toHaveLength(1) // no replacement client is built
    expect(currentClient()).toBeNull()
    expect(client.closeCalls).toBe(1)
    expect(get(stats)).toBeNull()
    expect(get(peers)).toEqual([])
    // SPEC 4.4.2: with no client attached, connection reads offline -- not
    // the stale 'connected' this client last reported.
    expect(get(connection).state).toBe('offline')
    stop()
  })

  it('emptying the list the same way (removing every host) also attaches nothing afterwards', async () => {
    const { addHost, removeHost, host, created, currentClient, stop } = await withOneClient()
    const other = addHost(newHost({ label: 'Unit B', address: '172.20.10.4' }))

    removeHost(host.id)
    removeHost(other.id)

    expect(created).toHaveLength(1)
    expect(currentClient()).toBeNull()
    expect(created[0]!.closeCalls).toBe(1)
    stop()
  })

  it('activating a host again after the active one was removed builds a fresh client', async () => {
    const { addHost, activateHost, removeHost, host, created, currentClient, stop } = await withOneClient()
    const other = addHost(newHost({ label: 'Unit B', address: '172.20.10.4' }))
    removeHost(host.id)
    expect(currentClient()).toBeNull()

    activateHost(other.id)

    expect(created).toHaveLength(2)
    expect(currentClient()).toBe(asClient(created[1]!))
    stop()
  })
})

// ---------------------------------------------------------------------------
// 4.8: "Nothing it does may throw out of a store subscriber." A build or a
// connect can fail -- new WebSocket throws on a port the browser refuses --
// and that failure must become connection state, not an exception escaping
// the settingsWritable.update() that produced the activeHost notification.
// An uncaught exception there aborts the settings write half done and, per
// svelte/store's shared subscriber queue, silently drops every later store
// notification in the app, not only this one's.
// ---------------------------------------------------------------------------

describe('a failure while building or connecting never throws out of the activeHost subscriber (4.8)', () => {
  it('createClient throwing does not abort the settings write or wedge later store notifications', async () => {
    // Two hosts, not one: with only one, `connection` never left its
    // module-initial `offline`, and the assertion below would pass against
    // an implementation with no failure handling at all -- the reviewer
    // caught exactly this. Starting from a genuinely `connected` first
    // host and switching to one whose createClient() throws is what makes
    // the reading below prove something: it can only come from
    // connectStores' teardown (SPEC 4.4.2, "with no client attached,
    // `connection` reads offline"), run when rebuild() tears the first
    // host's client down before the second host's build ever starts --
    // there is no second client, so no attach-time seed to fall back on
    // either (contrast the connect()-throws test below, which has one).
    const { loadSettings, addHost, activateHost, startSession, currentClient, connection, settings, peers, resetStores } =
      await loadModules()
    loadSettings()
    const hostA = addHost(newHost({ label: 'Unit A', address: '172.20.10.9' }))
    const hostB = addHost(newHost({ label: 'Unit B (bad port)', address: '172.20.10.3', wsPort: 1 }))

    let firstClient: FakeWsClient | null = null
    startSession({
      createClient: (options) => {
        if (options.url.includes('172.20.10.9')) {
          firstClient = new FakeWsClient(options)
          return asClient(firstClient)
        }
        throw new Error('refused: the browser will not open this port')
      },
    })

    activateHost(hostA.id)
    firstClient!.emitState('connected')
    expect(get(connection).state).toBe('connected')

    // peers, not stats: an array-valued store notifies on every set() call
    // regardless of content (svelte/store's safe_not_equal treats any two
    // objects as unequal), where a null-valued one like stats would not --
    // resetStores() setting an already-null stats back to null is a no-op
    // that proves nothing either way. This is what makes the count below a
    // real assertion about delivery rather than an accident of the fixture.
    const peersNotifications: unknown[] = []
    const unsubscribePeers = peers.subscribe((value) => peersNotifications.push(value))
    peersNotifications.length = 0 // drop the immediate replay of the current value

    expect(() => activateHost(hostB.id)).not.toThrow()

    // The settings write completed: persist() and storeToken() run inside
    // settingsWritable's own update callback, before Svelte notifies any
    // subscriber at all, including this session's.
    // Read the persisted value rather than its presence: addHost and the
    // first activateHost have already written this key three times, so a
    // not-null check would pass against an implementation that aborted the
    // write under test.
    const persisted = fakeStorage.getItem(SETTINGS_KEY)
    expect(persisted).not.toBeNull()
    expect(JSON.parse(persisted as string).activeHostId).toBe(hostB.id)
    expect(get(settings).activeHostId).toBe(hostB.id)

    // The failure shows as connection state, not a stale 'connected' from
    // the host that is no longer active: there is no client to hand out,
    // because the very first step of building one for hostB is what threw.
    expect(currentClient()).toBeNull()
    expect(get(connection).state).toBe('offline')

    // The shared subscriber queue was not left wedged by the exception: a
    // later, unrelated store write still reaches a listener that
    // subscribed before the failure. Isolated from whatever activateHost()
    // itself triggered internally by resetting the count right before it.
    peersNotifications.length = 0
    resetStores()
    expect(peersNotifications).toHaveLength(1)

    unsubscribePeers()
  })

  it('connect() throwing after a working first connection still reaches connection as offline, not stuck at the previous state', async () => {
    // new WebSocket only throws once a socket is actually opened, not at
    // construction (createClient succeeding), so this is the realistic
    // shape: the first host connects normally, the switch to the second
    // is the one whose connect() fails.
    const { loadSettings, addHost, activateHost, startSession, connection, settings, peers, resetStores } =
      await loadModules()
    loadSettings()
    const hostA = addHost(newHost({ label: 'Unit A', address: '172.20.10.9' }))
    const hostB = addHost(newHost({ label: 'Unit B (bad port)', address: '172.20.10.3', wsPort: 1 }))

    let firstClient: FakeWsClient | null = null
    startSession({
      createClient: (options) => {
        if (options.url.includes('172.20.10.9')) {
          firstClient = new FakeWsClient(options)
          return asClient(firstClient)
        }
        const failing = new FakeWsClient(options)
        failing.connect = () => {
          throw new Error('refused: the browser will not open this port')
        }
        return asClient(failing)
      },
    })

    activateHost(hostA.id)
    firstClient!.emitState('connected')
    expect(get(connection).state).toBe('connected')

    // peers, not stats: an array-valued store notifies on every set() call
    // regardless of content, where stats (null before and after) would not
    // and would prove nothing either way.
    const peersNotifications: unknown[] = []
    const unsubscribePeers = peers.subscribe((value) => peersNotifications.push(value))
    peersNotifications.length = 0

    expect(() => activateHost(hostB.id)).not.toThrow()

    // The settings write completed despite the failure.
    expect(get(settings).activeHostId).toBe(hostB.id)

    // The stale 'connected' from hostA does not linger. Checked by
    // mutation rather than assumed: deleting connectStores' teardown-sets-
    // offline line alone leaves this assertion green (the attach-time seed
    // from hostB's own, never-connected client state already produces
    // offline here), and deleting that seed alone also leaves it green
    // (teardown alone already forces offline before the failing build ever
    // starts). Neither rule is this assertion's own -- each already has a
    // test that isolates it (the createClient case above for the teardown
    // rule, "reflects the client state at mount" in stores.spec.ts for the
    // seed). What is this test's own is the try/catch around build/attach/
    // connect: removing it is what actually turns this assertion (and the
    // two around it) red, per the mutation table for the previous review
    // round. Kept here as a belt-and-braces regression check, not
    // relabelled as proof of either rule -- an earlier version of this
    // comment claimed it was, first by an ordering that turned out not to
    // matter, then by the seed alone, and both claims were wrong for the
    // same reason: neither was checked by mutation before being written
    // down.
    expect(get(connection).state).toBe('offline')

    // The shared subscriber queue was not left wedged by the exception,
    // isolated from whatever the activation itself triggered internally.
    peersNotifications.length = 0
    resetStores()
    expect(peersNotifications).toHaveLength(1)

    unsubscribePeers()
  })
})

// ---------------------------------------------------------------------------
// 4.8: two corrections from the review round. The rebuild identity is the
// address, the ws port and the token -- not the host id, and not the token
// forwarded to the client either, since the client re-reads
// companion.token on every connect() attempt (SPEC 4.3.9) and an explicit
// option would freeze a stale copy for that client's whole life.
// ---------------------------------------------------------------------------

describe('the rebuild identity and the token (4.8, corrected)', () => {
  it('activating a different host entry with the same address, ws port and token keeps the socket', async () => {
    const { addHost, activateHost, created, currentClient, stop } = await withOneClient()
    // withOneClient's host: address 172.20.10.9, wsPort 8082, token 'tok-a'.
    const sameConnection = addHost(
      newHost({ label: 'Same unit, different entry', address: '172.20.10.9', wsPort: 8082, token: 'tok-a' }),
    )

    activateHost(sameConnection.id)

    expect(created).toHaveLength(1)
    expect(currentClient()).toBe(asClient(created[0]!))
    expect(created[0]!.closeCalls).toBe(0)
    stop()
  })

  it('the client is never built with a token option: the session leaves companion.token as the only source', async () => {
    const { loadSettings, addHost, activateHost, startSession } = await loadModules()
    seedNoActiveHost() // reached deliberately (4.7): nothing active until this test activates its own host
    loadSettings()
    const host = addHost(newHost({ label: 'Has a token', token: 'tok-should-not-be-forwarded' }))
    const created: FakeWsClient[] = []
    const stop = startSession({
      createClient: (options) => {
        const c = new FakeWsClient(options)
        created.push(c)
        return asClient(c)
      },
    })

    activateHost(host.id)

    expect(created).toHaveLength(1)
    expect(created[0]!.options.token).toBeUndefined()
    stop()
  })
})
