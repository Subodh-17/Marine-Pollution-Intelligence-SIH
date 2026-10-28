import { describe, it, expect, vi, beforeEach } from 'vitest'

// --- Parts 2 & 3: App-level integration coverage, mocking `../api` the
// same way smoke.test.jsx does (not axios directly) - these exercise the
// actual click -> modal -> onSubmit -> runRealSatellite wiring, including
// React state and the two re-entrancy guards (UploadSatelliteModal's
// submittingRef and App.jsx's satelliteUploadInFlightRef). ---
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import React, { StrictMode } from 'react'
import investigationFixture from './fixtures/investigation.json'

vi.mock('react-leaflet', () => {
  const Stub = ({ children, ...props }) => {
    const skip = new Set(['eventHandlers', 'pathOptions', 'scrollWheelZoom', 'positions', 'center', 'bounds', 'attribution', 'url', 'radius', 'zoom', 'zoomControl'])
    const domProps = Object.fromEntries(Object.entries(props).filter(([k]) => !skip.has(k)))
    return React.createElement('div', { 'data-testid': 'leaflet-stub', ...domProps }, children)
  }
  return {
    MapContainer: Stub, TileLayer: Stub, ImageOverlay: Stub, Polygon: Stub,
    Polyline: Stub, Circle: Stub, CircleMarker: Stub, Marker: Stub, Popup: Stub,
    useMap: () => ({ fitBounds: vi.fn() }),
  }
})
vi.mock('leaflet', () => ({ default: { divIcon: vi.fn(() => ({})) } }))

const uploadSatelliteImage = vi.fn()
const getJobStatus = vi.fn()
vi.mock('../api', () => ({
  default: {
    runInvestigation: vi.fn(() => Promise.resolve(investigationFixture)),
    uploadAis: vi.fn(() => Promise.resolve({ status: 'uploaded' })),
    getInvestigation: vi.fn(() => Promise.resolve(investigationFixture)),
    uploadSatelliteImage: (...args) => uploadSatelliteImage(...args),
    getJobStatus: (...args) => getJobStatus(...args),
    reanalyzeAis: vi.fn(),
    getReportMarkdown: vi.fn(() => Promise.resolve('# report')),
    reportPdfUrl: (id) => `/api/report/${id}?fmt=pdf`,
    vesselsCsvUrl: (id) => `/api/export/${id}/vessels.csv`,
    getMlStatus: vi.fn(() => Promise.resolve({ torch_available: true, classifier_loaded: true, segmenter_loaded: true, load_errors: [], provenance: 'ML_PROTOTYPE_SYNTHETIC_TRAINED', genuine_evaluation_metrics_available: false, num_test_samples: null, model_info: {} })),
    getMlTrainingHistory: vi.fn(() => Promise.resolve({ available: false, epochs: [], reason: 'n/a' })),
    getMlMetrics: vi.fn(() => Promise.resolve({ metrics_available: false, reason: 'n/a' })),
    getMlDatasetStatus: vi.fn(() => Promise.resolve({ status: 'missing', parts: {}, category_counts: {}, issues: [] })),
  },
}))

import App from '../App'
import { ThemeProvider } from '../lib/theme.jsx'

function renderApp(ui) {
  return render(<ThemeProvider>{ui}</ThemeProvider>)
}

/** Opens the "Use a real satellite image" modal and selects one fake file,
 * leaving the "Upload & run" button enabled and ready to click - the exact
 * point every test below starts from. */
function openModalAndSelectFile() {
  fireEvent.click(screen.getByText('ANALYZE SATELLITE IMAGE'))
  const file = new File(['fake-tiff-bytes'], 'scene.tif', { type: 'image/tiff' })
  const input = document.querySelector('input[type="file"]')
  Object.defineProperty(input, 'files', { value: [file], writable: false })
  fireEvent.change(input)
  return screen.getByText('Upload & run')
}

/** A controllable promise, so a test can click the button while the
 * "backend" (the mock) is still deliberately pending - exactly the window
 * during which a duplicate click/request could otherwise slip through. */
function deferred() {
  let resolve, reject
  const promise = new Promise((res, rej) => { resolve = res; reject = rej })
  return { promise, resolve, reject }
}

