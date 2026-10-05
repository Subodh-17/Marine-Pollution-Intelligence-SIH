import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { useState } from 'react'
import ErrorBoundary from '../components/ErrorBoundary'

// Deliberately throws during render when told to, so the boundary's
// getDerivedStateFromError/componentDidCatch path is exercised for real
// rather than only asserted about in isolation.
function Bomb({ shouldThrow }) {
  if (shouldThrow) {
    throw new Error('boom: simulated render failure')
  }
  return <div>content rendered fine</div>
}

describe('ErrorBoundary', () => {
  it('renders children normally when nothing throws', () => {
    render(
      <ErrorBoundary resetKey="a">
        <Bomb shouldThrow={false} />
      </ErrorBoundary>
    )
    expect(screen.getByText('content rendered fine')).toBeInTheDocument()
  })

  it('shows a recovery panel instead of crashing when a child throws, without leaking the raw error message', () => {
    // React logs the thrown error to the console by default during tests;
    // silence that expected noise for this one assertion.
    const consoleSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    render(
      <ErrorBoundary resetKey="a">
        <Bomb shouldThrow={true} />
      </ErrorBoundary>
    )
    expect(screen.getByRole('alert')).toBeInTheDocument()
    expect(screen.getByText(/couldn't be rendered/i)).toBeInTheDocument()
    // The user-facing panel must never echo the internal error message.
    expect(screen.queryByText(/boom: simulated render failure/)).not.toBeInTheDocument()
    consoleSpy.mockRestore()
  })

  it('clears the error automatically when resetKey changes, e.g. the user switches tabs', () => {
    vi.spyOn(console, 'error').mockImplementation(() => {})
    function Harness() {
      const [key, setKey] = useState('tab-a')
      return (
        <div>
          <button onClick={() => setKey('tab-b')}>switch tab</button>
          <ErrorBoundary resetKey={key}>
            <Bomb shouldThrow={key === 'tab-a'} />
          </ErrorBoundary>
        </div>
      )
    }
    render(<Harness />)
    expect(screen.getByRole('alert')).toBeInTheDocument()
    fireEvent.click(screen.getByText('switch tab'))
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(screen.getByText('content rendered fine')).toBeInTheDocument()
  })

  it('lets the user manually retry via the "Try again" button', () => {
    vi.spyOn(console, 'error').mockImplementation(() => {})
    const onReset = vi.fn()
    render(
      <ErrorBoundary resetKey="a" onReset={onReset}>
        <Bomb shouldThrow={true} />
      </ErrorBoundary>
    )
    fireEvent.click(screen.getByText('Try again'))
    expect(onReset).toHaveBeenCalledTimes(1)
  })
})
