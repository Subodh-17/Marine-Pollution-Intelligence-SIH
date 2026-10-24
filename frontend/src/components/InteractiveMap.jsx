import React, { useState, useEffect, useRef } from 'react'
import { MapContainer, TileLayer, ImageOverlay, Polygon, Polyline, Circle, CircleMarker, Marker, Popup, useMap } from 'react-leaflet'
import L from 'leaflet'
import 'leaflet/dist/leaflet.css'
import './InteractiveMap.css'
import { getProvenanceInfo } from '../lib/detectorProvenance'

const RANK_COLORS = ['var(--signal-coral)', 'var(--signal-amber)', 'var(--signal-cyan)', 'var(--layer-neutral)']
function colorForRank(i) { return RANK_COLORS[Math.min(i, RANK_COLORS.length - 1)] }

// Public, key-free, OpenStreetMap-compatible raster tile sources, in
// fallback order. Both are standard OSM-data tile servers with no API key
// and no usage-token requirement - "API KEY REQUIRED" was specifically a
// Carto Dark Matter problem, not an OSM-tile-in-general problem. If the
// first provider's tiles fail outright (network egress restrictions,
// provider outage, rate limiting), the map automatically retries with the
// second provider rather than showing a dead grey background - and if both
// fail, the vector overlays (slick polygon, drift tracks, AIS candidate
// tracks) remain fully interactive on their own, since they don't depend on
// raster tiles at all (see the basemap-failure notice below).
//
// Per policy: no API key is ever added here, and the required OSM
// attribution is never hidden via CSS - if you need to change providers,
// keep both of those true.
const TILE_PROVIDERS = [
  {
    id: 'osm',
    url: 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    subdomains: ['a', 'b', 'c'],
  },
  {
    id: 'wikimedia',
    url: 'https://maps.wikimedia.org/osm-intl/{z}/{x}/{y}.png',
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors, tiles by Wikimedia',
    subdomains: [],
  },
]
// How many tile load errors to tolerate from the current provider (with
// zero successful loads) before switching to the next one in the list.
const PROVIDER_ERROR_THRESHOLD = 4

