import React from 'react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import './mockLeaflet'
import RoutePlannerTab from '../components/tabs/RoutePlannerTab'
import FuelIndicationMeter from '../components/FuelIndicationMeter'
import { getInitialTabFromLocation } from '../lib/routing'
import api from '../api'

vi.mock('../api', () => ({
  default: {
    getRoutePorts: vi.fn(),
    getVesselProfiles: vi.fn(),
    getRouteSpills: vi.fn(),
    planRoute: vi.fn(),
  },
}))

describe('Routing Deep-link Resolution', () => {
  it('resolves /routeplanner and aliases to routeplanner tab', () => {
    expect(getInitialTabFromLocation('/routeplanner')).toBe('routeplanner')
    expect(getInitialTabFromLocation('/routing')).toBe('routeplanner')
    expect(getInitialTabFromLocation('/routes')).toBe('routeplanner')
    expect(getInitialTabFromLocation('/navigation')).toBe('routeplanner')
    expect(getInitialTabFromLocation('/route-planner')).toBe('routeplanner')
  })
})

describe('FuelIndicationMeter Component', () => {
  const mockFuel = {
    distance_nm: 250.0,
    speed_knots: 14.5,
    duration_hours: 17.24,
    power_kw: 13500.0,
    hourly_consumption_mt: 2.322,
    total_fuel_consumed_mt: 40.03,
    total_fuel_consumed_litres: 46546.5,
    initial_fuel_mt: 2380.0,
    remaining_fuel_mt: 2339.97,
    remaining_fuel_litres: 2720895.3,
    remaining_range_nm: 14610.5,
    fuel_status: 'SUFFICIENT',
    reserve_limit_mt: 420.0,
    fuel_reserve_margin_pct: 83.6,
    fuel_tank_capacity_mt: 2800.0,
    co2_emissions_mt: 124.67,
    fuel_cost_usd: 26019.5,
  }

  it('renders fuel status and readouts in Metric Tonnes by default', () => {
    render(<FuelIndicationMeter fuel={mockFuel} />)
    expect(screen.getByText(/SUFFICIENT FUEL ON BOARD/i)).toBeInTheDocument()
    expect(screen.getByText(/Estimated Range/i)).toBeInTheDocument()
    expect(screen.getByText(/14,610.5/i)).toBeInTheDocument()
    expect(screen.getByText(/\$26,019.50/i)).toBeInTheDocument()
  })

  it('toggles unit between MT and Litres', () => {
    render(<FuelIndicationMeter fuel={mockFuel} />)
    const litresBtn = screen.getByRole('button', { name: /litres/i })
    fireEvent.click(litresBtn)
    expect(litresBtn).toHaveClass('active')
  })

  it('displays low reserve advisory when fuel_status is LOW_RESERVE', () => {
    const lowFuel = { ...mockFuel, fuel_status: 'LOW_RESERVE', remaining_fuel_mt: 300.0 }
    render(<FuelIndicationMeter fuel={lowFuel} />)
    expect(screen.getByText(/LOW RESERVE ADVISORY/i)).toBeInTheDocument()
  })

  it('displays critical warning when fuel_status is INSUFFICIENT', () => {
    const emptyFuel = { ...mockFuel, fuel_status: 'INSUFFICIENT', remaining_fuel_mt: 0.0 }
    render(<FuelIndicationMeter fuel={emptyFuel} />)
    expect(screen.getByText(/CRITICAL: INSUFFICIENT FUEL/i)).toBeInTheDocument()
  })
})

