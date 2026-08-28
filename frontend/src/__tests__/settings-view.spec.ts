import { get } from 'svelte/store'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { Capabilities, OutgoingMessage, OutgoingStats, Stats } from '../lib/protocol'
import type { ConnectionState, UnauthorizedReason, WsClient } from '../lib/ws'

// Written wholesale from SPEC.md 4.5.2.1 ("Settings presentation and DOM
// hooks", issue #134), the Settings bullet of 4.5.2, 4.5.1's ports-are-
// diagnostics and IPv4-only rules, 4.7 (lib/settings.ts's own contract), and
// 4.5.3/4.5.1.1's remote-string rule as it applies to the two remote
// strings this screen renders (pluginVersion, unauthorizedReason).
//
// Deliberately not read from views/Settings.svelte. The previous version of
// this file was written after that file had already been opened, guessed at
// hooks SPEC.md did not yet pin, and is replaced wholesale rather than
// amended: nothing in it carries any authority here, including the fixtures,
// which were rebuilt independently below (dashboard.spec.ts's shapes were
// used as the reference for what a conformant stats/capabilities payload
// looks like, not the old settings-view.spec.ts).
//
// The DOM hooks below are exactly SPEC.md 4.5.2.1's list and nothing else:
// data-view="settings"; data-host-id / data-host-active on a row;
// data-host-field ("label" | "address" | "wsPort" | "httpPort" | "token") on
// a row, distinct from data-add-field ("label" | "address" | "token") on the
// add-host form's own inputs, which the form carries rather than
// data-host-field precisely so a test never has to tell the two apart by
// position; data-action ("activate" | "remove" | "edit" | "save" | "cancel"
// | "add" | "reveal", the last unmasking one token field at a time, in a row
// or in the add form); data-field-error="address"; data-field
// ("connectionState" | "pluginVersion" | "unauthorizedReason"), reusing
// 4.5.1.1's data-empty convention except on unauthorizedReason, which
// SPEC.md says carries none.

// ---------------------------------------------------------------------------
// A fake localStorage: lib/settings.ts is reached through the real view
// import below, and real storage must never be touched by a test.
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

// ---------------------------------------------------------------------------
// Mounting, the same way dashboard.spec.ts mounts a view: no props, via
// svelte.mount, torn down between tests. lib/settings.ts and lib/stores.ts
// are imported in the same call as the view so the module singletons
// (settings, activeHost, the store writables) never diverge from what the
// mounted view itself sees.
// ---------------------------------------------------------------------------

type SvelteApi = typeof import('svelte')
type SettingsApi = typeof import('../lib/settings')
type StoresApi = typeof import('../lib/stores')

let svelte: SvelteApi | null = null
let container: HTMLElement | null = null
let instance: Record<string, unknown> | null = null
let teardownStores: (() => void) | null = null

async function mountSettings(): Promise<{ root: HTMLElement; settings: SettingsApi; stores: StoresApi }> {
  svelte = await import('svelte')
  const settingsApi = await import('../lib/settings')
  const storesApi = await import('../lib/stores')
  const module = (await import('../views/Settings.svelte')) as unknown as {
    default: Parameters<SvelteApi['mount']>[0]
  }
  container = document.createElement('div')
  document.body.appendChild(container)
  instance = svelte.mount(module.default, { target: container }) as Record<string, unknown>
  await settle()
  return { root: container, settings: settingsApi, stores: storesApi }
}

async function settle(): Promise<void> {
  svelte?.flushSync()
  await svelte?.tick()
}

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
})

function root(): HTMLElement {
  if (!container) throw new Error('Settings is not mounted')
  return container
}

function hostRow(id: string): HTMLElement {
  const found = root().querySelector(`[data-host-id="${id}"]`)
  expect(found, `expected a [data-host-id="${id}"] row`).not.toBeNull()
  return found as HTMLElement
}

function hostRowOrNull(id: string): HTMLElement | null {
  return root().querySelector(`[data-host-id="${id}"]`)
}

function hostField(id: string, name: string): HTMLElement {
  const row = hostRow(id)
  const found = row.querySelector(`[data-host-field="${name}"]`)
  expect(found, `expected [data-host-field="${name}"] inside host ${id}`).not.toBeNull()
  return found as HTMLElement
}

function hostAction(id: string, action: string): HTMLElement {
  const row = hostRow(id)
  const found = row.querySelector(`[data-action="${action}"]`)
  expect(found, `expected [data-action="${action}"] inside host ${id}`).not.toBeNull()
  return found as HTMLElement
}

/**
 * An input in the always-visible add-host form (SPEC 4.5.2.1: `data-add-field`,
 * over `label`, `address` and `token` -- distinct from a row's `data-host-field`
 * so a test never has to tell the two apart by position, the defect the
 * previous version of this helper worked around).
 */
function addField(name: string): HTMLInputElement {
  const found = root().querySelector(`[data-add-field="${name}"]`)
  expect(found, `expected [data-add-field="${name}"] in the add-host form`).not.toBeNull()
  return found as HTMLInputElement
}

