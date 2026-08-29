// SPEC 4.5.2.2. Owns which Dashboard control is armed, which state command
// is pending and what each control currently says. `views/Dashboard.svelte`
// subscribes to `controls` below and lays out what it returns; it decides
// nothing about a control's label, its enabled state or its wording, the
// same discipline `lib/format.ts` already holds Dashboard's readings to
// (SPEC 4.5.1.1).
//
// No timer lives here. Every rule below is expressed in observations that
// already flow through `lib/stores.ts` (`stats`, `connection`,
// `capabilities`) and in the promise `lib/ws.ts`'s `command()` returns
// (SPEC 4.3.8): a millisecond bound here would be a second opinion about how
// long a unit takes to come back, beside the patience `lib/ws.ts` already
// spends on the same restart (SPEC 4.3.1), and the two would disagree the
// first time either was tuned.

import { get, writable, type Readable } from 'svelte/store'

import {
  type ControlId,
  COMMAND_NOT_CONFIRMED_MESSAGE,
  formatControlFailure,
  formatModeConfirmLabel,
  formatModeControlLabel,
  formatPasvConfirmLabel,
  formatPasvConsequence,
  formatPasvControlLabel,
  MODE_CONSEQUENCE,
  MODE_PENDING,
  PASV_DISABLED_REASON,
  PASV_PENDING,
  REBOOT_CONFIRM_LABEL,
  REBOOT_CONSEQUENCE,
  REBOOT_LABEL,
  REBOOT_PENDING,
  SHUTDOWN_CONFIRM_LABEL,
  SHUTDOWN_CONSEQUENCE,
  SHUTDOWN_LABEL,
  SHUTDOWN_PENDING,
} from './format'
import type { Capabilities, Mode, Stats } from './protocol'
import { currentClient } from './session'
import { capabilities, connection, stats } from './stores'
import { canSendCommand, RemoteError, type WsClient } from './ws'

export type ControlUiState = 'idle' | 'armed' | 'pending'

export interface ControlEntry {
  /** SPEC 4.5.2: the PASV control alone is conditional, on `capabilities.pasv`. */
  visible: boolean
  disabled: boolean
  state: ControlUiState
  /** The idle button's own label -- names the outcome, not the current state (SPEC 4.5.2.2). */
  requestLabel: string
  /** The confirm button's label, shown only while `state` is `armed`. */
  confirmLabel: string
  /** The sentence beside the confirm/cancel pair, shown only while `state` is `armed`. */
  consequence: string
  /**
   * `data-control-message`'s content: the pending sentence while `state` is
   * `pending`; the static disabled reason while the control is disabled for
   * a reason the unit itself would give and the app could still send a
   * command (SPEC 4.5.2.2: PASV's "reachable only from AUTO", shown only
   * while `sendable`, since it is a claim about the unit's current mode and
   * the app cannot refresh that while the socket is down); otherwise the
   * last failure or abandonment this control has to report, which survives
   * every other kind of disablement -- going offline does not erase what
   * the unit already said. `null` when there is nothing to say at all.
   */
  message: string | null
}

export type ControlsView = Record<ControlId, ControlEntry>

type ModeTarget = 'auto' | 'manual'
type PasvTarget = 'on' | 'off'

// SPEC 4.5.2.2: `set_mode` now has one pending row, not two -- both
// directions resolve the same way, on the connection state becoming
// `restarting`, so there is no longer a reason to track which direction was
// requested once the command has been sent.
type PendingKind = 'mode' | 'pasv_on' | 'pasv_off' | 'reboot' | 'shutdown'

interface Pending {
  kind: PendingKind
  // SPEC 4.5.2.2: "two stats frames arrive without the confirmation", not
  // one -- the first may have been assembled before the command was
  // handled, since the ticker (SPEC 4.3.7) shares nothing with the command
  // path.
  statsFramesSeen: number
  // Minted fresh by every confirmControl() call and compared, not `kind`,
  // by the rejection handler that settles this entry -- see the comment
  // there for why `kind` alone is not enough to identify which request a
  // late rejection belongs to.
  token: number
}

function controlOf(kind: PendingKind): ControlId {
  switch (kind) {
    case 'mode':
      return 'mode'
    case 'pasv_on':
    case 'pasv_off':
      return 'pasv'
    case 'reboot':
      return 'reboot'
    case 'shutdown':
      return 'shutdown'
  }
}

