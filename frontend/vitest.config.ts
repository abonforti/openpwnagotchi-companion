import { svelte } from '@sveltejs/vite-plugin-svelte'
import { defineConfig } from 'vitest/config'

// Separate from vite.config.ts on purpose: the test run must not load
// vite-plugin-pwa, which would generate a service worker for every run.
export default defineConfig({
  plugins: [svelte()],
  test: {
    globals: true,
    include: ['src/__tests__/**/*.spec.ts'],

    // Node, not jsdom. Nothing here touches the DOM yet, and a DOM shim that
    // exists only to satisfy a config is a dependency bought for nothing.
    // The views will need one; it arrives with them.
    environment: 'node',

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

      // SPEC 10.7 sets these to 100 for lines and branches. They are off until
      // src/lib holds runtime code to cover: turning them on now would prove
      // only that an empty set is fully covered, which is the same reason
      // pytest.ini leaves --cov-fail-under off. They go on with ws.ts.
      // thresholds: { lines: 100, branches: 100 },
    },
  },
})
