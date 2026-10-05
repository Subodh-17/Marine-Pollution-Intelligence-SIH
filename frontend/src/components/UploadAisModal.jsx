import { useEffect, useRef, useState } from 'react'

export default function UploadAisModal({ onClose, onSubmit }) {
  const fileRef = useRef(null)
  const [fileName, setFileName] = useState(null)
  const [busy, setBusy] = useState(false)

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
        aria-labelledby="upload-ais-title"
        style={{
          position: 'fixed', top: '50%', left: '50%', transform: 'translate(-50%,-50%)',
          zIndex: 902, width: 440, maxWidth: '92vw',
        }}>
        <h3 id="upload-ais-title" style={{ marginTop: 0, fontFamily: 'var(--font-display)' }}>Use your own AIS data</h3>
        <p style={{ fontSize: 12.5, color: 'var(--text-secondary)', lineHeight: 1.6 }}>
          Upload a MarineCadastre-format AIS CSV (or any CSV with MMSI / timestamp / lat / lon
          columns — common header aliases are auto-detected). It will be combined with the
          synthetic SAR detection scene to run a full investigation.
        </p>
        <input
          ref={fileRef}
          type="file"
          accept=".csv"
          onChange={e => setFileName(e.target.files[0]?.name || null)}
          style={{ margin: '12px 0', fontSize: 12.5, color: 'var(--text-secondary)' }}
        />
        <div style={{ display: 'flex', gap: 10, marginTop: 16, justifyContent: 'flex-end' }}>
          <button className="btn-secondary" onClick={onClose}>Cancel</button>
          <button
            className="btn-run-demo"
            disabled={!fileName || busy}
            onClick={async () => {
              setBusy(true)
              await onSubmit(fileRef.current.files[0])
              setBusy(false)
            }}
          >
            {busy ? 'Uploading…' : 'Upload & run'}
          </button>
        </div>
      </div>
    </>
  )
}
