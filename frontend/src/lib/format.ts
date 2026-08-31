// Pure string-formatting rules for the Dashboard (SPEC 4.5.1.1). No store
// imports and no DOM: every function here takes the raw value a store or a
// stats payload carries and returns exactly the string the spec names.
// `views/Dashboard.svelte` subscribes to stores and lays out what these
// functions return; it does not itself decide how a value becomes text.

// Type-only, same direction as every other formatter's own import of what
// it renders: lib/geo.ts does not import this file.
import type { GeoState } from './geo'
import type { Gps, HandshakeGps, Mode, RestartReason } from './protocol'
// Type-only, and cycle-free: lib/wifi.ts does not import this file, so
// importing its NearbySortOrder here is the same direction of dependency
// every other formatter in this section already has on the module that
// owns the value it is turning into a string.
import type { NearbySortOrder } from './wifi'
import type { ConnectionState, LastError, UnauthorizedReason } from './ws'

/** The dash itself. Exported so no caller writes the character by hand. */
export const DASH = '-'

/** The accessible text carried beside a dash, for a value with no visible label of its own. */
export const EMPTY_LABEL = 'unavailable'

/**
 * What `formatAge` returns when there is no age. Exported so a caller
 * (the Dashboard's `degraded` banner sentence, SPEC 4.5.1.1) compares
 * against the formatter's own answer rather than against a copy of it:
 * testing `sessionAge === null` instead lets a non-finite `sessionAge`
 * (`formatAge` also answers `NEVER_REFRESHED` for one) back into the
 * "the data is <age> old" sentence and reconstructs
 * `Connected, and the data is never refreshed old.`, the exact string the
 * two-sentence banner rule exists to forbid.
 */
export const NEVER_REFRESHED = 'never refreshed'

/**
 * A value that is not a finite, non-negative number is not known (SPEC
 * 4.5.1.1). None of this is reachable from a conformant plugin -- the
 * schemas make these fields numbers and the plugin is their only writer --
 * but it is pinned here rather than left as defence in depth, because
 * `src/lib/**` is gated on branch coverage (SPEC 10.7) and a guard nobody
 * specified is a branch no independent test author knows to reach.
 */
function isValidDuration(value: number): boolean {
  return Number.isFinite(value) && value >= 0
}

/** NaN and +/-Infinity are not known; a negative reading (a temperature, a channel) still is. */
function isFiniteNumber(value: number): boolean {
  return Number.isFinite(value)
}

/**
 * The two largest non-zero units of an uptime, keeping the second unit even
 * when it is itself zero (`1h 0m`, not `1h`), so the string does not change
 * shape as it counts down. Seconds alone below a minute.
 *
 * `DASH` for a value that is not a finite, non-negative number: an uptime
 * is a duration still running, and there is no such thing as a negative
 * one (SPEC 4.5.1.1).
 */
export function formatUptime(seconds: number): string {
  if (!isValidDuration(seconds)) {
    return DASH
  }
  const total = Math.floor(seconds)
  if (total < 60) {
    return `${total}s`
  }
  if (total < 3600) {
    const minutes = Math.floor(total / 60)
    const remainingSeconds = total % 60
    return `${minutes}m ${remainingSeconds}s`
  }
  if (total < 86400) {
    const hours = Math.floor(total / 3600)
    const minutes = Math.floor((total % 3600) / 60)
    return `${hours}h ${minutes}m`
  }
  const days = Math.floor(total / 86400)
  const hours = Math.floor((total % 86400) / 3600)
  return `${days}d ${hours}h`
}

/**
 * Whole seconds below a minute, whole minutes above it, floored -- never
 * rounded, so an age never claims to be fresher than the payload says.
 * `null` means the bettercap snapshot has never refreshed (SPEC 2.5), and
 * a value that is not a finite, non-negative number says the same thing
 * for the same reason `formatUptime` returns `DASH` for one (SPEC 4.5.1.1).
 * Both return `NEVER_REFRESHED`, never a private copy of that string.
 */
export function formatAge(seconds: number | null): string {
  if (seconds === null || !isValidDuration(seconds)) {
    return NEVER_REFRESHED
  }
  const total = Math.floor(seconds)
  if (total < 60) {
    return `${total}s`
  }
  return `${Math.floor(total / 60)}m`
}

/**
 * Rounded to the nearest degree; `DASH` for `null` or for a value that is
 * not a finite number. Below-freezing readings are real temperatures, not
 * invalid ones, so a negative value is left alone (SPEC 4.5.1.1).
 */
export function formatTemperature(celsius: number | null): string {
  if (celsius === null || !isFiniteNumber(celsius)) {
    return DASH
  }
  return `${Math.round(celsius)} °C`
}

/**
 * `84%`, rounded; `DASH` for `null` or for a value that is not a finite
 * number. The battery is two rows, not one (SPEC 4.5.1.1): a combined
 * string had no way to render `{percent: null, charging: true}` without
 * discarding a fact the unit sent, or `{percent: null, charging: false}`
 * without rendering it as unavailable when the unit had in fact said
 * something. Each row dashes on its own `null` and needs no exception.
 */
export function formatBatteryPercent(percent: number | null): string {
  if (percent === null || !isFiniteNumber(percent)) {
    return DASH
  }
  return `${Math.round(percent)}%`
}

/**
 * `charging` or `on battery`; `DASH` for `null`. Its own row, beside
 * `formatBatteryPercent`, for the same reason that function's own
 * docstring gives (SPEC 4.5.1.1).
 */
export function formatCharging(charging: boolean | null): string {
  if (charging === null) {
    return DASH
  }
  return charging ? 'charging' : 'on battery'
}

/**
 * The channel number as it arrived, unrounded; `DASH` for `null` or for a
 * value that is not a finite number.
 */
