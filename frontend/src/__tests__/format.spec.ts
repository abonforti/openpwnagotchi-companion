import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  DASH,
  EMPTY_LABEL,
  formatAge,
  formatBatteryPercent,
  formatChannel,
  formatCharging,
  formatConnectionState,
  formatGpsFix,
  formatGpsSource,
  formatMode,
  formatNamedEvent,
  formatTemperature,
  formatUnauthorizedCallToAction,
  formatUnauthorizedReason,
  formatUnitTime,
  formatUptime,
  NEVER_REFRESHED,
} from '../lib/format'
import type { Gps } from '../lib/protocol'
import type { ConnectionState, UnauthorizedReason } from '../lib/ws'

// Written from SPEC.md 4.5.1.1's function table only, never from lib/format.ts
// itself (companion-implementer is authoring that file in parallel). Every
// rule pinned there is asserted on the function directly, not through the
// DOM: dashboard.spec.ts is where the same functions are proven wired into
// the view.

// ---------------------------------------------------------------------------
// DASH / EMPTY_LABEL - the two constants the table names verbatim.
// ---------------------------------------------------------------------------

describe('DASH', () => {
  it("is the literal character '-'", () => {
    expect(DASH).toBe('-')
  })
})

describe('EMPTY_LABEL', () => {
  it("is the literal accessible text 'unavailable'", () => {
    expect(EMPTY_LABEL).toBe('unavailable')
  })
})

// ---------------------------------------------------------------------------
// formatUptime - "the two largest units that are not both zero", the second
// unit kept even at zero, seconds alone below a minute.
// ---------------------------------------------------------------------------

describe('formatUptime', () => {
  it('matches the four worked examples in SPEC.md verbatim', () => {
    expect(formatUptime(45)).toBe('45s')
    expect(formatUptime(90)).toBe('1m 30s')
    expect(formatUptime(3600)).toBe('1h 0m')
    expect(formatUptime(90000)).toBe('1d 1h')
  })

  it('renders seconds alone below a minute, including zero', () => {
    // A falsy-zero bug (`value ? ... : DASH`-shaped) is the mutant this line
    // targets: 0 is a real duration, not an absent one, and formatUptime
    // never returns DASH at all.
    expect(formatUptime(0)).toBe('0s')
    expect(formatUptime(1)).toBe('1s')
    expect(formatUptime(59)).toBe('59s')
  })

  it('switches to minutes+seconds at the 60s boundary, keeping the zero second', () => {
    expect(formatUptime(60)).toBe('1m 0s')
    expect(formatUptime(61)).toBe('1m 1s')
    expect(formatUptime(119)).toBe('1m 59s')
    expect(formatUptime(120)).toBe('2m 0s')
    expect(formatUptime(3599)).toBe('59m 59s')
  })

  it('switches to hours+minutes at the 3600s boundary, dropping seconds even though the example keeps a zero minute', () => {
    expect(formatUptime(3600)).toBe('1h 0m')
    expect(formatUptime(3661)).toBe('1h 1m')
    expect(formatUptime(86399)).toBe('23h 59m')
  })

  it('switches to days+hours at the 86400s boundary, keeping the zero hour', () => {
    expect(formatUptime(86400)).toBe('1d 0h')
    expect(formatUptime(90000)).toBe('1d 1h')
    expect(formatUptime(176400)).toBe('2d 1h')
  })
})

// ---------------------------------------------------------------------------
// formatAge - whole seconds below a minute, whole minutes floored above it,
// null reading as "never refreshed" rather than as zero (SPEC 2.5).
// ---------------------------------------------------------------------------

describe('NEVER_REFRESHED', () => {
  it("is the literal string 'never refreshed', what formatAge returns when there is no age", () => {
    // SPEC.md 4.5.1.1 (amended): exported "so the banner compares against
    // the formatter's own answer rather than a copy of it" -- dashboard.spec.ts
    // uses this constant, not a hardcoded string, for exactly that reason.
    expect(NEVER_REFRESHED).toBe('never refreshed')
  })
})

