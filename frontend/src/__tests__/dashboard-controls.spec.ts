import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import setModeSchema from '../../../docs/schemas/incoming/set_mode.json'
import setPasvSchema from '../../../docs/schemas/incoming/set_pasv.json'
import rebootSchema from '../../../docs/schemas/incoming/reboot.json'
import shutdownSchema from '../../../docs/schemas/incoming/shutdown.json'

import type {
  Battery,
  Capabilities,
  Gps,
  Mode,
  OutgoingMessage,
  OutgoingStats,
  Stats,
} from '../lib/protocol'
// SPEC 4.5.2.2's own predicate, imported statically only for the pure,
// identity-independent job of building an `it()` title at collection time
// (before any dynamic import below has run). The value used inside a test
// body comes from the same dynamic `lib/ws` graph as everything else that
// test mounts against -- see the comment on mountDashboard()'s WsModule.
import { canSendCommand } from '../lib/ws'
import type {
  ConnectionState,
  Diagnostics,
  StatsSnapshot,
  UnauthorizedReason,
  WsClient,
  WsClientOptions,
} from '../lib/ws'

// Written from SPEC.md 4.5.2.2 ("Controls: confirm, pending and where a
// refusal appears", issue #184), plus 4.3.3 (queue vs. command, canSendCommand),
// 4.3.4 (nothing optimistic), 4.3.5 (where a refusal surfaces), 4.5.1.1 and
// 4.5.2.1 (the DOM-hook and copy-lives-in-format.ts conventions this section
// inherits), 2.6-2.6.3 (the four commands themselves) and 10.7. Deliberately
// not read from lib/controls.ts or views/Dashboard.svelte, both authored in
// parallel, nor from the parts of lib/format.ts or lib/ws.ts this change
// adds (RemoteError and canSendCommand predate it and are read as the stable
// surface they already are).
//
// Every copy assertion below is a literal transcribed from 4.5.2.2's own
// tables, never a string imported from lib/format.ts: that module is never
// imported by this file. Every element is found through the DOM hooks
// 4.5.2.2 declares (data-control, data-control-state, data-action,
// data-control-message) and nothing else, including the armed-state
// consequence sentence: 4.5.2.2's hook list folds it into
// data-control-message alongside the disabled reason, the failure, the
// abandonment and the pending sentence -- one element, with
// data-control-state saying which kind of sentence it is carrying.
//
// The client is reached through lib/session.ts's currentClient(), per the
// task brief: every test drives it with startSession({ createClient }),
// never with connectStores() directly, since a command has nowhere to go
// without a currentClient() to send it through.

// ---------------------------------------------------------------------------
// Schema conformance, without ajv (not a frontend dependency): the schema
// JSON itself, imported directly (tsconfig's resolveJsonModule), checked for
// additionalProperties, required, and the two enums/booleans this section's
// frames carry.
// ---------------------------------------------------------------------------

interface JsonSchemaLike {
  title: string
  properties: Record<
    string,
    { const?: string; enum?: unknown[]; type?: string }
  >
  required: string[]
}

const SET_MODE_SCHEMA = setModeSchema as JsonSchemaLike
const SET_PASV_SCHEMA = setPasvSchema as JsonSchemaLike
const REBOOT_SCHEMA = rebootSchema as JsonSchemaLike
const SHUTDOWN_SCHEMA = shutdownSchema as JsonSchemaLike

function assertConformsToSchema(
  frame: Record<string, unknown>,
  schema: JsonSchemaLike,
): void {
  const allowedKeys = Object.keys(schema.properties)
  for (const key of Object.keys(frame)) {
    expect(
      allowedKeys,
      `"${key}" is not a property of ${schema.title}`,
    ).toContain(key)
  }
  for (const key of schema.required) {
    expect(
      Object.prototype.hasOwnProperty.call(frame, key),
      `missing required "${key}"`,
    ).toBe(true)
  }
  expect(frame.type).toBe(schema.properties.type?.const)
  if (schema.properties.mode?.enum) {
    expect(schema.properties.mode.enum).toContain(frame.mode)
  }
  if (schema.properties.on) {
    expect(typeof frame.on).toBe('boolean')
  }
}

// ---------------------------------------------------------------------------
// Fixture builders. Realistic shapes, synthetic content only.
// ---------------------------------------------------------------------------

const T = 1_700_000_000

function battery(overrides: Partial<Battery> = {}): Battery {
  return { percent: 80, charging: false, ...overrides }
}