export function formatChannel(channel: number | null): string {
  if (channel === null || !isFiniteNumber(channel)) {
    return DASH
  }
  return `${channel}`
}

/**
 * The mode as the unit's own display writes it: `AUTO`, `PASV`, and `MANU`
 * for `MANUAL`. `DASH` for `null`, the ordinary rule for a value the plugin
 * has no agent to ask about, which is what a unit in manual mode gives it
 * (SPEC 2.5, issue #140).
 */
export function formatMode(mode: Mode | null): string {
  if (mode === null) {
    return DASH
  }
  return mode === 'MANUAL' ? 'MANU' : mode
}

/**
 * `off` when `enabled` is false, otherwise `fix` or `no fix`. A source
 * that is switched off has not failed to get a fix (SPEC 4.5.1.1): `no
 * fix` says the unit is looking and has not found one, which is a thing to
 * wait for, while `off` says nobody is looking, which is a thing to change
 * in `config.toml`.
 */
export function formatGpsFix(gps: Gps): string {
  if (!gps.enabled) {
    return 'off'
  }
  return gps.fix ? 'fix' : 'no fix'
}

/**
 * The source name as it arrived; `DASH` when it is `null` or `''`, which is
 * the ordinary dash rule and not a fourth word. `off` belongs to
 * `formatGpsFix`, the row that answers "is it looking": a source is a
 * name, and the absence of a name is the absence of a name (SPEC 4.5.1.1).
 *
 * The `''` arm is not reachable from a conformant payload -- `common.json`
 * types `GpsSource` as a closed enum with no empty member -- but
 * `lib/stores.ts` writes `message.data` into the store with no runtime
 * validation (issue #109), so that type is a compile-time claim about a
 * remote payload, not a fact about one. Pinned anyway rather than left as
 * defence in depth (SPEC 4.5.1.1, issue #142).
 */
export function formatGpsSource(gps: Gps): string {
  const source = gps.source as string | null
  return source === null || source === '' ? DASH : source
}

const SHORT_MONTHS = [
  'Jan',
  'Feb',
  'Mar',
  'Apr',
  'May',
  'Jun',
  'Jul',
  'Aug',
  'Sep',
  'Oct',
  'Nov',
  'Dec',
]

function isSameLocalDay(a: Date, b: Date): boolean {
  return (
    a.getFullYear() === b.getFullYear() &&
    a.getMonth() === b.getMonth() &&
    a.getDate() === b.getDate()
  )
}

/** Same rule as `isSameLocalDay`, one unit up: the year the date prefix is silent about. */
function isSameLocalYear(a: Date, b: Date): boolean {
  return a.getFullYear() === b.getFullYear()
}

/**
 * The instant the unit stamped, rendered in the reader's own timezone:
 * hours and minutes alone when the instant falls today, the date in front
 * of it when it does not, and the year in front of that when it is not
 * this year either: `09:14`, `5 Aug 09:14`, `5 Aug 2025 09:14`. `DASH` for
 * `null` or for a value that is not a finite number.
 *
 * Never a relative age (SPEC 4.5.1.1): the reference hardware has no
 * real-time clock, so a relative age computed against the phone's clock
 * could read "in four hours" for something that already happened, a lie
 * the app would manufacture out of two honest values. A bare time carries
 * the same defect by omission rather than by arithmetic: `09:14` on a
 * handshake caught last Tuesday reads as this morning, which the last
 * handshake and the last peer routinely are on a unit that has been out in
 * a pocket. The year is the same omission one unit up, and it is not
 * hypothetical on a device that keeps its captures until somebody clears
 * them: `5 Aug 09:14` on a handshake from last August reads as three weeks
 * ago. Today's events keep the bare time and this year's keep the bare
 * date, because a date on every row, or a year on every date, is noise on
 * the rows the reader is most likely to be looking at.
 *
 * The day is not zero-padded. A test matching a padded day on a word
 * boundary is green from the thirteenth of the month and red from the
 * fourth to the twelfth, because `5` does not sit on a word boundary
 * inside `05` (SPEC 4.5.1.1).
 *
 * A negative epoch is a real date before 1970 and renders its actual time
 * of day; nothing to guard there, unlike `NaN` or +/-`Infinity`, neither
 * reachable by schema, since every epoch field is a plain number or `null`.
 */
export function formatUnitTime(epochSeconds: number | null): string {
  if (epochSeconds === null || !isFiniteNumber(epochSeconds)) {
    return DASH
  }
  const date = new Date(epochSeconds * 1000)
  const hours = `${date.getHours()}`.padStart(2, '0')
  const minutes = `${date.getMinutes()}`.padStart(2, '0')
  const time = `${hours}:${minutes}`
  const now = new Date()
  if (isSameLocalDay(date, now)) {
    return time
  }
  const day = `${date.getDate()}`
  const month = SHORT_MONTHS[date.getMonth()]
  if (isSameLocalYear(date, now)) {
    return `${day} ${month} ${time}`
  }
  return `${day} ${month} ${date.getFullYear()} ${time}`
}

/**
 * The sentence Settings shows for a connection state (SPEC 4.5.2.1). Total
 * over `ConnectionState`: a `switch` with no `default` arm, so a state
 * added to the type without a case added here is a compile error rather
 * than a silent fall-through, the same discipline `formatMode` and
 * `formatGpsFix` already follow over their own closed enums.
 *
 * This is a different sentence from Dashboard's banner (SPEC 4.5.1.1),
 * which also carries the session's staleness age in `degraded` and has no
 * business here: Settings names the socket's own state, not the age of
 * the last snapshot it carried.
 */
export function formatConnectionState(state: ConnectionState): string {
  switch (state) {
    case 'connecting':
      return 'Connecting'
    case 'connected':
      return 'Connected'
    case 'degraded':
      return 'Connected (data is stale)'
    case 'offline':
      return 'Offline'
    case 'unauthorized':
      return 'Unauthorized'
    case 'restarting':
      return 'Restarting'
  }
}