describe('formatAge', () => {
  it('reads null as NEVER_REFRESHED, never as an age of zero', () => {
    // The rule this line exists to pin: a sessionAge of null is a different
    // fact from a sessionAge of 0, and the two must not collapse to the same
    // string. A mutant that folds null into 0 (`seconds ?? 0`) dies here.
    expect(formatAge(null)).toBe(NEVER_REFRESHED)
  })

  it('renders whole seconds below a minute, including zero', () => {
    expect(formatAge(0)).toBe('0s')
    expect(formatAge(40)).toBe('40s')
    expect(formatAge(59)).toBe('59s')
  })

  it('floors fractional seconds below a minute rather than rounding', () => {
    expect(formatAge(40.9)).toBe('40s')
  })

  it('renders whole minutes, floored, at and above the 60s boundary', () => {
    expect(formatAge(60)).toBe('1m')
    expect(formatAge(89)).toBe('1m')
    expect(formatAge(90)).toBe('1m')
    expect(formatAge(119)).toBe('1m')
    expect(formatAge(120)).toBe('2m')
    expect(formatAge(239)).toBe('3m')
    expect(formatAge(240)).toBe('4m')
  })
})

// ---------------------------------------------------------------------------
// formatTemperature - rounded (not truncated), DASH for null.
// ---------------------------------------------------------------------------

describe('formatTemperature', () => {
  it("is DASH for null, and DASH is what null maps to, not a bare '48 °C'-shaped fallback", () => {
    expect(formatTemperature(null)).toBe(DASH)
  })

  it('renders a plain integer reading verbatim as "N °C"', () => {
    expect(formatTemperature(48)).toBe('48 °C')
  })

  it('rounds rather than truncates, in both directions', () => {
    // SPEC.md: "a temperature of 47.6 shown as 47 is a degree the unit never
    // reported". A truncating implementation fails the .6 case below; a
    // floor-only implementation also fails the negative case.
    expect(formatTemperature(47.6)).toBe('48 °C')
    expect(formatTemperature(47.4)).toBe('47 °C')
    expect(formatTemperature(47.5)).toBe('48 °C')
    expect(formatTemperature(-3.6)).toBe('-4 °C')
  })

  it('renders zero rather than treating it as absent', () => {
    // 0 is falsy in JS; a `value ? ... : DASH` mutant collapses this to DASH.
    expect(formatTemperature(0)).toBe('0 °C')
  })
})

// ---------------------------------------------------------------------------
// The battery is two rows, and that is a reversal (SPEC.md amended
// §4.5.1.1): a single combined row rendered `{percent: null, charging:
// false}` as unavailable even though the unit had said something. Each of
// formatBatteryPercent and formatCharging is one fact and dashes on its own
// null; neither reads the other's field at all, which is the property the
// tests below are for.
// ---------------------------------------------------------------------------

describe('formatBatteryPercent', () => {
  it('is DASH for null', () => {
    expect(formatBatteryPercent(null)).toBe(DASH)
  })

  it('rounds rather than truncates', () => {
    expect(formatBatteryPercent(83.5)).toBe('84%')
    expect(formatBatteryPercent(83.4)).toBe('83%')
  })

  it('renders 0% and 100% rather than treating either as a boundary special case', () => {
    // 0 is falsy in JS; a `percent ? ... : DASH`-shaped mutant collapses
    // this to DASH.
    expect(formatBatteryPercent(0)).toBe('0%')
    expect(formatBatteryPercent(100)).toBe('100%')
  })
})

describe('formatCharging', () => {
  it('is DASH for null: no provider has answered the charging question at all', () => {
    expect(formatCharging(null)).toBe(DASH)
  })

  it('renders "charging" for true and "on battery" for false, one word each and never the other\'s', () => {
    // SPEC.md's own worked case for splitting this into two rows:
    // {percent: null, charging: false} must render as "on battery", a real
    // answer, not collapse to DASH the way the combined row once did. A
    // mutant folding `false` into the DASH branch (`charging ? 'charging' :
    // DASH`) dies on the second assertion below.
    expect(formatCharging(true)).toBe('charging')
    expect(formatCharging(false)).toBe('on battery')
  })
})

