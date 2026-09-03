import { svelte } from '@sveltejs/vite-plugin-svelte'
import { defineConfig } from 'vite'
import { VitePWA } from 'vite-plugin-pwa'

// The app is served by the plugin's own HTTPS server from web_root (SPEC 2.15),
// at the root of a bare IP address. Nothing sits in front of it, so paths are
// absolute from '/' and there is no base to configure.
export default defineConfig({
  plugins: [
    svelte(),
    VitePWA({
      // The unit serves whatever install-on-pi.sh last unpacked. A user who
      // reinstalls should not have to clear a stale shell out of Safari by hand.
      registerType: 'autoUpdate',

      // index.html already carries the manifest link and the apple-* meta tags,
      // because iOS reads those tags and not only the manifest (SPEC 4.2).
      // Pinned to the external form 'auto' resolves to today: the CSP has no
      // 'unsafe-inline' in script-src, so an upstream default change to an
      // inline registration script would break silently otherwise (SPEC
      // 2.15.1, issue #91). 'auto' did a second thing as well, invisible in
      // index.html: with registerType 'autoUpdate' it also turned on
      // skipWaiting and clientsClaim in the worker. Those are set explicitly
      // in the workbox block below, so this file states both effects.
      injectRegister: 'script',
      manifest: false,
      includeAssets: ['icons/*.png', 'manifest.webmanifest'],

      workbox: {
        // The app shell only. Map tiles come from the internet over the phone's
        // own cellular link and are deliberately not cached in v1 (SPEC 4.1).
        globPatterns: ['**/*.{js,css,html,png,svg,webmanifest}'],

        // A deep link that resolves to no file must serve index.html, the same
        // rule the plugin's static server follows (SPEC 2.15).
        navigateFallback: '/index.html',

        // What 'autoUpdate' means at the worker: a new worker activates
        // without waiting for every tab of the origin to close, and takes
        // the open ones. vite-plugin-pwa sets both only while injectRegister
        // is 'auto', so they are named here rather than inherited.
        skipWaiting: true,
        clientsClaim: true,
      },

      devOptions: {
        // A service worker in dev hides the very cache bugs it would cause.
        enabled: false,
      },
    }),
  ],

  build: {
    // install-on-pi.sh unpacks dist.tgz into web_root; CI packs this directory.
    outDir: 'dist',
    // Bluetooth PAN is the transport. Every kilobyte is paid for at BT speed,
    // so a source map that nobody reads on a phone is not shipped.
    sourcemap: false,
    target: 'es2022',
  },

  server: {
    // Deliberately not `host: true`. A dev server bound to every interface has
    // no TLS and no authentication, and on a laptop sharing the tether that
    // reaches the tether. Use `npm run dev:lan` when the phone genuinely needs
    // to reach it, so exposing it is a decision rather than a default.
    host: 'localhost',
  },

  preview: {
    // `vite preview` sends none of the plugin's headers by default, and the
    // service worker test needs an origin that does: the whole point of
    // service-worker.spec.ts is to show the header survives a response the
    // worker replays from the precache, not one it synthesises (SPEC
    // 2.15.1, issue #91). As a side effect the whole end-to-end suite now
    // runs under the policy the plugin enforces. This is a fixture, not a
    // second source of truth: the value below is copied from
    // content_security_policy([], None) in plugin/companion.py, which is
    // what the preview server actually approximates (no bound addresses,
    // no WSS listener), and tests/test_csp.py pins the copy to the function.
    headers: {
      'Content-Security-Policy':
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: https://*.tile.openstreetmap.org; connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
      'X-Content-Type-Options': 'nosniff',
      'Referrer-Policy': 'no-referrer',
    },
  },
})