function pendingMessageFor(kind: PendingKind): string {
  switch (kind) {
    case 'mode':
      return MODE_PENDING
    case 'pasv_on':
    case 'pasv_off':
      return PASV_PENDING
    case 'reboot':
      return REBOOT_PENDING
    case 'shutdown':
      return SHUTDOWN_PENDING
  }
}

/**
 * SPEC 4.5.2.2's pending-ends table, the half that is observable from
 * `stats` alone. `set_mode`, `reboot` and `shutdown` never confirm this way
 * -- they end on `connection` becoming `restarting`, handled separately
 * below -- so this answers `false` for them and lets the two-frame
 * abandonment rule below be the single fallback for every kind rather than
 * a rule only some of them are subject to.
 *
 * `set_mode` used to wait for `stats.mode` to become `AUTO` or `PASV` on the
 * `auto` request, on the reasoning that PASV *is* auto with a plugin on top
 * (SPEC 2.5). That reasoning is sound and the rule was still wrong: after
 * the restart the socket returns and the mode is null for the whole of
 * SPEC 2.6.0's startup window, which is explicitly not a fixed few seconds,
 * and the two-frame abandonment below would fire inside it and report a
 * failed rescue on the exact unit issue #147's null-mode rule exists to
 * rescue. Null is ambiguous in both directions and stays ambiguous for an
 * unbounded time, so no pending rule for a mode change can be built on
 * `stats.mode` at all -- the badge says the rest, in its own time.
 */
function confirmedByStats(kind: PendingKind, s: Stats | null): boolean {
  if (s === null) return false
  switch (kind) {
    case 'pasv_on':
      return s.mode === 'PASV'
    case 'pasv_off':
      return s.mode === 'AUTO'
    default:
      return false
  }
}

// SPEC 4.5.2.2: PASV *is* auto with a plugin on top (SPEC 2.5), which the
// section argues from three places -- what the mode switch offers, whether
// PASV is disabled, and (before this change) whether a mode-change request
// counted as confirmed. One predicate, asked wherever that fact is needed,
// rather than the same condition spelled out separately in each place.
function isAutoOrPasv(mode: Mode | null): boolean {
  return mode === 'AUTO' || mode === 'PASV'
}

function modeTarget(s: Stats | null): ModeTarget {
  // SPEC 4.5.2.2: MANUAL offers AUTO, AUTO and PASV both offer MANU, and a
  // null mode offers AUTO -- a unit with no agent is a unit in manual mode
  // (SPEC 2.6.0), and this switch is what takes it back.
  return isAutoOrPasv(s?.mode ?? null) ? 'manual' : 'auto'
}

/**
 * The one place `set_pasv`'s target is decided: what the PASV control's
 * label names and what `confirmControl` puts on the wire are two readings
 * of this single fact, not two independent computations of `stats.mode`
 * that happen to agree.
 */
function pasvTarget(s: Stats | null): PasvTarget {
  return s?.mode === 'PASV' ? 'off' : 'on'
}

// Module-level state (SPEC 4.5.2.2): one control armed at a time, one
// request pending at a time, and the last outcome each control has to
// report, none of it store-shaped on its own -- `render()` below folds it
// together with `stats`/`connection`/`capabilities` into the `ControlsView`
// the Dashboard actually reads.
let armed: ControlId | null = null
let pending: Pending | null = null
const lastMessage: Record<ControlId, string | null> = {
  mode: null,
  pasv: null,
  reboot: null,
  shutdown: null,
}

// SPEC 4.5.2.2: minted fresh at every confirmControl() call and stashed on
// the Pending it creates. See the comment in confirmControl's own .catch
// for why a rejection has to be matched against this and not against
// `kind`.
//
// Deliberately NOT reset by resetControls(), and that omission is the
// point, not an oversight to tidy away. A host switch tears down the old
// client and rejects whatever of its commands were still in flight
// (lib/ws.ts's close()), but that rejection settles asynchronously and can
// still be on its way when the new client's own first confirmControl()
// call runs. If this counter reset to 0 on every switch, the old client's
// stale token and the new client's fresh one could collide on the same
// number, reintroducing exactly the cross-request mismatch the token
// exists to prevent -- only across a host switch instead of across two
// requests on one control. Counting across the whole module's lifetime,
// never rewound, is what keeps every token unique regardless of how many
// clients this module has lived through.
let nextConfirmToken = 0