// ---------------------------------------------------------------------------
// formatMode - the mode as the unit's own display writes it: AUTO, PASV,
// and MANU for MANUAL.
// ---------------------------------------------------------------------------

describe('formatMode', () => {
  it('renders AUTO and PASV unchanged', () => {
    expect(formatMode('AUTO')).toBe('AUTO')
    expect(formatMode('PASV')).toBe('PASV')
  })

  it('renders MANUAL as MANU, matching the word on the unit\'s own display', () => {
    expect(formatMode('MANUAL')).toBe('MANU')
  })

  it('is DASH for null: the plugin has no agent to ask, which is what a unit in manual mode gives it (issue #140), and a badge is not exempt from the ordinary dash rule', () => {
    expect(formatMode(null)).toBe(DASH)
  })
})

// ---------------------------------------------------------------------------
// formatGpsFix / formatGpsSource - two fields, not one (SPEC.md amended
// §4.5.1.1): "off" when gps.enabled is false, otherwise "fix"/"no fix"; the
// source name as it arrived, DASH for null, and "off" belongs only to the
// fix indicator, never to the source.
// ---------------------------------------------------------------------------

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
    updated: 1_700_000_000,
    ...overrides,
  }
}

describe('formatGpsFix', () => {
  it('reads "off" when gps.enabled is false, regardless of fix', () => {
    expect(formatGpsFix(gpsReading({ enabled: false, fix: false, source: null }))).toBe('off')
    // Even if fix were somehow true alongside enabled: false, "off" still
    // wins: nobody is looking is a fact about the source, not about whether
    // a fix happens to be cached.
    expect(formatGpsFix(gpsReading({ enabled: false, fix: true, source: null }))).toBe('off')
  })

  it('reads "no fix" when enabled but not fixed', () => {
    expect(formatGpsFix(gpsReading({ enabled: true, fix: false }))).toBe('no fix')
  })

  it('reads "fix" when enabled and fixed', () => {
    expect(formatGpsFix(gpsReading({ enabled: true, fix: true }))).toBe('fix')
  })
})

describe('formatGpsSource', () => {
  it('is DASH when the source is null, the ordinary dash rule and never "off"', () => {
    // SPEC.md: "off belongs to the fix indicator ... a source is a name,
    // and the absence of a name is the absence of a name." This is the
    // case a formatGpsSource that copies formatGpsFix's "off" branch gets
    // wrong: source: null with enabled: false must still be DASH here, not
    // "off", even though formatGpsFix on the same reading is "off".
    expect(formatGpsSource(gpsReading({ enabled: false, source: null }))).toBe(DASH)
  })

  it('renders the source name as it arrived when enabled', () => {
    expect(formatGpsSource(gpsReading({ enabled: true, source: 'gpsd' }))).toBe('gpsd')
    expect(formatGpsSource(gpsReading({ enabled: true, source: 'bettercap' }))).toBe('bettercap')
    expect(formatGpsSource(gpsReading({ enabled: true, source: 'browser' }))).toBe('browser')
  })
})

// ---------------------------------------------------------------------------
// formatChannel - the number as it arrived, DASH for null.
// ---------------------------------------------------------------------------

describe('formatChannel', () => {
  it('is DASH for null', () => {
    expect(formatChannel(null)).toBe(DASH)
  })

  it('renders the channel number unmodified', () => {
    expect(formatChannel(6)).toBe('6')
    expect(formatChannel(165)).toBe('165')
  })

  it('renders channel 0 rather than treating it as absent', () => {
    expect(formatChannel(0)).toBe('0')
  })
})

// ---------------------------------------------------------------------------
// formatUnitTime - local time of day, DASH for null, and specifically never
// a relative age, including for a timestamp in the phone's future.
//
// The exact rendering (24h "HH:MM" vs a locale 12h "H:MM AM/PM") is not
// pinned by SPEC.md, only that it is "the local time of day, hours and
// minutes". Hard-coding one shape would fail on the other in any timezone or
// Node/ICU configuration where the implementation legitimately chose the
// other, and hard-coding an exact clock string at all only ever passes in
// one timezone. So these tests extract an hour:minute pair from the output
// and check it against Date.getHours()/getMinutes() of the same instant,
// accepting either a 24h or a 12h-with-meridiem rendering.
// ---------------------------------------------------------------------------

