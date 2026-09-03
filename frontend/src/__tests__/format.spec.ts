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
  formatLastErrorCode,
  formatLastErrorMessage,
  formatLastErrorTime,
  formatLatency,
  formatMode,
  formatTemperature,
  formatUnauthorizedCallToAction,
  formatUnauthorizedReason,
  formatUnitTime,
  formatUptime,
  NEVER_REFRESHED,
} from '../lib/format'
import type { Gps } from '../lib/protocol'
import type { ConnectionState, LastError, UnauthorizedReason } from '../lib/ws'

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

  it("renders MANUAL as MANU, matching the word on the unit's own display", () => {
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
    expect(
      formatGpsFix(gpsReading({ enabled: false, fix: false, source: null })),
    ).toBe('off')
    // Even if fix were somehow true alongside enabled: false, "off" still
    // wins: nobody is looking is a fact about the source, not about whether
    // a fix happens to be cached.
    expect(
      formatGpsFix(gpsReading({ enabled: false, fix: true, source: null })),
    ).toBe('off')
  })

  it('reads "no fix" when enabled but not fixed', () => {
    expect(formatGpsFix(gpsReading({ enabled: true, fix: false }))).toBe(
      'no fix',
    )
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
    expect(formatGpsSource(gpsReading({ enabled: false, source: null }))).toBe(
      DASH,
    )
  })

  it('renders the source name as it arrived when enabled', () => {
    expect(formatGpsSource(gpsReading({ enabled: true, source: 'gpsd' }))).toBe(
      'gpsd',
    )
    expect(
      formatGpsSource(gpsReading({ enabled: true, source: 'bettercap' })),
    ).toBe('bettercap')
    expect(
      formatGpsSource(gpsReading({ enabled: true, source: 'browser' })),
    ).toBe('browser')
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
function extractClock(text: string): {
  hour: number
  minute: number
  hasMeridiem: boolean
} {
  const match = /(\d{1,2}):(\d{2})/.exec(text)
  expect(
    match,
    `expected an "H:MM" clock time in ${JSON.stringify(text)}`,
  ).not.toBeNull()
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
function hourMatches(
  rendered: { hour: number; hasMeridiem: boolean },
  date: Date,
): boolean {
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

  it("still renders a time of day, not a relative-future phrase, for a timestamp in the phone's future", () => {
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
      expect(formatUnitTime(Math.floor(today0914.getTime() / 1000))).toBe(
        '09:14',
      )
    })

    it('renders the unpadded day and month, no year, for an earlier instant this year', () => {
      fakeNow()
      const earlierThisYear = new Date(2026, 7, 5, 9, 14, 0) // 5 Aug 2026, same year as "now"
      expect(formatUnitTime(Math.floor(earlierThisYear.getTime() / 1000))).toBe(
        '5 Aug 09:14',
      )
    })

    it('renders the unpadded day, month and year for an instant in another year', () => {
      fakeNow()
      const lastAugust = new Date(2025, 7, 5, 9, 14, 0) // 5 Aug 2025, a year before "now"
      expect(formatUnitTime(Math.floor(lastAugust.getTime() / 1000))).toBe(
        '5 Aug 2025 09:14',
      )
    })

    it('does not zero-pad a double-digit day either: the rule is "no padding", not "pad to two only for single digits"', () => {
      fakeNow()
      const midMonthLastYear = new Date(2025, 7, 15, 9, 14, 0) // 15 Aug 2025
      expect(
        formatUnitTime(Math.floor(midMonthLastYear.getTime() / 1000)),
      ).toBe('15 Aug 2025 09:14')
    })

    it('still pads the hour and minute even where the day is bare', () => {
      fakeNow()
      const earlySingleDigitClock = new Date(2026, 7, 5, 3, 4, 0) // 5 Aug 2026, 03:04
      expect(
        formatUnitTime(Math.floor(earlySingleDigitClock.getTime() / 1000)),
      ).toBe('5 Aug 03:04')
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
      expect(formatUnitTime(Math.floor(jan1ThisYear.getTime() / 1000))).toBe(
        '1 Jan 09:14',
      )
      expect(formatUnitTime(Math.floor(dec31LastYear.getTime() / 1000))).toBe(
        '31 Dec 2025 09:14',
      )
    })
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
// function. An epoch second (formatUnitTime's own argument) is not a
// duration either, so the same holds for it.
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
    const states: ConnectionState[] = [
      'connecting',
      'connected',
      'degraded',
      'offline',
      'unauthorized',
      'restarting',
    ]
    const sentences = states.map((state) => formatConnectionState(state))
    expect(new Set(sentences).size).toBe(states.length)
  })
})