// SPEC 4.5.2.2/4.4.2: a host switch must not leave one unit's armed or
// pending control attached to another's. `lib/session.ts` calls
// `resetStores()` on every switch, and this module could have been wired
// the same way -- but `lib/session.ts` documents itself as "the join
// between three layers that otherwise know nothing about each other", and
// none of the three is this one. Importing a reset there would make the
// join depend on a fourth layer built on top of it, for a module that
// already has everything it needs to notice a switch on its own:
// `currentClient()` is exactly the identity `lib/session.ts` rebuilds on
// (SPEC 4.8), so comparing it against what this module last saw, on every
// `stats`/`connection` notification it already reacts to, finds the same
// switch with no new coupling in either direction. Not exported: nothing
// outside this module calls it, so this is the reconciliation itself,
// rather than a seam left for something else to call.
function resetControls(): void {
  armed = null
  pending = null
  lastMessage.mode = null
  lastMessage.pasv = null
  lastMessage.reboot = null
  lastMessage.shutdown = null
}

let lastKnownClient: WsClient | null = null

function reconcileClientIdentity(): void {
  const client = currentClient()
  if (client !== lastKnownClient) {
    lastKnownClient = client
    resetControls()
  }
}

/**
 * The one sentence that survives a control being disabled while the app
 * could still send a command (SPEC 4.5.2.2) -- PASV's, when the mode is not
 * AUTO or PASV; the other three controls have none. The single place this
 * is decided: `computeBase` below folds it into `disabled`, and every
 * caller -- `buildPasv`'s own entry, and `isControlAvailable` further down,
 * by way of that entry's `disabled` field -- reads the same fact instead of
 * restating it.
 */
function staticDisabledReasonFor(id: ControlId, s: Stats | null): string | null {
  // SPEC 4.5.2.2: disabled when the mode is not AUTO or PASV, including a
  // null mode -- the opposite of the mode switch above, and for the
  // opposite reason: entering PASV on a unit with no agent is not a rescue,
  // it is a request the unit will refuse.
  if (id === 'pasv' && !isAutoOrPasv(s?.mode ?? null)) return PASV_DISABLED_REASON
  return null
}

interface BaseComputed {
  state: ControlUiState
  disabled: boolean
  message: string | null
}

/**
 * The part of a control's view that does not depend on which control it is:
 * whether it is the one pending, the one armed, or disabled because the app
 * cannot send a command right now, another control is mid-request, or (for
 * PASV) the mode makes it moot -- `staticDisabledReasonFor` above is the one
 * sentence that survives that last case. Pure: no mutation of `armed` or
 * `pending` happens here, only a read of them.
 */
function computeBase(id: ControlId, sendable: boolean, s: Stats | null): BaseComputed {
  const isPending = pending !== null && controlOf(pending.kind) === id
  if (isPending) {
    return { state: 'pending', disabled: true, message: pendingMessageFor((pending as Pending).kind) }
  }

  const staticDisabledReason = staticDisabledReasonFor(id, s)
  const otherPending = pending !== null && controlOf(pending.kind) !== id
  const disabled = !sendable || staticDisabledReason !== null || otherPending
  const state: ControlUiState = !disabled && armed === id ? 'armed' : 'idle'

  // SPEC 4.5.2.2: pending (handled above), then the static disabled reason
  // where it applies, then the last message -- which is shown regardless of
  // why (or whether) the control is currently disabled, because a failure
  // or an abandonment outlives the control being disabled.
  const message = sendable && staticDisabledReason !== null ? staticDisabledReason : lastMessage[id]

  return { state, disabled, message }
}

function buildMode(s: Stats | null, sendable: boolean): ControlEntry {
  const target = modeTarget(s)
  const base = computeBase('mode', sendable, s)
  return {
    visible: true,
    disabled: base.disabled,
    state: base.state,
    requestLabel: formatModeControlLabel(target),
    confirmLabel: formatModeConfirmLabel(target),
    consequence: MODE_CONSEQUENCE,
    message: base.message,
  }
}

function buildPasv(s: Stats | null, sendable: boolean, caps: Capabilities | null): ControlEntry {
  const target = pasvTarget(s)
  const currentlyOn = target === 'off'
  const base = computeBase('pasv', sendable, s)
  return {
    visible: caps?.pasv === true,
    disabled: base.disabled,
    state: base.state,
    requestLabel: formatPasvControlLabel(currentlyOn),
    confirmLabel: formatPasvConfirmLabel(target),
    consequence: formatPasvConsequence(target),
    message: base.message,
  }
}

