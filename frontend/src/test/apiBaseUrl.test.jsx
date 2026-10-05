import { describe, it, expect, beforeEach, vi } from 'vitest'

// Regression coverage for a real production bug: reportPdfUrl (and,
// found during the same audit, ReportTab.jsx's separately hardcoded
// vessels.csv link) built their URLs as a literal '/api/...' string
// instead of going through the same VITE_API_BASE_URL-aware resolution
// axios's `client` uses. In local dev both look identical (VITE_API_BASE_URL
// is unset, so the "bug" and the "fix" both produce '/api/...'), which is
// exactly why this shipped unnoticed - it only breaks once
// VITE_API_BASE_URL is actually set for a production build, at which
// point a plain <a href="/api/..."> resolves against the deployed
// FRONTEND's own origin (e.g. https://frontend.vercel.app/api/...)
// instead of the backend. This file exercises both the unset (local) and
// set (production) cases explicitly, unmocked, against the real api.js
// module - not the smoke-test suite's mocked `api` stand-in - since the
// bug lived in the URL-building logic itself, not in anything a mock
// would exercise.
describe('api.js base URL resolution (production API URL bug regression)', () => {
  beforeEach(() => {
    vi.unstubAllEnvs()
    vi.resetModules()
  })

  it('reportPdfUrl and vesselsCsvUrl use the relative /api path when VITE_API_BASE_URL is unset (local dev)', async () => {
    vi.stubEnv('VITE_API_BASE_URL', '')
    const { default: api } = await import('../api')
    expect(api.reportPdfUrl('abc123')).toBe('/api/report/abc123?fmt=pdf')
    expect(api.vesselsCsvUrl('abc123')).toBe('/api/export/abc123/vessels.csv')
  })

  it('reportPdfUrl and vesselsCsvUrl use the configured backend origin when VITE_API_BASE_URL is set (production build)', async () => {
    vi.stubEnv('VITE_API_BASE_URL', 'https://mpi-backend.onrender.com/api')
    const { default: api } = await import('../api')
    expect(api.reportPdfUrl('abc123')).toBe('https://mpi-backend.onrender.com/api/report/abc123?fmt=pdf')
    expect(api.vesselsCsvUrl('abc123')).toBe('https://mpi-backend.onrender.com/api/export/abc123/vessels.csv')
    // The critical assertion: the frontend's OWN origin must never appear
    // in these URLs when a backend origin is configured - that's exactly
    // what the bug produced (a bare '/api/...' silently resolves against
    // whatever page it's rendered on).
    expect(api.reportPdfUrl('abc123')).not.toMatch(/^\/api/)
    expect(api.vesselsCsvUrl('abc123')).not.toMatch(/^\/api/)
  })
})
