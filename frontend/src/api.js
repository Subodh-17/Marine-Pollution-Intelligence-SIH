import axios from 'axios'

// Base URL resolution:
// - Local dev (`npm run dev`): relative '/api', proxied by Vite's dev
//   server to the backend (see vite.config.js) - unaffected by anything
//   below, since VITE_API_BASE_URL is normally unset locally.
// - Production build (`npm run build`): the built static files get
//   deployed somewhere (e.g. Vercel) with no Vite dev server and
//   therefore no proxy - a relative '/api' would resolve against the
//   FRONTEND's own origin, not the backend's, and every request would
//   404. VITE_API_BASE_URL must be set at BUILD time (Vite inlines
//   import.meta.env.* values into the bundle - this is not readable or
//   changeable at runtime after the build, it has to be set in the
//   hosting provider's build-time environment variables) to the deployed
//   backend's full origin, e.g. https://your-backend.onrender.com/api.
//   See docs/DEPLOYMENT.md.
// A production build with VITE_API_BASE_URL left unset falls back to the
// same relative '/api' as dev - a deliberate, honest failure mode (a
// same-origin 404 an engineer can diagnose from the Network tab) rather
// than silently defaulting to a localhost URL, which would work during a
// demo on the deploying engineer's own machine and then silently break
// for every actual visitor.
const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || '/api'

// For the two download links that must be plain <a href> browser
// navigations rather than an axios call (see ReportTab.jsx's comment on
// why: the PDF and vessel-CSV endpoints send a real
// `Content-Disposition: attachment` header, so letting the browser
// navigate directly gives a real save-file prompt for free). A plain
// <a href="/api/..."> resolves against the CURRENT PAGE's origin, not
// axios's configured baseURL - harmless in local dev (frontend and
// backend share an origin through the Vite proxy) but a real bug in
// production, where it would silently request
// https://<frontend>.vercel.app/api/... instead of the deployed backend.
// This helper guarantees both call sites resolve exactly the same way
// axios's `client` below does.
function apiUrl(path) {
  return `${API_BASE_URL}${path}`
}

const client = axios.create({ baseURL: API_BASE_URL, timeout: 30000 })

// Dev-only diagnostics: when a request fails, log the exact URL and status
// that failed so a "could not reach backend" error in the UI can be
// traced back to its real cause (proxy misconfiguration, backend down,
// wrong path, etc.) instead of staying opaque. Never runs in production
// builds (import.meta.env.DEV is stripped/false there), and never logs
// request/response bodies, headers, or anything that could carry secrets.
if (import.meta.env.DEV) {
  client.interceptors.response.use(
    (response) => response,
    (error) => {
      const req = error.config || {}
      const fullUrl = `${req.baseURL || ''}${req.url || ''}`
      if (error.response) {
        // Server responded, but with an error status.
        console.error(
          `[api] ${req.method?.toUpperCase() || 'GET'} ${fullUrl} -> HTTP ${error.response.status}`,
        )
      } else if (error.request) {
        // Request was sent but no response came back - this is the
        // network-level failure (e.g. proxy target unreachable) that
        // surfaces to users as "Could not reach the backend".
        console.error(
          `[api] ${req.method?.toUpperCase() || 'GET'} ${fullUrl} -> no response (network error: ${error.message}). ` +
            'Check that the backend is running and that the Vite dev proxy target in vite.config.js is reachable.',
        )
      } else {
        console.error(`[api] request setup failed for ${fullUrl}:`, error.message)
      }
      return Promise.reject(error)
    },
  )
}