function buildReboot(s: Stats | null, sendable: boolean): ControlEntry {
  const base = computeBase('reboot', sendable, s)
  return {
    visible: true,
    disabled: base.disabled,
    state: base.state,
    requestLabel: REBOOT_LABEL,
    confirmLabel: REBOOT_CONFIRM_LABEL,
    consequence: REBOOT_CONSEQUENCE,
    message: base.message,
  }
}

function buildShutdown(s: Stats | null, sendable: boolean): ControlEntry {
  const base = computeBase('shutdown', sendable, s)
  return {
    visible: true,
    disabled: base.disabled,
    state: base.state,
    requestLabel: SHUTDOWN_LABEL,
    confirmLabel: SHUTDOWN_CONFIRM_LABEL,
    consequence: SHUTDOWN_CONSEQUENCE,
    message: base.message,
  }
}

/** The one `build*` function for a given control id, so a caller that needs a single entry -- `isControlAvailable`'s call site in `render()`, below -- does not restate the mapping `buildView` already makes. */
function buildEntry(
  id: ControlId,
  s: Stats | null,
  sendable: boolean,
  caps: Capabilities | null,
): ControlEntry {
  switch (id) {
    case 'mode':
      return buildMode(s, sendable)
    case 'pasv':
      return buildPasv(s, sendable, caps)
    case 'reboot':
      return buildReboot(s, sendable)
    case 'shutdown':
      return buildShutdown(s, sendable)
  }
}

function buildView(s: Stats | null, sendable: boolean, caps: Capabilities | null): ControlsView {
  return {
    mode: buildEntry('mode', s, sendable, caps),
    pasv: buildEntry('pasv', s, sendable, caps),
    reboot: buildEntry('reboot', s, sendable, caps),
    shutdown: buildEntry('shutdown', s, sendable, caps),
  }
}

/**
 * Whether a control could be armed or confirmed right now: visible, and not
 * disabled. The same two fields `requestControl`'s own guard reads off the
 * store, and the same order of operations `computeBase` already showed for
 * PASV's static reason -- one definition, asked everywhere the question
 * matters, rather than the render()-time disarm check and the arm guard
 * each deciding "available" on their own terms and agreeing only by
 * accident. That accident was real: an earlier version derived this from
 * `disabled` alone, which does not depend on `visible` at all, so an armed
 * PASV control whose capability dropped between broadcasts -- SPEC
 * 4.5.2.2's own race, argued at length for exactly this reason -- was never
 * disarmed, because `disabled` stayed false the whole time; only `visible`
 * had gone false, and nothing was asking it.
 */
function isControlAvailable(entry: ControlEntry): boolean {
  return entry.visible && !entry.disabled
}

function currentView(): ControlsView {
  const s = get(stats)
  const sendable = canSendCommand(get(connection).state)
  return buildView(s, sendable, get(capabilities))
}

const controlsWritable = writable<ControlsView>(currentView())

export const controls: Readable<ControlsView> = { subscribe: controlsWritable.subscribe }

function render(): void {
  reconcileClientIdentity()
  const s = get(stats)
  const sendable = canSendCommand(get(connection).state)
  const caps = get(capabilities)
  // SPEC 4.5.2.2: "arming does not expire... it is already cleared whenever
  // the control stops being available." Done here, where the inputs that
  // could make that true actually change, rather than as a side effect of
  // computing what to draw -- `computeBase`/`buildView` above are pure reads
  // of `armed`/`pending`, never writers of them. `buildEntry` for the one
  // armed control, not the whole view: `disabled` and `visible` do not
  // depend on `armed` (only `state` does), so this is safe to compute before
  // deciding whether to clear it.
  if (armed !== null && !isControlAvailable(buildEntry(armed, s, sendable, caps))) {
    armed = null
  }
  controlsWritable.set(buildView(s, sendable, caps))
}

function abandonPending(): void {
  if (pending === null) return
  lastMessage[controlOf(pending.kind)] = COMMAND_NOT_CONFIRMED_MESSAGE
  pending = null
}

// SPEC 4.5.2.2: a `stats` frame either confirms the pending request, or
// counts toward the two-frame abandonment that applies to every kind alike
// -- for the three rows that wait on `restarting` this is the backstop for
// a restart that never began, for the two `set_pasv` rows it is the unit
// having ignored the request.
stats.subscribe((s) => {
  if (pending !== null) {
    if (confirmedByStats(pending.kind, s)) {
      pending = null
    } else {
      pending.statsFramesSeen += 1
      if (pending.statsFramesSeen >= 2) {
        abandonPending()
      }
    }
  }
  render()
})

