import './TimelineTab.css'

const RANK_COLORS = ['var(--signal-coral)', 'var(--signal-amber)', 'var(--signal-cyan)', 'var(--layer-neutral)', 'var(--layer-neutral)']

export default function TimelineTab({ investigation, onSelectVessel }) {
  const drift = investigation.drift

  if (!drift.probable_origin) {
    const isMetoceanUnavailable = drift.drift_status === 'UNAVAILABLE' || drift.environment_source === 'UNAVAILABLE'
    return (
      <div>
        <div className="section-head">
          <div>
            <span className="eyebrow">Investigation timeline</span>
            <h2>Drift reconstruction & vessel correlation</h2>
          </div>
        </div>
        <div className="panel">
          <p style={{ color: 'var(--text-secondary)', fontSize: 13.5 }}>
            {isMetoceanUnavailable
              ? 'Drift reconstruction unavailable: real metocean data (wind and ocean currents) could not be retrieved. To maintain scientific validity, synthetic trajectories are refused for satellite observations.'
              : 'No slick was detected in this scene, so there is no drift trajectory or probable origin to build a timeline from.'}
          </p>
        </div>
      </div>
    )
  }

  const nodes = [
    ...drift.backward_track.map(p => ({ ...p, kind: 'past' })),
    { ...drift.probable_origin, kind: 'origin', label: 'PROBABLE ORIGIN' },
    { ...drift.observed, kind: 'observed', label: 'SATELLITE DETECTION' },
    ...drift.forward_track.map(p => ({ ...p, kind: 'future' })),
  ]

  // dedupe origin (it's also the first backward point) — keep the explicit origin card, drop the duplicate
  const seen = new Set()
  const cleanNodes = nodes.filter(n => {
    const key = `${n.t_offset_hours}`
    if (n.kind === 'past' && n.t_offset_hours === drift.probable_origin.t_offset_hours) return false
    if (seen.has(key) && n.kind !== 'origin' && n.kind !== 'observed') return false
    seen.add(key)
    return true
  })
  // Bug fix: the array was built as
  // [...backward_track, origin, observed, ...forward_track] and the dedupe
  // above only removes the duplicate backward_track entry - it doesn't
  // reorder anything. Since `probable_origin` shares its t_offset_hours
  // with the *earliest* backward_track point (both are the same -24h
  // moment, just labelled differently), it was left sitting in its
  // original array position (right after the remaining, LATER backward
  // points), so the timeline visually read T-18h, T-12h, T-6h, PROBABLE
  // ORIGIN — with "origin" appearing to happen *after* T-6h even though
  // its own timestamp is chronologically earliest. A left-to-right
  // timeline with a timestamp on every node needs to actually be sorted by
  // that timestamp, not by insertion order.
  cleanNodes.sort((a, b) => a.t_offset_hours - b.t_offset_hours)

  const minH = Math.min(...cleanNodes.map(n => n.t_offset_hours))
  const maxH = Math.max(...cleanNodes.map(n => n.t_offset_hours))
  const span = maxH - minH || 1

  return (
    <div>
      <div className="section-head">
        <div>
          <span className="eyebrow">Investigation timeline</span>
          <h2>Drift reconstruction & vessel correlation</h2>
        </div>
      </div>

      <div className="panel">
        <h3 style={{ marginTop: 0, fontFamily: 'var(--font-display)', fontSize: 14 }}>Slick drift stages</h3>
        <div className="timeline-track">
          {cleanNodes.map((n, i) => (
            <div className="timeline-node" key={i}>
              <div className={`timeline-node__dot ${n.kind}`} />
              <div className="timeline-node__label">{n.label || (n.t_offset_hours > 0 ? `T+${n.t_offset_hours}h` : `T${n.t_offset_hours}h`)}</div>
              <div className="timeline-node__time">{n.timestamp.slice(0, 16).replace('T', ' ')}</div>
              {n.uncertainty_km !== undefined && <div className="timeline-node__unc">±{n.uncertainty_km} km</div>}
            </div>
          ))}
        </div>
      </div>

      <div className="panel" style={{ marginTop: 'var(--sp-4)' }}>
        <h3 style={{ marginTop: 0, fontFamily: 'var(--font-display)', fontSize: 14 }}>
          Vessel closest-approach vs. estimated origin time
        </h3>
        <p style={{ fontSize: 12, color: 'var(--text-muted)', marginTop: 0 }}>
          Position along the axis shows how many hours each candidate's closest approach fell from
          the estimated spill origin time (0h, dashed line).
        </p>
        <div className="time-axis">
          <div className="time-axis__origin-line" style={{ left: `${((0 - minH) / span) * 100}%` }} />
          {investigation.candidates.map((c, i) => {
            // API reports an absolute time delta from the origin (sign is not
            // preserved), so candidates are plotted as an offset to the right
            // of the origin line at a distance proportional to |Δt|.
            const pos = ((c.time_delta_hours - minH) / span) * 100
            const clamped = Math.max(2, Math.min(98, pos))
            return (
              <div
                key={c.mmsi}
                className="time-axis__vessel"
                style={{ left: `${clamped}%` }}
                onClick={() => onSelectVessel(c.mmsi)}
                onKeyDown={e => {
                  if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault()
                    onSelectVessel(c.mmsi)
                  }
                }}
                tabIndex={0}
                role="button"
                aria-label={`View evidence breakdown for ${c.vessel_name || `vessel ${c.mmsi}`}`}
              >
                <span className="lbl">{c.vessel_name || c.mmsi}</span>
                <span className="dot" style={{ background: RANK_COLORS[Math.min(i, 4)] }} />
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}
