import { useEffect, useRef } from 'react'

/**
 * A confirmation modal. Unlike window.confirm it can show what the action will
 * touch (children) and name the action on the button ("Reject 12 candidates"),
 * which is the point: the person confirms the count they can see.
 */
export default function ConfirmDialog({
  title, children, confirmLabel = 'Confirm', cancelLabel = 'Cancel',
  danger = false, busy = false, disabled = false, onConfirm, onCancel, maxWidth = 560,
}) {
  const cancelRef = useRef(null)
  // Latest props for the Escape handler, so the effects below run once. Re-running
  // the focus on every render (callers pass an inline onCancel) would pull focus out
  // of any field inside the dialog on each keystroke.
  const latest = useRef({ busy, onCancel })
  latest.current = { busy, onCancel }
  useEffect(() => { cancelRef.current?.focus() }, [])
  useEffect(() => {
    const onKey = (e) => {
      if (e.key === 'Escape' && !latest.current.busy) latest.current.onCancel?.()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  return (
    <div className="modal-overlay" onClick={() => !busy && onCancel?.()}>
      <div className="modal" role="dialog" aria-modal="true" aria-label={title}
        style={{ maxWidth }} onClick={e => e.stopPropagation()}>
        <h2 style={{ fontSize: '1.15rem', marginBottom: 14 }}>{title}</h2>
        <div style={{ fontSize: '0.88rem', color: 'var(--text-secondary)' }}>{children}</div>
        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 10, marginTop: 22 }}>
          <button ref={cancelRef} className="btn btn-secondary" onClick={onCancel} disabled={busy}>
            {cancelLabel}
          </button>
          <button className={`btn ${danger ? 'btn-danger' : 'btn-primary'}`} onClick={onConfirm}
            disabled={busy || disabled}>
            {busy ? 'Working…' : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  )
}
