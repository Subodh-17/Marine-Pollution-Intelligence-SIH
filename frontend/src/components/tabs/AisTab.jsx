import { useState } from 'react'
import InteractiveMap from '../InteractiveMap'
import api from '../../api'
import './AisTab.css'

const RANK_COLORS = ['var(--signal-coral)', 'var(--signal-amber)', 'var(--signal-cyan)', 'var(--layer-neutral)']

export default function AisTab({ investigation, selectedMmsi, onSelectVessel }) {
  const [radius, setRadius] = useState(investigation.ais_summary.search_radius_km)
  const [window, setWindowH] = useState(investigation.ais_summary.time_window_hours)
  const [live, setLive] = useState(null) // { vessel_count, candidates, search_radius_km, time_window_hours }
  const [loading, setLoading] = useState(false)

  const data = live || {
    candidates: investigation.candidates,
    vessel_count: investigation.ais_summary.unique_vessels_in_window,
    excluded_vessels: investigation.ais_summary.excluded_vessels,
  }
  const ais = investigation.ais_summary
  const excludedVessels = data.excluded_vessels || []

  async function applyFilters() {
    setLoading(true)
    try {
      const result = await api.reanalyzeAis(investigation.id, { searchRadiusKm: radius, timeWindowHours: window })
      setLive(result)
    } catch (e) {
      console.error(e)
    } finally {
      setLoading(false)
    }
  }

  // build a shim investigation object for the map so it reflects live filter results
  const mapInvestigation = {
    ...investigation,
    candidates: data.candidates,
    ais_summary: { ...investigation.ais_summary, search_radius_km: radius },
  }

  return (
    <div>
      <div className="section-head">
        <div>
          <span className="eyebrow">Stage 9–11 · AIS reconstruction</span>
          <h2>AIS traffic & candidate filtering</h2>
        </div>
        <p>Source: {ais.ais_source}</p>
      </div>

      <div className="panel" style={{ marginBottom: 'var(--sp-5)' }}>
        <div className="filter-row">
          <div className="filter-field">
            <label>Search radius <b>{radius} km</b></label>
            <input type="range" min="10" max="150" step="5" value={radius} onChange={e => setRadius(+e.target.value)} />
          </div>
          <div className="filter-field">
            <label>Time window <b>±{window} h</b></label>
            <input type="range" min="2" max="30" step="1" value={window} onChange={e => setWindowH(+e.target.value)} />
          </div>
          <button className="btn-secondary" onClick={applyFilters} disabled={loading}>
            {loading ? 'Re-analyzing…' : 'Apply filters'}
          </button>
        </div>

        <div className="ais-funnel">
          <div className="ais-funnel__step"><div className="n">{live ? live.total_raw_records : ais.total_raw_records}</div><div className="l">Raw pings</div></div>
          <div className="ais-funnel__arrow">→</div>
          <div className="ais-funnel__step"><div className="n">{live ? live.cleaned_records : ais.cleaned_records}</div><div className="l">Cleaned</div></div>
          <div className="ais-funnel__arrow">→</div>
          <div className="ais-funnel__step"><div className="n">{live ? live.within_spatial_filter : ais.within_spatial_filter}</div><div className="l">Within radius</div></div>
          <div className="ais-funnel__arrow">→</div>
          <div className="ais-funnel__step"><div className="n">{data.vessel_count}</div><div className="l">Candidate vessels</div></div>
        </div>
      </div>

      <div className="two-col">
        <InteractiveMap
          investigation={mapInvestigation}
          height={440}
          defaultLayers={{ hindcast: false, forecast: false, satellite: false }}
          selectedMmsi={selectedMmsi}
          onSelectVessel={onSelectVessel}
        />
        <div className="panel">
          <h3 style={{ marginTop: 0, fontFamily: 'var(--font-display)', fontSize: 15 }}>Candidate vessels</h3>
          {data.candidates.map((c, i) => (
            <div
              className="vessel-mini-row"
              key={c.mmsi}
              onClick={() => onSelectVessel(c.mmsi)}
              onKeyDown={e => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault()
                  onSelectVessel(c.mmsi)
                }
              }}
              tabIndex={0}
              role="button"
              aria-label={`View evidence breakdown for ${c.vessel_name || `vessel ${c.mmsi}`}, score ${c.final_score}`}
            >
              <span>
                <span className="rank-dot" style={{ background: RANK_COLORS[Math.min(i, 3)] }} />
                {c.vessel_name || c.mmsi}
              </span>
              <span className="score">{c.final_score}</span>
            </div>
          ))}
          {data.candidates.length === 0 && <p style={{ color: 'var(--text-muted)', fontSize: 13 }}>No vessels within the current filters.</p>}
        </div>
      </div>

      <div className="panel" style={{ marginTop: 'var(--sp-5)' }}>
        <h3 style={{ marginTop: 0, fontFamily: 'var(--font-display)', fontSize: 15 }}>
          Nearby vessels ruled out
        </h3>
        <p className="caveat-note" style={{ marginTop: 0 }}>
          Not every vessel in the raw AIS traffic becomes a candidate. These are the closest
          vessels the spatial/temporal filters actually excluded, and why — not just an aggregate
          count.
        </p>
        {excludedVessels.length > 0 ? (
          <ul className="excluded-vessel-list">
            {excludedVessels.map(v => (
              <li key={v.mmsi} className="excluded-vessel-row">
                <span className="excluded-vessel-row__name">{v.vessel_name || v.mmsi}</span>
                <span className="excluded-vessel-row__reason">
                  {v.vessel_name ? `MMSI ${v.mmsi} ` : ''}{v.reason}
                </span>
              </li>
            ))}
          </ul>
        ) : (
          <p style={{ color: 'var(--text-muted)', fontSize: 13 }}>
            No nearby vessels were excluded — every vessel that appeared in the raw AIS traffic
            near this spill either became a candidate or was never near the origin at all.
          </p>
        )}
      </div>
    </div>
  )
}
