import './RankingTab.css'

const RANK_COLORS = ['var(--signal-coral)', 'var(--signal-amber)', 'var(--signal-cyan)', 'var(--layer-neutral)', 'var(--layer-neutral)']

function behaviourLevel(flags) {
  if (flags.some(f => f.severity === 'high')) return 'high'
  if (flags.some(f => f.severity === 'notable')) return 'notable'
  return 'none'
}

export default function RankingTab({ investigation, onSelectVessel }) {
  const candidates = investigation.candidates || []
  const aisSummary = investigation.ais_summary || {}
  const sat = investigation.satellite || {}
  const gating = sat.attribution_gating || {
    gated: aisSummary.attribution_gated || false,
    status: aisSummary.attribution_status || 'CONFIRMED_OIL_ATTRIBUTION',
    reason: aisSummary.attribution_decision_reason || '',
  }

  return (
    <div className="ranking-tab-workspace">
      <div className="section-head">
        <div>
          <span className="eyebrow">Stage 13–15 · Explainable Spatio-Temporal Attribution</span>
          <h2>Candidate Vessel Ranking & Safety Attribution</h2>
        </div>
        <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
          <span className={`bento-badge ${gating.gated ? 'bento-badge--coral' : 'bento-badge--green'}`}>
            POLICY: {gating.status || (candidates.length > 0 ? 'CONFIRMED' : 'NO_CANDIDATES')}
          </span>
          <span className="bento-badge bento-badge--cyan">
            RADIUS: {aisSummary.search_radius_km ?? 50} KM
          </span>
        </div>
      </div>

      {/* Fail-closed safety banner (Audit §3) */}
      {gating.gated && (
        <div className="safety-alert-banner safety-alert-banner--coral" style={{ marginBottom: 'var(--sp-4)' }}>
          <div style={{ flexShrink: 0, marginTop: '2px' }}>
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="var(--signal-coral)" strokeWidth="2">
              <polygon points="7.86 2 16.14 2 22 7.86 22 16.14 16.14 22 7.86 22 2 16.14 2 7.86 7.86 2"/>
              <line x1="12" y1="8" x2="12" y2="12"/>
              <line x1="12" y1="16" x2="12.01" y2="16"/>
            </svg>
          </div>
          <div>
            <strong style={{ color: 'var(--signal-coral)', fontSize: '14px', display: 'block', marginBottom: '4px' }}>
              ATTRIBUTION ABSTAINED: {gating.status}
            </strong>
            <p style={{ fontSize: '12.5px', color: 'var(--text-secondary)', margin: '0 0 6px 0', lineHeight: 1.5 }}>
              {gating.reason || 'Vessel attribution has failed closed to prevent unverified accusations.'}
            </p>
            <div style={{ fontSize: '11.5px', color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>
              Audit §3 Finding: The naive attribution policy flagged nearby vessels in 34% of lookalike/non-oil scenes. To protect innocent maritime operators, ranking requires explicit oil classification confirmation and minimum slick area (≥ 250 px).
            </div>
          </div>
        </div>
      )}

      {/* Candidates table or abstention display */}
      <div className="panel">
        {candidates.length > 0 ? (
          <table className="rank-table">
            <thead>
              <tr>
                <th>Rank</th>
                <th>Candidate Vessel</th>
                <th>MMSI</th>
                <th>Distance to Origin</th>
                <th>Time Δ</th>
                <th>Behaviour Anomaly</th>
                <th>Attribution Index</th>
              </tr>
            </thead>
            <tbody>
              {candidates.map((c, i) => {
                const level = behaviourLevel(c.behavior_flags || [])
                const selectThisVessel = () => onSelectVessel(c.mmsi)
                return (
                  <tr
                    key={c.mmsi}
                    className={i === 0 ? 'top-candidate' : ''}
                    onClick={selectThisVessel}
                    onKeyDown={e => {
                      if (e.key === 'Enter' || e.key === ' ') {
                        e.preventDefault()
                        selectThisVessel()
                      }
                    }}
                    tabIndex={0}
                    role="button"
                    aria-label={`View evidence breakdown for ${c.vessel_name || `vessel ${c.mmsi}`}, rank ${i + 1}, score ${c.final_score}`}
                  >
                    <td>
                      <span className="rank-badge" style={{ background: RANK_COLORS[Math.min(i, 4)] }}>
                        {i + 1}
                      </span>
                    </td>
                    <td>
                      {c.vessel_name || '—'}
                      <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>{c.vessel_type || 'Commercial Vessel'}</div>
                    </td>
                    <td className="mmsi-cell">{c.mmsi}</td>
                    <td>{c.min_distance_km} km</td>
                    <td>{c.time_delta_hours} h</td>
                    <td>
                      <span className={`behaviour-tag ${level}`}>{level === 'none' ? 'normal' : level}</span>
                    </td>
                    <td>
                      <div className="score-cell">
                        <div className="score-track">
                          <div
                            className="score-fill"
                            style={{ width: `${c.final_score}%`, background: RANK_COLORS[Math.min(i, 4)] }}
                          />
                        </div>
                        <span className="score-num">{c.final_score}</span>
                      </div>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        ) : (
          <div style={{ padding: '32px 16px', textAlign: 'center' }}>
            <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="var(--text-muted)" strokeWidth="1.5" style={{ marginBottom: '12px' }}>
              <circle cx="12" cy="12" r="10"/>
              <line x1="4.93" y1="4.93" x2="19.07" y2="19.07"/>
            </svg>
            <h4 style={{ margin: '0 0 8px 0', fontFamily: 'var(--font-display)', color: 'var(--text-primary)' }}>
              {gating.gated ? 'Attribution Withheld by Safety Gate' : 'No Candidate Vessels Found'}
            </h4>
            <p style={{ margin: '0 auto', maxWidth: '500px', fontSize: '13px', color: 'var(--text-secondary)' }}>
              {gating.gated
                ? 'Candidate vessel scoring is suppressed for this scene because satellite evidence did not meet the positive oil classification threshold.'
                : 'No commercial or fishing vessels with active AIS transmissions were detected within the spatial search radius and hindcast time window.'}
            </p>
          </div>
        )}
      </div>

      {/* AIS Excluded Traffic Summary */}
      {aisSummary.excluded_vessels && aisSummary.excluded_vessels.length > 0 && (
        <div className="panel" style={{ marginTop: 'var(--sp-4)' }}>
          <h3 style={{ marginTop: 0, fontFamily: 'var(--font-display)', fontSize: 14 }}>
            Excluded Nearby Traffic Summary ({aisSummary.excluded_vessels.length} vessels evaluated)
          </h3>
          <p style={{ fontSize: '12px', color: 'var(--text-secondary)', marginBottom: '12px' }}>
            Vessels detected in regional AIS feeds that did not meet spatial or temporal correlation criteria with the estimated spill origin:
          </p>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: '8px' }}>
            {aisSummary.excluded_vessels.slice(0, 6).map((ex, idx) => (
              <div key={idx} style={{ background: 'var(--bg-inset)', padding: '8px 10px', borderRadius: '4px', fontSize: '11.5px', border: '1px solid var(--border-hairline)' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', fontWeight: '600', color: 'var(--text-primary)' }}>
                  <span>MMSI {ex.mmsi}</span>
                  <span style={{ color: 'var(--text-muted)' }}>{ex.vessel_type || 'Unknown'}</span>
                </div>
                <div style={{ color: 'var(--text-muted)', fontSize: '11px', marginTop: '2px' }}>
                  Reason: {ex.exclusion_reason || 'Outside temporal window'}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      <p className="caveat-note" style={{ marginTop: 'var(--sp-4)' }}>
        Select a row to see the full explainable evidence breakdown for that vessel. Scores are a
        transparent spatio-temporal correlation index — not proof of responsibility. Candidate vessels are withheld when scenes fail the oil-confirmation safety gate.
      </p>
    </div>
  )
}
