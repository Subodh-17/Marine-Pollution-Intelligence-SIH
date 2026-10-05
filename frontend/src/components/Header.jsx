import './Header.css'
import ThemeToggle from './ThemeToggle'

function RadarMark() {
  return (
    <svg className="header__mark" viewBox="0 0 26 26" fill="none" xmlns="http://www.w3.org/2000/svg">
      <circle cx="13" cy="13" r="11" stroke="var(--signal-cyan-dim)" strokeWidth="1.2" />
      <circle cx="13" cy="13" r="6.5" stroke="var(--signal-cyan-dim)" strokeWidth="1" />
      <path d="M13 13 L13 2.2 A10.8 10.8 0 0 1 23.8 13 Z" fill="var(--signal-cyan)" opacity="0.55" />
      <circle cx="13" cy="13" r="1.6" fill="var(--signal-amber)" />
    </svg>
  )
}

export default function Header({ investigationId, onRunDemo, running, onUploadClick, onUploadSatelliteClick, onOpenReport, hasInvestigation }) {
  return (
    <header className="header">
      <div className="header__brand">
        <RadarMark />
        <div className="header__title">
          MARINE POLLUTION INTELLIGENCE
          <small>VESSEL ATTRIBUTION PLATFORM · SIH PROTOTYPE</small>
        </div>
        {investigationId && (
          <div className="header__inv-id">INV&nbsp;#{investigationId}</div>
        )}
      </div>
      <div className="header__actions">
        <ThemeToggle />
        {hasInvestigation && (
          <button className="btn-secondary" onClick={onOpenReport}>View report</button>
        )}
        <button className="btn-secondary" onClick={onUploadSatelliteClick}>ANALYZE SATELLITE IMAGE</button>
        <button className="btn-secondary" onClick={onUploadClick}>ANALYZE AIS DATA</button>
        <button className="btn-run-demo" onClick={onRunDemo} disabled={running}>
          {running ? <span className="spinner" /> : '▶'}
          {running ? 'ANALYZING…' : 'RUN ANALYSIS'}
        </button>
      </div>
    </header>
  )
}