// Finds the first "H:MM" or "HH:MM" run in the string and returns it parsed,
// along with whether the string appears to carry a 12-hour meridiem marker.
function extractClock(text: string): { hour: number; minute: number; hasMeridiem: boolean } {
  const match = /(\d{1,2}):(\d{2})/.exec(text)
  expect(match, `expected an "H:MM" clock time in ${JSON.stringify(text)}`).not.toBeNull()
  const [, h, m] = match as RegExpExecArray
  return {
    hour: Number(h),
    minute: Number(m),
    hasMeridiem: /am|pm/i.test(text),
  }
}

// True when the rendered hour is consistent with the Date's local hour,
// allowing for a 12-hour clock (where local hour 0 or 12 both render as 12,
// and 13-23 render as 1-11).
function hourMatches(rendered: { hour: number; hasMeridiem: boolean }, date: Date): boolean {
  const local = date.getHours()
  if (rendered.hour === local) return true
  if (rendered.hasMeridiem) {
    const twelveHour = local % 12 === 0 ? 12 : local % 12
    return rendered.hour === twelveHour
  }
  return false
}

describe('formatUnitTime', () => {
  it('is DASH for null', () => {
    expect(formatUnitTime(null)).toBe(DASH)
  })

  it('renders the local hour and minute of the given instant, not a relative age', () => {
    // A fixed epoch anchored at a whole minute so no boundary is crossed by
    // the local-vs-UTC offset arithmetic below.
    const epochSeconds = 1_700_000_460 // 2023-11-14T22:21:00Z, a whole minute
    const date = new Date(epochSeconds * 1000)

    const rendered = formatUnitTime(epochSeconds)

    // Never a duration or a relative phrase: no "ago", no leading "in ", no
    // bare seconds/minutes-style suffix the way formatAge/formatUptime do.
    expect(rendered.toLowerCase()).not.toContain('ago')
    expect(rendered.toLowerCase()).not.toMatch(/^in\s/)

    const clock = extractClock(rendered)
    expect(hourMatches(clock, date)).toBe(true)
    expect(clock.minute).toBe(date.getMinutes())
  })

  it('produces the same string for two instants in the same local minute', () => {
    // Proof that the output is derived from wall-clock hour/minute
    // components and not from some other epoch-based encoding: two instants
    // 10 seconds apart, both after :00 of the same minute, must render
    // identically.
    const base = 1_700_000_460 // aligned to a minute boundary
    expect(formatUnitTime(base)).toBe(formatUnitTime(base + 10))
    expect(formatUnitTime(base)).toBe(formatUnitTime(base + 45))
  })

  it('still renders a time of day, not a relative-future phrase, for a timestamp in the phone\'s future', () => {
    // SPEC.md: "A timestamp in the phone's future still renders its time of
    // day, which is the whole point of the rule above." A unit with no RTC
    // can stamp an event hours ahead of the phone's own clock; the rule this
    // test pins is that the string is still built the same way, not as
    // "in 4 hours".
    const futureEpochSeconds = Math.floor(Date.now() / 1000) + 4 * 3600 // 4h ahead
    const date = new Date(futureEpochSeconds * 1000)

    const rendered = formatUnitTime(futureEpochSeconds)

    expect(rendered.toLowerCase()).not.toContain('ago')
    expect(rendered.toLowerCase()).not.toMatch(/^in\s/)
    expect(rendered).not.toMatch(/^[+-]?\d+\s*h/i) // not a bare "4h"-style duration

    const clock = extractClock(rendered)
    expect(hourMatches(clock, date)).toBe(true)
    expect(clock.minute).toBe(date.getMinutes())
  })

  it('renders midnight (local hour 0) rather than treating it as absent', () => {
    // Find the next local midnight after a fixed anchor, deterministically,
    // rather than assuming the test host's own timezone lines up with a
    // hardcoded epoch. A `value ? ... : DASH`-shaped mutant on the whole
    // function is not reachable this way (formatUnitTime's null check is on
    // the argument, not on the numeric epoch value), but a formatter that
    // special-cases hour 0 as "no reading" would fail here.
    let probe = 1_700_000_000
    while (new Date(probe * 1000).getHours() !== 0) probe += 3600
    const date = new Date(probe * 1000)

    const rendered = formatUnitTime(probe)
    const clock = extractClock(rendered)
    expect(hourMatches(clock, date)).toBe(true)
    expect(clock.minute).toBe(date.getMinutes())
  })

  // SPEC.md 4.5.1.1 (amended): "It carries its date whenever it is not
  // today, and its year whenever it is not this year." Three tiers - bare
  // time, date with no year, date with year - and the boundaries between
  // them are exactly what the earlier version of this test, reasoning about
  // the real calendar via Date.now(), left unexercised for most of the
  // month: found "on a day of the month where it passed", per SPEC.md's own
  // account of the defect. The clock is faked here instead, so every
  // instant below is a fixed, chosen point relative to a fixed, chosen
  // "now", not a live one this suite would need to keep reasoning about.
  //
  // The day of August is deliberately a single digit, 5: SPEC.md pins "the
  // day is written without a leading zero" as "a real decision and not a
  // detail", and a padded "05" is indistinguishable from an unpadded "5"
  // under a `\b5\b`-style word-boundary check, which is the exact shape of
  // bug SPEC.md's own account describes. These tests match the literal
  // digit sequence instead, which a padded implementation fails outright.
  describe('the date/year tiers, against a faked clock', () => {
    afterEach(() => {
      vi.useRealTimers()
    })

    // "Now" is local noon on 15 August 2026. Local Date components are used
    // throughout so the fixture means the same thing in every timezone this
    // suite runs in, the same discipline the rest of this file holds to.
    function fakeNow(): void {
      vi.useFakeTimers()
      vi.setSystemTime(new Date(2026, 7, 15, 12, 0, 0))
    }

    it('renders the bare clock, no date and no year, for an instant today', () => {
      fakeNow()
      const today0914 = new Date(2026, 7, 15, 9, 14, 0)
      expect(formatUnitTime(Math.floor(today0914.getTime() / 1000))).toBe('09:14')
    })

    it('renders the unpadded day and month, no year, for an earlier instant this year', () => {
      fakeNow()
      const earlierThisYear = new Date(2026, 7, 5, 9, 14, 0) // 5 Aug 2026, same year as "now"
      expect(formatUnitTime(Math.floor(earlierThisYear.getTime() / 1000))).toBe('5 Aug 09:14')
    })

    it('renders the unpadded day, month and year for an instant in another year', () => {
      fakeNow()
      const lastAugust = new Date(2025, 7, 5, 9, 14, 0) // 5 Aug 2025, a year before "now"
      expect(formatUnitTime(Math.floor(lastAugust.getTime() / 1000))).toBe('5 Aug 2025 09:14')
    })

    it('does not zero-pad a double-digit day either: the rule is "no padding", not "pad to two only for single digits"', () => {
      fakeNow()
      const midMonthLastYear = new Date(2025, 7, 15, 9, 14, 0) // 15 Aug 2025
      expect(formatUnitTime(Math.floor(midMonthLastYear.getTime() / 1000))).toBe('15 Aug 2025 09:14')
    })

    it('still pads the hour and minute even where the day is bare', () => {
      fakeNow()
      const earlySingleDigitClock = new Date(2026, 7, 5, 3, 4, 0) // 5 Aug 2026, 03:04
      expect(formatUnitTime(Math.floor(earlySingleDigitClock.getTime() / 1000))).toBe('5 Aug 03:04')
    })

    it('treats "this year" by calendar year, not by a 365-day window: 1 Jan this year carries no year, 31 Dec last year does', () => {
      fakeNow()
      const jan1ThisYear = new Date(2026, 0, 1, 9, 14, 0)
      const dec31LastYear = new Date(2025, 11, 31, 9, 14, 0)
      // A mutant computing "this year" as "within 365 days of now" rather
      // than by calendar year would put 1 Jan (about seven and a half
      // months before the faked "now") in the no-year tier by coincidence,
      // but would mishandle a "now" nearer the turn of the year; asserted
      // directly against the calendar-year rule instead of relying on that
      // coincidence.
      expect(formatUnitTime(Math.floor(jan1ThisYear.getTime() / 1000))).toBe('1 Jan 09:14')
      expect(formatUnitTime(Math.floor(dec31LastYear.getTime() / 1000))).toBe('31 Dec 2025 09:14')
    })
  })
})

