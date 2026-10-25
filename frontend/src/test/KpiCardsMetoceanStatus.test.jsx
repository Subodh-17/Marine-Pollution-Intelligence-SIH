import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import KpiCards from '../components/KpiCards.jsx'
import SatelliteTab from '../components/tabs/SatelliteTab.jsx'
import investigationFixture from './fixtures/investigation.json'

describe('KPI Cards & Satellite Tab Metocean Status Integrity (Phase 4.8.39)', () => {
  it('displays Metocean data unavailable when a slick is detected but age estimate is null', () => {
    const inv = JSON.parse(JSON.stringify(investigationFixture))
    inv.satellite.age_estimate = null
    inv.drift.probable_origin = null
    inv.drift.drift_status = 'UNAVAILABLE'
    inv.drift.environment_source = 'UNAVAILABLE'

    render(<KpiCards investigation={inv} />)

    // Value must be N/A
    const cardValues = screen.getAllByText('N/A')
    expect(cardValues.length).toBeGreaterThanOrEqual(2)

    // Must NOT display "Confidence: undefined"
    expect(screen.queryByText(/Confidence: undefined/i)).toBeNull()

    // Must explicitly explain that metocean data is unavailable
    expect(screen.getByText('Metocean data unavailable')).toBeInTheDocument()
    expect(screen.getByText('Drift unavailable (metocean unreachable)')).toBeInTheDocument()
  })

  it('displays No slick detected when no slick geometry exists in scene', () => {
    const inv = JSON.parse(JSON.stringify(investigationFixture))
    inv.satellite.geometry = null
    inv.satellite.age_estimate = null
    inv.drift.probable_origin = null

    render(<KpiCards investigation={inv} />)

    const noSlickSubs = screen.getAllByText('No slick detected')
    expect(noSlickSubs.length).toBe(2)
  })

  it('displays normal age confidence when age is available', () => {
    const inv = JSON.parse(JSON.stringify(investigationFixture))
    inv.satellite.age_estimate = {
      hours: 21.0,
      hours_low: 11.5,
      hours_high: 35.7,
      confidence: 'low',
      caveat: 'Single-scene heuristic',
    }

    render(<KpiCards investigation={inv} />)

    expect(screen.getByText('Confidence: low')).toBeInTheDocument()
    expect(screen.getByText('~21')).toBeInTheDocument()
  })

  it('SatelliteTab distinguishes detected slick from missing metocean data', () => {
    const inv = JSON.parse(JSON.stringify(investigationFixture))
    inv.satellite.age_estimate = null
    inv.satellite.geometry = { area_km2: 1.84, length_km: 2.14, width_km: 0.66, aspect_ratio: 3.25 }

    render(<SatelliteTab investigation={inv} />)

    // Must explain that wind data could not be retrieved
    expect(screen.getByText(/Age estimate unavailable: metocean wind data could not be retrieved/i)).toBeInTheDocument()
    // Must NOT claim no slick was detected when geometry is present
    expect(screen.queryByText(/no slick was detected in this scene/i)).toBeNull()
  })
})
