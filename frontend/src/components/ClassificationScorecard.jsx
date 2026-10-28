import React from 'react'
import './ClassificationScorecard.css'

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

  const copy = sceneCopy || {
    badge: label === 'No Oil' ? 'No-Oil' : (label || 'Uncertain'),
    modifier: oil_status === 'LIKELY_OIL' ? 'oil'
      : oil_status === 'LOOKALIKE' ? 'lookalike'
      : oil_status === 'NO_OIL' ? 'no-oil'
      : 'uncertain',
    note: reasoning || '',
  }

  const safeConfidence = Number.isFinite(confidence) ? confidence : 0

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
      isWinner: oil_status === 'LIKELY_OIL',
    },
    {
      key: 'No Oil',
      name: 'No Oil',
      score: noOilScore,
      isWinner: oil_status === 'NO_OIL',
    },
    {
      key: 'Lookalike',
      name: 'Lookalike',
      score: lookalikeScore,
      isWinner: oil_status === 'LOOKALIKE',
    },
  ]

  return (
    <div
      className={`scene-classification-banner scene-classification-banner--${copy.modifier} classification-scorecard`}
      role="region"
      aria-label="Scene Classification Scorecard"
    >
      <div className="scene-classification-banner__header">
        <span className="scene-classification-banner__badge">
          {copy.badge}
        </span>
        <span className="scene-classification-banner__confidence">
          {(safeConfidence * 100).toFixed(1)}% confidence
        </span>
        <span className="scene-classification-banner__note">
          {copy.note}
        </span>
      </div>

      <div className="scene-classification-banner__probs">
        {categories.map((cat) => {
          const pct = (cat.score * 100).toFixed(1)
          const displayKey = (probabilities && probabilities['No-Oil'] !== undefined && cat.key === 'No Oil') ? 'No-Oil' : cat.name
          return (
            <span
              key={cat.key}
              className={`scene-classification-banner__prob ${cat.isWinner ? 'scene-classification-banner__prob--winner' : ''}`}
              role="progressbar"
              aria-valuenow={parseFloat(pct)}
              aria-valuemin={0}
              aria-valuemax={100}
              aria-label={`${cat.name} score: ${pct}%`}
            >
              {displayKey}: {pct}%
              <span className="sr-only">
                <span>{cat.name}:</span>
                <span>{pct}%</span>
                {cat.isWinner && <span>WINNING CLASS</span>}
              </span>
            </span>
          )
        })}
      </div>

      {reasoning && reasoning !== copy.note && (
        <div className="scene-classification-banner__reasoning">
          <span className="scene-classification-banner__reasoning-label">Diagnostic Basis: </span>
          {reasoning}
        </div>
      )}

      <div className="scene-classification-banner__meta sr-only">
        <span className="classification-scorecard__detector">{detectorDisplay}</span>
        <span className="classification-scorecard__type-pill">{scoreTypeDisplay}</span>
      </div>
    </div>
  )
}
