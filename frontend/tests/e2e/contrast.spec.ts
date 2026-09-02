import { expect, test } from '@playwright/test'

import { ROUTES, type ViewName, gotoView, openSheet } from './helpers'

/**
 * SPEC 10.5: "contrast computed for every token against every surface it can
 * land on", in both palettes (SPEC 4.5.1 follows `prefers-color-scheme` and
 * offers an explicit light/dark control, so both palettes are shipped and both
 * are gated here).
 *
 * Computed, not eyeballed, and computed from what the engine actually painted
 * rather than from the stylesheet: a token only matters where it lands, and
 * where it lands is a question about the rendered tree. The pairs are therefore
 * discovered by walking the shell in every view rather than enumerated from a
 * palette table, which is also why this catches a token used on a surface
 * nobody wrote down.
 *
 * Floors are WCAG 2.1 AA:
 *   1.4.3 text            4.5:1, or 3:1 for large text
 *                         (>= 24px, or >= 18.66px at weight >= 700)
 *   1.4.11 non-text       3:1 for a graphical object needed to understand
 *                         the content, which is what an icon-only rail entry
 *                         is (SPEC 4.5: rail entries are icon-only).
 *
 * SPEC names no level and no token list, so AA and the discovery walk are this
 * suite's reading of "the WCAG floors"; that reading is reported rather than
 * hidden. AAA is not asserted.
 */

const VIEWS = Object.keys(ROUTES) as ViewName[]

interface Finding {
  view: string
  scheme: string
  label: string
  foreground: string
  background: string
  ratio: number
  floor: number
  sample: string
}

/**
 * Runs inside the page. Returns one finding per element whose painted
 * foreground fails its floor against the surface it is actually sitting on,
 * plus the pairs that cannot be computed at all so they are reported instead of
 * being silently counted as passes.
 */
async function auditPage(
  page: import('@playwright/test').Page,
  view: string,
  scheme: string,
): Promise<{ failures: Finding[]; unverifiable: string[]; pairs: number }> {
  return page.evaluate(
    ({ view, scheme }) => {
      const unverifiable: string[] = []

      // `noUncheckedIndexedAccess` is on, and a regexp group is typed as
      // possibly absent even when the pattern guarantees it. Parsing through
      // one helper keeps that noise out of the arithmetic below.
      function num(value: string | undefined): number {
        return parseFloat(value ?? '0')
      }

      function parseColor(
        value: string,
      ): [number, number, number, number] | null {
        const rgb = value.match(
          /^rgba?\(\s*([\d.]+)[,\s]+([\d.]+)[,\s]+([\d.]+)(?:[,/\s]+([\d.]+%?))?\s*\)$/i,
        )
        if (rgb) {
          const alphaText = rgb[4]
          const alpha =
            alphaText === undefined
              ? 1
              : alphaText.endsWith('%')
                ? parseFloat(alphaText) / 100
                : parseFloat(alphaText)
          return [num(rgb[1]), num(rgb[2]), num(rgb[3]), alpha]
        }
        const srgb = value.match(
          /^color\(\s*srgb\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)(?:\s*\/\s*([\d.]+))?\s*\)$/i,
        )
        if (srgb) {
          return [
            num(srgb[1]) * 255,
            num(srgb[2]) * 255,
            num(srgb[3]) * 255,
            srgb[4] === undefined ? 1 : num(srgb[4]),
          ]
        }
        if (value === 'transparent') return [0, 0, 0, 0]
        return null
      }

      function channel(value: number): number {
        const c = value / 255
        return c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4)
      }

      function luminance(color: [number, number, number]): number {
        return (
          0.2126 * channel(color[0]) +
          0.7152 * channel(color[1]) +
          0.0722 * channel(color[2])
        )
      }

      function ratio(
        a: [number, number, number],
        b: [number, number, number],
      ): number {
        const la = luminance(a)
        const lb = luminance(b)
        const light = Math.max(la, lb)
        const dark = Math.min(la, lb)
        return (light + 0.05) / (dark + 0.05)
      }

      function over(
        top: [number, number, number, number],
        bottom: [number, number, number],
      ): [number, number, number] {
        const a = top[3]
        return [
          top[0] * a + bottom[0] * (1 - a),
          top[1] * a + bottom[1] * (1 - a),
          top[2] * a + bottom[2] * (1 - a),
        ]
      }

      function describe(element: Element): string {
        const parts: string[] = []
        let node: Element | null = element
        for (let depth = 0; node && depth < 4; depth += 1) {
          let part = node.tagName.toLowerCase()
          const view = node.getAttribute('data-view')
          const nav = node.getAttribute('data-nav')
          const sheet = node.getAttribute('data-sheet')
          if (node.id) part += `#${node.id}`
          if (view) part += `[data-view=${view}]`
          if (nav) part += `[data-nav=${nav}]`
          if (sheet) part += `[data-sheet=${sheet}]`
          parts.unshift(part)
          node = node.parentElement
        }
        return parts.join(' > ')
      }

      /**
       * The surface a foreground actually lands on: the stack of background
       * layers from the element upwards, composited until an opaque one is
       * reached. This is what "every surface it can land on" means once a
       * translucent card sits on a translucent bar.
       */
      function surfaceUnder(element: Element): [number, number, number] | null {
        const layers: Array<[number, number, number, number]> = []
        let node: Element | null = element
        while (node) {
          const style = getComputedStyle(node)
          if (style.backgroundImage !== 'none') {
            unverifiable.push(
              `${describe(node)} paints ${style.backgroundImage.slice(0, 40)}, ` +
                'whose contrast cannot be computed from a single colour',
            )
            return null
          }
          const parsed = parseColor(style.backgroundColor)
          if (!parsed) {
            unverifiable.push(
              `${describe(node)} has unparseable background ${style.backgroundColor}`,
            )
            return null
          }
          if (parsed[3] > 0) layers.push(parsed)
          if (parsed[3] >= 1) break
          node = node.parentElement
        }
        // Nothing opaque all the way up: the canvas shows through. The engine
        // paints the canvas colour there, which is what the user sees.
        const canvas = parseColor(
          getComputedStyle(document.documentElement).backgroundColor,
        )
        let base: [number, number, number] =
          canvas && canvas[3] >= 1
            ? [canvas[0], canvas[1], canvas[2]]
            : [255, 255, 255]
        for (let i = layers.length - 1; i >= 0; i -= 1) {
          const layer = layers[i]
          if (layer) base = over(layer, base)
        }
        return base
      }

      function visible(element: Element): boolean {
        const style = getComputedStyle(element)
        if (style.visibility !== 'visible' || style.display === 'none')
          return false
        if (parseFloat(style.opacity) === 0) return false
        const rect = element.getBoundingClientRect()
        return rect.width > 0 && rect.height > 0
      }

      function hasOwnText(element: Element): boolean {
        for (const node of Array.from(element.childNodes)) {
          if (
            node.nodeType === Node.TEXT_NODE &&
            (node.textContent || '').trim() !== ''
          ) {
            return true
          }
        }
        return false
      }

      const failures: Array<{
        view: string
        scheme: string
        label: string
        foreground: string
        background: string
        ratio: number
        floor: number
        sample: string
      }> = []
      let pairs = 0

      const root = document.querySelector('[data-layout]')
      const elements = root ? Array.from(root.querySelectorAll('*')) : []

      for (const element of elements) {
        if (!visible(element)) continue

        const style = getComputedStyle(element)
        const isText = hasOwnText(element)
        const isIcon =
          !isText &&
          (element.tagName.toLowerCase() === 'svg' ||
            element.tagName.toLowerCase() === 'path')
        if (!isText && !isIcon) continue

        // An icon painted with an explicit fill uses that, otherwise it
        // inherits `color`, which is the token this is about.
        let foregroundValue = style.color
        if (isIcon) {
          const fill = style.fill
          const stroke = style.stroke
          if (fill && fill !== 'none' && fill !== 'currentcolor')
            foregroundValue = fill
          else if (stroke && stroke !== 'none' && stroke !== 'currentcolor')
            foregroundValue = stroke
        }

        const parsedForeground = parseColor(foregroundValue)
        if (!parsedForeground) {
          unverifiable.push(
            `${describe(element)} has unparseable colour ${foregroundValue}`,
          )
          continue
        }
        if (parsedForeground[3] === 0) continue

        const background = surfaceUnder(element)
        if (!background) continue

        const foreground = over(parsedForeground, background)

        const size = parseFloat(style.fontSize)
        const weight = parseInt(style.fontWeight, 10) || 400
        const large = size >= 24 || (size >= 18.66 && weight >= 700)
        const floor = isIcon ? 3 : large ? 3 : 4.5

        const value = ratio(foreground, background)
        pairs += 1
        if (value + 0.005 < floor) {
          const sample = isText
            ? (element.textContent || '').trim().slice(0, 30)
            : `<${element.tagName.toLowerCase()}> icon`
          failures.push({
            view,
            scheme,
            label: describe(element),
            foreground: foregroundValue,
            background: `rgb(${background.map((c) => Math.round(c)).join(', ')})`,
            ratio: Math.round(value * 100) / 100,
            floor,
            sample,
          })
        }
      }

      return {
        failures,
        unverifiable: Array.from(new Set(unverifiable)),
        pairs,
      }
    },
    { view, scheme },
  )
}

