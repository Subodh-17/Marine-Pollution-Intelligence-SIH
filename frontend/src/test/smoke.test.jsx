import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import React from 'react'
import investigationFixture from './fixtures/investigation.json'

// --- Mock react-leaflet / leaflet (see rationale in mockLeaflet.js) ---
// Props real react-leaflet consumes internally and never forwards to the
// DOM. Stripped here too so this stub doesn't emit misleading "React does
// not recognize the `x` prop" warnings that don't reflect real behaviour.
const LEAFLET_NON_DOM_PROPS = new Set([
  'eventHandlers', 'pathOptions', 'scrollWheelZoom', 'positions', 'center',
  'bounds', 'attribution', 'url', 'radius', 'zoom', 'zoomControl',
])
vi.mock('react-leaflet', () => {
  const Stub = ({ children, ...props }) => {
    const domProps = Object.fromEntries(
      Object.entries(props).filter(([key]) => !LEAFLET_NON_DOM_PROPS.has(key))
    )
    return React.createElement('div', { 'data-testid': 'leaflet-stub', ...domProps }, children)
  }
  return {
    MapContainer: Stub, TileLayer: Stub, ImageOverlay: Stub, Polygon: Stub,
    Polyline: Stub, Circle: Stub, CircleMarker: Stub, Marker: Stub, Popup: Stub,
    useMap: () => ({ fitBounds: vi.fn() }),
  }
})
vi.mock('leaflet', () => ({ default: { divIcon: vi.fn(() => ({})) } }))

// --- Mock the API layer with a real captured backend response fixture ---
vi.mock('../api', () => ({
  default: {
    runInvestigation: vi.fn(() => Promise.resolve(investigationFixture)),
    uploadAis: vi.fn(() => Promise.resolve({ status: 'uploaded' })),
    getInvestigation: vi.fn(() => Promise.resolve(investigationFixture)),
    reanalyzeAis: vi.fn(() => Promise.resolve({
      search_radius_km: 80, time_window_hours: 18,
      vessel_count: investigationFixture.candidates.length,
      candidates: investigationFixture.candidates,
    })),
    getReportMarkdown: vi.fn(() => Promise.resolve('# MARINE POLLUTION INTELLIGENCE INVESTIGATION REPORT\n\nTest report body.')),
    reportPdfUrl: (id) => `/api/report/${id}?fmt=pdf`,
    vesselsCsvUrl: (id) => `/api/export/${id}/vessels.csv`,
    getMlStatus: vi.fn(() => Promise.resolve({
      torch_available: true, classifier_loaded: true, segmenter_loaded: true, load_errors: [],
      provenance: 'ML_PROTOTYPE_SYNTHETIC_TRAINED', genuine_evaluation_metrics_available: false,
      num_test_samples: null,
      model_info: {
        model_name: 'SpillUNet (synthetic-trained prototype)', model_version: 'prototype_v1',
        architecture: 'SpillUNet', dataset_used_for_training: 'Synthetically generated training data',
        dataset_type: 'synthetic_generated', dataset_dois: null, training_date: null, dataset_split: null,
        segmenter_checkpoint_filename: 'spill_unet.pt', segmenter_checkpoint_size_bytes: 499010,
        limitations: ['Not trained on real Sentinel-1 SAR imagery.'],
      },
    })),
    getMlTrainingHistory: vi.fn(() => Promise.resolve({
      available: true, provenance: 'ML_PROTOTYPE_SYNTHETIC_TRAINED', model_component: 'classifier',
      source_file: 'classifier_training_history.json', metric_name: 'accuracy',
      epochs: [{ epoch: 1, train_loss: 0.85, val_loss: 0.79, train_metric: null, val_metric: 0.61 }],
      best_epoch: 1, total_epochs: 1, reason: null,
    })),
    getMlMetrics: vi.fn(() => Promise.resolve({
      metrics_available: false, provenance: 'ML_PROTOTYPE_SYNTHETIC_TRAINED',
      reason: 'No evaluation report found.', precision: null, recall: null, f1: null, iou: null,
      dice: null, pixel_accuracy: null, evaluation_dataset: null, evaluation_split: null,
      evaluation_date: null, num_evaluated_samples: null, per_category: null, caveats: null,
    })),
    getMlDatasetStatus: vi.fn(() => Promise.resolve({
      status: 'missing', dataset_root: '/data/zenodo_sar', dataset_root_configured: true,
      reason: 'DATASET_ROOT is configured but that directory does not exist.',
      parts: {
        part1: { doi: '10.5281/zenodo.8346860', detected: false, validated_pairs: 0, category_counts: { Oil: 0, 'No-Oil': 0, Lookalike: 0 } },
        part2: { doi: '10.5281/zenodo.8253899', detected: false, validated_pairs: 0, category_counts: { Oil: 0, 'No-Oil': 0, Lookalike: 0 } },
        part3: { doi: '10.5281/zenodo.13761290', detected: false, validated_pairs: 0, category_counts: { Oil: 0, 'No-Oil': 0, Lookalike: 0 } },
      },
      category_counts: { Oil: 0, 'No-Oil': 0, Lookalike: 0 },
      total_validated_pairs: 0, issue_count: 0, issues: [], issues_truncated: false,
      dataset_citation: 'Trujillo-Acatitla et al. (2024).',
      note: 'This reflects whether the dataset is present on disk - it says nothing about training.',
    })),
  },
}))

