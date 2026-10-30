import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'

// Every value the component displays must come from these three mocked
// backend calls - no test here should ever be satisfied by a hardcoded
// string baked into the component itself. Deliberately unusual filenames/
// numbers below (never plausible defaults) make that easy to prove: if
// they show up on screen, they can only have come from the mock.
const getMlStatus = vi.fn()
const getMlTrainingHistory = vi.fn()
const getMlMetrics = vi.fn()
const getMlDatasetStatus = vi.fn()

vi.mock('../api', () => ({
  default: {
    getMlStatus: (...a) => getMlStatus(...a),
    getMlTrainingHistory: (...a) => getMlTrainingHistory(...a),
    getMlMetrics: (...a) => getMlMetrics(...a),
    getMlDatasetStatus: (...a) => getMlDatasetStatus(...a),
  },
}))

import MlTrainingTab from '../components/tabs/MlTrainingTab'

// A default, uninteresting dataset-status fixture - tests that aren't
// specifically about the Dataset Status section use this via beforeEach so
// they don't need to know or care about it.
const DEFAULT_DATASET_STATUS = {
  status: 'missing',
  dataset_root: '/QA-FIXTURE/data/zenodo_sar',
  dataset_root_configured: true,
  reason: 'QA-FIXTURE: directory does not exist.',
  parts: {
    part1: { doi: '10.5281/zenodo.8346860', detected: false, validated_pairs: 0, category_counts: { Oil: 0, 'No-Oil': 0, Lookalike: 0 } },
    part2: { doi: '10.5281/zenodo.8253899', detected: false, validated_pairs: 0, category_counts: { Oil: 0, 'No-Oil': 0, Lookalike: 0 } },
    part3: { doi: '10.5281/zenodo.13761290', detected: false, validated_pairs: 0, category_counts: { Oil: 0, 'No-Oil': 0, Lookalike: 0 } },
  },
  category_counts: { Oil: 0, 'No-Oil': 0, Lookalike: 0 },
  total_validated_pairs: 0,
  issue_count: 0,
  issues: [],
  issues_truncated: false,
  dataset_citation: 'QA-FIXTURE citation.',
  note: 'QA-FIXTURE-NOTE: this says nothing about whether a model has been trained.',
}

const SYNTHETIC_STATUS = {
  torch_available: true,
  classifier_loaded: true,
  segmenter_loaded: true,
  load_errors: [],
  provenance: 'ML_PROTOTYPE_SYNTHETIC_TRAINED',
  genuine_evaluation_metrics_available: false,
  num_test_samples: null,
  model_info: {
    model_name: 'SpillUNet-QA-FIXTURE-9182',
    model_version: 'prototype_v1',
    architecture: 'SpillUNet',
    is_real_data_trained: false,
    dataset_used_for_training: 'Synthetically generated training data (not real Sentinel-1 SAR imagery)',
    dataset_type: 'synthetic_generated',
    dataset_dois: null,
    training_date: null,
    dataset_split: null,
    segmenter_checkpoint_filename: 'spill_unet_QA_FIXTURE.pt',
    segmenter_checkpoint_size_bytes: 499010,
    limitations: ['QA-FIXTURE-LIMITATION: not trained on real SAR imagery.'],
  },
  status_message: 'test status message',
}

const SYNTHETIC_HISTORY = {
  available: true,
  provenance: 'ML_PROTOTYPE_SYNTHETIC_TRAINED',
  model_component: 'classifier',
  source_file: 'classifier_training_history.json',
  metric_name: 'accuracy',
  epochs: [
    { epoch: 1, train_loss: 0.8465, val_loss: 0.7899, train_metric: null, val_metric: 0.6067 },
    { epoch: 2, train_loss: 0.6964, val_loss: 0.7301, train_metric: null, val_metric: 0.56 },
    { epoch: 3, train_loss: 0.6318, val_loss: 0.5866, train_metric: null, val_metric: 0.6233 },
  ],
  best_epoch: 3,
  total_epochs: 3,
  reason: null,
}

