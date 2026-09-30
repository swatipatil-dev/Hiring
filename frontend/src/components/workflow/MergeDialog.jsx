import { useEffect, useState } from 'react'
import api from '../../api/client'
import ConfirmDialog from './ConfirmDialog'

const LABELS = {
  first_name: 'First name', last_name: 'Last name', phone: 'Phone', linkedin_url: 'LinkedIn',
  github_url: 'GitHub', portfolio_url: 'Portfolio', current_salary: 'Current salary',
  expected_salary: 'Expected salary', notice_period: 'Notice period',
  experience_years: 'Experience (yrs)', source: 'Source', source_detail: 'Source detail',
  referrer_name: 'Referrer', referrer_email: 'Referrer email', current_stage: 'Stage', resume: 'Resume',
}

const show = (v) => (v === null || v === undefined || v === '') ? '—' : String(v).split('/').pop()

/**
 * Merge `mergedId` into `primaryId`: shows every differing field side by side and lets
 * HR choose which value survives. Nothing is deleted; the merge can be undone from
 * Workflow → Duplicates.
 */
export default function MergeDialog({ primaryId, mergedId, onClose, onMerged }) {
  const [preview, setPreview] = useState(null)
  const [choices, setChoices] = useState({})
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [ids, setIds] = useState({ primaryId, mergedId })

  useEffect(() => {
    setPreview(null); setError('')
    api.post('/hr/workflow/merge/preview', { primary_id: ids.primaryId, merged_id: ids.mergedId })
      .then(r => {
        setPreview(r.data)
        setChoices(Object.fromEntries(r.data.fields.map(f => [f.field, f.default])))
      })
      .catch(e => setError(e?.response?.data?.detail || 'Could not load the merge preview'))
  }, [ids])

  const merge = async () => {
    setBusy(true); setError('')
    try {
      await api.post('/hr/workflow/merge', {
        primary_id: ids.primaryId, merged_id: ids.mergedId, choices, reason, confirm: true,
      })
      onMerged?.()
    } catch (e) {
      const d = e?.response?.data?.detail
      setError(typeof d === 'string' ? d : (d?.blockers || []).join(' ') || 'Merge failed')
    }
    setBusy(false)
  }

  const differing = preview?.fields.filter(f => f.differs) || []

  return (
    <ConfirmDialog title="Merge duplicate applications" confirmLabel="Merge applications"
      busy={busy} disabled={!preview?.can_merge} onConfirm={merge} onCancel={onClose} maxWidth={720}>
      {!preview && !error && <p>Loading…</p>}
      {error && <p role="alert" style={{ color: '#ef4444' }}>{String(error)}</p>}
      {preview && (
        <>
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10, marginBottom: 12, flexWrap: 'wrap' }}>
            <div><div className="text-muted text-sm">Keep</div><strong>{preview.primary.email}</strong></div>
            <button className="btn btn-ghost btn-sm" onClick={() => setIds({ primaryId: ids.mergedId, mergedId: ids.primaryId })}>
              ⇄ Swap
            </button>
            <div style={{ textAlign: 'right' }}><div className="text-muted text-sm">Merge away</div><strong>{preview.merged.email}</strong></div>
          </div>
          {preview.blockers.map(b => (
            <div key={b} style={{ background: '#ef444418', color: '#b91c1c', padding: '8px 10px', borderRadius: 8, marginBottom: 8 }}>{b}</div>
          ))}
          {preview.warnings.map(w => (
            <div key={w} style={{ background: '#f59e0b18', color: '#b45309', padding: '8px 10px', borderRadius: 8, marginBottom: 8 }}>{w}</div>
          ))}
          {preview.can_merge && (
            <>
              <p className="text-sm" style={{ margin: '10px 0' }}>
                The kept application keeps its email. Choose which value to keep for each field that differs:
              </p>
              {differing.length === 0 && <p className="text-muted">No other fields differ.</p>}
              {differing.length > 0 && (
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.82rem' }}>
                  <thead><tr><th align="left">Field</th><th align="left">Keep</th><th align="left">Merge away</th></tr></thead>
                  <tbody>
                    {differing.map(f => (
                      <tr key={f.field} style={{ borderTop: '1px solid var(--border-subtle)' }}>
                        <td style={{ padding: '6px 4px' }}>{LABELS[f.field] || f.field}</td>
                        {['primary', 'merged'].map(side => (
                          <td key={side} style={{ padding: '6px 4px' }}>
                            <label style={{ display: 'flex', gap: 6, alignItems: 'center', cursor: 'pointer' }}>
                              <input type="radio" name={`m-${f.field}`} checked={choices[f.field] === side}
                                onChange={() => setChoices(c => ({ ...c, [f.field]: side }))} />
                              {show(f[side])}
                            </label>
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
              <input className="input" style={{ marginTop: 12 }} placeholder="Reason (recorded in the audit trail)"
                value={reason} onChange={e => setReason(e.target.value)} />
            </>
          )}
        </>
      )}
    </ConfirmDialog>
  )
}