describe('RoutePlannerTab Component', () => {
  const mockPorts = [
    { id: 'mumbai', name: 'Mumbai Port / Anchorage', country: 'India', lat: 18.940, lon: 72.840 },
    { id: 'mormugao', name: 'Mormugao Port (Goa)', country: 'India', lat: 15.415, lon: 73.790 },
  ]

  const mockVessels = [
    {
      vessel_type: 'Tanker',
      name: 'Aframax Crude / Products Tanker',
      service_speed_knots: 14.5,
      fuel_tank_capacity_mt: 2800.0,
    },
  ]

  const mockSpills = [
    {
      id: 'demo_sar_slick',
      name: 'Sentinel-1 SAR Detection - Arabian Sea',
      polygon_latlon: [[19.0, 71.1], [19.0, 71.3], [19.1, 71.3], [19.1, 71.1]],
      buffer_nm: 2.5,
      area_km2: 24.8,
    },
  ]

  const mockPlanResponse = {
    origin: { lat: 18.940, lon: 72.840, name: 'Mumbai Port' },
    destination: { lat: 15.415, lon: 73.790, name: 'Mormugao Port' },
    recommended_route_type: 'spill_avoiding',
    baseline_route: {
      route_type: 'baseline',
      coordinates: [[18.940, 72.840], [15.415, 73.790]],
      distance_nm: 218.4,
      distance_km: 404.5,
      duration_hours: 15.06,
      speed_knots: 14.5,
      intersects_spill: true,
      closest_spill_distance_nm: 0.0,
      legs: [],
      fuel: {
        distance_nm: 218.4,
        speed_knots: 14.5,
        duration_hours: 15.06,
        power_kw: 13500.0,
        hourly_consumption_mt: 2.32,
        total_fuel_consumed_mt: 34.94,
        total_fuel_consumed_litres: 40627.9,
        initial_fuel_mt: 2380.0,
        remaining_fuel_mt: 2345.06,
        remaining_fuel_litres: 2726813.9,
        remaining_range_nm: 14655.0,
        fuel_status: 'SUFFICIENT',
        reserve_limit_mt: 420.0,
        fuel_reserve_margin_pct: 83.7,
        fuel_tank_capacity_mt: 2800.0,
        co2_emissions_mt: 108.82,
        fuel_cost_usd: 22711.0,
      },
    },
    spill_avoiding_route: {
      route_type: 'spill_avoiding',
      coordinates: [[18.940, 72.840], [17.5, 71.8], [15.415, 73.790]],
      distance_nm: 232.1,
      distance_km: 429.8,
      duration_hours: 16.01,
      speed_knots: 14.5,
      intersects_spill: false,
      closest_spill_distance_nm: 2.5,
      legs: [],
      fuel: {
        distance_nm: 232.1,
        speed_knots: 14.5,
        duration_hours: 16.01,
        power_kw: 13500.0,
        hourly_consumption_mt: 2.32,
        total_fuel_consumed_mt: 37.14,
        total_fuel_consumed_litres: 43186.0,
        initial_fuel_mt: 2380.0,
        remaining_fuel_mt: 2342.86,
        remaining_fuel_litres: 2724255.8,
        remaining_range_nm: 14641.0,
        fuel_status: 'SUFFICIENT',
        reserve_limit_mt: 420.0,
        fuel_reserve_margin_pct: 83.7,
        fuel_tank_capacity_mt: 2800.0,
        co2_emissions_mt: 115.67,
        fuel_cost_usd: 24141.0,
      },
    },
    comparison: {
      extra_distance_nm: 13.7,
      extra_distance_pct: 6.3,
      extra_duration_hours: 0.95,
      extra_fuel_consumed_mt: 2.2,
      extra_fuel_consumed_litres: 2558.1,
      extra_fuel_consumed_pct: 6.3,
      extra_cost_usd: 1430.0,
      extra_co2_mt: 6.85,
      safety_buffer_maintained_nm: 2.5,
    },
    spill_zones: mockSpills,
    vessel: {
      vessel_type: 'Tanker',
      name: 'Aframax Crude / Products Tanker',
      length_m: 245.0,
      beam_m: 42.0,
      draft_m: 14.5,
      displacement_dwt: 115000.0,
      service_speed_knots: 14.5,
      max_speed_knots: 16.0,
      main_engine_power_kw: 13500.0,
      auxiliary_power_kw: 1200.0,
      sfoc_g_per_kwh: 172.0,
      fuel_tank_capacity_mt: 2800.0,
      fuel_density_kg_per_l: 0.86,
      fuel_reserve_threshold_pct: 15.0,
    },
    disclaimer: 'NOTICE: ADVISORY DECISION SUPPORT ONLY. In compliance with SOLAS Chapter V, Regulation 34 and IMO Resolution A.893(21).',
  }

  beforeEach(() => {
    vi.clearAllMocks()
    api.getRoutePorts.mockResolvedValue(mockPorts)
    api.getVesselProfiles.mockResolvedValue(mockVessels)
    api.getRouteSpills.mockResolvedValue(mockSpills)
    api.planRoute.mockResolvedValue(mockPlanResponse)
  })

  it('renders route planner controls, triggers calculation, and displays comparison table', async () => {
    render(<RoutePlannerTab />)

    // Heading
    expect(screen.getByText(/Spill-Aware Route Optimization & Fuel Intelligence/i)).toBeInTheDocument()

    // Form inputs
    expect(screen.getByText(/Voyage & Vessel Parameters/i)).toBeInTheDocument()
    expect(screen.getByText(/Environmental Safety Buffer/i)).toBeInTheDocument()

    // Await planRoute response rendering
    await waitFor(() => {
      expect(api.planRoute).toHaveBeenCalled()
      expect(screen.getByRole('button', { name: /CALCULATE VOYAGE & FUEL CONSUMPTION/i })).toBeInTheDocument()
      expect(screen.getByText(/Voyage Trade-Off & Safety Comparison/i)).toBeInTheDocument()
      expect(screen.getByText(/COLLISION HAZARD/i)).toBeInTheDocument()
      expect(screen.getByText(/Buffer Enforced/i)).toBeInTheDocument()
      expect(screen.getByText(/SOLAS & IMO REGULATORY ADVISORY NOTICE/i)).toBeInTheDocument()
      expect(screen.getByText(/Spill-Avoiding Detour Recommended/i)).toBeInTheDocument()
    })
  })

  it('renders direct baseline recommended banner when baseline is clear of spills', async () => {
    const clearPlanResponse = {
      ...mockPlanResponse,
      recommended_route_type: 'baseline_safe',
      spill_avoiding_route: null,
      comparison: {
        ...mockPlanResponse.comparison,
        extra_distance_nm: 0.0,
        extra_distance_pct: 0.0,
        safety_buffer_maintained_nm: 12.4,
      },
      baseline_route: {
        ...mockPlanResponse.baseline_route,
        intersects_spill: false,
        closest_spill_distance_nm: 12.4,
      },
    }
    api.planRoute.mockResolvedValue(clearPlanResponse)

    render(<RoutePlannerTab />)

    await waitFor(() => {
      expect(screen.getByText(/Direct Baseline Recommended/i)).toBeInTheDocument()
      expect(screen.getByText(/The direct sea path is already safe/i)).toBeInTheDocument()
    })
  })
})
