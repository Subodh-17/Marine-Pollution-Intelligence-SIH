import { useCallback, useEffect, useRef, useState } from 'react'
import './styles/tokens.css'
import './styles/shell.css'
import './styles/components.css'
import Header from './components/Header'
import Sidebar from './components/Sidebar'
import ErrorBoundary from './components/ErrorBoundary'
import ProgressChecklist from './components/ProgressChecklist'
import VesselDetailDrawer from './components/VesselDetailDrawer'
import UploadAisModal from './components/UploadAisModal'
import UploadSatelliteModal from './components/UploadSatelliteModal'
import DashboardTab from './components/tabs/DashboardTab'
import SatelliteTab from './components/tabs/SatelliteTab'
import AisTab from './components/tabs/AisTab'
import RankingTab from './components/tabs/RankingTab'
import TimelineTab from './components/tabs/TimelineTab'
import ReportTab from './components/tabs/ReportTab'
import MlTrainingTab from './components/tabs/MlTrainingTab'
import { getInitialTabFromLocation } from './lib/routing'
import api from './api'

// Surfaces the backend's structured `{"detail": "..."}` error message when
// available (see backend/app/main.py's global exception handlers), falling
// back to a generic message otherwise - so a specific, actionable reason
// (e.g. "missing required columns: mmsi, lat") reaches the user instead of
// only ever a generic string.
function extractErrorDetail(e, fallback) {
  const detail = e?.response?.data?.detail
  if (typeof detail === 'string' && detail.trim()) return detail
  const message = e?.response?.data?.message
  if (typeof message === 'string' && message.trim()) return message
  if (e?.code === 'ECONNABORTED' || e?.message?.toLowerCase().includes('timeout')) {
    const isImageUpload = fallback.toLowerCase().includes('satellite image') || fallback.toLowerCase().includes('image')
    const tip = isImageUpload
      ? 'please retry or try a smaller image'
      : 'the backend may be waking up or retrieving live data — please retry'
    return `${fallback} (Request timed out — ${tip})`
  }
  if (e?.response?.status === 502 || e?.response?.status === 504) {
    return `${fallback} (Backend gateway waking up - please retry in a few moments)`
  }
  return fallback
}

// Persists only the investigation id (never the investigation payload
// itself) so that reloading the page - a lost wifi blip, an accidental
// F5, re-opening a pinned tab - can restore the same investigation from
// the backend's own store (SQLite by default, see DATA_PIPELINE.md §6)
// instead of dropping the user back to a blank console. sessionStorage
// (not localStorage) is deliberate: it survives a refresh within the
// same tab but does not quietly resurrect a days-old investigation the
// next time the app is opened, and it never leaks across tabs/devices.
const ACTIVE_INVESTIGATION_KEY = 'mpi_active_investigation_id'

function readStoredInvestigationId() {
  try {
    return window.sessionStorage.getItem(ACTIVE_INVESTIGATION_KEY)
  } catch {
    // Storage can throw in locked-down/private-browsing contexts - treat
    // exactly like "nothing stored" rather than crashing the app shell.
    return null
  }
}

function storeInvestigationId(id) {
  try {
    if (id) window.sessionStorage.setItem(ACTIVE_INVESTIGATION_KEY, id)
    else window.sessionStorage.removeItem(ACTIVE_INVESTIGATION_KEY)
  } catch {
    /* best-effort only */
  }
}

