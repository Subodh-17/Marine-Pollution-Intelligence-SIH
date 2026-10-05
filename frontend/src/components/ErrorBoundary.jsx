import { Component } from 'react'
import './ErrorBoundary.css'

// Catches rendering errors anywhere in the wrapped subtree so a bug in a
// single tab or panel (e.g. an investigation record missing an expected
// field) can never crash the whole console to a blank white screen. Never
// exposes a stack trace, component name, or other internal detail to the
// user - just a calm, actionable recovery panel - but logs the full error
// to the console so it's still debuggable in development.
//
// `resetKey` lets a parent clear a stale error automatically when the
// thing that caused it is no longer on screen (e.g. the user switched
// tabs or started a new investigation) without the caller needing to track
// error state itself.
class ErrorBoundary extends Component {
  constructor(props) {
    super(props)
    this.state = { hasError: false, resetKey: props.resetKey }
  }

  static getDerivedStateFromError() {
    return { hasError: true }
  }

  // Declarative counterpart to componentDidUpdate+setState (which oxlint's
  // react/no-did-update-set-state rule flags, for good reason - it forces
  // an extra render pass): comparing the incoming resetKey against the one
  // already recorded in state during render itself means React folds the
  // clear-on-navigation behaviour into the same render pass instead of
  // scheduling a second one.
  static getDerivedStateFromProps(props, state) {
    if (props.resetKey !== state.resetKey) {
      return { hasError: false, resetKey: props.resetKey }
    }
    return null
  }

  componentDidCatch(error, info) {
    // eslint-disable-next-line no-console
    console.error('[ErrorBoundary] Unhandled render error:', error, info?.componentStack)
  }

  handleReset = () => {
    this.setState({ hasError: false })
    this.props.onReset?.()
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="error-boundary panel" role="alert">
          <span className="error-boundary__eyebrow">Display error</span>
          <h2>This section couldn&apos;t be rendered</h2>
          <p>
            Something went wrong while rendering this part of the console. The rest of the
            investigation and its data are unaffected. Try again, or switch tabs to continue.
          </p>
          <button type="button" className="btn-secondary" onClick={this.handleReset}>
            Try again
          </button>
        </div>
      )
    }
    return this.props.children
  }
}

export default ErrorBoundary