describe('formatUnauthorizedReason', () => {
  it.each([
    ['rejected', 'The unit refused the stored token.'],
    ['required', 'The unit requires a token and none is stored.'],
  ] as const)('%s -> %s', (reason, expected) => {
    expect(formatUnauthorizedReason(reason as UnauthorizedReason)).toBe(
      expected,
    )
  })

  it('gives the two reasons different sentences: a swap or a merge both fail this', () => {
    // SPEC.md 4.5.1.1 records exactly this defect for the Dashboard's own
    // pair of unauthorized sentences: "swapping the two unauthorized arms
    // ... survived a test asserting only that both sentences mention a
    // token and differ from each other." This test intentionally goes
    // further than that weak shape by pinning each sentence to its exact
    // reason above; this second assertion only adds that a merge (both
    // reasons returning one shared string) is caught too.
    expect(formatUnauthorizedReason('rejected')).not.toBe(
      formatUnauthorizedReason('required'),
    )
  })
})

describe('formatUnauthorizedCallToAction', () => {
  it.each([
    ['rejected', 'Fix it in Settings.'],
    ['required', 'Add it in Settings.'],
  ] as const)('%s -> %s', (reason, expected) => {
    expect(formatUnauthorizedCallToAction(reason as UnauthorizedReason)).toBe(
      expected,
    )
  })

  it('gives the two reasons different calls to action: a swap or a merge both fail this', () => {
    expect(formatUnauthorizedCallToAction('rejected')).not.toBe(
      formatUnauthorizedCallToAction('required'),
    )
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
    [
      'required',
      'The unit requires a token and none is stored. Add it in Settings.',
    ],
  ] as const)(
    '%s composes to the pinned banner sentence',
    (reason, pinnedBannerSentence) => {
      const composed = `${formatUnauthorizedReason(reason as UnauthorizedReason)} ${formatUnauthorizedCallToAction(reason as UnauthorizedReason)}`
      expect(composed).toBe(pinnedBannerSentence)
    },
  )
})

// ---------------------------------------------------------------------------
// SPEC.md 4.3.10 (amended, issue #176): formatLatency, formatLastErrorMessage,
// formatLastErrorCode, formatLastErrorTime. None of these four is in a
// signature table anywhere in SPEC.md - 4.3.10 only pins the rules the
// rendered output must follow, not the functions' own argument shapes. Every
// other formatter in this file takes exactly one primitive named after what
// it formats (formatChannel(channel), formatBatteryPercent(percent), ...),
// so that was the starting assumption for all four: formatLatency(latencyMs),
// formatLastErrorMessage(message), formatLastErrorCode(code),
// formatLastErrorTime(at). `tsc --noEmit` (run without reading lib/format.ts
// itself) rejected that guess for formatLastErrorCode specifically - it takes
// the whole `LastError`, not a bare code, presumably because "a close code is
// shown, a wire code is not" needs `source` too - and that one signature was
// corrected from the compiler's diagnostic alone; the other three type-check
// as single-primitive-argument functions and are left on that assumption,
// unverified beyond that.
// ---------------------------------------------------------------------------

describe('formatLatency', () => {
  it('is DASH for null - no measurement yet, following this file\'s own "not known" convention', () => {
    expect(formatLatency(null)).toBe(DASH)
  })

  it('renders exact whole milliseconds with unit - "123 ms" (SPEC 4.3.10: "not rounded to seconds")', () => {
    expect(formatLatency(123)).toBe('123 ms')
  })

  it('renders 0ms as "0 ms", not as absent: SPEC 4.3.10 - "a round trip that completed inside the clock\'s resolution, not an absent value"', () => {
    expect(formatLatency(0)).toBe('0 ms')
  })

  it("does not round to seconds even for a duration under a second's worth of milliseconds either way", () => {
    expect(formatLatency(999)).toBe('999 ms')
    expect(formatLatency(1000)).toBe('1000 ms')
  })
})