function capabilitiesData(overrides: Partial<Capabilities> = {}): Capabilities {
  return {
    pasv: true,
    pisugar: false,
    gpsSource: 'gpsd',
    pluginVersion: '0.1.0',
    ...overrides,
  }
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

function statsData(overrides: Partial<Stats> = {}): Stats {
  return {
    uptime: 120,
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
    capabilities: capabilitiesData(),
    ...overrides,
  }
}

function statsEnvelope(overrides: Partial<Stats> = {}): OutgoingStats {
  return { type: 'stats', timestamp: T, data: statsData(overrides) }
}

// ---------------------------------------------------------------------------
// A fake localStorage, same shape as session.spec.ts's: lib/settings.ts and
// lib/ws.ts's token helpers both touch window.localStorage, and real storage
// is never touched.
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
// A hand-driven WsClient double that records every command() call instead of
// acting on it, so a test can inspect the exact frame and settle it (resolve
// or reject) on its own schedule -- never a real socket, never a timer.
// ---------------------------------------------------------------------------

interface RecordedCommand {
  type: string
  data: unknown
  resolve: (message: OutgoingMessage) => void
  reject: (error: Error) => void
}

class FakeWsClient implements WsClient {
  currentState: ConnectionState = 'connected'
  reasonValue: UnauthorizedReason | null = null
  private lastStatsValue: StatsSnapshot | null = null
  readonly stateHandlers = new Set<(state: ConnectionState) => void>()
  readonly messageHandlers = new Set<(message: OutgoingMessage) => void>()
  readonly commandCalls: RecordedCommand[] = []

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
  request(): Promise<never> {
    return new Promise(() => {
      // Never settles: no test in this file issues a read.
    })
  }
  command(type: string, data?: unknown): Promise<OutgoingMessage> {
    return new Promise((resolve, reject) => {
      this.commandCalls.push({ type, data, resolve, reject })
    })
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

  /** The command a confirm tap most recently issued. */
  lastCommand(): RecordedCommand {
    return this.commandAt(this.commandCalls.length - 1)
  }

  /**
   * The command recorded at a given position, in call order -- not only the
   * most recent one. Needed for the late-rejection race: a request that a
   * two-`stats`-frame abandonment has already given up on can still have
   * its own command() promise settle afterwards, once a *later* request
   * for the same control is already pending, and the test for that has to
   * reject the first one specifically rather than whichever the fake
   * happens to have recorded last.
   */
  commandAt(index: number): RecordedCommand {
    const entry = this.commandCalls[index]
    expect(entry, `expected a command() call at index ${index}`).toBeDefined()
    return entry as RecordedCommand
  }

  resolveLastCommand(message: OutgoingMessage): void {
    this.lastCommand().resolve(message)
  }

  rejectLastCommand(error: Error): void {
    this.lastCommand().reject(error)
  }
}

function asClient(fake: FakeWsClient): WsClient {
  return fake
}

function frameFromCall(call: RecordedCommand): Record<string, unknown> {
  const frame: Record<string, unknown> = {}
  if (call.data && typeof call.data === 'object')
    Object.assign(frame, call.data)
  frame.type = call.type
  frame.message_id = 'test-message-id'
  return frame
}

// ---------------------------------------------------------------------------
// Mounting: Dashboard.svelte via a real session (startSession), the seam the
// task brief names, so controls.ts's currentClient() resolves to the fake
// above. lib/settings.ts, lib/session.ts, lib/ws.ts and the view are all
// imported together, after vi.resetModules(), so the module singletons never
// diverge.
//
// lib/ws.ts is part of that graph, not merely a types source, and it has to
// be: `RemoteError` is a class, and vi.resetModules() gives every dynamic
// import() after it a fresh module registry, so a fresh `lib/ws.ts` and a
// fresh `RemoteError` class object come out of it. A `RemoteError` built
// from a *static* top-level import is a different object from the one the
// implementation's `instanceof RemoteError` checks against once the module
// has been reset underneath it -- correctly false, and a trap for any future
// test in this file that mounts a component under resetModules and then
// asserts identity against anything imported statically. mountDashboard()
// hands back the dynamically-imported module precisely so a test never has
// that choice to get wrong: `ws.RemoteError`, not the static import.
// canSendCommand is a pure function and its identity never mattered, but
// taking it from the same graph too means there is one rule here, not one
// rule plus an exception for the one function that happens to be safe.
type SvelteApi = typeof import('svelte')
type WsModule = typeof import('../lib/ws')
type SettingsModule = typeof import('../lib/settings')

let svelte: SvelteApi | null = null
let container: HTMLElement | null = null
let instance: Record<string, unknown> | null = null
let stopSession: (() => void) | null = null

beforeEach(() => {
  fakeStorage = new FakeStorage()
  vi.stubGlobal('localStorage', fakeStorage)
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

/**
 * `initialState` is what `client.state()` reads at the moment connectStores
 * seeds `connection` from it (SPEC 4.4.2), i.e. before any test has a chance
 * to call emitState() -- the only way to reach "the state a fresh mount
 * actually starts in" (SPEC 4.5.2.2's "before the first stats frame" is
 * `connecting`), since seeding happens synchronously inside this function,
 * before it returns the client to the caller.
 */
async function mountDashboard(
  initialState: ConnectionState = 'connected',
  // Fires every time startSession() builds a client, not only the first --
  // the host-switch suite below is the one caller that needs to see the
  // second and third, since the `client` this function returns is a
  // snapshot taken once, at mount time, and does not follow a later switch.
  onClientCreated?: (client: FakeWsClient) => void,
): Promise<{ client: FakeWsClient; ws: WsModule; settings: SettingsModule }> {
  svelte = await import('svelte')
  const settings = await import('../lib/settings')
  const session = await import('../lib/session')
  const ws = await import('../lib/ws')
  // Forces the deterministic prefilled-Bluetooth fallback (SPEC 4.7/4.8),
  // the same way session.spec.ts does, rather than depending on jsdom's
  // own default location.
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

  const module = (await import('../views/Dashboard.svelte')) as unknown as {
    default: Parameters<SvelteApi['mount']>[0]
  }
  container = document.createElement('div')
  document.body.appendChild(container)
  instance = svelte.mount(module.default, { target: container }) as Record<
    string,
    unknown
  >
  await settle()
  return { client, ws, settings }
}

async function settle(): Promise<void> {
  svelte?.flushSync()
  await svelte?.tick()
}

// ---------------------------------------------------------------------------
// Queries, on the declared data-* hooks only (SPEC 4.5.2.2's "the DOM
// hooks" list, and 4.5.1.1's reason for declaring them: a test that finds a
// button by walking the markup breaks when the markup is rearranged).
// ---------------------------------------------------------------------------

function root(): HTMLElement {
  if (!container) throw new Error('Dashboard is not mounted')
  return container
}

function controlRootOrNull(name: string): HTMLElement | null {
  return root().querySelector(`[data-control="${name}"]`)
}

function controlRoot(name: string): HTMLElement {
  const found = controlRootOrNull(name)
  expect(found, `expected [data-control="${name}"]`).not.toBeNull()
  return found as HTMLElement
}

function controlState(name: string): string | null {
  return controlRoot(name).getAttribute('data-control-state')
}

function controlButtonOrNull(
  name: string,
  action: 'request' | 'confirm' | 'cancel',
): HTMLElement | null {
  return controlRoot(name).querySelector(`[data-action="${action}"]`)
}

function controlButton(
  name: string,
  action: 'request' | 'confirm' | 'cancel',
): HTMLElement {
  const found = controlButtonOrNull(name, action)
  expect(
    found,
    `expected [data-control="${name}"] [data-action="${action}"]`,
  ).not.toBeNull()
  return found as HTMLElement
}

function controlMessageText(name: string): string {
  const found = controlRoot(name).querySelector('[data-control-message]')
  return (found?.textContent ?? '').trim()
}

function isDisabled(el: HTMLElement): boolean {
  return (el as HTMLButtonElement).disabled === true
}

function click(el: HTMLElement): void {
  el.dispatchEvent(new MouseEvent('click', { bubbles: true }))
}

async function arm(name: string, action: 'request' = 'request'): Promise<void> {
  click(controlButton(name, action))
  await settle()
}

async function confirm(name: string): Promise<void> {
  click(controlButton(name, 'confirm'))
  await settle()
}

// ---------------------------------------------------------------------------
// 1. The mode switch offers the right mode for each stats.mode, null included.
// ---------------------------------------------------------------------------

describe('the mode switch offers the mode that takes the unit somewhere else (4.5.2.2)', () => {
  const cases: Array<{ mode: Mode | null; label: string }> = [
    { mode: 'MANUAL', label: 'Switch to AUTO' },
    { mode: 'AUTO', label: 'Switch to MANU' },
    { mode: 'PASV', label: 'Switch to MANU' },
    { mode: null, label: 'Switch to AUTO' },
  ]

  for (const { mode, label } of cases) {
    it(`offers "${label}" when stats.mode is ${JSON.stringify(mode)}`, async () => {
      const { client } = await mountDashboard()
      client.emitMessage(statsEnvelope({ mode }))
      await settle()

      const request = controlButton('mode', 'request')
      expect((request.textContent ?? '').trim()).toBe(label)
    })
  }

  it('a null mode does not disable the switch: it is the rescue for a unit with no agent (SPEC 4.5.2, issue #147)', async () => {
    const { client } = await mountDashboard()
    client.emitMessage(statsEnvelope({ mode: null }))
    await settle()

    expect(isDisabled(controlButton('mode', 'request'))).toBe(false)
  })
})

// ---------------------------------------------------------------------------
// 2. The PASV control: absent without capabilities.pasv, disabled outside
//    AUTO/PASV, with the shared sentence.
// ---------------------------------------------------------------------------

describe('the PASV control (4.5.2.2)', () => {
  it('is not rendered at all when capabilities.pasv is false', async () => {
    const { client } = await mountDashboard()
    client.emitMessage(
      statsEnvelope({ capabilities: capabilitiesData({ pasv: false }) }),
    )
    await settle()

    expect(controlRootOrNull('pasv')).toBeNull()
  })

  it('is rendered and enabled when the mode is AUTO', async () => {
    const { client } = await mountDashboard()
    client.emitMessage(
      statsEnvelope({
        mode: 'AUTO',
        capabilities: capabilitiesData({ pasv: true }),
      }),
    )
    await settle()

    expect(isDisabled(controlButton('pasv', 'request'))).toBe(false)
  })

  it('is rendered and enabled when the mode is PASV', async () => {
    const { client } = await mountDashboard()
    client.emitMessage(
      statsEnvelope({
        mode: 'PASV',
        capabilities: capabilitiesData({ pasv: true }),
      }),
    )
    await settle()

    expect(isDisabled(controlButton('pasv', 'request'))).toBe(false)
  })

  it('is present but disabled, with the shared sentence, when the mode is MANUAL', async () => {
    const { client } = await mountDashboard()
    client.emitMessage(
      statsEnvelope({
        mode: 'MANUAL',
        capabilities: capabilitiesData({ pasv: true }),
      }),
    )
    await settle()

    expect(isDisabled(controlButton('pasv', 'request'))).toBe(true)
    expect(controlMessageText('pasv')).toBe('PASV is reachable only from AUTO.')
  })

  it('is present but disabled when the mode is null -- the opposite of the mode switch, for the opposite reason', async () => {
    const { client } = await mountDashboard()
    client.emitMessage(
      statsEnvelope({
        mode: null,
        capabilities: capabilitiesData({ pasv: true }),
      }),
    )
    await settle()

    expect(isDisabled(controlButton('pasv', 'request'))).toBe(true)
    expect(controlMessageText('pasv')).toBe('PASV is reachable only from AUTO.')
  })
})

// ---------------------------------------------------------------------------
// Before the first `stats` frame (4.5.2.2): `capabilities` arrives inside
// `stats`, so before the first frame there are none, and the PASV control's
// own rule -- rendered only when capabilities.pasv -- already says what
// happens: absent is not true, so it does not render. The mode switch
// renders and offers AUTO (a null mode does too, per section 1 above).
// Reboot and shutdown render. All four are disabled by no rule of their
// own: the connection is `connecting` before anything has been received,
// and canSendCommand is false there.
// ---------------------------------------------------------------------------

describe('before the first stats frame, the app is honestly connecting (4.5.2.2)', () => {
  it('PASV is absent, the mode switch offers AUTO, and all four controls are disabled because canSendCommand("connecting") is false', async () => {
    const { ws } = await mountDashboard('connecting')
    // No stats frame is ever emitted in this test: that is the point.

    expect(ws.canSendCommand('connecting')).toBe(false)
    expect(controlRootOrNull('pasv')).toBeNull()
    expect((controlButton('mode', 'request').textContent ?? '').trim()).toBe(
      'Switch to AUTO',
    )
    expect(isDisabled(controlButton('mode', 'request'))).toBe(true)
    expect(isDisabled(controlButton('reboot', 'request'))).toBe(true)
    expect(isDisabled(controlButton('shutdown', 'request'))).toBe(true)
  })

  it('the PASV control appearing once the first frame lands is correct, not a flicker to design around', async () => {
    const { client } = await mountDashboard('connecting')
    expect(controlRootOrNull('pasv')).toBeNull()

    client.emitMessage(
      statsEnvelope({
        mode: 'AUTO',
        capabilities: capabilitiesData({ pasv: true }),
      }),
    )
    await settle()

    expect(controlRootOrNull('pasv')).not.toBeNull()
  })
})

// ---------------------------------------------------------------------------
// 3. The two-step confirm, both directions for PASV (closes issue #37).
//
// The armed-state consequence sentence lives in data-control-message, the
// same element as the disabled reason, the failure and the abandonment: one
// element, with data-control-state saying which kind of sentence it is
// carrying at any given moment (4.5.2.2's DOM-hook list is explicit that
// this is closed at five, not four).
// ---------------------------------------------------------------------------

describe('the two-step confirm is inline and armed one at a time (4.5.2.2, issue #37)', () => {
  it('tapping the PASV control arms it without sending anything', async () => {
    const { client } = await mountDashboard()
    client.emitMessage(
      statsEnvelope({
        mode: 'AUTO',
        capabilities: capabilitiesData({ pasv: true }),
      }),
    )
    await settle()

    await arm('pasv')

    expect(controlState('pasv')).toBe('armed')
    expect(client.commandCalls).toHaveLength(0)
    expect((controlButton('pasv', 'confirm').textContent ?? '').trim()).toBe(
      'Confirm entering PASV',
    )
    expect((controlButton('pasv', 'cancel').textContent ?? '').trim()).toBe(
      'Cancel',
    )
    expect(controlMessageText('pasv')).toBe(
      'The unit stops attacking and only listens.',
    )
  })

  it('confirming the armed PASV-off direction shows its own consequence', async () => {
    const { client } = await mountDashboard()
    client.emitMessage(
      statsEnvelope({
        mode: 'PASV',
        capabilities: capabilitiesData({ pasv: true }),
      }),
    )
    await settle()

    await arm('pasv')

    expect((controlButton('pasv', 'confirm').textContent ?? '').trim()).toBe(
      'Confirm leaving PASV',
    )
    expect(controlMessageText('pasv')).toBe(
      'The unit returns to AUTO and resumes attacking.',
    )
  })

  it('focus moves to Cancel, not to Confirm, once a control is armed', async () => {
    const { client } = await mountDashboard()
    client.emitMessage(
      statsEnvelope({
        mode: 'AUTO',
        capabilities: capabilitiesData({ pasv: true }),
      }),
    )
    await settle()

    await arm('pasv')

    expect(document.activeElement).toBe(controlButton('pasv', 'cancel'))
  })

  it('cancelling sends nothing and returns the control to idle', async () => {
    const { client } = await mountDashboard()
    client.emitMessage(
      statsEnvelope({
        mode: 'AUTO',
        capabilities: capabilitiesData({ pasv: true }),
      }),
    )
    await settle()

    await arm('pasv')
    click(controlButton('pasv', 'cancel'))
    await settle()

    expect(controlState('pasv')).toBe('idle')
    expect(client.commandCalls).toHaveLength(0)
  })

  // Both orders, not one: review traced a suspected Svelte 5 flush-order
  // defect specifically in the direction below (arming a lower-index
  // control while a higher-index one is armed) -- both controls' blocks
  // update in the same flush in list order, so arming `mode` (index 0)
  // while `reboot` (index 2) is armed could create mode's Cancel and then
  // destroy reboot's, whose bind:this teardown nulls the shared focus-target
  // slot after the fact. It was not exercised before, so it may not
  // reproduce; this is the assertion that would catch it if it does. The
  // reverse order is not suspected to have the same defect, and is tested
  // too so a regression there is not invisible either.
  it('arming a lower-index control (mode) while a higher-index one (reboot) is armed disarms the first and moves focus to the new one', async () => {
    const { client } = await mountDashboard()
    client.emitMessage(statsEnvelope({ mode: 'AUTO' }))
    await settle()

    await arm('reboot')
    expect(controlState('reboot')).toBe('armed')

    await arm('mode')

    expect(controlState('mode')).toBe('armed')
    expect(controlState('reboot')).toBe('idle')
    expect(client.commandCalls).toHaveLength(0)
    expect(document.activeElement).toBe(controlButton('mode', 'cancel'))
  })

  it('arming a higher-index control (reboot) while a lower-index one (mode) is armed disarms the first and moves focus to the new one', async () => {
    const { client } = await mountDashboard()
    client.emitMessage(statsEnvelope({ mode: 'AUTO' }))
    await settle()

    await arm('mode')
    expect(controlState('mode')).toBe('armed')

    await arm('reboot')

    expect(controlState('reboot')).toBe('armed')
    expect(controlState('mode')).toBe('idle')
    expect(client.commandCalls).toHaveLength(0)
    expect(document.activeElement).toBe(controlButton('reboot', 'cancel'))
  })

  it('arming does not expire: it is still armed after ten minutes with no interaction (no timer owned by the module)', async () => {
    vi.useFakeTimers()
    const { client } = await mountDashboard()
    client.emitMessage(
      statsEnvelope({
        mode: 'AUTO',
        capabilities: capabilitiesData({ pasv: true }),
      }),
    )
    await settle()

    await arm('pasv')
    await vi.advanceTimersByTimeAsync(10 * 60 * 1000)

    expect(controlState('pasv')).toBe('armed')
    expect(client.commandCalls).toHaveLength(0)
  })

  // SPEC 4.5.2.2: arming "is already cleared whenever the control stops
  // being available", and the same section treats `pasv_mode` being
  // unloaded between broadcasts as a real race rather than a contrivance
  // (the same one `pasv_unavailable`'s own test answers a tap with, not by
  // contriving state). This is that race applied to an *armed* control
  // rather than an idle one, and it is the return trip that is the hard
  // part: the control must not simply reappear, it must reappear idle, with
  // no Confirm and no Cancel a tap on Request never asked for.
  it('a capability that disappears while PASV is armed clears the arming, and PASV returns idle -- not armed -- once the capability comes back', async () => {
    const { client } = await mountDashboard()
    client.emitMessage(
      statsEnvelope({
        mode: 'AUTO',
        capabilities: capabilitiesData({ pasv: true }),
      }),
    )
    await settle()

    await arm('pasv')
    expect(controlState('pasv')).toBe('armed')

    client.emitMessage(
      statsEnvelope({
        mode: 'AUTO',
        capabilities: capabilitiesData({ pasv: false }),
      }),
    )
    await settle()
    expect(controlRootOrNull('pasv')).toBeNull()

    client.emitMessage(
      statsEnvelope({
        mode: 'AUTO',
        capabilities: capabilitiesData({ pasv: true }),
      }),
    )
    await settle()

    expect(controlRootOrNull('pasv')).not.toBeNull()
    expect(controlState('pasv')).toBe('idle')
    expect(controlButtonOrNull('pasv', 'confirm')).toBeNull()
    expect(controlButtonOrNull('pasv', 'cancel')).toBeNull()
    expect(controlButtonOrNull('pasv', 'request')).not.toBeNull()
  })
})

// ---------------------------------------------------------------------------
// 4. The exact frame that reaches the wire on confirm, checked against schema.
// ---------------------------------------------------------------------------

describe("the frame a confirm sends matches the wire vocabulary, not stats.mode or restart()'s own (4.5.2.2, 2.6.1)", () => {
  it('set_mode to auto carries lowercase "auto"', async () => {
    const { client } = await mountDashboard()
    client.emitMessage(statsEnvelope({ mode: 'MANUAL' }))
    await settle()
    await arm('mode')
    await confirm('mode')

    const call = client.lastCommand()
    expect(call.type).toBe('set_mode')
    const frame = frameFromCall(call)
    assertConformsToSchema(frame, SET_MODE_SCHEMA)
    expect(frame.mode).toBe('auto')
  })

  it('set_mode to manual carries lowercase "manual", not "MANU" and not "MANUAL"', async () => {
    const { client } = await mountDashboard()
    client.emitMessage(statsEnvelope({ mode: 'AUTO' }))
    await settle()
    await arm('mode')
    await confirm('mode')

    const call = client.lastCommand()
    const frame = frameFromCall(call)
    assertConformsToSchema(frame, SET_MODE_SCHEMA)
    expect(frame.mode).toBe('manual')
  })

  it('set_pasv on carries { on: true }', async () => {
    const { client } = await mountDashboard()
    client.emitMessage(
      statsEnvelope({
        mode: 'AUTO',
        capabilities: capabilitiesData({ pasv: true }),
      }),
    )
    await settle()
    await arm('pasv')
    await confirm('pasv')

    const call = client.lastCommand()
    expect(call.type).toBe('set_pasv')
    const frame = frameFromCall(call)
    assertConformsToSchema(frame, SET_PASV_SCHEMA)
    expect(frame.on).toBe(true)
  })

  it('set_pasv off carries { on: false }', async () => {
    const { client } = await mountDashboard()
    client.emitMessage(
      statsEnvelope({
        mode: 'PASV',
        capabilities: capabilitiesData({ pasv: true }),
      }),
    )
    await settle()
    await arm('pasv')
    await confirm('pasv')

    const call = client.lastCommand()
    const frame = frameFromCall(call)
    assertConformsToSchema(frame, SET_PASV_SCHEMA)
    expect(frame.on).toBe(false)
  })

  it('reboot carries no payload beyond the envelope', async () => {
    const { client } = await mountDashboard()
    client.emitMessage(statsEnvelope())
    await settle()
    await arm('reboot')
    await confirm('reboot')

    const call = client.lastCommand()
    expect(call.type).toBe('reboot')
    assertConformsToSchema(frameFromCall(call), REBOOT_SCHEMA)
  })

  it('shutdown carries no payload beyond the envelope', async () => {
    const { client } = await mountDashboard()
    client.emitMessage(statsEnvelope())
    await settle()
    await arm('shutdown')
    await confirm('shutdown')

    const call = client.lastCommand()
    expect(call.type).toBe('shutdown')
    assertConformsToSchema(frameFromCall(call), SHUTDOWN_SCHEMA)
  })
})

// ---------------------------------------------------------------------------
// 5. The five pending-resolution rows (SPEC 4.5.2.2 merged `set_mode`'s two
//    into one), driven by pushing the named observation through the stores
//    -- never a timer.
// ---------------------------------------------------------------------------

describe('pending resolves only on the observation the table names (4.5.2.2)', () => {
  // Revised from an earlier version of this table (and of this file) that
  // gave `set_mode auto` its own row, waiting for `stats.mode` to become
  // `AUTO` or `PASV`. SPEC 4.5.2.2 now rejects that: after the restart the
  // socket returns and the mode is null for the whole of §2.6.0's startup
  // window, which is not a fixed few seconds, so the two-frame abandonment
  // below would fire inside it and report "The unit did not confirm the
  // change" over a rescue that had in fact worked. No observation the unit
  // sends can tell a successful mode change apart from one that failed or
  // never started, in either direction, so there is no test here that
  // asserts a mode change *succeeded* -- only that nothing except
  // `restarting` ever ends it, whichever direction was requested.
  const modeDirections: Array<{ target: 'auto' | 'manual'; startMode: Mode }> =
    [
      { target: 'auto', startMode: 'MANUAL' },
      { target: 'manual', startMode: 'AUTO' },
    ]

  for (const { target, startMode } of modeDirections) {
    it(`set_mode ${target}: no stats frame resolves it -- only the connection state becoming restarting does`, async () => {
      const { client } = await mountDashboard()
      client.emitMessage(statsEnvelope({ mode: startMode }))
      await settle()
      await arm('mode')
      await confirm('mode')

      expect(controlState('mode')).toBe('pending')
      expect(controlMessageText('mode')).toBe('Restarting.')

      // One frame that could plausibly be mistaken for confirmation: null is
      // what a unit going manual reports throughout its whole boot window
      // (SPEC 2.6.0, F31), and AUTO is what the reverted rule would have
      // accepted for the other direction. Neither resolves it. Only one is
      // sent here, not two: the "two stats frames without confirmation"
      // backstop below applies to this row exactly as it does to
      // reboot/shutdown now that both set_mode directions wait on
      // `restarting`, and a second one here would abandon the request
      // before the test gets to assert what it came to assert.
      client.emitMessage(
        statsEnvelope({ mode: target === 'auto' ? 'AUTO' : null }),
      )
      await settle()
      expect(controlState('mode')).toBe('pending')

      // SPEC 4.5.2.2: every row waiting on `restarting` waits on the state,
      // not its reason (`ConnectionView` does not expose one, issue #131),
      // so this resolves the request regardless of what the plugin actually
      // broadcast the reason as -- this fake never carries one either,
      // which is the honest reflection of that gap rather than a shortcut
      // around it.
      client.emitState('restarting')
      await settle()
      expect(controlState('mode')).toBe('idle')
    })
  }

  it('set_pasv on: stays pending while stats.mode is still AUTO, resolves on stats.mode PASV', async () => {
    const { client } = await mountDashboard()
    client.emitMessage(
      statsEnvelope({
        mode: 'AUTO',
        capabilities: capabilitiesData({ pasv: true }),
      }),
    )
    await settle()
    await arm('pasv')
    await confirm('pasv')

    expect(controlState('pasv')).toBe('pending')
    expect(controlMessageText('pasv')).toBe('Waiting for the unit to confirm.')

    client.emitMessage(
      statsEnvelope({
        mode: 'AUTO',
        capabilities: capabilitiesData({ pasv: true }),
      }),
    )
    await settle()
    expect(controlState('pasv')).toBe('pending')

    client.emitMessage(
      statsEnvelope({
        mode: 'PASV',
        capabilities: capabilitiesData({ pasv: true }),
      }),
    )
    await settle()
    expect(controlState('pasv')).toBe('idle')
  })

  it('set_pasv off: stays pending while stats.mode is still PASV, resolves on stats.mode AUTO', async () => {
    const { client } = await mountDashboard()
    client.emitMessage(
      statsEnvelope({
        mode: 'PASV',
        capabilities: capabilitiesData({ pasv: true }),
      }),
    )
    await settle()
    await arm('pasv')
    await confirm('pasv')

    expect(controlState('pasv')).toBe('pending')

    client.emitMessage(
      statsEnvelope({
        mode: 'PASV',
        capabilities: capabilitiesData({ pasv: true }),
      }),
    )
    await settle()
    expect(controlState('pasv')).toBe('pending')

    client.emitMessage(
      statsEnvelope({
        mode: 'AUTO',
        capabilities: capabilitiesData({ pasv: true }),
      }),
    )
    await settle()
    expect(controlState('pasv')).toBe('idle')
  })

  it('reboot: pending until the connection state becomes restarting', async () => {
    const { client } = await mountDashboard()
    client.emitMessage(statsEnvelope())
    await settle()
    await arm('reboot')
    await confirm('reboot')

    expect(controlState('reboot')).toBe('pending')
    expect(controlMessageText('reboot')).toBe('Rebooting.')

    client.emitMessage(statsEnvelope())
    await settle()
    expect(controlState('reboot')).toBe('pending')

    client.emitState('restarting')
    await settle()
    expect(controlState('reboot')).toBe('idle')
  })

  it('shutdown: pending until the connection state becomes restarting', async () => {
    const { client } = await mountDashboard()
    client.emitMessage(statsEnvelope())
    await settle()
    await arm('shutdown')
    await confirm('shutdown')

    expect(controlState('shutdown')).toBe('pending')
    expect(controlMessageText('shutdown')).toBe('Shutting down.')

    client.emitState('restarting')
    await settle()
    expect(controlState('shutdown')).toBe('idle')
  })
})

// ---------------------------------------------------------------------------
// 6. The two other ways a pending request ends: a rejected command, and two
//    stats frames without the confirmation (one is not enough).
// ---------------------------------------------------------------------------

describe('a pending request also ends on rejection or on two unconfirming stats frames (4.5.2.2)', () => {
  it('a rejected command with any code besides the two named ones shows the generic sentence', async () => {
    const { client, ws } = await mountDashboard()
    client.emitMessage(statsEnvelope())
    await settle()
    await arm('reboot')
    await confirm('reboot')

    client.rejectLastCommand(
      new ws.RemoteError('the unit said no', 'internal_error'),
    )
    await settle()

    expect(controlState('reboot')).toBe('idle')
    expect(controlMessageText('reboot')).toBe(
      'The unit did not accept the command.',
    )
  })

  it('a rejection carrying no code at all (a plain Error) also shows the generic sentence', async () => {
    const { client } = await mountDashboard()
    client.emitMessage(statsEnvelope())
    await settle()
    await arm('shutdown')
    await confirm('shutdown')

    client.rejectLastCommand(new Error('command timed out'))
    await settle()

    expect(controlMessageText('shutdown')).toBe(
      'The unit did not accept the command.',
    )
  })

  it('pasv_requires_auto reaches the PASV control and nowhere else -- the race SPEC 4.5.2.2 names explicitly', async () => {
    const { client, ws } = await mountDashboard()
    // The control was available when tapped (AUTO); the unit answers as
    // though it had since left AUTO, the race the section itself describes
    // rather than a state the test contrives.
    client.emitMessage(
      statsEnvelope({
        mode: 'AUTO',
        capabilities: capabilitiesData({ pasv: true }),
      }),
    )
    await settle()
    await arm('pasv')
    await confirm('pasv')

    client.rejectLastCommand(
      new ws.RemoteError('PASV requires auto', 'pasv_requires_auto'),
    )
    await settle()

    expect(controlMessageText('pasv')).toBe('PASV is reachable only from AUTO.')
    for (const other of ['mode', 'reboot', 'shutdown']) {
      expect(controlMessageText(other)).not.toBe(
        'PASV is reachable only from AUTO.',
      )
    }
  })

  it('pasv_unavailable reaches the PASV control and nowhere else', async () => {
    const { client, ws } = await mountDashboard()
    client.emitMessage(
      statsEnvelope({
        mode: 'AUTO',
        capabilities: capabilitiesData({ pasv: true }),
      }),
    )
    await settle()
    await arm('pasv')
    await confirm('pasv')

    client.rejectLastCommand(
      new ws.RemoteError('pasv_mode is not loaded', 'pasv_unavailable'),
    )
    await settle()

    expect(controlMessageText('pasv')).toBe(
      'The unit does not have the PASV plugin.',
    )
    for (const other of ['mode', 'reboot', 'shutdown']) {
      expect(controlMessageText(other)).not.toBe(
        'The unit does not have the PASV plugin.',
      )
    }
  })

  // SPEC 4.5.2.2: "A sentence only explains the control it is about." Both
  // PASV sentences are scoped to the PASV control specifically, not merely
  // to some control that happens to be pending: the code is chosen by
  // whatever answered, so a `reboot` (or any other non-PASV control)
  // rejected with either PASV code must not borrow PASV's explanation for
  // its own failure. The earlier version of the condition table did not
  // scope `pasv_unavailable` at all, and this is the case that was wrong --
  // a `reboot` answered with it would have shown "The unit does not have
  // the PASV plugin" beside the Reboot button.
  it.each(['pasv_requires_auto', 'pasv_unavailable'] as const)(
    'a non-PASV control rejected with %s shows the generic sentence, not the PASV one',
    async (code) => {
      const { client, ws } = await mountDashboard()
      client.emitMessage(statsEnvelope())
      await settle()
      await arm('reboot')
      await confirm('reboot')

      client.rejectLastCommand(new ws.RemoteError('rejected', code))
      await settle()

      expect(controlMessageText('reboot')).toBe(
        'The unit did not accept the command.',
      )
    },
  )

  it('an attacker-chosen code gets the generic sentence, and the code itself never appears in the rendered markup (4.5.3)', async () => {
    const { client, ws } = await mountDashboard()
    client.emitMessage(statsEnvelope())
    await settle()
    await arm('reboot')
    await confirm('reboot')

    const hostileCode = '<script>alert(document.cookie)</script>'
    client.rejectLastCommand(new ws.RemoteError('rejected', hostileCode))
    await settle()

    expect(controlMessageText('reboot')).toBe(
      'The unit did not accept the command.',
    )
    expect(root().innerHTML).not.toContain('<script>')
    expect(root().textContent ?? '').not.toContain(hostileCode)
  })

  it('one stats frame without the confirmation is not enough; the second one abandons the request', async () => {
    const { client } = await mountDashboard()
    client.emitMessage(statsEnvelope())
    await settle()
    await arm('reboot')
    await confirm('reboot')

    client.emitMessage(statsEnvelope())
    await settle()
    expect(controlState('reboot')).toBe('pending')
    expect(controlMessageText('reboot')).toBe('Rebooting.')

    client.emitMessage(statsEnvelope())
    await settle()
    expect(controlState('reboot')).toBe('idle')
    expect(controlMessageText('reboot')).toBe(
      'The unit did not confirm the change.',
    )
  })
})

// ---------------------------------------------------------------------------
// Message precedence (4.5.2.2): "pending sentence, then the static disabled
// reason where it applies, then the last message." Three rules, none of
// them the pre-existing behaviour: a failure/abandonment message now
// survives the control being disabled by a drop; the static PASV reason is
// now withheld while the app could not itself confirm it; and where both
// could apply, the static reason wins.
// ---------------------------------------------------------------------------

describe('message precedence: pending, then the static disabled reason, then the last message (4.5.2.2)', () => {
  it('a failure message survives the control being disabled by a disconnect -- going offline does not erase what the unit already said', async () => {
    const { client, ws } = await mountDashboard()
    client.emitMessage(statsEnvelope())
    await settle()
    await arm('reboot')
    await confirm('reboot')

    client.rejectLastCommand(
      new ws.RemoteError('the unit said no', 'internal_error'),
    )
    await settle()
    expect(controlMessageText('reboot')).toBe(
      'The unit did not accept the command.',
    )

    client.emitState('offline')
    await settle()

    expect(isDisabled(controlButton('reboot', 'request'))).toBe(true)
    expect(controlMessageText('reboot')).toBe(
      'The unit did not accept the command.',
    )
  })

  it('an abandonment message survives the control being disabled by a disconnect', async () => {
    const { client } = await mountDashboard()
    client.emitMessage(statsEnvelope())
    await settle()
    await arm('shutdown')
    await confirm('shutdown')

    client.emitMessage(statsEnvelope())
    await settle()
    client.emitMessage(statsEnvelope())
    await settle()
    expect(controlMessageText('shutdown')).toBe(
      'The unit did not confirm the change.',
    )

    client.emitState('offline')
    await settle()

    expect(isDisabled(controlButton('shutdown', 'request'))).toBe(true)
    expect(controlMessageText('shutdown')).toBe(
      'The unit did not confirm the change.',
    )
  })

  it('the static PASV disabled reason is shown only while the app could send the command, and disappears once it could not', async () => {
    const { client } = await mountDashboard()
    client.emitMessage(
      statsEnvelope({
        mode: 'MANUAL',
        capabilities: capabilitiesData({ pasv: true }),
      }),
    )
    await settle()

    expect(controlMessageText('pasv')).toBe('PASV is reachable only from AUTO.')

    // The claim is about the unit's *current* mode; once the socket is
    // down, "the unit's current mode" is not a fact this app can still
    // vouch for -- it is whatever the last broadcast said, which SPEC
    // 4.5.2.2 says is a different fact. The control stays disabled (now for
    // connectivity too), but the sentence about the mode must not.
    client.emitState('offline')
    await settle()

    expect(isDisabled(controlButton('pasv', 'request'))).toBe(true)
    expect(controlMessageText('pasv')).toBe('')
  })

  it('the static disabled reason takes precedence over a stale last message once both could apply', async () => {
    const { client, ws } = await mountDashboard()
    client.emitMessage(
      statsEnvelope({
        mode: 'AUTO',
        capabilities: capabilitiesData({ pasv: true }),
      }),
    )
    await settle()
    await arm('pasv')
    await confirm('pasv')

    client.rejectLastCommand(
      new ws.RemoteError('the unit said no', 'internal_error'),
    )
    await settle()
    expect(controlMessageText('pasv')).toBe(
      'The unit did not accept the command.',
    )

    // The mode has since left AUTO -- still connected, still capable of
    // PASV per capabilities, but the wrong mode for it. The static reason
    // is not merely present alongside the old failure text, it replaces it.
    client.emitMessage(
      statsEnvelope({
        mode: 'MANUAL',
        capabilities: capabilitiesData({ pasv: true }),
      }),
    )
    await settle()

    expect(controlMessageText('pasv')).toBe('PASV is reachable only from AUTO.')
  })

  it('the pending sentence takes precedence over the static disabled reason while a request is in flight', async () => {
    const { client } = await mountDashboard()
    client.emitMessage(
      statsEnvelope({
        mode: 'AUTO',
        capabilities: capabilitiesData({ pasv: true }),
      }),
    )
    await settle()
    await arm('pasv')
    await confirm('pasv')
    expect(controlMessageText('pasv')).toBe('Waiting for the unit to confirm.')

    // A frame that would make the static reason apply if it were being
    // read at all -- MANUAL is neither AUTO nor PASV -- arrives while the
    // request is still in flight. It counts toward the two-frame
    // abandonment backstop (this is the one frame that backstop tolerates),
    // but it must not make the disabled-reason sentence pre-empt the
    // pending one.
    client.emitMessage(
      statsEnvelope({
        mode: 'MANUAL',
        capabilities: capabilitiesData({ pasv: true }),
      }),
    )
    await settle()

    expect(controlState('pasv')).toBe('pending')
    expect(controlMessageText('pasv')).toBe('Waiting for the unit to confirm.')
  })
})

// ---------------------------------------------------------------------------
// A rejection belongs to the request that sent it, not to whichever request
// of the same control kind happens to be pending when it arrives. Reachable
// in practice, not hypothetical: command() times out after 15s
// (REQUEST_TIMEOUT_MS in lib/ws.ts) while the two-`stats`-frame abandonment
// above fires after two keepalive_intervals, whose floor is 5s (SPEC 2.4) --
// comfortably inside 15s, so a request the app has already given up on can
// still have its own command() promise settle afterwards, once a second
// request for the same control is already pending. The fix is a per-request
// token rather than a comparison by control kind, so this test expresses
// "this rejection belongs to that request" through which command it rejects,
// not through anything about how the correlation is implemented.
// ---------------------------------------------------------------------------

describe('a rejection is matched to the request that sent it, not to whichever request of the same control is pending now (4.5.2.2)', () => {
  it('a late rejection for an abandoned reboot does not touch a second, later reboot that is genuinely pending', async () => {
    const { client, ws } = await mountDashboard()
    client.emitMessage(statsEnvelope())
    await settle()

    await arm('reboot')
    await confirm('reboot')
    expect(client.commandCalls).toHaveLength(1)
    const firstRequest = client.commandAt(0)

    // Two stats frames without the confirmation abandon this first request
    // (SPEC 4.5.2.2): its own command() promise is still unsettled at this
    // point -- nothing has resolved or rejected it -- exactly the state the
    // 15s-vs-10s race leaves it in on a real connection.
    client.emitMessage(statsEnvelope())
    await settle()
    client.emitMessage(statsEnvelope())
    await settle()
    expect(controlState('reboot')).toBe('idle')
    expect(controlMessageText('reboot')).toBe(
      'The unit did not confirm the change.',
    )

    // A second reboot, requested after the first was given up on.
    await arm('reboot')
    await confirm('reboot')
    expect(client.commandCalls).toHaveLength(2)
    expect(controlState('reboot')).toBe('pending')
    expect(controlMessageText('reboot')).toBe('Rebooting.')

    // The first request's own command() promise settles late, as a
    // rejection -- the defect this test exists to catch matched a rejection
    // to whatever was pending for the control it named, which was by then
    // the second, unrelated reboot.
    firstRequest.reject(
      new ws.RemoteError('command timed out', 'internal_error'),
    )
    await settle()

    expect(controlState('reboot')).toBe('pending')
    expect(controlMessageText('reboot')).toBe('Rebooting.')
  })

  // The same race, across the boundary lib/session.ts's host switch owns
  // (SPEC 4.4.2/4.8) rather than within one client's lifetime. `controls.ts`
  // deliberately does not reset its confirm-token counter on that switch --
  // only the stores get torn down and rebuilt -- precisely so a token A
  // handed out can never collide with one B hands out later. A version that
  // moved the counter's reset into the module's own reset alongside the
  // stores would make unit B's first request reuse unit A's still-open
  // token, and A's late rejection would then match B's pending request by
  // the same mistaken identity the single-client test above rules out.
  it("a late rejection from unit A's abandoned-by-the-switch request does not touch unit B's own pending request", async () => {
    const created: FakeWsClient[] = []
    const { settings, ws } = await mountDashboard('connected', (client) =>
      created.push(client),
    )

    const clientA = created[0] as FakeWsClient
    clientA.emitMessage(statsEnvelope({ mode: 'AUTO' }))
    await settle()

    await arm('reboot')
    await confirm('reboot')
    expect(clientA.commandCalls).toHaveLength(1)
    const staleRequest = clientA.commandAt(0)

    const hostB = settings.addHost({
      label: 'Unit B',
      address: '172.20.10.12',
      wsPort: 8082,
      httpPort: 8443,
      token: null,
    })
    settings.activateHost(hostB.id)
    await settle()

    const clientB = created[1] as FakeWsClient
    clientB.emitMessage(statsEnvelope({ mode: 'AUTO' }))
    await settle()

    await arm('reboot')
    await confirm('reboot')
    expect(clientB.commandCalls).toHaveLength(1)
    expect(controlState('reboot')).toBe('pending')
    expect(controlMessageText('reboot')).toBe('Rebooting.')

    // A's own request was never resolved, rejected or abandoned by the
    // switch itself -- lib/session.ts tears the client down and resets the
    // stores, but the promise command() returned to A's confirm tap is
    // still sitting there, unsettled, and can still reject late.
    staleRequest.reject(
      new ws.RemoteError('command timed out', 'internal_error'),
    )
    await settle()

    expect(controlState('reboot')).toBe('pending')
    expect(controlMessageText('reboot')).toBe('Rebooting.')
  })
})

// ---------------------------------------------------------------------------
// 7. At most one pending request at a time; the other controls are disabled.
// ---------------------------------------------------------------------------

describe('at most one pending request at a time (4.5.2.2)', () => {
  it('the other three controls are disabled while one is pending', async () => {
    const { client } = await mountDashboard()
    client.emitMessage(
      statsEnvelope({
        mode: 'AUTO',
        capabilities: capabilitiesData({ pasv: true }),
      }),
    )
    await settle()
    await arm('reboot')
    await confirm('reboot')

    expect(controlState('reboot')).toBe('pending')
    expect(isDisabled(controlButton('mode', 'request'))).toBe(true)
    expect(isDisabled(controlButton('pasv', 'request'))).toBe(true)
    expect(isDisabled(controlButton('shutdown', 'request'))).toBe(true)
  })
})

// ---------------------------------------------------------------------------
// Whether a command may be sent at all is lib/ws.ts's canSendCommand,
// asked rather than restated (4.5.2.2's closing rule).
// ---------------------------------------------------------------------------

describe('enabling a control asks canSendCommand rather than restating its list (4.5.2.2, 4.3.3)', () => {
  const states: ConnectionState[] = [
    'connecting',
    'connected',
    'degraded',
    'offline',
    'unauthorized',
    'restarting',
  ]

  // One fresh mount per state, not a single mount cycled through all six:
  // 'unauthorized' clears every store (SPEC 4.4.2), which would otherwise
  // taint whatever this test still expects to find rendered for a later
  // state in the same loop.
  for (const state of states) {
    it(`the request button ${canSendCommand(state) ? 'is enabled' : 'is disabled'} in ${state}, matching canSendCommand`, async () => {
      const { client, ws } = await mountDashboard()
      client.emitMessage(statsEnvelope())
      await settle()

      client.emitState(state)
      await settle()

      // ws.canSendCommand, not the statically-imported one above: the same
      // dynamic lib/ws graph this mount's client and RemoteError come from
      // (see the comment on mountDashboard()). Identity never mattered for
      // a pure function, but taking it from here too means there is one
      // rule for "read from the same graph you mounted against", not a rule
      // plus a carved-out exception for the one symbol that happened to be
      // safe to get wrong.
      expect(isDisabled(controlButton('reboot', 'request'))).toBe(
        !ws.canSendCommand(state),
      )
    })
  }

  it('a control disabled only because the app is not connected says nothing of its own (the banner already does)', async () => {
    const { client } = await mountDashboard()
    client.emitMessage(statsEnvelope())
    await settle()

    client.emitState('offline')
    await settle()

    expect(isDisabled(controlButton('reboot', 'request'))).toBe(true)
    expect(controlMessageText('reboot')).toBe('')
  })
})

// ---------------------------------------------------------------------------
// A control does not survive a switch to a different unit (SPEC 4.4.2, 4.8).
// lib/session.ts tears the previous client down, calls resetStores(), and
// only then attaches the next one -- so a control's own local state (armed,
// pending, its message) has to come from the fresh stores that reconciliation
// produces, not persist across the swap by accident. Driven through
// lib/settings.ts's own addHost()/activateHost(), the pattern session.spec.ts
// uses, rather than poking activeHostId directly.
// ---------------------------------------------------------------------------

describe('a control does not survive a switch to a different unit (4.5.2.2, 4.4.2, 4.8)', () => {
  it('a pending request on unit A leaves neither its pending state nor its message once unit B becomes active', async () => {
    const created: FakeWsClient[] = []
    const { settings } = await mountDashboard('connected', (client) =>
      created.push(client),
    )

    expect(created).toHaveLength(1)
    const clientA = created[0] as FakeWsClient
    clientA.emitMessage(statsEnvelope({ mode: 'AUTO' }))
    await settle()

    await arm('reboot')
    await confirm('reboot')
    expect(controlState('reboot')).toBe('pending')
    expect(controlMessageText('reboot')).toBe('Rebooting.')
    expect(clientA.commandCalls).toHaveLength(1)

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
    clientB.emitMessage(statsEnvelope({ mode: 'AUTO' }))
    await settle()

    // Neither the pending state nor the message A's rejection or A's
    // pending tap would have left behind belongs to B, which has never
    // been asked for anything.
    expect(controlState('reboot')).toBe('idle')
    expect(controlMessageText('reboot')).toBe('')
    expect(clientB.commandCalls).toHaveLength(0)
  })

  it('an armed control on unit A is not armed on unit B, and B accepts its own tap normally', async () => {
    const created: FakeWsClient[] = []
    const { settings } = await mountDashboard('connected', (client) =>
      created.push(client),
    )

    const clientA = created[0] as FakeWsClient
    clientA.emitMessage(statsEnvelope({ mode: 'AUTO' }))
    await settle()

    await arm('mode')
    expect(controlState('mode')).toBe('armed')

    const hostB = settings.addHost({
      label: 'Unit B',
      address: '172.20.10.10',
      wsPort: 8082,
      httpPort: 8443,
      token: null,
    })
    settings.activateHost(hostB.id)
    await settle()

    const clientB = created[1] as FakeWsClient
    clientB.emitMessage(statsEnvelope({ mode: 'AUTO' }))
    await settle()

    expect(controlState('mode')).toBe('idle')

    // B's own controls work normally: the reconciliation left them idle,
    // not stuck.
    await arm('mode')
    await confirm('mode')
    expect(clientB.commandCalls).toHaveLength(1)
    expect(clientA.commandCalls).toHaveLength(0)
  })
})
