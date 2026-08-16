import { svelte } from '@sveltejs/vite-plugin-svelte'
import { defineConfig } from 'vitest/config'

// Separate from vite.config.ts on purpose: the test run must not load
// vite-plugin-pwa, which would generate a service worker for every run.
export default defineConfig({
  plugins: [svelte()],

  // Svelte ships both a server and a browser build. Mounting a component in a
  // test only works against the browser one, so the condition is stated rather
  // than inferred.
  resolve: { conditions: ['browser'] },

  test: {
    globals: true,
    include: ['src/__tests__/**/*.spec.ts'],

    // jsdom, as SPEC 10.5 anticipated: "the views will need one; it arrives
    // with them." navigation.spec.ts mounts the shell and asserts on roles,
    // aria-current, focus and the sheet, none of which exist without a DOM.
    environment: 'jsdom',

    coverage: {
      provider: 'v8',
      reporter: ['text', 'json-summary'],

      // Only src/lib. Svelte view components are exercised by hand and a
      // coverage number for markup buys nothing (SPEC 10.7).
      include: ['src/lib/**/*.ts'],

      // protocol.ts is generated from docs/schemas and holds types only, so it
      // emits no runtime code to cover. It is checked by
      // `node tools/gen-protocol-types.mjs --check`, which is a stronger
      // guarantee than coverage: it fails if the file drifts from the schemas.
      exclude: ['src/lib/protocol.ts'],

      // SPEC 10.7. On from the commit that put runtime code in src/lib:
      // router.ts is that code, and a gate switched on later is a gate that
      // starts its life failing.
      thresholds: { lines: 100, branches: 100 },
    },
  },
})
