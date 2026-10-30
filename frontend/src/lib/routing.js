/**
 * Workstream B: Resolves initial active tab from the URL path for client-side
 * SPA deep-link routing (e.g. /dashboard, /satellite, /ranking, /mltraining).
 */
export function getInitialTabFromLocation(pathname = (typeof window !== 'undefined' ? window.location?.pathname : '')) {
  try {
    const raw = (pathname || '').replace(/^\/+/, '').split('/')[0].toLowerCase()
    const validTabs = ['dashboard', 'satellite', 'ais', 'ranking', 'timeline', 'report', 'mltraining', 'routeplanner']
    if (raw === 'ml-training') return 'mltraining'
    if (raw === 'overview') return 'dashboard'
    if (raw === 'routing' || raw === 'routes' || raw === 'navigation' || raw === 'route-planner') return 'routeplanner'
    if (validTabs.includes(raw)) return raw
  } catch {
    /* fallback */
  }
  return 'dashboard'
}