/**
 * The sentence Settings shows for the reason a connection is `unauthorized`
 * (SPEC 4.3.1, 4.5.2.1). Total over `UnauthorizedReason`, the same
 * discipline `formatConnectionState` follows above: "rejected" is a token
 * sent and refused, "required" is no token sent, whether because none is
 * stored or because the close that would have said so never arrived.
 */
export function formatUnauthorizedReason(reason: UnauthorizedReason): string {
  switch (reason) {
    case 'rejected':
      return 'The unit refused the stored token.'
    case 'required':
      return 'The unit requires a token and none is stored.'
  }
}

/**
 * The call to action that follows `formatUnauthorizedReason`'s sentence
 * (SPEC 4.5.2.1): together they are the Dashboard's `unauthorized` banner
 * (SPEC 4.5.1.1), `${formatUnauthorizedReason(reason)} ${formatUnauthorizedCallToAction(reason)}`,
 * and Settings shows the first half alone as its diagnostic. One source
 * for both sentences, not a second typing of the half that tells somebody
 * what to do -- the half most likely to be corrected later.
 */
export function formatUnauthorizedCallToAction(reason: UnauthorizedReason): string {
  switch (reason) {
    case 'rejected':
      return 'Fix it in Settings.'
    case 'required':
      return 'Add it in Settings.'
  }
}

/**
 * The Dashboard's `restarting` banner sentence (SPEC 4.4.1/4.5.1.1, issue
 * #131). `mode_change` and `reboot` share one sentence; `shutdown` reads
 * differently on purpose, present tense and with no promise of a return,
 * because §4.3.1 makes that state terminal -- the app is not waiting for
 * anything and the copy must not say otherwise.
 */
export function formatRestartReason(reason: RestartReason): string {
  switch (reason) {
    case 'mode_change':
    case 'reboot':
      return 'The unit is restarting.'
    case 'shutdown':
      return 'The unit is shutting down.'
  }
}

// ---------------------------------------------------------------------------
// The diagnostics pair (SPEC 4.3.10, 4.5.2.1): the last error and the
// latency. Both live on lib/ws.ts's Diagnostics, mirrored onto the
// `connection` store, and rendered by Settings alone -- there is no second
// caller these need to agree with yet.
// ---------------------------------------------------------------------------

/**
 * SPEC 4.3.10/4.5.3: `error.json`'s `message` carries no length limit the
 * way an SSID's 32 bytes does, so the view bounds it itself rather than
 * trusting a remote to have been brief. Truncation, not escaping -- Svelte's
 * default `{...}` escaping already makes the text safe to render; this only
 * keeps one diagnostic line from being able to grow to the size of the
 * screen. The bound is 200 characters, counted in Unicode code points and
 * not UTF-16 units, so a truncation can never cut a surrogate pair in half
 * and leave a lone half to render as a replacement character.
 *
 * Total over its argument, not just its declared `string` type: `lib/ws.ts`
 * writes `error.json`'s payload into `LastError` with no runtime validation
 * (issue #109), so a `message` that is not actually a string reaches here at
 * runtime despite what the signature claims. Coercing it with `String()`
 * puts `null`, `undefined` or `[object Object]` on the line, attributed to
 * the unit -- a sentence the unit never sent, on the one screen whose job is
 * to report faithfully what it did send. So a non-string is treated as
 * absent, the same as no message at all, and the bound below is computed on
 * the string that survives, not on a `.length` that may not be a character
 * count at all.
 */
export const LAST_ERROR_MESSAGE_MAX_LENGTH = 200

export function formatLastErrorMessage(message: string): string {
  if (typeof message !== 'string') return ''
  const codePoints = Array.from(message)
  return codePoints.length > LAST_ERROR_MESSAGE_MAX_LENGTH
    ? `${codePoints.slice(0, LAST_ERROR_MESSAGE_MAX_LENGTH).join('')}...`
    : message
}

/**
 * SPEC 4.3.10/4.5.2.2: for `source: 'frame'`, `code` is remote-chosen and
 * never rendered itself -- it only selects one of the sentences below, the
 * same discipline `formatControlFailure` already holds the command path to.
 *
 * A code that already has a home elsewhere in this app (the PASV control,
 * the Log view) is given the same sentence it is shown under there, reused
 * rather than re-worded a second time for this line. Everything else falls
 * to one generic sentence: SPEC 4.3.5's own table already calls this
 * diagnostics line "where an unrecognised code goes rather than nowhere".
 *
 * For `source: 'close'`, `code` is not a wire code at all, it is the close
 * code in digits, browser-generated from a small closed set, and SPEC
 * 4.3.10 has it rendered rather than hidden: `1006` with no reason is the
 * tether gone or a certificate the phone refused, `1008` is the unit
 * answering and declining the token, and a clean `1000` is a unit that shut
 * down while connected. One sentence collapses those three; the code beside
 * it is the diagnosis.
 *
 * For `source: 'local'`, `code` names a drop this client detected itself --
 * `pong_timeout`, `connect_timeout`, `socket_failed` -- rather than anything
 * a remote said, so there is no message and no code shown beside the
 * sentence, unlike the close case above. `socket_failed` (issue #122) is
 * the one of the three that is about the phone rather than the unit -- the
 * browser refused to open the connection, before there was anything on the
 * other end to fail to answer -- and its sentence says so, because an
 * owner told the unit is not answering will go and check the unit instead.
 */
