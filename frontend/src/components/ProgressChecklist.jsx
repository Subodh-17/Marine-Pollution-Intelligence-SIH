import { useEffect, useState } from 'react'

export default function ProgressChecklist({ steps = [], revealedCount = 0 }) {
  const [elapsedSeconds, setElapsedSeconds] = useState(0)

  useEffect(() => {
    const start = performance.now()
    const timer = setInterval(() => {
      setElapsedSeconds(Math.max(0, (performance.now() - start) / 1000))
    }, 100)
    return () => clearInterval(timer)
  }, [])

  return (
    <div className="progress-panel" role="status" aria-live="polite">
      <div className="progress-panel-head">
        <h3>Running investigation pipeline…</h3>
        <span className="progress-elapsed" aria-label={`Elapsed time: ${elapsedSeconds.toFixed(1)} seconds`}>
          {elapsedSeconds.toFixed(1)}s
        </span>
      </div>

      {steps.length === 0 ? (
        <div className="progress-loading-state">
          <span className="progress-spinner" aria-hidden="true" />
          <span>Executing SAR detection, metocean advection & vessel attribution…</span>
        </div>
      ) : (
        steps.map((step, i) => {
          const state = i < revealedCount ? 'done' : (i === revealedCount ? 'current' : '')
          return (
            <div key={step} className={`progress-item ${state}`} style={{ animationDelay: `${i * 20}ms` }}>
              <span className="progress-item__mark" />
              {step}
            </div>
          )
        })
      )}
    </div>
  )
}
