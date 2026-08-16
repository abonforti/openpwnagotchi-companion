import { vitePreprocess } from '@sveltejs/vite-plugin-svelte'

// vitePreprocess is what makes <script lang="ts"> work inside a component.
// svelte-check reads this file too, so the two cannot disagree.
export default {
  preprocess: vitePreprocess(),
}