function FitBounds({ bounds }) {
  const map = useMap()
  useEffect(() => {
    if (bounds) map.fitBounds(bounds, { padding: [24, 24] })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [JSON.stringify(bounds)])
  return null
}

function pinIcon(color) {
  return L.divIcon({
    className: '',
    html: `<div style="width:12px;height:12px;border-radius:50%;background:${color};border:2px solid #051019;box-shadow:0 0 0 2px ${color}55"></div>`,
    iconSize: [12, 12],
    iconAnchor: [6, 6],
  })
}

export default function InteractiveMap({ investigation, height = 460, defaultLayers = {}, selectedMmsi, onSelectVessel }) {
  const [layers, setLayers] = useState({
    satellite: false,
    spill: true,
    hindcast: true,
    forecast: true,
    ais: true,
    radius: true,
    ...defaultLayers,
  })
  // Basemap tiles require outbound internet access (see README/FINAL_HANDOFF);
  // in network-restricted environments the tile requests 403/fail outright.
  // The vector overlays below (slick polygon, drift tracks, AIS candidate
  // tracks) don't depend on raster tiles and still render correctly, but a
  // silently-blank map background reads as "broken" rather than "offline" -
  // show a small, dismissable note instead of failing silently.
  const [basemapState, setBasemapState] = useState({ anyLoaded: false, anyError: false })
  const [providerIndex, setProviderIndex] = useState(0)
  // Bug fix: the failover decision below used to live entirely in React
  // state (`providerIndex` read from the enclosing render's closure inside
  // `handleTileError`). When a provider's tiles are completely unreachable
  // (common in network-restricted environments - both configured providers
  // 403 here), many `tileerror` events fire in quick succession, some of
  // them from an already-superseded <TileLayer> instance whose event
  // handler closure still captures the *previous* `providerIndex`. That
  // stale closure's bounds check (`providerIndex < length - 1`) could pass
  // a second time even after the index had already advanced, because the
  // actual increment (`setProviderIndex(i => i + 1)`) applies to the
  // *current* state, not the stale captured value - pushing the index one
  // past the end of `TILE_PROVIDERS` and crashing the whole map on
  // `provider.url` being undefined (`TypeError: Cannot read properties of
  // undefined (reading 'url')`). Refs are read synchronously and always
  // hold the true current value, so gating and clamping off the ref
  // (instead of the stale closed-over state) makes the index mathematically
  // unable to leave the valid range, regardless of how many stale/late
  // error events arrive. `provider` also gets a defensive fallback below as
  // a second, independent safety net.
  const providerIndexRef = useRef(0)
  const errorCountRef = useRef(0)
  const provider = TILE_PROVIDERS[providerIndex] ?? TILE_PROVIDERS[TILE_PROVIDERS.length - 1]
  const allProvidersExhausted = providerIndex >= TILE_PROVIDERS.length - 1 && basemapState.anyError && !basemapState.anyLoaded

  const handleTileLoad = () => {
    setBasemapState(s => (s.anyLoaded ? s : { ...s, anyLoaded: true }))
  }
  const handleTileError = () => {
    setBasemapState(s => (s.anyError ? s : { ...s, anyError: true }))
    errorCountRef.current += 1
    // Only fail over if this provider has produced zero successful loads so
    // far - a provider that's basically working but drops the odd tile
    // shouldn't be swapped out mid-session. `providerIndexRef.current` is
    // always up to date (updated synchronously right below whenever we
    // advance), so this can never fire for an index beyond the last one.
    if (
      errorCountRef.current >= PROVIDER_ERROR_THRESHOLD &&
      !basemapState.anyLoaded &&
      providerIndexRef.current < TILE_PROVIDERS.length - 1
    ) {
      providerIndexRef.current += 1
      errorCountRef.current = 0
      setProviderIndex(providerIndexRef.current)
      setBasemapState({ anyLoaded: false, anyError: false })
    }
  }

  const toggle = (key) => setLayers(l => ({ ...l, [key]: !l[key] }))

  const sat = investigation.satellite
  const geo = sat.geometry
  const drift = investigation.drift
  const candidates = investigation.candidates
  const searchRadiusKm = investigation.ais_summary.search_radius_km
  const provenanceInfo = getProvenanceInfo(sat.detector_provenance)

  const sceneBounds = sat.scene_bounds
    ? [[sat.scene_bounds.south, sat.scene_bounds.west], [sat.scene_bounds.north, sat.scene_bounds.east]]
    : null

  const origin = drift.probable_origin
  const observed = drift.observed

  // No slick was detected in this scene, so there is no origin/observed
  // point to center a map on — show a clear placeholder instead of
  // crashing on `origin.lat` / `observed.lat`.
  if (!observed) {
    return (
      <div className="map-wrap" style={{ height, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <p style={{ color: 'var(--text-muted)', fontSize: 13, textAlign: 'center', maxWidth: 340, padding: 'var(--sp-4)' }}>
          No slick was detected in this scene, so there is no estimated origin to map.
          {sceneBounds && geo === null ? ' See the Satellite tab for detector notes.' : ''}
        </p>
      </div>
    )
  }

  const mapCenter = origin ? [origin.lat, origin.lon] : [observed.lat, observed.lon]
  const backwardLine = origin ? [...drift.backward_track, observed].map(p => [p.lat, p.lon]) : []
  const forwardLine = drift.forward_track.length ? [observed, ...drift.forward_track].map(p => [p.lat, p.lon]) : []

  const fitBounds = sceneBounds || (geo ? [[geo.centroid_lat - 0.3, geo.centroid_lon - 0.3], [geo.centroid_lat + 0.3, geo.centroid_lon + 0.3]] : null)

  return (
    <div className="map-wrap" style={{ height }}>
      <MapContainer center={mapCenter} zoom={10} style={{ height: '100%', width: '100%' }} scrollWheelZoom={true}>
        <TileLayer
          key={provider.id}
          url={provider.url}
          attribution={provider.attribution}
          subdomains={provider.subdomains}
          eventHandlers={{
            tileload: handleTileLoad,
            tileerror: handleTileError,
          }}
        />
        {fitBounds && <FitBounds bounds={fitBounds} />}

        {layers.satellite && sceneBounds && (
          <ImageOverlay url={sat.original_image} bounds={sceneBounds} opacity={0.85} />
        )}

        {layers.spill && geo && geo.polygon_latlon.length > 2 && (
          <Polygon
            positions={geo.polygon_latlon}
            pathOptions={{ color: 'var(--signal-coral)', weight: 1.5, fillOpacity: 0.35 }}
          >
            <Popup>
              <b>Detected slick</b><br />
              Area: {geo.area_km2} km²<br />
              {provenanceInfo.confidenceSub}: {(sat.confidence * 100).toFixed(1)}%
            </Popup>
          </Polygon>
        )}
        {layers.spill && geo && (
          <CircleMarker center={[geo.centroid_lat, geo.centroid_lon]} radius={4} pathOptions={{ color: 'var(--signal-coral)', fillOpacity: 1 }}>
            <Popup>Slick centroid</Popup>
          </CircleMarker>
        )}

        {layers.hindcast && origin && (
          <>
            <Polyline positions={backwardLine} pathOptions={{ color: 'var(--signal-amber)', weight: 2, dashArray: '5 5' }} />
            {drift.backward_track.map((p, i) => (
              <Circle key={i} center={[p.lat, p.lon]} radius={p.uncertainty_km * 1000}
                pathOptions={{ color: 'var(--signal-amber)', weight: 1, fillOpacity: 0.04, opacity: 0.35 }} />
            ))}
            <Marker position={[origin.lat, origin.lon]} icon={pinIcon('var(--signal-amber)')}>
              <Popup>
                <b>Probable origin</b><br />
                {origin.timestamp}<br />
                Confidence: {drift.origin_confidence}
              </Popup>
            </Marker>
          </>
        )}

        {layers.forecast && forwardLine.length > 1 && (
          <>
            <Polyline positions={forwardLine} pathOptions={{ color: 'var(--signal-violet)', weight: 2, dashArray: '2 6' }} />
            {drift.forward_track.map((p, i) => (
              <Circle key={i} center={[p.lat, p.lon]} radius={p.uncertainty_km * 1000}
                pathOptions={{ color: 'var(--signal-violet)', weight: 1, fillOpacity: 0.04, opacity: 0.3 }} />
            ))}
          </>
        )}

        <CircleMarker center={[observed.lat, observed.lon]} radius={5} pathOptions={{ color: 'var(--signal-cyan)', fillOpacity: 1 }}>
          <Popup><b>Observed slick position</b><br />{observed.timestamp}</Popup>
        </CircleMarker>

        {layers.radius && (
          <Circle center={origin ? [origin.lat, origin.lon] : [observed.lat, observed.lon]} radius={searchRadiusKm * 1000}
            pathOptions={{ color: 'var(--text-muted)', weight: 1, dashArray: '4 4', fillOpacity: 0.02 }} />
        )}

        {layers.ais && candidates.map((c, i) => {
          const line = c.trajectory_points.map(p => [p.lat, p.lon])
          const color = colorForRank(i)
          const isSelected = selectedMmsi === c.mmsi
          return (
            <React.Fragment key={c.mmsi}>
              <Polyline
                positions={line}
                pathOptions={{ color, weight: isSelected ? 4 : 2, opacity: isSelected ? 1 : 0.75 }}
                eventHandlers={{ click: () => onSelectVessel && onSelectVessel(c.mmsi) }}
              />
              {line.length > 0 && (
                <Marker position={line[line.length - 1]} icon={pinIcon(color)}
                  eventHandlers={{ click: () => onSelectVessel && onSelectVessel(c.mmsi) }}>
                  <Popup>
                    <b>#{i + 1} {c.vessel_name || c.mmsi}</b><br />
                    MMSI: {c.mmsi}<br />
                    Score: {c.final_score}/100<br />
                    Distance to origin: {c.min_distance_km} km
                  </Popup>
                </Marker>
              )}
            </React.Fragment>
          )
        })}
      </MapContainer>

      {allProvidersExhausted && (
        <div className="map-basemap-notice">
          Basemap tiles unavailable from all configured providers (no outbound connection to any
          tile server) — slick, drift, and AIS overlays below are still real, computed data and
          remain fully usable.
        </div>
      )}

      <div className="map-legend">
        <h4>Layers</h4>
        <label><input type="checkbox" checked={layers.satellite} onChange={() => toggle('satellite')} /><span className="map-legend__swatch" style={{ background: 'var(--layer-neutral)' }} />SAR scene</label>
        <label><input type="checkbox" checked={layers.spill} onChange={() => toggle('spill')} /><span className="map-legend__swatch" style={{ background: 'var(--signal-coral)' }} />Slick + centroid</label>
        <label><input type="checkbox" checked={layers.hindcast} onChange={() => toggle('hindcast')} /><span className="map-legend__swatch" style={{ background: 'var(--signal-amber)' }} />Hindcast + origin</label>
        <label><input type="checkbox" checked={layers.forecast} onChange={() => toggle('forecast')} /><span className="map-legend__swatch" style={{ background: 'var(--signal-violet)' }} />Forecast</label>
        <label><input type="checkbox" checked={layers.ais} onChange={() => toggle('ais')} /><span className="map-legend__swatch" style={{ background: 'var(--signal-cyan)' }} />AIS candidates</label>
        <label><input type="checkbox" checked={layers.radius} onChange={() => toggle('radius')} /><span className="map-legend__swatch" style={{ background: 'var(--text-muted)' }} />Search radius circle</label>
      </div>
    </div>
  )
}
