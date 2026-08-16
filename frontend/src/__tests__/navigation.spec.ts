import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

// The navigation shell of SPEC 4.5, tested from the specification only.
//
// SPEC 10.5 asks this file to exist because 10.7 excuses view components from
// the coverage gate and that exclusion "was written for list markup and does not
// hold for navigation". What is pinned here is therefore only what 4.5 states:
// the seven routes, anchors rather than tabs, aria-current including the More
// slot standing in for a sheet-hosted view, the sheet's dialog obligations, the
// rail media query, and views surviving navigation.
//
// Two seams this file needs that SPEC does not pin, both reported rather than
// smuggled in:
//
//   1. `data-view="<name>"` on the root element of each mounted view, with the
//      names below. SPEC names the seven views and requires them to stay
//      mounted, but gives no way to tell one mounted view from another in the
//      DOM. Some hook is unavoidable for that requirement to be testable.
//   2. The focus trap is exercised by dispatching a Tab keydown, because jsdom
//      does not implement sequential focus navigation. An implementation that
//      traps focus by intercepting Tab passes; one that relies on the browser's
//      native behaviour cannot be tested here at all.

// Svelte is imported dynamically alongside the shell, never statically: each
// render resets the module registry so the shell starts from a clean graph, and
// a runtime imported before that reset belongs to the previous graph.
type SvelteApi = typeof import('svelte')

const RAIL_QUERY = '(orientation: landscape) and (max-height: 560px)'

// SPEC 4.5, the route table. The Wi-Fi segment is deliberately absent from
// every URL: it is view state, not a location.
const ROUTES = [
  { path: '/', view: 'dashboard', entry: 'Dashboard' },
  { path: '/wifi', view: 'wifi', entry: 'Wi-Fi' },
  { path: '/map', view: 'map', entry: 'Map' },
  { path: '/log', view: 'log', entry: 'Log' },
  { path: '/peers', view: 'peers', entry: 'More' },
  { path: '/mirror', view: 'mirror', entry: 'More' },
  { path: '/settings', view: 'settings', entry: 'More' },
] as const

// The five slots of the bottom bar, in order (SPEC 4.5).
const BAR_ENTRIES = ['Dashboard', 'Wi-Fi', 'Map', 'Log', 'More'] as const

// The three views the More sheet holds (SPEC 4.5).
const SHEET_ENTRIES = ['Peers', 'Mirror', 'Settings'] as const

// --- media query mock -------------------------------------------------------

interface ChangeLike {
  matches: boolean
  media: string
}

class FakeMediaQueryList {
  media: string
  matches = false
  onchange: ((event: ChangeLike) => void) | null = null
  private listeners = new Set<(event: ChangeLike) => void>()

  constructor(media: string) {
    this.media = media
  }

  addEventListener(type: string, listener: (event: ChangeLike) => void): void {
    if (type === 'change') {
      this.listeners.add(listener)
    }
  }

  removeEventListener(type: string, listener: (event: ChangeLike) => void): void {
    if (type === 'change') {
      this.listeners.delete(listener)
    }
  }

  // The legacy pair is still what Safari shipped for years; an implementation
  // that reaches for it is not wrong, so the mock answers both.
  addListener(listener: (event: ChangeLike) => void): void {
    this.listeners.add(listener)
  }

  removeListener(listener: (event: ChangeLike) => void): void {
    this.listeners.delete(listener)
  }

  set(matches: boolean): void {
    if (this.matches === matches) {
      return
    }
    this.matches = matches
    const event: ChangeLike = { matches, media: this.media }
    this.onchange?.(event)
    for (const listener of [...this.listeners]) {
      listener(event)
    }
  }
}

function normaliseQuery(query: string): string {
  return query.toLowerCase().replace(/\s+/g, ' ').trim()
}

let matchMediaCalls: string[] = []
let mediaLists: FakeMediaQueryList[] = []
let railMatches = false

function installMatchMedia(): void {
  matchMediaCalls = []
  mediaLists = []
  window.matchMedia = ((query: string) => {
    matchMediaCalls.push(query)
    const list = new FakeMediaQueryList(query)
    list.matches = normaliseQuery(query) === normaliseQuery(RAIL_QUERY) && railMatches
    mediaLists.push(list)
    return list
  }) as unknown as typeof window.matchMedia
}

