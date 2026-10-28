import React from 'react'
import './ClassificationScorecard.css'

/**
 * Unified Scene Classification Banner and Scorecard component.
 * Displays scene-level classification for both Classical and ML detectors with:
 * - Detector Name (Classical Detector vs ML Detector)
 * - Score Type badge (Heuristic score vs Model probability)
 * - Final classification badge (Oil, Lookalike, No-Oil, Uncertain)
 * - Scaled visual progress bar for each category (Oil, Lookalike, No Oil)
 * - Distinct highlight on the winning category
 * - Plain-English contextual note
 * - Per-class score list
 */
export default function ClassificationScorecard({
  sceneClassification,
  sceneCopy,
  detectorProvenance,
}) {
  if (!sceneClassification) return null

  const {
    label,
    confidence = 0,
    oil_status,
    scores,
    probabilities = {},
    score_type,
    score_type_label,
    detector_name,
    reasoning,
  } = sceneClassification

  const isHeuristic = score_type === 'heuristic_score'
  const scoreTypeDisplay = score_type_label || (isHeuristic ? 'Heuristic score' : 'Model probability')
  const detectorDisplay = detector_name || (detectorProvenance?.includes('ML') ? 'ML Detector' : 'Classical Detector')

  // Fallback copy if sceneCopy is not provided
  const copy = sceneCopy || {
    badge: label === 'No Oil' ? 'No-Oil' : (label || 'Uncertain'),
    modifier: oil_status === 'LIKELY_OIL' ? 'oil'
      : oil_status === 'LOOKALIKE' ? 'lookalike'
      : oil_status === 'NO_OIL' ? 'no-oil'
      : 'uncertain',
    note: reasoning || '',
  }

  const safeConfidence = Number.isFinite(confidence) ? confidence : 0

  // 3 Canonical categories: Oil, Lookalike, No Oil (robust against non-finite values)
  const oilScore = Number.isFinite(scores?.Oil) ? scores.Oil : (Number.isFinite(probabilities?.Oil) ? probabilities.Oil : 0)
  const rawLookalike = scores?.Lookalike ?? scores?.['Look-alike'] ?? probabilities?.Lookalike ?? probabilities?.['Look-alike']
  const lookalikeScore = Number.isFinite(rawLookalike) ? rawLookalike : 0
  const rawNoOil = scores?.['No Oil'] ?? scores?.['No-Oil'] ?? probabilities?.['No Oil'] ?? probabilities?.['No-Oil']
  const noOilScore = Number.isFinite(rawNoOil) ? rawNoOil : 0

  const categories = [
    {
      key: 'Oil',
      name: 'Oil',
      score: oilScore,
      barClass: 'classification-bar--oil',
      isWinner: oil_status === 'LIKELY_OIL',
    },
    {
      key: 'Lookalike',
      name: 'Lookalike',
      score: lookalikeScore,
      barClass: 'classification-bar--lookalike',
      isWinner: oil_status === 'LOOKALIKE',
    },
    {
      key: 'No Oil',
      name: 'No Oil',
      score: noOilScore,
      barClass: 'classification-bar--no-oil',
      isWinner: oil_status === 'NO_OIL',
    },
  ]

  return (
    <div
      className={`scene-classification-banner scene-classification-banner--${copy.modifier} classification-scorecard`}
      role="region"
      aria-label="Scene Classification Scorecard"
    >
      <div className="classification-scorecard__header">
        <div className="classification-scorecard__meta">
          <span className="classification-scorecard__detector">{detectorDisplay}</span>
          <span className="classification-scorecard__type-pill">{scoreTypeDisplay}</span>
        </div>
        <div className="classification-scorecard__status-wrap">
          <span className="scene-classification-banner__badge">
            {copy.badge}
          </span>
          <span className="scene-classification-banner__confidence">
            {(safeConfidence * 100).toFixed(1)}% confidence
          </span>
        </div>
      </div>

      <span className="scene-classification-banner__note">{copy.note}</span>

      {/* Visual scaled bars for Oil, Lookalike, No Oil */}
      <div className="classification-scorecard__bars">
        {categories.map((cat) => {
          const pct = (cat.score * 100).toFixed(1)
          return (
            <div
              key={cat.key}
              className={`classification-scorecard__bar-item ${cat.isWinner ? 'classification-scorecard__bar-item--winner' : ''}`}
            >
              <div className="classification-scorecard__bar-header">
                <div className="classification-scorecard__bar-title">
                  <span className="classification-scorecard__cat-name">{cat.name}:</span>
                  {cat.isWinner && (
                    <span className="classification-scorecard__winner-pill">WINNING CLASS</span>
                  )}
                </div>
                <span className="classification-scorecard__bar-value">
                  {pct}%
                </span>
              </div>
              <div
                className="classification-scorecard__track"
                role="progressbar"
                aria-valuenow={parseFloat(pct)}
                aria-valuemin={0}
                aria-valuemax={100}
                aria-label={`${cat.name} score: ${pct}%`}
              >
                <div
                  className={`classification-scorecard__fill ${cat.barClass}`}
                  style={{ width: `${Math.min(100, Math.max(0, cat.score * 100))}%` }}
                />
              </div>
            </div>
          )
        })}
      </div>

      {/* Per-class probabilities list for full contract and backwards compatibility */}
      <div className="scene-classification-banner__probs">
        {Object.entries(probabilities).map(([cls, p]) => {
          const val = Number.isFinite(p) ? (p * 100).toFixed(1) : '0.0'
          return (
            <span key={cls} className="scene-classification-banner__prob">
              {cls}: {val}%
            </span>
          )
        })}
      </div>

      {reasoning && reasoning !== copy.note && (
        <div className="classification-scorecard__reasoning">
          <span className="classification-scorecard__reasoning-label">Diagnostic Basis: </span>
          {reasoning}
        </div>
      )}
    </div>
  )
}
