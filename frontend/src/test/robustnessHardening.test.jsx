import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react'
import React from 'react'

vi.mock('../api', () => ({
  default: {
    getCommandCenterStats: vi.fn(() => Promise.resolve({
      scenes_processed: 42,
      active_incidents: 2,
      vessels_monitored: 120,
    })),
  },
}))

// Import components to test
import UploadAisModal from '../components/UploadAisModal.jsx'
import UploadSatelliteModal from '../components/UploadSatelliteModal.jsx'
import VesselDetailDrawer from '../components/VesselDetailDrawer.jsx'
import DashboardTab from '../components/tabs/DashboardTab.jsx'
import investigationFixture from './fixtures/investigation.json'

describe('Robustness Hardening - Frontend Components', () => {
  describe('UploadAisModal', () => {
    it('prevents duplicate submissions on rapid double-clicks (synchronous re-entrancy guard)', async () => {
      let resolveSubmit
      const submitPromise = new Promise(resolve => { resolveSubmit = resolve })
      const onSubmit = vi.fn(() => submitPromise)
      const onClose = vi.fn()

      render(<UploadAisModal onClose={onClose} onSubmit={onSubmit} />)

      const file = new File(['mmsi,timestamp,lat,lon\n123,2024-01-01T00:00:00,19,71'], 'test.csv', { type: 'text/csv' })
      const fileInput = screen.getByRole('dialog').querySelector('input[type="file"]')
      act(() => {
        fireEvent.change(fileInput, { target: { files: [file] } })
      })

      const submitBtn = screen.getByText('Upload & run')

      // Double click rapidly
      act(() => {
        fireEvent.click(submitBtn)
        fireEvent.click(submitBtn)
      })

      expect(onSubmit).toHaveBeenCalledTimes(1)
      expect(onSubmit).toHaveBeenCalledWith(file)

      // Resolve in-flight submit
      act(() => {
        resolveSubmit({ status: 'uploaded' })
      })
      await waitFor(() => {
        expect(screen.getByText('Upload & run')).toBeInTheDocument()
      })
    })
  })

  describe('UploadSatelliteModal', () => {
    it('safely handles cleared/empty lat/lon inputs by defaulting to fallback coordinates instead of NaN', async () => {
      const onSubmit = vi.fn(() => Promise.resolve())
      const onClose = vi.fn()

      render(<UploadSatelliteModal onClose={onClose} onSubmit={onSubmit} />)

      const file = new File(['dummy content'], 'scene.png', { type: 'image/png' })
      const fileInput = screen.getByRole('dialog').querySelector('input[type="file"]')
      act(() => {
        fireEvent.change(fileInput, { target: { files: [file] } })
      })

      const latInput = screen.getByLabelText(/Center lat/i)
      const lonInput = screen.getByLabelText(/Center lon/i)

      // Clear the inputs (simulate backspace)
      act(() => {
        fireEvent.change(latInput, { target: { value: '' } })
        fireEvent.change(lonInput, { target: { value: '' } })
      })

      const submitBtn = screen.getByText('Upload & run')
      act(() => {
        fireEvent.click(submitBtn)
      })

      expect(onSubmit).toHaveBeenCalledTimes(1)
      const submittedParams = onSubmit.mock.calls[0][1]
      expect(Number.isFinite(submittedParams.centerLat)).toBe(true)
      expect(Number.isFinite(submittedParams.centerLon)).toBe(true)
      expect(submittedParams.centerLat).toBe(19.05)
      expect(submittedParams.centerLon).toBe(71.20)
    })

    it('prevents duplicate submissions on rapid double-clicks (synchronous re-entrancy guard)', async () => {
      let resolveSubmit
      const submitPromise = new Promise(resolve => { resolveSubmit = resolve })
      const onSubmit = vi.fn(() => submitPromise)
      const onClose = vi.fn()

      render(<UploadSatelliteModal onClose={onClose} onSubmit={onSubmit} />)

      const file = new File(['dummy content'], 'scene.png', { type: 'image/png' })
      const fileInput = screen.getByRole('dialog').querySelector('input[type="file"]')
      act(() => {
        fireEvent.change(fileInput, { target: { files: [file] } })
      })

      const submitBtn = screen.getByText('Upload & run')

      // Rapid double click
      act(() => {
        fireEvent.click(submitBtn)
        fireEvent.click(submitBtn)
      })

      expect(onSubmit).toHaveBeenCalledTimes(1)
      act(() => {
        resolveSubmit()
      })
    })
  })

  describe('VesselDetailDrawer', () => {
    it('safely renders when closest_approach_time is invalid or missing without throwing RangeError', () => {
      const inv = {
        ...investigationFixture,
        candidates: [
          {
            ...investigationFixture.candidates[0],
            closest_approach_time: 'not-a-valid-date-timestamp',
          },
        ],
      }

      render(
        <VesselDetailDrawer
          investigation={inv}
          mmsi={inv.candidates[0].mmsi}
          rank={0}
          onClose={() => {}}
        />
      )

      expect(screen.getByText('Trajectory summary')).toBeInTheDocument()
      expect(screen.getByText('not-a-valid-date-timestamp')).toBeInTheDocument()
    })

    it('safely handles missing evidence_summary, breakdown, or trajectory_points arrays', () => {
      const inv = {
        candidates: [
          {
            mmsi: '999999999',
            vessel_name: 'Defensive Vessel',
            final_score: 88,
            confidence_level: 'HIGH',
            evidence_summary: null,
            breakdown: null,
            min_distance_km: 1.2,
            closest_approach_time: null,
            time_delta_hours: 0.5,
            trajectory_points: null,
            data_completeness_pct: 100,
          },
        ],
      }

      render(
        <VesselDetailDrawer
          investigation={inv}
          mmsi="999999999"
          rank={0}
          onClose={() => {}}
        />
      )

      expect(screen.getByText('Defensive Vessel')).toBeInTheDocument()
      expect(screen.getByText('N/A')).toBeInTheDocument()
      expect(screen.getByText('0')).toBeInTheDocument() // 0 AIS positions
    })
  })

  describe('DashboardTab Metocean Provenance', () => {
    it('renders Copernicus CMEMS and ECMWF labels correctly', () => {
      render(
        <DashboardTab
          investigation={investigationFixture}
          onSelectVessel={() => {}}
          onReanalyze={() => {}}
        />
      )

      expect(screen.getByText('Ocean Current (CMEMS):')).toBeInTheDocument()
      expect(screen.getByText('Atmospheric Wind (ECMWF):')).toBeInTheDocument()
    })
  })
})
