import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import getAccessPointsSchema from '../../../docs/schemas/incoming/get_access_points.json'
import getHandshakesSchema from '../../../docs/schemas/incoming/get_handshakes.json'

import type {
  AccessPoint,
  HandshakeEntry,
  OutgoingAccessPoints,
  OutgoingHandshakesList,
  OutgoingMessage,
  OutgoingStats,
  Stats,
} from '../lib/protocol'
import type {
  ConnectionState,
  Diagnostics,
  StatsSnapshot,
  UnauthorizedReason,
  WsClient,
  WsClientOptions,
} from '../lib/ws'
// DASH and EMPTY_LABEL are the pre-existing SPEC 4.5.1.1 surface, not part
// of this change: the row-level empty rule this file exercises on Nearby
// and Captured fields is that section's own rule, restated for this view by
// SPEC 4.5.2.3 ("every remote string on a row follows 4.5.1.1").
import { DASH, EMPTY_LABEL, formatUnitTime } from '../lib/format'

import { assertRemoteStringIsolated } from './helpers/remoteText'

// Written from SPEC.md 4.5.2.3 ("Wi-Fi: two segments, and the data behind
// them", issue #187), plus 4.5 (the segmented control's own mechanics,
// restated nowhere by 4.5.2.3), 4.5.1.1 and 4.5.2.1 (the DOM-hook and
// copy-lives-in-format.ts conventions this section inherits), 4.5.2, 4.5.2.2
// (the startSession/FakeWsClient harness), 4.4.1, 4.4.2, 2.7, 2.8 and 10.7.
//
// Deliberately not read: views/WiFi.svelte, shell/Segmented.svelte, or
// whatever new lib/ surface carries the refresh and the sort (both authored
// in parallel), nor the parts of lib/format.ts this change adds. Every
// numeric/string rule this file pins on the byte-size formatter is asserted
// through the rendered DOM (data-field="size"), never through an imported
// function, because 4.5.2.3 does not name that function the way 4.5.1.1
// names formatUptime and friends.
//
// Every element is reached through the DOM hooks 4.5.2.3 declares
// (data-view, data-segment, data-segment-panel, data-segment-badge,
// data-row, data-row-key, data-field, data-action, data-empty-message,
// data-truncation-notice) and nothing else. Every copy assertion is a
// literal transcribed from 4.5.2.3's own tables, never a string imported
// from lib/format.ts.
//
// The client is reached through lib/session.ts's startSession(), the same
// seam dashboard-controls.spec.ts uses, since a lib/-driven refresh has
// nowhere to send get_access_points/get_handshakes without a currentClient()
// to send them through.

// ---------------------------------------------------------------------------
// Fixture builders. Realistic shapes, synthetic content only.
// ---------------------------------------------------------------------------

const T = 1_700_000_000

function accessPoint(overrides: Partial<AccessPoint> = {}): AccessPoint {
  return {
    bssid: 'AA:BB:CC:DD:EE:01',
    hostname: 'TestNet-001',
    channel: 6,
    rssi: -55,
    encryption: 'WPA2',
    vendor: 'TestVendor',
    clients: 2,
    ...overrides,
  }
}

function handshakeEntry(
  overrides: Partial<HandshakeEntry> = {},
): HandshakeEntry {
  return {
    filename: 'TestNet_001_aabbccddeeff.pcapng',
    ssid: 'TestNet_001',
    bssid: 'aabbccddeeff',
    mtime: T,
    size: 4096,
    gps: null,
    ...overrides,
  }
}

function accessPointsEnvelope(list: AccessPoint[]): OutgoingAccessPoints {
  return { type: 'access_points', timestamp: T, data: list }
}

function handshakesListEnvelope(
  entries: HandshakeEntry[],
  truncated = false,
  total: number | null = entries.length,
): OutgoingHandshakesList {
  return {
    type: 'handshakes_list',
    timestamp: T,
    data: { entries, truncated, total },
  }
}

function battery() {
  return { percent: 80, charging: false }
}

function gpsReading() {
  return {
    enabled: true,
    source: 'gpsd' as const,
    piFix: true,
    fix: true,
    lat: 10.1,
    lon: -30.2,
    altitude: 5,
    accuracy: 8,
    updated: T,
  }
}

function statsData(overrides: Partial<Stats> = {}): Stats {
  return {
    uptime: 120,
    name: null,
    mode: 'AUTO',
    channel: 6,
    battery: battery(),
    temperature: 45,
    handshakes: 3,
    handshakesTotal: 3,
    peers: 1,
    accessPoints: 5,
    lastHandshake: null,
    lastPeer: null,
    gps: gpsReading(),
    sessionAge: 5,
    capabilities: {
      pasv: true,
      pisugar: false,
      gpsSource: 'gpsd',
      pluginVersion: '0.1.0',
    },
    ...overrides,
  }
}

function statsEnvelope(overrides: Partial<Stats> = {}): OutgoingStats {
  return { type: 'stats', timestamp: T, data: statsData(overrides) }
}

// ---------------------------------------------------------------------------
// A fake localStorage, the same shape every other frontend spec file uses.
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

// ---------------------------------------------------------------------------
// A hand-driven WsClient double. Records every request() call by type,
// letting a test settle or reject a specific one (get_access_points and
// get_handshakes are both outstanding at once, so "the oldest pending" is
// not enough - each has to be addressable by its own type). command() is
// never issued by this view: it rejects, per SPEC 4.4's rule that the store
// layer never issues a command of its own, which the same reasoning covers
// here since a Wi-Fi refresh is a read (get_access_points/get_handshakes),
// never a state command.
// ---------------------------------------------------------------------------

interface PendingRequest {
  type: string
  resolve: (message: OutgoingMessage) => void
  reject: (error: Error) => void
}

class FakeWsClient implements WsClient {
  currentState: ConnectionState = 'connected'
  reasonValue: UnauthorizedReason | null = null
  private lastStatsValue: StatsSnapshot | null = null
  readonly stateHandlers = new Set<(state: ConnectionState) => void>()
  readonly messageHandlers = new Set<(message: OutgoingMessage) => void>()
  readonly requestCalls: string[] = []
  private readonly pending: PendingRequest[] = []

  constructor(readonly options?: WsClientOptions) {}