export function formatLastErrorCode(lastError: LastError): string {
  if (lastError.source === 'frame') {
    switch (lastError.code) {
      case 'unauthorized':
        return formatUnauthorizedReason('rejected')
      case 'pasv_requires_auto':
        return PASV_DISABLED_REASON
      case 'pasv_unavailable':
        return PASV_UNAVAILABLE_MESSAGE
      case 'log_unavailable':
        return LOG_UNAVAILABLE_MESSAGE
      case 'no_frame':
        return MIRROR_NO_FRAME_MESSAGE
      default:
        return 'The unit reported an error.'
    }
  }
  if (lastError.source === 'local') {
    // `LastError`'s `'local'` arm types `code` to `LocalErrorCode` (SPEC
    // 4.3.10), so the switch below is total over the three codes it can
    // actually carry without needing a default arm no runtime value could
    // ever reach.
    switch (lastError.code) {
      case 'pong_timeout':
        return 'The connection stopped responding.'
      case 'connect_timeout':
        return 'The unit did not answer while connecting.'
      case 'socket_failed':
        return 'The browser refused to open the connection.'
    }
  }
  return `The connection closed (code ${lastError.code}).`
}

/**
 * SPEC 4.3.10: `at`, in the phone's own clock -- never through
 * `formatUnitTime`. Every other timestamp this app renders came off the
 * wire and belongs to the unit, whose clock can be days out on a device
 * with no RTC (the same reason `pong.timestamp` is never read for the
 * latency above); formatting a phone stamp with the unit's formatter would
 * be at its most misleading exactly when the two clocks disagree, which is
 * the case somebody reads this line to work out.
 *
 * A time of day, not an age: an age has to be recomputed to stay true, and
 * SPEC 4.5.2.5 spends the one timer this client is allowed on the Log. This
 * is written once and stays correct.
 *
 * A bare `HH:MM` claims today: the last error survives until the connection
 * is admitted (SPEC 4.3.10), so one recorded at 23:59 can still be on
 * screen after midnight, and read as today's if the date is left off. The
 * date is added once the stamp is not the current day, compared at render
 * rather than kept by a timer -- there is no timer here to keep it with,
 * and a render that happens after midnight without one is a render nobody
 * is looking at.
 */
export function formatLastErrorTime(atMs: number): string {
  const date = new Date(atMs)
  const hours = `${date.getHours()}`.padStart(2, '0')
  const minutes = `${date.getMinutes()}`.padStart(2, '0')
  const time = `${hours}:${minutes}`
  if (isSameLocalDay(date, new Date())) {
    return time
  }
  const day = `${date.getDate()}`
  const month = SHORT_MONTHS[date.getMonth()]
  return `${day} ${month} ${time}`
}

/** SPEC 4.3.10/4.5.2.1: the round trip in whole milliseconds, `DASH` when there is none to show. */
export function formatLatency(latencyMs: number | null): string {
  return latencyMs === null ? DASH : `${Math.round(latencyMs)} ms`
}

// ---------------------------------------------------------------------------
// The Dashboard controls (SPEC 4.5.2.2). Every string in that section's four
// copy tables, verbatim, so `lib/controls.ts` and `views/Dashboard.svelte`
// never type a control's wording themselves. `ControlId` lives here, not in
// `lib/controls.ts`, so this file has no import back onto the module that
// consumes it.
// ---------------------------------------------------------------------------

export type ControlId = 'mode' | 'pasv' | 'reboot' | 'shutdown'

type ModeTarget = 'auto' | 'manual'
type PasvTarget = 'on' | 'off'

/**
 * The mode switch's label, from what it offers rather than the current
 * state (SPEC 4.5.2.2): a toggle that names the current state has to be
 * read twice, once for what it says and once for what tapping it will do.
 * Which mode is offered for a given `stats.mode` is a rule `lib/controls.ts`
 * decides, not this function -- this only turns an already-decided target
 * into the sentence the table names.
 */
export function formatModeControlLabel(target: ModeTarget): string {
  return target === 'auto' ? 'Switch to AUTO' : 'Switch to MANU'
}

export function formatModeConfirmLabel(target: ModeTarget): string {
  return target === 'auto' ? 'Confirm switch to AUTO' : 'Confirm switch to MANU'
}

/** Both `set_mode` rows share one consequence sentence (SPEC 4.5.2.2's table). */
export const MODE_CONSEQUENCE = "Restarts the unit's services. It comes back in 20 to 60 seconds."

/** Both `set_mode` rows share one pending sentence (SPEC 4.5.2.2's table). */
export const MODE_PENDING = 'Restarting.'

/**
 * The PASV control's label, from whether the unit is currently in PASV, not
 * from the value the tap would request -- the same "name the outcome"
 * reasoning as `formatModeControlLabel` above.
 */
export function formatPasvControlLabel(currentlyOn: boolean): string {
  return currentlyOn ? 'Leave PASV' : 'Enter PASV'
}

export function formatPasvConfirmLabel(target: PasvTarget): string {
  return target === 'on' ? 'Confirm entering PASV' : 'Confirm leaving PASV'
}

export function formatPasvConsequence(target: PasvTarget): string {
  return target === 'on'
    ? 'The unit stops attacking and only listens.'
    : 'The unit returns to AUTO and resumes attacking.'
}

export const PASV_PENDING = 'Waiting for the unit to confirm.'

/**
 * Shown both as the disabled PASV control's static reason (mode is not AUTO
 * or PASV) and as the failure sentence for a `pasv_requires_auto` rejection
 * (SPEC 4.5.2.2): one sentence from one place, so the client-side rule and
 * the server-side refusal cannot drift into two different explanations of
 * one condition.
 */
export const PASV_DISABLED_REASON = 'PASV is reachable only from AUTO.'

export const REBOOT_LABEL = 'Reboot'
export const REBOOT_CONFIRM_LABEL = 'Confirm reboot'
export const REBOOT_CONSEQUENCE = 'The unit reboots. It comes back in a minute or two.'
export const REBOOT_PENDING = 'Rebooting.'

