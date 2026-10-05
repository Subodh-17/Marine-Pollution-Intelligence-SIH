import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
//
// NOTE on the proxy target below: the backend (uvicorn) binds to the IPv4
// loopback address 127.0.0.1 by default (see `backend/app/main.py`'s
// documented run command, which never passes --host). Node.js 17+ resolves
// the *hostname* "localhost" using the OS's address-family preference
// (RFC 6724), which on many machines (Windows, macOS, and Linux hosts with
// an `::1 localhost` entry in /etc/hosts) returns the IPv6 address ::1
// first. Vite's dev proxy runs inside Node, so a target of
// 'http://localhost:8000' can end up dialing [::1]:8000 - where nothing is
// listening - and fail with ECONNREFUSED, even though the backend is up
// and `curl http://127.0.0.1:8000/...` succeeds. Using the literal IPv4
// address here removes the DNS/address-family ambiguity entirely.
const BACKEND_ORIGIN = 'http://127.0.0.1:8000'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      // Forwarded as-is (no `rewrite`): the backend's routes are already
      // registered under the /api prefix (see `backend/app/main.py`), and
      // `frontend/src/api.js` calls it with a relative `/api/...` baseURL,
      // so the path reaching the backend is exactly `/api/ml/status` etc.
      // Do not add a `rewrite` here - that would strip `/api` and 404, and
      // do not change api.js's baseURL to also include `/api` - that would
      // produce `/api/api/ml/status`.
      '/api': {
        target: BACKEND_ORIGIN,
        changeOrigin: true,
      },
    },
  },
})