// Rotating the device, as far as the shell can observe it.
async function setRail(matches: boolean): Promise<void> {
  railMatches = matches
  for (const list of mediaLists) {
    if (normaliseQuery(list.media) === normaliseQuery(RAIL_QUERY)) {
      list.set(matches)
    }
  }
  await settle()
}

// --- mounting ---------------------------------------------------------------

let container: HTMLElement | null = null
let instance: Record<string, unknown> | null = null
let svelte: SvelteApi | null = null

async function renderShell(path: string): Promise<HTMLElement> {
  window.history.pushState({}, '', path)
  // Fresh modules per render: router.ts is hand-rolled and may hold module-level
  // state, and every real page load hands it a clean graph. Reusing one would
  // let the previous route leak into this test.
  vi.resetModules()
  svelte = await import('svelte')
  // Widened through `unknown`: App takes no props, so its generated component
  // type is `Component<Record<string, never>>`, which does not overlap with the
  // `Record<string, any>` props that `mount` is declared against. The mismatch is
  // in the declarations, not in the call, and mounting a propless component with
  // an empty props object is exactly what happens in `main.ts`.
  const module = (await import('../App.svelte')) as unknown as {
    default: Parameters<SvelteApi['mount']>[0]
  }
  container = document.createElement('div')
  document.body.appendChild(container)
  instance = svelte.mount(module.default, { target: container }) as Record<string, unknown>
  await settle()
  return container
}

function shell(): HTMLElement {
  if (!container) {
    throw new Error('the shell is not mounted')
  }
  return container
}

async function settle(): Promise<void> {
  svelte?.flushSync()
  await svelte?.tick()
}

async function click(element: Element, init: MouseEventInit = {}): Promise<MouseEvent> {
  const event = new MouseEvent('click', { bubbles: true, cancelable: true, ...init })
  element.dispatchEvent(event)
  await settle()
  return event
}

// The browser Back button. jsdom traverses session history on a later task, as
// a browser does, so the wait is for the traversal and not for Svelte.
async function goBack(expected: string): Promise<void> {
  window.history.back()
  for (let attempt = 0; attempt < 50 && window.location.pathname !== expected; attempt += 1) {
    await new Promise((resolve) => setTimeout(resolve, 5))
  }
  await settle()
}

async function pressKey(element: Element, key: string, init: KeyboardEventInit = {}): Promise<void> {
  element.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true, ...init }))
  await settle()
}

// --- queries ----------------------------------------------------------------

const INTERACTIVE = 'a, button, [role="link"], [role="button"], [role="menuitem"]'

function accessibleName(element: Element): string {
  const label = element.getAttribute('aria-label')
  if (label && label.trim()) {
    return label.trim()
  }
  const labelledBy = element.getAttribute('aria-labelledby')
  if (labelledBy) {
    const named = labelledBy
      .split(/\s+/)
      .map((id) => document.getElementById(id)?.textContent ?? '')
      .join(' ')
      .replace(/\s+/g, ' ')
      .trim()
    if (named) {
      return named
    }
  }
  return (element.textContent ?? '').replace(/\s+/g, ' ').trim()
}

// "Wi-Fi", "Wi Fi" and "WiFi" are the same entry; the spelling is not the
// contract, the entry is.
function sameName(a: string, b: string): boolean {
  const strip = (value: string) => value.toLowerCase().replace(/[^a-z0-9]/g, '')
  return strip(a) === strip(b)
}

function navBar(): HTMLElement {
  const candidates = [...shell().querySelectorAll('nav')]
  expect(candidates.length, 'SPEC 4.5: the bar is a <nav>').toBeGreaterThan(0)
  const withMore = candidates.find((element) =>
    [...element.querySelectorAll(INTERACTIVE)].some((child) => sameName(accessibleName(child), 'More')),
  )
  expect(withMore, 'the bar carrying the More control must be a <nav>').toBeDefined()
  return withMore as HTMLElement
}

