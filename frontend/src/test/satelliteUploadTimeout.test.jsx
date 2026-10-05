import { describe, it, expect, vi, beforeEach } from 'vitest'

// --- Part 1: api.js's per-request timeout override, tested unmocked ---
// Mirrors apiBaseUrl.test.jsx's approach: import the REAL api.js (not the
// smoke-test suite's mocked stand-in), but mock the `axios` package itself
// so the exact config object passed to `client.post(...)` can be inspected
// directly - this is the only way to actually prove the timeout value the
// real code sends, rather than asserting on a mock that could drift from
// api.js undetected. Deliberately kept in its own file, separate from
// satelliteUploadFlow.test.jsx: that file's top-level `vi.mock('../api',
// ...)` is hoisted file-wide by Vitest, which would otherwise silently
// replace the REAL api.js this file needs with that mock too.
describe('api.js uploadSatelliteImage timeout (Part 1 regression)', () => {
  let mockPost
  let mockCreate

  beforeEach(() => {
    vi.resetModules()
    mockPost = vi.fn(() => Promise.resolve({ data: { id: 'inv-1' } }))
    mockCreate = vi.fn(() => ({
      post: mockPost,
      get: vi.fn(() => Promise.resolve({ data: {} })),
      interceptors: { response: { use: vi.fn() } },
    }))
    vi.doMock('axios', () => ({ default: { create: mockCreate } }))
  })

  it('creates the shared client with the normal 30s timeout, unchanged for every other endpoint', async () => {
    await import('../api')
    expect(mockCreate).toHaveBeenCalledWith(expect.objectContaining({ timeout: 30000 }))
  })

  it('sends /satellite/upload with its own 300000ms (5 min) timeout, not the client default', async () => {
    const { default: api } = await import('../api')
    const file = new File(['fake-tiff-bytes'], 'scene.tif', { type: 'image/tiff' })
    await api.uploadSatelliteImage(file, { detector: 'ml', centerLat: 19.05, centerLon: 71.20 })

    expect(mockPost).toHaveBeenCalledTimes(1)
    const [url, , config] = mockPost.mock.calls[0]
    expect(url).toBe('/satellite/upload')
    expect(config.timeout).toBe(300000)
    // A request that legitimately takes ~128s (the observed real duration
    // that motivated this fix) must fit comfortably inside this timeout.
    expect(config.timeout).toBeGreaterThan(128000)
    expect(config.params).toEqual({ detector: 'ml', center_lat: 19.05, center_lon: 71.20 })
  })
})