export const SHUTDOWN_LABEL = 'Shut down'
export const SHUTDOWN_CONFIRM_LABEL = 'Confirm shutdown'
// SPEC 4.5.2.2: names no specific button, because which one that is depends
// on what the unit is built from, and the reference hardware's PiSugar 3 is
// not the only answer. What is true of every unit is that this app is not
// the way back.
export const SHUTDOWN_CONSEQUENCE = 'The unit powers off. It cannot be turned back on from here.'
export const SHUTDOWN_PENDING = 'Shutting down.'

/** The cancel button's label, the same word for every control (SPEC 4.5.2.2). */
export const CANCEL_LABEL = 'Cancel'

// Exported so formatLastErrorCode (SPEC 4.3.10, below) can reuse it rather
// than re-wording the same code's meaning a second time for the diagnostics
// line.
export const PASV_UNAVAILABLE_MESSAGE = 'The unit does not have the PASV plugin.'
const COMMAND_REFUSED_MESSAGE = 'The unit did not accept the command.'

/** Two `stats` frames arrived with no confirmation of the change (SPEC 4.5.2.2). */
export const COMMAND_NOT_CONFIRMED_MESSAGE = 'The unit did not confirm the change.'

/**
 * The sentence for a command that failed, from SPEC 4.5.2.2's condition
 * table. `code` is the rejection's `RemoteError.code` (`lib/ws.ts`) when the
 * command was refused by an `error` frame, and `null` for a rejection that
 * never named one -- refused before it was sent, or timed out. The code is
 * remote-chosen and never rendered itself (SPEC 4.5.3): it only selects one
 * of the sentences below, and anything unrecognised selects the last of
 * them.
 *
 * Both PASV sentences are scoped to `control === 'pasv'`, and the scoping
 * is the point rather than tidiness: the code is chosen by whatever
 * answered, so an unscoped `pasv_unavailable` would put "The unit does not
 * have the PASV plugin" beside a `reboot` that failed for an unrelated
 * reason -- a sentence true of nothing the owner just did, offered as the
 * explanation of why their reboot failed. A sentence only explains the
 * control it is about.
 */
export function formatControlFailure(control: ControlId, code: string | null): string {
  if (control === 'pasv' && code === 'pasv_requires_auto') {
    return PASV_DISABLED_REASON
  }
  if (control === 'pasv' && code === 'pasv_unavailable') {
    return PASV_UNAVAILABLE_MESSAGE
  }
  return COMMAND_REFUSED_MESSAGE
}

// ---------------------------------------------------------------------------
// Shared list-view copy (SPEC 4.5.2.3/4.5.2.4). Named for what it is, not
// for which screen first needed it: both the Wi-Fi view's two segments and
// Peers use these, and a name that claimed one screen is what let
// lib/lists.ts's predicate get born as `lib/wifi.ts`'s in the first place.
// ---------------------------------------------------------------------------

/**
 * SPEC 4.5.2.3/4.5.2.4: the "not connected" sentence for an empty list that
 * has not been fetched, shared unchanged between the Wi-Fi view and the
 * Peers view (§4.5.2.4: "The second is §4.5.2.3's sentence, unchanged and
 * shared: it says nothing about which list it is under, because the reason
 * it appears has nothing to do with the list."). One string, not two.
 */
const NOT_CONNECTED_LIST_MESSAGE = 'Not connected, so this list has not been read.'

/**
 * The label on the refresh control every list view carries (SPEC 4.5.2.3's
 * copy table; SPEC 4.5.2.4 names no wording of its own for Peers' own
 * refresh control, so this is the same word rather than a second, identical
 * constant).
 */
export const REFRESH_LABEL = 'Refresh'

// ---------------------------------------------------------------------------
// The Wi-Fi view (SPEC 4.5.2.3). Every string in that section's two copy
// tables, verbatim, plus the byte-size formatter its own paragraph fixes the
// wording of. `views/WiFi.svelte` subscribes to the accessPoints and
// handshakes stores and lays out what these functions return; the sort order,
// which is logic rather than wording, lives in `lib/wifi.ts` instead, and the
// empty-versus-never-fetched decision lives in `lib/lists.ts`, shared with
// Peers.
// ---------------------------------------------------------------------------

export type WifiSegment = 'nearby' | 'captured'

/**
 * The sentence shown in place of an empty list (SPEC 4.5.2.3's copy table).
 * `fetched` is `lib/lists.ts`'s `hasListDataArrived`, read off the
 * connection state -- not a property of the list itself, which is why it is
 * passed in rather than derived here.
 */
export function formatWifiEmptyMessage(segment: WifiSegment, fetched: boolean): string {
  if (!fetched) {
    return NOT_CONNECTED_LIST_MESSAGE
  }
  return segment === 'nearby' ? 'The unit reports no access points.' : 'The unit has no captures.'
}

/**
 * Shown on the Captured segment only, while `truncated` is set (SPEC
 * 4.5.2.3). `total` is nullable (SPEC 2.7) because `handshakes_list.total`
 * is, but the two cannot occur together: a list that was truncated was a
 * list that was enumerated, so `total` is never null while `truncated` is
 * set. The null branch below is written anyway, because "cannot happen"
 * held by two independent facts is exactly the pairing that stops holding
 * when one of them changes, and it says what SPEC 4.5.2.3 says: there is
 * nothing to subtract from when the total is unknown.
 */
export function formatTruncationNotice(total: number | null): string {
  if (total === null) {
    return `Showing the newest 500 captures. Total ${EMPTY_LABEL}.`
  }
  return `Showing the newest 500 captures of ${total}.`
}

/** The badge's own text for the 500-entry cap (SPEC 4.5.2.3), never composed by hand. */
export const TRUNCATED_BADGE = '500+'