describe('Real satellite upload: duplicate-request prevention and error/success lifecycle (Parts 2 & 3 regression)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    window.sessionStorage.clear()
  })

  it('one deliberate click produces exactly one POST, even under React StrictMode', async () => {
    uploadSatelliteImage.mockImplementation(() => Promise.resolve(investigationFixture))
    renderApp(<StrictMode><App /></StrictMode>)

    fireEvent.click(openModalAndSelectFile())

    await waitFor(() => expect(screen.getByText(/Spill detected/i)).toBeInTheDocument(), { timeout: 8000 })
    expect(uploadSatelliteImage).toHaveBeenCalledTimes(1)
  })

  it('a rapid double-click on "Upload & run" while the request is still pending sends exactly one request', async () => {
    const d = deferred()
    uploadSatelliteImage.mockImplementation(() => d.promise)
    renderApp(<App />)

    const button = openModalAndSelectFile()
    // Two clicks fired back to back, before the mock ever resolves and
    // before React has necessarily re-rendered the button as disabled -
    // this is precisely the race the submittingRef/satelliteUploadInFlightRef
    // guards exist to close.
    fireEvent.click(button)
    fireEvent.click(button)

    expect(uploadSatelliteImage).toHaveBeenCalledTimes(1)
    d.resolve(investigationFixture)
    await waitFor(() => expect(screen.getByText(/Spill detected/i)).toBeInTheDocument(), { timeout: 8000 })
    // Still exactly one, after the deferred request finally resolves too.
    expect(uploadSatelliteImage).toHaveBeenCalledTimes(1)
  })

  it('a genuine failure shows the real backend error detail and never a fabricated success', async () => {
    // extractErrorDetail (App.jsx) prefers the backend's own detail message
    // over a generic fallback whenever one is present - asserting on that
    // exact real message (not a paraphrase) is the point of this test.
    uploadSatelliteImage.mockImplementation(() => Promise.reject({
      response: { status: 400, data: { detail: 'Unsupported file type.' } },
    }))
    renderApp(<App />)

    fireEvent.click(openModalAndSelectFile())

    await waitFor(() => expect(screen.getByText('Unsupported file type.')).toBeInTheDocument())
    expect(screen.queryByText(/Spill detected/i)).not.toBeInTheDocument()
  })

  it('a failure with no backend detail message falls back to the generic satellite-upload error text', async () => {
    uploadSatelliteImage.mockImplementation(() => Promise.reject(new Error('Network Error')))
    renderApp(<App />)

    fireEvent.click(openModalAndSelectFile())

    await waitFor(() => expect(screen.getByText(/Investigation on the uploaded satellite image failed/i)).toBeInTheDocument())
    expect(screen.queryByText(/Spill detected/i)).not.toBeInTheDocument()
  })

  it('a slow-but-genuinely-successful upload clears a previous failure banner - never a stale failure shown alongside a successful investigation', async () => {
    // First attempt: a real failure (e.g. what a premature client-side
    // timeout used to look like before Part 1's fix) - error banner shown.
    uploadSatelliteImage.mockImplementationOnce(() => Promise.reject({
      response: { status: 400, data: { detail: 'Unsupported file type.' } },
    }))
    renderApp(<App />)
    fireEvent.click(openModalAndSelectFile())
    await waitFor(() => expect(screen.getByText('Unsupported file type.')).toBeInTheDocument())

    // Second attempt: this time it genuinely succeeds, but slowly (a
    // deferred promise standing in for the ~128s a real large-image
    // upload can legitimately take now that Part 1 gives it room to).
    const d = deferred()
    uploadSatelliteImage.mockImplementation(() => d.promise)
    fireEvent.click(openModalAndSelectFile())

    // The instant a new attempt starts, the stale failure banner is gone -
    // runRealSatellite clears `error` synchronously before awaiting
    // anything, so this must be true well before the deferred promise
    // ever resolves.
    expect(screen.queryByText('Unsupported file type.')).not.toBeInTheDocument()

    d.resolve(investigationFixture)
    await waitFor(() => expect(screen.getByText(/Spill detected/i)).toBeInTheDocument(), { timeout: 8000 })
    // And once the real success lands, the failure banner is still gone -
    // no stale-error-next-to-fresh-success state exists at any point.
    expect(screen.queryByText('Unsupported file type.')).not.toBeInTheDocument()
  })

  it('polls background job status and transitions to dashboard upon completion', async () => {
    uploadSatelliteImage.mockImplementation(() => Promise.resolve({
      id: 'job-phase12-test',
      job_type: 'satellite_upload',
      status: 'running',
      progress_stage: 'oil_slick_detected',
    }))
    getJobStatus
      .mockImplementationOnce(() => Promise.resolve({
        id: 'job-phase12-test',
        status: 'running',
        progress_stage: 'hindcast_completed',
      }))
      .mockImplementationOnce(() => Promise.resolve({
        id: 'job-phase12-test',
        status: 'completed',
        progress_stage: 'completed',
        result_investigation_id: 'inv-test-id',
      }))

    renderApp(<App />)
    fireEvent.click(openModalAndSelectFile())

    await waitFor(() => expect(screen.getByText(/Lagrangian drift hindcast calculated/i)).toBeInTheDocument(), { timeout: 5000 })
    await waitFor(() => expect(screen.getByText(/Spill detected/i)).toBeInTheDocument(), { timeout: 8000 })
  })

  it('handles background job failure by displaying the server error message', async () => {
    uploadSatelliteImage.mockImplementation(() => Promise.resolve({
      id: 'job-failed-test',
      job_type: 'satellite_upload',
      status: 'running',
      progress_stage: 'sar_preprocessing_completed',
    }))
    getJobStatus.mockImplementationOnce(() => Promise.resolve({
      id: 'job-failed-test',
      status: 'failed',
      progress_stage: 'failed',
      error: 'Corrupt GeoTIFF tile offset header.',
    }))

    renderApp(<App />)
    fireEvent.click(openModalAndSelectFile())

    await waitFor(() => expect(screen.getByText(/Corrupt GeoTIFF tile offset header/i)).toBeInTheDocument(), { timeout: 5000 })
    expect(screen.queryByText(/Spill detected/i)).not.toBeInTheDocument()
  })

  it('handles server restart during job polling with an actionable message', async () => {
    uploadSatelliteImage.mockImplementation(() => Promise.resolve({
      id: 'job-interrupted-test',
      job_type: 'satellite_upload',
      status: 'running',
      progress_stage: 'sar_preprocessing_completed',
    }))
    getJobStatus.mockImplementationOnce(() => Promise.resolve({
      id: 'job-interrupted-test',
      status: 'interrupted',
      progress_stage: 'interrupted',
    }))

    renderApp(<App />)
    fireEvent.click(openModalAndSelectFile())

    await waitFor(() => expect(screen.getByText(/Analysis was interrupted by a server restart/i)).toBeInTheDocument(), { timeout: 5000 })
  })
})
