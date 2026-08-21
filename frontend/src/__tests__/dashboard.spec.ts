import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type {
  Battery,
  Capabilities,
  FaceStatus,
  Gps,
  LastHandshake,
  Mode,
  OutgoingChannelHop,
  OutgoingFaceStatus,
  OutgoingGpsUpdate,
  OutgoingMessage,
  OutgoingRestarting,
  OutgoingStats,
  Peer,
  RestartReason,
  Stats,
} from '../lib/protocol'
import type { ConnectionState, UnauthorizedReason, WsClient } from '../lib/ws'
import {
  DASH,
  EMPTY_LABEL,
  formatAge,
  formatBatteryPercent,
  formatChannel,
  formatCharging,
  formatGpsFix,
  formatGpsSource,
  formatMode,
  formatNamedEvent,
  formatTemperature,
  formatUnitTime,
  formatUptime,
  NEVER_REFRESHED,
} from '../lib/format'
import { connectStores, resetStores } from '../lib/stores'

// Written from SPEC.md 4.5.1.1, 4.5.2 (Dashboard field list only, controls
// out of scope), 4.3.1 (connection states) and 4.4.1 (which store each field
// reads), plus docs/schemas/. Deliberately not read from Dashboard.svelte,
// which companion-implementer is authoring in parallel - the placeholder
// currently on disk was not opened past confirming it exists.
//
// Pattern for driving the stores lifted from stores.spec.ts: connectStores()
// with a hand-driven WsClient double, never a real socket, torn down and
// resetStores()'d between tests because the stores are module singletons.

const T = 1_700_000_000

// ---------------------------------------------------------------------------
// Fixture builders. Realistic shapes, synthetic content only.
// ---------------------------------------------------------------------------

function battery(overrides: Partial<Battery> = {}): Battery {
  return { percent: 80, charging: false, ...overrides }
}

function capabilitiesData(overrides: Partial<Capabilities> = {}): Capabilities {
  return { pasv: true, pisugar: false, gpsSource: 'gpsd', pluginVersion: '0.1.0', ...overrides }
}

function gpsReading(overrides: Partial<Gps> = {}): Gps {
  return {
    enabled: true,
    source: 'gpsd',
    piFix: true,
    fix: true,
    lat: 10.1,
    lon: -30.2,
    altitude: 5,
    accuracy: 8,
    updated: T,
    ...overrides,
  }
}

