// Settings layer of SPEC 4.7: the host list of the Settings view (4.5.2),
// persisted, and the one place that decides which unit lib/ws.ts talks to.
// lib/ws.ts takes a URL and a token and asks no questions about where they
// came from; this file is where those questions get answered.

import { derived, type Readable, writable } from 'svelte/store'

import { storeToken } from './ws'

export interface Host {
  id: string
  label: string
  address: string
  wsPort: number
  httpPort: number
  token: string | null
}

export interface Settings {
  hosts: Host[]
  activeHostId: string | null
}

const SETTINGS_STORAGE_KEY = 'companion.settings'
const DEFAULT_WS_PORT = 8082
const DEFAULT_HTTP_PORT = 8443
const MIN_PORT = 1
const MAX_PORT = 65535

/**
 * SPEC 4.7: prefilled rather than discovered. The app is served by the
 * unit, so the address it was reached on is the one that works; there is
 * no scan, only what the owner already typed in when they set the unit up.
 * The Bluetooth PAN address is the common case (SPEC 4.5.2, 4.5.5) and is
 * offered first; the USB gadget address is offered too. The desktop-only
 * note is rendered by the Settings screen, not carried in the label: a
 * label is persisted on first run and never re-derived, so a warning
 * living there is one the owner can delete by renaming the entry.
 */
// The ids below are literal strings rather than generateId() output on
// purpose: these two entries are the ones every fresh install starts
// with, not ones a caller minted. Nothing in this file refers to either
// id today; SPEC 4.7 holds them stable so that a future loadSettings or
// the Settings screen can refer to "the Bluetooth default" across
// reloads without depending on insertion order or persisted state.
export function defaultHosts(): Host[] {
  return [
    {
      id: 'bluetooth',
      label: 'Bluetooth',
      address: '172.20.10.2',
      wsPort: DEFAULT_WS_PORT,
      httpPort: DEFAULT_HTTP_PORT,
      token: null,
    },
    {
      id: 'usb',
      label: 'USB',
      address: '10.0.0.2',
      wsPort: DEFAULT_WS_PORT,
      httpPort: DEFAULT_HTTP_PORT,
      token: null,
    },
  ]
}

