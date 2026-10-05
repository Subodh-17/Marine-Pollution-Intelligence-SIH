import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import SatelliteTab from '../components/tabs/SatelliteTab.jsx'
import investigationFixture from './fixtures/investigation.json'

// Part 6 (Lookalike safety) regression coverage: before this banner
// existed, the classifier's own scene-level verdict (Oil / No-Oil /
// Lookalike / Uncertain - see app/satellite/detection.py's
// scene_classification docstring) was visible nowhere on its own; a
// Lookalike or low-confidence Uncertain call was buried inside the
// free-text "Detection notes" list, indistinguishable in the UI from a
// confirmed oil detection. These tests build a scene_classification block
// for each of the four states directly (real API shape) and check the
// resulting badge/label, never relying on any hardcoded string baked into
// the component instead of the real field.
function withSceneClassification(oil_status, extra = {}) {
  return {
    ...investigationFixture,
    satellite: {
      ...investigationFixture.satellite,
      scene_classification: {
        label: { LIKELY_OIL: 'Oil', NO_OIL: 'No-Oil', LOOKALIKE: 'Lookalike', UNCERTAIN: 'Oil' }[oil_status],
        confidence: 0.87,
        probabilities: { Oil: 0.87, 'No-Oil': 0.08, Lookalike: 0.05 },
        oil_status,
        ...extra,
      },
    },
  }
}

describe('SatelliteTab scene classification banner (Part 6, Lookalike safety)', () => {
  it('renders no scene classification banner when the field is absent (classical/fallback results)', () => {
    render(<SatelliteTab investigation={investigationFixture} />)
    expect(screen.queryByText('Oil')).not.toBeInTheDocument()
    expect(screen.queryByText('No-Oil')).not.toBeInTheDocument()
    expect(screen.queryByText('Lookalike')).not.toBeInTheDocument()
    expect(screen.queryByText('Uncertain')).not.toBeInTheDocument()
  })

  it('shows a distinct "Oil" badge for LIKELY_OIL, with its confidence and per-class probabilities', () => {
    render(<SatelliteTab investigation={withSceneClassification('LIKELY_OIL')} />)
    expect(screen.getByText('Oil')).toBeInTheDocument()
    expect(screen.getByText('87.0% confidence')).toBeInTheDocument()
    expect(screen.getByText(/Oil: 87\.0%/)).toBeInTheDocument()
    expect(screen.getByText(/No-Oil: 8\.0%/)).toBeInTheDocument()
    expect(screen.getByText(/Lookalike: 5\.0%/)).toBeInTheDocument()
  })

  it('shows a distinct "No-Oil" badge for NO_OIL', () => {
    render(<SatelliteTab investigation={withSceneClassification('NO_OIL')} />)
    expect(screen.getByText('No-Oil')).toBeInTheDocument()
    expect(screen.queryByText('Oil')).not.toBeInTheDocument()
  })

  it('a Lookalike verdict is shown as its own distinct state and explicitly says it must not be read as confirmed oil', () => {
    render(<SatelliteTab investigation={withSceneClassification('LOOKALIKE')} />)
    expect(screen.getByText('Lookalike')).toBeInTheDocument()
    // The critical safety assertion: a Lookalike call is never silently
    // presented as (or worded like) a confirmed spill.
    expect(screen.getByText(/must not be read as a confirmed spill/i)).toBeInTheDocument()
    expect(screen.queryByText('Oil')).not.toBeInTheDocument()
  })

  it('an Uncertain (below-threshold) verdict is shown as its own distinct state, not silently folded into Oil', () => {
    render(<SatelliteTab investigation={withSceneClassification('UNCERTAIN')} />)
    expect(screen.getByText('Uncertain')).toBeInTheDocument()
    expect(screen.getByText(/unresolved rather than assuming either outcome/i)).toBeInTheDocument()
    // Even though the underlying top-predicted label was "Oil" (see
    // withSceneClassification above - this is the realistic case:
    // low-confidence Oil, not a clean disagreement), the rendered badge
    // must say "Uncertain", never "Oil" - the whole point of oil_status is
    // that confidence gates the badge shown, independent of which raw
    // label scored highest.
    expect(screen.queryByText('Oil')).not.toBeInTheDocument()
  })

  it('applies a visually distinct CSS modifier class per state, so Lookalike/Uncertain are never styled identically to a confirmed Oil call', () => {
    const { container: oilContainer } = render(<SatelliteTab investigation={withSceneClassification('LIKELY_OIL')} />)
    expect(oilContainer.querySelector('.scene-classification-banner--oil')).toBeInTheDocument()

    const { container: lookalikeContainer } = render(<SatelliteTab investigation={withSceneClassification('LOOKALIKE')} />)
    expect(lookalikeContainer.querySelector('.scene-classification-banner--lookalike')).toBeInTheDocument()
    expect(lookalikeContainer.querySelector('.scene-classification-banner--oil')).not.toBeInTheDocument()

    const { container: uncertainContainer } = render(<SatelliteTab investigation={withSceneClassification('UNCERTAIN')} />)
    expect(uncertainContainer.querySelector('.scene-classification-banner--uncertain')).toBeInTheDocument()
  })
})
