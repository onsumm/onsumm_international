/**
 * The SSR half of the build. Bundles prerender/entry-server.tsx into plain ESM
 * that Node can import, so prerender/prerender.mjs can call renderRoute() for
 * each marketing path. Output is a build artefact, not something shipped — it
 * lands in prerender/.out and never in dist.
 */
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  build: {
    ssr: 'prerender/entry-server.tsx',
    outDir: 'prerender/.out',
    emptyOutDir: true,
    // The client build owns dist/ and has already written its manifest by the
    // time this runs; anything logged here about missing assets is expected.
    rollupOptions: {
      output: { entryFileNames: 'entry-server.mjs', format: 'es' },
    },
  },
})