function barEntry(name: string): HTMLElement {
  const found = [...navBar().querySelectorAll(INTERACTIVE)].filter((element) =>
    sameName(accessibleName(element), name),
  )
  expect(found, `exactly one bar entry named ${name}`).toHaveLength(1)
  return found[0] as HTMLElement
}

function barEntries(): HTMLElement[] {
  return BAR_ENTRIES.map((name) => barEntry(name))
}

function sheet(): HTMLElement {
  const dialogs = [...shell().querySelectorAll('[role="dialog"]')]
  expect(dialogs, 'SPEC 4.5: the More sheet is a role="dialog"').toHaveLength(1)
  return dialogs[0] as HTMLElement
}

function sheetIsOpen(): boolean {
  // SPEC 4.5 pins `inert` at rest and nothing else about the closed state, so
  // inert is what open and closed are distinguished by.
  //
  // The attribute is looked for on the dialog or on any ancestor up to the shell,
  // because inertness inherits: a wrapper carrying it makes the dialog inside it
  // inert just as effectively. SPEC says the sheet is inert at rest without saying
  // which element carries the attribute, and a test demanding it sit on the dialog
  // itself would assert something the specification never promised.
  let node: HTMLElement | null = sheet()
  const root = shell()
  while (node) {
    if (node.hasAttribute('inert')) return false
    if (node === root) break
    node = node.parentElement
  }
  return true
}

// The modal container, which is not always the `role="dialog"` element itself.
// A close affordance such as a backdrop button legitimately lives beside the
// dialog rather than inside it, and it belongs in the focus cycle: SPEC 4.5
// requires focus trapped while open and says nothing about the boundary, so the
// trap is checked against the container rather than the dialog alone.
function sheetRoot(): HTMLElement {
  const dialog = sheet()
  const parent = dialog.parentElement
  return parent && parent !== shell() ? parent : dialog
}

function sheetEntry(name: string): HTMLElement {
  const found = [...sheet().querySelectorAll(INTERACTIVE)].filter((element) =>
    sameName(accessibleName(element), name),
  )
  expect(found, `exactly one sheet entry named ${name}`).toHaveLength(1)
  return found[0] as HTMLElement
}

function focusablesIn(root: HTMLElement): HTMLElement[] {
  return [...root.querySelectorAll<HTMLElement>('a[href], button, input, select, textarea, [tabindex]')].filter(
    (element) => element.getAttribute('tabindex') !== '-1' && !element.hasAttribute('disabled'),
  )
}

function views(): HTMLElement[] {
  return [...shell().querySelectorAll<HTMLElement>('[data-view]')]
}

// Hidden from the accessibility tree, by any of the three means available.
// SPEC 4.5 requires the inactive Wi-Fi panel to carry `hidden` because "left
// merely present, it exposes two tab panels to VoiceOver at once"; seven views
// mounted at once are the same defect one level up.
function isPresented(element: HTMLElement): boolean {
  let node: HTMLElement | null = element
  while (node && node !== shell()) {
    if (node.hasAttribute('hidden') || node.hasAttribute('inert')) {
      return false
    }
    if (node.getAttribute('aria-hidden') === 'true') {
      return false
    }
    if (node.style.display === 'none' || node.style.visibility === 'hidden') {
      return false
    }
    node = node.parentElement
  }
  return true
}

function presentedViews(): string[] {
  return views()
    .filter((element) => isPresented(element))
    .map((element) => element.getAttribute('data-view') ?? '')
}

function currentEntryNames(): string[] {
  return barEntries()
    .filter((element) => element.getAttribute('aria-current') === 'page')
    .map((element) => accessibleName(element))
}

// --- lifecycle --------------------------------------------------------------

beforeEach(() => {
  railMatches = false
  installMatchMedia()
  window.history.pushState({}, '', '/')
})

afterEach(() => {
  if (instance && svelte) {
    svelte.unmount(instance)
  }
  instance = null
  svelte = null
  container?.remove()
  container = null
  vi.restoreAllMocks()
})

// --- routes -----------------------------------------------------------------