describe('formatLastErrorTime', () => {
  afterEach(() => {
    vi.useRealTimers()
  })

  it("renders a bare time of day for a stamp made today, on the phone's own clock", () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date(2026, 7, 15, 12, 0, 0)) // "now": local noon, 15 Aug 2026
    const stampToday = new Date(2026, 7, 15, 9, 14, 0).getTime()

    const rendered = formatLastErrorTime(stampToday)
    const match = /(\d{1,2}):(\d{2})/.exec(rendered)
    expect(
      match,
      `expected an "H:MM" clock time in ${JSON.stringify(rendered)}`,
    ).not.toBeNull()
    const [, h, m] = match as RegExpExecArray
    expect(Number(h)).toBe(9)
    expect(m).toBe('14')
  })

  // SPEC 4.3.10 (amended): "A stamp from another day says so ... a bare
  // HH:MM then reads as something that happened today." Read on the day
  // after it was recorded, the render must differ from the same stamp read
  // on the day it happened - the property, not a specific format, since the
  // exact way the date is appended is not pinned here (SPEC 4.5.1.1's own
  // formatUnitTime table above is the only place a literal date format is
  // pinned, and that pin is explicitly not this function - "it is not
  // formatUnitTime").
  it('a stamp from another day renders differently from the same stamp read on the day it happened', () => {
    const stamp = new Date(2026, 7, 14, 23, 59, 0).getTime() // 23:59, 14 Aug 2026

    vi.useFakeTimers()
    vi.setSystemTime(new Date(2026, 7, 14, 23, 59, 30)) // read 30s later, same day
    const sameDayText = formatLastErrorTime(stamp)
    vi.useRealTimers()

    vi.useFakeTimers()
    vi.setSystemTime(new Date(2026, 7, 15, 0, 1, 0)) // read just after midnight, the next day
    const nextDayText = formatLastErrorTime(stamp)
    vi.useRealTimers()

    expect(nextDayText).not.toBe(sameDayText)
  })

  it('is a pure function of the stamp within the same calendar day: two reads of the same stamp on the same day render identically', () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date(2026, 7, 15, 9, 0, 0))
    const stamp = new Date(2026, 7, 15, 8, 30, 0).getTime()

    const first = formatLastErrorTime(stamp)
    vi.setSystemTime(new Date(2026, 7, 15, 18, 0, 0)) // ten hours later, same day
    const second = formatLastErrorTime(stamp)

    expect(second).toBe(first)
  })
})

