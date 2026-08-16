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

      // SPEC 10.7: the gate covers src/lib and src/shell. App.svelte is named
      // explicitly because it sits outside src/shell/ while holding the router
      // wiring, the sheet state and the `inert` handling - that is the logic
      // SPEC 10.7 means by "the shell", and leaving it out would put the part
      // that breaks quietly outside the gate. src/views/ stays out, because it
      // genuinely is markup, and a coverage number for markup buys nothing.
      include: ['src/lib/**', 'src/shell/**', 'src/App.svelte'],

      // protocol.ts is generated from docs/schemas and holds types only, so it
      // emits no runtime code to cover. It is checked by
      // `node tools/gen-protocol-types.mjs --check`, which is a stronger
      // guarantee than coverage: it fails if the file drifts from the schemas.
      exclude: ['src/lib/protocol.ts'],

      // SPEC 10.7: 85 is the floor, not the target. A compiled Svelte
      // component has branches with no source-level counterpart, so a single
      // global 100 either forces the awkward files out of the gate or produces
      // a threshold nobody meets. The recorded figure in
      // .github/coverage-baseline.json is what catches a fall that stays above
      // this line.
      thresholds: { lines: 85, branches: 85 },
    },
  },
})
