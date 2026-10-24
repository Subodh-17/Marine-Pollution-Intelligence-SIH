// Theme system: Light / Dark / System.
//
// The actual color values live in styles/tokens.css under
// [data-theme="light"] / [data-theme="dark"] — this module only decides
// *which* of those two attribute values should be active and keeps it in
// sync with the user's stored preference and (in "system" mode) the OS.
//
// Flash-of-wrong-theme prevention: index.html has a small inline script,
// mirroring the STORAGE_KEY/resolve logic below, that sets `data-theme` on
// <html> before the stylesheet/app ever paints. This provider re-derives
// and re-applies the same value on mount (a no-op in the normal case) and
// then keeps it correct as the user changes their choice or their OS theme
// changes — the two are intentionally kept in lockstep so there is exactly
// one place (README/DEMO_GUIDE) that needs to document the storage key.

import { createContext, useCallback, useContext, useEffect, useMemo, useState, useSyncExternalStore } from 'react'

const STORAGE_KEY = 'mpi-theme-preference'
const PREFERENCES = ['light', 'dark', 'system']

function subscribeToSystemDark(callback) {
  if (typeof window === 'undefined' || !window.matchMedia) return () => {}
  const mql = window.matchMedia('(prefers-color-scheme: dark)')
  mql.addEventListener('change', callback)
  return () => mql.removeEventListener('change', callback)
}

function getSystemDarkSnapshot() {
  return typeof window !== 'undefined' && Boolean(window.matchMedia?.('(prefers-color-scheme: dark)').matches)
}

function getServerDarkSnapshot() {
  return false
}

function readStoredPreference() {
  try {
    const stored = localStorage.getItem(STORAGE_KEY)
    return PREFERENCES.includes(stored) ? stored : 'system'
  } catch {
    // localStorage unavailable (privacy mode, disabled storage, ...) -
    // fall back to following the OS setting for this session only.
    return 'system'
  }
}

function applyResolvedTheme(resolved) {
  if (typeof document === 'undefined') return
  document.documentElement.setAttribute('data-theme', resolved)
  // Lets native form controls (checkboxes, scrollbars in some browsers,
  // date pickers, etc.) pick a matching light/dark rendering too.
  document.documentElement.style.colorScheme = resolved
}

const ThemeContext = createContext(null)

export function ThemeProvider({ children }) {
  const [preference, setPreference] = useState(readStoredPreference)
  const systemDark = useSyncExternalStore(subscribeToSystemDark, getSystemDarkSnapshot, getServerDarkSnapshot)

  const resolvedTheme = preference === 'system' ? (systemDark ? 'dark' : 'light') : preference

  // Keep the DOM attribute in sync whenever the resolved theme changes.
  useEffect(() => {
    applyResolvedTheme(resolvedTheme)
  }, [resolvedTheme])

  const setTheme = useCallback((next) => {
    if (!PREFERENCES.includes(next)) return
    setPreference(next)
    try {
      localStorage.setItem(STORAGE_KEY, next)
    } catch {
      // Non-fatal: the choice still applies for the current session via
      // React state, it just won't survive a refresh.
    }
  }, [])

  const value = useMemo(
    () => ({ preference, resolvedTheme, setTheme }),
    [preference, resolvedTheme, setTheme],
  )

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>
}

// oxlint-disable-next-line react/only-export-components
export function useTheme() {
  const ctx = useContext(ThemeContext)
  if (!ctx) throw new Error('useTheme must be used within a ThemeProvider')
  return ctx
}
