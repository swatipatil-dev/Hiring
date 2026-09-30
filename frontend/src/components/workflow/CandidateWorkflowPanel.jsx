import { useCallback, useEffect, useState } from 'react'
import api from '../../api/client'
import MergeDialog from './MergeDialog'

/**
 * Source and possible duplicates for one candidate, shown in the HR stage tracker.
 */
export default function CandidateWorkflowPanel({ candidateId, source, onChanged }) {
  const [sources, setSources] = useState([])
  const [form, setForm] = useState(null)
  const [saved, setSaved] = useState(source || null)
  const [matches, setMatches] = useState(null)
  const [mergeWith, setMergeWith] = useState(null)
  const [msg, setMsg] = useState('')

  const loadMatches = useCallback(() => {
    api.get(`/hr/workflow/candidates/${candidateId}/duplicates`)
      .then(r => setMatches(r.data?.matches || [])).catch(() => setMatches([]))
  }, [candidateId])

  useEffect(() => {
    api.get('/hr/workflow/sources').then(r => setSources(r.data?.sources || [])).catch(() => {})
    loadMatches()
  }, [loadMatches])
  useEffect(() => { setSaved(source || null) }, [source])

  const save = async () => {
    setMsg('')
    try {
      const r = await api.patch(`/hr/workflow/candidates/${candidateId}/source`, form)
      setSaved(r.data); setForm(null); onChanged?.()
    } catch (e) { setMsg(e?.response?.data?.detail || 'Could not save the source') }
  }

  return (
    <div style={{ borderTop: '1px solid var(--border-subtle)', marginTop: 16, paddingTop: 14, fontSize: '0.85rem' }}>
      <div className="flex-between" style={{ marginBottom: 8 }}>
        <strong>Source</strong>
        {!form && (
          <button className="btn btn-ghost btn-sm" onClick={() => setForm({
            source: saved?.source || 'direct', source_detail: saved?.source_detail || '',
            referrer_name: saved?.referrer_name || '', referrer_email: saved?.referrer_email || '',
          })}>Edit</button>
        )}
      </div>
      {!form && (
        <div>
          {saved?.source_label || 'Not recorded'}
          {saved?.source_detail ? ` · ${saved.source_detail}` : ''}
          {saved?.referrer_name ? ` · referred by ${saved.referrer_name}` : ''}
        </div>
      )}
      {form && (
        <div style={{ display: 'grid', gap: 8 }}>
          <select className="select" aria-label="Source" value={form.source} onChange={e => setForm(f => ({ ...f, source: e.target.value }))}>
            {sources.map(s => <option key={s.key} value={s.key}>{s.label}</option>)}
          </select>
          <input className="input" placeholder="Portal, agency or campaign (e.g. LinkedIn)" value={form.source_detail}
            onChange={e => setForm(f => ({ ...f, source_detail: e.target.value }))} />
          {form.source === 'referral' && (
            <div style={{ display: 'flex', gap: 8 }}>
              <input className="input" placeholder="Referrer name" value={form.referrer_name}
                onChange={e => setForm(f => ({ ...f, referrer_name: e.target.value }))} />
              <input className="input" placeholder="Referrer email" value={form.referrer_email}
                onChange={e => setForm(f => ({ ...f, referrer_email: e.target.value }))} />
            </div>
          )}
          <div style={{ display: 'flex', gap: 8 }}>
            <button className="btn btn-primary btn-sm" onClick={save}>Save</button>
            <button className="btn btn-secondary btn-sm" onClick={() => setForm(null)}>Cancel</button>
          </div>
          {msg && <span role="alert" style={{ color: '#ef4444' }}>{String(msg)}</span>}
        </div>
      )}

      <strong style={{ display: 'block', marginTop: 14, marginBottom: 6 }}>Possible duplicates</strong>
      {matches === null && <span className="text-muted">Checking…</span>}
      {matches?.length === 0 && <span className="text-muted">None found.</span>}
      {matches?.map(m => (
        <div key={m.candidate_id} className="flex-between" style={{ padding: '6px 0', borderBottom: '1px solid var(--border-subtle)', gap: 8 }}>
          <div>
            <div>{m.name} <span className="text-muted">· {m.email}</span></div>
            <div className="text-muted" style={{ fontSize: '0.75rem' }}>
              {m.same_job ? 'Same job' : `Other job: ${m.job_title}`} · matched on {m.reasons.join(', ')}
              {m.confidence === 'weak' ? ' (weak)' : ''}
            </div>
          </div>
          {m.can_merge && (
            <button className="btn btn-secondary btn-sm" onClick={() => setMergeWith(m.candidate_id)}>Merge…</button>
          )}
        </div>
      ))}
      {mergeWith && (
        <MergeDialog primaryId={candidateId} mergedId={mergeWith} onClose={() => setMergeWith(null)}
          onMerged={() => { setMergeWith(null); loadMatches(); onChanged?.() }} />
      )}
    </div>
  )
}