// ---------------------------------------------------------------------------
// formatNamedEvent - "<name> at <time of day>" for the last handshake and
// the last peer; the name alone when the timestamp is missing, the time
// alone when the name is; DASH when both are absent; the name passed
// through unchanged, hostile or not (SPEC.md 4.5.1.1, added for issue #121's
// "named, not merely timed" decision).
// ---------------------------------------------------------------------------

describe('formatNamedEvent', () => {
  it('is DASH when both the name and the timestamp are absent', () => {
    expect(formatNamedEvent(null, null)).toBe(DASH)
  })

  it('renders the name alone when the timestamp is missing', () => {
    expect(formatNamedEvent('TestNet_001', null)).toBe('TestNet_001')
  })

  it('renders the time alone when the name is missing', () => {
    const epochSeconds = 1_700_000_460
    const clock = extractClock(formatUnitTime(epochSeconds))
    const rendered = formatNamedEvent(null, epochSeconds)
    // The time-alone rendering must carry the same clock as formatUnitTime
    // would produce on its own: this function does not invent its own time
    // formatting rule for the name-absent case.
    expect(extractClock(rendered)).toEqual(clock)
  })

  it('combines the name and the time of day as "<name> at <time of day>" when both are present', () => {
    const epochSeconds = 1_700_000_460
    const rendered = formatNamedEvent('TestNet_001', epochSeconds)

    expect(rendered.startsWith('TestNet_001')).toBe(true)
    expect(rendered).toContain(' at ')
    // The time-of-day portion carries the same clock formatUnitTime would
    // produce for the same instant, wherever in the string it lands.
    expect(extractClock(rendered)).toEqual(extractClock(formatUnitTime(epochSeconds)))
  })

  it('passes a hostile name through unchanged rather than escaping or stripping it', () => {
    // SPEC.md: "The name is passed through unchanged, hostile or not:
    // escaping is the renderer's job and mangling it here would be a
    // second, quieter answer to §4.5.3." This function's contract is string
    // in, string out with no sanitisation; the view is what must render it
    // as text (pinned separately in dashboard.spec.ts).
    const hostileName = '<script>alert(1)</script>'
    expect(formatNamedEvent(hostileName, null)).toBe(hostileName)
    const withTime = formatNamedEvent(hostileName, 1_700_000_460)
    expect(withTime.startsWith(hostileName)).toBe(true)
  })

  it('treats an empty name as absent: a hidden network broadcasts no SSID, and that is a real capture', () => {
    // SPEC.md 4.5.1.1 (amended): "the time alone when the name is absent or
    // empty ... it must not render as ' at 12:04' with a leading space
    // where a name should be." An empty string is truthy-adjacent (`name
    // !== null` is true for ''), so a version of this function that only
    // checks for `null` renders exactly that leading-space defect; checking
    // for `''` too is the fix this test pins.
    const epochSeconds = 1_700_000_460
    const rendered = formatNamedEvent('', epochSeconds)
    expect(rendered).toBe(formatUnitTime(epochSeconds))
    expect(rendered.startsWith(' ')).toBe(false)
    expect(rendered).not.toContain(' at ')
  })

  it('is DASH for an empty name with no timestamp either, the same as a null name', () => {
    expect(formatNamedEvent('', null)).toBe(DASH)
  })
})

