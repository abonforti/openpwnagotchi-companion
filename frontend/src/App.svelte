<script lang="ts">
  // The shell, and deliberately nothing else. The eight views of SPEC 4.5
  // arrive one pull request at a time; this exists so the project builds,
  // typechecks and installs as a PWA before any of them do.
</script>

<main>
  <h1>openpwnagotchi companion</h1>
  <p>
    The interface is not built yet. This is the application shell: it builds, registers a service
    worker, and installs to the Home Screen.
  </p>
  <p class="dim">Add to Home Screen to check that standalone mode and the icons are right.</p>
</main>

<style>
  main {
    display: flex;
    flex-direction: column;
    justify-content: center;
    min-height: 100dvh;
    padding: 1.5rem;
    gap: 0.5rem;
  }

  h1 {
    margin: 0;
    font-size: 1.25rem;
    letter-spacing: -0.01em;
  }

  p {
    margin: 0;
    max-width: 34ch;
  }

  .dim {
    color: var(--text-dim);
  }
</style>