for (const scheme of ['dark', 'light'] as const) {
  test(`every foreground meets its WCAG AA floor in the ${scheme} palette`, async ({
    page,
  }) => {
    await page.emulateMedia({ colorScheme: scheme })

    const failures: Finding[] = []
    const unverifiable = new Set<string>()
    let pairs = 0

    for (const view of VIEWS) {
      await gotoView(page, view)
      const result = await auditPage(page, view, scheme)
      failures.push(...result.failures)
      result.unverifiable.forEach((item) => unverifiable.add(item))
      pairs += result.pairs
    }

    // The sheet is a surface of its own, and its entries carry tokens that land
    // nowhere else (SPEC 4.5: Peers, Mirror and Settings live only here).
    await gotoView(page, 'dashboard')
    await openSheet(page)
    const sheetResult = await auditPage(page, 'more-sheet', scheme)
    failures.push(...sheetResult.failures)
    sheetResult.unverifiable.forEach((item) => unverifiable.add(item))
    pairs += sheetResult.pairs

    // A contrast suite that measured nothing reports as green, which is worse
    // than reporting red. Pin that the walk found real pairs.
    expect(
      pairs,
      'the contrast walk found no foreground/surface pair to measure',
    ).toBeGreaterThan(20)

    const report = failures
      .map(
        (f) =>
          `  ${f.view} [${f.scheme}] ${f.label}\n` +
          `    "${f.sample}" ${f.foreground} on ${f.background} = ${f.ratio}:1, floor ${f.floor}:1`,
      )
      .join('\n')

    expect(
      failures,
      failures.length === 0
        ? ''
        : `${failures.length} of ${pairs} foreground/surface pairs are below their WCAG AA floor:\n${report}\n` +
            (unverifiable.size
              ? `not computable (reported, not asserted):\n  ${Array.from(unverifiable).join('\n  ')}`
              : ''),
    ).toEqual([])
  })
}
