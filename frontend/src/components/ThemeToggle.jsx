import './ThemeToggle.css'
import { useTheme } from '../lib/theme.jsx'

const OPTIONS = [
  {
    id: 'light',
    label: 'Light theme',
    icon: (
      <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
        <circle cx="12" cy="12" r="4.2" stroke="currentColor" strokeWidth="1.6" />
        <path
          d="M12 2.5v2.4M12 19.1v2.4M4.2 4.2l1.7 1.7M18.1 18.1l1.7 1.7M2.5 12h2.4M19.1 12h2.4M4.2 19.8l1.7-1.7M18.1 5.9l1.7-1.7"
          stroke="currentColor"
          strokeWidth="1.6"
          strokeLinecap="round"
        />
      </svg>
    ),
  },
  {
    id: 'dark',
    label: 'Dark theme',
    icon: (
      <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
        <path
          d="M20.2 14.6A8.6 8.6 0 1 1 9.4 3.8a7 7 0 0 0 10.8 10.8z"
          stroke="currentColor"
          strokeWidth="1.6"
          strokeLinejoin="round"
        />
      </svg>
    ),
  },
  {
    id: 'system',
    label: 'Match system theme',
    icon: (
      <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
        <rect x="3" y="4.5" width="18" height="12" rx="1.5" stroke="currentColor" strokeWidth="1.6" />
        <path d="M8 20.5h8M12 16.5v4" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
      </svg>
    ),
  },
]

/**
 * Compact Light / Dark / System control. Persists via ThemeProvider
 * (localStorage) and applies immediately - no confirmation, no page
 * reload, and switching tabs afterwards does not reset it (the choice
 * lives above the router/tab state in main.jsx).
 */
export default function ThemeToggle() {
  const { preference, setTheme } = useTheme()

  return (
    <div className="theme-toggle" role="radiogroup" aria-label="Theme">
      {OPTIONS.map((opt) => (
        <button
          key={opt.id}
          type="button"
          role="radio"
          aria-checked={preference === opt.id}
          aria-label={opt.label}
          title={opt.label}
          className={`theme-toggle__btn${preference === opt.id ? ' active' : ''}`}
          onClick={() => setTheme(opt.id)}
        >
          {opt.icon}
        </button>
      ))}
    </div>
  )
}
