import React, { useState, useEffect, useMemo, useRef, useCallback } from 'react'
import { MapContainer, TileLayer, Polygon, Polyline, Marker, Popup, useMap, useMapEvents } from 'react-leaflet'
import L from 'leaflet'
import 'leaflet/dist/leaflet.css'
import './RoutePlannerTab.css'
import FuelIndicationMeter from '../FuelIndicationMeter'
import api from '../../api'

const TILE_PROVIDERS = [
  {
    id: 'osm',
    url: 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    subdomains: ['a', 'b', 'c'],
  },
]

function FitRouteBounds({ coords }) {
  const map = useMap()
  useEffect(() => {
    if (coords && coords.length >= 2) {
      try {
        const bounds = L.latLngBounds(coords.map(c => [c[0], c[1]]))
        map.fitBounds(bounds, { padding: [40, 40], maxZoom: 12 })
      } catch {
        /* best-effort */
      }
    }
  }, [map, coords])
  return null
}

function MapClickHandler({ onClickCoord, mode }) {
  useMapEvents({
    click(e) {
      if (mode && onClickCoord) {
        onClickCoord([roundCoord(e.latlng.lat), roundCoord(e.latlng.lng)])
      }
    },
  })
  return null
}

function roundCoord(val) {
  return Math.round(val * 1000) / 1000
}

function createPinIcon(color, label) {
  return L.divIcon({
    className: 'custom-map-pin',
    html: `<div style="
      background: ${color};
      color: #050d14;
      font-weight: 800;
      font-size: 10px;
      padding: 3px 7px;
      border-radius: 12px;
      border: 2px solid #ffffff;
      box-shadow: 0 2px 8px rgba(0,0,0,0.5);
      white-space: nowrap;
      display: inline-block;
      transform: translate(-50%, -100%);
    ">${label}</div>`,
    iconSize: [0, 0],
    iconAnchor: [0, 0],
  })
}