describe('formatLastErrorCode', () => {
  // `tsc --noEmit` rejected the bare-code-argument guess this block
  // originally used ("Argument of type 'string' is not assignable to
  // parameter of type 'LastError'") - the compiler's own diagnostic, not a
  // read of lib/format.ts's body, corrected the assumption to the whole
  // `LastError` to this shape instead. `source` is still needed on every
  // fixture below because the "close code is shown, a wire code is not"
  // rule (SPEC 4.3.10) means the function's output depends on it, not on
  // `code` alone.
  function frameLastError(code: string): LastError {
    return { source: 'frame', code, message: '', at: 1_700_000_000_000 }
  }

  // SPEC 4.3.10: "pasv_unavailable ... reuse the sentence they already
  // carry where §4.3.5's table sends them" - the PASV control's own table
  // pins this one verbatim, and it is a single sentence with no
  // call-to-action clause, unlike unauthorized's reused banner text (which
  // is two sentences and not asserted here for that reason).
  it('pasv_unavailable renders the sentence pinned for it elsewhere, not the raw code', () => {
    const rendered = formatLastErrorCode(frameLastError('pasv_unavailable'))
    expect(rendered).toBe('The unit does not have the PASV plugin.')
    expect(rendered).not.toContain('pasv_unavailable')
  })

  // SPEC 4.3.10's own list names five pinned codes; the suite only pinned
  // one of them (pasv_unavailable) at the formatter level. A mutant deleting
  // either of these two `case`s and letting it fall through to the generic
  // sentence was unverified - the sentences are the PASV control's own
  // table entries, reused verbatim.
  it('pasv_requires_auto renders the sentence pinned for it on the PASV control, not the raw code', () => {
    const rendered = formatLastErrorCode(frameLastError('pasv_requires_auto'))
    expect(rendered).toBe('PASV is reachable only from AUTO.')
    expect(rendered).not.toContain('pasv_requires_auto')
  })

  it('no_frame renders the sentence pinned for it on the Mirror control, not the raw code', () => {
    const rendered = formatLastErrorCode(frameLastError('no_frame'))
    expect(rendered).toBe('The unit has not drawn a frame yet.')
    expect(rendered).not.toContain('no_frame')
  })

  // SPEC 4.3.10: "log_unavailable carries two [sentences], because the Log
  // view needs the second one ... Reusing it here means this line carries
  // two as well, which is the price of the rule that one code is worded
  // once." Pinned in full, trailing full stop included: formatLastErrorCode
  // returns the sentence as the Log view's own table carries it: stripping
  // the trailing stop for the composed line is the View's job (settings-view
  // .spec.ts pins that half), not this formatter's.
  it('log_unavailable renders both of its sentences verbatim, not just the first', () => {
    const rendered = formatLastErrorCode(frameLastError('log_unavailable'))
    expect(rendered).toBe(
      'The unit could not read its log. With no agent it has no configuration to find the path in.',
    )
    expect(rendered).not.toContain('log_unavailable')
  })

  it("an unrecognised code never leaks through verbatim (SPEC 4.5.2.2's rule already required of the command path)", () => {
    const distinctive = 'zzz_unmapped_diagnostic_code_xyz'
    expect(formatLastErrorCode(frameLastError(distinctive))).not.toContain(
      distinctive,
    )
  })

  it('two different unrecognised codes render the identical generic sentence - the generic case must not quietly start printing the code again', () => {
    const first = formatLastErrorCode(
      frameLastError('totally_unrecognised_code_alpha'),
    )
    const second = formatLastErrorCode(
      frameLastError('completely_different_unrecognised_beta'),
    )
    expect(second).toBe(first)
  })

  it('a pinned code renders a sentence distinct from the generic one an unmapped code gets', () => {
    const pinned = formatLastErrorCode(frameLastError('pasv_unavailable'))
    const generic = formatLastErrorCode(
      frameLastError('an_unmapped_diagnostic_code_one'),
    )
    expect(pinned).not.toBe(generic)
  })

  // SPEC 4.3.10 (amended, issue #176): the generic sentence is what a
  // non-string `message` composes with when its code is unmapped (e.g.
  // `internal_error`), so settings-view.spec.ts's non-string-message DOM
  // test relies on this sentence itself containing none of the literal
  // substrings a coercion bug would print. Asserted once here at the source
  // rather than left to the DOM test to happen not to trip over: a reworded
  // generic sentence that introduced one of these substrings would make that
  // DOM test pass for the wrong reason (the substring belonging to the
  // sentence, not to a coerced non-string message) without this test to
  // catch the sentence itself.
  it('the generic sentence contains none of the literal substrings a coercion bug would print', () => {
    const generic = formatLastErrorCode(
      frameLastError('an_unmapped_diagnostic_code_two'),
    )
    expect(generic).not.toContain('null')
    expect(generic).not.toContain('undefined')
    expect(generic).not.toContain('[object')
  })

  // SPEC 4.3.10: "A close code is shown, and a wire code is not ... the
  // close case renders the number beside its sentence; the frame case
  // renders the sentence alone." settings-view.spec.ts already pins this at
  // the DOM level; asserted here too now that the function is known to take
  // the whole LastError and therefore can make the distinction itself.
  it('a close renders its numeric code, a frame with the same digits as its code does not', () => {
    const close = formatLastErrorCode({
      source: 'close',
      code: '1006',
      message: '',
      at: 1_700_000_000_000,
    })
    const frame = formatLastErrorCode(frameLastError('1006'))
    expect(close).toContain('1006')
    expect(frame).not.toContain('1006')
  })

  // SPEC 4.3.10 (amended, issue #176): "unauthorized reuses the reason it
  // already carries and not the call to action beside it: 'Fix it in
  // Settings' is a sentence for the screen that sends the reader somewhere,
  // and this line is already on the screen it would send them to." A frame
  // with code `unauthorized` is, by construction, always the *rejected*
  // reason and never *required*: §4.3.1 draws the *required* reading only
  // from a bare close with no error frame at all (a client with no token
  // sends nothing and is closed on the auth timeout), or from three
  // consecutive closes with none of them carrying an `error` frame, so a
  // `source: 'frame', code: 'unauthorized'` LastError can only have arisen
  // from the plugin answering an `auth` it received and refused - the
  // rejected branch. `formatUnauthorizedReason`/`formatUnauthorizedCallToAction`
  // are the same two functions the Dashboard banner composition test above
  // pins against SPEC 4.5.1.1's table, so this asserts the diagnostics line
  // reuses exactly one of that pair's two halves and not the other.
  it('unauthorized reuses only the reason, never the call to action beside it', () => {
    // `formatUnauthorizedReason('rejected')` and not `('required')`: this is
    // drawn from §4.3.1's prose, not assumed. §4.3.1 draws the *required*
    // reading only from a bare close with no error frame at all, or from
    // three consecutive closes with none carrying an `error` frame - never
    // from a frame that arrived. A `source: 'frame', code: 'unauthorized'`
    // LastError can therefore only have arisen from the plugin answering an
    // `auth` it received and refused, which is *rejected* by construction.
    // Do not weaken this back to 'required' or to "excludes the call to
    // action" - the reason side is a specific, derived fact, not a guess.
    const rendered = formatLastErrorCode(frameLastError('unauthorized'))
    expect(rendered).toBe(formatUnauthorizedReason('rejected'))
    expect(rendered).not.toContain(formatUnauthorizedCallToAction('rejected'))
    expect(rendered).not.toContain('Fix it in Settings')
  })

  // SPEC 4.3.10 (resolved, issue #176): "Each of the two gets its own
  // sentence, and the five-code table above does not govern them ... They
  // must not collapse into each other or into the generic sentence, because
  // they are two different failures - a link that was working and went
  // away, against one that never came up ... Neither shows its code: unlike
  // a close code, these are names this app chose, and printing an
  // identifier the owner has no way to look up is not a diagnostic." The
  // five-code table (unauthorized, pasv_requires_auto, pasv_unavailable,
  // log_unavailable, no_frame) governs codes the wire carries, so that a
  // code worn by another screen is worded once; `pong_timeout` and
  // `connect_timeout` came from this client and no screen has ever worded
  // them, so §4.3.10 words them itself, distinctly from each other and from
  // the generic sentence an unrecognised code gets. No fixture anywhere in
  // this suite built a `source: 'local'` LastError before this change, so an
  // implementation that collapsed both into the generic sentence, collapsed
  // them into each other, leaked the raw code, threw on the unfamiliar
  // `source`, or rendered nothing passed regardless.
  it('pong_timeout (source "local") says the link stopped responding, and never leaks the raw code', () => {
    const rendered = formatLastErrorCode({
      source: 'local',
      code: 'pong_timeout',
      message: '',
      at: 1_700_000_000_000,
    })
    expect(rendered.trim().length).toBeGreaterThan(0)
    expect(rendered).not.toContain('pong_timeout')
    expect(rendered.toLowerCase()).toContain('stopped responding')
  })

  it('connect_timeout (source "local") says the unit did not answer while connecting, and never leaks the raw code', () => {
    const rendered = formatLastErrorCode({
      source: 'local',
      code: 'connect_timeout',
      message: '',
      at: 1_700_000_000_000,
    })
    expect(rendered.trim().length).toBeGreaterThan(0)
    expect(rendered).not.toContain('connect_timeout')
    expect(rendered.toLowerCase()).toContain('did not answer')
  })

  // The two must not collapse into each other, and neither may collapse
  // into the generic sentence an unrecognised code gets - "a link that was
  // working and went away, against one that never came up ... the
  // difference is most of what the reader is on this screen to learn."
  it('pong_timeout and connect_timeout render different sentences from each other and from the generic case', () => {
    const pongTimeout = formatLastErrorCode({
      source: 'local',
      code: 'pong_timeout',
      message: '',
      at: 1_700_000_000_000,
    })
    const connectTimeout = formatLastErrorCode({
      source: 'local',
      code: 'connect_timeout',
      message: '',
      at: 1_700_000_000_000,
    })
    const generic = formatLastErrorCode(
      frameLastError('an_unmapped_diagnostic_code_three'),
    )
    expect(pongTimeout).not.toBe(connectTimeout)
    expect(pongTimeout).not.toBe(generic)
    expect(connectTimeout).not.toBe(generic)
  })

  // SPEC 4.3.10 (issue #122): `socket_failed` is the third local code, and
  // the one about the phone rather than the unit - the browser refused to
  // open the connection before there was anything on the other end to fail
  // to answer. §4.3.10's comment on this pins the exact sentence: "an owner
  // told the unit is not answering will go and check the unit instead",
  // which is why this case gets its own wording rather than sharing
  // connect_timeout's. Pinned verbatim, not just by substring, because a
  // review found this sentence untested against the whole suite: deleting
  // the case arm and falling through to the generic sentence was caught by
  // nothing before this test existed.
  it('socket_failed (source "local") renders the exact pinned sentence, distinct from the other two local codes', () => {
    const socketFailed = formatLastErrorCode({
      source: 'local',
      code: 'socket_failed',
      message: '',
      at: 1_700_000_000_000,
    })
    expect(socketFailed).toBe('The browser refused to open the connection.')
    expect(socketFailed).not.toContain('socket_failed')

    const pongTimeout = formatLastErrorCode({
      source: 'local',
      code: 'pong_timeout',
      message: '',
      at: 1_700_000_000_000,
    })
    const connectTimeout = formatLastErrorCode({
      source: 'local',
      code: 'connect_timeout',
      message: '',
      at: 1_700_000_000_000,
    })
    const generic = formatLastErrorCode(
      frameLastError('an_unmapped_diagnostic_code_four'),
    )
    expect(socketFailed).not.toBe(pongTimeout)
    expect(socketFailed).not.toBe(connectTimeout)
    expect(socketFailed).not.toBe(generic)
  })

  // SPEC 4.3.11 (issue #109): `bad_frame` is the fourth local code, and the
  // only one about the unit's own words rather than about the link or the
  // phone -- a frame arrived and failed the boundary guard, so this client
  // has no idea what the unit meant. Pinned verbatim, the same as
  // `socket_failed` above and for the same reason: a case arm falling
  // through to the generic sentence, or being worded to sound like a link
  // failure instead of an unreadable one, is a defect this file catches
  // only if the exact string is asserted.
  it('bad_frame (source "local") renders the exact pinned sentence, distinct from the other three local codes', () => {
    const badFrame = formatLastErrorCode({
      source: 'local',
      code: 'bad_frame',
      message: '',
      at: 1_700_000_000_000,
    })
    expect(badFrame).toBe('The unit sent something this app could not read.')
    expect(badFrame).not.toContain('bad_frame')

    const pongTimeout = formatLastErrorCode({
      source: 'local',
      code: 'pong_timeout',
      message: '',
      at: 1_700_000_000_000,
    })
    const connectTimeout = formatLastErrorCode({
      source: 'local',
      code: 'connect_timeout',
      message: '',
      at: 1_700_000_000_000,
    })
    const socketFailed = formatLastErrorCode({
      source: 'local',
      code: 'socket_failed',
      message: '',
      at: 1_700_000_000_000,
    })
    const generic = formatLastErrorCode(
      frameLastError('an_unmapped_diagnostic_code_five'),
    )
    expect(badFrame).not.toBe(pongTimeout)
    expect(badFrame).not.toBe(connectTimeout)
    expect(badFrame).not.toBe(socketFailed)
    expect(badFrame).not.toBe(generic)
  })
})

