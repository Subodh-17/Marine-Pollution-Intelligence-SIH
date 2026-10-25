const DOT_CLASS = (val) => {
  if (!val) return 'synthetic'
  const v = val.toUpperCase()
  if (v.includes('UNAVAILABLE') || v.includes('NOT_RUN') || v.includes('NOT RUN')) return 'synthetic'
  if (v.includes('PARTIAL')) return 'cached'
  if (v.includes('FROM FILE')) return 'real'
  if (v.includes('ASSUMED')) return 'synthetic'
  if (v.includes('SYNTHETIC') || v.includes('DEMO')) return 'synthetic'
  if (v.includes('CACHED')) return 'cached'
  if (v.includes('REAL')) return 'real'
  return 'synthetic'
}

// Known raw provenance-tag fragments -> a short, professional display
// label. This only changes what's shown as the chip's bold text - both
// DOT_CLASS (the color, computed from the raw val above) and the chip's
// `title` tooltip (the full, unabbreviated raw string, unchanged) still
// see the original value, so the underlying truthfulness signal
// (synthetic vs cached vs live, fallback vs not) is never lost or
// hidden - this only removes the ALL_CAPS_ENUM styling from the one
// line most people will actually read at a glance. Full technical
// provenance remains available via the tooltip and in investigation
// reports/exports (see app/reports/generator.py), never removed.
const TAG_LABELS = {
  SYNTHETIC_DEMO: 'Illustrative Scenario',
  USER_UPLOADED_CSV: 'Uploaded CSV',
  USER_UPLOADED_REAL_IMAGE: 'Satellite Observation',
  LIVE_AIS: 'Live Feed',
  LIVE_AIS_UNAVAILABLE_FALLBACK_SYNTHETIC: 'Live Feed Unavailable — Demo Fleet Used',
  REAL: 'Live Feed',
  REAL_OPEN_METEO: 'Open-Meteo (Real CMEMS/GFS)',
  REAL_MET_NORWAY: 'MET Norway (Real ECMWF Wind)',
  PARTIAL_REAL: 'Partial Real (Wind Only)',
  PARTIAL_WIND_ONLY: 'Partial Real (Wind Only)',
  CACHED: 'Cached Feed',
  LOCAL_SAMPLE: 'Local Sample Data',
  UNAVAILABLE: 'Data Unavailable',
  NOT_RUN: 'Not Run',
}

// ALL_CAPS_WITH_UNDERSCORES -> Title Case, for any tag not covered by
// TAG_LABELS above (keeps this forward-compatible with a new backend
// provenance value instead of silently showing nothing for it).
function prettifyTag(tag) {
  return tag.toLowerCase().split('_').map((w) => w[0].toUpperCase() + w.slice(1)).join(' ')
}

const DISPLAY_VALUE = (val) => {
  if (!val) return val
  // Chained provenance strings (e.g. "MarineCadastre AIS (reference schema)
  // -> LIVE_AIS_UNAVAILABLE_FALLBACK_SYNTHETIC") put the actually-relevant
  // status at the END, which a naive front-truncation would hide. Prefer
  // showing the tail of the last "->" segment; the full string is always
  // still available via the chip's `title` tooltip.
  const arrowIdx = val.lastIndexOf('->')
  const tail = arrowIdx >= 0 ? val.slice(arrowIdx + 2).trim() : val
  // A raw backend tag is ALL_CAPS_SNAKE_CASE; anything with lowercase
  // letters is already hand-written prose (e.g. detection_method's
  // descriptions) and is shown as-is, just truncated.
  const isRawTag = /^[A-Z][A-Z0-9_]*$/.test(tail)
  const shown = isRawTag ? (TAG_LABELS[tail] || prettifyTag(tail)) : tail
  return shown.length > 42 ? shown.slice(0, 42) + '…' : shown
}

export default function DataSourceIndicator({ dataSources, geolocationSource, geolocationAssumed }) {
  if (!dataSources) return null
  const entries = [
    ['Satellite', dataSources.satellite],
    ['AIS', dataSources.ais],
    ['Environment', dataSources.environment],
    ['Detection', dataSources.detection_method],
    ['Drift', dataSources.drift_method],
    ['Attribution', dataSources.attribution_method],
  ]
  if (geolocationSource) {
    entries.splice(1, 0, [
      'Geolocation',
      geolocationSource + (geolocationAssumed ? ' (assumed)' : ' (from file)'),
    ])
  }
  return (
    <div className="source-strip" role="list" aria-label="Data source indicator">
      {entries.map(([label, val]) => (
        <div className="source-chip" role="listitem" key={label} title={val}>
          <span className={`source-chip__dot ${DOT_CLASS(val)}`} />
          {label}: <b>{DISPLAY_VALUE(val)}</b>
        </div>
      ))}
    </div>
  )
}