// ---------------------------------------------------------------------------
// "A value that is not a finite number is not known" (SPEC.md amended
// §4.5.1.1). Every function returns DASH for NaN, for either infinity, and
// for a negative duration; formatAge returns "never refreshed" for the same
// class of input, its own way of saying the same thing. None of this is
// reachable from a conformant plugin -- it is stated so a branch nobody
// specified is not a branch this suite leaves for coverage to find by
// accident.
//
// "Negative" is pinned as invalid only for a *duration* (uptime, age): a
// temperature or a channel is not a duration and SPEC.md's own worked
// example asserts a legitimate negative temperature (-3.6 -> "-4 °C",
// above), so negative is not tested as invalid for formatTemperature,
// formatChannel or formatBattery's percentage here -- only NaN and the two
// infinities, which the rule states without qualification for every
// function. An epoch second (formatUnitTime/formatNamedEvent's second
// argument) is not a duration either, so the same holds for it.
// ---------------------------------------------------------------------------

describe('non-finite and negative-duration input is treated as not known', () => {
  it('formatUptime: DASH for NaN, +Infinity, -Infinity, and any negative duration', () => {
    expect(formatUptime(NaN)).toBe(DASH)
    expect(formatUptime(Infinity)).toBe(DASH)
    expect(formatUptime(-Infinity)).toBe(DASH)
    expect(formatUptime(-1)).toBe(DASH)
    expect(formatUptime(-3600)).toBe(DASH)
  })

  it('formatAge: NEVER_REFRESHED for NaN, +Infinity, -Infinity and any negative duration, the same as null', () => {
    // This is the exact input class SPEC.md 4.5.1.1 warns the banner must
    // decide on by comparing against NEVER_REFRESHED rather than testing
    // `sessionAge === null`: a non-finite sessionAge is not null, but
    // formatAge still answers NEVER_REFRESHED for it.
    expect(formatAge(NaN)).toBe(NEVER_REFRESHED)
    expect(formatAge(Infinity)).toBe(NEVER_REFRESHED)
    expect(formatAge(-Infinity)).toBe(NEVER_REFRESHED)
    expect(formatAge(-1)).toBe(NEVER_REFRESHED)
  })

  it('formatTemperature: DASH for NaN and either infinity', () => {
    expect(formatTemperature(NaN)).toBe(DASH)
    expect(formatTemperature(Infinity)).toBe(DASH)
    expect(formatTemperature(-Infinity)).toBe(DASH)
  })

  it('formatChannel: DASH for NaN and either infinity', () => {
    expect(formatChannel(NaN)).toBe(DASH)
    expect(formatChannel(Infinity)).toBe(DASH)
    expect(formatChannel(-Infinity)).toBe(DASH)
  })

  it('formatBatteryPercent: DASH for a non-finite percentage', () => {
    expect(formatBatteryPercent(NaN)).toBe(DASH)
    expect(formatBatteryPercent(Infinity)).toBe(DASH)
    expect(formatBatteryPercent(-Infinity)).toBe(DASH)
  })

  it('formatUnitTime: DASH for NaN and either infinity', () => {
    expect(formatUnitTime(NaN)).toBe(DASH)
    expect(formatUnitTime(Infinity)).toBe(DASH)
    expect(formatUnitTime(-Infinity)).toBe(DASH)
  })

  it('formatNamedEvent: a non-finite timestamp is treated as absent, the same as null', () => {
    expect(formatNamedEvent('TestNet_001', NaN)).toBe('TestNet_001')
    expect(formatNamedEvent(null, NaN)).toBe(DASH)
    expect(formatNamedEvent('TestNet_001', Infinity)).toBe('TestNet_001')
  })
})

