import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import App from '../App'
import { getInitialTabFromLocation } from '../lib/routing'
import { ThemeProvider } from '../lib/theme'

vi.mock('../api', () => ({
  default: {
    getInvestigation: vi.fn().mockRejectedValue(new Error('no stored investigation')),
    getCommandCenter: vi.fn().mockResolvedValue({
      incidents: { total_investigations: 0, confirmed_oil: 0, lookalike_count: 0, no_oil_count: 0, review_required: 0 },
      jobs: { active: 0, completed: 0, failed: 0, recent: [] },
      model_readiness: { available: true, runtime: 'onnxruntime_cpu' },
      data_sources: { satellite: { status: 'OPERATIONAL' }, ais: { status: 'OPERATIONAL' }, drift: { status: 'OPERATIONAL' }, persistence: { status: 'OPERATIONAL' } },
      recent_acquisitions: [],
      review_queue: [],
    }),
    getMlStatus: vi.fn().mockResolvedValue({
      torch_available: true,
      classifier_loaded: true,
      segmenter_loaded: true,
      load_errors: [],
      provenance: 'ML_REAL_ZENODO_TRAINED',
      genuine_evaluation_metrics_available: true,
      num_test_samples: 450,
      model_info: { model_name: 'SpillUNet', model_version: 'real_zenodo_v1' },
    }),
    getMlTrainingHistory: vi.fn().mockResolvedValue({
      available: true,
      provenance: 'ML_REAL_ZENODO_TRAINED',
      model_component: 'classifier',
      source_file: 'classifier_v2_training_history.json',
      metric_name: 'accuracy',
      epochs: [{ epoch: 1, train_loss: 0.5, val_loss: 0.4, train_metric: null, val_metric: 0.85 }],
      best_epoch: 1,
      total_epochs: 1,
      reason: null,
    }),
    getMlMetrics: vi.fn().mockResolvedValue({
      metrics_available: true,
      provenance: 'ML_REAL_ZENODO_TRAINED',
      precision: 0.79,
      recall: 0.65,
      f1: 0.71,
      iou: 0.55,
      dice: 0.71,
      pixel_accuracy: 0.98,
      evaluation_dataset: 'Zenodo SAR',
      evaluation_split: 'test',
    }),
    getMlDatasetStatus: vi.fn().mockResolvedValue({
      status: 'verified',
      dataset_root: '/data/zenodo_sar',
      parts: {},
    }),
  },
}))

describe('Workstream B: SPA Deep Link Routing & Tab Resolution', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    window.sessionStorage.clear()
  })

  it('correctly maps client-side deep links to initial tabs', () => {
    expect(getInitialTabFromLocation('/')).toBe('dashboard')
    expect(getInitialTabFromLocation('/dashboard')).toBe('dashboard')
    expect(getInitialTabFromLocation('/overview')).toBe('dashboard')
    expect(getInitialTabFromLocation('/satellite')).toBe('satellite')
    expect(getInitialTabFromLocation('/ais')).toBe('ais')
    expect(getInitialTabFromLocation('/ranking')).toBe('ranking')
    expect(getInitialTabFromLocation('/timeline')).toBe('timeline')
    expect(getInitialTabFromLocation('/report')).toBe('report')
    expect(getInitialTabFromLocation('/mltraining')).toBe('mltraining')
    expect(getInitialTabFromLocation('/ml-training')).toBe('mltraining')
  })

  it('does not treat API or arbitrary subpaths as valid SPA tabs', () => {
    expect(getInitialTabFromLocation('/api/health')).toBe('dashboard')
    expect(getInitialTabFromLocation('/api/spill/detect')).toBe('dashboard')
    expect(getInitialTabFromLocation('/assets/index.js')).toBe('dashboard')
    expect(getInitialTabFromLocation('/some-unknown-path')).toBe('dashboard')
  })

  it('mounts App on the requested deep-link tab when location is set to /mltraining', async () => {
    window.history.pushState({}, '', '/mltraining')
    render(
      <ThemeProvider>
        <App />
      </ThemeProvider>
    )

    // ML Training navigation item is active and its section header is rendered
    expect(await screen.findByRole('heading', { name: /ML Training/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /ML TRAINING/i })).toHaveClass('active')
  })
})
