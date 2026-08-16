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
      injectRegister: 'auto',
      manifest: false,
      includeAssets: ['icons/*.png', 'manifest.webmanifest'],

      workbox: {
        // The app shell only. Map tiles come from the internet over the phone's
        // own cellular link and are deliberately not cached in v1 (SPEC 4.1).
        globPatterns: ['**/*.{js,css,html,png,svg,webmanifest}'],

        // A deep link that resolves to no file must serve index.html, the same
        // rule the plugin's static server follows (SPEC 2.15).
        navigateFallback: '/index.html',
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
})