const NO_METRICS = {
  metrics_available: false,
  provenance: 'ML_PROTOTYPE_SYNTHETIC_TRAINED',
  reason: 'QA-FIXTURE-REASON: no evaluation report found.',
  precision: null, recall: null, f1: null, iou: null, dice: null, pixel_accuracy: null,
  evaluation_dataset: null, evaluation_split: null, evaluation_date: null,
  num_evaluated_samples: null, per_category: null, caveats: null,
}

const REAL_STATUS = {
  ...SYNTHETIC_STATUS,
  provenance: 'ML_REAL_ZENODO_TRAINED',
  genuine_evaluation_metrics_available: true,
  num_test_samples: 6,
  model_info: {
    ...SYNTHETIC_STATUS.model_info,
    model_name: 'SpillUNet-QA-REAL-FIXTURE-4471',
    is_real_data_trained: true,
    dataset_used_for_training: 'QA-FIXTURE Real Zenodo Sentinel-1 SAR Oil Spill Dataset',
    dataset_type: 'real_sentinel1_sar_geotiff',
    dataset_dois: { part1: '10.5281/zenodo.QA-FIXTURE' },
    training_date: '2026-01-01T00:00:00',
    dataset_split: { train: 6, val: 6 },
    segmenter_checkpoint_filename: 'spill_unet_real_promoted_QA_FIXTURE.pt',
    preprocessing_version: 'sar_percentile_1_99_minmax_v1',
  },
}

const REAL_HISTORY = {
  available: true,
  provenance: 'ML_REAL_ZENODO_TRAINED',
  model_component: 'segmenter',
  source_file: 'spill_unet_real_training_history.json',
  metric_name: 'iou',
  epochs: [{ epoch: 1, train_loss: 0.89, val_loss: 0.87, train_metric: 0.01, val_metric: 0.01 }],
  best_epoch: 1,
  total_epochs: 1,
  reason: null,
}

const REAL_METRICS = {
  metrics_available: true,
  provenance: 'ML_REAL_ZENODO_TRAINED',
  reason: null,
  precision: 0.0512, recall: 0.91, f1: 0.097, iou: 0.0512, dice: 0.097, pixel_accuracy: 0.99,
  evaluation_dataset: 'QA-FIXTURE Real Zenodo Sentinel-1 SAR Oil Spill Dataset (held-out test split)',
  evaluation_split: 'test',
  evaluation_date: '2026-01-02T00:00:00',
  num_evaluated_samples: 6,
  per_category: { 'QA-Fixture-Category': { precision: 0.5, recall: 0.5, f1: 0.5, iou: 0.5, n_scenes: 2 } },
  caveats: ['QA-FIXTURE-CAVEAT: read this before trusting these numbers.'],
}

const NOT_TRAINED_STATUS = {
  torch_available: true,
  classifier_loaded: false,
  segmenter_loaded: false,
  load_errors: ['classifier checkpoint not found - looked for: [...]', 'segmenter checkpoint not found - looked for: [...]'],
  provenance: 'ML_UNAVAILABLE',
  genuine_evaluation_metrics_available: false,
  num_test_samples: null,
  model_info: null,
  status_message: 'no checkpoints found',
}

