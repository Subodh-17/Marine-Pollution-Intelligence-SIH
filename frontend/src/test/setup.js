import '@testing-library/jest-dom/vitest'

global.ResizeObserver = class { observe(){} unobserve(){} disconnect(){} }
// jsdom has no real matchMedia implementation. Recharts' internal
// usePrefersReducedMotion hook (recharts >= 3.x) queries
// `(prefers-reduced-motion: reduce)` via the modern MediaQueryList
// addEventListener/removeEventListener API, not the deprecated
// addListener/removeListener pair - both are stubbed here so any
// component using either API works under test, matching a real browser.
window.matchMedia = function () {
  return {
    matches: false,
    media: '',
    onchange: null,
    addListener() {},
    removeListener() {},
    addEventListener() {},
    removeEventListener() {},
    dispatchEvent() { return false },
  }
}