/**
 * A segment badge's text (SPEC 4.5.2.3's DOM hooks: "its text is the count
 * and nothing else"): the bare count, or `TRUNCATED_BADGE` while `truncated`
 * is set. Deciding between the two is the one thing that can be wrong here
 * -- a raw `500` where `500+` belongs -- so it lives here rather than in a
 * view's own ternary, the same discipline every other rule on this screen
 * already follows.
 */
export function formatSegmentBadge(count: number, truncated: boolean): string {
  return truncated ? TRUNCATED_BADGE : `${count}`
}

/**
 * The sort control's label, from what tapping it will do rather than the
 * order in force (SPEC 4.5.2.3) -- the same "name the outcome" rule
 * `formatModeControlLabel` already follows for the Dashboard's mode switch,
 * and for the same reason: a label that states the current order has to be
 * read twice.
 */
export function formatWifiSortLabel(currentOrder: NearbySortOrder): string {
  return currentOrder === 'rssi' ? 'Sort by channel' : 'Sort by signal'
}

/**
 * `<n> B` below 1024 bytes, then `<n.n> kB` and `<n.n> MB`, each to one
 * decimal place, powers of 1024 (SPEC 4.5.2.3). `DASH` for a value that is
 * not a finite, non-negative number: `size` is a required integer by
 * schema, but `lib/stores.ts` writes the payload with no runtime
 * validation (issue #109), the same reasoning `formatGpsSource`'s
 * docstring gives for its own unreachable arm.
 *
 * Every boundary is decided on the *rounded* value, never the raw one, and
 * that holds one unit down as well as one unit up. `1023.6` bytes is below
 * the 1024 threshold raw, but `Math.round`ed -- which the B arm already
 * does, for the same non-integer-payload defence the top guard exists for
 * -- it is `1024`, a reading of `1024 B` the table cannot contain any more
 * than it can contain `1024.0 kB`. Comparing the raw value to a threshold
 * and rounding the *output* separately lets the two disagree; rounding
 * first and branching on that same rounded number, at both boundaries, is
 * what keeps the number a reader sees and the unit beside it always
 * consistent with each other. The B/kB case is unreachable from a
 * schema-conformant integer, which is exactly why it is worth closing
 * rather than waving through: the guard above and the rounding in this arm
 * both exist only because nothing validates the payload (issue #109), and
 * a function that defends against a non-integer size on one line and
 * assumes an integer on the next is not defending against anything. The
 * kB/MB boundary is unreachable in this product for a different reason --
 * no capture is that large -- and is held to the same rule anyway, so one
 * formatter has one way of deciding a unit rather than two.
 */
export function formatByteSize(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) {
    return DASH
  }
  const roundedBytes = Math.round(bytes)
  if (roundedBytes < 1024) {
    return `${roundedBytes} B`
  }
  const kb = Math.round((bytes / 1024) * 10) / 10
  if (kb < 1024) {
    return `${kb.toFixed(1)} kB`
  }
  const mb = Math.round((bytes / 1024 / 1024) * 10) / 10
  return `${mb.toFixed(1)} MB`
}

/**
 * A capture's GPS pin, present or not. SPEC 4.5.2.3 asks the row to carry
 * "a GPS pin when the sidecar carried one" and names no coordinate format;
 * the Map view (SPEC 4.5.2) is already where a position is plotted, so this
 * list states only whether one exists rather than inventing a second,
 * unspecified rendering of the coordinates themselves.
 */
export const GPS_PIN_LABEL = 'Pinned'
export function formatGpsPin(gps: HandshakeGps | null): string {
  return gps === null ? DASH : GPS_PIN_LABEL
}

/**
 * SPEC 4.5.2.3: "every remote string on a row follows SPEC 4.5.1.1" --
 * decided from the value, not from the formatted string, the same
 * discipline `formatGpsSource`'s own docstring argues for. Covers two
 * shapes at once: `AccessPoint.hostname`/`vendor`/`encryption` are required
 * strings by schema with no nullable slot of their own, where an empty
 * string is not a reading; `HandshakeEntry.bssid` is the nullable case,
 * where a missing value says the same thing. Neither is a dash by
 * accident, so one function answers both rather than two call sites
 * re-deriving the same claim, which is what let this rule live inside a
 * view four times before it moved here.
 *
 * Exported beside `formatRemoteString`, the pair `formatGpsSource` and its
 * own emptiness rule already model: a caller asks this, never compares the
 * formatted string against `DASH`, because a dash is as legal an SSID, a
 * hostname or a BSSID as any other string a stranger could choose.
 */
function hasRemoteStringValue(value: string | null): value is string {
  return value !== null && value !== ''
}

export function isRemoteStringUnknown(value: string | null): boolean {
  return !hasRemoteStringValue(value)
}

export function formatRemoteString(value: string | null): string {
  return hasRemoteStringValue(value) ? value : DASH
}

// ---------------------------------------------------------------------------
// The Peers view (SPEC 4.5.2.4). Sort order is not wording and lives in
// lib/peers.ts, the same split lib/wifi.ts already draws for the Wi-Fi view.
// ---------------------------------------------------------------------------

/**
 * The sentence shown in place of an empty peer list (SPEC 4.5.2.4's copy
 * table). `fetched` is `lib/lists.ts`'s `hasListDataArrived`, the same
 * predicate `formatWifiEmptyMessage` above reads: SPEC 4.5.2.4 shares the
 * rule rather than restating it.
 */
export function formatPeersEmptyMessage(fetched: boolean): string {
  if (!fetched) {
    return NOT_CONNECTED_LIST_MESSAGE
  }
  return 'The unit has met no peers.'
}

