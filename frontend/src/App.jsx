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
import api from './api'

// Surfaces the backend's structured `{"detail": "..."}` error message when
// available (see backend/app/main.py's global exception handlers), falling
// back to a generic message otherwise - so a specific, actionable reason
// (e.g. "missing required columns: mmsi, lat") reaches the user instead of
// only ever a generic string.
function extractErrorDetail(e, fallback) {
  const detail = e?.response?.data?.detail
  if (typeof detail === 'string' && detail.trim()) return detail
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
  const [activeTab, setActiveTab] = useState('dashboard')
  const [running, setRunning] = useState(false)
  const [revealedCount, setRevealedCount] = useState(0)
  const [progressSteps, setProgressSteps] = useState([])
  const [selectedMmsi, setSelectedMmsi] = useState(null)
  const [showUpload, setShowUpload] = useState(false)
  const [showSatelliteUpload, setShowSatelliteUpload] = useState(false)
  const [error, setError] = useState(null)
  const [restoring, setRestoring] = useState(() => !!readStoredInvestigationId())
  const timerRef = useRef(null)
  // Defense-in-depth re-entrancy guard for the real-satellite-upload
  // pipeline, independent of UploadSatelliteModal's own guard (see that
  // component's submittingRef comment). This one lives here, at the
  // orchestration layer, so runRealSatellite itself can never kick off a
  // second POST /api/satellite/upload while one is already in flight,
  // regardless of what triggers the second call (a stale onSubmit
  // reference, a remounted modal, a StrictMode-driven double invocation of
  // some future caller, etc.) - "one deliberate click, exactly one
  // request" is guaranteed here even if every other caller of this
  // function were misbehaving.
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

  const stageProgress = useCallback((inv) => {
    const steps = inv.progress_log.map(p => p.step)
    setProgressSteps(steps)
    setRevealedCount(0)
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
    setError(null)
    setRunning(true)
    setSelectedMmsi(null)
    try {
      const inv = await api.runInvestigation({ mode: 'demo' })
      storeInvestigationId(inv.id)
      stageProgress(inv)
    } catch (e) {
      console.error(e)
      setError(extractErrorDetail(e, 'Investigation failed to run. Check that the backend API is reachable.'))
      setRunning(false)
    }
  }, [stageProgress])

  const runCustomAis = useCallback(async (file) => {
    setError(null)
    setShowUpload(false)
    setRunning(true)
    setSelectedMmsi(null)
    try {
      await api.uploadAis(file)
      const inv = await api.runInvestigation({ mode: 'custom_ais' })
      storeInvestigationId(inv.id)
      stageProgress(inv)
    } catch (e) {
      console.error(e)
      setError(extractErrorDetail(e, 'Investigation with custom AIS data failed. Check the CSV format and try again.'))
      setRunning(false)
    }
  }, [stageProgress])

  const runRealSatellite = useCallback(async (file, { detector, centerLat, centerLon }) => {
    if (satelliteUploadInFlightRef.current) {
      console.warn('runRealSatellite: upload already in flight, ignoring duplicate call')
      return
    }
    satelliteUploadInFlightRef.current = true
    setError(null)
    setShowSatelliteUpload(false)
    setRunning(true)
    setSelectedMmsi(null)
    try {
      const inv = await api.uploadSatelliteImage(file, { detector, centerLat, centerLon })
      // A late-but-genuine success always wins over anything shown earlier
      // for this same attempt: error was already cleared above, and this
      // path never runs unless the request truly succeeded, so there is no
      // path left by which a stale failure banner and a successful current
      // investigation could ever be visible at the same time.
      storeInvestigationId(inv.id)
      stageProgress(inv)
    } catch (e) {
      console.error(e)
      setError(extractErrorDetail(e, 'Investigation on the uploaded satellite image failed. Check the file format (GeoTIFF/PNG) and try again.'))
      setRunning(false)
    } finally {
      satelliteUploadInFlightRef.current = false
    }
  }, [stageProgress])

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
          <div className="caveat-note" style={{ marginBottom: 'var(--sp-4)', borderLeftColor: 'var(--signal-coral)' }}>
            {error}
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
                <DashboardTab investigation={investigation} selectedMmsi={selectedMmsi} onSelectVessel={setSelectedMmsi} />
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