function defaultSettings(): Settings {
  return { hosts: defaultHosts(), activeHostId: null }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

/**
 * Reads a field as an own property only (SPEC 4.7). `value` is untrusted
 * input parsed from storage; reading `value.token` directly would resolve
 * through the prototype chain, so a bare `{}` entry would adopt whatever
 * `Object.prototype.token` happens to hold if anything else on the origin
 * polluted it. `Object.hasOwn` closes that off.
 */
function ownField(value: Record<string, unknown>, key: string): unknown {
  return Object.hasOwn(value, key) ? value[key] : undefined
}

function isPort(value: unknown): value is number {
  return typeof value === 'number' && Number.isInteger(value) && value >= MIN_PORT && value <= MAX_PORT
}

// Lenient on purpose: this only has to tell an IPv6 literal apart from a
// hostname or a "host:port" typo, not validate every RFC 4291 shorthand.
// Two or more colons is the signal; a single colon is what "host:port"
// looks like, and that is exactly the shape SPEC 4.7 rejects. isBareHost
// below is what actually rejects a malformed literal such as "1:2:3" --
// this only decides whether to bracket for that round trip.
const IPV6_CHARS_RE = /^[0-9a-fA-F:]+$/

function isIPv6(address: string): boolean {
  if (!IPV6_CHARS_RE.test(address)) return false
  return (address.match(/:/g)?.length ?? 0) >= 2
}

// SPEC 4.7 refuses the unspecified IPv6 address, whatever shorthand it is
// spelled in -- "::", "::0" and "0:0:0:0:0:0:0:0" all name the same 128
// zero bits, and "::ffff:0:0" names it again through the IPv4-mapped
// form, the IPv6 spelling of the IPv4 wildcard "0.0.0.0". Enumerating
// spellings is the thing this rule keeps having to re-close, so this
// expands `address` (already confirmed by isIPv6 to be colon-hex only)
// to its full eight 16-bit groups and asks what the value is rather than
// how it was written. `address` is never bracketed here; the caller
// still owns bracketing for the URL round trip.
// Depth behind the serializer rather than the thing that catches the odd
// spellings: `new URL` renormalises `::0` and `0:0:0:0:0:0:0:0` on its own, so
// the round-trip comparison refuses those before this runs, and only `::` and
// `::ffff:0:0` reach it. It exists so the rule is decided by what an address
// means rather than by how it happens to be written, which is the mistake the
// leading-zero rule made twice (SPEC 4.7).
function ipv6Groups(address: string): number[] | null {
  const halves = address.split('::')
  if (halves.length > 2) return null
  const first = halves[0] ?? ''
  const second = halves[1] ?? ''
  let head: string[]
  let tail: string[]
  if (halves.length === 2) {
    head = first === '' ? [] : first.split(':')
    tail = second === '' ? [] : second.split(':')
  } else {
    head = first.split(':')
    tail = []
  }
  const missing = 8 - head.length - tail.length
  if (missing < 0 || (halves.length === 1 && missing !== 0)) return null
  const groups = [...head, ...Array(missing).fill('0'), ...tail]
  if (groups.length !== 8) return null
  return groups.map((group) => Number.parseInt(group === '' ? '0' : group, 16))
}

function isUnspecifiedIPv6(address: string): boolean {
  const groups = ipv6Groups(address)
  if (groups === null) return false
  if (groups.every((group) => group === 0)) return true
  // ::ffff:0:0, the IPv4-mapped spelling of 0.0.0.0.
  return (
    groups[0] === 0 &&
    groups[1] === 0 &&
    groups[2] === 0 &&
    groups[3] === 0 &&
    groups[4] === 0 &&
    groups[5] === 0xffff &&
    groups[6] === 0 &&
    groups[7] === 0
  )
}

// SPEC 4.7: an address is ASCII. new URL() folds a hostname by IDNA, not
// by Unicode case rules, and the two agree on almost everything except
// U+212A, the Kelvin sign -- it lowercases in JavaScript to the same "k"
// IDNA maps it to, so an address typed with it would round-trip through
// candidate.toLowerCase() unchanged and then connect to a host other than
// the one on screen. Refusing any non-ASCII code point up front closes
// that off before the comparison ever runs; a punycode name is already
// ASCII in its "xn--" form and passes unaffected, the shape a browser
// would have produced anyway. This is one half of the Kelvin-sign defence:
// isAscii states the refusal as a rule, before the parser ever sees the
// character; asciiLowerCase below is what makes that refusal observable
// by keeping the comparison itself out of Unicode case folding. Neither
// closes the hazard alone.
const ASCII_RE = /^[\x00-\x7f]*$/

function isAscii(address: string): boolean {
  return ASCII_RE.test(address)
}

// The other half of the Kelvin-sign defence started at isAscii above: the
// comparison in bareHostRoundTrips only ever runs on an address isAscii
// already accepted, so this only has to fold "A"-"Z" -- but it is kept
// independent of toLowerCase() so nothing routes a non-ASCII input through
// Unicode case folding again by accident. Either half alone looks like a
// safe, removable refactor; together they are what keeps U+212A from
// round-tripping to a host other than the one on screen.
function asciiLowerCase(value: string): string {
  return value.replace(/[A-Z]/g, (char) => String.fromCharCode(char.charCodeAt(0) + 32))
}

/**
 * SPEC 4.7: an address is accepted only if it round-trips through the same
 * parser wsUrlFor hands it to, bracketed the same way wsUrlFor brackets
 * it. This replaced a per-grammar hostname/IPv4 check that rejected a
 * leading zero ("010.0.0.2" reads as octal to new URL()) but missed the
 * identical hazard in hex -- "0x7f.1", "0x7f000001", "0xc0000201" and
 * "0x1.0x2.0x3.0x4" all pass the old all-digit guard and reach new URL()
 * as something else entirely. Closing the class rather than the instance:
 * anything the URL parser silently rewrites, whatever base or shorthand it
 * happens to read a label in, is by definition an address that displays
 * as one thing and connects to another, and there is no need to know the
 * name of the rewrite to refuse it. The comparison is case-insensitive
 * because new URL() lowercases a hostname on the way through.
 */
function bareHostRoundTrips(address: string): boolean {
  if (!isAscii(address)) return false
  const bracketed = isIPv6(address)
  const candidate = bracketed ? `[${address}]` : address
  let url: URL
  try {
    url = new URL(`wss://${candidate}:1`)
  } catch {
    return false
  }
  if (url.hostname !== asciiLowerCase(candidate)) return false
  // The unspecified IPv6 address and the unspecified IPv4 address both
  // round-trip to themselves, so nothing above catches either, but
  // neither names a host to connect to -- SPEC 4.7 refuses both
  // explicitly, on the same grounds SPEC 3 holds the plugin listeners
  // to: nothing in this project ever binds a wildcard, so nothing here
  // should be able to name one. The IPv6 side is decided by meaning
  // (isUnspecifiedIPv6), not by one literal spelling of it.
  if (bracketed && isUnspecifiedIPv6(address)) return false
  if (!bracketed && url.hostname === '0.0.0.0') return false
  return true
}

/**
 * SPEC 4.7: an address is a bare host, a name or an IPv4 or IPv6 literal,
 * with no scheme, no path, no credentials and no port. Ruling out "://",
 * "/" and "@" up front covers the scheme/path/credentials cases directly
 * and gives a clearer rejection than routing them through the URL parser
 * would. "[" and "]" are ruled out for the same reason and one more: a
 * bare host never carries the brackets an IPv6 literal needs inside a
 * URL, and bracketing is bareHostRoundTrips' own job below, done once,
 * consistently, from the unbracketed address -- accepting a pre-bracketed
 * literal here would let it reach the URL parser unbracketed instead
 * (isIPv6 does not recognise "[" or "]" as IPv6 characters), round-trip
 * to itself, and skip the IPv6 branch of every check downstream,
 * including the unspecified-address refusal. A trailing dot (a
 * root-labelled FQDN such as "example.test.") is also rejected up front:
 * new URL() accepts it unchanged, so the round trip below would not
 * catch it, but SPEC 4.7 asks for a bare host with no extra syntax and
 * there is nothing a root label buys an entry on this screen. Everything
 * else is decided by bareHostRoundTrips.
 */
function isBareHost(address: string): boolean {
  if (
    address === '' ||
    address.includes('://') ||
    address.includes('/') ||
    address.includes('@') ||
    address.includes('[') ||
    address.includes(']') ||
    address.endsWith('.')
  ) {
    return false
  }
  return bareHostRoundTrips(address)
}

function sanitizeLabel(value: unknown, address: string): string {
  return typeof value === 'string' && value !== '' ? value : address
}

function sanitizePort(value: unknown, fallback: number): number {
  return isPort(value) ? value : fallback
}

function sanitizeToken(value: unknown): string | null {
  return typeof value === 'string' && value !== '' ? value : null
}

/**
 * Validates one persisted host. SPEC 4.7: "a bad port falls back to its
 * default rather than rejecting the host", so every field that has a
 * sensible default (label, the two ports, the token) is repaired in place
 * rather than failing the whole entry. `id` and `address` have no such
 * default -- a host with neither is not a different host, it is not a host
 * -- so those two are the only fields that drop the entry rather than
 * patch it, which still leaves every other entry in the blob usable.
 */
function parseHost(value: unknown): Host | null {
  if (!isRecord(value)) return null
  const id = ownField(value, 'id')
  const address = ownField(value, 'address')
  if (typeof id !== 'string' || id === '') return null
  if (typeof address !== 'string' || !isBareHost(address)) return null
  const label = sanitizeLabel(ownField(value, 'label'), address)
  const wsPort = sanitizePort(ownField(value, 'wsPort'), DEFAULT_WS_PORT)
  const httpPort = sanitizePort(ownField(value, 'httpPort'), DEFAULT_HTTP_PORT)
  const token = sanitizeToken(ownField(value, 'token'))
  return { id, label, address, wsPort, httpPort, token }
}

/**
 * The stored blob is treated as hostile input, not as a shape this module
 * wrote an hour ago (SPEC 4.7): anything that can run script on this
 * origin can write companion.settings, and companion.token lives under the
 * same threat. A blob that is not JSON, or is JSON but not the
 * `{hosts, activeHostId}` shape this module writes, is indistinguishable
 * from an older version's shape read by a newer one -- both fall back to
 * the defaults so the app still starts, rather than being partially
 * salvaged.
 */
function parseSettings(raw: string): Settings {
  let parsed: unknown
  try {
    parsed = JSON.parse(raw)
  } catch {
    return defaultSettings()
  }
  if (!isRecord(parsed) || !Array.isArray(parsed.hosts)) {
    return defaultSettings()
  }
  const hosts: Host[] = []
  for (const entry of parsed.hosts) {
    const host = parseHost(entry)
    if (host !== null) hosts.push(host)
  }
  // SPEC 4.7: an empty list is a state, not a corruption -- deleting the
  // last host is reachable from the Settings screen, so a stored `hosts`
  // that was already `[]` stays `[]`. A list that is empty because every
  // entry in it was dropped is a different thing: nobody chose that state,
  // and leaving it empty would strand somebody with no entry to connect to
  // and no prefilled one to start from, on the screen that is their only
  // way back. The defaults come back instead.
  if (parsed.hosts.length > 0 && hosts.length === 0) {
    return defaultSettings()
  }
  const activeHostId =
    typeof parsed.activeHostId === 'string' && hosts.some((host) => host.id === parsed.activeHostId)
      ? parsed.activeHostId
      : null
  return { hosts, activeHostId }
}

/**
 * SPEC 4.7: storage that refuses to answer does not stop the app.
 * `localStorage` throws rather than returns when it is disabled or full,
 * both reachable on the browser this ships to. A write that throws leaves
 * the session working from memory; the store already holds `next`, only
 * the next reload will not remember it.
 */
function persist(next: Settings): void {
  try {
    window.localStorage.setItem(SETTINGS_STORAGE_KEY, JSON.stringify(next))
  } catch {
    // Nothing to act on here: the in-memory store is already up to date.
  }
}

const settingsWritable = writable<Settings>(defaultSettings())

export const settings: Readable<Settings> = { subscribe: settingsWritable.subscribe }

export const activeHost: Readable<Host | null> = derived(
  settings,
  ($settings) => $settings.hosts.find((host) => host.id === $settings.activeHostId) ?? null,
)

/**
 * Reads companion.settings and (re)initialises the store from it, falling
 * back to the defaults for anything that will not parse (SPEC 4.7). A read
 * that throws -- storage disabled or unreadable -- is treated the same as
 * a blob that will not parse.
 *
 * SPEC 4.7 also makes this one of the paths that has to reconcile
 * companion.token: a reload restores an active host from storage while the
 * key still holds whatever the last session left, and the parser can drop
 * that host or null a dangling id, so without this the key could end up
 * paired with a host that is no longer in the list, or with no host at
 * all. The resolved active host's token (or `null`, if none is active) is
 * written before the store is set, so nothing observes the mismatch.
 *
 * Meant to be called once, at startup; the store above is what everything
 * after that point reads.
 */
export function loadSettings(): Settings {
  let raw: string | null
  try {
    raw = window.localStorage.getItem(SETTINGS_STORAGE_KEY)
  } catch {
    raw = null
  }
  const next = raw === null ? defaultSettings() : parseSettings(raw)
  const active = next.hosts.find((host) => host.id === next.activeHostId) ?? null
  storeToken(active === null ? null : active.token)
  settingsWritable.set(next)
  return next
}

/**
 * Ids are minted here rather than accepted from the caller: SPEC 4.7 puts
 * every other field of a host under the caller's control, but never asks
 * it to invent an identifier, and a caller-supplied id risks colliding
 * with one already in the list. `crypto.randomUUID` is unconditional
 * because SPEC 3 makes the origin secure, which is what the API requires.
 */
function generateId(): string {
  return crypto.randomUUID()
}

/**
 * SPEC 4.7: the stored blob is validated on the way in and on the way out
 * -- a field the Settings screen writes is no more trustworthy than one
 * read from storage. `address` has no substitute, same as in parseHost, so
 * an address that is not a bare host refuses the call rather than being
 * persisted for the next load to repair; every other field is sanitized
 * the same way parseHost repairs it.
 */
export function addHost(host: Omit<Host, 'id'>): Host {
  if (typeof host.address !== 'string' || !isBareHost(host.address)) {
    throw new Error('settings: address must be a bare host (SPEC 4.7)')
  }
  const created: Host = {
    id: generateId(),
    address: host.address,
    label: sanitizeLabel(host.label, host.address),
    wsPort: sanitizePort(host.wsPort, DEFAULT_WS_PORT),
    httpPort: sanitizePort(host.httpPort, DEFAULT_HTTP_PORT),
    token: sanitizeToken(host.token),
  }
  settingsWritable.update((current) => {
    const next: Settings = { ...current, hosts: [...current.hosts, created] }
    persist(next)
    return next
  })
  return created
}

/**
 * SPEC 4.7: the patch is validated the same way a stored host is, not
 * trusted because it came from the Settings screen rather than
 * `localStorage`. `address` refuses rather than repairs, same as
 * addHost and parseHost. Every other field is sanitized in place --
 * `sanitizeToken` in particular means a patch of `{token: undefined}` (an
 * explicit key that TypeScript's `Partial` allows through unchecked) is
 * folded to `null` rather than reaching storeToken as the literal string
 * "undefined".
 *
 * SPEC 4.7: editing a host's address clears that host's token, on the
 * record and not only in companion.token, for any host and not only the
 * active one -- a token is issued by the unit at an address, and an entry
 * edited today can be activated tomorrow, by which point nothing else
 * remembers that its address ever changed. A patch that sets a token in
 * the same call means the owner has supplied the new unit's token, and
 * that wins over the clear. Editing the active host's token, or its
 * address, also rewrites companion.token, the key SPEC 4.3.6 pins; the
 * key is written after persisting, in line with removeHost.
 */
export function updateHost(id: string, patch: Partial<Omit<Host, 'id'>>): void {
  settingsWritable.update((current) => {
    const existing = current.hosts.find((host) => host.id === id)
    if (existing === undefined) return current

    const addressPatched = 'address' in patch
    const address = addressPatched ? patch.address : existing.address
    if (typeof address !== 'string' || !isBareHost(address)) {
      throw new Error('settings: address must be a bare host (SPEC 4.7)')
    }
    const addressChanged = addressPatched && address !== existing.address

    const label = sanitizeLabel('label' in patch ? patch.label : existing.label, address)
    const wsPort = sanitizePort('wsPort' in patch ? patch.wsPort : existing.wsPort, DEFAULT_WS_PORT)
    const httpPort = sanitizePort('httpPort' in patch ? patch.httpPort : existing.httpPort, DEFAULT_HTTP_PORT)
    const tokenPatched = 'token' in patch
    const token = tokenPatched ? sanitizeToken(patch.token) : addressChanged ? null : existing.token

    const updatedHost: Host = { id, address, label, wsPort, httpPort, token }
    const hosts = current.hosts.map((host) => (host.id === id ? updatedHost : host))
    const next: Settings = { ...current, hosts }
    persist(next)

    if (current.activeHostId === id && (tokenPatched || addressChanged)) {
      storeToken(updatedHost.token)
    }

    return next
  })
}

export function removeHost(id: string): void {
  settingsWritable.update((current) => {
    if (!current.hosts.some((host) => host.id === id)) return current
    const hosts = current.hosts.filter((host) => host.id !== id)
    const wasActive = current.activeHostId === id
    const next: Settings = { hosts, activeHostId: wasActive ? null : current.activeHostId }
    persist(next)
    if (wasActive) {
      // SPEC 4.7: no host is active any more, so no host's token is either.
      storeToken(null)
    }
    return next
  })
}

/**
 * SPEC 4.7: activating a host writes that host's token to companion.token
 * (SPEC 4.3.6), the key lib/ws.ts reads, and clears it when the host has
 * none, rather than leaving the previous host's copy live. Settings is
 * persisted before the key is written, and both happen before the settings
 * store notifies its subscribers, so that whatever performs the actual
 * switch -- teardown, resetStores(), attach, in that order (SPEC 4.4.2) --
 * observes a token that already belongs to the host it is about to attach
 * to. The ordering also decides which of the two crash windows is the one
 * left open: persist first means a crash between the two leaves the
 * persisted active id with its key not written yet, which storeToken can
 * still catch up on the next call; the reverse order would let
 * companion.token point at a host id that persist never got to save, and
 * nothing would know to fix that up. Unknown ids are a no-op: there is
 * nothing to activate.
 */
export function activateHost(id: string): void {
  settingsWritable.update((current) => {
    const host = current.hosts.find((candidate) => candidate.id === id)
    if (host === undefined) return current
    const next: Settings = { ...current, activeHostId: id }
    persist(next)
    storeToken(host.token)
    return next
  })
}

/**
 * The wss:// URL lib/ws.ts's WsClientOptions.url wants, built from a
 * host's address and wsPort. TLS is mandatory (SPEC 3), so this is always
 * wss, never ws. SPEC 4.7: `host.address` is a bare host by construction
 * (parseHost, addHost and updateHost all refuse anything else), so the
 * only thing left to do here is bracket an IPv6 literal, which the URL
 * syntax requires and a plain address string does not carry.
 */
export function wsUrlFor(host: Host): string {
  const address = isIPv6(host.address) ? `[${host.address}]` : host.address
  return `wss://${address}:${host.wsPort}`
}
