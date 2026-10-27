import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import React from 'react'

import ProgressChecklist from '../components/ProgressChecklist.jsx'

describe('Investigation Pipeline Latency & Loading State Optimization', () => {
  describe('ProgressChecklist', () => {
    beforeEach(() => {
      vi.useFakeTimers()
    })

    afterEach(() => {
      vi.useRealTimers()
    })

    it('renders spinner and live elapsed timer immediately when steps array is empty', () => {
      render(
        <ProgressChecklist
          steps={[]}
          revealedCount={0}
        />
      )

      // Active execution indicator should be visible
      expect(screen.getByText(/Running investigation pipeline/i)).toBeInTheDocument()

      // Spinner should be present in the document
      const spinner = document.querySelector('.progress-spinner')
      expect(spinner).toBeInTheDocument()

      // Elapsed timer should show initial zero state
      const elapsedEl = document.querySelector('.progress-elapsed')
      expect(elapsedEl).toBeInTheDocument()
      expect(elapsedEl.textContent).toMatch(/0\.0s/)

      // Placeholder status text
      expect(screen.getByText(/Executing SAR detection, metocean advection & vessel attribution/i)).toBeInTheDocument()
    })

    it('renders string steps as they arrive while preserving the live elapsed timer', () => {
      const mockSteps = [
        'SAR scene loaded',
        'SAR preprocessing completed',
        'Oil slick detected',
      ]

      render(
        <ProgressChecklist
          steps={mockSteps}
          revealedCount={2}
        />
      )

      expect(screen.getByText('SAR scene loaded')).toBeInTheDocument()
      expect(screen.getByText('SAR preprocessing completed')).toBeInTheDocument()
      expect(screen.getByText('Oil slick detected')).toBeInTheDocument()

      const elapsedEl = document.querySelector('.progress-elapsed')
      expect(elapsedEl).toBeInTheDocument()

      // Spinner should not be shown when steps are present
      const spinner = document.querySelector('.progress-spinner')
      expect(spinner).toBeNull()
    })

    it('cleans up interval timer on unmount without throwing errors', () => {
      const clearIntervalSpy = vi.spyOn(window, 'clearInterval')
      const { unmount } = render(
        <ProgressChecklist
          steps={[]}
          revealedCount={0}
        />
      )

      unmount()
      expect(clearIntervalSpy).toHaveBeenCalled()
      clearIntervalSpy.mockRestore()
    })

    it('never displays fabricated progress percentages', () => {
      const { container } = render(
        <ProgressChecklist
          steps={['Step 1', 'Step 2']}
          revealedCount={1}
        />
      )

      // The text content should never contain a '%' sign or fake completion fraction
      const text = container.textContent || ''
      expect(text).not.toContain('%')
    })
  })
})
