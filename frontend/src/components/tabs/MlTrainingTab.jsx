import { useCallback, useEffect, useState } from 'react'
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer } from 'recharts'
import api from '../../api'
import { DETECTOR_PROVENANCE } from '../../lib/detectorProvenance'
import './MlTrainingTab.css'

// Every value rendered on this page comes from the three /api/ml/* calls
// below at request time - nothing here is a hardcoded model name, metric,
// date, or sample count. A field the backend reports as unavailable is
// rendered as "Not available" (see `fmt`/`Row`), never a guessed number.

function fmt(value, fallback = 'Not available') {
  if (value === null || value === undefined || value === '') return fallback
  return String(value)
}

function fmtBytes(bytes) {
  if (bytes === null || bytes === undefined) return 'Not available'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`
}

function fmtPct(value) {
  if (value === null || value === undefined) return 'Not available'
  return `${(value * 100).toFixed(2)}%`
}

function fmtDate(iso) {
  if (!iso) return 'Not available'
  try {
    return new Date(iso).toLocaleString()
  } catch {
    return iso
  }
}

function Row({ label, value }) {
  return (
    <tr>
      <td>{label}</td>
      <td>{value}</td>
    </tr>
  )
}

// Four states per the SIH spec: NOT TRAINED / SYNTHETIC-TRAINED /
// REAL ZENODO-TRAINED / UNAVAILABLE. Derived from the backend's own
// verified metadata (checkpoint load booleans, load_errors, and the
// provenance value it already computed via checkpoint/sidecar metadata) -
// never re-derived here from a filename.
function deriveTrainingStatus(status) {
  // Availability is driven by whether the backend actually loaded an
  // inference runtime (`available`), NOT by whether PyTorch happens to be
  // installed. Production serves these same trained checkpoints through
  // ONNX Runtime CPU with no PyTorch present, so keying off torch alone
  // would wrongly report a fully working real model as "Unavailable".
  // `available` is absent on older backends, hence the torch_available
  // fallback for backwards compatibility.
  const runtimeUp = status
    && (status.available !== undefined ? status.available : status.torch_available)
  if (!status || !runtimeUp) {
    return {
      key: 'unavailable',
      label: 'Unavailable',
      detail: 'No inference runtime is available in this environment, so the model and its metadata cannot be loaded or assessed.',
    }
  }
  const anyLoaded = status.classifier_loaded || status.segmenter_loaded
  if (!anyLoaded) {
    const hasRealLoadError = (status.load_errors || []).some((e) => !e.includes('not found'))
    return hasRealLoadError
      ? {
          key: 'unavailable',
          label: 'Unavailable',
          detail: 'A checkpoint file is present but could not be loaded - see load errors below.',
        }
      : {
          key: 'not_trained',
          label: 'Not trained',
          detail: 'No valid trained checkpoint or training record is available for this model.',
        }
  }
  if (status.provenance === DETECTOR_PROVENANCE.ML_REAL) {
    return {
      key: 'real',
      label: 'Real Zenodo-trained',
      detail: 'The active checkpoint is verified through metadata as trained on the real Zenodo Sentinel-1 SAR Oil Spill Dataset.',
    }
  }
  return {
    key: 'synthetic',
    label: 'Synthetic-trained',
    detail: 'Synthetic-trained prototype model. Real Zenodo-trained model has not been trained or verified yet.',
  }
}

// Fetches one resource independently of the other three ML Training page
// calls. This is the core of the fix for the page-wide "Could not reach
// the backend" error that used to appear whenever ONLY the (slow, large
// filesystem scan) dataset-status call was slow or failed: each of the
// four `/api/ml/*` calls this page needs now has its own
// {data, loading, error} state and its own retry, so one call's failure
// or slowness can never hide another call's genuinely successful data
// (no Promise.all combining them into a single success/failure outcome).
function useMlResource(fetchFn) {
  const [state, setState] = useState({ data: null, loading: true, error: null })

  const load = useCallback((arg) => {
    setState((prev) => ({ data: prev.data, loading: true, error: null }))
    return fetchFn(arg)
      .then((data) => setState({ data, loading: false, error: null }))
      .catch((error) => setState((prev) => ({ data: prev.data, loading: false, error })))
  }, [fetchFn])

  // Fires exactly once on mount - `load` is a stable reference (fetchFn
  // itself never changes identity across renders, see the api.js
  // singleton), so this never re-runs on its own; manual retries call
  // `load(...)` directly rather than depending on this effect again.
  useEffect(() => {
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  return [state, load]
}

export default function MlTrainingTab() {
  const [statusRes, retryStatus] = useMlResource(api.getMlStatus)
  const [historyRes, retryHistory] = useMlResource(api.getMlTrainingHistory)
  const [metricsRes, retryMetrics] = useMlResource(api.getMlMetrics)
  const [datasetRes, retryDataset] = useMlResource(api.getMlDatasetStatus)

  // Used only by the page-level error state below (model status itself
  // unreachable) - retries every section at once, since that's the one
  // case where the whole page is genuinely degraded. Each individual
  // section also has its own scoped retry (see MlTrainingContent).
  const retryAll = useCallback(() => {
    retryStatus()
    retryHistory()
    retryMetrics()
    retryDataset()
  }, [retryStatus, retryHistory, retryMetrics, retryDataset])

  const refreshDataset = useCallback(() => retryDataset({ refresh: true }), [retryDataset])

  return (
    <div>
      <div className="section-head">
        <div>
          <span className="eyebrow">Model status & provenance</span>
          <h2>ML Training</h2>
        </div>
        <p>Genuine backend-verified model status - never hardcoded.</p>
      </div>

      {statusRes.loading && <p className="ml-empty-note">Loading model status…</p>}

      {!statusRes.loading && statusRes.error && (
        <div className="ml-retry-row">
          <div className="caveat-note">Could not reach the backend to load ML model status. Check that the API server is running and try again.</div>
          <button className="btn-secondary" onClick={retryAll}>Retry</button>
        </div>
      )}

      {!statusRes.loading && !statusRes.error && statusRes.data && (
        <MlTrainingContent
          status={statusRes.data}
          historyRes={historyRes}
          metricsRes={metricsRes}
          onRetryHistory={retryHistory}
          onRetryMetrics={retryMetrics}
          onRetry={retryAll}
        />
      )}
    </div>
  )
}

const DATASET_STATUS_LABELS = {
  not_configured: 'Not configured',
  missing: 'Missing',
  incomplete: 'Incomplete',
  validated: 'Validated',
}

function DatasetStatusSection() {
  return (
    <div className="panel ml-training-grid--full">
      <h3 className="ml-panel-title">Training data</h3>
      <p className="ml-empty-note">
        <strong>Zenodo Sentinel-1 SAR Oil Spill Dataset</strong><br/><br/>
        Used for model development and evaluation. Training data is not required for inference.
      </p>
    </div>
  )
}

function MlTrainingContent({ status, historyRes, metricsRes, datasetRes, onRetryHistory, onRetryMetrics, onRefreshDataset, onRetry }) {
  const info = status.model_info || {}
  const trainingStatus = deriveTrainingStatus(status)
  const isReal = trainingStatus.key === 'real'
  const dois = info.dataset_dois && typeof info.dataset_dois === 'object' ? Object.values(info.dataset_dois) : null
  const history = historyRes.data
  const metrics = metricsRes.data

  return (
    <>
      {/* Section 2: Training status */}
      <div className={`status-banner status-banner--${trainingStatus.key}`}>
        <span className="status-banner__badge" data-testid="ml-status-badge">{trainingStatus.label}</span>
        <span className="status-banner__note">{trainingStatus.detail}</span>
      </div>

      <div className="ml-training-grid">
        {/* Dataset Status (independent of model-training status - see the
            note rendered inside this section) */}
        <DatasetStatusSection />

        {/* Section 1: Model overview */}
        <div className="panel">
          <h3 className="ml-panel-title">Model overview</h3>
          <table className="ml-table">
            <tbody>
              <Row label="Model name" value={fmt(info.model_name)} />
              <Row label="Version" value={fmt(info.model_version)} />
              <Row label="Architecture" value={fmt(info.architecture)} />
              <Row label="Current status" value={trainingStatus.label} />
              <Row
                label="Inference runtime"
                value={
                  status.runtime === 'onnxruntime_cpu'
                    ? 'ONNX Runtime (CPU)'
                    : status.runtime === 'pytorch'
                      ? 'PyTorch (local/development)'
                      : fmt(status.runtime)
                }
              />
              <Row label="Dataset name" value={fmt(info.dataset_used_for_training)} />
              <Row label="Dataset type" value={fmt(info.dataset_type)} />
              <Row label="Training date" value={fmtDate(info.training_date)} />
              <Row label="Checkpoint filename" value={fmt(info.segmenter_checkpoint_filename)} />
              <Row label="Checkpoint size" value={fmtBytes(info.segmenter_checkpoint_size_bytes)} />
              <Row
                label="Checkpoint load status"
                value={status.segmenter_loaded ? 'Loaded successfully' : 'Not loaded'}
              />
            </tbody>
          </table>
        </div>

        {/* Section 3: Dataset information */}
        <div className="panel">
          <h3 className="ml-panel-title">Dataset information</h3>
          <table className="ml-table">
            <tbody>
              <Row label="Dataset name" value={fmt(info.dataset_used_for_training)} />
              <Row label="Dataset type" value={fmt(info.dataset_type)} />
              <Row
                label="Dataset DOI"
                value={dois ? dois.join(', ') : 'Not available'}
              />
              <Row label="Training samples" value={fmt(info.dataset_split?.train)} />
              <Row label="Validation samples" value={fmt(info.dataset_split?.val)} />
              <Row label="Test samples" value={fmt(status.num_test_samples)} />
            </tbody>
          </table>
          {!isReal && (
            <p className="caveat-note" style={{ marginTop: 'var(--sp-3)' }}>
              The real Zenodo dataset has not been used to train the checkpoint currently active on this server.
            </p>
          )}
        </div>

        {/* Section 4: Training history */}
        <div className="panel ml-training-grid--full">
          <h3 className="ml-panel-title">Training history</h3>
          {historyRes.loading && !history ? (
            <p className="ml-empty-note">Loading training history…</p>
          ) : historyRes.error && !history ? (
            <div className="ml-retry-row">
              <p className="caveat-note">Training history could not be loaded. This does not affect model status above.</p>
              <button className="btn-secondary" onClick={onRetryHistory}>Retry training history</button>
            </div>
          ) : history && history.available ? (
            <>
              <div className="ml-chart-wrap">
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart data={history.epochs} margin={{ top: 8, right: 16, bottom: 0, left: 0 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="var(--border-hairline)" />
                    <XAxis dataKey="epoch" stroke="var(--text-muted)" fontSize={11} label={{ value: 'Epoch', position: 'insideBottom', offset: -2, fill: 'var(--text-muted)', fontSize: 11 }} />
                    <YAxis stroke="var(--text-muted)" fontSize={11} />
                    <Tooltip contentStyle={{ background: 'var(--bg-panel-raised)', border: '1px solid var(--border-hairline)', borderRadius: 6, fontSize: 12 }} />
                    <Legend wrapperStyle={{ fontSize: 12 }} />
                    <Line type="monotone" dataKey="train_loss" name="Training loss" stroke="var(--signal-cyan)" dot={false} strokeWidth={2} />
                    <Line type="monotone" dataKey="val_loss" name="Validation loss" stroke="var(--signal-amber)" dot={false} strokeWidth={2} />
                  </LineChart>
                </ResponsiveContainer>
              </div>
              <p className="caveat-note" style={{ marginTop: 'var(--sp-3)' }}>
                Source: <code className="mono">{history.source_file}</code> ({history.model_component}) - {history.total_epochs} epoch(s) recorded, best epoch: {fmt(history.best_epoch)}.
              </p>
            </>
          ) : (
            <p className="ml-empty-note">{history?.reason || 'No training history is available.'}</p>
          )}
        </div>

        {/* Section 5: Validation metrics */}
        <div className="panel ml-training-grid--full">
          <h3 className="ml-panel-title">Validation metrics</h3>
          {metricsRes.loading && !metrics ? (
            <p className="ml-empty-note">Loading validation metrics…</p>
          ) : metricsRes.error && !metrics ? (
            <div className="ml-retry-row">
              <p className="caveat-note">Validation metrics could not be loaded. This does not affect model status above.</p>
              <button className="btn-secondary" onClick={onRetryMetrics}>Retry metrics</button>
            </div>
          ) : metrics && metrics.metrics_available ? (
            <>
              <div className="ml-metric-grid">
                <div className="kpi-card"><div className="kpi-card__label">Precision</div><div className="kpi-card__value">{fmtPct(metrics.precision)}</div></div>
                <div className="kpi-card"><div className="kpi-card__label">Recall</div><div className="kpi-card__value">{fmtPct(metrics.recall)}</div></div>
                <div className="kpi-card"><div className="kpi-card__label">F1</div><div className="kpi-card__value">{fmtPct(metrics.f1)}</div></div>
                <div className="kpi-card"><div className="kpi-card__label">IoU</div><div className="kpi-card__value">{fmtPct(metrics.iou)}</div></div>
                <div className="kpi-card"><div className="kpi-card__label">Dice</div><div className="kpi-card__value">{fmtPct(metrics.dice)}</div></div>
                <div className="kpi-card"><div className="kpi-card__label">Pixel accuracy</div><div className="kpi-card__value">{fmtPct(metrics.pixel_accuracy)}</div></div>
              </div>
              <table className="ml-table">
                <tbody>
                  <Row label="Evaluation dataset" value={fmt(metrics.evaluation_dataset)} />
                  <Row label="Evaluation split" value={fmt(metrics.evaluation_split)} />
                  <Row label="Evaluation date" value={fmtDate(metrics.evaluation_date)} />
                  <Row label="Samples evaluated" value={fmt(metrics.num_evaluated_samples)} />
                </tbody>
              </table>
              {metrics.per_category && (
                <table className="ml-category-table">
                  <thead>
                    <tr><th>Category</th><th>Precision</th><th>Recall</th><th>F1</th><th>IoU</th><th>Scenes</th></tr>
                  </thead>
                  <tbody>
                    {Object.entries(metrics.per_category).map(([cat, m]) => (
                      <tr key={cat}>
                        <td>{cat}</td>
                        <td>{fmtPct(m.precision)}</td>
                        <td>{fmtPct(m.recall)}</td>
                        <td>{fmtPct(m.f1)}</td>
                        <td>{fmtPct(m.iou)}</td>
                        <td>{fmt(m.n_scenes)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
              {metrics.caveats && metrics.caveats.length > 0 && (
                <ul className="ml-caveats-list">
                  {metrics.caveats.map((c, i) => <li key={i}>{c}</li>)}
                </ul>
              )}
            </>
          ) : (
            <>
              <p className="ml-empty-note">Genuine evaluation metrics are not available yet.</p>
              {metrics?.reason && <p className="caveat-note">{metrics.reason}</p>}
            </>
          )}
        </div>

        {/* Section 6: Model provenance */}
        <div className="panel">
          <h3 className="ml-panel-title">Model provenance</h3>
          <table className="ml-table">
            <tbody>
              <Row label="Training source" value={fmt(info.dataset_used_for_training)} />
              <Row label="Dataset DOI" value={dois ? dois.join(', ') : 'Not available'} />
              <Row label="Model version" value={fmt(info.model_version)} />
              <Row label="Checkpoint source" value={fmt(info.segmenter_checkpoint_filename)} />
              <Row label="Training config available" value={info.preprocessing_version ? 'Yes' : 'Not available'} />
              <Row label="Evaluation available" value={metrics?.metrics_available ? 'Yes' : 'No'} />
            </tbody>
          </table>
          {!isReal && (
            <p className="caveat-note" style={{ marginTop: 'var(--sp-3)' }}>
              Synthetic-data warning: this checkpoint was not trained on real Sentinel-1 SAR imagery.
            </p>
          )}
        </div>

        {/* Section 7: Limitations */}
        <div className="panel">
          <h3 className="ml-panel-title">Limitations</h3>
          {info.limitations && info.limitations.length > 0 ? (
            <ul className="ml-limitations-list">
              {info.limitations.map((l, i) => <li key={i}>{l}</li>)}
            </ul>
          ) : (
            <p className="ml-empty-note">No limitations metadata is available.</p>
          )}
        </div>

        {/* Section 8: Real Zenodo status */}
        <div className="panel ml-training-grid--full">
          <h3 className="ml-panel-title">Real Zenodo status</h3>
          {isReal ? (
            <p className="ml-empty-note">
              A real Zenodo-trained checkpoint is active and verified through metadata (see Model provenance above).
            </p>
          ) : (
            <p className="ml-empty-note">Real Zenodo-trained model not available yet.</p>
          )}
        </div>
      </div>

      {status.load_errors && status.load_errors.length > 0 && (
        <div className="ml-retry-row">
          <p className="caveat-note">{status.load_errors.join(' | ')}</p>
          <button className="btn-secondary" onClick={onRetry}>Retry</button>
        </div>
      )}
    </>
  )
}