export const api = {
  health: () => client.get('/health').then(r => r.data),

  getCommandCenter: () => client.get('/command-center').then(r => r.data),

  getInvestigations: (limit = 20) => client.get('/investigations', { params: { limit } }).then(r => r.data),

  // Given a 75s timeout (below Render's 100s proxy threshold): allows absorbing a
  // 35-50s container cold start plus live Open-Meteo fetches (~10-15s), avoiding client aborts.
  runInvestigation: ({ mode = 'demo', searchRadiusKm = 50, timeWindowHours = 12 } = {}) =>
    client.post('/spill/detect', null, {
      params: { mode, search_radius_km: searchRadiusKm, time_window_hours: timeWindowHours },
      timeout: 75000,
    }).then(r => r.data),

  uploadAis: (file) => {
    const form = new FormData()
    form.append('file', file)
    return client.post('/ais/upload', form).then(r => r.data)
  },

  // Given its own long per-request timeout (5 min), NOT the client's global
  // 30s default: this endpoint runs the full detection -> geometry -> drift
  // -> AIS -> attribution pipeline against a real, potentially large
  // GeoTIFF/PNG (and, for detector='ml', a genuine PyTorch/ONNX forward
  // pass), which has been observed taking well over 30s end to end on a
  // real image (~128s in one recorded run) - a real, expected duration for
  // this one endpoint, not a hung request. The previous 30s *global*
  // timeout aborted the client side of the request well before the backend
  // (which keeps processing regardless of the client's own abort) could
  // finish, so the frontend reported a failure for a request that then
  // went on to genuinely succeed server-side moments later - see App.jsx's
  // runRealSatellite for how a request that legitimately fails (a real
  // network error, a 4xx/5xx, or this same timeout if 5 minutes still isn't
  // enough) is still surfaced honestly. Every other endpoint keeps the
  // client's normal 30s timeout; only this long-running upload gets one.
  uploadSatelliteImage: (file, { detector = 'classical', centerLat = 19.05, centerLon = 71.20, asyncJob = false } = {}) => {
    const form = new FormData()
    form.append('file', file)
    const params = { detector, center_lat: centerLat, center_lon: centerLon }
    if (asyncJob) {
      params.async_job = true
    }
    return client.post('/satellite/upload', form, {
      params,
      timeout: 300000,
    }).then(r => r.data)
  },

  runSampleSatellite: ({ sampleName = 'demo_oil_scene_01.tif', detector = 'classical', centerLat = 19.05, centerLon = 71.20, asyncJob = false } = {}) => {
    const params = { sample_name: sampleName, detector, center_lat: centerLat, center_lon: centerLon }
    if (asyncJob) {
      params.async_job = true
    }
    return client.post('/satellite/sample', null, {
      params,
      timeout: 120000,
    }).then(r => r.data)
  },

  getJobStatus: (id) => client.get('/jobs/' + id).then(r => r.data),

  getSampleScenes: () => client.get('/satellite/samples').then(r => r.data),

  getMlStatus: () => client.get('/ml/status').then(r => r.data),

  getMlTrainingHistory: () => client.get('/ml/training-history').then(r => r.data),

  getMlMetrics: () => client.get('/ml/metrics').then(r => r.data),

  // Deliberately fetched independently of the three calls above (see
  // MlTrainingTab.jsx) and given a longer timeout: this one endpoint
  // performs a full filesystem scan and TIFF image/mask validation over
  // the (potentially very large) dataset on disk, so it can legitimately
  // take much longer than a normal API call even though the backend
  // caches its result between calls (see app/ml/dataset.py). `refresh:
  // true` forces the backend to bypass that cache and rescan now - this
  // is what the ML Training page's "Refresh dataset status" button sends.
  getMlDatasetStatus: ({ refresh = false } = {}) =>
    client.get('/ml/dataset-status', { params: refresh ? { refresh: true } : undefined, timeout: 120000 }).then(r => r.data),

  getInvestigation: (id) => client.get(`/spill/${id}`).then(r => r.data),

  reanalyzeAis: (id, { searchRadiusKm, timeWindowHours }) =>
    client.post('/ais/analyze', null, {
      params: { investigation_id: id, search_radius_km: searchRadiusKm, time_window_hours: timeWindowHours },
    }).then(r => r.data),

  getReportMarkdown: (id) => client.get(`/report/${id}`, { params: { fmt: 'markdown' } }).then(r => r.data.markdown),

  reportPdfUrl: (id) => apiUrl(`/report/${id}?fmt=pdf`),

  vesselsCsvUrl: (id) => apiUrl(`/export/${id}/vessels.csv`),

  getSpillPolygonGeojson: (id) => client.get(`/export/${id}/spill_polygon.geojson`).then(r => r.data),

  getVesselTracksGeojson: (id) => client.get(`/export/${id}/vessel_tracks.geojson`).then(r => r.data),
}

export default api