function lastHandshake(overrides: Partial<LastHandshake> = {}): LastHandshake {
  return { filename: 'TestNet_001_aabbccddeeff.pcapng', ssid: 'TestNet_001', bssid: 'aabbccddeeff', mtime: T, ...overrides }
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

function statsData(overrides: Partial<Stats> = {}): Stats {
  return {
    uptime: 120,
    mode: 'AUTO',
    channel: 6,
    battery: battery(),
    temperature: 45.2,
    handshakes: 3,
    handshakesTotal: 3,
    peers: 1,
    accessPoints: 5,
    lastHandshake: null,
    lastPeer: null,
    gps: gpsReading(),
    sessionAge: 5,
    capabilities: capabilitiesData(),
    ...overrides,
  }
}

function statsEnvelope(overrides: Partial<Stats> = {}): OutgoingStats {
  return { type: 'stats', timestamp: T, data: statsData(overrides) }
}

function faceStatusData(overrides: Partial<FaceStatus> = {}): FaceStatus {
  return { face: '(-_-)', status: 'idle', mode: 'AUTO', ...overrides }
}

function faceStatusEnvelope(overrides: Partial<FaceStatus> = {}): OutgoingFaceStatus {
  return { type: 'face_status', timestamp: T, data: faceStatusData(overrides) }
}

function channelHopEnvelope(value: number): OutgoingChannelHop {
  return { type: 'channel_hop', timestamp: T, data: { channel: value } }
}

function gpsUpdateEnvelope(reading: Gps): OutgoingGpsUpdate {
  return { type: 'gps_update', timestamp: T, data: reading }
}

function restartingEnvelope(reason: RestartReason, mode: Mode | null = null): OutgoingRestarting {
  return { type: 'restarting', timestamp: T, data: { reason, mode } }
}

// ---------------------------------------------------------------------------
// A hand-driven double for WsClient (same shape as stores.spec.ts's own).
// ---------------------------------------------------------------------------

class FakeWsClient {
  currentState: ConnectionState = 'offline'
  reasonValue: UnauthorizedReason | null = null
  private lastStatsValue: Stats | null = null
  readonly stateHandlers = new Set<(state: ConnectionState) => void>()
  readonly messageHandlers = new Set<(message: OutgoingMessage) => void>()

  connect(): void {}
  close(): void {}
  state(): ConnectionState {
    return this.currentState
  }
  unauthorizedReason(): UnauthorizedReason | null {
    return this.reasonValue
  }
  lastStats(): Stats | null {
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
  request(): Promise<OutgoingMessage> {
    return new Promise(() => {
      // Never settles: nothing under test issues a read.
    })
  }
  command(): Promise<OutgoingMessage> {
    return Promise.reject(new Error('Dashboard read-only tests never issue a command'))
  }
  sendGps(): void {}

  emitState(state: ConnectionState, reason: UnauthorizedReason | null = null): void {
    this.currentState = state
    this.reasonValue = reason
    for (const handler of [...this.stateHandlers]) handler(state)
  }

  emitMessage(message: OutgoingMessage): void {
    if (message.type === 'stats') this.lastStatsValue = message.data
    for (const handler of [...this.messageHandlers]) handler(message)
  }
}

function asClient(fake: FakeWsClient): WsClient {
  return fake as unknown as WsClient
}

// ---------------------------------------------------------------------------
// Mounting, the same way App.svelte mounts a view: no props.
// ---------------------------------------------------------------------------

type SvelteApi = typeof import('svelte')

let svelte: SvelteApi | null = null
let container: HTMLElement | null = null
let instance: Record<string, unknown> | null = null
let teardownStores: (() => void) | null = null

async function mountDashboard(client?: FakeWsClient): Promise<{ root: HTMLElement; client: FakeWsClient | null }> {
  svelte = await import('svelte')
  if (client) {
    teardownStores = connectStores(asClient(client))
  }
  const module = (await import('../views/Dashboard.svelte')) as unknown as {
    default: Parameters<SvelteApi['mount']>[0]
  }
  container = document.createElement('div')
  document.body.appendChild(container)
  instance = svelte.mount(module.default, { target: container }) as Record<string, unknown>
  await settle()
  return { root: container, client: client ?? null }
}

async function settle(): Promise<void> {
  svelte?.flushSync()
  await svelte?.tick()
}

beforeEach(() => {
  resetStores()
})

afterEach(async () => {
  if (instance && svelte) {
    svelte.unmount(instance)
  }
  instance = null
  container?.remove()
  container = null
  svelte = null
  if (teardownStores) {
    teardownStores()
    teardownStores = null
  }
  resetStores()
})

// ---------------------------------------------------------------------------
// Queries, on the declared data-* hooks only (SPEC.md 4.5.1.1: "declared
// hooks, the same way data-view and data-region are, and for the same
// reason: a test that finds a value by walking the markup is a test that
// breaks when the markup is rearranged").
// ---------------------------------------------------------------------------

const FIELD_NAMES = [
  'uptime',
  'mode',
  'battery',
  'charging',
  'temperature',
  'channel',
  'accessPoints',
  'handshakes',
  'peers',
  'lastHandshake',
  'lastPeer',
  'gpsSource',
  'gpsFix',
  'face',
  'status',
] as const

function root(): HTMLElement {
  if (!container) throw new Error('Dashboard is not mounted')
  return container
}

function field(name: string): HTMLElement {
  const found = root().querySelector(`[data-field="${name}"]`)
  expect(found, `expected a [data-field="${name}"] element`).not.toBeNull()
  return found as HTMLElement
}

function fieldOrNull(name: string): HTMLElement | null {
  return root().querySelector(`[data-field="${name}"]`)
}

function fieldText(name: string): string {
  return (field(name).textContent ?? '').trim()
}

// The value slot may legitimately carry the accessible EMPTY_LABEL text
// alongside the dash in the same element (e.g. a visually-hidden sibling
// text node), which textContent flattens into one string. The "row stays,
// accessible unavailable label alongside it" test below asserts that text is
// present; this helper strips it back out so a DASH-equality assertion is
// about the visible dash and not about whether a label happens to sit beside
// it in the DOM.
function visibleFieldText(name: string): string {
  return fieldText(name).split(EMPTY_LABEL).join('').trim()
}

function isEmpty(name: string): boolean {
  return field(name).getAttribute('data-empty') === 'true'
}

// The smallest ancestor of a field's value element that contains no *other*
// [data-field] element. That scopes the search to just this field's own
// row rather than to the whole Dashboard section: `el.closest('[data-view]')`
// (the earlier version of this helper) is the entire view, so a label check
// against it cannot fail even with every `.field-label` deleted, since the
// section's total text is never just the bare dash.
function rowFor(name: string): HTMLElement {
  const valueEl = field(name)
  let node: HTMLElement | null = valueEl.parentElement
  while (node && node !== root()) {
    const otherFields = [...node.querySelectorAll('[data-field]')].filter((el) => el !== valueEl)
    if (otherFields.length === 0) return node
    node = node.parentElement
  }
  // No ancestor short of the whole view is field-exclusive: fall back to the
  // value element's own parent, which is still closer than the full section.
  return valueEl.parentElement ?? valueEl
}

function banner(): HTMLElement {
  const found = root().querySelector('[data-banner]')
  expect(found, 'expected a [data-banner] element').not.toBeNull()
  return found as HTMLElement
}

// ---------------------------------------------------------------------------
// "A value that is not known renders as a dash, and its row stays."
// ---------------------------------------------------------------------------

describe('before the first frame, every field is a dash', () => {
  it('shows DASH with data-empty for every declared field', async () => {
    const client = new FakeWsClient()
    client.emitState('connecting')
    await mountDashboard(client)

    for (const name of FIELD_NAMES) {
      expect(visibleFieldText(name), `field ${name}`).toBe(DASH)
      expect(isEmpty(name), `field ${name} data-empty`).toBe(true)
    }
  })

  it('carries an accessible "unavailable" alongside the dash, since a dash is not readable aloud', async () => {
    const client = new FakeWsClient()
    client.emitState('connecting')
    await mountDashboard(client)

    for (const name of FIELD_NAMES) {
      const el = field(name)
      // The accessible text can be the element's own aria-label, or that of
      // a labelled/described element, or plain text carried in the DOM
      // (e.g. visually-hidden). SPEC pins the string, not its exact
      // placement, so all three are accepted.
      const ariaLabel = el.getAttribute('aria-label') ?? ''
      const describedBy = el.getAttribute('aria-describedby')
      const describedText = describedBy ? (document.getElementById(describedBy)?.textContent ?? '') : ''
      const ownText = el.textContent ?? ''
      const haystack = `${ariaLabel} ${describedText} ${ownText}`.toLowerCase()
      expect(haystack, `field ${name} must carry "${EMPTY_LABEL}" accessibly`).toContain(EMPTY_LABEL.toLowerCase())
    }
  })

  it('keeps the row present rather than hiding it: the label beside each dashed value is still on screen', async () => {
    const client = new FakeWsClient()
    client.emitState('connecting')
    await mountDashboard(client)

    for (const name of FIELD_NAMES) {
      const valueEl = field(name)
      expect(valueEl.isConnected).toBe(true)

      // Scoped to this field's own row (see rowFor above), so deleting
      // every `<span class="field-label">` -- the mutant this replaces a
      // vacuously-true assertion for -- leaves nothing behind but the
      // value's own text and fails the check below.
      const row = rowFor(name)
      const rowText = (row.textContent ?? '').replace(/\s+/g, ' ').trim()
      const valueText = (valueEl.textContent ?? '').replace(/\s+/g, ' ').trim()
      expect(rowText, `row for ${name} must carry more than its own value`).not.toBe(valueText)
      expect(rowText.length, `row for ${name} must carry a label`).toBeGreaterThan(valueText.length)
    }
  })

  it('shows the offline banner before any client attaches at all, with no client and no socket', async () => {
    // SPEC 4.4.1: "with no client attached [connection] reads offline". This
    // is the one test in the file that mounts with nothing behind the
    // stores whatsoever.
    await mountDashboard()

    expect(banner().getAttribute('data-banner')).toBe('offline')
    expect((banner().textContent ?? '').trim()).toBe(
      'Not connected. Reconnecting automatically. Check that the Personal Hotspot is on.',
    )
  })
})

describe('a frame carrying nulls still renders every row, dashed where the schema allows null', () => {
  it('dashes channel, temperature, battery.percent, lastHandshake and lastPeer; leaves the rest as real values', async () => {
    const client = new FakeWsClient()
    client.emitState('connected')
    await mountDashboard(client)
    client.emitMessage(
      statsEnvelope({
        channel: null,
        temperature: null,
        battery: { percent: null, charging: null },
        lastHandshake: null,
        lastPeer: null,
      }),
    )
    await settle()

    for (const name of ['channel', 'temperature', 'battery', 'charging', 'lastHandshake', 'lastPeer']) {
      expect(isEmpty(name), `field ${name}`).toBe(true)
      expect(visibleFieldText(name), `field ${name}`).toBe(DASH)
    }

    // Non-nullable Stats members present in the same frame are not dashed.
    for (const name of ['uptime', 'mode', 'accessPoints', 'handshakes', 'peers']) {
      expect(isEmpty(name), `field ${name}`).toBe(false)
      expect(fieldText(name), `field ${name}`).not.toBe(DASH)
    }
  })
})

// ---------------------------------------------------------------------------
// GPS source/fix: two fields, not one (SPEC.md amended 4.5.1.1). gpsFix
// reads "off" (not DASH, not "no fix") when gps.enabled is false: "off says
// nobody is looking ... no fix says the unit is looking and has not found
// one." gpsSource is a different question -- "which provider resolved the
// position" -- and "off" belongs only to the fix indicator: with source
// null (§2.12: null exactly when enabled is false) it reads the ordinary
// DASH, "the absence of a name is the absence of a name", never a fourth
// word borrowed from the fix row.
// ---------------------------------------------------------------------------

describe('a GPS source that is switched off', () => {
  it('reads "off" on gpsFix and DASH (not "off") on gpsSource', async () => {
    const client = new FakeWsClient()
    client.emitState('connected')
    await mountDashboard(client)
    client.emitMessage(
      statsEnvelope({
        gps: gpsReading({ enabled: false, source: null, fix: false, piFix: false }),
      }),
    )
    await settle()

    const off = gpsReading({ enabled: false, source: null, fix: false, piFix: false })
    expect(fieldText('gpsFix')).toBe(formatGpsFix(off))
    expect(fieldText('gpsFix')).toBe('off')
    // "off" is a real answer for the fix indicator, not an absent one.
    expect(isEmpty('gpsFix')).toBe(false)

    // gpsSource is a name, and there is none here: the ordinary dash rule,
    // not "off" borrowed from the fix row. A mutant sharing formatGpsFix's
    // "off" branch with formatGpsSource dies on this line.
    expect(formatGpsSource(off)).toBe(DASH)
    expect(visibleFieldText('gpsSource')).toBe(DASH)
    expect(isEmpty('gpsSource')).toBe(true)
  })

  it('reads "no fix" (not "off") when enabled but not fixed, and "fix" when fixed', async () => {
    const client = new FakeWsClient()
    client.emitState('connected')
    await mountDashboard(client)
    const noFix = gpsReading({ enabled: true, source: 'gpsd', fix: false })
    client.emitMessage(statsEnvelope({ gps: noFix }))
    await settle()
    expect(fieldText('gpsFix')).toBe(formatGpsFix(noFix))
    expect(fieldText('gpsFix')).toBe('no fix')
    expect(fieldText('gpsSource')).toBe(formatGpsSource(noFix))
    expect(fieldText('gpsSource')).toBe('gpsd')

    const fixed = gpsReading({ enabled: true, source: 'gpsd', fix: true })
    client.emitMessage(statsEnvelope({ gps: fixed }))
    await settle()
    expect(fieldText('gpsFix')).toBe(formatGpsFix(fixed))
    expect(fieldText('gpsFix')).toBe('fix')
  })
})

// ---------------------------------------------------------------------------
// GPS is the second one-snapshot exception (SPEC.md amended 4.5.1.1): the
// `gps` store (SPEC 4.4.1) holds the later of `stats.gps` and a `gps_update`
// push, so these two rows must follow that store and not `stats.gps`
// directly, the same way `channel` does.
// ---------------------------------------------------------------------------

describe('GPS rows follow the gps store, not the last stats.gps', () => {
  it('shows a gps_update pushed after stats rather than the stats snapshot it arrived after', async () => {
    const client = new FakeWsClient()
    client.emitState('connected')
    await mountDashboard(client)
    client.emitMessage(statsEnvelope({ gps: gpsReading({ enabled: true, source: 'gpsd', fix: false }) }))
    await settle()
    expect(fieldText('gpsSource')).toBe('gpsd')
    expect(fieldText('gpsFix')).toBe('no fix')

    client.emitMessage(gpsUpdateEnvelope(gpsReading({ enabled: true, source: 'browser', fix: true })))
    await settle()

    // stats.gps (the last stats snapshot) still says gpsd/no fix; the two
    // rows must show the gps_update's browser/fix instead, proving they
    // read the gps store rather than stats.gps.
    expect(fieldText('gpsSource')).toBe('browser')
    expect(fieldText('gpsFix')).toBe('fix')
  })
})

// ---------------------------------------------------------------------------
// formatUptime wired through.
// ---------------------------------------------------------------------------

describe('uptime', () => {
  it('renders formatUptime(stats.uptime)', async () => {
    const client = new FakeWsClient()
    client.emitState('connected')
    await mountDashboard(client)
    client.emitMessage(statsEnvelope({ uptime: 90000 }))
    await settle()

    expect(fieldText('uptime')).toBe(formatUptime(90000))
  })
})

// ---------------------------------------------------------------------------
// Mode badge: MANUAL -> MANU, and follows `stats`, never `face`.
// ---------------------------------------------------------------------------

describe('mode badge', () => {
  it.each([
    ['AUTO', 'AUTO'],
    ['PASV', 'PASV'],
    ['MANUAL', 'MANU'],
  ] as const)('renders stats.mode %s as formatMode(%s)', async (mode, expected) => {
    const client = new FakeWsClient()
    client.emitState('connected')
    await mountDashboard(client)
    client.emitMessage(statsEnvelope({ mode }))
    await settle()

    expect(fieldText('mode')).toBe(formatMode(mode))
    expect(fieldText('mode')).toBe(expected)
  })

  it('follows stats.mode, never face.mode, when the two disagree', async () => {
    const client = new FakeWsClient()
    client.emitState('connected')
    await mountDashboard(client)
    client.emitMessage(statsEnvelope({ mode: 'AUTO' }))
    client.emitMessage(faceStatusEnvelope({ mode: 'MANUAL' }))
    await settle()

    // SPEC 4.5.1.1: "The mode badge is not read from face.mode ... stats is
    // the one the banner speaks for." A mutant reading face.mode here would
    // show MANU; the rule under test is that it does not.
    expect(fieldText('mode')).toBe('AUTO')
  })
})

// ---------------------------------------------------------------------------
// The channel follows the `channel` store, not the last `stats`.
// ---------------------------------------------------------------------------

describe('channel', () => {
  it('follows the channel store, not the last stats snapshot, once a hop has arrived after it', async () => {
    const client = new FakeWsClient()
    client.emitState('connected')
    await mountDashboard(client)
    client.emitMessage(statsEnvelope({ channel: 6 }))
    await settle()
    expect(fieldText('channel')).toBe(formatChannel(6))

    client.emitMessage(channelHopEnvelope(11))
    await settle()

    // stats.channel is still 6 in the last stats snapshot; the channel field
    // must show 11, proving it reads the channel store rather than
    // stats.channel directly.
    expect(fieldText('channel')).toBe(formatChannel(11))
    expect(fieldText('channel')).not.toBe(formatChannel(6))
  })
})

// ---------------------------------------------------------------------------
// The counters: handshakesTotal is authoritative; the *.pcapng figure
// (handshakesOnUnit) appears only when the two disagree.
// ---------------------------------------------------------------------------

describe('the handshake counters', () => {
  it('shows only handshakesTotal, with no second line, when handshakes and handshakesTotal agree', async () => {
    const client = new FakeWsClient()
    client.emitState('connected')
    await mountDashboard(client)
    client.emitMessage(statsEnvelope({ handshakes: 5, handshakesTotal: 5 }))
    await settle()

    expect(fieldText('handshakes')).toBe('5')
    expect(fieldOrNull('handshakesOnUnit')).toBeNull()
  })

  it('shows handshakesTotal as the counter and handshakes as a second, named line when they disagree', async () => {
    const client = new FakeWsClient()
    client.emitState('connected')
    await mountDashboard(client)
    client.emitMessage(statsEnvelope({ handshakes: 3, handshakesTotal: 5 }))
    await settle()

    expect(fieldText('handshakes')).toBe('5')
    const onUnit = fieldOrNull('handshakesOnUnit')
    expect(onUnit, 'handshakesOnUnit must appear when the two disagree').not.toBeNull()
    expect((onUnit?.textContent ?? '').trim()).toContain('3')
  })
})

// ---------------------------------------------------------------------------
// lastHandshake: a unit timestamp, rendered as a time of day.
// ---------------------------------------------------------------------------

describe('lastHandshake', () => {
  it('is DASH when the directory is empty', async () => {
    const client = new FakeWsClient()
    client.emitState('connected')
    await mountDashboard(client)
    client.emitMessage(statsEnvelope({ lastHandshake: null }))
    await settle()

    expect(visibleFieldText('lastHandshake')).toBe(DASH)
    expect(isEmpty('lastHandshake')).toBe(true)
  })

  it('renders formatNamedEvent(ssid, mtime), named rather than merely timed', async () => {
    const client = new FakeWsClient()
    client.emitState('connected')
    await mountDashboard(client)
    const mtime = 1_700_000_460
    client.emitMessage(statsEnvelope({ lastHandshake: lastHandshake({ ssid: 'TestNet_001', mtime }) }))
    await settle()

    // SPEC.md 4.5.1.1: "lastHandshake renders its ssid beside its time of
    // day ... A bare time answers 'when' and leaves out the half of the row
    // an operator is actually reading." formatNamedEvent is the pinned
    // function for this combination (SPEC.md's function table); its own
    // rules are asserted independently in format.spec.ts, this test only
    // pins that Dashboard passes it stats.lastHandshake.ssid and .mtime.
    expect(fieldText('lastHandshake')).toBe(formatNamedEvent('TestNet_001', mtime))
    expect(fieldText('lastHandshake')).not.toBe(formatUnitTime(mtime))
  })

  it('renders a literal expected string against a faked clock, not one built from formatNamedEvent itself', async () => {
    // The test above asserts the field matches whatever formatNamedEvent(ssid,
    // mtime) returns, which cannot catch the formatter and the view
    // drifting together -- if Dashboard stopped calling formatNamedEvent at
    // all and hand-rolled something that happened to match its current
    // output, that test would still pass. This one computes the expected
    // string independently, from a faked "now" and SPEC.md 4.5.1.1's own
    // worked example (5 Aug 09:14, this year, no year carried).
    vi.useFakeTimers()
    try {
      vi.setSystemTime(new Date(2026, 7, 15, 12, 0, 0)) // "now": 15 Aug 2026
      const mtime = Math.floor(new Date(2026, 7, 5, 9, 14, 0).getTime() / 1000) // 5 Aug 2026

      const client = new FakeWsClient()
      client.emitState('connected')
      await mountDashboard(client)
      client.emitMessage(statsEnvelope({ lastHandshake: lastHandshake({ ssid: 'TestNet_001', mtime }) }))
      await settle()

      expect(fieldText('lastHandshake')).toBe('TestNet_001 at 5 Aug 09:14')
    } finally {
      vi.useRealTimers()
    }
  })

  it('is empty when the ssid is empty and the timestamp is not known either, neither half present', async () => {
    // SPEC.md 4.5.1.1 (amended): "the field is empty when the object is
    // absent or when it carries neither half: an empty name and no
    // timestamp." LastHandshake.mtime is not nullable on the wire, so this
    // is reached the same way the rest of this suite reaches an otherwise
    // unreachable-by-schema state: a non-finite value, which formatUnitTime
    // already treats the same as absent (format.spec.ts's own non-finite
    // section). A `data-empty` decided by a string comparison would not
    // catch this either, since formatNamedEvent('', NaN) is DASH, the same
    // string a legal ssid of "-" produces -- which is exactly why the rule
    // is decided from the value, not the string.
    const client = new FakeWsClient()
    client.emitState('connected')
    await mountDashboard(client)
    client.emitMessage(statsEnvelope({ lastHandshake: lastHandshake({ ssid: '', mtime: NaN }) }))
    await settle()

    expect(visibleFieldText('lastHandshake')).toBe(DASH)
    expect(isEmpty('lastHandshake')).toBe(true)
    expect(fieldText('lastHandshake')).toContain(EMPTY_LABEL)
  })
})

// ---------------------------------------------------------------------------
// lastPeer: dash when absent; the hostile-string surface for a peer's name.
// ---------------------------------------------------------------------------

describe('lastPeer', () => {
  it('is DASH when no peer has ever been seen this session', async () => {
    const client = new FakeWsClient()
    client.emitState('connected')
    await mountDashboard(client)
    client.emitMessage(statsEnvelope({ lastPeer: null }))
    await settle()

    expect(visibleFieldText('lastPeer')).toBe(DASH)
    expect(isEmpty('lastPeer')).toBe(true)
  })

  it('renders formatNamedEvent(name, lastSeen), named rather than merely timed, once a peer has been seen', async () => {
    const client = new FakeWsClient()
    client.emitState('connected')
    await mountDashboard(client)
    const lastSeen = 1_700_000_460
    client.emitMessage(statsEnvelope({ lastPeer: peer({ name: 'unit-bravo', lastSeen }) }))
    await settle()

    expect(fieldText('lastPeer')).toBe(formatNamedEvent('unit-bravo', lastSeen))
    expect(fieldText('lastPeer')).toContain('unit-bravo')
    expect(isEmpty('lastPeer')).toBe(false)
  })

  it('is empty when the name is empty and lastSeen is null, neither half present', async () => {
    // SPEC.md 4.5.1.1 (amended), the worked example itself: "a peer with an
    // empty name and no lastSeen renders a dash that nothing declares
    // empty and that carries no accessible unavailable, which is the one
    // hole the value-decided rule would otherwise leave." This is a real,
    // schema-reachable case (Peer.name is a required string that can be
    // empty; Peer.lastSeen is nullable), unlike the equivalent
    // lastHandshake case above.
    const client = new FakeWsClient()
    client.emitState('connected')
    await mountDashboard(client)
    client.emitMessage(statsEnvelope({ lastPeer: peer({ name: '', lastSeen: null }) }))
    await settle()

    expect(visibleFieldText('lastPeer')).toBe(DASH)
    expect(isEmpty('lastPeer')).toBe(true)
    expect(fieldText('lastPeer')).toContain(EMPTY_LABEL)
  })
})

// ---------------------------------------------------------------------------
// temperature: rounded via format.ts, DASH for null.
// ---------------------------------------------------------------------------

describe('temperature', () => {
  it('renders formatTemperature(stats.temperature)', async () => {
    const client = new FakeWsClient()
    client.emitState('connected')
    await mountDashboard(client)
    client.emitMessage(statsEnvelope({ temperature: 47.6 }))
    await settle()

    expect(fieldText('temperature')).toBe(formatTemperature(47.6))
  })

  it('renders a dash and reports itself empty, with the accessible label, for a non-finite reading', async () => {
    // SPEC.md 4.5.1.1 (amended): "Everywhere else the comparison against
    // DASH is the correct test ... a first implementation re-derived
    // emptiness in the view as === null, which agreed with format.ts on
    // null and disagreed with it on every non-finite value, so a dash
    // appeared with nothing declaring it empty and no accessible
    // unavailable beside it." temperature is not one of the five fields
    // whose emptiness is decided from the value (face, status,
    // lastHandshake, lastPeer, gpsSource); it is a number, and a `=== null`
    // guard here is exactly the bug the amendment names: NaN !== null, so
    // formatTemperature(NaN) renders DASH while such a guard reports the
    // field as not empty, dropping data-empty and the accessible label.
    expect(formatTemperature(NaN)).toBe(DASH)

    const client = new FakeWsClient()
    client.emitState('connected')
    await mountDashboard(client)
    client.emitMessage(statsEnvelope({ temperature: NaN }))
    await settle()

    expect(visibleFieldText('temperature')).toBe(DASH)
    expect(isEmpty('temperature')).toBe(true)
    expect(fieldText('temperature')).toContain(EMPTY_LABEL)
  })
})

// ---------------------------------------------------------------------------
// The battery is two rows, and that is a reversal (SPEC.md amended
// §4.5.1.1): `battery` is the percentage alone (formatBatteryPercent) and
// `charging` is its own field (formatCharging) with its own data-field
// hook, each dashing on its own null and neither reading the other's value.
// The case the split exists for: {percent: null, charging: false} must show
// "on battery" on the charging row, not a dash -- combined, it rendered as
// unavailable although the unit had said something.
// ---------------------------------------------------------------------------

describe('battery percentage', () => {
  it('renders formatBatteryPercent(stats.battery.percent)', async () => {
    const client = new FakeWsClient()
    client.emitState('connected')
    await mountDashboard(client)
    client.emitMessage(statsEnvelope({ battery: battery({ percent: 83.5 }) }))
    await settle()

    expect(fieldText('battery')).toBe(formatBatteryPercent(83.5))
    expect(fieldText('battery')).toBe('84%')
  })

  it('is DASH when the percentage is null, regardless of the charging state', async () => {
    const client = new FakeWsClient()
    client.emitState('connected')
    await mountDashboard(client)
    client.emitMessage(statsEnvelope({ battery: { percent: null, charging: true } }))
    await settle()

    expect(visibleFieldText('battery')).toBe(DASH)
    expect(isEmpty('battery')).toBe(true)
  })
})

describe('charging', () => {
  it('renders "charging" and "on battery" for true and false, never a dash for either', async () => {
    const client = new FakeWsClient()
    client.emitState('connected')
    await mountDashboard(client)
    client.emitMessage(statsEnvelope({ battery: battery({ percent: 84, charging: true }) }))
    await settle()
    expect(fieldText('charging')).toBe(formatCharging(true))
    expect(fieldText('charging')).toBe('charging')
    expect(isEmpty('charging')).toBe(false)

    // The exact case SPEC.md gives for the two-row split: a percentage-less
    // reading with charging known false must render "on battery", not the
    // dash a combined row once produced for it.
    client.emitMessage(statsEnvelope({ battery: { percent: null, charging: false } }))
    await settle()
    expect(fieldText('charging')).toBe(formatCharging(false))
    expect(fieldText('charging')).toBe('on battery')
    expect(isEmpty('charging')).toBe(false)
  })

  it('is DASH only when charging is null, regardless of the percentage', async () => {
    const client = new FakeWsClient()
    client.emitState('connected')
    await mountDashboard(client)
    client.emitMessage(statsEnvelope({ battery: { percent: 84, charging: null } }))
    await settle()

    expect(visibleFieldText('charging')).toBe(DASH)
    expect(isEmpty('charging')).toBe(true)
  })
})

// ---------------------------------------------------------------------------
// accessPoints / handshakes / peers: plain counts from `stats`.
// ---------------------------------------------------------------------------

describe('plain counters', () => {
  it('renders accessPoints and peers as the numbers stats carries', async () => {
    const client = new FakeWsClient()
    client.emitState('connected')
    await mountDashboard(client)
    client.emitMessage(statsEnvelope({ accessPoints: 12, peers: 4 }))
    await settle()

    expect(fieldText('accessPoints')).toBe('12')
    expect(fieldText('peers')).toBe('4')
  })

  it('renders a count of zero rather than treating it as absent', async () => {
    const client = new FakeWsClient()
    client.emitState('connected')
    await mountDashboard(client)
    client.emitMessage(statsEnvelope({ accessPoints: 0, peers: 0, handshakes: 0, handshakesTotal: 0 }))
    await settle()

    expect(fieldText('accessPoints')).toBe('0')
    expect(isEmpty('accessPoints')).toBe(false)
    expect(fieldText('peers')).toBe('0')
    expect(isEmpty('peers')).toBe(false)
    expect(fieldText('handshakes')).toBe('0')
    expect(isEmpty('handshakes')).toBe(false)
  })
})

// ---------------------------------------------------------------------------
// face / status: from the `face` store, a different message than `stats`.
// ---------------------------------------------------------------------------

describe('face and status', () => {
  it('are DASH before any face_status has arrived', async () => {
    const client = new FakeWsClient()
    client.emitState('connected')
    await mountDashboard(client)
    client.emitMessage(statsEnvelope())
    await settle()

    expect(visibleFieldText('face')).toBe(DASH)
    expect(visibleFieldText('status')).toBe(DASH)
  })

  it('render the face and status carried in face_status', async () => {
    const client = new FakeWsClient()
    client.emitState('connected')
    await mountDashboard(client)
    client.emitMessage(faceStatusEnvelope({ face: '(o_o)', status: 'scanning' }))
    await settle()

    expect(fieldText('face')).toBe('(o_o)')
    expect(fieldText('status')).toBe('scanning')
  })
})

// ---------------------------------------------------------------------------
// SPEC 4.5.3: remote strings render as text, never as markup.
// ---------------------------------------------------------------------------

describe('hostile remote strings render as text (SPEC 4.5.3)', () => {
  const HOSTILE_SCRIPT = '<script>window.__pwned = true</script>'
  const HOSTILE_QUOTES = `"'><img src=x onerror="window.__pwned = true">`

  it('renders a hostile face verbatim, with no element created from it', async () => {
    const client = new FakeWsClient()
    client.emitState('connected')
    await mountDashboard(client)
    client.emitMessage(faceStatusEnvelope({ face: HOSTILE_SCRIPT, status: 'idle' }))
    await settle()

    const el = field('face')
    expect(el.textContent).toBe(HOSTILE_SCRIPT)
    expect(el.children.length).toBe(0)
    expect(root().querySelector('script')).toBeNull()
    expect((window as unknown as { __pwned?: boolean }).__pwned).not.toBe(true)
  })

  it('renders a hostile status verbatim, quote-and-angle-bracket soup included, with no element created', async () => {
    const client = new FakeWsClient()
    client.emitState('connected')
    await mountDashboard(client)
    client.emitMessage(faceStatusEnvelope({ face: '(-_-)', status: HOSTILE_QUOTES }))
    await settle()

    const el = field('status')
    expect(el.textContent).toBe(HOSTILE_QUOTES)
    expect(el.children.length).toBe(0)
    expect(root().querySelector('img')).toBeNull()
    expect((window as unknown as { __pwned?: boolean }).__pwned).not.toBe(true)
  })

  it("renders a hostile lastPeer.name verbatim, with no element created", async () => {
    const client = new FakeWsClient()
    client.emitState('connected')
    await mountDashboard(client)
    client.emitMessage(statsEnvelope({ lastPeer: peer({ name: HOSTILE_SCRIPT }) }))
    await settle()

    const el = field('lastPeer')
    expect(el.textContent).toContain(HOSTILE_SCRIPT)
    expect(el.children.length).toBe(0)
    expect(root().querySelector('script')).toBeNull()
    expect((window as unknown as { __pwned?: boolean }).__pwned).not.toBe(true)
  })

  it('renders a hostile lastHandshake.ssid verbatim, with no element created', async () => {
    // §4.5.3 names an SSID directly: "an SSID is 32 bytes chosen by a
    // stranger". lastHandshake reaches the DOM through formatNamedEvent,
    // the same function lastPeer does, so this is the same rule on the
    // fixture SPEC.md itself calls out and was missing from this block.
    const client = new FakeWsClient()
    client.emitState('connected')
    await mountDashboard(client)
    client.emitMessage(statsEnvelope({ lastHandshake: lastHandshake({ ssid: HOSTILE_SCRIPT }) }))
    await settle()

    const el = field('lastHandshake')
    expect(el.textContent).toContain(HOSTILE_SCRIPT)
    expect(el.children.length).toBe(0)
    expect(root().querySelector('script')).toBeNull()
    expect((window as unknown as { __pwned?: boolean }).__pwned).not.toBe(true)
  })
})

// ---------------------------------------------------------------------------
// "data-empty is decided by the value, never by the string" (SPEC.md
// amended 4.5.1.1): a network can legally be named "-" and a peer can call
// itself "-", so a field whose *string* happens to equal DASH must not, on
// that basis alone, report itself empty or carry the accessible label.
//
// lastHandshake is not exercised here: its `mtime` is not nullable on the
// wire (LastHandshake.mtime: number), so formatNamedEvent always appends
// " at <time>" and the rendered text can never collide with a bare DASH for
// that field. face, status and lastPeer (whose lastSeen *is* nullable) are
// where the collision is actually reachable.
// ---------------------------------------------------------------------------

describe('data-empty is decided by the value, not by the rendered string', () => {
  it('a face of exactly "-" is a real reading, not an absent one', async () => {
    const client = new FakeWsClient()
    client.emitState('connected')
    await mountDashboard(client)
    client.emitMessage(faceStatusEnvelope({ face: '-', status: 'idle' }))
    await settle()

    expect(fieldText('face')).toBe('-')
    expect(isEmpty('face')).toBe(false)
  })

  it('a status of exactly "-" is a real reading, not an absent one', async () => {
    const client = new FakeWsClient()
    client.emitState('connected')
    await mountDashboard(client)
    client.emitMessage(faceStatusEnvelope({ face: '(-_-)', status: '-' }))
    await settle()

    expect(fieldText('status')).toBe('-')
    expect(isEmpty('status')).toBe(false)
  })

  it('a peer named exactly "-" with no lastSeen still reports a real reading, not an absent one', async () => {
    // formatNamedEvent('-', null) is the name alone: literally "-", the
    // same string DASH itself is. A `data-empty` decided by comparing the
    // rendered string to DASH would flag this row empty; SPEC.md is
    // explicit that a remote party must not get to make that call.
    const client = new FakeWsClient()
    client.emitState('connected')
    await mountDashboard(client)
    client.emitMessage(statsEnvelope({ lastPeer: peer({ name: '-', lastSeen: null }) }))
    await settle()

    expect(fieldText('lastPeer')).toBe('-')
    expect(isEmpty('lastPeer')).toBe(false)
  })
})

// ---------------------------------------------------------------------------
// The connection banner: state and, where a state has one, its reason,
// asserted per state (SPEC.md 4.5.1.1's table).
// ---------------------------------------------------------------------------

describe('the connection banner', () => {
  it('carries the connecting state and no copy', async () => {
    const client = new FakeWsClient()
    client.emitState('connecting')
    await mountDashboard(client)

    // SPEC.md 4.5.1.1 (amended): "The two silent rows are §4.3.1's, not an
    // omission here" -- connecting and connected are pinned to *(no copy)*.
    // The element still carries its state, so a test can tell "connected,
    // and deliberately silent" from "the banner is broken". Asserting only
    // `data-banner` here would pass with any sentence at all in the banner,
    // including the wrong one for another state entirely.
    expect(banner().getAttribute('data-banner')).toBe('connecting')
    expect((banner().textContent ?? '').trim()).toBe('')
  })

  it('carries the connected state and no copy', async () => {
    const client = new FakeWsClient()
    client.emitState('connected')
    await mountDashboard(client)

    expect(banner().getAttribute('data-banner')).toBe('connected')
    expect((banner().textContent ?? '').trim()).toBe('')
  })

  it('carries the offline state with the exact pinned sentence', async () => {
    const client = new FakeWsClient()
    client.emitState('offline')
    await mountDashboard(client)

    expect(banner().getAttribute('data-banner')).toBe('offline')
    expect((banner().textContent ?? '').trim()).toBe(
      'Not connected. Reconnecting automatically. Check that the Personal Hotspot is on.',
    )
  })

  it('carries the degraded state with the data\'s age, from stats.sessionAge', async () => {
    const client = new FakeWsClient()
    client.emitState('connected')
    await mountDashboard(client)
    client.emitMessage(statsEnvelope({ sessionAge: 125 }))
    client.emitState('degraded')
    await settle()

    expect(banner().getAttribute('data-banner')).toBe('degraded')
    // SPEC.md 4.5.1.1 pins this sentence verbatim, the only row in the
    // banner table it does: "Connected, and the data is <age> old." A
    // substring check here would still pass if the null-sessionAge branch
    // below were folded into this one (formatAge(null) substituted into
    // this same template reads "the data is never refreshed old"), which is
    // exactly the mutant this exact-match guards against.
    expect((banner().textContent ?? '').trim()).toBe(`Connected, and the data is ${formatAge(125)} old.`)
  })

  it('reads the exact "has never refreshed" sentence in degraded when sessionAge is null, not an age of zero', async () => {
    const client = new FakeWsClient()
    client.emitState('connected')
    await mountDashboard(client)
    client.emitMessage(statsEnvelope({ sessionAge: null }))
    client.emitState('degraded')
    await settle()

    expect(banner().getAttribute('data-banner')).toBe('degraded')
    // SPEC.md 4.5.1.1's other verbatim sentence: "Connected, and the data
    // has never refreshed." A null sessionAge is not an age, so this must
    // not be the age sentence with formatAge(null) spliced in (which would
    // read "the data is never refreshed old").
    expect((banner().textContent ?? '').trim()).toBe('Connected, and the data has never refreshed.')
  })

  it('reads the exact "has never refreshed" sentence for a non-finite sessionAge too, not only for null', async () => {
    // SPEC.md 4.5.1.1 (amended): "Which sentence to use is decided by what
    // formatAge returned, not by testing the field for null ... formatAge
    // answers never refreshed for a non-finite value as well as for null,
    // so a guard reading sessionAge === null lets exactly that string back
    // into the first sentence and reconstructs the defect one layer up."
    // sessionAge is not itself null here -- NaN !== null -- so a view using
    // that guard takes the `<age> old` branch and produces `Connected, and
    // the data is never refreshed old.`, the exact defect the equality
    // assertions elsewhere in this describe were written to catch, just
    // reached through a different door.
    expect(formatAge(NaN)).toBe(NEVER_REFRESHED)

    const client = new FakeWsClient()
    client.emitState('connected')
    await mountDashboard(client)
    client.emitMessage(statsEnvelope({ sessionAge: NaN }))
    client.emitState('degraded')
    await settle()

    expect(banner().getAttribute('data-banner')).toBe('degraded')
    expect((banner().textContent ?? '').trim()).toBe('Connected, and the data has never refreshed.')
  })

  it('renders the exact pinned sentence for the unauthorized "rejected" reason', async () => {
    const client = new FakeWsClient()
    client.emitState('unauthorized', 'rejected')
    await mountDashboard(client)

    expect(banner().getAttribute('data-banner')).toBe('unauthorized')
    // SPEC.md 4.5.1.1's banner table, verbatim. A substring-and-differ check
    // (both mention "token" and are unequal) still passes with the two arms
    // swapped, per SPEC.md's own account of the review that found it; only
    // an exact match against each pinned sentence catches that.
    expect((banner().textContent ?? '').trim()).toBe('The unit refused the stored token. Fix it in Settings.')
  })

  it('renders the exact pinned sentence for the unauthorized "required" reason', async () => {
    const client = new FakeWsClient()
    client.emitState('unauthorized', 'required')
    await mountDashboard(client)

    expect(banner().getAttribute('data-banner')).toBe('unauthorized')
    expect((banner().textContent ?? '').trim()).toBe(
      'The unit requires a token and none is stored. Add it in Settings.',
    )
  })

  it('carries the restarting state with the exact pinned sentence', async () => {
    const client = new FakeWsClient()
    client.emitState('connected')
    await mountDashboard(client)
    client.emitMessage(restartingEnvelope('reboot'))
    client.emitState('restarting')
    await settle()

    expect(banner().getAttribute('data-banner')).toBe('restarting')
    expect((banner().textContent ?? '').trim()).toBe('The unit is restarting.')
  })

  // SPEC.md 4.5.1.1 pins two distinct sentences for restarting's two reason
  // groups (mode_change/reboot vs shutdown), and then immediately qualifies
  // it: "The two restarting sentences are specified and not yet reachable,
  // issue #131. `connection` mirrors the client's state and its unauthorized
  // reason and nothing else (§4.4.1) ... So a view can tell restarting from
  // connected and cannot tell a reboot from a shutdown, and the Dashboard
  // renders one reason-agnostic line for both until the store carries the
  // reason." The test below pins *that* current, specified state rather
  // than the eventual one: both reasons render the same banner line today,
  // by design, tracked as issue #131. A future change wiring the reason
  // into `connection` should make the two sentences differ and this test
  // should then flip to `.not.toBe`, matching the banner table's two rows.
  it('renders the same reason-agnostic restarting line for reboot and shutdown today (issue #131)', async () => {
    const rebootClient = new FakeWsClient()
    rebootClient.emitState('connected')
    await mountDashboard(rebootClient)
    rebootClient.emitMessage(restartingEnvelope('reboot'))
    rebootClient.emitState('restarting')
    await settle()
    const rebootText = (banner().textContent ?? '').toLowerCase()
    expect(banner().getAttribute('data-banner')).toBe('restarting')

    if (instance && svelte) svelte.unmount(instance)
    instance = null
    container?.remove()
    container = null
    if (teardownStores) {
      teardownStores()
      teardownStores = null
    }
    resetStores()

    const shutdownClient = new FakeWsClient()
    shutdownClient.emitState('connected')
    await mountDashboard(shutdownClient)
    shutdownClient.emitMessage(restartingEnvelope('shutdown'))
    shutdownClient.emitState('restarting')
    await settle()
    const shutdownText = (banner().textContent ?? '').toLowerCase()
    expect(banner().getAttribute('data-banner')).toBe('restarting')

    expect(
      shutdownText,
      'issue #131: the two restarting reasons share one banner line until connection carries the reason; ' +
        'if this now fails, the store learned to distinguish them and this test should flip to .not.toBe',
    ).toBe(rebootText)
  })
})
