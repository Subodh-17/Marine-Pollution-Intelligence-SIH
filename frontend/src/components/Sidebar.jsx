import './Sidebar.css'

const ICONS = {
  dashboard: (
    <svg viewBox="0 0 24 24" fill="none"><rect x="3" y="3" width="8" height="10" rx="1.5" stroke="currentColor" strokeWidth="1.6"/><rect x="13" y="3" width="8" height="6" rx="1.5" stroke="currentColor" strokeWidth="1.6"/><rect x="13" y="11" width="8" height="10" rx="1.5" stroke="currentColor" strokeWidth="1.6"/><rect x="3" y="15" width="8" height="6" rx="1.5" stroke="currentColor" strokeWidth="1.6"/></svg>
  ),
  satellite: (
    <svg viewBox="0 0 24 24" fill="none"><path d="M8 8l3-3 5 5-3 3-5-5z" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round"/><path d="M13 13l6 6" stroke="currentColor" strokeWidth="1.6"/><path d="M17 17l2-2" stroke="currentColor" strokeWidth="1.6"/><path d="M3 21l4-4" stroke="currentColor" strokeWidth="1.6"/><path d="M15.5 5.5L18 3M20.5 8L23 5.5" stroke="currentColor" strokeWidth="1.6"/></svg>
  ),
  ais: (
    <svg viewBox="0 0 24 24" fill="none"><circle cx="12" cy="12" r="1.8" fill="currentColor"/><circle cx="12" cy="12" r="6" stroke="currentColor" strokeWidth="1.4" opacity="0.7"/><circle cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="1.2" opacity="0.4"/></svg>
  ),
  ranking: (
    <svg viewBox="0 0 24 24" fill="none"><path d="M5 21V10M12 21V3M19 21v-7" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round"/></svg>
  ),
  timeline: (
    <svg viewBox="0 0 24 24" fill="none"><path d="M3 12h18" stroke="currentColor" strokeWidth="1.6"/><circle cx="6" cy="12" r="2" fill="currentColor"/><circle cx="12" cy="12" r="2.4" fill="var(--signal-amber)"/><circle cx="18" cy="12" r="2" fill="currentColor"/></svg>
  ),
  report: (
    <svg viewBox="0 0 24 24" fill="none"><rect x="5" y="3" width="14" height="18" rx="1.5" stroke="currentColor" strokeWidth="1.6"/><path d="M8 8h8M8 12h8M8 16h5" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round"/></svg>
  ),
  mltraining: (
    <svg viewBox="0 0 24 24" fill="none"><rect x="8" y="8" width="8" height="8" rx="1.2" stroke="currentColor" strokeWidth="1.6"/><path d="M12 3v3M12 18v3M3 12h3M18 12h3M6 6l2 2M18 6l-2 2M6 18l2-2M18 18l-2-2" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/></svg>
  ),
  routeplanner: (
    <svg viewBox="0 0 24 24" fill="none"><circle cx="12" cy="12" r="9" stroke="currentColor" strokeWidth="1.6"/><polygon points="12,6 14.8,11.8 12,18 9.2,11.8" fill="currentColor"/><circle cx="12" cy="12" r="1.5" fill="var(--bg-void)"/></svg>
  ),
}

const ITEMS = [
  { id: 'dashboard', label: 'Overview' },
  { id: 'satellite', label: 'Satellite' },
  { id: 'ais', label: 'AIS' },
  { id: 'ranking', label: 'Ranking' },
  { id: 'timeline', label: 'Timeline' },
  { id: 'report', label: 'Report' },
  { id: 'routeplanner', label: 'Route Planner' },
  { id: 'mltraining', label: 'ML TRAINING' },
]

// Dashboard, ML Training, and Route Planner don't require a prior SAR investigation run:
const ALWAYS_ENABLED_IDS = new Set(['dashboard', 'mltraining', 'routeplanner'])

export default function Sidebar({ active, onSelect, enabled }) {
  return (
    <nav className="sidebar" aria-label="Investigation sections">
      {ITEMS.map(item => (
        <button
          key={item.id}
          className={`sidebar__item${active === item.id ? ' active' : ''}`}
          onClick={() => onSelect(item.id)}
          disabled={!ALWAYS_ENABLED_IDS.has(item.id) && !enabled}
          title={item.label}
        >
          {ICONS[item.id]}
          <span>{item.label}</span>
        </button>
      ))}
    </nav>
  )
}
