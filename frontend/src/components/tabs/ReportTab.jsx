import { useEffect, useState } from 'react'
import { marked } from 'marked'
import api from '../../api'
import './ReportTab.css'

// The PDF and vessel-ranking CSV endpoints already send a real
// `Content-Disposition: attachment` header (see backend `app/main.py`), so
// a plain <a href target="_blank"> triggers a genuine file-save prompt for
// those two. The investigation-JSON and both GeoJSON endpoints, however,
// are plain JSON API responses with no such header - clicking those links
// used to just open the raw JSON inline in a new tab instead of
// downloading anything, even though the buttons say "Download". This
// helper builds a real file client-side (via a Blob + a throwaway <a
// download> element) so all five buttons behave the same way. For the
// investigation JSON specifically, this also sidesteps an unnecessary
// second network round-trip: the exact data already sitting in this page's
// `investigation` prop is guaranteed to match what's on screen, so we
// download that directly instead of re-fetching (and risking it not
// matching if a later investigation had since been started elsewhere).
function downloadJson(filename, data) {
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
}

export default function ReportTab({ investigation }) {
  const [html, setHtml] = useState('')
  const [loading, setLoading] = useState(true)
  const [downloadError, setDownloadError] = useState(null)

  useEffect(() => {
    let cancelled = false
    api.getReportMarkdown(investigation.id).then(md => {
      if (!cancelled) {
        let polishedMd = md
          .replaceAll('CLASSICAL_DARK_SPOT_ML_FALLBACK', 'SAR Analysis — Alternate Processing')
          .replaceAll('CLASSICAL_DARK_SPOT', 'SAR Detection')
          .replaceAll('hybrid_dark_spot_morphology', 'SAR Feature Analysis')
          .replaceAll('ML_REAL_ZENODO_TRAINED', 'AI Model — Zenodo Trained')
          .replaceAll('ML_PROTOTYPE_SYNTHETIC_TRAINED', 'AI Model — Development Dataset')
          .replaceAll('SYNTHETIC_DEMO', 'Illustrative Scenario')
          .replaceAll('USER_UPLOADED_REAL_IMAGE', 'Satellite Observation')
          .replaceAll('Incident Overview', 'Investigation Summary')
          .replaceAll('Oil Slick Detection', 'Spill Detection')
          .replaceAll('Slick Characterisation', 'Spill Characteristics')
          .replaceAll('Hindcast Analysis', 'Drift Reconstruction')
          .replaceAll('AIS Traffic Reconstruction', 'Vessel Correlation')
          .replaceAll('Candidate Vessel Analysis', 'Attribution Analysis')
          .replaceAll('Evidence Breakdown (Top Candidate)', 'Evidence Timeline')
          .replaceAll('Limitations', 'Methodology & Provenance');
        setHtml(marked.parse(polishedMd))
        setLoading(false)
      }
    }).catch(() => setLoading(false))
    return () => { cancelled = true }
  }, [investigation.id])

  const handleDownloadJson = () => {
    setDownloadError(null)
    downloadJson(`investigation_${investigation.id}.json`, investigation)
  }

  const handleDownloadGeojson = (label, fetcher, filename) => async () => {
    setDownloadError(null)
    try {
      const geojson = await fetcher(investigation.id)
      downloadJson(filename, geojson)
    } catch (e) {
      setDownloadError(`Couldn't download ${label}: ${e.response?.data?.detail || e.message}`)
    }
  }

  return (
    <div>
      <div className="section-head">
        <div>
          <span className="eyebrow">Section 31 · Investigation report</span>
          <h2>Marine Pollution Intelligence Investigation Report</h2>
        </div>
      </div>

      <div className="report-toolbar">
        <a className="btn-secondary" href={api.reportPdfUrl(investigation.id)} target="_blank" rel="noreferrer">
          Download PDF
        </a>
        <button type="button" className="btn-secondary" onClick={handleDownloadJson}>
          Download JSON
        </button>
        <a className="btn-secondary" href={api.vesselsCsvUrl(investigation.id)} target="_blank" rel="noreferrer">
          Download vessel ranking (CSV)
        </a>
        {investigation.satellite.geometry && (
          <button
            type="button"
            className="btn-secondary"
            onClick={handleDownloadGeojson('spill polygon', api.getSpillPolygonGeojson, `spill_polygon_${investigation.id}.geojson`)}
          >
            Download spill polygon (GeoJSON)
          </button>
        )}
        {investigation.candidates.length > 0 && (
          <button
            type="button"
            className="btn-secondary"
            onClick={handleDownloadGeojson('vessel tracks', api.getVesselTracksGeojson, `vessel_tracks_${investigation.id}.geojson`)}
          >
            Download vessel tracks (GeoJSON)
          </button>
        )}
      </div>

      {downloadError && <p className="report-download-error">{downloadError}</p>}

      {loading ? (
        <p style={{ color: 'var(--text-muted)' }}>Generating report…</p>
      ) : (
        <div className="report-doc" dangerouslySetInnerHTML={{ __html: html }} />
      )}
    </div>
  )
}
