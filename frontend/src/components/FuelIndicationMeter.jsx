import React, { useState } from 'react'
import './FuelIndicationMeter.css'

export default function FuelIndicationMeter({ fuel, comparison }) {
  const [unit, setUnit] = useState('mt') // 'mt' or 'litres'

  if (!fuel) {
    return (
      <div className="fuel-meter-empty">
        <p>Awaiting voyage calculation to evaluate vessel fuel endurance.</p>
      </div>
    )
  }

  const isLitres = unit === 'litres'
  const mult = isLitres ? (fuel.total_fuel_consumed_litres / Math.max(0.001, fuel.total_fuel_consumed_mt)) : 1.0
  const unitLabel = isLitres ? 'Litres' : 'MT'

  const capacity = fuel.fuel_tank_capacity_mt * mult
  const consumed = (isLitres ? fuel.total_fuel_consumed_litres : fuel.total_fuel_consumed_mt)
  const remaining = Math.max(0, isLitres ? fuel.remaining_fuel_litres : fuel.remaining_fuel_mt)
  const reserveThreshold = fuel.reserve_limit_mt * mult

  const remainingPct = Math.min(100, Math.max(0, (remaining / capacity) * 100))
  const reservePct = Math.min(100, (reserveThreshold / capacity) * 100)
  const consumedPct = Math.min(100, Math.max(0, (consumed / capacity) * 100))

  // Determine visual color theme
  let statusTheme = 'sufficient'
  let statusTitle = 'SUFFICIENT FUEL ON BOARD'
  let statusSubtitle = 'Voyage completed with healthy reserve margin above mandatory threshold.'

  if (fuel.fuel_status === 'LOW_RESERVE') {
    statusTheme = 'warning'
    statusTitle = 'LOW RESERVE ADVISORY'
    statusSubtitle = 'Arrival fuel drops below mandatory 15% reserve limit. Bunkering recommended.'
  } else if (fuel.fuel_status === 'INSUFFICIENT') {
    statusTheme = 'critical'
    statusTitle = 'CRITICAL: INSUFFICIENT FUEL'
    statusSubtitle = 'Estimated consumption exceeds available onboard fuel. Refueling mandatory.'
  }

  return (
    <div className={`fuel-meter-container fuel-meter-container--${statusTheme}`}>
      <div className="fuel-meter-header">
        <div className="fuel-meter-title-group">
          <svg className="fuel-meter-icon" viewBox="0 0 24 24" fill="none">
            <path d="M4 22V4c0-.6.4-1 1-1h6c.6 0 1 .4 1 1v18M4 11h8M12 8h3a3 3 0 013 3v7a2 2 0 002 2 2 2 0 002-2v-6l-3-3" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"/>
          </svg>
          <div>
            <h4>Vessel Fuel Intelligence & Endurance</h4>
            <span className="fuel-meter-subtitle">Hydrodynamic SFOC power calculation</span>
          </div>
        </div>

        <div className="fuel-unit-toggle" role="group" aria-label="Fuel unit selector">
          <button
            type="button"
            className={`unit-btn ${unit === 'mt' ? 'active' : ''}`}
            onClick={() => setUnit('mt')}
          >
            MT
          </button>
          <button
            type="button"
            className={`unit-btn ${unit === 'litres' ? 'active' : ''}`}
            onClick={() => setUnit('litres')}
          >
            Litres
          </button>
        </div>
      </div>

      {/* Prominent Status Callout Banner */}
      <div className={`fuel-status-banner fuel-status-banner--${statusTheme}`}>
        <div className="fuel-status-banner__badge">
          {statusTheme === 'sufficient' && (
            <svg viewBox="0 0 24 24" fill="none"><path d="M20 6L9 17l-5-5" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"/></svg>
          )}
          {statusTheme === 'warning' && (
            <svg viewBox="0 0 24 24" fill="none"><path d="M12 9v4m0 4h.01M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/></svg>
          )}
          {statusTheme === 'critical' && (
            <svg viewBox="0 0 24 24" fill="none"><circle cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="2"/><line x1="15" y1="9" x2="9" y2="15" stroke="currentColor" strokeWidth="2"/><line x1="9" y1="9" x2="15" y2="15" stroke="currentColor" strokeWidth="2"/></svg>
          )}
          <span>{statusTitle}</span>
        </div>
        <p className="fuel-status-banner__text">{statusSubtitle}</p>
      </div>

      {/* Visual Level Gauge Bar */}
      <div className="fuel-gauge-card">
        <div className="fuel-gauge-header">
          <span>Tank Capacity Fill Level</span>
          <span className="mono">{remaining.toLocaleString(undefined, { maximumFractionDigits: 1 })} / {capacity.toLocaleString(undefined, { maximumFractionDigits: 1 })} {unitLabel} ({remainingPct.toFixed(1)}%)</span>
        </div>

        <div className="fuel-gauge-track">
          {/* Reserve Marker Line */}
          <div
            className="fuel-gauge-reserve-marker"
            style={{ left: `${reservePct}%` }}
            title={`Mandatory 15% Reserve Buffer (${reserveThreshold.toFixed(1)} ${unitLabel})`}
          >
            <span className="reserve-tag">15% Reserve</span>
          </div>

          {/* Fill Level */}
          <div
            className={`fuel-gauge-fill fuel-gauge-fill--${statusTheme}`}
            style={{ width: `${remainingPct}%` }}
          />
        </div>

        <div className="fuel-gauge-legend">
          <div className="legend-item">
            <span className="legend-color legend-color--consumed" />
            <span>Voyage Burn: <strong>{consumed.toLocaleString(undefined, { maximumFractionDigits: 1 })} {unitLabel}</strong> ({consumedPct.toFixed(1)}%)</span>
          </div>
          <div className="legend-item">
            <span className="legend-color legend-color--reserve" />
            <span>Safety Reserve: <strong>{reserveThreshold.toLocaleString(undefined, { maximumFractionDigits: 1 })} {unitLabel}</strong></span>
          </div>
          <div className="legend-item">
            <span className={`legend-color legend-color--${statusTheme}`} />
            <span>End-of-Voyage Fuel: <strong>{remaining.toLocaleString(undefined, { maximumFractionDigits: 1 })} {unitLabel}</strong></span>
          </div>
        </div>
      </div>

      {/* Grid of Key Hydrodynamic & Economic Readouts */}
      <div className="fuel-readouts-grid">
        <div className="readout-card">
          <span className="readout-label">Estimated Range</span>
          <span className="readout-val mono">{fuel.remaining_range_nm.toLocaleString()} <small>NM</small></span>
          <span className="readout-note">At current {fuel.speed_knots} kt speed</span>
        </div>

        <div className="readout-card">
          <span className="readout-label">Trip Duration</span>
          <span className="readout-val mono">{fuel.duration_hours} <small>hrs</small></span>
          <span className="readout-note">Propulsion: {fuel.power_kw.toLocaleString()} kW</span>
        </div>

        <div className="readout-card">
          <span className="readout-label">Fuel Bunker Cost</span>
          <span className="readout-val mono">${fuel.fuel_cost_usd.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</span>
          <span className="readout-note">@ $650 / MT VLSFO</span>
        </div>

        <div className="readout-card">
          <span className="readout-label">CO2 Footprint</span>
          <span className="readout-val mono">{fuel.co2_emissions_mt.toFixed(2)} <small>MT</small></span>
          <span className="readout-note">IMO GHG factor 3.1144</span>
        </div>
      </div>

      {/* Extra Avoidance Overhead (if available) */}
      {comparison && comparison.extra_fuel_consumed_mt > 0.001 && (
        <div className="avoidance-delta-panel">
          <div className="delta-title">
            <svg viewBox="0 0 24 24" fill="none"><path d="M13 17h8m0 0l-4-4m4 4l-4 4M3 7h18" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"/></svg>
            <span>Environmental Spill-Avoidance Overhead:</span>
          </div>
          <div className="delta-chips">
            <span className="delta-chip mono">+{comparison.extra_distance_nm} NM ({comparison.extra_distance_pct}%)</span>
            <span className="delta-chip mono">+{comparison.extra_duration_hours} hrs</span>
            <span className="delta-chip mono">
              +{isLitres ? (comparison.extra_fuel_consumed_litres.toLocaleString() + ' L') : (comparison.extra_fuel_consumed_mt + ' MT')}
            </span>
            <span className="delta-chip mono">+${comparison.extra_cost_usd.toLocaleString(undefined, { maximumFractionDigits: 0 })}</span>
            <span className="delta-chip delta-chip--safe mono">Safety Margin: {comparison.safety_buffer_maintained_nm} NM</span>
          </div>
        </div>
      )}
    </div>
  )
}
