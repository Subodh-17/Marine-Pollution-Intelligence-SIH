import { vi } from 'vitest'

// Stub every react-leaflet export our components use with a trivial
// pass-through so we can smoke-test OUR data-flow logic (prop shaping,
// conditional rendering, list construction) without fighting Leaflet's
// real-browser DOM requirements (canvas sizing, pointer events, tile
// loading) inside jsdom — those are third-party concerns with their own
// upstream test suite, not something this smoke test needs to re-prove.
vi.mock('react-leaflet', () => {
  // Props that real react-leaflet components consume internally and never
  // forward to the underlying DOM node. Our stub renders a plain <div>, so
  // spreading these through would produce "React does not recognize the
  // `x` prop" console warnings that don't reflect any real production
  // behaviour (the real library never does this). Strip them here instead.
  const NON_DOM_PROPS = new Set([
    'eventHandlers', 'pathOptions', 'scrollWheelZoom', 'positions', 'center',
    'bounds', 'attribution', 'url', 'radius', 'zoom', 'zoomControl',
  ])
  const Stub = ({ children, ...props }) => {
    const React = require('react')
    const domProps = Object.fromEntries(
      Object.entries(props).filter(([key]) => !NON_DOM_PROPS.has(key))
    )
    return React.createElement('div', { 'data-testid': 'leaflet-stub', ...domProps }, children)
  }
  return {
    MapContainer: Stub,
    TileLayer: Stub,
    ImageOverlay: Stub,
    Polygon: Stub,
    Polyline: Stub,
    Circle: Stub,
    CircleMarker: Stub,
    Marker: Stub,
    Popup: Stub,
    useMap: () => ({ fitBounds: vi.fn() }),
    useMapEvents: () => ({}),
  }
})

vi.mock('leaflet', () => ({
  default: {
    divIcon: vi.fn(() => ({})),
    latLngBounds: vi.fn(() => ({})),
  },
}))