function addFormAddressInput(): HTMLInputElement {
  return addField('address')
}

function addButton(): HTMLElement {
  const found = root().querySelector('[data-action="add"]')
  expect(found, 'expected an [data-action="add"] control').not.toBeNull()
  return found as HTMLElement
}

/** The reveal control belonging to the add-host form's token field, not to any row's. */
function addFormReveal(): HTMLElement {
  const candidates = [...root().querySelectorAll('[data-action="reveal"]')].filter(
    (el) => el.closest('[data-host-id]') === null,
  )
  expect(candidates, 'expected exactly one reveal control outside any row').toHaveLength(1)
  return candidates[0] as HTMLElement
}

function click(el: HTMLElement): void {
  el.dispatchEvent(new MouseEvent('click', { bubbles: true }))
}

function typeInto(el: HTMLElement, value: string): void {
  const input = el as HTMLInputElement
  input.value = value
  input.dispatchEvent(new Event('input', { bubbles: true }))
}

function diagnosticField(name: string): HTMLElement {
  const found = root().querySelector(`[data-field="${name}"]`)
  expect(found, `expected a [data-field="${name}"] diagnostic`).not.toBeNull()
  return found as HTMLElement
}

function diagnosticFieldOrNull(name: string): HTMLElement | null {
  return root().querySelector(`[data-field="${name}"]`)
}

// ---------------------------------------------------------------------------
// A hand-driven WsClient double, the same shape dashboard.spec.ts uses, for
// the diagnostics block.
// ---------------------------------------------------------------------------

