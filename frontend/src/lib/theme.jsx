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

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'

export const STORAGE_KEY = 'mpi-theme-preference'
const PREFERENCES = ['light', 'dark', 'system']

function systemPrefersDark() {
  return typeof window !== 'undefined'
    && window.matchMedia
    && window.matchMedia('(prefers-color-scheme: dark)').matches
}

function resolve(preference) {
  return preference === 'system' ? (systemPrefersDark() ? 'dark' : 'light') : preference
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
  document.documentElement.setAttribute('data-theme', resolved)
  // Lets native form controls (checkboxes, scrollbars in some browsers,
  // date pickers, etc.) pick a matching light/dark rendering too.
  document.documentElement.style.colorScheme = resolved
}

const ThemeContext = createContext(null)

export function ThemeProvider({ children }) {
  const [preference, setPreference] = useState(readStoredPreference)
  const [resolvedTheme, setResolvedTheme] = useState(() => resolve(readStoredPreference()))

  // Keep the DOM attribute in sync whenever the user's choice changes.
  useEffect(() => {
    const resolved = resolve(preference)
    setResolvedTheme(resolved)
    applyResolvedTheme(resolved)
  }, [preference])

  // In "system" mode, follow live OS theme changes (e.g. the user's OS
  // switches to dark mode at sunset) without needing a page refresh.
  useEffect(() => {
    if (preference !== 'system' || !window.matchMedia) return undefined
    const mql = window.matchMedia('(prefers-color-scheme: dark)')
    const onChange = () => {
      const resolved = resolve('system')
      setResolvedTheme(resolved)
      applyResolvedTheme(resolved)
    }
    mql.addEventListener('change', onChange)
    return () => mql.removeEventListener('change', onChange)
  }, [preference])

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

export function useTheme() {
  const ctx = useContext(ThemeContext)
  if (!ctx) throw new Error('useTheme must be used within a ThemeProvider')
  return ctx
}