/**
 * `rssi`, `channel`, `encounters`, `pwndRun` and `pwndTotal` are all
 * nullable integers on the wire with nothing to round and no unit to add
 * (SPEC 4.5.2.4). `DASH` for `null` or for a value that is not a finite
 * number, the ordinary rule (SPEC 4.5.1.1) also applied above to `size`
 * (`formatByteSize`) for the same reason: `lib/stores.ts` writes the
 * payload with no runtime validation (issue #109), so the schema's
 * non-nullable, integer-typed claim is compile-time, not a fact about a
 * given payload.
 */
export function formatPeerNumber(value: number | null): string {
  if (value === null || !isFiniteNumber(value)) {
    return DASH
  }
  return `${value}`
}

// ---------------------------------------------------------------------------
// The Log view (SPEC 4.5.2.5). Every string in that section's two copy
// tables, verbatim. The filter itself is logic, not wording, and lives in
// lib/log.ts; the follow timer's mechanism lives in lib/viewRefresh.ts
// beside the refresh trigger it reuses.
// ---------------------------------------------------------------------------

// Exported so formatLastErrorCode (SPEC 4.3.10, below) can reuse it rather
// than re-wording the same code's meaning a second time for the diagnostics
// line.
export const LOG_UNAVAILABLE_MESSAGE =
  'The unit could not read its log. With no agent it has no configuration to find the path in.'

/**
 * SPEC 4.5.2.5's copy table, the "no lines at all" half of it, and this is
 * only ever asked with no lines to show: the DOM hooks say the
 * empty-message element is absent whenever a line is rendered, so a
 * `log_unavailable` held from an earlier ask never overrides a buffer the
 * client is still showing.
 *
 * **`!fetched` outranks `unavailable`.** `log_unavailable` is an answer from
 * a session that may since have ended, and nothing clears it on an ordinary
 * drop: `resetStores()` runs on an explicit disconnect and on
 * `unauthorized`, not on the socket simply going away (SPEC 4.4.2). Ranked
 * the other way, a unit that answered once and then went out of range would
 * leave the screen asserting it has no agent, indefinitely, about a
 * connection that no longer exists -- the mirror SPEC 4.3.1 already argues
 * against for the connection banner, applied here to a flag instead of a
 * state.
 */
export function formatLogEmptyMessage(fetched: boolean, unavailable: boolean): string {
  if (!fetched) {
    return NOT_CONNECTED_LIST_MESSAGE
  }
  if (unavailable) {
    return LOG_UNAVAILABLE_MESSAGE
  }
  return "The unit's log is empty."
}

/** Lines are held but none match the filter (SPEC 4.5.2.5's copy table). */
export const LOG_NO_FILTER_MATCH_MESSAGE = 'No lines match the filter.'

const LOG_FOLLOW_LABEL = 'Follow'
const LOG_FOLLOWING_LABEL = 'Following'

/**
 * The follow control names its own state, not an outcome: it is a toggle
 * carrying `aria-pressed` (SPEC 4.5.2.5's DOM hooks), not a command like the
 * Dashboard's mode switch (SPEC 4.5.2.2), so "read it twice" does not apply
 * here the way it does there.
 */
export function formatFollowLabel(following: boolean): string {
  return following ? LOG_FOLLOWING_LABEL : LOG_FOLLOW_LABEL
}

/** The font-size control's label (SPEC 4.5.2.5's copy table), fixed rather than naming a size: the table gives it one word, not one per state. */
export const LOG_FONT_SIZE_LABEL = 'Text size'

/**
 * The filter input's accessible name (SPEC 4.5.2.5's copy table and DOM
 * hooks): a `<label for>`, not a `placeholder`, the same rule every other
 * input in this app already follows -- a placeholder is not a name, and
 * VoiceOver announces a field with none as a bare "text field".
 */
export const LOG_FILTER_LABEL = 'Filter'

// ---------------------------------------------------------------------------
// The Mirror view (SPEC 4.5.2.6). Every string in that section's two copy
// tables, verbatim. The base64 shape check is logic, not wording, and lives
// in lib/screen.ts; the auto-refresh timer's mechanism is lib/viewRefresh.ts's
// watchViewFollow, the Log's own, granted a second call site by that section.
// ---------------------------------------------------------------------------

// Exported so formatLastErrorCode (SPEC 4.3.10, above) reuses it rather than
// wording the `no_frame` code a second way.
export const MIRROR_NO_FRAME_MESSAGE = 'The unit has not drawn a frame yet.'

/**
 * SPEC 4.5.2.6's copy table. Both the "no frame held, connected" row and the
 * "the unit answered `no_frame`" row read the same sentence: a store that was
 * never written by a `screen_image` reply and a store the unit has just
 * confirmed holds nothing are, per that section's own words, "the same thing
 * from the owner's side", so this function is asked only from whether a
 * frame is held (`lib/stores.ts`'s `screen` store being `null`), the same way
 * `formatLogEmptyMessage` is asked from whether `log` holds any lines rather
 * than from which particular request last failed.
 */
export function formatMirrorEmptyMessage(fetched: boolean): string {
  if (!fetched) {
    return NOT_CONNECTED_LIST_MESSAGE
  }
  return MIRROR_NO_FRAME_MESSAGE
}

/**
 * A frame is held but `lib/screen.ts`'s `screenFrameSrc` rejected its `png`
 * (SPEC 4.5.2.6's copy table, "a frame arrived that is not readable"). The
 * sentence says the frame could not be read rather than naming the unit,
 * because a frame that arrived corrupt and a frame this app mishandles look
 * identical from the screen.
 */
export const MIRROR_UNREADABLE_MESSAGE = 'The frame could not be read.'

const MIRROR_AUTO_LABEL = 'Refresh automatically'
const MIRROR_AUTO_ON_LABEL = 'Refreshing automatically'

/**
 * The auto-refresh control names its own state, not an outcome (SPEC
 * 4.5.2.6's DOM hooks: it carries `aria-pressed` because it is a toggle
 * rather than a command), the same reasoning `formatFollowLabel` already
 * follows for the Log's own toggle.
 */
