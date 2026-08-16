import { describe, expect, it } from 'vitest'

import type { AccessPoint, IncomingSetMode, Mode } from '../lib/protocol'

// protocol.ts is generated from docs/schemas and holds types only: nothing in
// it survives compilation, so there is no runtime surface to exercise. What can
// go wrong is the types drifting from the schemas or becoming unusable, and two
// checks already cover that better than a test could:
//
//   node tools/gen-protocol-types.mjs --check   the file matches the schemas
//   tsc --noEmit / svelte-check                 the file compiles and is usable
//
// This file adds the third thing neither of those does: it pins that the shapes
// the app will actually build against still exist and still fit real payloads.
// The value is in the compile, so a change that renames a field or narrows a
// union fails the build here rather than at runtime on a phone.

describe('the generated protocol types', () => {
  it('accepts an access point in the shape the plugin sends', () => {
    const ap = {
      bssid: 'aa:bb:cc:dd:ee:ff',
      // hostname, not ssid: the plugin maps ap['hostname'] and falls back to
      // the MAC when it is empty. Getting this wrong renders blank rows.
      hostname: 'TestNet_001',
      channel: 6,
      rssi: -58,
      clients: 2,
      encryption: 'WPA2',
      vendor: 'Example Networks',
    } satisfies AccessPoint

    expect(ap.bssid).toBe('aa:bb:cc:dd:ee:ff')
  })

  it('accepts every mode the plugin can report, PASV included', () => {
    // Three states, not two: pasv_mode.py adds PASV between AUTO and MANUAL,
    // and the app must be able to name it (SPEC 2.6).
    const modes = ['AUTO', 'PASV', 'MANUAL'] as const satisfies readonly Mode[]

    expect(modes).toHaveLength(3)
  })

  it('sends set_mode in lower case, though stats reports mode in upper case', () => {
    // The asymmetry is real and deliberate: `Mode` above is what the unit
    // reports, while the command takes 'auto' | 'manual'. Sending 'MANUAL'
    // would be rejected, and no amount of care at the call site catches that
    // if the types do not. Pinned here so a future edit cannot quietly align
    // the two and break the plugin.
    const command = {
      type: 'set_mode',
      mode: 'manual',
      message_id: '1f0c0f3a',
    } satisfies IncomingSetMode

    expect(command.type).toBe('set_mode')
  })
})
