import { useEffect, useRef, useState } from 'react'

export default function UploadSatelliteModal({ onClose, onSubmit }) {
  const fileRef = useRef(null)
  const [fileName, setFileName] = useState(null)
  const [detector, setDetector] = useState('auto')
  const [centerLat, setCenterLat] = useState(19.05)
  const [centerLon, setCenterLon] = useState(71.20)
  const [busy, setBusy] = useState(false)
  // A synchronous, render-independent guard against a second submit firing
  // before React has re-rendered the button as `disabled` - `busy` (state)
  // only takes effect on the NEXT render, so two clicks (or one real click
  // plus a stray duplicate, e.g. a fast double-click or an Enter-key repeat)
  // arriving in the same tick could otherwise both pass the `disabled`
  // check and both call `onSubmit`. A ref updates immediately, with no
  // render in between, so the second call is rejected synchronously - this
  // is what actually guarantees one click produces exactly one upload
  // request, with the `busy` state still driving the visible disabled/label
  // change for the user.
  const submittingRef = useRef(false)

  useEffect(() => {
    const onKeyDown = e => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [onClose])

  return (
    <>
      <div className="drawer-backdrop" onClick={onClose} />
      <div
        className="panel"
        role="dialog"
        aria-modal="true"
        aria-labelledby="upload-satellite-title"
        style={{
          position: 'fixed', top: '50%', left: '50%', transform: 'translate(-50%,-50%)',
          zIndex: 902, width: 460, maxWidth: '92vw',
        }}>
        <h3 id="upload-satellite-title" style={{ marginTop: 0, fontFamily: 'var(--font-display)' }}>Use a real satellite image</h3>
        <p style={{ fontSize: 12.5, color: 'var(--text-secondary)', lineHeight: 1.6 }}>
          Upload a GeoTIFF or PNG SAR-like image. This runs the same detection → geometry → drift
          → AIS → attribution pipeline as the demo, on your actual image instead of the synthetic
          scene. This file format carries no embedded geolocation, so the approximate scene-center
          coordinates below anchor the geometry — they are not read from the file.
        </p>

        <input
          ref={fileRef}
          type="file"
          accept=".tif,.tiff,.geotiff,.png,.jpg,.jpeg,.webp"
          disabled={busy}
          onChange={e => setFileName(e.target.files[0]?.name || null)}
          style={{ margin: '10px 0', fontSize: 12.5, color: 'var(--text-secondary)' }}
        />

        <div style={{ margin: '10px 0 14px', padding: '10px 12px', background: 'var(--bg-subtle, rgba(255,255,255,0.03))', borderRadius: 6, border: '1px solid var(--border-subtle, #333)' }}>
          <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--text-secondary)', marginBottom: 8, textTransform: 'uppercase', letterSpacing: '0.04em' }}>
            Or analyze a bundled benchmark SAR scene:
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 6 }}>
            <button
              type="button"
              className="btn-secondary"
              style={{ fontSize: 11, padding: '6px 8px', textAlign: 'left', cursor: 'pointer' }}
              disabled={busy}
              onClick={() => {
                if (submittingRef.current) return
                submittingRef.current = true
                setBusy(true)
                onSubmit(null, { detector, centerLat, centerLon, sampleName: 'demo_oil_scene_03.tif' })
                  .finally(() => {
                    submittingRef.current = false
                    setBusy(false)
                  })
              }}
            >
              🛢️ Oil Spill (Zenodo SAR)
            </button>
            <button
              type="button"
              className="btn-secondary"
              style={{ fontSize: 11, padding: '6px 8px', textAlign: 'left', cursor: 'pointer' }}
              disabled={busy}
              onClick={() => {
                if (submittingRef.current) return
                submittingRef.current = true
                setBusy(true)
                onSubmit(null, { detector, centerLat, centerLon, sampleName: 'demo_oil_scene_01.tif' })
                  .finally(() => {
                    submittingRef.current = false
                    setBusy(false)
                  })
              }}
            >
              🛢️ Oil Benchmark Scene
            </button>
            <button
              type="button"
              className="btn-secondary"
              style={{ fontSize: 11, padding: '6px 8px', textAlign: 'left', cursor: 'pointer' }}
              disabled={busy}
              onClick={() => {
                if (submittingRef.current) return
                submittingRef.current = true
                setBusy(true)
                onSubmit(null, { detector, centerLat, centerLon, sampleName: 'demo_lookalike_scene_01.tif' })
                  .finally(() => {
                    submittingRef.current = false
                    setBusy(false)
                  })
              }}
            >
              🌊 Lookalike Anomaly
            </button>
            <button
              type="button"
              className="btn-secondary"
              style={{ fontSize: 11, padding: '6px 8px', textAlign: 'left', cursor: 'pointer' }}
              disabled={busy}
              onClick={() => {
                if (submittingRef.current) return
                submittingRef.current = true
                setBusy(true)
                onSubmit(null, { detector, centerLat, centerLon, sampleName: 'demo_no_oil_scene_01.tif' })
                  .finally(() => {
                    submittingRef.current = false
                    setBusy(false)
                  })
              }}
            >
              ✅ Clean Sea (No Oil)
            </button>
          </div>
        </div>

        <div style={{ display: 'flex', gap: 14, margin: '10px 0', fontSize: 12 }}>
          <label style={{ display: 'flex', alignItems: 'center', gap: 6, cursor: 'pointer' }}>
            <input type="radio" name="detector" checked={detector === 'auto'} onChange={() => setDetector('auto')} />
            Auto (Recommended)
          </label>
          <label style={{ display: 'flex', alignItems: 'center', gap: 6, cursor: 'pointer' }}>
            <input type="radio" name="detector" checked={detector === 'ml'} onChange={() => setDetector('ml')} />
            Real ML (Dual-Pol VV/VH)
          </label>
          <label style={{ display: 'flex', alignItems: 'center', gap: 6, cursor: 'pointer' }}>
            <input type="radio" name="detector" checked={detector === 'classical'} onChange={() => setDetector('classical')} />
            Classical SAR
          </label>
        </div>
        {detector === 'auto' && (
          <p style={{ fontSize: 11.5, color: 'var(--text-secondary)', lineHeight: 1.5, margin: '4px 0 10px' }}>
            Auto Mode: Inspects input and executes deep learning ML on dual-polarization (VV+VH) images, or Classical SAR detection for single-band images.
          </p>
        )}
        {detector === 'ml' && (
          <p style={{ fontSize: 11.5, color: 'var(--signal-amber)', lineHeight: 1.5, margin: '4px 0 10px' }}>
            Strict ML Mode: Runs trained deep learning models (SpillUNet + SceneClassifierV2; provenance: ML_REAL_ZENODO_TRAINED). Requires dual-polarization (VV+VH) SAR input.
          </p>
        )}
        {detector === 'classical' && (
          <p style={{ fontSize: 11.5, color: 'var(--text-secondary)', lineHeight: 1.5, margin: '4px 0 10px' }}>
            Classical SAR Mode: Literature-standard dark-spot adaptive thresholding, morphological filtering, and texture analysis (heuristic scores).
          </p>
        )}

        <div style={{ display: 'flex', gap: 10, fontSize: 12.5, marginBottom: 4 }}>
          <label style={{ flex: 1 }}>
            Center lat
            <input
              type="number"
              step="0.01"
              value={centerLat}
              disabled={busy}
              onChange={e => {
                const val = e.target.value === '' ? '' : parseFloat(e.target.value)
                setCenterLat(val)
              }}
              style={{ width: '100%', marginTop: 4 }}
            />
          </label>
          <label style={{ flex: 1 }}>
            Center lon
            <input
              type="number"
              step="0.01"
              value={centerLon}
              disabled={busy}
              onChange={e => {
                const val = e.target.value === '' ? '' : parseFloat(e.target.value)
                setCenterLon(val)
              }}
              style={{ width: '100%', marginTop: 4 }}
            />
          </label>
        </div>

        <div style={{ display: 'flex', gap: 10, marginTop: 16, justifyContent: 'flex-end' }}>
          <button className="btn-secondary" onClick={onClose} disabled={busy}>Cancel</button>
          <button
            className="btn-run-demo"
            disabled={!fileName || busy}
            onClick={async () => {
              // Synchronous re-entrancy guard (see submittingRef comment
              // above): reject a second call outright, before doing
              // anything else, rather than relying only on the `disabled`
              // attribute which can lag one render behind a fast repeat
              // click.
              if (submittingRef.current) return
              submittingRef.current = true
              setBusy(true)
              try {
                const safeLat = centerLat !== '' && Number.isFinite(parseFloat(centerLat)) ? parseFloat(centerLat) : 19.05
                const safeLon = centerLon !== '' && Number.isFinite(parseFloat(centerLon)) ? parseFloat(centerLon) : 71.20
                await onSubmit(fileRef.current.files[0], { detector, centerLat: safeLat, centerLon: safeLon })
              } finally {
                // The modal is typically closed by the parent (via onClose,
                // triggered from onSubmit's own success/failure handling)
                // before this line ever runs, but resetting both the ref
                // and the state here is what keeps this guard correct if
                // the modal instead stays mounted (e.g. onSubmit lets the
                // user retry in place after a genuine failure).
                submittingRef.current = false
                setBusy(false)
              }
            }}
          >
            {busy ? 'Uploading…' : 'Upload & run'}
          </button>
        </div>
      </div>
    </>
  )
}