  connect(): void {}
  close(): void {}
  state(): ConnectionState {
    return this.currentState
  }
  unauthorizedReason(): UnauthorizedReason | null {
    return this.reasonValue
  }
  // SPEC 4.4.1 (issue #131): not exercised by this file - no test here
  // drives a restarting state, so this always answers null rather than
  // gating on state like stores.spec.ts's own double does.
  restartReason(): null {
    return null
  }
  lastStats(): StatsSnapshot | null {
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
  request(type: string, ..._rest: unknown[]): Promise<OutgoingMessage> {
    this.requestCalls.push(type)
    return new Promise((resolve, reject) => {
      this.pending.push({ type, resolve, reject })
    })
  }
  command(): Promise<OutgoingMessage> {
    return Promise.reject(
      new Error('the wifi view must never send a command, only reads'),
    )
  }
  sendGps(): void {}
  diagnostics(): Diagnostics {
    return { lastError: null, latencyMs: null, droppedFrames: 0 }
  }
  onDiagnostics(): () => void {
    return () => {}
  }

  emitState(
    state: ConnectionState,
    reason: UnauthorizedReason | null = null,
  ): void {
    this.currentState = state
    this.reasonValue = reason
    for (const handler of [...this.stateHandlers]) handler(state)
  }

  emitMessage(message: OutgoingMessage): void {
    if (message.type === 'stats')
      this.lastStatsValue = {
        stats: message.data,
        timestamp: message.timestamp,
      }
    for (const handler of [...this.messageHandlers]) handler(message)
  }

  /** Settles the oldest still-pending request() call of the given type. */
  settle(type: string, reply: OutgoingMessage): void {
    const index = this.pending.findIndex((entry) => entry.type === type)
    expect(index, `expected a pending "${type}" request`).not.toBe(-1)
    const entry = this.pending.splice(index, 1)[0] as PendingRequest
    this.emitMessage(reply)
    entry.resolve(reply)
  }

  /** Rejects the oldest still-pending request() call of the given type. */
  rejectPending(type: string, error: Error): void {
    const index = this.pending.findIndex((entry) => entry.type === type)
    expect(index, `expected a pending "${type}" request`).not.toBe(-1)
    const entry = this.pending.splice(index, 1)[0] as PendingRequest
    entry.reject(error)
  }

  countRequestCalls(type: string): number {
    return this.requestCalls.filter((entry) => entry === type).length
  }
}

function asClient(fake: FakeWsClient): WsClient {
  return fake
}

// ---------------------------------------------------------------------------
// Mounting: WiFi.svelte via a real session (startSession), exactly the
// pattern dashboard-controls.spec.ts uses and for the same reason - a lib/
// function reached from currentClient() has nowhere to send a request
// without one. lib/settings.ts, lib/session.ts, lib/ws.ts and the view are
// imported together, after vi.resetModules(), so the module singletons
// never diverge (the same trap dashboard-controls.spec.ts documents: a
// statically-imported class or function is a different module-graph object
// from the one the dynamically-mounted view's `instanceof`/internal checks
// use, so every runtime value below is taken from the same dynamic import
// mountWifi() itself performs).
// ---------------------------------------------------------------------------

type SvelteApi = typeof import('svelte')
type SettingsModule = typeof import('../lib/settings')
type SessionModule = typeof import('../lib/session')
type RouterModule = typeof import('../lib/router')

let svelte: SvelteApi | null = null
let container: HTMLElement | null = null
let instance: Record<string, unknown> | null = null
let stopSession: (() => void) | null = null

beforeEach(() => {
  fakeStorage = new FakeStorage()
  vi.stubGlobal('localStorage', fakeStorage)
  // SPEC 4.5.2.3: the refresh trigger is the route becoming /wifi, which
  // lib/router.ts reads off window.location. jsdom's location survives
  // across tests (unlike the module registry, which vi.resetModules()
  // below gives a fresh copy of), so a stray pushState from a previous
  // test's navigation would otherwise leak into this one - the same reset
  // navigation.spec.ts's own beforeEach performs.
  window.history.pushState({}, '', '/')
  vi.resetModules()
})

afterEach(async () => {
  if (instance && svelte) svelte.unmount(instance)
  instance = null
  container?.remove()
  container = null
  if (stopSession) {
    stopSession()
    stopSession = null
  }
  svelte = null
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

async function settle(): Promise<void> {
  svelte?.flushSync()
  await svelte?.tick()
}

/**
 * `route`, unlike `initialState`, is not merely a starting condition: SPEC
 * 4.5.2.3 makes "the route becoming /wifi" the refresh trigger, not the
 * component mounting (views stay mounted across navigation, SPEC 4.5, so a
 * mount-only trigger would fire once per app launch and never again). The
 * default '/wifi' pre-navigates before the component ever subscribes, which
 * is what every test that only cares about the resulting data wants - the
 * first evaluation of a fresh subscription against an already-true
 * condition is itself a legitimate "becoming current", the same way a
 * freshly mounted Dashboard reads a connection state that changed before it
 * existed. Passing `null` mounts the view while the route is whatever it
 * already was (the default '/' from this file's own beforeEach) and leaves
 * navigating to '/wifi' to the test, for the tests that are about the
 * transition itself.
 */
async function mountWifi(
  initialState: ConnectionState = 'connected',
  route: string | null = '/wifi',
  target: HTMLElement | null = null,
  // Fires every time startSession() builds a client, not only the first -
  // the in-place host-switch tests need to see the second one too, since
  // the `client` this function returns is a snapshot taken once, at mount
  // time, and does not follow a later switch (mirrors
  // dashboard-controls.spec.ts's mountDashboard()).
  onClientCreated?: (client: FakeWsClient) => void,
): Promise<{
  client: FakeWsClient
  settings: SettingsModule
  session: SessionModule
  router: RouterModule
  target: HTMLElement
}> {
  svelte = await import('svelte')
  const settings = await import('../lib/settings')
  const session = await import('../lib/session')
  const router = await import('../lib/router')
  // Forces the deterministic prefilled-Bluetooth fallback (SPEC 4.7/4.8),
  // the same way dashboard-controls.spec.ts and session.spec.ts do.
  settings.loadSettings(() => 'not-an-ip-address')

  let client!: FakeWsClient
  stopSession = session.startSession({
    createClient: (options: WsClientOptions) => {
      client = new FakeWsClient(options)
      client.currentState = initialState
      onClientCreated?.(client)
      return asClient(client)
    },
  })

  if (route !== null) router.navigate(route)

  const module = (await import('../views/WiFi.svelte')) as unknown as {
    default: Parameters<SvelteApi['mount']>[0]
  }
  const mountTarget = target ?? document.createElement('div')
  if (!target) document.body.appendChild(mountTarget)
  container = mountTarget
  instance = svelte.mount(module.default, { target: mountTarget }) as Record<
    string,
    unknown
  >
  await settle()
  return { client, settings, session, router, target: mountTarget }
}

async function click(element: Element): Promise<void> {
  element.dispatchEvent(
    new MouseEvent('click', { bubbles: true, cancelable: true }),
  )
  await settle()
}

async function pressKey(element: Element, key: string): Promise<KeyboardEvent> {
  const event = new KeyboardEvent('keydown', {
    key,
    bubbles: true,
    cancelable: true,
  })
  element.dispatchEvent(event)
  await settle()
  return event
}

// ---------------------------------------------------------------------------
// Queries, on the declared data-* hooks only (SPEC 4.5.2.3's "The DOM
// hooks", closed list).
// ---------------------------------------------------------------------------

function root(): HTMLElement {
  if (!container) throw new Error('WiFi view is not mounted')
  // The mount target is a bare wrapper div Svelte was given to mount into,
  // not the view root itself: data-view lives on the element the component
  // renders, which querySelector has to find inside the wrapper (SPEC 4.5
  // requires data-view="wifi" on the view root, not on whatever holds it).
  const viewRoot = container.querySelector('[data-view="wifi"]')
  expect(
    viewRoot,
    'expected [data-view="wifi"] inside the mount container',
  ).not.toBeNull()
  return viewRoot as HTMLElement
}

function tab(name: 'nearby' | 'captured'): HTMLElement {
  const found = root().querySelector(`[data-segment="${name}"]`)
  expect(found, `expected [data-segment="${name}"]`).not.toBeNull()
  return found as HTMLElement
}

function panel(name: 'nearby' | 'captured'): HTMLElement {
  const found = root().querySelector(`[data-segment-panel="${name}"]`)
  expect(found, `expected [data-segment-panel="${name}"]`).not.toBeNull()
  return found as HTMLElement
}

function badge(name: 'nearby' | 'captured'): HTMLElement {
  const found = tab(name).querySelector('[data-segment-badge]')
  expect(
    found,
    `expected a data-segment-badge inside the ${name} tab`,
  ).not.toBeNull()
  return found as HTMLElement
}

function rows(name: 'nearby' | 'captured'): HTMLElement[] {
  return Array.from(panel(name).querySelectorAll(`[data-row="${name}"]`))
}

function rowKeys(name: 'nearby' | 'captured'): string[] {
  return rows(name).map((row) => row.getAttribute('data-row-key') ?? '')
}

function fieldIn(row: Element, field: string): HTMLElement {
  const found = row.querySelector(`[data-field="${field}"]`)
  expect(found, `expected [data-field="${field}"] inside row`).not.toBeNull()
  return found as HTMLElement
}

function fieldText(row: Element, field: string): string {
  return (fieldIn(row, field).textContent ?? '').trim()
}

// dashboard.spec.ts's own helper, same shape: the value slot may legitimately
// carry the accessible EMPTY_LABEL text alongside the dash in the same
// element (SPEC 4.5.1.1's "a dash carries an accessible unavailable
// alongside it"), which textContent flattens into one string. This strips
// EMPTY_LABEL back out so a DASH-equality assertion is about the visible
// dash, not about whether a label happens to sit beside it in the DOM.
function visibleFieldText(row: Element, field: string): string {
  return fieldText(row, field).split(EMPTY_LABEL).join('').trim()
}

function emptyMessage(name: 'nearby' | 'captured'): HTMLElement | null {
  return panel(name).querySelector('[data-empty-message]')
}

function truncationNotice(): HTMLElement | null {
  return panel('captured').querySelector('[data-truncation-notice]')
}

function refreshControl(): HTMLElement {
  const found = root().querySelector('[data-action="refresh"]')
  expect(found, 'expected [data-action="refresh"]').not.toBeNull()
  return found as HTMLElement
}

function sortControl(): HTMLElement {
  const found = panel('nearby').querySelector('[data-action="sort"]')
  expect(
    found,
    'expected [data-action="sort"] inside the Nearby panel',
  ).not.toBeNull()
  return found as HTMLElement
}

function tablist(): HTMLElement {
  const found = tab('nearby').closest('[role="tablist"]')
  expect(found, 'expected an ancestor with role="tablist"').not.toBeNull()
  return found as HTMLElement
}

// The same reach dashboard.spec.ts's accessibleText() uses: an accessible
// name can be aria-label or an aria-labelledby target, and nothing here
// pins which - only that one of them is non-empty.
function accessibleName(el: Element): string {
  const ariaLabel = el.getAttribute('aria-label')
  if (ariaLabel !== null) return ariaLabel.trim()
  const labelledBy = el.getAttribute('aria-labelledby')
  if (labelledBy !== null)
    return (document.getElementById(labelledBy)?.textContent ?? '').trim()
  return ''
}

// =============================================================================
// 1. The segmented control is a real tablist (SPEC 4.5, restated by 4.5.2.3).
// =============================================================================

describe('the segmented control is a real tablist', () => {
  it('carries data-view="wifi" on the view root', async () => {
    await mountWifi()
    expect(root().getAttribute('data-view')).toBe('wifi')
  })

  it('starts with Nearby selected: aria-selected, roving tabindex 0, and its panel visible', async () => {
    await mountWifi()
    expect(tab('nearby').getAttribute('role')).toBe('tab')
    expect(tab('captured').getAttribute('role')).toBe('tab')
    expect(tab('nearby').getAttribute('aria-selected')).toBe('true')
    expect(tab('captured').getAttribute('aria-selected')).toBe('false')
    expect(tab('nearby').getAttribute('tabindex')).toBe('0')
    expect(tab('captured').getAttribute('tabindex')).toBe('-1')
  })

  it('the tabs sit inside an element carrying role="tablist"', async () => {
    await mountWifi()
    expect(tab('nearby').closest('[role="tablist"]')).not.toBeNull()
    expect(tab('captured').closest('[role="tablist"]')).not.toBeNull()
  })

  it('the tablist carries an accessible name - a tablist widget with none is unannounced to a screen reader', async () => {
    await mountWifi()
    expect(accessibleName(tablist())).not.toBe('')
  })

  it('the inactive panel carries hidden, the active one does not', async () => {
    await mountWifi()
    expect(panel('nearby').hasAttribute('hidden')).toBe(false)
    expect(panel('captured').hasAttribute('hidden')).toBe(true)
  })

  it('each panel carries role="tabpanel", labelled back by aria-labelledby to its own tab', async () => {
    await mountWifi()
    for (const name of ['nearby', 'captured'] as const) {
      expect(panel(name).getAttribute('role')).toBe('tabpanel')
      const labelledBy = panel(name).getAttribute('aria-labelledby')
      expect(labelledBy, `${name} panel's aria-labelledby`).toBeTruthy()
      expect(document.getElementById(labelledBy as string)).toBe(tab(name))
    }
  })

  it('each tab points at its own panel through aria-controls', async () => {
    await mountWifi()
    for (const name of ['nearby', 'captured'] as const) {
      const controls = tab(name).getAttribute('aria-controls')
      expect(controls, `${name} tab's aria-controls`).toBeTruthy()
      expect(document.getElementById(controls as string)).toBe(panel(name))
    }
  })

  it('ArrowRight moves focus to Captured and activates it automatically, with no separate activation key', async () => {
    await mountWifi()
    tab('nearby').focus()
    await pressKey(tab('nearby'), 'ArrowRight')
    expect(document.activeElement).toBe(tab('captured'))
    expect(tab('captured').getAttribute('aria-selected')).toBe('true')
    expect(tab('captured').getAttribute('tabindex')).toBe('0')
    expect(tab('nearby').getAttribute('aria-selected')).toBe('false')
    expect(tab('nearby').getAttribute('tabindex')).toBe('-1')
    expect(panel('captured').hasAttribute('hidden')).toBe(false)
    expect(panel('nearby').hasAttribute('hidden')).toBe(true)
  })

  it('ArrowLeft from Captured moves focus back to Nearby and activates it', async () => {
    await mountWifi()
    tab('nearby').focus()
    await pressKey(tab('nearby'), 'ArrowRight')
    await pressKey(tab('captured'), 'ArrowLeft')
    expect(document.activeElement).toBe(tab('nearby'))
    expect(tab('nearby').getAttribute('aria-selected')).toBe('true')
    expect(panel('nearby').hasAttribute('hidden')).toBe(false)
  })

  it('End moves focus to and activates the last tab regardless of where focus started', async () => {
    await mountWifi()
    tab('nearby').focus()
    await pressKey(tab('nearby'), 'End')
    expect(document.activeElement).toBe(tab('captured'))
    expect(tab('captured').getAttribute('aria-selected')).toBe('true')
  })

  it('Home moves focus to and activates the first tab regardless of where focus started', async () => {
    await mountWifi()
    tab('nearby').focus()
    await pressKey(tab('nearby'), 'ArrowRight') // now on captured
    await pressKey(tab('captured'), 'Home')
    expect(document.activeElement).toBe(tab('nearby'))
    expect(tab('nearby').getAttribute('aria-selected')).toBe('true')
  })

  it('ArrowRight on the last tab wraps to the first (SPEC 4.5.2.3: arrow movement wraps)', async () => {
    await mountWifi()
    tab('nearby').focus()
    await pressKey(tab('nearby'), 'ArrowRight') // now on Captured, the last tab
    await pressKey(tab('captured'), 'ArrowRight') // wraps to Nearby, the first tab
    expect(document.activeElement).toBe(tab('nearby'))
    expect(tab('nearby').getAttribute('aria-selected')).toBe('true')
    expect(panel('nearby').hasAttribute('hidden')).toBe(false)
  })

  it('ArrowLeft on the first tab wraps to the last', async () => {
    await mountWifi()
    tab('nearby').focus() // Nearby is the first tab, and already selected
    await pressKey(tab('nearby'), 'ArrowLeft') // wraps to Captured, the last tab
    expect(document.activeElement).toBe(tab('captured'))
    expect(tab('captured').getAttribute('aria-selected')).toBe('true')
    expect(panel('captured').hasAttribute('hidden')).toBe(false)
  })

  it('an unhandled key is ignored: selection and focus do not change, and the event is not swallowed', async () => {
    const { client } = await mountWifi()
    client.settle('get_access_points', accessPointsEnvelope([]))
    client.settle('get_handshakes', handshakesListEnvelope([]))
    await settle()
    tab('nearby').focus()
    // SPEC 4.5 gives the tablist Arrow, Home and End and nothing else - a
    // letter key, Tab and Enter must all fall through untouched. Tab is the
    // one worth naming: a tablist that quietly consumes it is a keyboard
    // trap, which is the failure mode defaultPrevented guards against here.
    for (const key of ['a', 'Tab', 'Enter', ' ']) {
      const event = await pressKey(tab('nearby'), key)
      expect(event.defaultPrevented, `key "${key}" must not be swallowed`).toBe(
        false,
      )
      expect(document.activeElement, `key "${key}" must not move focus`).toBe(
        tab('nearby'),
      )
      expect(tab('nearby').getAttribute('aria-selected')).toBe('true')
      expect(tab('captured').getAttribute('aria-selected')).toBe('false')
      expect(panel('nearby').hasAttribute('hidden')).toBe(false)
    }
  })

  it('clicking the Captured tab activates it directly, the same as arriving there by keyboard', async () => {
    await mountWifi()
    await click(tab('captured'))
    expect(tab('captured').getAttribute('aria-selected')).toBe('true')
    expect(panel('captured').hasAttribute('hidden')).toBe(false)
    expect(panel('nearby').hasAttribute('hidden')).toBe(true)
  })

  it('both panels stay mounted across a switch - the same Nearby panel node survives being hidden', async () => {
    await mountWifi()
    const nearbyPanelNode = panel('nearby')
    await pressKey(tab('nearby'), 'ArrowRight')
    // Still in the document, just hidden - not destroyed and not recreated.
    expect(root().contains(nearbyPanelNode)).toBe(true)
    expect(panel('nearby')).toBe(nearbyPanelNode)
    await pressKey(tab('captured'), 'ArrowLeft')
    expect(panel('nearby')).toBe(nearbyPanelNode)
    expect(panel('nearby').hasAttribute('hidden')).toBe(false)
  })
})

// =============================================================================
// 2. Both lists are fetched together, coalesced per client, and by no timer.
// =============================================================================

describe('the refresh: asked for on mount and by the control, both lists together, coalesced per client', () => {
  it('mounting the view issues exactly one get_access_points and one get_handshakes request', async () => {
    const { client } = await mountWifi()
    expect(client.countRequestCalls('get_access_points')).toBe(1)
    expect(client.countRequestCalls('get_handshakes')).toBe(1)
  })

  it('the request types match docs/schemas\' own "type" const, not a hand-typed literal that could drift from it', async () => {
    const { client } = await mountWifi()
    const accessPointsType = (
      getAccessPointsSchema as { properties: { type: { const: string } } }
    ).properties.type.const
    const handshakesType = (
      getHandshakesSchema as { properties: { type: { const: string } } }
    ).properties.type.const
    expect(client.requestCalls).toContain(accessPointsType)
    expect(client.requestCalls).toContain(handshakesType)
  })

  it('refresh sits above the tablist and outside both panels - it acts on both lists, not on whichever is showing', async () => {
    await mountWifi()
    const control = refreshControl()
    expect(panel('nearby').contains(control)).toBe(false)
    expect(panel('captured').contains(control)).toBe(false)
    // DOCUMENT_POSITION_FOLLOWING on the tablist, read from the control,
    // means the control comes before the tablist in document order.
    const position = control.compareDocumentPosition(tablist())
    expect(position & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it('the refresh control issues another pair of requests once the mount-time pair has settled', async () => {
    const { client } = await mountWifi()
    client.settle('get_access_points', accessPointsEnvelope([]))
    client.settle('get_handshakes', handshakesListEnvelope([]))
    await settle()
    // The coalescing owner clears a hop or two after resolve(), not
    // necessarily within a single microtask - the same shape
    // stores.spec.ts's own tests document for refreshHandshakes, which this
    // refresh is coalesced "exactly as" (SPEC 4.5.2.3). Flushing past a
    // macrotask, rather than pinning an exact microtask count, is what the
    // coalescing contract actually promises.
    await new Promise((resolve) => setTimeout(resolve, 0))
    await click(refreshControl())
    expect(client.countRequestCalls('get_access_points')).toBe(2)
    expect(client.countRequestCalls('get_handshakes')).toBe(2)
  })

  it('tapping refresh while the mount-time pair is still in flight coalesces into it, rather than adding a second pair', async () => {
    const { client } = await mountWifi()
    expect(client.countRequestCalls('get_access_points')).toBe(1)
    expect(client.countRequestCalls('get_handshakes')).toBe(1)
    // Neither request has been settled: SPEC 4.5.2.3 coalesces the refresh
    // per client, the same rule the tablist-mount coalescing test below
    // pins from the other side.
    await click(refreshControl())
    expect(client.countRequestCalls('get_access_points')).toBe(1)
    expect(client.countRequestCalls('get_handshakes')).toBe(1)
  })

  it('a second mount against the same client, while the first pair is still in flight, does not add a second pair', async () => {
    const { client } = await mountWifi()
    expect(client.countRequestCalls('get_access_points')).toBe(1)
    expect(client.countRequestCalls('get_handshakes')).toBe(1)

    // A second WiFi.svelte instance mounted onto its own element, against
    // the very same client (session.currentClient() is a module singleton -
    // SPEC 4.5.2.3 says the coalescing is per client, not per component
    // instance).
    svelte = await import('svelte')
    const module = (await import('../views/WiFi.svelte')) as unknown as {
      default: Parameters<SvelteApi['mount']>[0]
    }
    const secondTarget = document.createElement('div')
    document.body.appendChild(secondTarget)
    const secondInstance = svelte.mount(module.default, {
      target: secondTarget,
    })
    await settle()

    expect(client.countRequestCalls('get_access_points')).toBe(1)
    expect(client.countRequestCalls('get_handshakes')).toBe(1)

    svelte.unmount(secondInstance)
    secondTarget.remove()
  })

  it('a client that is no longer attached does not block a new one: the new client sends its own pair', async () => {
    // The route becoming /wifi against client A (mountWifi's default
    // pre-navigates), whose pair is left
    // deliberately unsettled - its reply can never reach a store once A is
    // detached (SPEC 4.4.1's refreshHandshakes reasoning, restated for this
    // refresh by 4.5.2.3).
    const first = await mountWifi()
    expect(first.client.countRequestCalls('get_access_points')).toBe(1)
    expect(first.client.countRequestCalls('get_handshakes')).toBe(1)
    if (instance && svelte) svelte.unmount(instance)
    instance = null
    container?.remove()
    container = null
    stopSession?.()
    stopSession = null

    // A fresh session, a fresh client, the route becoming /wifi again for it -
    // the new
    // client must not be blocked by A's owner having never cleared.
    const second = await mountWifi()
    expect(second.client.countRequestCalls('get_access_points')).toBe(1)
    expect(second.client.countRequestCalls('get_handshakes')).toBe(1)
  })

  it("A's own refresh settling late, after B has already taken over, does not release an owner that is now B's", async () => {
    // Client A: the route becomes /wifi, its pair is issued and left
    // deliberately unsettled.
    const first = await mountWifi()
    expect(first.client.countRequestCalls('get_access_points')).toBe(1)
    expect(first.client.countRequestCalls('get_handshakes')).toBe(1)
    if (instance && svelte) svelte.unmount(instance)
    instance = null
    container?.remove()
    container = null
    stopSession?.()
    stopSession = null

    // A fresh session, client B, its own pair also left unsettled.
    const second = await mountWifi()
    expect(second.client.countRequestCalls('get_access_points')).toBe(1)
    expect(second.client.countRequestCalls('get_handshakes')).toBe(1)

    // A's own request finally settles, long after A detached. Its owner is
    // A, not B (lib/stores.ts's own handshakesRefreshOwner comment names
    // exactly this scenario as the reason the owner is a client reference
    // rather than a bare flag) - this must not release an in-flight owner
    // that belongs to B now.
    first.client.settle('get_access_points', accessPointsEnvelope([]))
    first.client.settle('get_handshakes', handshakesListEnvelope([]))
    await settle()
    await new Promise((resolve) => setTimeout(resolve, 0))

    // B's own pair is still unsettled. Tapping refresh now must still
    // coalesce into it - a duplicate here would mean A's late settle wrongly
    // cleared an owner that was never A's to clear.
    await click(refreshControl())
    expect(second.client.countRequestCalls('get_access_points')).toBe(1)
    expect(second.client.countRequestCalls('get_handshakes')).toBe(1)

    // B's own refresh still works once B is genuinely free to ask again.
    second.client.settle('get_access_points', accessPointsEnvelope([]))
    second.client.settle('get_handshakes', handshakesListEnvelope([]))
    await settle()
    await new Promise((resolve) => setTimeout(resolve, 0))
    await click(refreshControl())
    expect(second.client.countRequestCalls('get_access_points')).toBe(2)
    expect(second.client.countRequestCalls('get_handshakes')).toBe(2)
  })

  it('no timer: waiting does not by itself produce another pair of requests', async () => {
    vi.useFakeTimers()
    try {
      const { client } = await mountWifi()
      expect(client.countRequestCalls('get_access_points')).toBe(1)
      expect(client.countRequestCalls('get_handshakes')).toBe(1)
      await vi.advanceTimersByTimeAsync(5 * 60 * 1000)
      expect(client.countRequestCalls('get_access_points')).toBe(1)
      expect(client.countRequestCalls('get_handshakes')).toBe(1)
    } finally {
      vi.useRealTimers()
    }
  })
})

describe('the trigger is the route becoming /wifi, not the component mounting (SPEC 4.5.2.3)', () => {
  it('mounting while the route is not /wifi issues nothing; navigating there asks', async () => {
    // route: null - the view mounts while the default location ('/', the
    // Dashboard route) is still current, per this file's own beforeEach.
    const { client, router } = await mountWifi('connected', null)
    expect(client.countRequestCalls('get_access_points')).toBe(0)
    expect(client.countRequestCalls('get_handshakes')).toBe(0)

    router.navigate('/wifi')
    await settle()
    expect(client.countRequestCalls('get_access_points')).toBe(1)
    expect(client.countRequestCalls('get_handshakes')).toBe(1)
  })

  it('leaving /wifi does not itself ask for anything', async () => {
    const { client, router } = await mountWifi()
    expect(client.countRequestCalls('get_access_points')).toBe(1)
    expect(client.countRequestCalls('get_handshakes')).toBe(1)

    router.navigate('/log')
    await settle()
    expect(client.countRequestCalls('get_access_points')).toBe(1)
    expect(client.countRequestCalls('get_handshakes')).toBe(1)
  })

  it('returning to /wifi on the same still-mounted view asks again - becoming current is not a once-per-launch event', async () => {
    const { client, router } = await mountWifi()
    expect(client.countRequestCalls('get_access_points')).toBe(1)
    expect(client.countRequestCalls('get_handshakes')).toBe(1)

    // The mount-time pair has to settle before leaving, or the return visit
    // coalesces into it rather than asking again (SPEC 4.5.2.3's "coalesced
    // per client" - the same rule the second-mount and tap-while-in-flight
    // tests above pin from the other side). This test is about the trigger,
    // not the coalescing, so nothing may be left in flight to swallow it.
    client.settle('get_access_points', accessPointsEnvelope([]))
    client.settle('get_handshakes', handshakesListEnvelope([]))
    await settle()
    // As in the refresh-after-settle test above: the coalescing owner
    // clears a hop or two after resolve(), not necessarily within a single
    // microtask.
    await new Promise((resolve) => setTimeout(resolve, 0))

    router.navigate('/log')
    await settle()
    router.navigate('/wifi')
    await settle()

    expect(client.countRequestCalls('get_access_points')).toBe(2)
    expect(client.countRequestCalls('get_handshakes')).toBe(2)
  })

  it('a host switch in place - the view still mounted, the route still /wifi - asks the new unit for both lists', async () => {
    const created: FakeWsClient[] = []
    const { client: clientA, settings } = await mountWifi(
      'connected',
      '/wifi',
      null,
      (c) => created.push(c),
    )
    expect(created).toHaveLength(1)
    expect(clientA.countRequestCalls('get_access_points')).toBe(1)
    expect(clientA.countRequestCalls('get_handshakes')).toBe(1)

    // A host switch driven the same way session.spec.ts drives one:
    // settings.addHost then settings.activateHost, inside the same running
    // session. session.ts's rebuild tears A down, calls resetStores()
    // (which empties the handshakes store), and attaches B - all while the
    // WiFi view stays mounted and the route stays /wifi throughout, which is
    // the ordinary case (SPEC 4.5 keeps views mounted; Settings is where a
    // host is switched, SPEC 4.5.1). This pins that an in-place host switch
    // is its own occasion to ask, distinct from the route-becoming-/wifi
    // trigger pinned above: written red against an earlier implementation
    // that asked only on a route change and left an in-place switch silent,
    // and confirmed green once the switch itself became an ask too.
    const hostB = settings.addHost({
      label: 'Unit B',
      address: '172.20.10.9',
      wsPort: 8082,
      httpPort: 8443,
      token: null,
    })
    settings.activateHost(hostB.id)
    await settle()

    expect(created).toHaveLength(2)
    const clientB = created[1] as FakeWsClient
    expect(clientB.countRequestCalls('get_access_points')).toBe(1)
    expect(clientB.countRequestCalls('get_handshakes')).toBe(1)

    // The nastier half of the same defect: resetStores() empties the
    // handshakes store and the initial burst carries no handshakes_list, so
    // without a genuine ask Captured is stuck showing "The unit has no
    // captures." about a unit nothing ever asked - not a one-frame race,
    // since with the trigger broken nothing ever asks again either. Settling
    // B's own reply, rather than reading the sentence immediately after the
    // switch, is what actually tells the two apart: SPEC 4.5.2.3's
    // empty-vs-not-fetched rule is decided by connection state alone with no
    // second "has this list ever been asked for" flag, so a merely-connected,
    // reply-still-pending moment legitimately reads the same "no captures"
    // sentence under a correct fix as it does under the bug, for exactly as
    // long as the real round trip takes - asserting against that instant
    // would pin an interval SPEC's own design accepts, not the defect.
    // Attempting to settle a request that this fake never received throws by
    // itself (FakeWsClient.settle's own "expected a pending request" check),
    // which already fails this test for the right reason if B was never
    // asked; once it does not throw, the panel must show B's real data.
    clientB.settle('get_access_points', accessPointsEnvelope([accessPoint()]))
    clientB.settle('get_handshakes', handshakesListEnvelope([handshakeEntry()]))
    await settle()
    expect(emptyMessage('captured')).toBeNull()
    expect(badge('captured').textContent).toBe('1')
  })

  it('becoming current while offline still asks: being connected is not a precondition, only a state command needs one (SPEC 4.5.2.3, 4.3.3)', async () => {
    const { client, router } = await mountWifi('offline', null)
    router.navigate('/wifi')
    await settle()
    // A read is queued and flushed on reconnect (SPEC 4.3.3); only a state
    // command is refused at the point of sending. Gating the WiFi refresh on
    // connection state would be a second, wrong copy of a rule lib/ws.ts
    // already owns, so becoming current must ask regardless of state.
    expect(client.countRequestCalls('get_access_points')).toBe(1)
    expect(client.countRequestCalls('get_handshakes')).toBe(1)
  })

  it('the socket returning inside the queue window flushes the pair and fills both lists, with no tap on refresh', async () => {
    const { client, router } = await mountWifi('offline', null)
    router.navigate('/wifi')
    await settle()
    expect(client.countRequestCalls('get_access_points')).toBe(1)
    expect(client.countRequestCalls('get_handshakes')).toBe(1)

    // The tether returns and lib/ws.ts's own queue (SPEC 4.3.3, 4.3.8)
    // flushes the two reads that were already asked for. Simulated directly
    // through the fake client's settle(), rather than by advancing a clock:
    // the 15s-from-the-moment-it-was-asked-for timer and flushQueue() are
    // lib/ws.ts's own machinery, exercised in ws.spec.ts, and this fake has
    // no timer of its own for a test here to advance.
    client.emitState('connected')
    client.settle('get_access_points', accessPointsEnvelope([accessPoint()]))
    client.settle('get_handshakes', handshakesListEnvelope([handshakeEntry()]))
    await settle()
    expect(rows('nearby')).toHaveLength(1)
    expect(rows('captured')).toHaveLength(1)
  })

  it('the socket staying down past the queue timeout rejects the reads, which the refresh swallows: the panel still reads not-connected, never empty or stale', async () => {
    const { client, router } = await mountWifi('offline', null)
    router.navigate('/wifi')
    await settle()
    expect(client.countRequestCalls('get_access_points')).toBe(1)
    expect(client.countRequestCalls('get_handshakes')).toBe(1)

    // SPEC 4.5.2.3: an outage longer than SPEC 4.3.8's 15s ends in a
    // rejection the refresh swallows, not in a list that fills when the
    // tether returns. lib/ws.ts owns that 15s clock (armed at the moment
    // the read was asked for, not at send); this fake client stands in for
    // its eventual outcome by rejecting directly, since it has no timer of
    // its own to advance and the clock itself is exercised in ws.spec.ts.
    client.rejectPending(
      'get_access_points',
      new Error('simulated queue timeout'),
    )
    client.rejectPending('get_handshakes', new Error('simulated queue timeout'))
    await settle()

    // Still offline throughout: the sentence must remain the not-connected
    // one, not flip to an empty-list reading and not throw on the rejection.
    expect(emptyMessage('nearby')?.textContent).toBe(
      'Not connected, so this list has not been read.',
    )
    expect(emptyMessage('captured')?.textContent).toBe(
      'Not connected, so this list has not been read.',
    )
  })
})

// =============================================================================
// 3. An empty list and a list nobody has fetched read differently.
// =============================================================================

describe('empty vs. not-yet-fetched, decided by connection state', () => {
  it('Nearby empty while connected: "The unit reports no access points."', async () => {
    const { client } = await mountWifi('connected')
    client.settle('get_access_points', accessPointsEnvelope([]))
    client.settle('get_handshakes', handshakesListEnvelope([]))
    await settle()
    const message = emptyMessage('nearby')
    expect(message).not.toBeNull()
    expect(message?.textContent).toBe('The unit reports no access points.')
    expect(message?.getAttribute('role')).toBe('status')
  })

  it('Nearby empty while degraded (still counted as fetched): the same sentence as connected', async () => {
    const { client } = await mountWifi('degraded')
    client.settle('get_access_points', accessPointsEnvelope([]))
    client.settle('get_handshakes', handshakesListEnvelope([]))
    await settle()
    expect(emptyMessage('nearby')?.textContent).toBe(
      'The unit reports no access points.',
    )
  })

  it('Nearby empty while not connected: "Not connected, so this list has not been read."', async () => {
    await mountWifi('connecting')
    expect(emptyMessage('nearby')?.textContent).toBe(
      'Not connected, so this list has not been read.',
    )
  })

  it('Nearby empty while offline: the same not-connected sentence', async () => {
    await mountWifi('offline')
    expect(emptyMessage('nearby')?.textContent).toBe(
      'Not connected, so this list has not been read.',
    )
  })

  it('Captured empty while connected: "The unit has no captures."', async () => {
    const { client } = await mountWifi('connected')
    client.settle('get_access_points', accessPointsEnvelope([]))
    client.settle('get_handshakes', handshakesListEnvelope([]))
    await settle()
    expect(emptyMessage('captured')?.textContent).toBe(
      'The unit has no captures.',
    )
  })

  it('Captured empty while not connected: the not-connected sentence', async () => {
    await mountWifi('offline')
    expect(emptyMessage('captured')?.textContent).toBe(
      'Not connected, so this list has not been read.',
    )
  })

  it('the empty message is absent once a panel has rows', async () => {
    const { client } = await mountWifi('connected')
    client.settle('get_access_points', accessPointsEnvelope([accessPoint()]))
    client.settle('get_handshakes', handshakesListEnvelope([handshakeEntry()]))
    await settle()
    expect(emptyMessage('nearby')).toBeNull()
    expect(emptyMessage('captured')).toBeNull()
  })
})

// =============================================================================
// 4. Order: Nearby is a total order sorted by the client, Captured is not.
// =============================================================================

describe('Nearby order is total: RSSI first, BSSID as the tie-break, stable across frames', () => {
  it('sorts strongest RSSI first', async () => {
    const { client } = await mountWifi()
    const weak = accessPoint({ bssid: 'AA:BB:CC:DD:EE:03', rssi: -80 })
    const strong = accessPoint({ bssid: 'AA:BB:CC:DD:EE:01', rssi: -40 })
    const mid = accessPoint({ bssid: 'AA:BB:CC:DD:EE:02', rssi: -60 })
    client.settle(
      'get_access_points',
      accessPointsEnvelope([weak, strong, mid]),
    )
    client.settle('get_handshakes', handshakesListEnvelope([]))
    await settle()
    expect(rowKeys('nearby')).toEqual([strong.bssid, mid.bssid, weak.bssid])
  })

  it('two access points tied on RSSI hold the same relative order regardless of arrival order - the tie-break is a function of BSSID, not of arrival', async () => {
    const tiedA = accessPoint({ bssid: 'AA:BB:CC:DD:EE:01', rssi: -50 })
    const tiedB = accessPoint({ bssid: 'AA:BB:CC:DD:EE:02', rssi: -50 })

    const first = await mountWifi()
    first.client.settle(
      'get_access_points',
      accessPointsEnvelope([tiedA, tiedB]),
    )
    first.client.settle('get_handshakes', handshakesListEnvelope([]))
    await settle()
    const orderOne = rowKeys('nearby')

    if (instance && svelte) svelte.unmount(instance)
    instance = null
    container?.remove()
    container = null
    stopSession?.()
    stopSession = null

    const second = await mountWifi()
    // Same two access points, same RSSI tie, arriving in the opposite order.
    second.client.settle(
      'get_access_points',
      accessPointsEnvelope([tiedB, tiedA]),
    )
    second.client.settle('get_handshakes', handshakesListEnvelope([]))
    await settle()
    const orderTwo = rowKeys('nearby')

    expect(orderOne).toEqual(orderTwo)
    expect(new Set(orderOne)).toEqual(new Set([tiedA.bssid, tiedB.bssid]))
  })

  it('a later frame with the same RSSI ties keeps the same relative order rather than swapping', async () => {
    const tiedA = accessPoint({ bssid: 'AA:BB:CC:DD:EE:01', rssi: -50 })
    const tiedB = accessPoint({ bssid: 'AA:BB:CC:DD:EE:02', rssi: -50 })
    const { client } = await mountWifi()
    client.settle('get_access_points', accessPointsEnvelope([tiedA, tiedB]))
    client.settle('get_handshakes', handshakesListEnvelope([]))
    await settle()
    const firstOrder = rowKeys('nearby')

    // A fresh access_points push (SPEC 4.4.1: replaced whole), same set,
    // still tied, arriving in a different array order.
    client.emitMessage(accessPointsEnvelope([tiedB, tiedA]))
    await settle()
    expect(rowKeys('nearby')).toEqual(firstOrder)
  })

  it('the sort control names what tapping it will do, and tapping it switches to channel order, lowest first (SPEC 4.5.2.3 fixes the direction)', async () => {
    // Deliberately disagreeing on both keys: RSSI order and channel order
    // must pick out opposite rows first, or a fixture where they happen to
    // coincide would pass even if the control did nothing.
    const weakLowChannel = accessPoint({
      bssid: 'AA:BB:CC:DD:EE:01',
      rssi: -80,
      channel: 1,
    })
    const strongHighChannel = accessPoint({
      bssid: 'AA:BB:CC:DD:EE:02',
      rssi: -40,
      channel: 11,
    })
    const { client } = await mountWifi()
    client.settle(
      'get_access_points',
      accessPointsEnvelope([weakLowChannel, strongHighChannel]),
    )
    client.settle('get_handshakes', handshakesListEnvelope([]))
    await settle()

    // RSSI order: strongest first.
    expect(rowKeys('nearby')).toEqual([
      strongHighChannel.bssid,
      weakLowChannel.bssid,
    ])
    expect(sortControl().textContent).toBe('Sort by channel')

    await click(sortControl())
    expect(sortControl().textContent).toBe('Sort by signal')

    // Channel order: lowest first, exactly reversed from the RSSI order above.
    expect(rowKeys('nearby')).toEqual([
      weakLowChannel.bssid,
      strongHighChannel.bssid,
    ])
  })

  it('the BSSID tie-break is ascending, on both orders', async () => {
    const tiedLower = accessPoint({
      bssid: 'AA:BB:CC:DD:EE:01',
      rssi: -50,
      channel: 6,
    })
    const tiedHigher = accessPoint({
      bssid: 'AA:BB:CC:DD:EE:02',
      rssi: -50,
      channel: 6,
    })
    const { client } = await mountWifi()
    // Arrive in descending BSSID order, so an unsorted or arrival-order
    // render would read backwards from what is asserted below.
    client.settle(
      'get_access_points',
      accessPointsEnvelope([tiedHigher, tiedLower]),
    )
    client.settle('get_handshakes', handshakesListEnvelope([]))
    await settle()
    expect(rowKeys('nearby')).toEqual([tiedLower.bssid, tiedHigher.bssid])

    await click(sortControl())
    expect(rowKeys('nearby')).toEqual([tiedLower.bssid, tiedHigher.bssid])
  })

  it('the channel order is also total: a RSSI-tied pair with distinct channels holds a consistent, frame-stable order once sorted by channel', async () => {
    const chanA = accessPoint({
      bssid: 'AA:BB:CC:DD:EE:01',
      rssi: -50,
      channel: 1,
    })
    const chanB = accessPoint({
      bssid: 'AA:BB:CC:DD:EE:02',
      rssi: -50,
      channel: 6,
    })
    const { client } = await mountWifi()
    client.settle('get_access_points', accessPointsEnvelope([chanA, chanB]))
    client.settle('get_handshakes', handshakesListEnvelope([]))
    await settle()
    await click(sortControl())
    const orderOne = rowKeys('nearby')

    client.emitMessage(accessPointsEnvelope([chanB, chanA]))
    await settle()
    expect(rowKeys('nearby')).toEqual(orderOne)
  })
})

describe('Captured is never re-sorted: rendered in exactly the order it arrived', () => {
  it('renders a payload whose entries are in no client-computable order, in exactly that order', async () => {
    // Not sorted by mtime, not by ssid, not by bssid, not by size - so a
    // client re-sorting by any obvious key would produce a different order
    // than the one asserted below.
    const one = handshakeEntry({
      filename: 'zzz_capture.pcapng',
      ssid: 'zzz',
      bssid: 'aaaaaaaaaaaa',
      mtime: T + 500,
      size: 10,
    })
    const two = handshakeEntry({
      filename: 'aaa_capture.pcapng',
      ssid: 'aaa',
      bssid: 'ffffffffffff',
      mtime: T + 1,
      size: 999999,
    })
    const three = handshakeEntry({
      filename: 'mmm_capture.pcapng',
      ssid: 'mmm',
      bssid: 'bbbbbbbbbbbb',
      mtime: T + 250,
      size: 5,
    })
    const { client } = await mountWifi()
    client.settle('get_access_points', accessPointsEnvelope([]))
    client.settle('get_handshakes', handshakesListEnvelope([one, two, three]))
    await settle()
    expect(rowKeys('captured')).toEqual([
      one.filename,
      two.filename,
      three.filename,
    ])
  })
})

// SPEC 4.5.2.3 never claims the BSSID is unique: it names it as the
// data-row-key and as the sort tie-break, and contrasts it explicitly with
// the filename, "the one field 2.7 guarantees unique". Two access points
// with the same key are a conformant access_points payload the plugin's own
// map_access_point can produce without any attacker involved -- it sets
// bssid to '' when bettercap reports no mac, so two APs with no mac are two
// empty-string keys, and two APs advertising the same BSSID are the
// ordinary hostile case SPEC 4.5.3 exists for. Both must render as two rows.
// Written red against an implementation keyed on bssid alone: Svelte 5
// throws each_key_duplicate on a duplicate #each key, in production as well
// as in dev, and the throw escapes from the render effect, which a reading
// of Svelte's each.js predicted before either test was run. Confirmed by
// mutation once the fix landed: putting the bare bssid key back fails
// exactly these two tests and no others.
describe('the Nearby list does not assume the BSSID is unique (SPEC 4.5.2.3)', () => {
  it('two access points reporting the same BSSID both render as separate rows', async () => {
    const first = accessPoint({
      bssid: 'AA:BB:CC:DD:EE:01',
      hostname: 'DuplicateOne',
    })
    const second = accessPoint({
      bssid: 'AA:BB:CC:DD:EE:01',
      hostname: 'DuplicateTwo',
      channel: 11,
      rssi: -70,
    })
    const { client } = await mountWifi()
    client.settle('get_access_points', accessPointsEnvelope([first, second]))
    client.settle('get_handshakes', handshakesListEnvelope([]))
    await settle()
    expect(rows('nearby')).toHaveLength(2)
  })

  it("two access points that both report an empty BSSID (map_access_point's own fallback when bettercap has no mac) both render as separate rows", async () => {
    const first = accessPoint({ bssid: '', hostname: 'NoMacOne' })
    const second = accessPoint({ bssid: '', hostname: 'NoMacTwo' })
    const { client } = await mountWifi()
    client.settle('get_access_points', accessPointsEnvelope([first, second]))
    client.settle('get_handshakes', handshakesListEnvelope([]))
    await settle()
    expect(rows('nearby')).toHaveLength(2)
  })
})

// =============================================================================
// 5. Badges, truncation notice, and the number that disagrees.
// =============================================================================

describe('badges and the truncation notice', () => {
  it('each badge shows the length of the list it names', async () => {
    const { client } = await mountWifi()
    client.settle(
      'get_access_points',
      accessPointsEnvelope([
        accessPoint({ bssid: 'AA:BB:CC:DD:EE:01' }),
        accessPoint({ bssid: 'AA:BB:CC:DD:EE:02' }),
      ]),
    )
    client.settle('get_handshakes', handshakesListEnvelope([handshakeEntry()]))
    await settle()
    expect(badge('nearby').textContent).toBe('2')
    expect(badge('captured').textContent).toBe('1')
  })

  it('the Captured badge reads 500+ when truncated is set, not the raw entry count', async () => {
    const entries = Array.from({ length: 500 }, (_, index) =>
      handshakeEntry({ filename: `capture_${index}.pcapng`, bssid: null }),
    )
    const { client } = await mountWifi()
    client.settle('get_access_points', accessPointsEnvelope([]))
    client.settle('get_handshakes', handshakesListEnvelope(entries, true, 812))
    await settle()
    expect(badge('captured').textContent).toBe('500+')
  })

  it('the truncation notice carries the total, and is absent when truncated is false', async () => {
    const { client } = await mountWifi()
    client.settle('get_access_points', accessPointsEnvelope([]))
    client.settle(
      'get_handshakes',
      handshakesListEnvelope([handshakeEntry()], true, 812),
    )
    await settle()
    expect(truncationNotice()?.textContent).toBe(
      'Showing the newest 500 captures of 812.',
    )
  })

  it('no truncation notice when truncated is false', async () => {
    const { client } = await mountWifi()
    client.settle('get_access_points', accessPointsEnvelope([]))
    client.settle(
      'get_handshakes',
      handshakesListEnvelope([handshakeEntry()], false, 1),
    )
    await settle()
    expect(truncationNotice()).toBeNull()
  })

  // SPEC 4.5.2.3: "a list that was truncated was a list that was enumerated,
  // so `truncated` cannot be true while `total` is null - and the rule is
  // written here anyway, because 'cannot happen' held by two independent
  // facts is exactly the pairing that stops holding when one of them
  // changes." Nothing in this plugin can send truncated:true with total:null
  // today, and this test is pinned for the same reason SPEC gives for
  // writing the rule down at all: the pairing is enforced by two separate
  // facts staying true together, not by a schema constraint that couples
  // them, so a future change to either fact reaches this state. The notice's
  // signature has to accept a null total without producing nonsense.
  it('truncated:true paired with total:null does not crash and never prints the total as the literal string "null"', async () => {
    const { client } = await mountWifi()
    client.settle('get_access_points', accessPointsEnvelope([]))
    client.settle(
      'get_handshakes',
      handshakesListEnvelope([handshakeEntry()], true, null),
    )
    await expect(settle()).resolves.toBeUndefined()
    expect(root().textContent).not.toContain('null')
  })

  it('the Captured badge is not required to agree with stats.handshakesTotal - a frame where they differ shows both, neither hidden by the other', async () => {
    const { client } = await mountWifi()
    // stats says a very different number of handshakes from the list itself
    // - the two arrive on separate pushes (SPEC 4.5.2) and are allowed to
    // disagree, deliberately, in this test.
    client.emitMessage(statsEnvelope({ handshakesTotal: 999, handshakes: 999 }))
    client.settle('get_access_points', accessPointsEnvelope([]))
    client.settle(
      'get_handshakes',
      handshakesListEnvelope(
        [handshakeEntry(), handshakeEntry({ filename: 'second.pcapng' })],
        false,
        2,
      ),
    )
    await settle()
    expect(badge('captured').textContent).toBe('2')
  })
})

// =============================================================================
// 6. What a row holds: hostile strings render as text, dashes for what did
//    not parse, and a hostname of exactly "-" is not treated as empty.
// =============================================================================

const HOSTILE_STRINGS = [
  '<script>window.__wifiViewPwned = true</script>',
  '"><img src=x onerror="window.__wifiViewPwned = true">',
  '\'"><svg/onload=alert(1)>',
]

describe('every remote string renders as text, on both segments (SPEC 4.5.3)', () => {
  beforeEach(() => {
    // A canary set by a payload that escaped text rendering and actually
    // ran as script/markup. Not present in jsdom by default.
    ;(window as unknown as { __wifiViewPwned?: boolean }).__wifiViewPwned =
      undefined
  })

  // Every field here is isolated inside a <bdi> (6a below pins bssid,
  // encryption, vendor and the captured bssid directly, alongside hostname
  // and ssid), so "no element created" stopped being literally true once
  // the template started wrapping these fields on purpose (SPEC 4.5.3).
  // assertRemoteStringIsolated is what this test's own title always meant:
  // the string's own characters present as text inside that one wrapper,
  // and no script, img or other element anywhere in the field beyond it.
  for (const hostile of HOSTILE_STRINGS) {
    it(`Nearby hostname "${hostile}" renders as text, isolated inside a <bdi>, with no other element created`, async () => {
      const { client } = await mountWifi()
      client.settle(
        'get_access_points',
        accessPointsEnvelope([accessPoint({ hostname: hostile })]),
      )
      client.settle('get_handshakes', handshakesListEnvelope([]))
      await settle()
      const field = fieldIn(rows('nearby')[0] as HTMLElement, 'hostname')
      expect(field.textContent).toBe(hostile)
      expect(root().querySelector('script')).toBeNull()
      expect(root().querySelector('[onerror]')).toBeNull()
      expect(
        (window as unknown as { __wifiViewPwned?: boolean }).__wifiViewPwned,
      ).toBeUndefined()
      assertRemoteStringIsolated(field, hostile)
    })

    it(`Nearby bssid "${hostile}" renders as text, isolated inside a <bdi>, with no other element created`, async () => {
      const { client } = await mountWifi()
      client.settle(
        'get_access_points',
        accessPointsEnvelope([accessPoint({ bssid: hostile })]),
      )
      client.settle('get_handshakes', handshakesListEnvelope([]))
      await settle()
      const field = fieldIn(rows('nearby')[0] as HTMLElement, 'bssid')
      expect(field.textContent).toBe(hostile)
      expect(root().querySelector('script')).toBeNull()
      expect(
        (window as unknown as { __wifiViewPwned?: boolean }).__wifiViewPwned,
      ).toBeUndefined()
      assertRemoteStringIsolated(field, hostile)
    })

    it(`Nearby vendor "${hostile}" renders as text, isolated inside a <bdi>, with no other element created`, async () => {
      const { client } = await mountWifi()
      client.settle(
        'get_access_points',
        accessPointsEnvelope([accessPoint({ vendor: hostile })]),
      )
      client.settle('get_handshakes', handshakesListEnvelope([]))
      await settle()
      const field = fieldIn(rows('nearby')[0] as HTMLElement, 'vendor')
      expect(field.textContent).toBe(hostile)
      expect(root().querySelector('script')).toBeNull()
      expect(
        (window as unknown as { __wifiViewPwned?: boolean }).__wifiViewPwned,
      ).toBeUndefined()
      assertRemoteStringIsolated(field, hostile)
    })

    it(`Nearby encryption "${hostile}" renders as text, isolated inside a <bdi>, with no other element created`, async () => {
      const { client } = await mountWifi()
      client.settle(
        'get_access_points',
        accessPointsEnvelope([accessPoint({ encryption: hostile })]),
      )
      client.settle('get_handshakes', handshakesListEnvelope([]))
      await settle()
      const field = fieldIn(rows('nearby')[0] as HTMLElement, 'encryption')
      expect(field.textContent).toBe(hostile)
      expect(root().querySelector('script')).toBeNull()
      expect(
        (window as unknown as { __wifiViewPwned?: boolean }).__wifiViewPwned,
      ).toBeUndefined()
      assertRemoteStringIsolated(field, hostile)
    })

    it(`Captured ssid "${hostile}" renders as text, isolated inside a <bdi>, with no other element created`, async () => {
      const { client } = await mountWifi()
      client.settle('get_access_points', accessPointsEnvelope([]))
      client.settle(
        'get_handshakes',
        handshakesListEnvelope([
          handshakeEntry({ ssid: hostile, bssid: null }),
        ]),
      )
      await settle()
      const field = fieldIn(rows('captured')[0] as HTMLElement, 'ssid')
      expect(field.textContent).toBe(hostile)
      expect(root().querySelector('script')).toBeNull()
      expect(
        (window as unknown as { __wifiViewPwned?: boolean }).__wifiViewPwned,
      ).toBeUndefined()
      assertRemoteStringIsolated(field, hostile)
    })

    it(`Captured bssid "${hostile}" renders as text, isolated inside a <bdi>, with no other element created`, async () => {
      const { client } = await mountWifi()
      client.settle('get_access_points', accessPointsEnvelope([]))
      client.settle(
        'get_handshakes',
        handshakesListEnvelope([handshakeEntry({ bssid: hostile })]),
      )
      await settle()
      const field = fieldIn(rows('captured')[0] as HTMLElement, 'bssid')
      expect(field.textContent).toBe(hostile)
      expect(root().querySelector('script')).toBeNull()
      expect(
        (window as unknown as { __wifiViewPwned?: boolean }).__wifiViewPwned,
      ).toBeUndefined()
      assertRemoteStringIsolated(field, hostile)
    })
  }

  it('a hostname of exactly "-" is a real reading and is not reported as empty', async () => {
    const { client } = await mountWifi()
    client.settle(
      'get_access_points',
      accessPointsEnvelope([accessPoint({ hostname: '-' })]),
    )
    client.settle('get_handshakes', handshakesListEnvelope([]))
    await settle()
    const field = fieldIn(rows('nearby')[0] as HTMLElement, 'hostname')
    expect(field.textContent).toBe('-')
    expect(field.getAttribute('data-empty')).not.toBe('true')
  })

  it('an empty hostname (the plugin failing its own fallback-to-MAC guarantee) dashes and is marked empty', async () => {
    const { client } = await mountWifi()
    client.settle(
      'get_access_points',
      accessPointsEnvelope([accessPoint({ hostname: '' })]),
    )
    client.settle('get_handshakes', handshakesListEnvelope([]))
    await settle()
    const row = rows('nearby')[0] as HTMLElement
    // SPEC 4.5.1.1: a dash carries the accessible EMPTY_LABEL alongside it
    // in the same element, so the visible dash is asserted with the label
    // stripped back out (dashboard.spec.ts's own visibleFieldText shape),
    // never against the flattened textContent.
    expect(visibleFieldText(row, 'hostname')).toBe(DASH)
    expect(fieldIn(row, 'hostname').getAttribute('data-empty')).toBe('true')
  })

  it('an empty vendor dashes and is marked empty, the same rule as hostname (SPEC 4.5.1.1 governs every remote string on this row)', async () => {
    const { client } = await mountWifi()
    client.settle(
      'get_access_points',
      accessPointsEnvelope([accessPoint({ vendor: '' })]),
    )
    client.settle('get_handshakes', handshakesListEnvelope([]))
    await settle()
    const row = rows('nearby')[0] as HTMLElement
    expect(visibleFieldText(row, 'vendor')).toBe(DASH)
    expect(fieldIn(row, 'vendor').getAttribute('data-empty')).toBe('true')
  })

  it('an empty encryption dashes and is marked empty, the same rule as hostname', async () => {
    const { client } = await mountWifi()
    client.settle(
      'get_access_points',
      accessPointsEnvelope([accessPoint({ encryption: '' })]),
    )
    client.settle('get_handshakes', handshakesListEnvelope([]))
    await settle()
    const row = rows('nearby')[0] as HTMLElement
    expect(visibleFieldText(row, 'encryption')).toBe(DASH)
    expect(fieldIn(row, 'encryption').getAttribute('data-empty')).toBe('true')
  })

  it('a non-empty vendor and encryption render verbatim and are not marked empty', async () => {
    const { client } = await mountWifi()
    client.settle(
      'get_access_points',
      accessPointsEnvelope([
        accessPoint({ vendor: 'RealVendor', encryption: 'WPA3' }),
      ]),
    )
    client.settle('get_handshakes', handshakesListEnvelope([]))
    await settle()
    const row = rows('nearby')[0] as HTMLElement
    const vendorField = fieldIn(row, 'vendor')
    const encryptionField = fieldIn(row, 'encryption')
    expect(vendorField.textContent).toBe('RealVendor')
    expect(vendorField.getAttribute('data-empty')).not.toBe('true')
    expect(encryptionField.textContent).toBe('WPA3')
    expect(encryptionField.getAttribute('data-empty')).not.toBe('true')
  })

  it('a null Captured bssid (unparsed filename, SPEC 2.7) dashes and is marked empty, never guessed', async () => {
    const { client } = await mountWifi()
    client.settle('get_access_points', accessPointsEnvelope([]))
    client.settle(
      'get_handshakes',
      handshakesListEnvelope([
        handshakeEntry({
          bssid: null,
          ssid: 'a_capture_with_no_parseable_bssid',
        }),
      ]),
    )
    await settle()
    const row = rows('captured')[0] as HTMLElement
    expect(visibleFieldText(row, 'bssid')).toBe(DASH)
    expect(fieldIn(row, 'bssid').getAttribute('data-empty')).toBe('true')
  })

  it('a present Captured bssid renders verbatim and is not marked empty', async () => {
    const { client } = await mountWifi()
    client.settle('get_access_points', accessPointsEnvelope([]))
    client.settle(
      'get_handshakes',
      handshakesListEnvelope([handshakeEntry({ bssid: 'aabbccddeeff' })]),
    )
    await settle()
    const field = fieldIn(rows('captured')[0] as HTMLElement, 'bssid')
    expect(field.textContent).toBe('aabbccddeeff')
    expect(field.getAttribute('data-empty')).not.toBe('true')
  })

  it('a Captured entry with no GPS sidecar dashes its gps field and marks it empty', async () => {
    const { client } = await mountWifi()
    client.settle('get_access_points', accessPointsEnvelope([]))
    client.settle(
      'get_handshakes',
      handshakesListEnvelope([handshakeEntry({ gps: null })]),
    )
    await settle()
    const row = rows('captured')[0] as HTMLElement
    expect(visibleFieldText(row, 'gps')).toBe(DASH)
    expect(fieldIn(row, 'gps').getAttribute('data-empty')).toBe('true')
  })

  it('a Captured entry with a GPS sidecar reads "Pinned" - presence only, never coordinates (SPEC 4.5.2.3)', async () => {
    const { client } = await mountWifi()
    client.settle('get_access_points', accessPointsEnvelope([]))
    client.settle(
      'get_handshakes',
      handshakesListEnvelope([
        handshakeEntry({
          gps: { lat: 10.5, lon: -20.25, accuracy: 12, source: 'gpsd' },
        }),
      ]),
    )
    await settle()
    const field = fieldIn(rows('captured')[0] as HTMLElement, 'gps')
    expect(field.textContent).toBe('Pinned')
    expect(field.getAttribute('data-empty')).not.toBe('true')
  })
})

// =============================================================================
// 6a. Bidi isolation (SPEC 4.5.3, "A string rendered as text can still lie
//     about its neighbours", issue #219): a remote string is rendered inside
//     a <bdi>, which isolates it without touching a single character. The
//     rule is isolation rather than stripping, so the pair that matters is
//     both directions at once - a hostile override character is contained,
//     and a legitimate right-to-left name is untouched.
//
// What DOM shape the isolation takes is not pinned by SPEC beyond "inside a
// <bdi>", so assertRemoteStringIsolated (helpers/remoteText.ts) accepts
// either the field element itself being a <bdi>, or a <bdi> nested inside
// it - deliberately tolerant of either reading of "rendered inside a <bdi>"
// rather than one guessed shape.
// =============================================================================

const BIDI_OVERRIDE = '‮'
// A classic RLO extension-spoof shape: reversed, "TestNet_exe.evael" reads
// as a filename with a swapped extension. The point is not that this is a
// filename here, only that it is exactly the character the isolation rule
// exists for, carried inside an otherwise ordinary-looking SSID.
const HOSTILE_BIDI_SSID = `TestNet_${BIDI_OVERRIDE}lave.exe`
// Synthetic Arabic and Hebrew network names - real right-to-left script,
// not a real SSID - each meaning roughly "test network" in its language.
const RTL_SSID_ARABIC = 'شبكة_اختبار'
const RTL_SSID_HEBREW = 'רשת_בדיקה'

describe('bidi isolation for remote strings (SPEC 4.5.3, issue #219)', () => {
  it('a Nearby hostname carrying U+202E is isolated inside a <bdi>, the character preserved rather than stripped', async () => {
    const { client } = await mountWifi()
    client.settle(
      'get_access_points',
      accessPointsEnvelope([accessPoint({ hostname: HOSTILE_BIDI_SSID })]),
    )
    client.settle('get_handshakes', handshakesListEnvelope([]))
    await settle()
    const field = fieldIn(rows('nearby')[0] as HTMLElement, 'hostname')
    expect(field.textContent).toBe(HOSTILE_BIDI_SSID)
    expect(field.textContent).toContain(BIDI_OVERRIDE)
    assertRemoteStringIsolated(field, HOSTILE_BIDI_SSID)
  })

  // Left uncovered until review found the gap: bssid, encryption and vendor
  // sit right beside hostname on the same Nearby row and are wrapped the
  // same way - a wrapper deleted from any one of the three left this suite
  // green, since only hostname's own wrapper was ever exercised.
  it('a Nearby bssid carrying U+202E is isolated inside a <bdi>, the character preserved rather than stripped', async () => {
    const { client } = await mountWifi()
    client.settle(
      'get_access_points',
      accessPointsEnvelope([accessPoint({ bssid: HOSTILE_BIDI_SSID })]),
    )
    client.settle('get_handshakes', handshakesListEnvelope([]))
    await settle()
    const field = fieldIn(rows('nearby')[0] as HTMLElement, 'bssid')
    expect(field.textContent).toBe(HOSTILE_BIDI_SSID)
    expect(field.textContent).toContain(BIDI_OVERRIDE)
    assertRemoteStringIsolated(field, HOSTILE_BIDI_SSID)
  })

  it('a Nearby encryption carrying U+202E is isolated inside a <bdi>, the character preserved rather than stripped', async () => {
    const { client } = await mountWifi()
    client.settle(
      'get_access_points',
      accessPointsEnvelope([accessPoint({ encryption: HOSTILE_BIDI_SSID })]),
    )
    client.settle('get_handshakes', handshakesListEnvelope([]))
    await settle()
    const field = fieldIn(rows('nearby')[0] as HTMLElement, 'encryption')
    expect(field.textContent).toBe(HOSTILE_BIDI_SSID)
    expect(field.textContent).toContain(BIDI_OVERRIDE)
    assertRemoteStringIsolated(field, HOSTILE_BIDI_SSID)
  })

  it('a Nearby vendor carrying U+202E is isolated inside a <bdi>, the character preserved rather than stripped', async () => {
    const { client } = await mountWifi()
    client.settle(
      'get_access_points',
      accessPointsEnvelope([accessPoint({ vendor: HOSTILE_BIDI_SSID })]),
    )
    client.settle('get_handshakes', handshakesListEnvelope([]))
    await settle()
    const field = fieldIn(rows('nearby')[0] as HTMLElement, 'vendor')
    expect(field.textContent).toBe(HOSTILE_BIDI_SSID)
    expect(field.textContent).toContain(BIDI_OVERRIDE)
    assertRemoteStringIsolated(field, HOSTILE_BIDI_SSID)
  })

  it('a Captured ssid carrying U+202E is isolated inside a <bdi>, the character preserved rather than stripped', async () => {
    const { client } = await mountWifi()
    client.settle('get_access_points', accessPointsEnvelope([]))
    client.settle(
      'get_handshakes',
      handshakesListEnvelope([
        handshakeEntry({ ssid: HOSTILE_BIDI_SSID, bssid: null }),
      ]),
    )
    await settle()
    const field = fieldIn(rows('captured')[0] as HTMLElement, 'ssid')
    expect(field.textContent).toBe(HOSTILE_BIDI_SSID)
    expect(field.textContent).toContain(BIDI_OVERRIDE)
    assertRemoteStringIsolated(field, HOSTILE_BIDI_SSID)
  })

  // The other half of "captured bssid" (the null-filename-parse case is
  // already pinned above, in the empty/dash block): a captures's own bssid
  // is a remote-derived string too - `bssid` in a HandshakeEntry comes from
  // parsing the capture filename (SPEC 2.7), and the isolation rule applies
  // to it the same as any other field on this row.
  it('a Captured bssid carrying U+202E is isolated inside a <bdi>, the character preserved rather than stripped', async () => {
    const { client } = await mountWifi()
    client.settle('get_access_points', accessPointsEnvelope([]))
    client.settle(
      'get_handshakes',
      handshakesListEnvelope([handshakeEntry({ bssid: HOSTILE_BIDI_SSID })]),
    )
    await settle()
    const field = fieldIn(rows('captured')[0] as HTMLElement, 'bssid')
    expect(field.textContent).toBe(HOSTILE_BIDI_SSID)
    expect(field.textContent).toContain(BIDI_OVERRIDE)
    assertRemoteStringIsolated(field, HOSTILE_BIDI_SSID)
  })

  // The distinguishing assertion: isolation is not a filter, so a genuine
  // right-to-left name must render exactly as it arrived, with no character
  // dropped or reordered by this app - "an Arabic or Hebrew SSID is a name,
  // not an attack" (SPEC 4.5.3).
  for (const rtlSsid of [RTL_SSID_ARABIC, RTL_SSID_HEBREW]) {
    it(`a legitimate right-to-left hostname "${rtlSsid}" renders unmangled, not stripped or reordered`, async () => {
      const { client } = await mountWifi()
      client.settle(
        'get_access_points',
        accessPointsEnvelope([accessPoint({ hostname: rtlSsid })]),
      )
      client.settle('get_handshakes', handshakesListEnvelope([]))
      await settle()
      const field = fieldIn(rows('nearby')[0] as HTMLElement, 'hostname')
      expect(field.textContent).toBe(rtlSsid)
    })

    it(`a legitimate right-to-left Captured ssid "${rtlSsid}" renders unmangled, not stripped or reordered`, async () => {
      const { client } = await mountWifi()
      client.settle('get_access_points', accessPointsEnvelope([]))
      client.settle(
        'get_handshakes',
        handshakesListEnvelope([
          handshakeEntry({ ssid: rtlSsid, bssid: null }),
        ]),
      )
      await settle()
      const field = fieldIn(rows('captured')[0] as HTMLElement, 'ssid')
      expect(field.textContent).toBe(rtlSsid)
    })
  }
})

// SPEC 4.5.2.3: "channel, rssi and clients are non-nullable by schema, so a
// dash rule for them would be a branch nothing can reach". These three, and
// Captured's time, are the declared data-field hooks no other test in this
// file reaches - every other one is exercised by the dash tests above or by
// the order/badge/hostile-string tests elsewhere in this file.
describe('the non-nullable Nearby fields render their value with no data-empty, and Captured time matches formatUnitTime', () => {
  it('channel, rssi and clients all render their value and carry no data-empty', async () => {
    const point = accessPoint({ channel: 11, rssi: -42, clients: 7 })
    const { client } = await mountWifi()
    client.settle('get_access_points', accessPointsEnvelope([point]))
    client.settle('get_handshakes', handshakesListEnvelope([]))
    await settle()
    const row = rows('nearby')[0] as HTMLElement

    const channelField = fieldIn(row, 'channel')
    expect(channelField.textContent).toContain('11')
    expect(channelField.getAttribute('data-empty')).not.toBe('true')

    const rssiField = fieldIn(row, 'rssi')
    expect(rssiField.textContent).toContain('-42')
    expect(rssiField.getAttribute('data-empty')).not.toBe('true')

    const clientsField = fieldIn(row, 'clients')
    expect(clientsField.textContent).toContain('7')
    expect(clientsField.getAttribute('data-empty')).not.toBe('true')
  })

  it('channel, rssi and clients at zero still render their value, not a dash for a falsy number', async () => {
    const point = accessPoint({ channel: 0, rssi: 0, clients: 0 })
    const { client } = await mountWifi()
    client.settle('get_access_points', accessPointsEnvelope([point]))
    client.settle('get_handshakes', handshakesListEnvelope([]))
    await settle()
    const row = rows('nearby')[0] as HTMLElement
    expect(fieldIn(row, 'channel').getAttribute('data-empty')).not.toBe('true')
    expect(fieldIn(row, 'rssi').getAttribute('data-empty')).not.toBe('true')
    expect(fieldIn(row, 'clients').getAttribute('data-empty')).not.toBe('true')
  })

  it("Captured's time field carries exactly what formatUnitTime returns for the entry's mtime - format.spec.ts owns the function itself, this owns the wiring", async () => {
    const entry = handshakeEntry({ mtime: T })
    const { client } = await mountWifi()
    client.settle('get_access_points', accessPointsEnvelope([]))
    client.settle('get_handshakes', handshakesListEnvelope([entry]))
    await settle()
    const field = fieldIn(rows('captured')[0] as HTMLElement, 'time')
    expect(field.textContent).toBe(formatUnitTime(entry.mtime))
  })
})

// =============================================================================
// 7. The byte-size formatter, at its boundaries (SPEC 4.5.2.3's wording,
//    asserted through the DOM since the section names no exported function).
// =============================================================================

describe('the Captured size field, at the boundaries SPEC 4.5.2.3 fixes', () => {
  const cases: Array<[number, string]> = [
    [0, '0 B'],
    [1023, '1023 B'],
    [1024, '1.0 kB'],
    // One byte short of a megabyte, and the one boundary where the two
    // possible orderings disagree. 1048575 / 1024 = 1023.999... kB, which
    // rounds to 1024.0 - a reading the table above does not contain, since
    // 1024 kB is what the next unit is for. SPEC 4.5.2.3 settles it: the
    // unit is chosen after rounding, not before, so this renders '1.0 MB'.
    // Both orders round; only one of them prints a number the thresholds
    // say cannot occur, which is what makes this boundary worth pinning
    // regardless of which way it went.
    [1048575, '1.0 MB'],
    [1024 * 1024, '1.0 MB'],
  ]

  for (const [size, expected] of cases) {
    it(`${size} bytes renders as "${expected}"`, async () => {
      const { client } = await mountWifi()
      client.settle('get_access_points', accessPointsEnvelope([]))
      client.settle(
        'get_handshakes',
        handshakesListEnvelope([handshakeEntry({ size })]),
      )
      await settle()
      const field = fieldIn(rows('captured')[0] as HTMLElement, 'size')
      expect(field.textContent).toBe(expected)
    })
  }
})

// =============================================================================
// The refresh label copy.
// =============================================================================

describe("control copy, pinned by equality against SPEC 4.5.2.3's table", () => {
  it('the refresh control reads "Refresh"', async () => {
    await mountWifi()
    expect(refreshControl().textContent).toBe('Refresh')
  })
})