export default function RoutePlannerTab({ investigation }) {
  // Preset data states
  const [ports, setPorts] = useState([])
  const [vesselProfiles, setVesselProfiles] = useState([])
  const [spillZones, setSpillZones] = useState([])

  // User input states
  const [originPortId, setOriginPortId] = useState('mumbai')
  const [originCoord, setOriginCoord] = useState([18.940, 72.840])
  const [originName, setOriginName] = useState('Mumbai Port / Anchorage')

  const [destPortId, setDestPortId] = useState('mormugao')
  const [destCoord, setDestCoord] = useState([15.415, 73.790])
  const [destName, setDestName] = useState('Mormugao Port (Goa)')

  const [vesselType, setVesselType] = useState('Tanker')
  const [customSpeed, setCustomSpeed] = useState('')
  const [currentFuel, setCurrentFuel] = useState('')
  const [spillBufferNm, setSpillBufferNm] = useState(2.5)
  const [avoidSpills, setAvoidSpills] = useState(true)

  // Map click picking mode
  const [pickMode, setPickMode] = useState(null) // 'origin' | 'dest' | null

  // Plan result state
  const [routePlan, setRoutePlan] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  // Initial data loading
  useEffect(() => {
    let mounted = true

    // Fetch ports
    api.getRoutePorts()
      .then(data => {
        if (mounted && Array.isArray(data)) setPorts(data)
      })
      .catch(console.error)

    // Fetch vessels
    api.getVesselProfiles()
      .then(data => {
        if (mounted && Array.isArray(data)) setVesselProfiles(data)
      })
      .catch(console.error)

    // Fetch spills
    api.getRouteSpills({ investigationId: investigation?.id })
      .then(data => {
        if (mounted && Array.isArray(data)) setSpillZones(data)
      })
      .catch(console.error)

    return () => { mounted = false }
  }, [investigation?.id])

  // If active investigation has a detected slick geometry, inject it cleanly
  const effectiveSpillZones = useMemo(() => {
    const list = [...spillZones]
    const geo = investigation?.satellite?.geometry
    if (geo?.polygon_latlon && geo.polygon_latlon.length >= 3) {
      const id = `inv_${investigation.id}`
      if (!list.some(sz => sz.id === id)) {
        list.unshift({
          id,
          name: `Active SAR Slick (${investigation.id.slice(0, 8)})`,
          polygon_latlon: geo.polygon_latlon,
          buffer_nm: spillBufferNm,
          centroid_lat: geo.centroid_lat,
          centroid_lon: geo.centroid_lon,
          area_km2: geo.area_km2,
          active: true,
        })
      }
    }
    return list
  }, [spillZones, investigation, spillBufferNm])

  // Handle port selection
  const handleOriginSelect = (id) => {
    setOriginPortId(id)
    if (id === 'custom') return
    const p = ports.find(port => port.id === id)
    if (p) {
      setOriginCoord([p.lat, p.lon])
      setOriginName(p.name)
    }
  }

  const handleDestSelect = (id) => {
    setDestPortId(id)
    if (id === 'custom') return
    const p = ports.find(port => port.id === id)
    if (p) {
      setDestCoord([p.lat, p.lon])
      setDestName(p.name)
    }
  }

  // Calculate route plan
  const handleCalculateRoute = useCallback(() => {
    setLoading(true)
    setError(null)

    const payload = {
      origin: { lat: originCoord[0], lon: originCoord[1], name: originName },
      destination: { lat: destCoord[0], lon: destCoord[1], name: destName },
      vessel_type: vesselType,
      custom_speed_knots: customSpeed ? parseFloat(customSpeed) : null,
      current_fuel_mt: currentFuel ? parseFloat(currentFuel) : null,
      spill_buffer_nm: parseFloat(spillBufferNm),
      avoid_spills: avoidSpills,
      investigation_id: investigation?.id || null,
    }

    api.planRoute(payload)
      .then(res => {
        setRoutePlan(res)
        setLoading(false)
      })
      .catch(err => {
        console.error('Route calculation error:', err)
        setError(err.response?.data?.detail || 'Failed to calculate optimal route.')
        setLoading(false)
      })
  }, [originCoord, originName, destCoord, destName, vesselType, customSpeed, currentFuel, spillBufferNm, avoidSpills, investigation?.id])

  // Trigger initial calculation on mount once defaults are set
  const initialCalculated = useRef(false)
  useEffect(() => {
    if (!initialCalculated.current) {
      initialCalculated.current = true
      handleCalculateRoute()
    }
  }, [handleCalculateRoute])

  // Map click handler callback
  const handleMapCoordClick = (coord) => {
    if (pickMode === 'origin') {
      setOriginCoord(coord)
      setOriginName(`Custom (${coord[0]}, ${coord[1]})`)
      setOriginPortId('custom')
      setPickMode(null)
    } else if (pickMode === 'dest') {
      setDestCoord(coord)
      setDestName(`Custom (${coord[0]}, ${coord[1]})`)
      setDestPortId('custom')
      setPickMode(null)
    }
  }

  // Bounds for map fitting
  const allMapCoords = useMemo(() => {
    const list = [originCoord, destCoord]
    if (routePlan?.baseline_route?.coordinates) {
      list.push(...routePlan.baseline_route.coordinates)
    }
    if (routePlan?.spill_avoiding_route?.coordinates) {
      list.push(...routePlan.spill_avoiding_route.coordinates)
    }
    effectiveSpillZones.forEach(sz => {
      if (sz.polygon_latlon) list.push(...sz.polygon_latlon)
    })
    return list
  }, [originCoord, destCoord, routePlan, effectiveSpillZones])

  const selectedVessel = useMemo(() => {
    return vesselProfiles.find(v => v.vessel_type === vesselType) || null
  }, [vesselProfiles, vesselType])

  return (
    <div className="route-planner-tab">
      {/* Top Banner Header */}
      <div className="route-planner-banner">
        <div className="banner-title-group">
          <span className="banner-eyebrow">MARITIME NAVIGATION & EMISSION CONTROL</span>
          <h2>Spill-Aware Route Optimization & Fuel Intelligence</h2>
          <p>
            Dynamic shortest-path maritime navigation avoiding detected Sentinel-1 SAR oil slick zones
            with hydrodynamic Specific Fuel Oil Consumption (SFOC) physics and SOLAS advisory compliance.
          </p>
        </div>
        <div className="banner-badge-group">
          <span className="badge badge--solas">IMO RESOLUTION A.893(21)</span>
          <span className="badge badge--advisory">SOLAS CH. V ADVISORY</span>
        </div>
      </div>

      {/* Main Two-Column Layout */}
      <div className="route-planner-grid">
        {/* Left Column: Interactive Map */}
        <div className="map-panel">
          <div className="map-panel-header">
            <div className="map-legend-items">
              <span className="legend-chip legend-chip--baseline">
                <span className="chip-line chip-line--baseline" /> Baseline Sea Path
              </span>
              <span className="legend-chip legend-chip--avoidance">
                <span className="chip-line chip-line--avoidance" /> Spill-Avoiding Detour
              </span>
              <span className="legend-chip legend-chip--spill">
                <span className="chip-box chip-box--spill" /> Detected Slick
              </span>
              <span className="legend-chip legend-chip--buffer">
                <span className="chip-box chip-box--buffer" /> {spillBufferNm} NM Buffer
              </span>
            </div>

            <div className="map-pick-controls">
              <button
                type="button"
                className={`btn-pick ${pickMode === 'origin' ? 'active' : ''}`}
                onClick={() => setPickMode(pickMode === 'origin' ? null : 'origin')}
                title="Click on map to set Origin coordinates"
              >
                {pickMode === 'origin' ? 'Click Map for Origin...' : 'Set Origin on Map'}
              </button>
              <button
                type="button"
                className={`btn-pick ${pickMode === 'dest' ? 'active' : ''}`}
                onClick={() => setPickMode(pickMode === 'dest' ? null : 'dest')}
                title="Click on map to set Destination coordinates"
              >
                {pickMode === 'dest' ? 'Click Map for Destination...' : 'Set Dest on Map'}
              </button>
            </div>
          </div>

          <div className="leaflet-wrapper">
            <MapContainer
              center={originCoord}
              zoom={7}
              style={{ width: '100%', height: '100%', minHeight: '520px' }}
            >
              <TileLayer
                url={TILE_PROVIDERS[0].url}
                attribution={TILE_PROVIDERS[0].attribution}
              />

              <FitRouteBounds coords={allMapCoords} />
              <MapClickHandler onClickCoord={handleMapCoordClick} mode={pickMode} />

              {/* Origin Marker */}
              <Marker
                position={originCoord}
                icon={createPinIcon('#3fd8c4', `ORIGIN: ${originName}`)}
              >
                <Popup>
                  <strong>Departure Point:</strong> {originName}<br />
                  <span className="mono">{originCoord[0]}°N, {originCoord[1]}°E</span>
                </Popup>
              </Marker>

              {/* Destination Marker */}
              <Marker
                position={destCoord}
                icon={createPinIcon('#2ecc71', `DEST: ${destName}`)}
              >
                <Popup>
                  <strong>Destination Point:</strong> {destName}<br />
                  <span className="mono">{destCoord[0]}°N, {destCoord[1]}°E</span>
                </Popup>
              </Marker>

              {/* Active Oil Slick Polygons & Buffers */}
              {effectiveSpillZones.map((sz, idx) => (
                <React.Fragment key={sz.id || idx}>
                  {/* Raw Slick Polygon */}
                  <Polygon
                    positions={sz.polygon_latlon}
                    pathOptions={{
                      color: '#ff6b5c',
                      fillColor: '#7a3129',
                      fillOpacity: 0.65,
                      weight: 2,
                    }}
                  >
                    <Popup>
                      <strong>{sz.name}</strong><br />
                      Area: {sz.area_km2 ? `${sz.area_km2} km²` : 'SAR Detected Slick'}<br />
                      Center: {sz.centroid_lat?.toFixed(3)}°N, {sz.centroid_lon?.toFixed(3)}°E
                    </Popup>
                  </Polygon>

                  {/* Buffer Ring if routePlan supplied it */}
                  {routePlan?.spill_zones?.find(z => z.id === sz.id) && (
                    <Polygon
                      positions={sz.polygon_latlon}
                      pathOptions={{
                        color: '#ffb84d',
                        weight: 1.5,
                        dashArray: '6, 6',
                        fillOpacity: 0.08,
                        fillColor: '#ffb84d',
                      }}
                    />
                  )}
                </React.Fragment>
              ))}

              {/* Baseline Path */}
              {routePlan?.baseline_route?.coordinates && (
                <Polyline
                  positions={routePlan.baseline_route.coordinates}
                  pathOptions={{
                    color: routePlan.baseline_route.intersects_spill ? '#ff6b5c' : '#7b8a97',
                    weight: 3,
                    dashArray: '8, 8',
                    opacity: 0.85,
                  }}
                >
                  <Popup>
                    <strong>Baseline Sea Path</strong><br />
                    Distance: {routePlan.baseline_route.distance_nm} NM<br />
                    Duration: {routePlan.baseline_route.duration_hours} hrs<br />
                    Hazard Collision: {routePlan.baseline_route.intersects_spill ? 'YES (Crosses Slick)' : 'None'}
                  </Popup>
                </Polyline>
              )}

              {/* Spill-Avoiding Detour Path */}
              {routePlan?.spill_avoiding_route?.coordinates && (
                <Polyline
                  positions={routePlan.spill_avoiding_route.coordinates}
                  pathOptions={{
                    color: '#3fd8c4',
                    weight: 4.5,
                    opacity: 0.95,
                  }}
                >
                  <Popup>
                    <strong>Spill-Avoiding Detour</strong><br />
                    Distance: {routePlan.spill_avoiding_route.distance_nm} NM<br />
                    Duration: {routePlan.spill_avoiding_route.duration_hours} hrs<br />
                    Safety Buffer Maintained: {routePlan.comparison?.safety_buffer_maintained_nm} NM
                  </Popup>
                </Polyline>
              )}
            </MapContainer>
          </div>
        </div>

        {/* Right Column: Controls & Tactical Analytics */}
        <div className="controls-panel">
          {/* Passage Planning Parameters Form */}
          <div className="planning-form-card">
            <h3 className="section-title">Voyage & Vessel Parameters</h3>

            <div className="form-row form-row--ports">
              <div className="form-group">
                <label>Origin Port / Waypoint</label>
                <select
                  value={originPortId}
                  onChange={(e) => handleOriginSelect(e.target.value)}
                >
                  {ports.map(p => (
                    <option key={p.id} value={p.id}>{p.name} ({p.country})</option>
                  ))}
                  <option value="custom">Custom Coordinates...</option>
                </select>
                <div className="coord-readout mono">
                  {originCoord[0]}°N, {originCoord[1]}°E
                </div>
              </div>

              <div className="form-group">
                <label>Destination Port / Waypoint</label>
                <select
                  value={destPortId}
                  onChange={(e) => handleDestSelect(e.target.value)}
                >
                  {ports.map(p => (
                    <option key={p.id} value={p.id}>{p.name} ({p.country})</option>
                  ))}
                  <option value="custom">Custom Coordinates...</option>
                </select>
                <div className="coord-readout mono">
                  {destCoord[0]}°N, {destCoord[1]}°E
                </div>
              </div>
            </div>

            <div className="form-row form-row--vessel">
              <div className="form-group">
                <label>Vessel Category</label>
                <select
                  value={vesselType}
                  onChange={(e) => setVesselType(e.target.value)}
                >
                  {vesselProfiles.map(v => (
                    <option key={v.vessel_type} value={v.vessel_type}>
                      {v.vessel_type} ({v.name})
                    </option>
                  ))}
                </select>
              </div>

              <div className="form-group">
                <label>Speed (Knots)</label>
                <input
                  type="number"
                  step="0.5"
                  min="2"
                  max="35"
                  placeholder={selectedVessel ? `${selectedVessel.service_speed_knots} kt (Default)` : '14.5'}
                  value={customSpeed}
                  onChange={(e) => setCustomSpeed(e.target.value)}
                />
              </div>

              <div className="form-group">
                <label>Fuel on Board (MT)</label>
                <input
                  type="number"
                  step="10"
                  min="1"
                  placeholder={selectedVessel ? `${(selectedVessel.fuel_tank_capacity_mt * 0.85).toFixed(0)} MT` : '2400'}
                  value={currentFuel}
                  onChange={(e) => setCurrentFuel(e.target.value)}
                />
              </div>
            </div>

            <div className="form-row form-row--sliders">
              <div className="form-group">
                <div className="slider-label-row">
                  <label>Environmental Safety Buffer</label>
                  <span className="mono bold">{spillBufferNm} NM</span>
                </div>
                <input
                  type="range"
                  min="0.5"
                  max="10.0"
                  step="0.5"
                  value={spillBufferNm}
                  onChange={(e) => setSpillBufferNm(parseFloat(e.target.value))}
                />
              </div>

              <div className="form-group form-group--toggle">
                <label className="checkbox-label">
                  <input
                    type="checkbox"
                    checked={avoidSpills}
                    onChange={(e) => setAvoidSpills(e.target.checked)}
                  />
                  <span>Hazard Spill Avoidance</span>
                </label>
                <span className="toggle-help">Reroute around detected oil slick polygons</span>
              </div>
            </div>

            <button
              type="button"
              className="btn-calculate-route"
              onClick={handleCalculateRoute}
              disabled={loading}
            >
              {loading ? 'Optimizing Navigational Path…' : '▶ CALCULATE VOYAGE & FUEL CONSUMPTION'}
            </button>

            {error && <div className="route-error-banner">{error}</div>}
          </div>

          {/* Side-by-Side Comparison Card */}
          {routePlan && (
            <div className="route-comparison-card">
              <h3 className="section-title">Voyage Trade-Off & Safety Comparison</h3>

              <div className="comparison-table-wrapper">
                <table className="comparison-table">
                  <thead>
                    <tr>
                      <th>Metric</th>
                      <th>Baseline (Direct)</th>
                      <th>Spill-Avoiding Detour</th>
                      <th>Variance / Impact</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr>
                      <td>Total Distance</td>
                      <td className="mono">{routePlan.baseline_route.distance_nm} NM</td>
                      <td className="mono bold">{routePlan.spill_avoiding_route ? `${routePlan.spill_avoiding_route.distance_nm} NM` : 'Identical'}</td>
                      <td className="mono variance">
                        {routePlan.comparison ? `+${routePlan.comparison.extra_distance_nm} NM (+${routePlan.comparison.extra_distance_pct}%)` : '0%'}
                      </td>
                    </tr>
                    <tr>
                      <td>Voyage Duration</td>
                      <td className="mono">{routePlan.baseline_route.duration_hours} hrs</td>
                      <td className="mono bold">{routePlan.spill_avoiding_route ? `${routePlan.spill_avoiding_route.duration_hours} hrs` : `${routePlan.baseline_route.duration_hours} hrs`}</td>
                      <td className="mono variance">
                        {routePlan.comparison ? `+${routePlan.comparison.extra_duration_hours} hrs` : '0 hrs'}
                      </td>
                    </tr>
                    <tr>
                      <td>Fuel Consumed</td>
                      <td className="mono">{routePlan.baseline_route.fuel.total_fuel_consumed_mt} MT</td>
                      <td className="mono bold">{routePlan.spill_avoiding_route ? `${routePlan.spill_avoiding_route.fuel.total_fuel_consumed_mt} MT` : `${routePlan.baseline_route.fuel.total_fuel_consumed_mt} MT`}</td>
                      <td className="mono variance">
                        {routePlan.comparison ? `+${routePlan.comparison.extra_fuel_consumed_mt} MT (+${routePlan.comparison.extra_fuel_consumed_pct}%)` : '0 MT'}
                      </td>
                    </tr>
                    <tr>
                      <td>Bunker Fuel Cost</td>
                      <td className="mono">${routePlan.baseline_route.fuel.fuel_cost_usd.toLocaleString(undefined, { maximumFractionDigits: 0 })}</td>
                      <td className="mono bold">{routePlan.spill_avoiding_route ? `$${routePlan.spill_avoiding_route.fuel.fuel_cost_usd.toLocaleString(undefined, { maximumFractionDigits: 0 })}` : `$${routePlan.baseline_route.fuel.fuel_cost_usd.toLocaleString(undefined, { maximumFractionDigits: 0 })}`}</td>
                      <td className="mono variance">
                        {routePlan.comparison ? `+$${routePlan.comparison.extra_cost_usd.toLocaleString(undefined, { maximumFractionDigits: 0 })}` : '$0'}
                      </td>
                    </tr>
                    <tr>
                      <td>CO2 Footprint</td>
                      <td className="mono">{routePlan.baseline_route.fuel.co2_emissions_mt} MT</td>
                      <td className="mono bold">{routePlan.spill_avoiding_route ? `${routePlan.spill_avoiding_route.fuel.co2_emissions_mt} MT` : `${routePlan.baseline_route.fuel.co2_emissions_mt} MT`}</td>
                      <td className="mono variance">
                        {routePlan.comparison ? `+${routePlan.comparison.extra_co2_mt} MT CO2` : '0 MT'}
                      </td>
                    </tr>
                    <tr>
                      <td>Spill Hazard Clearance</td>
                      <td>
                        <span className={`hazard-status ${routePlan.baseline_route.intersects_spill ? 'hazard-status--danger' : 'hazard-status--safe'}`}>
                          {routePlan.baseline_route.intersects_spill ? 'COLLISION HAZARD' : 'Clear'}
                        </span>
                      </td>
                      <td>
                        <span className="hazard-status hazard-status--safe">
                          SAFE ({routePlan.comparison?.safety_buffer_maintained_nm || spillBufferNm} NM)
                        </span>
                      </td>
                      <td className="mono safe-text">
                        Buffer Enforced
                      </td>
                    </tr>
                  </tbody>
                </table>
              </div>

              {/* Fuel Gauge Component */}
              <FuelIndicationMeter
                fuel={routePlan.spill_avoiding_route?.fuel || routePlan.baseline_route.fuel}
                comparison={routePlan.comparison}
              />
            </div>
          )}

          {/* Statutory Advisory Disclaimer */}
          <div className="solas-disclaimer-card">
            <div className="disclaimer-header">
              <svg viewBox="0 0 24 24" fill="none">
                <path d="M12 9v4m0 4h.01M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
              </svg>
              <span>SOLAS & IMO REGULATORY ADVISORY NOTICE</span>
            </div>
            <p className="disclaimer-body">
              {routePlan?.disclaimer || (
                "NOTICE: ADVISORY DECISION SUPPORT ONLY. In compliance with SOLAS Chapter V, Regulation 34 " +
                "and IMO Resolution A.893(21), the Master and Officer of the Watch (OOW) maintain sole statutory " +
                "responsibility for vessel navigation safety, weather routing, and passage execution using official " +
                "certified Electronic Chart Display and Information Systems (ECDIS) and hydrographic nautical charts."
              )}
            </p>
          </div>
        </div>
      </div>
    </div>
  )
}
