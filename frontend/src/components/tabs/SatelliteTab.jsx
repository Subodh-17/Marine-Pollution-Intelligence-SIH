import './SatelliteTab.css'
import { getProvenanceInfo, DETECTOR_PROVENANCE } from '../../lib/detectorProvenance'
import { formatSourceLabel, formatMethodLabel } from '../../lib/formatters'

export default function SatelliteTab({ investigation }) {
  const sat = investigation.satellite
  const geo = sat.geometry
  const age = sat.age_estimate
  // The confidence number's actual mechanism - and what "fallback" even
  // means - differs across all THREE possible detector_provenance values,
  // not just "ML vs not-ML": a fallback result must never be captioned as
  // if the ML model produced it, and must never be presented identically
  // to a genuine classical-by-request result either, since the person
  // asked for ML and didn't get it (see MODEL_CARD.md "ML confidence must
  // be honest").
  const provenance = sat.detector_provenance
  const provenanceInfo = getProvenanceInfo(provenance)
  const isFallback = provenance === DETECTOR_PROVENANCE.ML_FALLBACK
  const isMl = provenance === DETECTOR_PROVENANCE.ML
  const isMlReal = provenance === DETECTOR_PROVENANCE.ML_REAL
  const confidenceInlineLabel = provenanceInfo.confidenceInline
  const confidenceCaveat = provenanceInfo.confidenceCaveat
  // Structured model-provenance metadata (Part E/F/G) - present whenever the
  // ML engine was actually invoked, regardless of whether its result
  // ultimately qualified (see backend/app/investigation/orchestrator.py).
  // Never contains a raw filesystem path - only names/versions/DOIs/dates.
  const modelInfo = sat.model_provenance_info

  // Lookalike safety (Part 6): the classifier's own scene-level verdict -
  // Oil / No-Oil / Lookalike / Uncertain - is a structured field, separate
  // from (and independent of) the segmenter's pixel mask above and the
  // aggregate `sat.confidence` number in the panel below. Before this
  // block existed, a Lookalike or low-confidence classification was only
  // ever visible buried inside the free-text "Detection notes" list further
  // down this page - never as its own clearly-labelled state - so a
  // confident Lookalike call could reach an investigator looking exactly
  // like a confirmed oil detection. Only present when the ML classifier
  // actually ran (see app/satellite/detection.py's scene_classification
  // docstring) - always null for a classical-only or fallback result.
  const sceneClassification = sat.scene_classification
  const SCENE_CLASSIFICATION_COPY = {
    LIKELY_OIL: {
      badge: 'Oil', modifier: 'oil',
      note: 'The classifier\u2019s own scene-level call. This is independent of the segmenter\u2019s pixel mask above - check both before treating this as a confirmed spill.',
    },
    NO_OIL: {
      badge: 'No-Oil', modifier: 'no-oil',
      note: 'The classifier judged this scene as not containing oil. Any mask shown above still comes from a separate, independent segmenter pass.',
    },
    LOOKALIKE: {
      badge: 'Lookalike', modifier: 'lookalike',
      note: 'The classifier flagged this as a known oil-lookalike (e.g. a low-wind slick, algae bloom, or current shear line) rather than genuine oil. This must not be read as a confirmed spill, even if a mask is shown above - the segmenter runs independently of this classification.',
    },
    UNCERTAIN: {
      badge: 'Uncertain', modifier: 'uncertain',
      note: `The classifier\u2019s top prediction fell below the confidence threshold required to call it Oil, No-Oil, or Lookalike (see the probabilities below). Treat this scene\u2019s oil status as unresolved rather than assuming either outcome.`,
    },
  }
  const sceneCopy = sceneClassification ? SCENE_CLASSIFICATION_COPY[sceneClassification.oil_status] : null

  return (
    <div>
      <div className="section-head">
        <div>
          <span className="eyebrow">Stage 1–4 · SAR processing chain</span>
          <h2>Satellite detection & segmentation</h2>
        </div>
        <p>Source: {formatSourceLabel(sat.source)} &middot; Method: {formatMethodLabel(sat.method)}</p>
      </div>

      <div className={`provenance-banner provenance-banner--${isMlReal ? 'ml-real' : isMl ? 'ml' : isFallback ? 'fallback' : 'classical'}`}>
        <span className="provenance-banner__badge">{provenanceInfo.badge}</span>
        {isFallback && (
          <span className="provenance-banner__note">
            ML detection was requested but did not produce a usable result — this is the
            classical detector's output, shown in its place. It is not, and must not be read
            as, an ML result.
          </span>
        )}
        {isMl && (
          <span className="provenance-banner__note">
            Genuine PyTorch inference produced this result. The model is a prototype, trained
            on synthetic data — see the Model Card before treating this as validated.
          </span>
        )}
        {isMlReal && (
          <span className="provenance-banner__note">
            Genuine PyTorch inference produced this result, using a checkpoint trained on the
            real Zenodo Sentinel-1 SAR Oil Spill Dataset. See the model limitations below and
            its evaluation report before relying on this in a new deployment context.
          </span>
        )}
      </div>

      {sceneClassification && sceneCopy && (
        <div className={`scene-classification-banner scene-classification-banner--${sceneCopy.modifier}`}>
          <span className="scene-classification-banner__badge">{sceneCopy.badge}</span>
          <span className="scene-classification-banner__confidence">
            {(sceneClassification.confidence * 100).toFixed(1)}% confidence
          </span>
          <span className="scene-classification-banner__note">{sceneCopy.note}</span>
          <div className="scene-classification-banner__probs">
            {Object.entries(sceneClassification.probabilities).map(([cls, p]) => (
              <span key={cls} className="scene-classification-banner__prob">
                {cls}: {(p * 100).toFixed(1)}%
              </span>
            ))}
          </div>
        </div>
      )}

      {modelInfo && (
        <div className="panel model-provenance-panel" style={{ marginBottom: 'var(--sp-4)' }}>
          <h3 style={{ marginTop: 0, fontFamily: 'var(--font-display)', fontSize: 15 }}>Model provenance</h3>
          <dl style={{ margin: 0 }}>
            <dt>Model</dt>
            <dd>{modelInfo.model_name} {modelInfo.model_version != null && <>(version {String(modelInfo.model_version)})</>}</dd>

            <dt>Trained on real Sentinel-1 SAR data</dt>
            <dd>{modelInfo.is_real_data_trained ? 'Yes' : 'No'}</dd>

            <dt>Training dataset</dt>
            <dd>{modelInfo.dataset_used_for_training}</dd>

            {modelInfo.dataset_dois && (
              <>
                <dt>Dataset DOIs</dt>
                <dd>
                  {Object.entries(modelInfo.dataset_dois).map(([part, doi], i, arr) => (
                    <span key={part}>
                      <a href={`https://doi.org/${doi}`} target="_blank" rel="noreferrer">{doi}</a>
                      {i < arr.length - 1 ? ', ' : ''}
                    </span>
                  ))}
                </dd>
              </>
            )}

            <dt>Input channels / preprocessing version</dt>
            <dd>{modelInfo.input_channels} channel(s) &middot; <code>{modelInfo.preprocessing_version}</code></dd>

            {modelInfo.training_date && (
              <>
                <dt>Training date</dt>
                <dd>{modelInfo.training_date}</dd>
              </>
            )}

            {modelInfo.dataset_split && (modelInfo.dataset_split.train != null || modelInfo.dataset_split.val != null) && (
              <>
                <dt>Dataset split (train / val samples)</dt>
                <dd>{modelInfo.dataset_split.train ?? '—'} / {modelInfo.dataset_split.val ?? '—'}</dd>
              </>
            )}

            <dt>Detection method used</dt>
            <dd>{modelInfo.fallback_status === 'not_used' ? 'ML result used as requested' : 'Classical detector used (ML result not applied)'}</dd>
          </dl>
          {modelInfo.limitations && modelInfo.limitations.length > 0 && (
            <>
              <dt style={{ display: 'block' }}>Model limitations</dt>
              <ul>
                {modelInfo.limitations.map((lim, i) => <li key={i}>{lim}</li>)}
              </ul>
            </>
          )}
        </div>
      )}

      <div className="sat-image-row">
        <div className="sat-image-card">
          <img src={sat.original_image} alt="Original SAR scene" />
          <div className="sat-image-card__label"><span className="step-num">1</span> Original SAR</div>
        </div>
        <div className="sat-image-card">
          <img src={sat.processed_image} alt="Despeckled, normalized SAR scene" />
          <div className="sat-image-card__label"><span className="step-num">2</span> Despeckled / normalized</div>
        </div>
        <div className="sat-image-card">
          <img src={sat.detection_overlay} alt="Detected slick overlay" />
          <div className="sat-image-card__label"><span className="step-num">3</span> Detected slick overlay</div>
        </div>
      </div>

      <div className="two-col">
        <div className="panel">
          <h3 style={{ marginTop: 0, fontFamily: 'var(--font-display)', fontSize: 15 }}>Slick characterisation</h3>
          {geo ? (
            <table className="geometry-table">
              <tbody>
                <tr><td>Detection confidence <span className="caveat-inline">{confidenceInlineLabel}</span></td><td>{(sat.confidence * 100).toFixed(1)}%</td></tr>
                <tr><td>Area</td><td>{geo.area_km2} km²</td></tr>
                <tr><td>Perimeter</td><td>{geo.perimeter_km} km</td></tr>
                <tr><td>Length</td><td>{geo.length_km} km</td></tr>
                <tr><td>Width</td><td>{geo.width_km} km</td></tr>
                <tr><td>Aspect ratio</td><td>{geo.aspect_ratio}</td></tr>
                <tr><td>Orientation</td><td>{geo.orientation_deg}°</td></tr>
                <tr><td>Centroid</td><td>{geo.centroid_lat}°, {geo.centroid_lon}°</td></tr>
              </tbody>
            </table>
          ) : <p>No slick detected in this scene.</p>}
          <div className="confidence-bar-track">
            <div className="confidence-bar-fill" style={{ width: `${sat.confidence * 100}%` }} />
          </div>
          <p className="caveat-note">{confidenceCaveat}</p>
        </div>

        <div className="panel">
          <h3 style={{ marginTop: 0, fontFamily: 'var(--font-display)', fontSize: 15 }}>Estimated spill age</h3>
          {!age || age.confidence === 'unavailable' ? (
            <p style={{ color: 'var(--text-secondary)', fontSize: 13.5 }}>
              {age?.caveat || 'Age estimate unavailable: no slick was detected in this scene.'}
            </p>
          ) : (
            <>
              <div style={{ fontFamily: 'var(--font-display)', fontSize: 28, fontWeight: 600 }}>
                ~{age.hours}<small style={{ fontSize: 14, marginLeft: 4, color: 'var(--text-secondary)' }}>hours</small>
              </div>
              <p style={{ fontSize: 12.5, color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>
                Range: {age.hours_low}–{age.hours_high} h &middot; Confidence: {age.confidence}
              </p>
              <p className="caveat-note">{age.caveat}</p>
            </>
          )}

          <h3 style={{ fontFamily: 'var(--font-display)', fontSize: 15, marginTop: 'var(--sp-5)' }}>Detection notes</h3>
          <ul style={{ fontSize: 12.5, color: 'var(--text-secondary)', paddingLeft: 18, lineHeight: 1.6 }}>
            {sat.detection_notes.map((n, i) => <li key={i}>{n}</li>)}
          </ul>
        </div>
      </div>
    </div>
  )
}