// ---------------------------------------------------------------------------
// formatConnectionState / formatUnauthorizedReason - SPEC.md 4.5.2.1: "The
// copy lives in lib/format.ts ... Pinning the sentences themselves is then
// possible and belongs with the formatter, in the way 4.5.1.1 pins the
// Dashboard's banner text." Both are total switches with no default arm
// over their respective closed enums (ConnectionState, UnauthorizedReason),
// so every branch is reachable and every one is asserted here individually:
// a case each, exact-matched, so a mutant merging or swapping two sentences
// cannot pass the way SPEC.md 4.5.1.1's own account of that defect for the
// Dashboard banner describes.
// ---------------------------------------------------------------------------

describe('formatConnectionState', () => {
  it.each([
    ['connecting', 'Connecting'],
    ['connected', 'Connected'],
    ['degraded', 'Connected (data is stale)'],
    ['offline', 'Offline'],
    ['unauthorized', 'Unauthorized'],
    ['restarting', 'Restarting'],
  ] as const)('%s -> %s', (state, expected) => {
    expect(formatConnectionState(state as ConnectionState)).toBe(expected)
  })

  it('gives every state its own sentence: no two states share one', () => {
    // A mutant that merges two branches (e.g. degraded returning the same
    // string as connected) survives an assertion that only checks each
    // sentence individually against its own pinned string if the two
    // pinned strings themselves happened to collide - they do not here,
    // and this test is what would catch the merge itself rather than
    // relying on that coincidence.
    const states: ConnectionState[] = ['connecting', 'connected', 'degraded', 'offline', 'unauthorized', 'restarting']
    const sentences = states.map((state) => formatConnectionState(state))
    expect(new Set(sentences).size).toBe(states.length)
  })
})