import App from '../App'
import { ThemeProvider } from '../lib/theme.jsx'

// App itself doesn't own a ThemeProvider (that's mounted once in main.jsx,
// alongside <App/>, so it isn't torn down on route/tab changes). Header's
// ThemeToggle calls useTheme() unconditionally, so every full-App render in
// this file needs the same wrapper main.jsx provides, or React throws
// "useTheme must be used within a ThemeProvider" before anything renders.
function renderApp(ui) {
  return render(<ThemeProvider>{ui}</ThemeProvider>)
}

describe('End-to-end UI smoke test (App)', () => {
  // App persists the active investigation id to sessionStorage (see
  // App.jsx's restore-on-refresh effect) so a real page reload doesn't
  // lose the investigation. jsdom's sessionStorage is shared across every
  // `it()` in this file, so it must be cleared here - otherwise a later
  // test's fresh `render(<App />)` would silently auto-restore whatever
  // investigation id an earlier test left behind instead of starting from
  // the real empty state, which would make these tests order-dependent.
  beforeEach(() => {
    vi.clearAllMocks()
    window.sessionStorage.clear()
  })

  it('renders the empty state before any investigation has run', () => {
    renderApp(<App />)
    // both the always-present header button and the empty-state's own CTA
    // button are expected here — that's intentional duplication, not a bug
    expect(screen.getAllByText(/RUN ANALYSIS/i).length).toBe(2)
    expect(screen.getByText(/No active investigation/i)).toBeInTheDocument()
  })

  it('restores the investigation from a stored id after a page reload, without re-running the pipeline', async () => {
    // Simulate a real F5: sessionStorage already has an id from before
    // this render (App.jsx's mount effect is what's under test), and the
    // mocked api module still has the fixture id available via
    // getInvestigation - runInvestigation must NOT be called again.
    window.sessionStorage.setItem('mpi_active_investigation_id', investigationFixture.id)
    const apiModule = (await import('../api')).default

    renderApp(<App />)

    await waitFor(() => {
      expect(screen.getByText(/Spill detected/i)).toBeInTheDocument()
    })
    expect(apiModule.getInvestigation).toHaveBeenCalledWith(investigationFixture.id)
    expect(apiModule.runInvestigation).not.toHaveBeenCalled()
  })

  it('silently falls back to the empty state when the stored investigation id no longer exists on the backend', async () => {
    window.sessionStorage.setItem('mpi_active_investigation_id', 'stale-id-from-a-restarted-backend')
    const apiModule = (await import('../api')).default
    apiModule.getInvestigation.mockRejectedValueOnce(new Error('404'))

    renderApp(<App />)

    await waitFor(() => {
      expect(screen.getByText(/No active investigation/i)).toBeInTheDocument()
    })
    // A gone/expired local pointer is not a user-facing error - no banner.
    expect(screen.queryByText(/failed to run/i)).not.toBeInTheDocument()
    expect(window.sessionStorage.getItem('mpi_active_investigation_id')).toBeNull()
  })

  it('running the demo investigation populates the dashboard with real data', async () => {
    renderApp(<App />)
    const runButtons = screen.getAllByText(/RUN ANALYSIS/i)
    fireEvent.click(runButtons[0])

    // progress checklist should appear while "running"
    expect(await screen.findByText(/Running investigation pipeline/i)).toBeInTheDocument()

    // wait for the staged progress animation to finish and the dashboard to mount
    await waitFor(() => {
      // header brand + dashboard summary heading both legitimately contain
      // this phrase (case-insensitively) — assert at least one is present
      expect(screen.getAllByText(/marine pollution intelligence/i).length).toBeGreaterThan(0)
      expect(screen.getByText(/Spill detected/i)).toBeInTheDocument()
    }, { timeout: 8000 })

    // KPI values should reflect the real fixture data, not placeholders.
    // (Our jsdom Leaflet stub renders Popup children unconditionally,
    // unlike real Leaflet which hides them until clicked, so the same
    // number can legitimately appear twice — assert at least once.)
    const geo = investigationFixture.satellite.geometry
    expect(screen.getAllByText(new RegExp(`${geo.area_km2}`)).length).toBeGreaterThan(0)

    // top candidate vessel name should appear somewhere on the dashboard
    const topVessel = investigationFixture.candidates[0].vessel_name
    expect(screen.getAllByText(new RegExp(topVessel)).length).toBeGreaterThan(0)
  }, 10000)

  it('navigating tabs shows satellite, AIS, ranking, timeline and report content', async () => {
    renderApp(<App />)
    fireEvent.click(screen.getAllByText(/RUN ANALYSIS/i)[0])
    await waitFor(() => expect(screen.getByText(/Spill detected/i)).toBeInTheDocument(), { timeout: 8000 })

    // Satellite tab
    fireEvent.click(screen.getByText('Satellite'))
    expect(await screen.findByText(/Satellite detection & segmentation/i)).toBeInTheDocument()
    expect(screen.getByText(/Slick characterisation/i)).toBeInTheDocument()

    // AIS tab
    fireEvent.click(screen.getByText('AIS'))
    expect(await screen.findByText(/AIS traffic & candidate filtering/i)).toBeInTheDocument()
    // the slider control and the map's radius-circle toggle both mention
    // "search radius" (disambiguated wording) — assert the filter slider specifically
    expect(screen.getByText((_, el) => el?.tagName === 'LABEL' && /search radius\s+\d/i.test(el.textContent))).toBeInTheDocument()

    // Ranking tab
    fireEvent.click(screen.getByText('Ranking'))
    expect(await screen.findByText(/Candidate vessel ranking/i)).toBeInTheDocument()
    // every candidate mmsi from the fixture should render in the table
    for (const c of investigationFixture.candidates) {
      expect(screen.getByText(c.mmsi)).toBeInTheDocument()
    }

    // Timeline tab
    fireEvent.click(screen.getByText('Timeline'))
    expect(await screen.findByText(/Drift reconstruction/i)).toBeInTheDocument()

    // Report tab
    fireEvent.click(screen.getByText('Report'))
    expect(await screen.findByText(/MARINE POLLUTION INTELLIGENCE INVESTIGATION REPORT/i)).toBeInTheDocument()
  }, 15000)

  it('the AIS filter controls call the backend re-analysis endpoint, not a hardcoded value', async () => {
    const api = (await import('../api')).default
    renderApp(<App />)
    fireEvent.click(screen.getAllByText(/RUN ANALYSIS/i)[0])
    await waitFor(() => expect(screen.getByText(/Spill detected/i)).toBeInTheDocument(), { timeout: 8000 })

    fireEvent.click(screen.getByText('AIS'))
    await screen.findByText(/AIS traffic & candidate filtering/i)

    const applyBtn = screen.getByText(/Apply filters/i)
    fireEvent.click(applyBtn)

    await waitFor(() => expect(api.reanalyzeAis).toHaveBeenCalledTimes(1))
  }, 10000)

  it('clicking a ranked vessel opens the evidence-breakdown drawer with real scoring data', async () => {
    renderApp(<App />)
    fireEvent.click(screen.getAllByText(/RUN ANALYSIS/i)[0])
    await waitFor(() => expect(screen.getByText(/Spill detected/i)).toBeInTheDocument(), { timeout: 8000 })

    fireEvent.click(screen.getByText('Ranking'))
    await screen.findByText(/Candidate vessel ranking/i)

    const top = investigationFixture.candidates[0]
    fireEvent.click(screen.getByText(top.vessel_name))

    expect(await screen.findByText(/attribution score/i)).toBeInTheDocument()
    expect(screen.getByText(/Why this vessel ranked/i)).toBeInTheDocument()
    // spot-check one real evidence line and one real score-breakdown component from the fixture
    expect(screen.getByText(top.evidence_summary[0])).toBeInTheDocument()
    expect(screen.getByText(/spatial proximity/i)).toBeInTheDocument()
  }, 10000)

  it('evidence drawer exposes distance-from-detection, data completeness and confidence level (SIH requirement D/E)', async () => {
    renderApp(<App />)
    fireEvent.click(screen.getAllByText(/RUN ANALYSIS/i)[0])
    await waitFor(() => expect(screen.getByText(/Spill detected/i)).toBeInTheDocument(), { timeout: 8000 })

    fireEvent.click(screen.getByText('Ranking'))
    await screen.findByText(/Candidate vessel ranking/i)

    const top = investigationFixture.candidates[0]
    fireEvent.click(screen.getByText(top.vessel_name))
    await screen.findByText(/attribution score/i)

    // the two new weighted factors must render as their own breakdown rows
    expect(screen.getByText(/speed consistency/i)).toBeInTheDocument()
    // "data completeness" appears twice by design: once as a breakdown row,
    // once as a trajectory-summary table row - assert both are present.
    expect(screen.getAllByText(/data completeness/i).length).toBeGreaterThanOrEqual(2)
    // distance-from-origin and distance-from-detection are distinct, both shown
    expect(screen.getByText(/Distance from estimated origin/i)).toBeInTheDocument()
    expect(screen.getByText(/Distance from raw detection location/i)).toBeInTheDocument()
    // data-confidence badge (data-quality confidence, distinct from the score itself)
    expect(screen.getByText(new RegExp(`${top.confidence_level} data confidence`, 'i'))).toBeInTheDocument()
  }, 10000)

  it('never claims proven guilt anywhere in the rendered UI (Section 19 language check)', async () => {
    renderApp(<App />)
    fireEvent.click(screen.getAllByText(/RUN ANALYSIS/i)[0])
    await waitFor(() => expect(screen.getByText(/Spill detected/i)).toBeInTheDocument(), { timeout: 8000 })

    const bodyText = document.body.textContent.toLowerCase()
    expect(bodyText).not.toMatch(/definitely caused/)
    expect(bodyText).not.toMatch(/proven guilty/)
    expect(bodyText).not.toMatch(/legally responsible/)
  }, 10000)

  it('derives the confidence label from detector_provenance, not a hardcoded string', async () => {
    renderApp(<App />)
    fireEvent.click(screen.getAllByText(/RUN ANALYSIS/i)[0])
    await waitFor(() => expect(screen.getByText(/Spill detected/i)).toBeInTheDocument(), { timeout: 8000 })

    // Fixture uses detector_provenance: "CLASSICAL_DARK_SPOT" - the KPI card's
    // sub-label must reflect that (not a generic hardcoded caption that would
    // stay the same regardless of which detector actually ran).
    expect(screen.getAllByText(/Heuristic detection confidence/i).length).toBeGreaterThan(0)

    fireEvent.click(screen.getByText('Satellite'))
    await screen.findByText(/Satellite detection & segmentation/i)
    // The three-way provenance banner should show the classical badge for
    // this fixture, distinct from both the ML and ML-fallback wording.
    expect(screen.getByText(/SAR Detection$/i)).toBeInTheDocument()
  }, 10000)

  it('the ML TRAINING nav item is reachable without running an investigation, unlike the other tabs', async () => {
    renderApp(<App />)
    // Before any investigation has run, every investigation-scoped tab is
    // disabled - but ML Training reports the model's own status, which
    // exists independently of any investigation.
    expect(screen.getByText('Satellite').closest('button')).toBeDisabled()
    expect(screen.getByText('ML TRAINING').closest('button')).not.toBeDisabled()

    fireEvent.click(screen.getByText('ML TRAINING'))
    expect(await screen.findByRole('heading', { name: 'ML Training' })).toBeInTheDocument()
    expect(await screen.findByTestId('ml-status-badge')).toHaveTextContent('Synthetic-trained')
  })
})
