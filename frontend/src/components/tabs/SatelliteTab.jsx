import { useState } from 'react'
import './SatelliteTab.css'
import { getProvenanceInfo, DETECTOR_PROVENANCE } from '../../lib/detectorProvenance'
import { formatSourceLabel, formatMethodLabel } from '../../lib/formatters'
import ClassificationScorecard from '../ClassificationScorecard.jsx'

export default function SatelliteTab({ investigation }) {
  const sat = investigation.satellite
  const geo = sat.geometry
  const age = sat.age_estimate
  const provenance = sat.detector_provenance
  const provenanceInfo = getProvenanceInfo(provenance)
  const isFallback = provenance === DETECTOR_PROVENANCE.ML_FALLBACK
  const isMl = provenance === DETECTOR_PROVENANCE.ML
  const isMlReal = provenance === DETECTOR_PROVENANCE.ML_REAL
  const confidenceInlineLabel = provenanceInfo.confidenceInline
  const confidenceCaveat = provenanceInfo.confidenceCaveat
  const sceneClassification = sat.scene_classification
  const gating = sat.attribution_gating || {}

  const [overlayOpacity, setOverlayOpacity] = useState(0.75)
  const [viewMode, setViewMode] = useState('gallery') // 'gallery' | 'overlay'

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
  const CLASSICAL_SCENE_CLASSIFICATION_COPY = {
    LIKELY_OIL: {
      badge: 'Oil', modifier: 'oil',
      note: 'Classical SAR analysis identified an elongated, low-backscatter candidate with high capillary wave damping (oil slick).',
    },
    NO_OIL: {
      badge: 'No-Oil', modifier: 'no-oil',
      note: 'Classical SAR analysis detected no anomalous dark spots exceeding slick plausibility thresholds; scene matches normal sea surface.',
    },
    LOOKALIKE: {
      badge: 'Lookalike', modifier: 'lookalike',
      note: 'Classical SAR analysis flagged low-wind area or non-oil dark feature (insufficient elongation, rough internal texture, or excessive area). This must not be read as a confirmed spill.',
    },
    UNCERTAIN: {
      badge: 'Uncertain', modifier: 'uncertain',
      note: 'Classical detector features are ambiguous between lookalike and oil film. Treat status as unresolved rather than assuming either outcome.',
    },
  }
  const isClassical = !isMl && !isMlReal && !isFallback
  const copyTable = (isClassical || sceneClassification?.score_type === 'heuristic_score')
    ? CLASSICAL_SCENE_CLASSIFICATION_COPY
    : SCENE_CLASSIFICATION_COPY
  const sceneCopy = sceneClassification ? (copyTable[sceneClassification.oil_status] || SCENE_CLASSIFICATION_COPY[sceneClassification.oil_status]) : null

  return (
    <div className="satellite-workspace">
      {/* Header */}
      <div className="section-head">
        <div>
          <span className="eyebrow">Stage 1–4 · SAR Geospatial Intelligence</span>
          <h2>Satellite Detection & Segmentation Workspace</h2>
        </div>
        <p>Source: {formatSourceLabel(sat.source)} &middot; Method: {formatMethodLabel(sat.method)}</p>
      </div>

      {/* Fail-closed Attribution Warning Banner (Audit §3) */}
      {gating.gated && (
        <div className="safety-alert-banner safety-alert-banner--amber">
          <div style={{ flexShrink: 0, marginTop: '2px' }}>
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="var(--signal-amber)" strokeWidth="2">
              <path d="M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z"/>
              <line x1="12" y1="9" x2="12" y2="13"/>
              <line x1="12" y1="17" x2="12.01" y2="17"/>
            </svg>
          </div>
          <div>
            <strong style={{ color: 'var(--signal-amber)', fontSize: '13px', display: 'block', marginBottom: '2px' }}>
              ATTRIBUTION SAFETY GATE ACTIVE: {gating.status}
            </strong>
            <span style={{ fontSize: '12px', color: 'var(--text-secondary)', lineHeight: 1.5 }}>
              {gating.reason} (Audit §3: Ungated policy flagged 34% of lookalike/non-oil scenes).
            </span>
          </div>
        </div>
      )}      <div className={`provenance-banner provenance-banner--${isMlReal ? 'ml-real' : isMl ? 'ml' : isFallback ? 'fallback' : 'classical'}`}>
        <span className="provenance-banner__badge">{provenanceInfo.badge}</span>
        {isClassical && (
          <>
            <span className="provenance-banner__type-tag">HEURISTIC SCORE</span>
            <span className="provenance-banner__note">
              Classical SAR morphological and backscatter heuristics produced this candidate evaluation. Values are heuristic plausibility scores (0–100%), not calibrated statistical probabilities.
            </span>
          </>
        )}
        {isFallback && (
          <>
            <span className="provenance-banner__type-tag">HEURISTIC SCORE</span>
            <span className="provenance-banner__note">
              ML detection was requested but fell back to classical SAR heuristics{sat.fallback_reason ? ` (${sat.fallback_reason})` : ''} — this is the classical detector's output, shown in its place (HEURISTIC SCORE). It is not, and must not be read as, an ML result.
            </span>
          </>
        )}
        {isMl && (
          <span className="provenance-banner__note">
            Genuine PyTorch inference produced this result. The model is an early synthetic-trained benchmark model — see the Model Card before treating this as validated.
          </span>
        )}
        {isMlReal && (
          <span className="provenance-banner__note">
            Genuine PyTorch/ONNX inference produced this result, using a checkpoint trained on the
            real Zenodo Sentinel-1 SAR Oil Spill Dataset.
          </span>
        )}
      </div>

      {/* Scene Classification Banner & Scorecard */}
      {Boolean(sceneClassification) && (
        <ClassificationScorecard
          sceneClassification={sceneClassification}
          sceneCopy={sceneCopy}
          detectorProvenance={provenance}
        />
      )}

      {/* Interactive Imagery Viewer & Opacity Controls */}
      <div className="sat-gallery-container" style={{ marginBottom: 'var(--sp-5)' }}>
        <div className="sat-gallery-header">
          <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
            <h3 className="sat-gallery-title">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="var(--signal-cyan)" strokeWidth="2">
                <rect x="3" y="3" width="18" height="18" rx="2" ry="2"/>
                <circle cx="8.5" cy="8.5" r="1.5"/>
                <polyline points="21 15 16 10 5 21"/>
              </svg>
              Dual-Polarization SAR Imagery & Segmentation
            </h3>
            <div className="view-mode-toggle" style={{ display: 'flex', gap: '4px' }}>
              <button
                className={`sat-toggle-btn ${viewMode === 'gallery' ? 'sat-toggle-btn--active' : ''}`}
                onClick={() => setViewMode('gallery')}
              >
                Gallery (3-Step)
              </button>
              <button
                className={`sat-toggle-btn ${viewMode === 'overlay' ? 'sat-toggle-btn--active' : ''}`}
                onClick={() => setViewMode('overlay')}
              >
                Adjustable Opacity Overlay
              </button>
            </div>
          </div>

          {viewMode === 'overlay' && (
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
              <span style={{ fontSize: '11px', fontFamily: 'var(--font-mono)', color: 'var(--text-secondary)' }}>
                MASK OPACITY: {Math.round(overlayOpacity * 100)}%
              </span>
              <input
                type="range"
                min="0"
                max="1"
                step="0.05"
                value={overlayOpacity}
                onChange={(e) => setOverlayOpacity(parseFloat(e.target.value))}
                style={{ accentColor: 'var(--signal-cyan)', cursor: 'pointer', width: '120px' }}
              />
            </div>
          )}
        </div>

        {viewMode === 'gallery' ? (
          <div className="sat-image-row" style={{ margin: 0 }}>
            <div className="sat-image-card">
              <img src={sat.original_image} alt="Original SAR scene" />
              <div className="sat-image-card__label"><span className="step-num">1</span> Original SAR</div>
            </div>
            <div className="sat-image-card">
              <img src={sat.processed_image} alt="Despeckled, normalized SAR scene" />
              <div className="sat-image-card__label"><span className="step-num">2</span> Despeckled & Normalized</div>
            </div>
            <div className="sat-image-card">
              <img src={sat.detection_overlay} alt="Detected slick overlay" />
              <div className="sat-image-card__label"><span className="step-num">3</span> Oil Slick Overlay</div>
            </div>
          </div>
        ) : (
          <div style={{ position: 'relative', width: '100%', maxHeight: '520px', overflow: 'hidden', borderRadius: '4px', background: '#000' }}>
            <img
              src={sat.processed_image}
              alt="Base SAR scene"
              style={{ width: '100%', height: 'auto', display: 'block' }}
            />
            <img
              src={sat.detection_overlay}
              alt="Overlay mask"
              style={{
                position: 'absolute',
                top: 0,
                left: 0,
                width: '100%',
                height: '100%',
                opacity: overlayOpacity,
                mixBlendMode: 'screen',
                pointerEvents: 'none',
              }}
            />
          </div>
        )}
      </div>

      {/* Diagnostics and Characterisation */}
      <div className="two-col">
        {/* Slick Characterisation Panel */}
        <div className="panel">
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 'var(--sp-2)' }}>
            <h3 style={{ margin: 0, fontFamily: 'var(--font-display)', fontSize: 15 }}>
              Slick Characterisation & Multi-Component Diagnostics
            </h3>
            {geo?.slick_quartile && (
              <span className={`bento-badge ${geo.slick_quartile === 'q4' ? 'bento-badge--coral' : 'bento-badge--amber'}`}>
                {geo.slick_quartile.toUpperCase()} QUARTILE
              </span>
            )}
          </div>

          {geo ? (
            <>
              {geo.slick_quartile === 'q4' && (
                <div style={{
                  padding: '10px 12px',
                  background: 'rgba(255, 107, 92, 0.08)',
                  borderLeft: '3px solid var(--signal-coral)',
                  marginBottom: '14px',
                  borderRadius: '4px',
                }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '4px' }}>
                    <strong style={{ color: 'var(--signal-coral)', fontSize: '12px' }}>
                      Large-Slick Q4 Diagnostic
                    </strong>
                    {sat.detection_notes?.some(n => n.includes('Multiscale')) ? (
                      <span className="bento-badge bento-badge--cyan" style={{ fontSize: '10px', padding: '2px 6px' }}>
                        EXPERIMENTAL CANDIDATE: MULTISCALE (size_gated_closure)
                      </span>
                    ) : (
                      <span className="bento-badge bento-badge--green" style={{ fontSize: '10px', padding: '2px 6px' }}>
                        PRODUCTION DEFAULT: SINGLE-SCALE BASELINE
                      </span>
                    )}
                  </div>
                  <span style={{ fontSize: '11.5px', color: 'var(--text-secondary)', lineHeight: 1.5, display: 'block' }}>
                    {sat.detection_notes?.some(n => n.includes('Multiscale'))
                      ? "Slick spans >11% of image frame (Q4 category). Experimental candidate size_gated_closure active: coarse-scale infill supplements receptive field for components >= 100 px. Not a production guarantee."
                      : "Slick spans >11% of image frame (Q4 category). Running production single-scale segmenter (ML_MULTISCALE_SEGMENTATION=false). Multiscale recovery remains an opt-in experimental candidate pending independent validation."}
                  </span>
                </div>
              )}

              <table className="geometry-table">
                <tbody>
                  <tr><td>Detection confidence <span className="caveat-inline">{confidenceInlineLabel}</span></td><td>{(sat.confidence * 100).toFixed(1)}%</td></tr>
                  <tr><td>Slick Area</td><td>{geo.area_km2} km² ({geo.area_px} pixels)</td></tr>
                  <tr><td>Size Quartile</td><td>{geo.slick_quartile ? geo.slick_quartile.toUpperCase() : 'CALCULATED'}</td></tr>
                  <tr><td>Perimeter</td><td>{geo.perimeter_km} km</td></tr>
                  <tr><td>Length / Width</td><td>{geo.length_km} km / {geo.width_km} km</td></tr>
                  <tr><td>Aspect ratio</td><td>{geo.aspect_ratio} : 1</td></tr>
                  <tr><td>Orientation</td><td>{geo.orientation_deg}°</td></tr>
                  <tr><td>Connected Components</td><td>{geo.component_count ?? 1} parts</td></tr>
                  <tr><td>Fragmentation Index</td><td>{geo.fragmentation_index != null ? `${(geo.fragmentation_index * 100).toFixed(1)}%` : '0%'}</td></tr>
                  <tr><td>Centroid (Area-Weighted)</td><td>{geo.centroid_lat}°, {geo.centroid_lon}°</td></tr>
                </tbody>
              </table>
            </>
          ) : <p>No slick detected in this scene.</p>}

          <div className="confidence-bar-track" style={{ marginTop: '16px' }}>
            <div className="confidence-bar-fill" style={{ width: `${sat.confidence * 100}%` }} />
          </div>
          <p className="caveat-note" style={{ marginTop: '12px' }}>{confidenceCaveat}</p>
        </div>

        {/* Acquisition Metadata & Estimated Spill Age */}
        <div className="panel">
          <h3 style={{ marginTop: 0, fontFamily: 'var(--font-display)', fontSize: 15 }}>Estimated Spill Age</h3>
          {!age || age.confidence === 'unavailable' ? (
            <p style={{ color: 'var(--text-secondary)', fontSize: 13.5 }}>
              {age?.caveat || (geo
                ? 'Age estimate unavailable: metocean wind data could not be retrieved. Spreading-rate heuristics require verified 10m wind speed vectors.'
                : 'Age estimate unavailable: no slick was detected in this scene.')}
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

          <h3 style={{ fontFamily: 'var(--font-display)', fontSize: 15, marginTop: 'var(--sp-5)' }}>
            Sensor & Geolocation Metadata
          </h3>
          <table className="geometry-table" style={{ fontSize: '12px' }}>
            <tbody>
              <tr><td>Sensor Source</td><td>{formatSourceLabel(sat.source)}</td></tr>
              <tr><td>Geolocation Tag</td><td>{sat.geolocation_source} {sat.geolocation_is_assumed ? '(Assumed Anchor)' : '(Embedded GeoTIFF Tags)'}</td></tr>
              <tr><td>Detector Version</td><td>{sat.detector_version}</td></tr>
              <tr><td>Attribution Safety Status</td><td style={{ color: gating.gated ? 'var(--signal-amber)' : 'var(--signal-green)' }}>{gating.status || 'UNCHECKED'}</td></tr>
            </tbody>
          </table>

          <h3 style={{ fontFamily: 'var(--font-display)', fontSize: 15, marginTop: 'var(--sp-5)' }}>Detection Notes</h3>
          <ul style={{ fontSize: 12.5, color: 'var(--text-secondary)', paddingLeft: 18, lineHeight: 1.6 }}>
            {sat.detection_notes.map((n, i) => <li key={i}>{n}</li>)}
          </ul>
        </div>
      </div>
    </div>
  )
}