// SPEC 4.5.2.2: `set_mode`, `reboot` and `shutdown` all end pending the same
// way -- the connection state becomes `restarting` -- because there is no
// other observable success for a unit that is about to drop the socket that
// would have reported one.
connection.subscribe((conn) => {
  if (pending !== null && conn.state === 'restarting') {
    const kind = pending.kind
    if (kind === 'mode' || kind === 'reboot' || kind === 'shutdown') {
      pending = null
    }
  }
  render()
})

/**
 * Arms `id`, per SPEC 4.5.2.2's "one control armed at a time": arming a
 * second disarms the first, which falls out of `armed` being a single slot
 * rather than a set. Clears `id`'s own last message -- a fresh question
 * starts clean, not still carrying the previous answer.
 *
 * A no-op while `id` is not available, by `isControlAvailable` above -- the
 * same question `render()` asks to decide whether an already-armed control
 * has to be disarmed. Not reachable through the DOM -- the request button
 * carries the same `disabled` attribute the browser already acts on, and a
 * control that is not visible has no button to tap in the first place
 * (SPEC 4.5.2: PASV only, while `capabilities.pasv` is not true) -- kept as
 * the same defensive guard `lib/ws.ts`'s `command()` keeps for its own
 * unreachable half.
 */
export function requestControl(id: ControlId): void {
  if (!isControlAvailable(get(controlsWritable)[id])) return
  armed = id
  lastMessage[id] = null
  render()
}

/** Disarms whichever control is armed. Idempotent: nothing is armed is a no-op. */
export function cancelControl(): void {
  armed = null
  render()
}

/**
 * Sends the command `id`'s armed state represents, and arms the pending
 * state SPEC 4.5.2.2's table resolves. A no-op if `id` is not the armed
 * control or a request is already pending -- both unreachable through the
 * DOM the confirm button only exists on the armed control, and every
 * control is disabled while one is pending -- kept as the same defensive
 * guard `lib/ws.ts`'s own `command()` keeps for its unreachable half.
 */
export function confirmControl(id: ControlId): void {
  if (armed !== id || pending !== null) return
  const client = currentClient()
  if (client === null) {
    // SPEC 4.5.2.2: a control disabled for connectivity says nothing of its
    // own; there is nothing to send it against, so it disarms rather than
    // staying armed on a client that does not exist.
    armed = null
    render()
    return
  }

  const s = get(stats)
  let kind: PendingKind
  let send: () => Promise<unknown>
  switch (id) {
    case 'mode': {
      const target = modeTarget(s)
      kind = 'mode'
      send = () => client.command('set_mode', { mode: target })
      break
    }
    case 'pasv': {
      const target = pasvTarget(s)
      kind = target === 'on' ? 'pasv_on' : 'pasv_off'
      send = () => client.command('set_pasv', { on: target === 'on' })
      break
    }
    case 'reboot':
      kind = 'reboot'
      send = () => client.command('reboot')
      break
    case 'shutdown':
      kind = 'shutdown'
      send = () => client.command('shutdown')
      break
  }

  const token = ++nextConfirmToken
  armed = null
  pending = { kind, statsFramesSeen: 0, token }
  render()

  send().catch((err: unknown) => {
    // Matched by `token`, not by `kind`. A late rejection can otherwise
    // land on a newer request for the same control: REQUEST_TIMEOUT_MS
    // (lib/ws.ts, 15 s) can outlast the two-frame abandonment below, whose
    // floor is two keepalive_interval's 5 s minimum (SPEC 2.4) -- so on a
    // wedged unit the first Reboot can be abandoned at ~10 s while its own
    // timeout is still running, the owner taps Reboot again, and at 15 s
    // the first request's timeout arrives while the second is genuinely
    // pending. Both share `kind === 'reboot'`, so comparing on that alone
    // would let the first request's failure overwrite the second's pending
    // state -- reporting a live reboot as refused. `token` is unique per
    // call, so only the entry this exact call created can ever be the one
    // matched and cleared here.
    if (pending === null || pending.token !== token) return
    const code = err instanceof RemoteError ? err.code : null
    lastMessage[id] = formatControlFailure(id, code)
    pending = null
    render()
  })
}
