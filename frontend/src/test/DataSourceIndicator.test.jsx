import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import DataSourceIndicator from '../components/DataSourceIndicator'

describe('DataSourceIndicator', () => {
  const baseSources = {
    satellite: 'Synthetic offline demo scene',
    ais: 'MarineCadastre AIS (reference schema) -> SYNTHETIC_DEMO',
    environment: 'SYNTHETIC_DEMO',
    detection_method: 'Classical SAR Detection',
    drift_method: 'Backward/forward Lagrangian drift',
    attribution_method: 'Weighted multi-factor scoring',
  }

  it('renders nothing when dataSources is missing', () => {
    const { container } = render(<DataSourceIndicator dataSources={null} />)
    expect(container.firstChild).toBeNull()
  })

  it('does not show a Geolocation chip when geolocationSource is not provided', () => {
    render(<DataSourceIndicator dataSources={baseSources} />)
    expect(screen.queryByText(/Geolocation/)).not.toBeInTheDocument()
  })

  it('shows an assumed Geolocation chip for a scene with no embedded geo tags', () => {
    render(
      <DataSourceIndicator
        dataSources={baseSources}
        geolocationSource="assumed_center_coordinates"
        geolocationAssumed={true}
      />
    )
    expect(screen.getByText(/Geolocation/)).toBeInTheDocument()
    const chip = screen.getByTitle('assumed_center_coordinates (assumed)')
    expect(chip).toBeInTheDocument()
  })

  it('shows a real Geolocation chip for a scene with embedded GeoTIFF metadata', () => {
    render(
      <DataSourceIndicator
        dataSources={baseSources}
        geolocationSource="embedded_geotiff_metadata"
        geolocationAssumed={false}
      />
    )
    const chip = screen.getByTitle('embedded_geotiff_metadata (from file)')
    expect(chip).toBeInTheDocument()
    expect(chip.querySelector('.source-chip__dot')).toHaveClass('real')
  })

  it('renders the live-AIS-fallback reason when present, not a silent synthetic label', () => {
    const aisValue = 'MarineCadastre AIS (reference schema) -> LIVE_AIS_UNAVAILABLE_FALLBACK_SYNTHETIC'
    render(
      <DataSourceIndicator
        dataSources={{ ...baseSources, ais: aisValue }}
      />
    )
    const chip = screen.getByTitle(aisValue)
    expect(chip).toBeInTheDocument()
    // Visible text is a polished label, not the raw enum string - but it
    // must still say plainly that live AIS was unavailable and a demo
    // fleet was used instead, and the full raw value must still be
    // present (via the tooltip, asserted above) rather than hidden.
    expect(chip.textContent).toContain('Live Feed Unavailable')
    expect(chip.textContent).toContain('Demo Fleet')
  })

  it('shows a polished label instead of a raw enum tag, while keeping the raw value in the tooltip', () => {
    render(<DataSourceIndicator dataSources={baseSources} />)
    // environment: 'SYNTHETIC_DEMO' (a bare raw tag, no "->") should render
    // as the mapped label, not the raw SNAKE_CASE string.
    const chip = screen.getByTitle('SYNTHETIC_DEMO')
    expect(chip).toBeInTheDocument()
    expect(chip.textContent).toContain('Illustrative Scenario')
    expect(chip.textContent).not.toContain('SYNTHETIC_DEMO')
  })

  it('falls back to a Title Case rendering for an unmapped raw tag rather than hiding it', () => {
    render(
      <DataSourceIndicator dataSources={{ ...baseSources, environment: 'SOME_NEW_TAG' }} />
    )
    const chip = screen.getByTitle('SOME_NEW_TAG')
    expect(chip.textContent).toContain('Some New Tag')
  })
})
