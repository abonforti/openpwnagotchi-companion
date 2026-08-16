// The seven routes of SPEC 4.5 and nothing more. Hand-rolled against the
// History API on purpose: seven static routes with no parameters, no nesting
// and no guards do not justify a routing dependency that is paid for over a
// Bluetooth link every time the unit serves the app.

import { writable, type Readable } from 'svelte/store'

export type ViewId = 'dashboard' | 'wifi' | 'map' | 'log' | 'peers' | 'mirror' | 'settings'

/** Where the route is reached from: the navigation bar, or the More sheet. */
export type RouteSlot = 'bar' | 'sheet'

export interface Route {
  readonly id: ViewId
  readonly path: string
  readonly label: string
  readonly slot: RouteSlot
}

/**
 * The route of the start URL, and the one an unknown path resolves to. Declared
 * separately only so the fallback can point at the very object in `routes`
 * rather than a second copy of it that would drift on the first edit.
 */
const DASHBOARD: Route = { id: 'dashboard', path: '/', label: 'Dashboard', slot: 'bar' }

/** Order matters: it is the order of the bar entries and of the sheet entries. */
export const routes: readonly Route[] = [
  DASHBOARD,
  { id: 'wifi', path: '/wifi', label: 'Wi-Fi', slot: 'bar' },
  { id: 'map', path: '/map', label: 'Map', slot: 'bar' },
  { id: 'log', path: '/log', label: 'Log', slot: 'bar' },
  { id: 'peers', path: '/peers', label: 'Peers', slot: 'sheet' },
  { id: 'mirror', path: '/mirror', label: 'Mirror', slot: 'sheet' },
  { id: 'settings', path: '/settings', label: 'Settings', slot: 'sheet' },
]

export const barRoutes: readonly Route[] = routes.filter((route) => route.slot === 'bar')
export const sheetRoutes: readonly Route[] = routes.filter((route) => route.slot === 'sheet')

/**
 * Reduce a URL path to the form the table above is written in. The Wi-Fi
 * segment is view state and never reaches the URL (SPEC 4.5), so a query string
 * or a fragment is never route-bearing here.
 */
function normalise(path: string): string {
  return path
    .replace(/[?#].*/, '') // query and fragment carry no route
    .replace(/(.)\/+$/, '$1') // a trailing slash is the same place, except at the root
    .replace(/^$/, '/') // an empty path is the root
}

/**
 * The route for a path. Unknown paths resolve to the Dashboard rather than to
 * an error page: the static server already serves index.html for anything that
 * matches no file (D14), so a mistyped deep link arrives here and must land
 * somewhere usable.
 */
export function resolve(path: string): Route {
  const wanted = normalise(path)
  return routes.find((route) => route.path === wanted) ?? DASHBOARD
}

const store = writable<Route>(resolve(window.location.pathname))

/** The route currently displayed. Read-only to everything but `navigate`. */
export const currentRoute: Readable<Route> = { subscribe: store.subscribe }

/** Adopt whatever the address bar says. Used on popstate and at startup. */
export function syncFromLocation(): void {
  const path = normalise(window.location.pathname)
  const route = resolve(path)
  if (path !== route.path) {
    // An unknown path is exactly what the static server hands the shell (D14),
    // and leaving it in the address bar makes the URL and the view disagree for
    // the rest of the session. replaceState, not pushState: a URL that resolves
    // to nothing must not become a Back destination.
    window.history.replaceState({}, '', route.path)
  }
  store.set(route)
}

/**
 * Go to a path, pushing a history entry unless we are already there. Re-tapping
 * the current bar entry must not stack duplicate entries under the back button.
 */
export function navigate(path: string): void {
  const route = resolve(path)
  if (normalise(window.location.pathname) !== route.path) {
    window.history.pushState({}, '', route.path)
  }
  store.set(route)
}

/**
 * Click handler shared by every in-app anchor. Returns true when the click was
 * taken over, false when the browser should follow the href as it stands: a
 * modified click still means "open this somewhere else", and the hrefs are real
 * so that behaviour is worth keeping.
 */
export function followLink(event: MouseEvent, path: string): boolean {
  if (event.defaultPrevented || event.button !== 0) {
    return false
  }
  if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) {
    return false
  }
  event.preventDefault()
  navigate(path)
  return true
}

/**
 * Start listening to the back and forward buttons. Returns the teardown, so a
 * caller that mounts the shell more than once (a test does) leaves no listener
 * behind.
 */
export function startRouter(): () => void {
  syncFromLocation()
  const onPopState = (): void => syncFromLocation()
  window.addEventListener('popstate', onPopState)
  return () => window.removeEventListener('popstate', onPopState)
}