describe('the seven routes', () => {
  for (const route of ROUTES) {
    it(`shows the ${route.view} view at ${route.path}`, async () => {
      await renderShell(route.path)

      // Deep links have to work: SPEC 2.15 and D14 make the static server serve
      // index.html for any path with no file behind it, which buys nothing
      // unless the shell can start on that path.
      expect(presentedViews()).toEqual([route.view])
      expect(window.location.pathname).toBe(route.path)
    })
  }

  it('resolves /wifi without a segment in the URL', async () => {
    await renderShell('/wifi')

    // SPEC 4.5: "The Wi-Fi segment is not in the URL: it is view state, not a
    // location, and /wifi must resolve without one."
    expect(window.location.pathname).toBe('/wifi')
    expect(window.location.search).toBe('')
    expect(window.location.hash).toBe('')
    expect(presentedViews()).toEqual(['wifi'])
  })

  it('navigates without leaving the document when a bar entry is chosen', async () => {
    await renderShell('/')

    const event = await click(barEntry('Map'))

    // An unprevented anchor click is a full page load, which drops the
    // WebSocket that SPEC 4.3 spends its whole design keeping alive.
    expect(event.defaultPrevented).toBe(true)
    expect(window.location.pathname).toBe('/map')
    expect(presentedViews()).toEqual(['map'])
  })

  // A real href is a promise to the person clicking it. Cmd-click, Ctrl-click
  // and middle-click all mean "do the browser thing with this link, not the app
  // thing", and an app that swallows them has an anchor that lies about being
  // one. SPEC 4.5 chose anchors over buttons deliberately, so honouring the
  // modified click is part of that choice rather than a nicety.
  for (const modifier of ['metaKey', 'ctrlKey', 'shiftKey', 'altKey'] as const) {
    it(`leaves a ${modifier} click to the browser`, async () => {
      await renderShell('/')

      const event = await click(barEntry('Map'), { [modifier]: true })

      expect(event.defaultPrevented).toBe(false)
      expect(window.location.pathname).toBe('/')
      expect(presentedViews()).toEqual(['dashboard'])
    })
  }

  it('leaves a middle click to the browser', async () => {
    await renderShell('/')

    // button 1 is the middle button, which opens the href in a new tab. Taking
    // it over would navigate the current view instead, which is not what was
    // asked for and loses the tab the user wanted.
    const event = await click(barEntry('Map'), { button: 1 })

    expect(event.defaultPrevented).toBe(false)
    expect(window.location.pathname).toBe('/')
    expect(presentedViews()).toEqual(['dashboard'])
  })

  it('does not act on a click whose default was already prevented', async () => {
    await renderShell('/')

    // Something earlier in the chain has already decided this click is spoken
    // for. Navigating on top of that decision would act on one gesture twice.
    const prevent = (event: Event): void => event.preventDefault()
    document.addEventListener('click', prevent, true)
    try {
      const event = await click(barEntry('Map'))
      expect(event.defaultPrevented).toBe(true)
    } finally {
      document.removeEventListener('click', prevent, true)
    }

    expect(window.location.pathname).toBe('/')
    expect(presentedViews()).toEqual(['dashboard'])
  })

  it('goes back to the previous view when the browser Back button is used', async () => {
    await renderShell('/')
    await click(barEntry('Map'))
    expect(presentedViews()).toEqual(['map'])

    await goBack('/')

    // SPEC 4.5 routes "are not decoration" and the router is built on the
    // History API: a place you can navigate to but not come back from is not a
    // location, and on iOS the back gesture is how the view gets left.
    expect(window.location.pathname).toBe('/')
    expect(presentedViews()).toEqual(['dashboard'])
    expect(barEntry('Dashboard').getAttribute('aria-current')).toBe('page')
    expect(barEntry('Map').hasAttribute('aria-current')).toBe(false)
  })

  it('pushes no history entry when the entry for the current view is chosen again', async () => {
    await renderShell('/')
    await click(barEntry('Map'))
    expect(presentedViews()).toEqual(['map'])

    await click(barEntry('Map'))

    // Nothing moves: SPEC 4.5, "choosing the entry for the view already showing
    // is not a navigation."
    expect(window.location.pathname).toBe('/map')
    expect(presentedViews()).toEqual(['map'])
    expect(barEntry('Map').getAttribute('aria-current')).toBe('page')

    await goBack('/')

    // The point of the rule, and the only form in which a user meets it: one
    // Back leaves the view. A duplicate entry would need two, "a view the user
    // only entered once".
    expect(window.location.pathname).toBe('/')
    expect(presentedViews()).toEqual(['dashboard'])
  })

  it('carries the route in the href of every bar entry that is a view', async () => {
    await renderShell('/')

    for (const route of ROUTES.filter((candidate) => candidate.entry !== 'More')) {
      const entry = barEntry(route.entry)
      expect(entry.tagName).toBe('A')
      expect(entry.getAttribute('href')).toBe(route.path)
    }
  })

  it('resolves an unmatched path to the Dashboard and rewrites the address bar', async () => {
    // SPEC 2.15 and D14 send every unresolvable path to index.html, so the
    // shell is handed URLs like this one. SPEC 4.5 now decides what it does with
    // them: the Dashboard, with the address bar rewritten to "/", because a URL
    // that disagrees with the view "reads as a dead Back button" later on.
    await renderShell('/not-a-route')

    expect(presentedViews()).toEqual(['dashboard'])
    expect(window.location.pathname).toBe('/')
    expect(barEntries()).toHaveLength(BAR_ENTRIES.length)
    expect(currentEntryNames()).toHaveLength(1)
    expect(barEntry('Dashboard').getAttribute('aria-current')).toBe('page')
  })
})

