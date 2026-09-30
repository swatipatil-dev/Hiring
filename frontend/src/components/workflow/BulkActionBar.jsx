import { useEffect, useState } from 'react'
import api from '../../api/client'
import ConfirmDialog from './ConfirmDialog'

const ACTIONS = [
  { key: 'shortlist', label: 'Shortlist' },
  { key: 'reject', label: 'Reject' },
  { key: 'move_stage', label: 'Move to stage…' },
  { key: 'add_note', label: 'Add internal note' },
]

const errText = (e) => {
  const d = e?.response?.data?.detail
  return typeof d === 'string' ? d : d?.message || 'Something went wrong'
}

/**
 * Bulk actions on a selection of candidates: preview (what will happen to whom),
 * then confirm the exact count. The backend refuses to execute a count other than
 * the one previewed, and records the batch in the audit history.
 */
export default function BulkActionBar({ selectedIds, onClear, onDone }) {
  const [action, setAction] = useState('shortlist')
  const [target, setTarget] = useState('')
  const [note, setNote] = useState('')
  const [notify, setNotify] = useState(true)
  const [stages, setStages] = useState([])
  const [preview, setPreview] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [result, setResult] = useState(null)

  useEffect(() => {
    api.get('/hr/workflow/stages')
      .then(r => setStages((r.data?.stages || []).filter(s => s.is_active && !['withdrawn', 'merged'].includes(s.key))))
      .catch(() => {})
  }, [])

  const runPreview = async () => {
    setError(''); setBusy(true)
    try {
      const r = await api.post('/hr/workflow/bulk/preview', {
        action, candidate_ids: selectedIds,
        target_stage: action === 'move_stage' ? target : undefined,
        note: note || undefined, notify_candidates: notify,
      })
      setPreview(r.data)
    } catch (e) { setError(errText(e)) }
    setBusy(false)
  }

  const execute = async () => {
    setBusy(true)
    try {
      const r = await api.post(`/hr/workflow/bulk/${preview.id}/execute`,
        { confirm_count: preview.eligible_count })
      setPreview(null)
      setResult(r.data)
      onDone?.(r.data)
    } catch (e) { setError(errText(e)); setPreview(null) }
    setBusy(false)
  }

  const cancelPreview = () => {
    if (preview) api.post(`/hr/workflow/bulk/${preview.id}/cancel`).catch(() => {})
    setPreview(null)
  }

  const needsTarget = action === 'move_stage' && !target
  const needsNote = action === 'add_note' && !note.trim()
  const actionLabel = ACTIONS.find(a => a.key === action)?.label.replace('…', '')
  const n = preview?.eligible_count || 0

  return (
    <div className="card" style={{ padding: '12px 16px', marginBottom: 16, display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap', borderColor: 'var(--accent-primary)' }}>
      <strong style={{ fontSize: '0.88rem' }}>{selectedIds.length} selected</strong>
      <select className="select" aria-label="Bulk action" value={action} onChange={e => setAction(e.target.value)} style={{ flex: '0 1 190px' }}>
        {ACTIONS.map(a => <option key={a.key} value={a.key}>{a.label}</option>)}
      </select>
      {action === 'move_stage' && (
        <select className="select" aria-label="Target stage" value={target} onChange={e => setTarget(e.target.value)} style={{ flex: '0 1 190px' }}>
          <option value="">Choose stage…</option>
          {stages.map(s => <option key={s.key} value={s.key}>{s.label}</option>)}
        </select>
      )}
      <input className="input" placeholder={action === 'add_note' ? 'Note (required)' : 'Note (optional)'}
        value={note} onChange={e => setNote(e.target.value)} style={{ flex: '1 1 180px' }} />
      {action === 'reject' && (
        <label style={{ fontSize: '0.8rem', display: 'flex', gap: 6, alignItems: 'center' }}>
          <input type="checkbox" checked={notify} onChange={e => setNotify(e.target.checked)} /> Email candidates
        </label>
      )}
      <button className="btn btn-primary btn-sm" onClick={runPreview} disabled={busy || needsTarget || needsNote}>
        Preview
      </button>
      <button className="btn btn-ghost btn-sm" onClick={onClear}>Clear</button>
      {error && <span role="alert" style={{ color: '#ef4444', fontSize: '0.8rem', width: '100%' }}>{error}</span>}
      {result && !preview && (
        <span style={{ color: '#16a34a', fontSize: '0.8rem', width: '100%' }}>
          {result.action_label}: {result.affected_count} updated
          {result.rejection_emails?.failed ? ` · ${result.rejection_emails.failed} email(s) failed` : ''}
        </span>
      )}

      {preview && (
        <ConfirmDialog
          title={`${actionLabel}: review before applying`}
          confirmLabel={n ? `${actionLabel} ${n} candidate${n === 1 ? '' : 's'}` : 'Nothing to apply'}
          danger={action === 'reject'} busy={busy} disabled={!n}
          onConfirm={execute} onCancel={cancelPreview}>
          <p style={{ marginBottom: 10 }}>
            <strong>{n}</strong> of {preview.requested_count} selected will be updated
            {preview.preview?.target_stage ? <> to <strong>{preview.preview.target_stage.replace(/_/g, ' ')}</strong></> : null}.
          </p>
          {(preview.preview?.warnings || []).map(w => (
            <div key={w} style={{ background: '#f59e0b18', color: '#b45309', padding: '8px 10px', borderRadius: 8, marginBottom: 8, fontSize: '0.82rem' }}>{w}</div>
          ))}
          {preview.preview?.skipped?.length > 0 && (
            <details style={{ marginTop: 8 }}>
              <summary style={{ cursor: 'pointer' }}>{preview.preview.skipped.length} will be skipped</summary>
              <ul style={{ margin: '8px 0 0 18px', fontSize: '0.8rem' }}>
                {preview.preview.skipped.map(s => <li key={s.candidate_id}>{s.name || s.candidate_id}: {s.reason}</li>)}
              </ul>
            </details>
          )}
          {n > 0 && (
            <details style={{ marginTop: 8 }}>
              <summary style={{ cursor: 'pointer' }}>Show the {n} candidate{n === 1 ? '' : 's'}</summary>
              <ul style={{ margin: '8px 0 0 18px', fontSize: '0.8rem' }}>
                {preview.preview.eligible.map(e => <li key={e.candidate_id}>{e.name} <span className="text-muted">({(e.from_stage || '').replace(/_/g, ' ')})</span></li>)}
              </ul>
            </details>
          )}
          <p className="text-muted" style={{ fontSize: '0.76rem', marginTop: 12 }}>
            This preview expires in 15 minutes. The batch is recorded in Workflow → Bulk history.
          </p>
        </ConfirmDialog>
      )}
    </div>
  )
}
