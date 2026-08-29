// Pure string-formatting rules for the Dashboard (SPEC 4.5.1.1). No store
// imports and no DOM: every function here takes the raw value a store or a
// stats payload carries and returns exactly the string the spec names.
// `views/Dashboard.svelte` subscribes to stores and lays out what these
// functions return; it does not itself decide how a value becomes text.

import type { Gps, Mode } from './protocol'
import type { ConnectionState, UnauthorizedReason } from './ws'

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
 * types `GpsSource` as a closed enum with no empty member, the same way the
 * `null`-name arm of `formatNamedEvent` is not -- but `lib/stores.ts` writes
 * `message.data` into the store with no runtime validation (issue #109), so
 * that type is a compile-time claim about a remote payload, not a fact
 * about one. Pinned anyway rather than left as defence in depth
 * (SPEC 4.5.1.1, issue #142).
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
 * `<name> at <time>` for the last handshake and the last peer: the name
 * alone when the timestamp is missing, the time alone when the name is
 * absent or empty, `DASH` when both are (SPEC 4.5.1.1).
 *
 * An empty name is a real capture, not a malformed one -- a hidden network
 * broadcasts no SSID -- and must not render as `` at 12:04`` with a
 * leading space where a name should be, so it is treated the same as an
 * absent one.
 *
 * `common.json` makes `LastHandshake.ssid` and `Peer.name` required
 * strings, so the `null`-name arm is unreachable from a conformant
 * payload; it exists because this function takes the two fields off an
 * object (`stats.lastHandshake`, `stats.lastPeer`) that may itself be
 * `null`, and the caller passes that absence straight through rather than
 * inventing a name.
 *
 * The name passes through unchanged, hostile or not. An SSID is 32 bytes
 * chosen by whoever the unit just saw, and a peer name arrives over the
 * mesh from somebody else's unit (SPEC 4.5.3); escaping a value like that
 * is the renderer's job, and mangling it here -- truncating it, stripping
 * characters, anything -- would be a second, quieter answer to a rule that
 * already has one.
 */
export function formatNamedEvent(name: string | null, epochSeconds: number | null): string {
  const hasName = name !== null && name !== ''
  const time = formatUnitTime(epochSeconds)
  const hasTime = time !== DASH
  if (hasName && hasTime) {
    return `${name} at ${time}`
  }
  if (hasName) {
    // hasName narrows name to a non-null, non-empty string here.
    return name as string
  }
  if (hasTime) {
    return time
  }
  return DASH
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

const PASV_UNAVAILABLE_MESSAGE = 'The unit does not have the PASV plugin.'
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