describe('MlTrainingTab', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    getMlDatasetStatus.mockResolvedValue(DEFAULT_DATASET_STATUS)
  })

  it('shows a loading state before the backend responds', () => {
    getMlStatus.mockReturnValue(new Promise(() => {}))
    getMlTrainingHistory.mockReturnValue(new Promise(() => {}))
    getMlMetrics.mockReturnValue(new Promise(() => {}))
    render(<MlTrainingTab />)
    expect(screen.getByText(/Loading model status/i)).toBeInTheDocument()
  })

  it('renders genuine backend data with no hardcoded model values, and shows the synthetic-trained warning', async () => {
    getMlStatus.mockResolvedValue(SYNTHETIC_STATUS)
    getMlTrainingHistory.mockResolvedValue(SYNTHETIC_HISTORY)
    getMlMetrics.mockResolvedValue(NO_METRICS)
    render(<MlTrainingTab />)

    expect(await screen.findByTestId('ml-status-badge')).toHaveTextContent('Synthetic-trained')
    // These strings only exist in the mock fixture, never in the component
    // source - their presence proves the page is rendering fetched data.
    expect(screen.getByText('SpillUNet-QA-FIXTURE-9182')).toBeInTheDocument()
    expect(screen.getAllByText('spill_unet_QA_FIXTURE.pt').length).toBeGreaterThan(0)
    expect(screen.getByText(/QA-FIXTURE-LIMITATION/)).toBeInTheDocument()

    // Required exact copy from the spec for the synthetic/no-real-model state.
    expect(screen.getByText(/Real Zenodo-trained model has not been trained or verified yet/i)).toBeInTheDocument()
    expect(screen.getByText('Real Zenodo-trained model not available yet.')).toBeInTheDocument()
  })

  it('shows the missing-metrics empty state instead of fabricated numbers', async () => {
    getMlStatus.mockResolvedValue(SYNTHETIC_STATUS)
    getMlTrainingHistory.mockResolvedValue(SYNTHETIC_HISTORY)
    getMlMetrics.mockResolvedValue(NO_METRICS)
    render(<MlTrainingTab />)

    expect(await screen.findByText(/Genuine evaluation metrics are not available yet/i)).toBeInTheDocument()
    expect(screen.getByText(/QA-FIXTURE-REASON/)).toBeInTheDocument()
    // No placeholder 0% / 100% metric cards should be rendered.
    expect(screen.queryByText('Precision')).not.toBeInTheDocument()
  })

  it('shows the missing-training-history empty state instead of a fake chart', async () => {
    getMlStatus.mockResolvedValue(SYNTHETIC_STATUS)
    getMlTrainingHistory.mockResolvedValue({
      available: false, provenance: 'ML_PROTOTYPE_SYNTHETIC_TRAINED', model_component: 'classifier',
      source_file: null, metric_name: null, epochs: [], best_epoch: null, total_epochs: 0,
      reason: 'QA-FIXTURE-NO-HISTORY-REASON',
    })
    getMlMetrics.mockResolvedValue(NO_METRICS)
    render(<MlTrainingTab />)

    expect(await screen.findByText(/QA-FIXTURE-NO-HISTORY-REASON/)).toBeInTheDocument()
  })

  it('renders the real-Zenodo-trained state with genuine metrics and its own training history', async () => {
    getMlStatus.mockResolvedValue(REAL_STATUS)
    getMlTrainingHistory.mockResolvedValue(REAL_HISTORY)
    getMlMetrics.mockResolvedValue(REAL_METRICS)
    render(<MlTrainingTab />)

    expect(await screen.findByTestId('ml-status-badge')).toHaveTextContent('Real Zenodo-trained')
    expect(screen.getByText('SpillUNet-QA-REAL-FIXTURE-4471')).toBeInTheDocument()
    expect(screen.getAllByText(/QA-FIXTURE Real Zenodo Sentinel-1 SAR Oil Spill Dataset/).length).toBeGreaterThan(0)
    // Genuine metrics rendered as percentages derived from the fixture's numbers.
    expect(screen.getAllByText('5.12%').length).toBeGreaterThan(0)
    expect(screen.getByText(/QA-FIXTURE-CAVEAT/)).toBeInTheDocument()
    expect(screen.getByText(/QA-Fixture-Category/)).toBeInTheDocument()
    // Training history correctly attributed to the segmenter's own real
    // history file, never silently substituted with the classifier's.
    expect(screen.getByText(/spill_unet_real_training_history\.json/)).toBeInTheDocument()
    // The "not available yet" copy must NOT appear once a real model exists.
    expect(screen.queryByText('Real Zenodo-trained model not available yet.')).not.toBeInTheDocument()
  })

  it('renders multi-component history with model switcher and accuracy/loss controls', async () => {
    const MULTI_COMPONENT_HISTORY = {
      available: true,
      provenance: 'ML_REAL_ZENODO_TRAINED',
      model_component: 'classifier',
      active_component: 'classifier',
      source_file: 'classifier_v2_training_history.json',
      metric_name: 'accuracy',
      epochs: [
        { epoch: 1, train_loss: 0.84, val_loss: 0.52, train_metric: 0.64, val_metric: 0.78, train_accuracy: 0.64, val_accuracy: 0.78 },
        { epoch: 32, train_loss: 0.24, val_loss: 0.21, train_metric: 0.91, val_metric: 0.9249, train_accuracy: 0.91, val_accuracy: 0.9249 },
        { epoch: 40, train_loss: 0.22, val_loss: 0.24, train_metric: 0.92, val_metric: 0.9223, train_accuracy: 0.92, val_accuracy: 0.9223 },
      ],
      best_epoch: 32,
      total_epochs: 40,
      reported_test_accuracy: 0.7933,
      components: {
        classifier: {
          available: true,
          model_component: 'classifier',
          model_name: 'SceneClassifierV2',
          source_file: 'classifier_v2_training_history.json',
          metric_name: 'accuracy',
          epochs: [
            { epoch: 1, train_loss: 0.84, val_loss: 0.52, train_metric: 0.64, val_metric: 0.78, train_accuracy: 0.64, val_accuracy: 0.78 },
            { epoch: 32, train_loss: 0.24, val_loss: 0.21, train_metric: 0.91, val_metric: 0.9249, train_accuracy: 0.91, val_accuracy: 0.9249 },
            { epoch: 40, train_loss: 0.22, val_loss: 0.24, train_metric: 0.92, val_metric: 0.9223, train_accuracy: 0.92, val_accuracy: 0.9223 },
          ],
          best_epoch: 32,
          total_epochs: 40,
          reported_test_accuracy: 0.7933,
        },
        segmenter: {
          available: true,
          model_component: 'segmenter',
          model_name: 'SpillUNet',
          source_file: 'spill_unet_real_training_history.json',
          metric_name: 'iou',
          epochs: [
            { epoch: 1, train_loss: 0.49, val_loss: 0.37, train_metric: 0.35, val_metric: 0.43, train_iou: 0.35, val_iou: 0.43 },
            { epoch: 18, train_loss: 0.32, val_loss: 0.32, train_metric: 0.49, val_metric: 0.6294, train_iou: 0.49, val_iou: 0.6294 },
            { epoch: 20, train_loss: 0.32, val_loss: 0.32, train_metric: 0.51, val_metric: 0.5651, train_iou: 0.51, val_iou: 0.5651 },
          ],
          best_epoch: 18,
          total_epochs: 20,
        },
      },
    }

    getMlStatus.mockResolvedValue(REAL_STATUS)
    getMlTrainingHistory.mockResolvedValue(MULTI_COMPONENT_HISTORY)
    getMlMetrics.mockResolvedValue(REAL_METRICS)
    render(<MlTrainingTab />)

    // Check classifier controls and 40 epochs
    expect(await screen.findByText('SceneClassifierV2 (40 Epochs)')).toBeInTheDocument()
    expect(screen.getByText('SpillUNet (20 Epochs)')).toBeInTheDocument()
    expect(screen.getByText('40 of 40 epochs')).toBeInTheDocument()
    expect(screen.getByText('Epoch 32')).toBeInTheDocument()
    expect(screen.getByText('79.33%')).toBeInTheDocument()

    // Switch to segmenter
    fireEvent.click(screen.getByText('SpillUNet (20 Epochs)'))
    expect(screen.getByText('20 of 20 epochs')).toBeInTheDocument()
    expect(screen.getByText('Epoch 18')).toBeInTheDocument()
    expect(screen.getByText('62.94%')).toBeInTheDocument()

    // Switch view modes
    fireEvent.click(screen.getByRole('button', { name: 'Loss' }))
    expect(screen.getByRole('button', { name: 'Loss' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'IoU' }))
    expect(screen.getByRole('button', { name: 'IoU' })).toBeInTheDocument()
  })

  it('shows "not trained" when no checkpoint is available at all', async () => {
    getMlStatus.mockResolvedValue(NOT_TRAINED_STATUS)
    getMlTrainingHistory.mockResolvedValue({
      available: false, provenance: 'ML_UNAVAILABLE', model_component: 'classifier',
      source_file: null, metric_name: null, epochs: [], best_epoch: null, total_epochs: 0,
      reason: 'no checkpoint',
    })
    getMlMetrics.mockResolvedValue(NO_METRICS)
    render(<MlTrainingTab />)

    expect(await screen.findByTestId('ml-status-badge')).toHaveTextContent('Not trained')
  })

  it('shows a clear error state when the backend is unreachable, with a retry control', async () => {
    getMlStatus.mockRejectedValue(new Error('network error'))
    getMlTrainingHistory.mockRejectedValue(new Error('network error'))
    getMlMetrics.mockRejectedValue(new Error('network error'))
    render(<MlTrainingTab />)

    expect(await screen.findByText(/Could not reach the backend/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /retry/i })).toBeInTheDocument()
  })

  it('retrying after a failure re-fetches and renders successfully', async () => {
    getMlStatus.mockRejectedValueOnce(new Error('network error')).mockResolvedValue(SYNTHETIC_STATUS)
    getMlTrainingHistory.mockRejectedValueOnce(new Error('network error')).mockResolvedValue(SYNTHETIC_HISTORY)
    getMlMetrics.mockRejectedValueOnce(new Error('network error')).mockResolvedValue(NO_METRICS)
    render(<MlTrainingTab />)

    expect(await screen.findByText(/Could not reach the backend/i)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /retry/i }))

    await waitFor(() => expect(screen.getByTestId('ml-status-badge')).toHaveTextContent('Synthetic-trained'))
    expect(getMlStatus).toHaveBeenCalledTimes(2)
  })


  it('reports a real ONNX-served model as trained, not unavailable, when PyTorch is absent', async () => {
    // Production (Render Free) runs ONNX Runtime CPU with no PyTorch
    // installed. Keying availability off torch alone used to render a
    // fully-working real model as "Unavailable".
    getMlStatus.mockResolvedValue({
      ...SYNTHETIC_STATUS,
      torch_available: false,
      onnx_available: true,
      available: true,
      runtime: 'onnxruntime_cpu',
      provenance: 'ML_REAL_ZENODO_TRAINED',
      model_info: { ...SYNTHETIC_STATUS.model_info, is_real_data_trained: true },
    })
    getMlTrainingHistory.mockResolvedValue(SYNTHETIC_HISTORY)
    getMlMetrics.mockResolvedValue(NO_METRICS)
    render(<MlTrainingTab />)

    await waitFor(() =>
      expect(screen.getByTestId('ml-status-badge')).toHaveTextContent('Real Zenodo-trained'))
    expect(screen.queryByText(/No inference runtime is available/i)).not.toBeInTheDocument()
    expect(screen.getByText('ONNX Runtime (CPU)')).toBeInTheDocument()
  })

  it('reports unavailable when neither inference runtime loaded', async () => {
    getMlStatus.mockResolvedValue({
      ...SYNTHETIC_STATUS,
      torch_available: false,
      onnx_available: false,
      available: false,
      runtime: null,
      classifier_loaded: false,
      segmenter_loaded: false,
      model_info: null,
    })
    getMlTrainingHistory.mockResolvedValue(SYNTHETIC_HISTORY)
    getMlMetrics.mockResolvedValue(NO_METRICS)
    render(<MlTrainingTab />)

    await waitFor(() =>
      expect(screen.getByTestId('ml-status-badge')).toHaveTextContent('Unavailable'))
  })

})
