import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig(({ mode }) => {
  // Load environment variables from:
  // - process environment (Docker Compose)
  // - .env files (if present)
  //
  // The third argument '' means:
  //   load ALL variables, not just those prefixed with VITE_
  const env = loadEnv(mode, process.cwd(), '')

  // --------------------------------------------------------------------------
  // Proxy targets (server-side, run inside Docker)
  // --------------------------------------------------------------------------
  // Evaluated by the Vite dev server (Node.js), NOT the browser. Therefore:
  // - Must use Docker service names ("curator-backend", "dataexplorer-backend")
  // - Must NOT use localhost (would point to this container)
  //
  // Because they resolve server-side, no browser-facing value has to name the
  // host: the browser only ever talks to the origin that served the page, so
  // the same config works on localhost, on an IP, through an SSH tunnel, or
  // behind a domain. Override either one to run a backend outside Compose.
  const write = env.VITE_PROXY_TARGET || 'http://curator-backend:8000'
  const read = env.VITE_READ_PROXY_TARGET || 'http://dataexplorer-backend:8001'

  return {
    plugins: [react()],

    server: {
      port: 5173,

      // ----------------------------------------------------------------------
      // The same prefix split the production edge performs
      // ----------------------------------------------------------------------
      // These three rules mirror the handle blocks in proxy/Caddyfile, which is
      // the point: dev and prod route one URL space the same way, so a call
      // that works here works there. Plain prefix matching suffices because each
      // prefix names the service that owns it, so no path is shared between the
      // two backends.
      //
      // devnote: unlike the edge, there is no catch-all 404 for unmatched
      // /api/* here — Vite's SPA fallback answers those with index.html, so a
      // typo'd route reads as 200 + HTML rather than a clean miss. Upgrade path
      // is a small dev-server middleware if that ever costs real debugging time.
      proxy: {
        '/api/v1/curator': { target: write, changeOrigin: true },
        '/api/v1/dataexplorer': { target: read, changeOrigin: true },
        // The reader's unversioned data space — entity HTML pages and file
        // content, reached by page navigation rather than by axios.
        '/rdf': { target: read, changeOrigin: true },
      },
    },
  }
})