class FakeWsClient {
  currentState: ConnectionState = 'connected'
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
    return new Promise(() => {})
  }
  command(): Promise<OutgoingMessage> {
    return Promise.reject(new Error('not used in these tests'))
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

function capabilitiesData(overrides: Partial<Capabilities> = {}): Capabilities {
  return { pasv: true, pisugar: false, gpsSource: 'gpsd', pluginVersion: '0.1.0', ...overrides }
}

function statsEnvelope(capabilities: Capabilities): OutgoingStats {
  return {
    type: 'stats',
    timestamp: 1_700_000_000,
    data: {
      uptime: 1,
      mode: 'AUTO',
      channel: 6,
      battery: { percent: 90, charging: false },
      temperature: 40,
      handshakes: 0,
      handshakesTotal: 0,
      peers: 0,
      accessPoints: 0,
      lastHandshake: null,
      lastPeer: null,
      gps: {
        enabled: false,
        source: null,
        piFix: false,
        fix: false,
        lat: null,
        lon: null,
        altitude: null,
        accuracy: null,
        updated: null,
      },
      sessionAge: 1,
      capabilities,
    },
  }
}

// ---------------------------------------------------------------------------
// data-view, SPEC 4.5.
// ---------------------------------------------------------------------------

describe('the view root', () => {
  it('carries data-view="settings"', async () => {
    const { settings: api } = await mountSettings()
    api.loadSettings(() => 'not-an-ip-address')
    await settle()

    expect(root().querySelector('[data-view="settings"]')).not.toBeNull()
  })
})

// ---------------------------------------------------------------------------
// "The host list comes first and the add-host form sits under it." (4.5.2.1)
// ---------------------------------------------------------------------------

describe('the host list comes before the add-host form (SPEC 4.5.2.1)', () => {
  it('places the first host row earlier in the document than the add-host form', async () => {
    const { settings: api } = await mountSettings()
    api.loadSettings(() => 'not-an-ip-address')
    await settle()

    const firstRow = hostRow('bluetooth')
    const button = addButton()
    const relative = firstRow.compareDocumentPosition(button)
    expect(
      relative & Node.DOCUMENT_POSITION_FOLLOWING,
      'expected the add-host form (its [data-action="add"] control) to follow the host list in document order',
    ).not.toBe(0)
  })
})

// ---------------------------------------------------------------------------
// "The add-host form is always visible, with no toggle." (SPEC 4.5.2.1)
// ---------------------------------------------------------------------------

describe('the add-host form is always visible, with no toggle (SPEC 4.5.2.1)', () => {
  it('shows the address input and the add control at mount, before any interaction', async () => {
    const { settings: api } = await mountSettings()
    api.loadSettings(() => 'not-an-ip-address')
    await settle()

    const input = addFormAddressInput()
    expect(input.hidden).toBe(false)
    expect(addButton().hasAttribute('hidden')).toBe(false)
  })

  it('stays visible even when the host list is empty, the reachable state SPEC 4.7 names', async () => {
    const { settings: api } = await mountSettings()
    api.loadSettings(() => 'not-an-ip-address')
    for (const host of get(api.settings).hosts) api.removeHost(host.id)
    await settle()

    expect(get(api.settings).hosts).toHaveLength(0)
    const input = addFormAddressInput()
    expect(input.hidden).toBe(false)
    expect(addButton().hasAttribute('hidden')).toBe(false)
  })
})

// ---------------------------------------------------------------------------
// Host list: add, edit, remove, activate (SPEC 4.5.2's Settings bullet).
// ---------------------------------------------------------------------------

describe('the host list: add, edit, remove, activate (SPEC 4.5.2)', () => {
  it('shows the two prefilled hosts on a fresh install, Bluetooth active, via data-host-active', async () => {
    const { settings: api } = await mountSettings()
    api.loadSettings(() => 'not-an-ip-address')
    await settle()

    expect(hostField('bluetooth', 'address').textContent).toContain('172.20.10.2')
    expect(hostField('usb', 'address').textContent).toContain('10.0.0.2')
    expect(hostRow('bluetooth').getAttribute('data-host-active')).toBe('true')
    expect(hostRow('usb').getAttribute('data-host-active')).not.toBe('true')
  })

  it('activates a host from the list, through [data-action="activate"], without touching localStorage by hand', async () => {
    const { settings: api } = await mountSettings()
    api.loadSettings(() => 'not-an-ip-address')
    await settle()

    click(hostAction('usb', 'activate'))
    await settle()

    expect(get(api.activeHost)?.id).toBe('usb')
    expect(hostRow('usb').getAttribute('data-host-active')).toBe('true')
    expect(hostRow('bluetooth').getAttribute('data-host-active')).not.toBe('true')
  })

  it('removes a host from the list, through [data-action="remove"]', async () => {
    const { settings: api } = await mountSettings()
    api.loadSettings(() => 'not-an-ip-address')
    api.addHost({ label: 'Extra unit', address: '172.20.10.5', wsPort: 8082, httpPort: 8443, token: null })
    await settle()

    const extra = get(api.settings).hosts.find((h) => h.label === 'Extra unit')
    expect(extra).toBeDefined()
    click(hostAction(extra!.id, 'remove'))
    await settle()

    expect(get(api.settings).hosts.some((h) => h.id === extra!.id)).toBe(false)
    expect(hostRowOrNull(extra!.id)).toBeNull()
  })

  it('adds a host through the form, with a valid IPv4 address, through [data-action="add"]', async () => {
    const { settings: api } = await mountSettings()
    api.loadSettings(() => 'not-an-ip-address')
    await settle()

    typeInto(addFormAddressInput(), '172.20.10.40')
    click(addButton())
    await settle()

    const added = get(api.settings).hosts.find((h) => h.address === '172.20.10.40')
    expect(added, 'the new host must appear in the settings store').toBeDefined()
    expect(hostRowOrNull(added!.id)).not.toBeNull()
  })

  it('edits a host through [data-action="edit"] and [data-action="save"], and persists the change', async () => {
    const { settings: api } = await mountSettings()
    api.loadSettings(() => 'not-an-ip-address')
    await settle()

    click(hostAction('bluetooth', 'edit'))
    await settle()

    const labelField = hostField('bluetooth', 'label')
    expect(labelField.tagName, 'expected the label field to become an input while editing').toBe('INPUT')
    typeInto(labelField, 'My Bluetooth Unit')
    click(hostAction('bluetooth', 'save'))
    await settle()

    expect(get(api.settings).hosts.find((h) => h.id === 'bluetooth')?.label).toBe('My Bluetooth Unit')
  })

  it('discards an in-progress edit through [data-action="cancel"]', async () => {
    const { settings: api } = await mountSettings()
    api.loadSettings(() => 'not-an-ip-address')
    await settle()

    const before = get(api.settings).hosts.find((h) => h.id === 'bluetooth')?.label
    click(hostAction('bluetooth', 'edit'))
    await settle()
    typeInto(hostField('bluetooth', 'label'), 'Discarded label')
    click(hostAction('bluetooth', 'cancel'))
    await settle()

    expect(get(api.settings).hosts.find((h) => h.id === 'bluetooth')?.label).toBe(before)
  })

  it('ports are shown but never editable (SPEC 4.5.1: "ports are diagnostics, not settings")', async () => {
    const { settings: api } = await mountSettings()
    api.loadSettings(() => 'not-an-ip-address')
    await settle()

    for (const [port, expected] of [
      ['wsPort', String(api.DEFAULT_WS_PORT)],
      ['httpPort', String(api.DEFAULT_HTTP_PORT)],
    ] as const) {
      const el = hostField('bluetooth', port)
      if (el.tagName === 'INPUT') {
        const input = el as HTMLInputElement
        expect(input.disabled || input.readOnly, `${port} input must be disabled or readOnly`).toBe(true)
      }
      expect(el.textContent ?? (el as HTMLInputElement).value).toContain(expected)
    }

    // Also true while a row is in its "edit" state: ports never become
    // editable there either, since editing them means the plugin restarts
    // and drops the socket carrying the confirmation (SPEC 4.5.1). Same
    // shape as the block above and for the same reason: the `if` guard
    // alone only fires on a mutant that turns the span into an input, so
    // there must also be an unconditional assertion that runs against the
    // shipped (non-input) markup, or this loop pins nothing about what
    // actually renders while editing.
    click(hostAction('bluetooth', 'edit'))
    await settle()
    for (const [port, expected] of [
      ['wsPort', String(api.DEFAULT_WS_PORT)],
      ['httpPort', String(api.DEFAULT_HTTP_PORT)],
    ] as const) {
      const el = hostField('bluetooth', port)
      if (el.tagName === 'INPUT') {
        const input = el as HTMLInputElement
        expect(input.disabled || input.readOnly, `${port} input must stay disabled or readOnly while editing`).toBe(
          true,
        )
      }
      expect(
        el.textContent ?? (el as HTMLInputElement).value,
        `${port} must still show the negotiated value while editing`,
      ).toContain(expected)
    }
  })
})

// ---------------------------------------------------------------------------
// SPEC 4.5.2.1: "Any constant the library owns is imported, never restated
// ... a change to the library's defaults would have left the screen
// quietly creating hosts on the old ports." Driven from lib/settings.ts's
// own exported DEFAULT_WS_PORT/DEFAULT_HTTP_PORT rather than from 8082 and
// 8443 written out again here, so this test cannot become the third copy
// of the number it exists to guard.
// ---------------------------------------------------------------------------

describe("the view carries no port literals of its own (SPEC 4.5.2.1)", () => {
  it("a host added through the form comes out at the library's exported port defaults", async () => {
    const { settings: api } = await mountSettings()
    api.loadSettings(() => 'not-an-ip-address')
    await settle()

    typeInto(addFormAddressInput(), '172.20.10.12')
    click(addButton())
    await settle()

    const added = get(api.settings).hosts.find((h) => h.address === '172.20.10.12')
    expect(added, 'the new host must appear in the settings store').toBeDefined()
    expect(added?.wsPort).toBe(api.DEFAULT_WS_PORT)
    expect(added?.httpPort).toBe(api.DEFAULT_HTTP_PORT)
  })
})

// ---------------------------------------------------------------------------
// "A token is edited in the row, beside the host it belongs to." (4.5.2.1)
// No edit mode gates it: unlike label/address, it is directly editable.
// ---------------------------------------------------------------------------

describe('a token is edited directly in the row, with no edit mode gating it (SPEC 4.5.2.1)', () => {
  it('writes to the per-host record, and to companion.token when that host is active, without clicking edit first', async () => {
    const { settings: api } = await mountSettings()
    const result = api.loadSettings(() => 'not-an-ip-address')
    api.activateHost(result.hosts[0]!.id)
    await settle()

    const activeId = get(api.activeHost)?.id
    expect(activeId).toBeDefined()

    // No [data-action="edit"] click: the token field must already be a
    // usable input at mount, per 4.5.2.1's "no grammar to check ... putting
    // it behind an edit mode separates it from the one fact that gives it
    // meaning".
    const tokenInput = hostField(activeId!, 'token')
    expect(tokenInput.tagName, 'the token field must be directly editable').toBe('INPUT')
    typeInto(tokenInput, 'a-fresh-token')
    tokenInput.dispatchEvent(new Event('change', { bubbles: true }))
    tokenInput.dispatchEvent(new FocusEvent('blur', { bubbles: true }))
    await settle()

    const record = get(api.settings).hosts.find((h) => h.id === activeId)
    expect(record?.token, 'the per-host record is the source of truth').toBe('a-fresh-token')
    expect(fakeStorage.getItem(TOKEN_KEY), 'companion.token is the copy the client reads').toBe('a-fresh-token')
  })
})

// ---------------------------------------------------------------------------
// SPEC 4.5.2.1: "It is masked, with a control to reveal it ... The add-host
// form's token is masked on the same terms ... One field is revealed at a
// time, across the whole screen. Revealing a token re-masks whatever was
// showing before it." Discovered through the DOM this file is written
// against (an <input type="password"> toggled to type="text">), not by
// reading views/Settings.svelte: the masking mechanism itself is not
// pinned by SPEC.md, only its observable effect, so this asserts the input
// is not readable as plain text by default and becomes so only once
// [data-action="reveal"] is used, without assuming *how*.
// ---------------------------------------------------------------------------

function isMasked(input: HTMLInputElement): boolean {
  // The one thing SPEC.md pins is the observable effect: not readable from
  // the DOM as plain text until revealed. type="password" is how a browser
  // (and jsdom) keeps a field's value out of the rendered text while still
  // letting the input carry it, which is the shape this suite found; a
  // mutant that flips this attribute is exactly what "make the reveal
  // control a no-op" targets.
  return input.type === 'password'
}

// A password input's current value is never part of an element's
// serialized markup, in any browser or in jsdom: the "value" IDL property
// (what the input actually holds and what `reveal` toggles the rendering
// of) is distinct from the "value" content attribute (what `innerHTML`
// reads back), and setting the former does not touch the latter -- this is
// standard DOM behaviour and true for a type="text" input exactly as much
// as for type="password". Verified empirically against the mounted view
// rather than assumed. So `[data-action="reveal"]` toggling `type` alone
// already guarantees the token is absent from `container.innerHTML` in
// both states; what this checks is the regression the type toggle alone
// cannot catch, named explicitly by the review: a `title`, `aria-label`,
// `placeholder` or stray text node carrying the token in parallel with a
// correctly-toggling `type`, which would leave every test above green
// while putting the secret back on screen by a different road.
function tokenLeaksIntoMarkup(token: string): boolean {
  return root().innerHTML.includes(token)
}

describe('the token is masked, with a reveal control (SPEC 4.5.2.1)', () => {
  it("is masked in a row until [data-action=\"reveal\"] is used there, then reads as plain text", async () => {
    const { settings: api } = await mountSettings()
    api.loadSettings(() => 'not-an-ip-address')
    const fixtureToken = 'row-reveal-fixture-value'
    api.updateHost('bluetooth', { token: fixtureToken })
    await settle()

    const tokenInput = hostField('bluetooth', 'token') as HTMLInputElement
    expect(isMasked(tokenInput), 'the token must be masked by default').toBe(true)
    expect(tokenLeaksIntoMarkup(fixtureToken), 'the token must not appear in the rendered markup while masked').toBe(
      false,
    )
    // The value is still there, carried in the property the browser reads
    // to render the field, just not exposed as text or as a stray
    // attribute -- masking hides it visually, it does not discard it.
    expect(tokenInput.value).toBe(fixtureToken)

    click(hostAction('bluetooth', 'reveal'))
    await settle()

    const revealedInput = hostField('bluetooth', 'token') as HTMLInputElement
    expect(isMasked(revealedInput), 'revealing must unmask it').toBe(false)
    expect(revealedInput.value, 'revealing must not have altered or discarded the value').toBe(fixtureToken)
    // Still true after revealing: the field being type="text" now is what
    // makes the value visible to a person looking at the screen, but that
    // visibility is the browser rendering the input widget, not the value
    // becoming part of the DOM's serialized markup -- so this assertion,
    // like the masked one above, is the guard against a *different* leak
    // (title/aria-label/placeholder/text node) than the one `isMasked`
    // already covers, not a claim that revealing changes what
    // `innerHTML` contains.
    expect(tokenLeaksIntoMarkup(fixtureToken), 'revealing must not spill the token into markup either').toBe(false)
  })

  it('is masked in the add-host form on the same terms, until its own reveal control is used', async () => {
    const { settings: api } = await mountSettings()
    api.loadSettings(() => 'not-an-ip-address')
    await settle()

    const fixtureToken = 'add-form-reveal-fixture-value'
    const addToken = addField('token')
    typeInto(addToken, fixtureToken)
    await settle()

    expect(isMasked(addField('token')), 'the add-host form token must be masked by default too').toBe(true)
    expect(tokenLeaksIntoMarkup(fixtureToken), 'a token typed into the add form must not leak into markup while masked').toBe(
      false,
    )

    click(addFormReveal())
    await settle()

    const revealed = addField('token')
    expect(isMasked(revealed), 'revealing the add-host form field must unmask it').toBe(false)
    expect(revealed.value).toBe(fixtureToken)
    expect(tokenLeaksIntoMarkup(fixtureToken), 'revealing must not spill the token into markup either').toBe(false)
  })

  // "One field is revealed at a time, across the whole screen. Revealing a
  // token re-masks whatever was showing before it." Both orders are worth
  // an assertion: a shared flag cleared in only one direction would still
  // pass a test that only reveals row-then-form.
  it('revealing a second field re-masks whichever was showing, in both orders', async () => {
    const { settings: api } = await mountSettings()
    api.loadSettings(() => 'not-an-ip-address')
    await settle()

    // Row, then the add form: the row must re-mask.
    click(hostAction('bluetooth', 'reveal'))
    await settle()
    expect(isMasked(hostField('bluetooth', 'token') as HTMLInputElement)).toBe(false)

    click(addFormReveal())
    await settle()
    expect(isMasked(addField('token')), 'the add-host form field must now be revealed').toBe(false)
    expect(
      isMasked(hostField('bluetooth', 'token') as HTMLInputElement),
      "revealing the add-host form's token must re-mask the row's",
    ).toBe(true)

    // The add form, then a row: the add form must re-mask.
    click(hostAction('usb', 'reveal'))
    await settle()
    expect(isMasked(hostField('usb', 'token') as HTMLInputElement), 'the usb row must now be revealed').toBe(false)
    expect(
      isMasked(addField('token')),
      "revealing a row's token must re-mask the add-host form's",
    ).toBe(true)
  })
})

// ---------------------------------------------------------------------------
// SPEC 4.7 / 4.5.2.1: "An address is validated in the form, with the message
// next to the field and role="alert" on it, and the mutator's throw is
// caught as the backstop it is meant to be."
// ---------------------------------------------------------------------------

describe('a bad address typed into the add-host form (SPEC 4.5.2.1, 4.7)', () => {
  it('is refused, with role="alert" on the message beside the field, and never reaches the user as a thrown error', async () => {
    const { settings: api } = await mountSettings()
    api.loadSettings(() => 'not-an-ip-address')
    await settle()
    const before = get(api.settings).hosts.length

    typeInto(addFormAddressInput(), 'not-an-ip-address')

    expect(() => click(addButton())).not.toThrow()
    await settle()

    expect(get(api.settings).hosts.length, 'no host is added for a refused address').toBe(before)
    const error = root().querySelector('[data-field-error="address"]')
    expect(error, 'expected an error message next to the address field').not.toBeNull()
    expect((error?.textContent ?? '').length).toBeGreaterThan(0)
    expect(error?.getAttribute('role')).toBe('alert')
  })

})

// ---------------------------------------------------------------------------
// SPEC 4.5.2.1 (amended): "The address grammar has one expression, and the
// form asks for it ... A test cannot reach a backstop that nothing can
// trip, so what it pins instead is that the form and the mutator refuse the
// same set - driven from one table, so a value added to it is asserted on
// both sides at once." lib/settings.ts now exports isUsableAddress for
// exactly this. Includes the two named-but-well-formed refusals SPEC 4.7
// names by hand (the wildcard and the limited broadcast address) and the
// leading-zero spellings SPEC 4.7 explains at length: an octal-looking
// octet new URL would silently reinterpret, and one whose leading zero
// makes new URL throw instead.
// ---------------------------------------------------------------------------

const ADDRESS_CASES: Array<[label: string, address: string, usable: boolean]> = [
  ['an ordinary IPv4 literal', '172.20.10.9', true],
  ['the highest legal octets', '255.255.255.254', true],
  ['the wildcard address, refused by name (SPEC 4.7)', '0.0.0.0', false],
  ['the limited broadcast address, refused by name (SPEC 4.7)', '255.255.255.255', false],
  ['an octal-looking octet new URL would silently reinterpret (SPEC 4.7)', '010.0.0.2', false],
  ['a leading zero on a digit that is not a valid octal one either, where new URL would throw (SPEC 4.7)', '08.0.0.2', false],
  ['a malformed literal', 'not-an-ip-address', false],
  ['an out-of-range octet', '256.0.0.1', false],
  ['an IPv6 literal', '[fe80::1]', false],
]

describe('the form and the mutator refuse the same set (SPEC 4.5.2.1, 4.7)', () => {
  it.each(ADDRESS_CASES)('%s (%s): isUsableAddress, addHost and the form agree (usable=%s)', async (_label, address, usable) => {
    const { settings: api } = await mountSettings()
    api.loadSettings(() => 'not-an-ip-address')
    await settle()

    // 1. The predicate itself, exported from lib/settings.ts.
    expect(api.isUsableAddress(address)).toBe(usable)

    // 2. The mutator: addHost throws iff the predicate refuses the address
    // (SPEC 4.7's "a mutator throws on a bad address").
    const beforeMutator = get(api.settings).hosts.length
    const hostArgs = { label: 'probe', address, wsPort: 8082, httpPort: 8443, token: null }
    if (usable) {
      expect(() => api.addHost(hostArgs)).not.toThrow()
      expect(get(api.settings).hosts.length).toBe(beforeMutator + 1)
    } else {
      expect(() => api.addHost(hostArgs)).toThrow()
      expect(get(api.settings).hosts.length).toBe(beforeMutator)
    }
    // Undo whatever the mutator did, so the form assertion below starts
    // clean regardless of which branch ran.
    const mutatorAdded = get(api.settings).hosts.find((h) => h.address === address)
    if (mutatorAdded) api.removeHost(mutatorAdded.id)
    await settle()

    // 3. The form: the same address, through the DOM, never a thrown error
    // reaching the user either way (SPEC 4.5.2.1).
    const beforeForm = get(api.settings).hosts.length
    typeInto(addFormAddressInput(), address)
    expect(() => click(addButton())).not.toThrow()
    await settle()

    if (usable) {
      expect(get(api.settings).hosts.some((h) => h.address === address)).toBe(true)
      expect(get(api.settings).hosts.length).toBe(beforeForm + 1)
      expect(root().querySelector('[data-field-error="address"]')).toBeNull()
    } else {
      expect(get(api.settings).hosts.length, `${address} must not be added through the form`).toBe(beforeForm)
      const error = root().querySelector('[data-field-error="address"]')
      expect(error, `expected an error for ${address}`).not.toBeNull()
      expect(error?.getAttribute('role')).toBe('alert')
    }
  })
})

// ---------------------------------------------------------------------------
// SPEC 4.7: "the note that the USB path is desktop-only ... is rendered by
// the Settings screen and is not part of the default label."
// ---------------------------------------------------------------------------

describe('the USB desktop-only note (SPEC 4.7)', () => {
  it('is rendered on screen, and the USB host\'s persisted label stays exactly "USB"', async () => {
    const { settings: api } = await mountSettings()
    api.loadSettings(() => 'not-an-ip-address')
    await settle()

    const usbHost = get(api.settings).hosts.find((h) => h.id === 'usb')
    expect(usbHost?.label).toBe('USB')

    const usbRow = hostRow('usb')
    expect(
      /desktop[- ]only|cannot use|an iphone/i.test(usbRow.textContent ?? ''),
      'expected the desktop-only note somewhere in the USB row',
    ).toBe(true)

    // The persisted label must not be where the warning lives: SPEC 4.7
    // says plainly that a warning in the label is one the owner can delete
    // by renaming the entry.
    expect(hostField('usb', 'label').textContent).not.toMatch(/desktop[- ]only|cannot use/i)
  })
})

// ---------------------------------------------------------------------------
// SPEC 4.7: "The list is for a client served by the unit it talks to ...
// Settings should say so where the second host is added."
// ---------------------------------------------------------------------------

describe('the CSP note (SPEC 4.7)', () => {
  it('names the Content-Security-Policy restriction somewhere in the screen once a second host exists', async () => {
    // AMBIGUITY, see report: SPEC 4.7 says the note appears "where the
    // second host is added", which could mean conditionally (only once
    // hosts.length > 1) or simply positioned near the add-host form as
    // static copy. The two prefilled hosts already make hosts.length === 2
    // on a fresh install, so this test only asserts the note is present
    // once that ordinary two-host state is reached; it does not assert
    // absence with a single host, since SPEC.md does not pin that either
    // way.
    const { settings: api } = await mountSettings()
    api.loadSettings(() => 'not-an-ip-address')
    await settle()

    expect(get(api.settings).hosts.length).toBeGreaterThan(1)
    expect(
      /content[- ]security[- ]policy|\bcsp\b/i.test(root().textContent ?? ''),
      'expected a note about the Content-Security-Policy restriction somewhere in the screen',
    ).toBe(true)
  })
})

// ---------------------------------------------------------------------------
// Diagnostics (SPEC 4.5.2.1): render what exists, name what does not.
// ---------------------------------------------------------------------------

describe('diagnostics (SPEC 4.5.2.1)', () => {
  it('renders connectionState as a real, non-empty value even before any client attaches', async () => {
    // No exact wording is pinned for this diagnostic (unlike the Dashboard
    // banner's table, SPEC 4.5.1.1): the view is free to present the state
    // as friendly copy rather than the raw enum, and the shipped build does
    // ("Offline" rather than "offline"). Assert presence and non-emptiness
    // rather than a specific string.
    const { settings: api } = await mountSettings()
    api.loadSettings(() => 'not-an-ip-address')
    await settle()

    const el = diagnosticField('connectionState')
    expect((el.textContent ?? '').trim().length).toBeGreaterThan(0)
    expect(el.getAttribute('data-empty')).not.toBe('true')
  })

  it('dashes pluginVersion, with data-empty, before the first stats frame', async () => {
    const client = new FakeWsClient()
    const { settings: api, stores } = await mountSettings()
    api.loadSettings(() => 'not-an-ip-address')
    teardownStores = stores.connectStores(asClient(client))
    await settle()

    const el = diagnosticField('pluginVersion')
    expect(el.getAttribute('data-empty')).toBe('true')
  })

  it('renders the negotiated plugin version once a stats frame carries it', async () => {
    const client = new FakeWsClient()
    const { settings: api, stores } = await mountSettings()
    api.loadSettings(() => 'not-an-ip-address')
    teardownStores = stores.connectStores(asClient(client))
    client.emitMessage(statsEnvelope(capabilitiesData({ pluginVersion: '1.4.2' })))
    await settle()

    const el = diagnosticField('pluginVersion')
    expect(el.textContent).toBe('1.4.2')
    expect(el.getAttribute('data-empty')).not.toBe('true')
  })

  it('reflects the live connection state as it changes: the text differs between two distinct states', async () => {
    const client = new FakeWsClient()
    const { settings: api, stores } = await mountSettings()
    api.loadSettings(() => 'not-an-ip-address')
    teardownStores = stores.connectStores(asClient(client))
    client.emitState('connected')
    await settle()
    const connectedText = diagnosticField('connectionState').textContent

    client.emitState('degraded')
    await settle()
    const degradedText = diagnosticField('connectionState').textContent

    expect(degradedText).not.toBe(connectedText)
  })

  // "the last error and the latency have no surface at all ... a test that
  // a view has not invented one is a test worth having."
  it('has no lastError or latency surface (issue #176 has not landed)', async () => {
    const { settings: api } = await mountSettings()
    api.loadSettings(() => 'not-an-ip-address')
    await settle()

    expect(diagnosticFieldOrNull('lastError')).toBeNull()
    expect(diagnosticFieldOrNull('latency')).toBeNull()
  })

  describe('unauthorizedReason', () => {
    it('is absent while the state is not unauthorized', async () => {
      const client = new FakeWsClient()
      const { settings: api, stores } = await mountSettings()
      api.loadSettings(() => 'not-an-ip-address')
      teardownStores = stores.connectStores(asClient(client))
      client.emitState('connected')
      await settle()

      expect(diagnosticFieldOrNull('unauthorizedReason')).toBeNull()
    })

    it('appears only while the state is unauthorized, and disappears again once it changes', async () => {
      const client = new FakeWsClient()
      const { settings: api, stores } = await mountSettings()
      api.loadSettings(() => 'not-an-ip-address')
      teardownStores = stores.connectStores(asClient(client))
      client.emitState('unauthorized', 'rejected')
      await settle()

      expect(diagnosticFieldOrNull('unauthorizedReason')).not.toBeNull()

      client.emitState('connected')
      await settle()
      expect(diagnosticFieldOrNull('unauthorizedReason')).toBeNull()
    })

    // "It is also the one field that carries no data-empty: the row exists
    // only when there is a reason, so the two can never both be true, and a
    // branch that cannot be reached is worse than absent."
    it('carries no data-empty attribute at all while it is rendered', async () => {
      const client = new FakeWsClient()
      const { settings: api, stores } = await mountSettings()
      api.loadSettings(() => 'not-an-ip-address')
      teardownStores = stores.connectStores(asClient(client))
      client.emitState('unauthorized', 'required')
      await settle()

      const el = diagnosticField('unauthorizedReason')
      expect(el.hasAttribute('data-empty')).toBe(false)
    })

    // "Diagnostics render what exists ... labelling the authentication
    // reason as 'the last error' says nothing happened when a TLS failure
    // or a refused connection is exactly what did." The row must not be
    // presented under that label.
    it('is never labelled "the last error"', async () => {
      const client = new FakeWsClient()
      const { settings: api, stores } = await mountSettings()
      api.loadSettings(() => 'not-an-ip-address')
      teardownStores = stores.connectStores(asClient(client))
      client.emitState('unauthorized', 'rejected')
      await settle()

      const el = diagnosticField('unauthorizedReason')
      const label = `${el.getAttribute('aria-label') ?? ''} ${el.closest('[data-field]')?.previousElementSibling?.textContent ?? ''}`
      expect(/last error/i.test(label)).toBe(false)
      expect(/last error/i.test(root().textContent ?? '')).toBe(false)
    })
  })
})

// ---------------------------------------------------------------------------
// SPEC 4.5.3, applied to this screen's two remote strings: pluginVersion
// and unauthorizedReason both come off the wire (4.5.2.1's own closing
// line) and must render as text, never as markup, regardless of what
// TypeScript's UnauthorizedReason union claims the wire will only ever send.
// ---------------------------------------------------------------------------

describe('hostile remote strings render as text (SPEC 4.5.3)', () => {
  const HOSTILE_SCRIPT = '<script>window.__pwned_settings = true</script>'

  afterEach(() => {
    delete (window as unknown as { __pwned_settings?: boolean }).__pwned_settings
  })

  it('renders a hostile plugin version verbatim, with no element created from it', async () => {
    const client = new FakeWsClient()
    const { settings: api, stores } = await mountSettings()
    api.loadSettings(() => 'not-an-ip-address')
    teardownStores = stores.connectStores(asClient(client))
    client.emitMessage(statsEnvelope(capabilitiesData({ pluginVersion: HOSTILE_SCRIPT })))
    await settle()

    const el = diagnosticField('pluginVersion')
    expect(el.textContent).toBe(HOSTILE_SCRIPT)
    expect(el.children.length).toBe(0)
    expect(root().querySelector('script')).toBeNull()
    expect((window as unknown as { __pwned_settings?: boolean }).__pwned_settings).not.toBe(true)
  })

  it('never turns a hostile unauthorizedReason into markup, whether reflected verbatim or mapped to fixed copy', async () => {
    // UnauthorizedReason is typed as 'rejected' | 'required', but the value
    // reaches the view off the wire through WsClient.unauthorizedReason(),
    // which nothing in this test file's reach enforces at runtime -- the
    // same cast dashboard.spec.ts uses for a closed-enum field (gpsSource).
    //
    // The shipped view maps the reason through fixed copy per branch
    // (mirroring SPEC 4.5.1.1's banner sentences) rather than reflecting the
    // raw string, so an unrecognised value never reaches the DOM at all --
    // this test does not assume which of the two designs is in force, only
    // that neither ever produces an element or lets the payload execute.
    const client = new FakeWsClient()
    const { settings: api, stores } = await mountSettings()
    api.loadSettings(() => 'not-an-ip-address')
    teardownStores = stores.connectStores(asClient(client))
    client.emitState('unauthorized', HOSTILE_SCRIPT as unknown as UnauthorizedReason)
    await settle()

    const el = diagnosticField('unauthorizedReason')
    expect(el.children.length).toBe(0)
    expect(root().querySelector('script')).toBeNull()
    expect((window as unknown as { __pwned_settings?: boolean }).__pwned_settings).not.toBe(true)
  })
})
