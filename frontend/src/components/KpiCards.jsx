import { getProvenanceInfo } from '../lib/detectorProvenance'

function Card({ label, value, unit, sub, accent }) {
  return (
    <div className="kpi-card" style={{ '--kpi-accent': accent }}>
      <div className="kpi-card__label">{label}</div>
      <div className="kpi-card__value">{value}{unit && <small>{unit}</small>}</div>
      {sub && <div className="kpi-card__sub">{sub}</div>}
    </div>
  )
}

export default function KpiCards({ investigation }) {
  const geo = investigation.satellite.geometry
  const age = investigation.satellite.age_estimate
  const origin = investigation.drift.probable_origin
  const top = investigation.candidates[0]
  const provenanceInfo = getProvenanceInfo(investigation.satellite.detector_provenance)

  const ageLabel = age && age.confidence !== 'unavailable'
    ? `~${age.hours}` : 'N/A'
  const ageUnit = age && age.confidence !== 'unavailable' ? 'h' : ''
  const ageSub = age
    ? (age.confidence === 'unavailable' ? 'Unavailable / low confidence' : `Confidence: ${age.confidence}`)
    : (geo ? 'Metocean data unavailable' : 'No slick detected')

  const isDriftUnavailable = investigation.drift?.drift_status === 'UNAVAILABLE' || investigation.drift?.environment_source === 'UNAVAILABLE'
  const originSub = origin
    ? `${origin.lat.toFixed(3)}° · ${investigation.drift.origin_confidence} confidence`
    : (isDriftUnavailable ? 'Drift unavailable (metocean unreachable)' : (geo ? 'Drift unavailable' : 'No slick detected'))

  return (
    <div className="kpi-grid">
      <Card
        label="Spill detected"
        value={`${(investigation.satellite.confidence * 100).toFixed(1)}`}
        unit="% conf."
        sub={provenanceInfo.confidenceSub}
        accent="var(--signal-coral)"
      />
      <Card
        label="Spill area"
        value={geo ? geo.area_km2 : '—'}
        unit="km²"
        sub={geo ? `${geo.length_km} × ${geo.width_km} km` : ''}
        accent="var(--signal-cyan)"
      />
      <Card
        label="Estimated age"
        value={ageLabel}
        unit={ageUnit}
        sub={ageSub}
        accent="var(--signal-amber)"
      />
      <Card
        label="Probable origin"
        value={origin ? `${origin.lat.toFixed(3)}°` : 'N/A'}
        sub={originSub}
        accent="var(--signal-violet)"
      />
      <Card
        label="AIS candidates"
        value={investigation.candidates.length}
        sub={`of ${investigation.ais_summary.total_raw_records} raw pings in AOI`}
        accent="var(--signal-cyan)"
      />
      <Card
        label="Top candidate"
        value={top ? (top.vessel_name || top.mmsi) : '—'}
        sub={top ? `Attribution score ${top.final_score}/100` : 'No candidates'}
        accent="var(--signal-coral)"
      />
    </div>
  )
}