// --- the bar is navigation, not tabs ---------------------------------------

describe('the bar is site navigation, not a tab control', () => {
  it('is a nav of anchors and claims no tab roles', async () => {
    await renderShell('/')

    const bar = navBar()
    expect(bar.tagName).toBe('NAV')
    // SPEC 4.5: "the fifth slot opens a dialog rather than switching a panel,
    // so a tablist role on the bar would be a lie."
    expect(bar.querySelector('[role="tablist"]')).toBeNull()
    expect(bar.querySelector('[role="tab"]')).toBeNull()
    expect(bar.getAttribute('role')).not.toBe('tablist')
  })

  it('holds the five slots of SPEC 4.5 in order', async () => {
    await renderShell('/')

    const names = [...navBar().querySelectorAll(INTERACTIVE)]
      .filter((element) => BAR_ENTRIES.some((name) => sameName(accessibleName(element), name)))
      .map((element) => accessibleName(element))

    expect(names).toHaveLength(BAR_ENTRIES.length)
    for (const [index, expected] of BAR_ENTRIES.entries()) {
      expect(sameName(names[index] ?? '', expected)).toBe(true)
    }
  })

  for (const route of ROUTES) {
    it(`marks ${route.entry} as the current page at ${route.path}`, async () => {
      await renderShell(route.path)

      // SPEC 4.5: the current entry carries aria-current="page", and while a
      // sheet-hosted view is current that entry is More, "otherwise the
      // indicator vanishes from the bar entirely whenever the user is in Peers,
      // Mirror or Settings".
      expect(barEntry(route.entry).getAttribute('aria-current')).toBe('page')

      const current = currentEntryNames()
      expect(current).toHaveLength(1)
      expect(sameName(current[0] ?? '', route.entry)).toBe(true)
    })
  }

  it('moves the indicator when navigation happens', async () => {
    await renderShell('/')
    expect(currentEntryNames().map((name) => sameName(name, 'Dashboard'))).toEqual([true])

    await click(barEntry('Log'))

    expect(barEntry('Log').getAttribute('aria-current')).toBe('page')
    expect(barEntry('Dashboard').hasAttribute('aria-current')).toBe(false)
  })
})

// --- the More sheet ---------------------------------------------------------