export default function App() {
  const [investigation, setInvestigation] = useState(null)
  const [activeTab, setActiveTab] = useState(getInitialTabFromLocation)
  const [running, setRunning] = useState(false)
  const [revealedCount, setRevealedCount] = useState(0)
  const [progressSteps, setProgressSteps] = useState([])
  const [selectedMmsi, setSelectedMmsi] = useState(null)
  const [showUpload, setShowUpload] = useState(false)
  const [showSatelliteUpload, setShowSatelliteUpload] = useState(false)
  const [error, setError] = useState(null)
  const [canRetry, setCanRetry] = useState(false)
  const retryRef = useRef(null)
  const [restoring, setRestoring] = useState(() => !!readStoredInvestigationId())
  const timerRef = useRef(null)

  // Workstream B: Synchronizes browser address bar with client activeTab
  // so deep-links, browser navigation, and refresh preserve the current view.
  useEffect(() => {
    try {
      if (typeof window === 'undefined') return
      const currentRaw = window.location.pathname.replace(/^\/+/, '').split('/')[0].toLowerCase()
      const normalizedCurrent = currentRaw === 'ml-training' ? 'mltraining' : (currentRaw || 'dashboard')
      if (normalizedCurrent !== activeTab) {
        const targetUrl = activeTab === 'dashboard' ? '/' : `/${activeTab}`
        window.history.replaceState(null, '', targetUrl)
      }
    } catch {
      /* best-effort only */
    }
  }, [activeTab])
  // Defense-in-depth re-entrancy guards for pipeline invocations
  const runDemoInFlightRef = useRef(false)
  const runCustomAisInFlightRef = useRef(false)
  const satelliteUploadInFlightRef = useRef(false)

  // Restore-on-refresh: runs once on mount only. Deliberately silent on
  // failure (expired/unknown id, backend restarted with DATABASE_ENABLED=
  // false, auth enabled without a token) - a stale local pointer to a
  // gone investigation is not something the user did wrong, so it is
  // cleared quietly and the app falls back to the normal empty state
  // rather than surfacing an error banner for it.
  useEffect(() => {
    const storedId = readStoredInvestigationId()
    if (!storedId) return
    let cancelled = false
    api.getInvestigation(storedId)
      .then((inv) => {
        if (cancelled) return
        setInvestigation(inv)
      })
      .catch(() => {
        if (cancelled) return
        storeInvestigationId(null)
      })
      .finally(() => {
        if (!cancelled) setRestoring(false)
      })
    return () => { cancelled = true }
  }, [])

  useEffect(() => {
    return () => {
      if (timerRef.current) clearInterval(timerRef.current)
    }
  }, [])

  const stageProgress = useCallback((inv) => {
    const steps = (inv?.progress_log || []).map(p => p.step)
    setProgressSteps(steps)
    setRevealedCount(0)
    if (steps.length === 0) {
      setInvestigation(inv)
      setRunning(false)
      setActiveTab('dashboard')
      return
    }
    let i = 0
    clearInterval(timerRef.current)
    timerRef.current = setInterval(() => {
      i += 1
      setRevealedCount(i)
      if (i >= steps.length) {
        clearInterval(timerRef.current)
        setTimeout(() => {
          setInvestigation(inv)
          setRunning(false)
          setActiveTab('dashboard')
        }, 350)
      }
    }, 180)
  }, [])

  const runDemo = useCallback(async () => {
    if (runDemoInFlightRef.current) {
      console.warn('runDemo: analysis already in flight, ignoring duplicate call')
      return
    }
    runDemoInFlightRef.current = true
    setError(null)
    setCanRetry(false)
    setRunning(true)
    setProgressSteps([])
    setRevealedCount(0)
    setSelectedMmsi(null)
    try {
      const inv = await api.runInvestigation({ mode: 'demo' })
      storeInvestigationId(inv.id)
      stageProgress(inv)
    } catch (e) {
      console.error(e)
      setError(extractErrorDetail(e, 'Investigation failed to run. Check that the backend API is reachable.'))
      retryRef.current = { type: 'demo' }
      setCanRetry(true)
      setRunning(false)
      setProgressSteps([])
    } finally {
      runDemoInFlightRef.current = false
    }
  }, [stageProgress])

  const runCustomAis = useCallback(async (file) => {
    if (runCustomAisInFlightRef.current) {
      console.warn('runCustomAis: analysis already in flight, ignoring duplicate call')
      return
    }
    runCustomAisInFlightRef.current = true
    setError(null)
    setCanRetry(false)
    setShowUpload(false)
    setRunning(true)
    setProgressSteps([])
    setRevealedCount(0)
    setSelectedMmsi(null)
    try {
      await api.uploadAis(file)
      const inv = await api.runInvestigation({ mode: 'custom_ais' })
      storeInvestigationId(inv.id)
      stageProgress(inv)
    } catch (e) {
      console.error(e)
      setError(extractErrorDetail(e, 'Investigation with custom AIS data failed. Check the CSV format and try again.'))
      retryRef.current = { type: 'custom_ais', file }
      setCanRetry(true)
      setRunning(false)
      setProgressSteps([])
    } finally {
      runCustomAisInFlightRef.current = false
    }
  }, [stageProgress])

  const runRealSatellite = useCallback(async (file, { detector, centerLat, centerLon, sampleName } = {}) => {
    if (satelliteUploadInFlightRef.current) {
      console.warn('runRealSatellite: upload already in flight, ignoring duplicate call')
      return
    }
    satelliteUploadInFlightRef.current = true
    setError(null)
    setCanRetry(false)
    setShowSatelliteUpload(false)
    setRunning(true)
    setProgressSteps([])
    setRevealedCount(0)
    setSelectedMmsi(null)
    try {
      let inv
      if (sampleName) {
        inv = await api.runSampleSatellite({ sampleName, detector, centerLat, centerLon })
      } else {
        inv = await api.uploadSatelliteImage(file, { detector, centerLat, centerLon })
      }
      // A late-but-genuine success always wins over anything shown earlier
      // for this same attempt: error was already cleared above, and this
      // path never runs unless the request truly succeeded, so there is no
      // path left by which a stale failure banner and a successful current
      // investigation could ever be visible at the same time.
      storeInvestigationId(inv.id)
      try {
        stageProgress(inv)
      } catch (stageErr) {
        console.warn('stageProgress animation failed, falling back to direct render', stageErr)
        setInvestigation(inv)
        setRunning(false)
        setActiveTab('dashboard')
      }
    } catch (e) {
      console.error(e)
      setError(extractErrorDetail(e, 'Investigation on the uploaded satellite image failed. Check the file format (GeoTIFF/PNG) and try again.'))
      retryRef.current = { type: 'real_satellite', file, params: { detector, centerLat, centerLon, sampleName } }
      setCanRetry(true)
      setRunning(false)
      setProgressSteps([])
    } finally {
      satelliteUploadInFlightRef.current = false
    }
  }, [stageProgress])

  const handleRetry = useCallback(() => {
    const action = retryRef.current
    if (!action) return
    setError(null)
    setCanRetry(false)
    if (action.type === 'demo') {
      runDemo()
    } else if (action.type === 'custom_ais') {
      runCustomAis(action.file)
    } else if (action.type === 'real_satellite') {
      runRealSatellite(action.file, action.params)
    }
  }, [runDemo, runCustomAis, runRealSatellite])

  const selectedRank = investigation && selectedMmsi
    ? investigation.candidates.findIndex(c => c.mmsi === selectedMmsi)
    : -1

  return (
    <div className="app-shell">
      <Sidebar active={activeTab} onSelect={setActiveTab} enabled={!!investigation} />
      <Header
        investigationId={investigation?.id}
        onRunDemo={runDemo}
        running={running}
        onUploadClick={() => setShowUpload(true)}
        onUploadSatelliteClick={() => setShowSatelliteUpload(true)}
        onOpenReport={() => setActiveTab('report')}
        hasInvestigation={!!investigation}
      />
      <main className="app-main chart-grid-bg">
        {error && (
          <div className="caveat-note" style={{ marginBottom: 'var(--sp-4)', borderLeftColor: 'var(--signal-coral)', display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 'var(--sp-3)' }}>
            <span>{error}</span>
            {canRetry && (
              <button
                className="btn-secondary"
                style={{ padding: '4px 12px', fontSize: '12px', whiteSpace: 'nowrap' }}
                onClick={handleRetry}
              >
                ↻ Retry
              </button>
            )}
          </div>
        )}

        {running && (
          <ProgressChecklist steps={progressSteps} revealedCount={revealedCount} />
        )}

        {/* A rendering bug in any single tab (e.g. an unexpected shape in
            investigation data) is caught here instead of taking down the
            whole console. Keyed on the active tab + investigation id so
            switching tabs or starting a new investigation always starts
            from a clean slate rather than re-showing a stale error. */}
        <ErrorBoundary resetKey={`${activeTab}:${investigation?.id ?? 'none'}`}>
          {!running && activeTab === 'mltraining' && <MlTrainingTab />}

          {/* While a stored investigation id from a previous page load is
              being re-fetched, show neither the empty state (which would
              flash misleadingly before the restore resolves) nor the full
              step-by-step progress checklist (which implies a pipeline is
              actively running, not just being re-fetched). */}
          {!running && restoring && activeTab !== 'mltraining' && !investigation && (
            <div className="empty-state">
              <span className="empty-state__eyebrow">Restoring your last investigation…</span>
            </div>
          )}

          {!running && !restoring && activeTab !== 'mltraining' && !investigation && (
            <div className="empty-state">
              <span className="empty-state__eyebrow">No active investigation</span>
              <h1>Detect the slick.<br />Trace the origin.<br />Rank the vessels.</h1>
              <p>
                This console runs the full pipeline end to end: Sentinel-1 SAR oil-slick detection,
                geometric characterisation, oceanographic/meteorological drift hindcasting to a
                probable spill origin, and AIS-based traffic reconstruction that scores and ranks
                candidate vessels with a fully explainable evidence breakdown.
              </p>
              <div className="empty-state__chain">
                <span>SAR Detection</span><span>Spill Characterization</span><span>Drift Reconstruction</span>
                <span>Vessel Correlation</span><span>Attribution Analysis</span><span>Investigation Report</span>
              </div>
              <button className="btn-run-demo" onClick={runDemo}>▶ RUN ANALYSIS</button>
            </div>
          )}

          {!running && activeTab !== 'mltraining' && investigation && (
            <>
              {activeTab === 'dashboard' && (
                <DashboardTab
                  investigation={investigation}
                  selectedMmsi={selectedMmsi}
                  onSelectVessel={setSelectedMmsi}
                  onRunDemo={runDemo}
                  onUploadAis={() => setShowUpload(true)}
                  onUploadSatellite={() => setShowSatelliteUpload(true)}
                  onSelectInvestigation={(id) => {
                    api.getInvestigation(id).then(inv => {
                      storeInvestigationId(inv.id)
                      setInvestigation(inv)
                    }).catch(console.error)
                  }}
                />
              )}
              {activeTab === 'satellite' && <SatelliteTab investigation={investigation} />}
              {activeTab === 'ais' && (
                <AisTab investigation={investigation} selectedMmsi={selectedMmsi} onSelectVessel={setSelectedMmsi} />
              )}
              {activeTab === 'ranking' && (
                <RankingTab investigation={investigation} onSelectVessel={setSelectedMmsi} />
              )}
              {activeTab === 'timeline' && (
                <TimelineTab investigation={investigation} onSelectVessel={setSelectedMmsi} />
              )}
              {activeTab === 'report' && <ReportTab investigation={investigation} />}
            </>
          )}
        </ErrorBoundary>
      </main>

      {investigation && selectedMmsi && (
        <VesselDetailDrawer
          investigation={investigation}
          mmsi={selectedMmsi}
          rank={Math.max(0, selectedRank)}
          onClose={() => setSelectedMmsi(null)}
        />
      )}

      {showUpload && (
        <UploadAisModal onClose={() => setShowUpload(false)} onSubmit={runCustomAis} />
      )}

      {showSatelliteUpload && (
        <UploadSatelliteModal onClose={() => setShowSatelliteUpload(false)} onSubmit={runRealSatellite} />
      )}
    </div>
  )
}
