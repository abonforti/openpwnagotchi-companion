import { mount } from 'svelte'

import App from './App.svelte'
import './app.css'

/**
 * Flag standalone mode on the document element, once, before anything is
 * mounted. The condition is `navigator.standalone` and not the
 * `(display-mode: standalone)` media query: measured on the device, the query
 * reports false while `navigator.standalone` is true in the same frame
 * (SPEC 4.2.1), so CSS keyed on the query is inert on the only platform this
 * app targets. The shell reads the flag to choose its height rule.
 */
const iosNavigator = navigator as Navigator & { standalone?: boolean }
if (iosNavigator.standalone === true) {
  document.documentElement.dataset.standalone = 'true'
}

const target = document.getElementById('app')
if (!target) {
  throw new Error('#app is missing from index.html')
}

export default mount(App, { target })
