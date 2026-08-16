<script module lang="ts">
  /**
   * Toggle the `inert` attribute rather than the DOM property: the attribute is
   * what the platform defines inertness by, it is the spelling a test can see,
   * and Svelte would otherwise compile `inert={...}` to a property assignment.
   *
   * It lives here because the dialog is what defines "inert while I am open":
   * the shell and the bar import it to take themselves out of the accessibility
   * tree for as long as the sheet has the user's attention.
   */
  export function inertWhen(node: HTMLElement, inert: boolean) {
    node.toggleAttribute('inert', inert)
    return {
      update: (next: boolean) => node.toggleAttribute('inert', next),
    }
  }
</script>

<script lang="ts">
  import { followLink, sheetRoutes } from '../lib/router'

  interface Props {
    open: boolean
    /**
     * Requested close. The caller owns the open flag and returns focus to the
     * More control, because that control lives in the bar and not in here.
     */
    onclose: () => void
  }

  let { open, onclose }: Props = $props()

  let rootEl: HTMLDivElement | undefined = $state()

  // Anything the user could reach with Tab, and nothing they could not: a
  // disabled, hidden or deliberately unreachable control must not become the
  // edge of the trap, or Tab lands somewhere invisible.
  const FOCUSABLE =
    'a[href]:not([tabindex="-1"]):not([hidden]), ' +
    'button:not([disabled]):not([tabindex="-1"]):not([hidden])'

  function focusable(): HTMLElement[] {
    if (!rootEl) {
      return []
    }
    return Array.from(rootEl.querySelectorAll<HTMLElement>(FOCUSABLE))
  }

  // Focus enters the dialog on open. Runs after the DOM update, so the entries
  // exist and `inert` has already been lifted.
  $effect(() => {
    if (open && rootEl) {
      focusable()[0]?.focus()
    }
  })

  // Listened on the window rather than on the dialog: focus is trapped inside,
  // but a key event raised anywhere still has to close a modal dialog. The trap
  // is the keyboard half of the story; `inert` on everything else is the other.
  $effect(() => {
    if (!open) {
      return
    }
    const onKeyDown = (event: KeyboardEvent): void => {
      if (event.key === 'Escape') {
        event.preventDefault()
        onclose()
        return
      }
      if (event.key !== 'Tab') {
        return
      }
      const items = focusable()
      const first = items[0]
      const last = items[items.length - 1]
      if (!first || !last) {
        return
      }
      const active = document.activeElement
      if (event.shiftKey && active === first) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && active === last) {
        event.preventDefault()
        first.focus()
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  })
</script>

<!-- At rest the whole thing is inert, so its entries are neither focusable nor
     in the accessibility tree while the bar is what the user is using. -->
<div class="sheet-root" class:open bind:this={rootEl} use:inertWhen={!open}>
  <div
    id="more-sheet"
    class="sheet"
    role="dialog"
    aria-modal="true"
    aria-labelledby="more-sheet-title"
  >
    <h2 id="more-sheet-title">More</h2>
    <ul>
      {#each sheetRoutes as route (route.id)}
        <li>
          <a
            href={route.path}
            data-sheet={route.id}
            onclick={(event) => {
              if (followLink(event, route.path)) {
                onclose()
              }
            }}
          >
            {route.label}
          </a>
        </li>
      {/each}
    </ul>
  </div>

  <!-- After the dialog in the DOM so the entries come first in the tab cycle,
       behind it in paint order. It is a button because a backdrop that closes
       the dialog is a control, and a div with a click handler is not one. -->
  <button type="button" class="backdrop" data-backdrop aria-label="Close" onclick={onclose}
  ></button>
</div>

<style>
  .sheet-root {
    /* Absolute for the same reason as the bar: it covers the shell, which is
       the box that spans the screen, not the shorter viewport iOS declares in
       standalone (SPEC 4.2.1). */
    position: absolute;
    inset: 0;
    z-index: 30;
    visibility: hidden;
    /* The hide is delayed by the length of the slide-out, otherwise visibility
       snaps in the frame the class drops and the close animation is never seen.
       The delay sits inside the shorthand so the prefers-reduced-motion
       override in app.css collapses it too, rather than leaving a user who
       asked for less motion staring at a sheet for 200ms after it closed. */
    transition: visibility 0s linear 200ms;
  }

  .sheet-root.open {
    visibility: visible;
    transition: visibility 0s linear 0s;
  }

  .backdrop {
    position: absolute;
    inset: 0;
    z-index: 0;
    width: 100%;
    padding: 0;
    border: 0;
    background: rgb(0 0 0 / 55%);
    opacity: 0;
    transition: opacity 200ms ease-out;
  }

  .sheet {
    position: absolute;
    z-index: 1;
    inset: auto 0 0 0;
    padding: 0.5rem max(1rem, var(--safe-right)) calc(0.5rem + var(--safe-bottom))
      max(1rem, var(--safe-left));
    background: var(--surface);
    border-top: 1px solid var(--border);
    border-radius: 16px 16px 0 0;
    /* Transform and opacity only: this animates on a 120 Hz display. */
    transform: translateY(100%);
    transition: transform 200ms ease-out;
  }

  .sheet-root.open .sheet {
    transform: translateY(0);
  }

  .sheet-root.open .backdrop {
    opacity: 1;
  }

  h2 {
    margin: 0.25rem 0 0.5rem;
    font-size: 0.8125rem;
    font-weight: 600;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    color: var(--text-dim);
  }

  ul {
    margin: 0;
    padding: 0;
    list-style: none;
  }

  a {
    display: flex;
    align-items: center;
    min-height: 52px;
    color: var(--text);
    text-decoration: none;
  }

  li + li a {
    border-top: 1px solid var(--border);
  }

  a:focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: -2px;
  }
</style>
