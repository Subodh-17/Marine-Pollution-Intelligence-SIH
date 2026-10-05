import './RankingTab.css'

const RANK_COLORS = ['var(--signal-coral)', 'var(--signal-amber)', 'var(--signal-cyan)', 'var(--layer-neutral)', 'var(--layer-neutral)']

function behaviourLevel(flags) {
  if (flags.some(f => f.severity === 'high')) return 'high'
  if (flags.some(f => f.severity === 'notable')) return 'notable'
  return 'none'
}

export default function RankingTab({ investigation, onSelectVessel }) {
  const candidates = investigation.candidates

  return (
    <div>
      <div className="section-head">
        <div>
          <span className="eyebrow">Stage 13–15 · Explainable attribution</span>
          <h2>Candidate vessel ranking</h2>
        </div>
        <p>{candidates.length} candidate(s) within the investigation window</p>
      </div>

      <div className="panel">
        <table className="rank-table">
          <thead>
            <tr>
              <th>Rank</th><th>Vessel</th><th>MMSI</th><th>Distance</th><th>Time Δ</th><th>Behaviour</th><th>Score</th>
            </tr>
          </thead>
          <tbody>
            {candidates.map((c, i) => {
              const level = behaviourLevel(c.behavior_flags)
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
                  <td><span className="rank-badge" style={{ background: RANK_COLORS[Math.min(i, 4)] }}>{i + 1}</span></td>
                  <td>{c.vessel_name || '—'}<div style={{ fontSize: 11, color: 'var(--text-muted)' }}>{c.vessel_type}</div></td>
                  <td className="mmsi-cell">{c.mmsi}</td>
                  <td>{c.min_distance_km} km</td>
                  <td>{c.time_delta_hours} h</td>
                  <td><span className={`behaviour-tag ${level}`}>{level === 'none' ? 'low' : level}</span></td>
                  <td>
                    <div className="score-cell">
                      <div className="score-track"><div className="score-fill" style={{ width: `${c.final_score}%`, background: RANK_COLORS[Math.min(i, 4)] }} /></div>
                      <span className="score-num">{c.final_score}</span>
                    </div>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
        {candidates.length === 0 && <p style={{ color: 'var(--text-muted)' }}>No candidate vessels found within the search parameters.</p>}
      </div>

      <p className="caveat-note" style={{ marginTop: 'var(--sp-4)' }}>
        Select a row to see the full explainable evidence breakdown for that vessel. Scores are a
        transparent spatio-temporal correlation index — not proof of responsibility.
      </p>
    </div>
  )
}
