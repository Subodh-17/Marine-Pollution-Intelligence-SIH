import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import ClassificationScorecard from '../components/ClassificationScorecard.jsx'

describe('ClassificationScorecard component', () => {
  const classicalOilFixture = {
    label: 'Oil',
    confidence: 0.7375,
    oil_status: 'LIKELY_OIL',
    scores: {
      Oil: 0.7375,
      Lookalike: 0.1748,
      'No Oil': 0.0877,
    },
    probabilities: {
      Oil: 0.7375,
      Lookalike: 0.1748,
      'No-Oil': 0.0877,
    },
    score_type: 'heuristic_score',
    score_type_label: 'Heuristic score',
    detector_name: 'Classical Detector',
    reasoning: 'Candidate exhibits significant capillary wave damping (texture score 0.67), wind-driven elongation (5.55:1), and coherent slick footprint (2402px).',
  }

  const mlLookalikeFixture = {
    label: 'Lookalike',
    confidence: 0.824,
    oil_status: 'LOOKALIKE',
    scores: {
      Oil: 0.091,
      Lookalike: 0.824,
      'No Oil': 0.085,
    },
    probabilities: {
      Oil: 0.091,
      Lookalike: 0.824,
      'No-Oil': 0.085,
    },
    score_type: 'probability',
    score_type_label: 'Model probability',
    detector_name: 'ML Detector (Zenodo-Trained)',
    reasoning: 'High classifier confidence for natural lookalike feature.',
  }

  const noOilFixture = {
    label: 'No Oil',
    confidence: 0.942,
    oil_status: 'NO_OIL',
    scores: {
      Oil: 0.015,
      Lookalike: 0.043,
      'No Oil': 0.942,
    },
    probabilities: {
      Oil: 0.015,
      Lookalike: 0.043,
      'No-Oil': 0.942,
    },
    score_type: 'heuristic_score',
    score_type_label: 'Heuristic score',
    detector_name: 'Classical Detector',
    reasoning: 'Adaptive thresholding detected no dark candidate regions above minimum area threshold.',
  }

  it('renders null gracefully when sceneClassification is null or undefined', () => {
    const { container: c1 } = render(<ClassificationScorecard sceneClassification={null} />)
    expect(c1.firstChild).toBeNull()

    const { container: c2 } = render(<ClassificationScorecard sceneClassification={undefined} />)
    expect(c2.firstChild).toBeNull()
  })

  it('renders Classical Detector output with badge, all 3 scores, heuristic label, and winner', () => {
    const { container } = render(
      <ClassificationScorecard sceneClassification={classicalOilFixture} />
    )

    // Detector name and score type
    expect(screen.getByText('Classical Detector')).toBeInTheDocument()
    expect(screen.getByText('Heuristic score')).toBeInTheDocument()

    // Verdict badge and confidence
    expect(screen.getByText('Oil')).toBeInTheDocument()
    expect(screen.getByText('73.8% confidence')).toBeInTheDocument()

    // 3 Category bars and percentages
    expect(screen.getByText('Oil:')).toBeInTheDocument()
    expect(screen.getByText('73.8%')).toBeInTheDocument()

    expect(screen.getByText('Lookalike:')).toBeInTheDocument()
    expect(screen.getByText('17.5%')).toBeInTheDocument()

    expect(screen.getByText('No Oil:')).toBeInTheDocument()
    expect(screen.getByText('8.8%')).toBeInTheDocument()

    // Winning class highlight pill
    expect(screen.getByText('WINNING CLASS')).toBeInTheDocument()

    // Contextual note / reasoning
    expect(screen.getByText(/capillary wave damping/i)).toBeInTheDocument()

    // Banner styling modifier
    expect(container.querySelector('.scene-classification-banner--oil')).toBeInTheDocument()
  })

  it('renders ML Detector output with model probability label, detector name, and probabilities', () => {
    const { container } = render(
      <ClassificationScorecard sceneClassification={mlLookalikeFixture} />
    )

    expect(screen.getByText('ML Detector (Zenodo-Trained)')).toBeInTheDocument()
    expect(screen.getByText('Model probability')).toBeInTheDocument()

    expect(screen.getByText('Lookalike')).toBeInTheDocument()
    expect(screen.getByText('82.4% confidence')).toBeInTheDocument()

    expect(screen.getByText('Lookalike:')).toBeInTheDocument()
    expect(screen.getByText('82.4%')).toBeInTheDocument()

    expect(screen.getByText('WINNING CLASS')).toBeInTheDocument()
    expect(container.querySelector('.scene-classification-banner--lookalike')).toBeInTheDocument()
  })

  it('renders No-Oil verdict correctly with winning class highlighted and green styling', () => {
    const { container } = render(
      <ClassificationScorecard sceneClassification={noOilFixture} />
    )

    expect(screen.getByText('No-Oil')).toBeInTheDocument()
    expect(screen.getByText('94.2% confidence')).toBeInTheDocument()

    expect(screen.getByText('No Oil:')).toBeInTheDocument()
    expect(screen.getByText('94.2%')).toBeInTheDocument()

    expect(screen.getByText('WINNING CLASS')).toBeInTheDocument()
    expect(container.querySelector('.scene-classification-banner--no-oil')).toBeInTheDocument()
  })

  it('renders ML fallback detector name when provided', () => {
    const fallbackFixture = {
      ...classicalOilFixture,
      detector_name: 'Classical Detector (ML Fallback)',
    }
    render(<ClassificationScorecard sceneClassification={fallbackFixture} />)

    expect(screen.getByText('Classical Detector (ML Fallback)')).toBeInTheDocument()
    expect(screen.getByText('Heuristic score')).toBeInTheDocument()
  })

  it('handles empty or partial classification object gracefully without crashing', () => {
    const partialFixture = {
      label: 'Uncertain',
      confidence: 0,
      oil_status: 'UNCERTAIN',
    }
    const { container } = render(
      <ClassificationScorecard sceneClassification={partialFixture} />
    )

    expect(screen.getByText('Uncertain')).toBeInTheDocument()
    expect(screen.getByText('0.0% confidence')).toBeInTheDocument()
    expect(screen.getByText('Oil:')).toBeInTheDocument()
    expect(screen.getByText('Lookalike:')).toBeInTheDocument()
    expect(screen.getByText('No Oil:')).toBeInTheDocument()
    expect(container.querySelector('.scene-classification-banner--uncertain')).toBeInTheDocument()
  })

  it('falls back to probabilities dictionary if scores key is absent', () => {
    const legacyFixture = {
      label: 'Oil',
      confidence: 0.85,
      oil_status: 'LIKELY_OIL',
      probabilities: {
        Oil: 0.85,
        Lookalike: 0.10,
        'No-Oil': 0.05,
      },
    }
    render(<ClassificationScorecard sceneClassification={legacyFixture} />)

    expect(screen.getByText('Oil:')).toBeInTheDocument()
    expect(screen.getByText('85.0%')).toBeInTheDocument()
    expect(screen.getByText('Lookalike:')).toBeInTheDocument()
    expect(screen.getByText('10.0%')).toBeInTheDocument()
    expect(screen.getByText('No Oil:')).toBeInTheDocument()
    expect(screen.getByText('5.0%')).toBeInTheDocument()
  })

  it('exposes accessible ARIA region and progressbar attributes', () => {
    render(
      <ClassificationScorecard sceneClassification={classicalOilFixture} />
    )

    const region = screen.getByRole('region', { name: /scene classification scorecard/i })
    expect(region).toBeInTheDocument()

    const progressbars = screen.getAllByRole('progressbar')
    expect(progressbars.length).toBe(3)
    expect(progressbars[0]).toHaveAttribute('aria-valuenow', '73.8')
    expect(progressbars[0]).toHaveAttribute('aria-label', 'Oil score: 73.8%')
  })

  it('handles non-finite NaN values safely without rendering NaN%', () => {
    const nanFixture = {
      label: 'Oil',
      confidence: NaN,
      oil_status: 'LIKELY_OIL',
      scores: {
        Oil: NaN,
        Lookalike: NaN,
        'No Oil': NaN,
      },
      probabilities: {
        Oil: NaN,
      },
    }
    render(<ClassificationScorecard sceneClassification={nanFixture} />)

    expect(screen.getByText('0.0% confidence')).toBeInTheDocument()
    expect(screen.queryByText(/NaN/)).not.toBeInTheDocument()
  })
})
