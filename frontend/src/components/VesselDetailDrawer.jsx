import { useEffect } from 'react'
import './VesselDetailDrawer.css'

export default function VesselDetailDrawer({ investigation, mmsi, rank, onClose }) {
  useEffect(() => {
    if (!mmsi) return undefined
    const onKeyDown = e => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [mmsi, onClose])

  if (!mmsi) return null
  const c = investigation.candidates.find(v => v.mmsi === mmsi)
  if (!c) return null

  return (
    <>
      <div className="drawer-backdrop" onClick={onClose} />
      <aside className="drawer" role="dialog" aria-modal="true" aria-label={`Vessel detail: ${c.vessel_name || c.mmsi}`}>
        <button className="drawer__close" onClick={onClose} aria-label="Close">×</button>

        <div className="drawer__header">
          <div className="rank-tag">{rank === 0 ? 'Highest-priority candidate' : `Candidate #${rank + 1}`}</div>
          <h2>{c.vessel_name || 'Unnamed vessel'}</h2>
          <div className="mmsi">MMSI {c.mmsi} {c.imo && `· IMO ${c.imo}`} {c.vessel_type && `· ${c.vessel_type}`}</div>
        </div>

        <div className="drawer__score">
          <span className="big">{c.final_score}</span>
          <span className="of100">/ 100 attribution score</span>
          {c.confidence_level && (
            <span className={`confidence-badge confidence-badge--${c.confidence_level}`}>
              {c.confidence_level} data confidence
            </span>
          )}
        </div>

        <div className="drawer__section-title">Why this vessel ranked #{rank + 1}</div>
        <ul className="evidence-list">
          {c.evidence_summary.map((e, i) => <li key={i}>{e}</li>)}
        </ul>

        <div className="drawer__section-title">Score breakdown</div>
        {c.breakdown.map(b => (
          <div className="breakdown-row" key={b.component}>
            <div className="breakdown-row__top">
              <span>{b.component.replaceAll('_', ' ')}</span>
              <span>{b.points.toFixed(1)} / {b.max_points}</span>
            </div>
            <div className="breakdown-row__track">
              <div className="breakdown-row__fill" style={{ width: `${(b.points / b.max_points) * 100}%` }} />
            </div>
            <div className="breakdown-row__expl">{b.explanation}</div>
          </div>
        ))}

        <div className="drawer__section-title">Trajectory summary</div>
        <table className="geometry-table">
          <tbody>
            <tr><td>Distance from estimated origin</td><td>{c.min_distance_km} km</td></tr>
            <tr><td>Closest approach time</td><td>{new Date(c.closest_approach_time).toISOString().slice(0, 16).replace('T', ' ')}</td></tr>
            <tr><td>Time delta from origin</td><td>{c.time_delta_hours} h</td></tr>
            {c.distance_from_detection_km != null && (
              <tr><td>Distance from raw detection location</td><td>{c.distance_from_detection_km} km</td></tr>
            )}
            {c.time_delta_from_detection_hours != null && (
              <tr><td>Time delta from detection</td><td>{c.time_delta_from_detection_hours} h</td></tr>
            )}
            <tr><td>AIS positions in window</td><td>{c.trajectory_points.length}</td></tr>
            <tr><td>Data completeness</td><td>{c.data_completeness_pct}%</td></tr>
          </tbody>
        </table>

        {c.data_warnings && c.data_warnings.length > 0 && (
          <>
            <div className="drawer__section-title">Missing-data warnings</div>
            <ul className="evidence-list">
              {c.data_warnings.map((w, i) => <li key={i}>{w}</li>)}
            </ul>
          </>
        )}

        <p className="caveat-note" style={{ marginTop: 'var(--sp-5)' }}>
          This score is a transparent spatio-temporal correlation index, not a statistical
          probability of guilt and not a legal finding. Treat as investigative evidence requiring
          further verification. The "data confidence" badge above reflects how much AIS data
          backed this score, not the likelihood of involvement.
        </p>
      </aside>
    </>
  )
}