describe('formatUnauthorizedReason', () => {
  it.each([
    ['rejected', 'The unit refused the stored token.'],
    ['required', 'The unit requires a token and none is stored.'],
  ] as const)('%s -> %s', (reason, expected) => {
    expect(formatUnauthorizedReason(reason as UnauthorizedReason)).toBe(expected)
  })

  it('gives the two reasons different sentences: a swap or a merge both fail this', () => {
    // SPEC.md 4.5.1.1 records exactly this defect for the Dashboard's own
    // pair of unauthorized sentences: "swapping the two unauthorized arms
    // ... survived a test asserting only that both sentences mention a
    // token and differ from each other." This test intentionally goes
    // further than that weak shape by pinning each sentence to its exact
    // reason above; this second assertion only adds that a merge (both
    // reasons returning one shared string) is caught too.
    expect(formatUnauthorizedReason('rejected')).not.toBe(formatUnauthorizedReason('required'))
  })
})

describe('formatUnauthorizedCallToAction', () => {
  it.each([
    ['rejected', 'Fix it in Settings.'],
    ['required', 'Add it in Settings.'],
  ] as const)('%s -> %s', (reason, expected) => {
    expect(formatUnauthorizedCallToAction(reason as UnauthorizedReason)).toBe(expected)
  })

  it('gives the two reasons different calls to action: a swap or a merge both fail this', () => {
    expect(formatUnauthorizedCallToAction('rejected')).not.toBe(formatUnauthorizedCallToAction('required'))
  })
})

// ---------------------------------------------------------------------------
// SPEC.md 4.5.2.1 (amended): "Those two sentences are also half of the
// Dashboard's banner ... the banner is this sentence followed by its call
// to action ... One source, not two." This is the assertion the review
// asked for, and it matters more than either per-function table above: it
// pins that the composition of formatUnauthorizedReason and
// formatUnauthorizedCallToAction reproduces SPEC.md 4.5.1.1's own banner
// table byte for byte, which is what would catch the two copies drifting
// apart if a future edit changed one function's wording without the
// banner's composition (or the table itself) following. dashboard.spec.ts
// separately pins that Dashboard.svelte actually calls these two functions
// rather than composing its own copy; this test pins that the composition
// itself is correct, independent of who calls it.
// ---------------------------------------------------------------------------

describe('the Dashboard unauthorized banner is this composition (SPEC 4.5.1.1, 4.5.2.1)', () => {
  it.each([
    ['rejected', 'The unit refused the stored token. Fix it in Settings.'],
    ['required', 'The unit requires a token and none is stored. Add it in Settings.'],
  ] as const)('%s composes to the pinned banner sentence', (reason, pinnedBannerSentence) => {
    const composed = `${formatUnauthorizedReason(reason as UnauthorizedReason)} ${formatUnauthorizedCallToAction(reason as UnauthorizedReason)}`
    expect(composed).toBe(pinnedBannerSentence)
  })
})
