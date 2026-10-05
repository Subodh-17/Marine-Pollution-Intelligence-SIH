export default function ProgressChecklist({ steps, revealedCount }) {
  return (
    <div className="progress-panel" role="status" aria-live="polite">
      <h3>Running investigation pipeline…</h3>
      {steps.map((step, i) => {
        const state = i < revealedCount ? 'done' : (i === revealedCount ? 'current' : '')
        return (
          <div key={step} className={`progress-item ${state}`} style={{ animationDelay: `${i * 20}ms` }}>
            <span className="progress-item__mark" />
            {step}
          </div>
        )
      })}
    </div>
  )
}
