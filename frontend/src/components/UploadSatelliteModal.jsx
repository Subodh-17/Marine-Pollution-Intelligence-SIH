import { useEffect, useRef, useState } from 'react'

export default function UploadSatelliteModal({ onClose, onSubmit }) {
  const fileRef = useRef(null)
  const [fileName, setFileName] = useState(null)
  const [detector, setDetector] = useState('ml')
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
          accept=".tif,.tiff,.png,.jpg,.jpeg"
          disabled={busy}
          onChange={e => setFileName(e.target.files[0]?.name || null)}
          style={{ margin: '10px 0', fontSize: 12.5, color: 'var(--text-secondary)' }}
        />

        <div style={{ display: 'flex', gap: 16, margin: '10px 0', fontSize: 12.5 }}>
          <label style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <input type="radio" name="detector" checked={detector === 'ml'} onChange={() => setDetector('ml')} />
            Real ML detector — dual-pol VV/VH
          </label>
          <label style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <input type="radio" name="detector" checked={detector === 'classical'} onChange={() => setDetector('classical')} />
            Classical SAR detector
          </label>
        </div>
        {detector === 'ml' && (
          <p style={{ fontSize: 11.5, color: 'var(--signal-amber)', lineHeight: 1.5, margin: '4px 0 10px' }}>
            Uses the real trained ML checkpoint (provenance: ML_REAL_ZENODO_TRAINED) on a
            dual-polarization (VV+VH) GeoTIFF. Automatically falls back to Classical SAR
            Detection if the image is not dual-polarization, the ML engine is unavailable,
            or its result does not clear the minimum confidence threshold — the response
            always states which detector actually ran.
          </p>
        )}

        <div style={{ display: 'flex', gap: 10, fontSize: 12.5, marginBottom: 4 }}>
          <label style={{ flex: 1 }}>
            Center lat
            <input type="number" step="0.01" value={centerLat} onChange={e => setCenterLat(parseFloat(e.target.value))}
              style={{ width: '100%', marginTop: 4 }} />
          </label>
          <label style={{ flex: 1 }}>
            Center lon
            <input type="number" step="0.01" value={centerLon} onChange={e => setCenterLon(parseFloat(e.target.value))}
              style={{ width: '100%', marginTop: 4 }} />
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
                await onSubmit(fileRef.current.files[0], { detector, centerLat, centerLon })
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
