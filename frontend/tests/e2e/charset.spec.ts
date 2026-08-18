import { expect, test } from '@playwright/test'

/**
 * SPEC 4.2: WebKit does not sniff an undeclared encoding, so the charset
 * declaration is mandatory and only honoured within the first 1024 bytes of
 * the document. Two things follow from that, and each gets its own test
 * below.
 *
 * The first is a property of the *built* document, not the source: the tag
 * sits first in `frontend/index.html` today, but a build tool that reorders
 * or injects into `<head>` can push it past the window without Chromium ever
 * showing anything wrong (Chromium sniffs UTF-8; WebKit does not). So this
 * fetches `/` from the `webServer` this suite already runs against the built
 * `dist/` (SPEC 10.5) and inspects the raw response bytes, rather than
 * reading the source file or opening a page that would let the browser's own
 * decoding hide the thing being tested. It has no engine dependency, so it
 * runs once rather than once per project.
 *
 * The second is `document.characterSet`, read from a live page in every
 * project of the matrix. It is not the trivial assertion it looks like, and
 * it does have a mutant: `vite preview` serves `/` as `text/html` with no
 * charset parameter, so under this harness the meta tag is the only thing
 * deciding the encoding. Remove it from `frontend/index.html` and this fails
 * with `windows-1252` - measured, in both engines, not only in WebKit.
 *
 * That last point is worth stating because issue #39 says otherwise. It
 * records Chromium sniffing its way out while WebKit does not; served without
 * a declaration, both decode as `windows-1252` here, with or without a body
 * full of non-ASCII text to feed a detector. An encoding detector is
 * conditional on locale and content, so the reason to run both engines is not
 * that one of them is known to be forgiving (SPEC 4.2).
 *
 * What this file does not cover: the fourth acceptance criterion of the issue
 * this section defends asks for the pwnagotchi face rendering identically in
 * both engines. Nothing in the app renders a face yet - every view under
 * `frontend/src/views/` is still a placeholder, and there is no non-ASCII
 * string anywhere in `frontend/src/` for a face test to render. That test
 * waits on a view that can display one; `document.characterSet` is the rule
 * that criterion depends on, and asserting it now is meaningful on its own,
 * but it is not a substitute for the face test and should not be read as one.
 */

test('the built index.html declares utf-8 within the first 1024 bytes', async ({
  request,
}, testInfo) => {
  test.skip(
    testInfo.project.name !== 'chromium-portrait',
    'a raw-byte check with no engine dependency; one project is enough',
  )

  const response = await request.get('/')
  const body = await response.body()
  const head = body.subarray(0, 1024).toString('utf-8')

  expect(head).toMatch(/<meta\s+charset=["']?utf-8["']?/i)
})

test('the document reports its character set as utf-8', async ({ page }) => {
  await page.goto('/')

  const characterSet = await page.evaluate(() => document.characterSet)

  expect(characterSet).toBe('UTF-8')
})