describe('the More sheet as a dialog', () => {
  it('is inert at rest', async () => {
    await renderShell('/')

    // SPEC 4.5: "inert at rest, so its controls are neither focusable nor in
    // the accessibility tree." The attribute may sit on the dialog or on the
    // container around it, since inertness inherits; sheetIsOpen() accepts both.
    expect(sheetIsOpen()).toBe(false)
  })

  it('opens with a dialog role, aria-modal and an accessible name', async () => {
    await renderShell('/')

    await click(barEntry('More'))

    const dialog = sheet()
    expect(sheetIsOpen()).toBe(true)
    expect(dialog.getAttribute('role')).toBe('dialog')
    expect(dialog.getAttribute('aria-modal')).toBe('true')
    expect(accessibleName(dialog)).not.toBe('')
  })

  it('holds Peers, Mirror and Settings', async () => {
    await renderShell('/')

    await click(barEntry('More'))

    for (const name of SHEET_ENTRIES) {
      const entry = sheetEntry(name)
      const route = ROUTES.find((candidate) => sameName(candidate.view, name))
      expect(entry.getAttribute('href')).toBe(route?.path)
    }
  })

  it('takes focus into the dialog when it opens', async () => {
    await renderShell('/')

    await click(barEntry('More'))

    // Focus that never enters the dialog cannot be trapped in it, and the
    // return-on-close obligation has nothing to return from.
    expect(sheet().contains(document.activeElement)).toBe(true)
  })

  it('closes on Escape and returns focus to the More control', async () => {
    await renderShell('/')
    const more = barEntry('More')
    await click(more)

    await pressKey(document.activeElement ?? sheet(), 'Escape')

    expect(sheetIsOpen()).toBe(false)
    // SPEC 4.5: "Focus that is trapped but never returned is the most common
    // defect in this pattern and the one screen-reader users notice first."
    expect(document.activeElement).toBe(more)
  })

  it('closes on a click outside the dialog and returns focus to the More control', async () => {
    await renderShell('/')
    const more = barEntry('More')
    await click(more)

    // SPEC names a backdrop but does not say which element carries it, so the
    // dismissal is driven the way a user drives it: a click that lands outside
    // the dialog.
    const backdrop = shell().querySelector('[data-backdrop], .backdrop, [class*="backdrop"]')
    if (backdrop) {
      await click(backdrop)
    }
    if (sheetIsOpen()) {
      const parent = sheet().parentElement
      if (parent) {
        await click(parent)
      }
    }
    if (sheetIsOpen()) {
      await click(shell())
    }

    expect(sheetIsOpen()).toBe(false)
    expect(document.activeElement).toBe(more)
  })

  it('closes when an entry is chosen, navigates, and returns focus to the More control', async () => {
    await renderShell('/')
    const more = barEntry('More')
    await click(more)

    await click(sheetEntry('Peers'))

    expect(sheetIsOpen()).toBe(false)
    expect(window.location.pathname).toBe('/peers')
    expect(presentedViews()).toEqual(['peers'])
    // The indicator lives on More while a sheet-hosted view is current.
    expect(more.getAttribute('aria-current')).toBe('page')
    expect(document.activeElement).toBe(more)
  })

  it('traps focus while open', async () => {
    await renderShell('/')
    await click(barEntry('More'))

    const container = sheetRoot()
    const focusables = focusablesIn(container)
    expect(focusables.length).toBeGreaterThan(1)
    const first = focusables[0] as HTMLElement
    const last = focusables[focusables.length - 1] as HTMLElement

    last.focus()
    await pressKey(last, 'Tab')
    expect(container.contains(document.activeElement)).toBe(true)
    expect(document.activeElement).toBe(first)

    first.focus()
    await pressKey(first, 'Tab', { shiftKey: true })
    expect(container.contains(document.activeElement)).toBe(true)
    expect(document.activeElement).toBe(last)
  })

  it('is closed again after the sheet-hosted view has been reached', async () => {
    await renderShell('/settings')

    // Arriving by deep link must not leave the sheet hanging open over the view.
    expect(sheetIsOpen()).toBe(false)
    expect(presentedViews()).toEqual(['settings'])
  })
})

// --- the landscape rail -----------------------------------------------------