export function formatMirrorAutoLabel(auto: boolean): string {
  return auto ? MIRROR_AUTO_ON_LABEL : MIRROR_AUTO_LABEL
}

/**
 * The frame `img`'s accessible name (SPEC 4.5.2.6's copy table): names the
 * object rather than what it shows. The face and status arrive separately,
 * in `face_status`, and reading them out of the picture is not something
 * this client does.
 */
export const MIRROR_FRAME_LABEL = "The unit's display"

// ---------------------------------------------------------------------------
// The Map view (SPEC 4.5.2.7). Which captures are trusted enough to plot and
// where the unit's own marker comes from are lib/map.ts's; this file only
// carries the wording for the three states an owner can find the captures
// layer in, and the caption naming how many of them have a position.
// ---------------------------------------------------------------------------

const MAP_NO_CAPTURES_MESSAGE = 'The unit has no captures.'
const MAP_NO_POSITIONED_CAPTURES_MESSAGE = "None of the unit's captures has a position."

/**
 * SPEC 4.5.2.7's copy table: three states, and only the third is new wording.
 * The first is `NOT_CONNECTED_LIST_MESSAGE`, "the shared one SPEC 4.5.2.4
 * already refused to duplicate", unchanged. The second is the Wi-Fi view's
 * own Captured-segment sentence, "the same fact about the same list", so a
 * second wording for it would be a second answer to one question -- deliberate
 * duplication of that literal rather than an import, since `lib/wifi.ts`'s
 * sibling never extracted it into a constant of its own either.
 *
 * `fetched` is `lib/lists.ts`'s `hasListDataArrived`: "the Map uses it rather
 * than a 'have we ever received one' flag of its own." `hasCaptures` is
 * whether the last `handshakes_list` reply held any entry at all, regardless
 * of whether any of them carry a usable position -- `lib/map.ts`'s
 * `capturesWithGps` decides that separately, so a caller with a non-empty pin
 * list has nothing to show here and never calls this function to find out.
 */
export function formatMapEmptyMessage(fetched: boolean, hasCaptures: boolean): string {
  if (!fetched) {
    return NOT_CONNECTED_LIST_MESSAGE
  }
  return hasCaptures ? MAP_NO_POSITIONED_CAPTURES_MESSAGE : MAP_NO_CAPTURES_MESSAGE
}

/**
 * SPEC 4.5.2.7's caption: `12 captures, 3 with a position.` State three of
 * the table above is a spectrum rather than a switch, and this is what makes
 * the finite-coordinate rule (`lib/map.ts`'s `capturesWithGps`) observable
 * without a test counting Leaflet's own markers. Always plural, the same
 * convention `formatTruncationNotice` already follows for a count that is
 * routinely 0 or 1.
 *
 * `total` is nullable (SPEC 2.7, issue #153): null means the plugin has no
 * directory to enumerate, not zero captures, and a manual-mode unit is
 * exactly this case, not a corner of it. The first clause counts captures,
 * so on a null it must not count -- it names the condition instead, in the
 * shape SPEC 4.5.1.1 uses for the Dashboard's unknown values, rather than
 * printing a number the plugin never had. The second clause stays a real
 * count either way: there being none is not a guess.
 */
export function formatMapCaption(total: number | null, withPosition: number): string {
  const captureCount = total === null ? `Capture count ${EMPTY_LABEL}` : `${total} captures`
  return `${captureCount}, ${withPosition} with a position.`
}

// ---------------------------------------------------------------------------
// The Settings geolocation control (SPEC 4.6.2). The mechanism -- the tap,
// the watch, the push timer, the backgrounding rule -- is lib/geo.ts's own;
// this file only carries the five sentences its copy table names, verbatim.
// ---------------------------------------------------------------------------

const GEO_OFF_MESSAGE = "Not sharing this phone's position."
const GEO_WAITING_MESSAGE = 'Waiting for a position.'
const GEO_SHARING_MESSAGE = "Sharing this phone's position."
const GEO_DENIED_MESSAGE =
  "This browser refused location access. Allow it in the browser's settings, then try again."
const GEO_UNSUPPORTED_MESSAGE = 'This browser cannot supply a position.'

/**
 * SPEC 4.6.2's copy table for `lib/geo.ts`'s `GeoState`. None of these five
 * sentences says anything about what the unit has -- that is §4.6.1's table
 * and `lib/stores.ts`'s `gps` store, and a section that mixed the two would
 * be telling the owner their captures are located because their phone is
 * willing, which is the mistake §4.6.1 spends its second half on.
 */
export function formatGeoStateMessage(state: GeoState): string {
  switch (state) {
    case 'off':
      return GEO_OFF_MESSAGE
    case 'waiting':
      return GEO_WAITING_MESSAGE
    case 'sharing':
      return GEO_SHARING_MESSAGE
    case 'denied':
      return GEO_DENIED_MESSAGE
    case 'unsupported':
      return GEO_UNSUPPORTED_MESSAGE
  }
}

const GEO_TOGGLE_LABEL = "Share this phone's position"
const GEO_TOGGLE_TRY_AGAIN_LABEL = 'Try again'

/**
 * The toggle names its own state (SPEC 4.6.2's DOM hooks: `aria-pressed`
 * carries it), the same rule `formatFollowLabel` and `formatMirrorAutoLabel`
 * already follow for their own toggles -- except for `denied`, which is not
 * a state this control can be pressed back out of by tapping "on" again: the
 * only honest thing left to offer is another attempt, so the label says
 * that instead of repeating a state the section's own copy table already
 * gives the section a sentence for.
 */
export function formatGeoToggleLabel(state: GeoState): string {
  return state === 'denied' ? GEO_TOGGLE_TRY_AGAIN_LABEL : GEO_TOGGLE_LABEL
}