describe('formatLastErrorMessage', () => {
  it('passes a short message through unchanged', () => {
    expect(formatLastErrorMessage('token rejected')).toBe('token rejected')
  })

  it('passes an empty message through as the empty string, not DASH - SPEC 4.3.10: "the empty string when it did not" carry one', () => {
    expect(formatLastErrorMessage('')).toBe('')
  })

  // SPEC 4.3.10 (amended, issue #176): "The bound is 200 characters, counted
  // in code points and not in UTF-16 units ... The number is not load-bearing
  // and no behaviour depends on its exact value; it is written down here so
  // that a test can assert it instead of asserting that some bound exists,
  // which is an assertion a missing bound also satisfies." Previously this
  // suite could only assert "some bound under 1000", which a missing bound
  // also happened to satisfy at the 50,000-character fixture used then. The
  // number is now pinned, so the exact code-point count is asserted instead.
  //
  // SPEC 4.3.10 (amended, issue #176): "A message that was cut says it was
  // cut, with `...` after the two hundredth code point and only when
  // something was actually removed ... a truncated line carries 203 code
  // points." A message this far over the bound is unambiguously truncated,
  // so the marker is expected here.
  it('bounds a very long message to 200 content code points plus the "..." marker, not merely to some smaller number', () => {
    const hostileLength = 50_000
    const veryLongMessage = 'x'.repeat(hostileLength)
    const rendered = formatLastErrorMessage(veryLongMessage)
    expect(Array.from(rendered).length).toBe(203)
    expect(rendered).toBe(`${'x'.repeat(200)}...`)
  })

  it('a message at a modest, unremarkable length is not truncated and carries no marker', () => {
    const ordinaryMessage = 'x'.repeat(120)
    expect(formatLastErrorMessage(ordinaryMessage)).toBe(ordinaryMessage)
  })

  // This is the edge that distinguishes ">" from ">=" in the truncation
  // condition: a message at exactly the bound was not cut, so it must pass
  // through byte-identical and unmarked. An implementation that truncates
  // (or appends "...") at ">= 200" instead of "> 200" fails only here -
  // every other test in this block uses a length far enough from the
  // boundary that both readings agree.
  it('a message of exactly 200 code points passes through unchanged and unmarked - the bound is inclusive', () => {
    const exactly200 = 'x'.repeat(200)
    const rendered = formatLastErrorMessage(exactly200)
    expect(rendered).toBe(exactly200)
    expect(rendered).not.toContain('...')
  })

  it('a message of 201 code points, one over the bound, is truncated to 200 content code points plus the marker', () => {
    const exactly201 = 'x'.repeat(201)
    const rendered = formatLastErrorMessage(exactly201)
    expect(Array.from(rendered).length).toBe(203)
    expect(rendered).toBe(`${'x'.repeat(200)}...`)
  })

  // SPEC 4.3.10: "so that a truncation can never cut a surrogate pair in
  // half and leave a lone half to render as a replacement character." An
  // astral character (U+1F600, a surrogate pair - two UTF-16 code units but
  // one code point) is placed so that it occupies UTF-16 units 200 and 201:
  // 199 BMP characters precede it, then padding follows so the message is
  // long enough to be truncated at all. A truncation that counts UTF-16
  // units (e.g. `String.prototype.slice(0, 200)`) grabs units 1-200, which
  // is the 199 BMP characters plus only the astral character's leading
  // surrogate - a lone half. A truncation that counts code points keeps the
  // astral character whole as the 200th code point. `Array.from` iterates
  // by code point and would report length 200 either way (it treats an
  // unpaired surrogate as one element too), so the count alone cannot tell
  // the two readings apart - only the content of the 200th code point can.
  // The message is truncated (299 code points over the bound), so the "..."
  // marker is expected after the astral character, not in place of it.
  it('does not split an astral character straddling the 200-code-point boundary in UTF-16 units', () => {
    const astral = '\u{1F600}' // one code point, two UTF-16 units
    const prefix = 'a'.repeat(199)
    const suffix = 'b'.repeat(300)
    const rendered = formatLastErrorMessage(prefix + astral + suffix)
    const codePoints = Array.from(rendered)
    expect(codePoints.length).toBe(203)
    expect(codePoints[199]).toBe(astral)
    expect(rendered).toBe(`${prefix}${astral}...`)
  })

  // SPEC 4.3.10 (amended, issue #176): "A message that is not a string has
  // no message ... Coercing it prints null, undefined or [object Object] on
  // the line, attributed to the unit, which is worse than saying nothing
  // ... So a non-string is treated as absent." Previously this suite only
  // asserted "does not throw, returns a string", which a coercing
  // implementation (`String(message)`, printing the literal word "null")
  // also satisfies - `typeof "null"` is `'string'` too. The reading is now
  // pinned: a non-string collapses to the same empty-string "no message"
  // value the wire's own empty string already produces (asserted above),
  // not to a stringified representation of whatever the wire actually sent.
  it.each([
    ['a number', 12345],
    ['null', null],
    ['undefined', undefined],
    ['a boolean', true],
    [
      'an array (the audit finding: an array-valued message reached the screen)',
      ['unexpected', 'array', 'payload'],
    ],
    ['a plain object', { unexpected: 'object payload' }],
  ] as const)(
    'a %s message is treated as absent, not coerced to its string form',
    (_label, notAString) => {
      const rendered = formatLastErrorMessage(notAString as unknown as string)
      expect(rendered).toBe('')
    },
  )

  it('does not throw when message is not actually a string at runtime, and still returns a string', () => {
    const notAString = 12345 as unknown as string
    expect(() => formatLastErrorMessage(notAString)).not.toThrow()
    expect(typeof formatLastErrorMessage(notAString)).toBe('string')
  })
})