describe('the landscape rail', () => {
  it('asks for exactly the media query SPEC 4.5 gives', async () => {
    await renderShell('/')

    // SPEC 4.5: "The word landscape alone is not a condition: (orientation:
    // landscape) and (min-aspect-ratio: 1/1) disagree." The exact query is the
    // contract, so an implementation that guesses a different one fails here.
    const asked = matchMediaCalls.map((query) => normaliseQuery(query))
    expect(asked).toContain(normaliseQuery(RAIL_QUERY))
  })

  it('keeps the bottom bar with its labels outside the range', async () => {
    railMatches = false
    await renderShell('/')

    for (const name of BAR_ENTRIES) {
      const entry = barEntry(name)
      expect(sameName((entry.textContent ?? '').trim(), name)).toBe(true)
    }
  })

  it('becomes an icon-only rail inside the range', async () => {
    railMatches = true
    await renderShell('/')

    for (const name of BAR_ENTRIES) {
      const entry = barEntry(name)
      // SPEC 4.5: "Rail entries are icon-only with an aria-label; 72px does not
      // hold a label like Dashboard at a readable size."
      expect(entry.getAttribute('aria-label')?.trim()).toBeTruthy()
      expect(sameName(entry.getAttribute('aria-label') ?? '', name)).toBe(true)
      expect((entry.textContent ?? '').trim()).toBe('')
      // Icon-only is not nameless: the accessible name must survive.
      expect(accessibleName(entry)).not.toBe('')
    }
  })

  it('switches to the rail when the device is rotated into the range', async () => {
    railMatches = false
    await renderShell('/')
    expect(sameName((barEntry('Map').textContent ?? '').trim(), 'Map')).toBe(true)

    await setRail(true)

    // Rotation is the whole reason the query exists; a rail that only appears
    // when the app launches already rotated is not the behaviour SPEC asks for.
    expect((barEntry('Map').textContent ?? '').trim()).toBe('')
    expect(sameName(barEntry('Map').getAttribute('aria-label') ?? '', 'Map')).toBe(true)
  })

  it('goes back to the bottom bar when the device is rotated out of the range', async () => {
    railMatches = true
    await renderShell('/')

    await setRail(false)

    expect(sameName((barEntry('Map').textContent ?? '').trim(), 'Map')).toBe(true)
  })

  it('keeps navigation working in the rail', async () => {
    railMatches = true
    await renderShell('/')

    await click(barEntry('Log'))

    // SPEC 4.5: "Landscape exists for the Log and the Map."
    expect(window.location.pathname).toBe('/log')
    expect(presentedViews()).toEqual(['log'])
    expect(barEntry('Log').getAttribute('aria-current')).toBe('page')
  })
})

// --- views stay mounted -----------------------------------------------------

describe('views stay mounted when navigated away from', () => {
  it('keeps a visited view in the document after leaving it', async () => {
    await renderShell('/map')
    expect(presentedViews()).toEqual(['map'])

    await click(barEntry('Log'))

    // SPEC 4.5: "The Map keeps its viewport and zoom, and the Log keeps its
    // buffer and scroll position, rather than re-initialising on every visit."
    const map = views().find((element) => element.getAttribute('data-view') === 'map')
    expect(map, 'the Map view must still be mounted after navigating away').toBeDefined()
    expect(presentedViews()).toEqual(['log'])
  })

  it('returns to the same element rather than a rebuilt one', async () => {
    await renderShell('/map')
    const first = views().find((element) => element.getAttribute('data-view') === 'map')
    expect(first).toBeDefined()

    await click(barEntry('Log'))
    await click(barEntry('Map'))

    const second = views().find((element) => element.getAttribute('data-view') === 'map')
    // Element identity is the observable form of "not re-initialised": a
    // Leaflet instance rebuilt on every visit re-centres, which reads as a bug.
    expect(second).toBe(first)
    expect(presentedViews()).toEqual(['map'])
  })

  it('presents exactly one view at a time even with several mounted', async () => {
    await renderShell('/map')
    await click(barEntry('Log'))
    await click(barEntry('More'))
    await click(sheetEntry('Mirror'))

    expect(views().length).toBeGreaterThan(1)
    expect(presentedViews()).toEqual(['mirror'])
  })
})
