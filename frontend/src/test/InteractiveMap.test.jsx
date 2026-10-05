import { describe, it, expect, vi } from 'vitest'
import { render, screen, act } from '@testing-library/react'
import investigationFixture from './fixtures/investigation.json'

// Regression test for a real bug found via a live browser run of the demo
// investigation: when the configured basemap tile providers are both
// unreachable (as they are in any network-restricted environment, and were
// in the environment this bug was found in), react-leaflet fires many
// `tileerror` events in quick succession, some of them from a <TileLayer>
// instance that has already been superseded by the provider-failover logic
// switching to the next provider. The old instance's event handler closure
// still captures the *previous* `providerIndex`. Before the fix, the
// failover guard read that stale closed-over value instead of the true
// current index, so a late/stale error event could push the index one past
// the end of the `TILE_PROVIDERS` array — `TILE_PROVIDERS[2]` is
// `undefined` for a 2-entry array — crashing the whole Overview dashboard
// with "Cannot read properties of undefined (reading 'url')".
//
// The shared `mockLeaflet.js` used by the smoke tests stubs `TileLayer`
// down to a plain `<div>` and drops `eventHandlers` entirely (see its own
// comment for why), which is exactly why this bug was invisible to the
// existing suite. This test uses its own, slightly richer mock that
// captures the `url` and `eventHandlers` passed on every single render, so
// we can (a) assert `url` is never undefined - the literal symptom of the
// crash - across an arbitrarily long, adversarial sequence of tileerror
// events, and (b) keep a handle to the very first render's `eventHandlers`
// to replay "a stale, already-superseded TileLayer instance fires again"
// after failover has already happened, which is what a real browser does
// with in-flight image error events that were queued before React tore the
// old <img> down.
const capturedRenders = []

vi.mock('react-leaflet', () => {
  const React = require('react')
  const Passthrough = ({ children }) => React.createElement('div', null, children)
  const TileLayerStub = (props) => {
    capturedRenders.push({ url: props.url, eventHandlers: props.eventHandlers })
    return React.createElement('div', { 'data-testid': 'tile-layer' })
  }
  return {
    MapContainer: Passthrough,
    TileLayer: TileLayerStub,
    ImageOverlay: () => null,
    Polygon: Passthrough,
    Polyline: () => null,
    Circle: () => null,
    CircleMarker: Passthrough,
    Marker: Passthrough,
    Popup: Passthrough,
    useMap: () => ({ fitBounds: vi.fn() }),
  }
})

vi.mock('leaflet', () => ({
  default: { divIcon: vi.fn(() => ({})) },
}))

// Imported after the mocks above so InteractiveMap picks them up.
const { default: InteractiveMap } = await import('../components/InteractiveMap.jsx')

const KNOWN_PROVIDER_URLS = [
  'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',
  'https://maps.wikimedia.org/osm-intl/{z}/{x}/{y}.png',
]

describe('InteractiveMap basemap tile failover', () => {
  it('never renders TileLayer with an undefined url, even when a stale (superseded) instance keeps firing tileerror after failover already happened', async () => {
    capturedRenders.length = 0
    render(<InteractiveMap investigation={investigationFixture} />)

    expect(capturedRenders.length).toBe(1)
    expect(capturedRenders[0].url).toBe(KNOWN_PROVIDER_URLS[0])
    // Keep the very first render's handlers - this is the "stale" instance
    // we'll keep calling even after the component has already moved on.
    const staleFirstInstanceHandlers = capturedRenders[0].eventHandlers

    // Fail it out 4 times (PROVIDER_ERROR_THRESHOLD) - this should trigger
    // exactly one failover to provider 1 ("wikimedia").
    act(() => {
      for (let i = 0; i < 4; i++) {
        staleFirstInstanceHandlers.tileerror()
      }
    })
    const latestUrlAfterFirstFailover = capturedRenders[capturedRenders.length - 1].url
    expect(latestUrlAfterFirstFailover).toBe(KNOWN_PROVIDER_URLS[1])

    // Now replay the real-world race this bug was found from: the OLD,
    // superseded first instance's handler (a stale closure from before the
    // switch) fires many more times, exactly as a browser does for
    // in-flight image error events queued before React tore the old <img>
    // down. Before the fix, this could push providerIndex to an
    // out-of-bounds 2, rendering TileLayer with `url: undefined`.
    expect(() => {
      act(() => {
        for (let i = 0; i < 20; i++) {
          staleFirstInstanceHandlers.tileerror()
        }
      })
    }).not.toThrow()

    // Across every single render that happened - including all the ones
    // triggered by the stale/duplicate events above - url must always be
    // one of the two real, configured provider URLs. Never undefined.
    for (const r of capturedRenders) {
      expect(r.url).toBeDefined()
      expect(KNOWN_PROVIDER_URLS).toContain(r.url)
    }

    // With only 2 configured providers, the second (last) one is the most
    // any amount of stale/duplicate error traffic should ever reach.
    expect(capturedRenders[capturedRenders.length - 1].url).toBe(KNOWN_PROVIDER_URLS[1])

    // And the component must still be rendering real content, not the
    // ErrorBoundary's fallback UI.
    expect(screen.queryByText(/couldn't be rendered/i)).not.toBeInTheDocument()
  })
})
