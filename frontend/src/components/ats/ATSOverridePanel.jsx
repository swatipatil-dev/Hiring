import { useEffect, useState } from 'react'
import api from '../../api/client'

const EVENT_LABEL = {
  scored: 'Scored', rescored: 'Rescored', override: 'Overridden', override_cleared: 'Override removed',
}

/**
 * HR override of a resume score, with a required reason, plus the score's full history.
 * The model's score is never replaced: it stays visible beside the override, and the
 * history records who changed what, when and why.
 */
export default function ATSOverridePanel({ jobId, candidate, onChanged }) {
  const [history, setHistory] = useState(null)
  const [editing, setEditing] = useState(false)
  const [score, setScore] = useState('')
  const [reason, setReason] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState(null)
  const base = `/hr/jobs/${jobId}/candidates/${candidate.candidate_id}`

  const loadHistory = () =>
    api.get(`${base}/ats-history`).then((r) => setHistory(r.data || [])).catch(() => setHistory([]))
  useEffect(() => { loadHistory() }, [base])   // eslint-disable-line react-hooks/exhaustive-deps

  const submit = async (clear) => {
    setSaving(true); setError(null)
    try {
      if (clear) await api.delete(`${base}/ats-override`, { data: { reason: reason.trim() } })
      else await api.post(`${base}/ats-override`, { new_score: Number(score), reason: reason.trim() })
      setEditing(false); setReason(''); setScore('')
      await loadHistory()
      onChanged?.()
    } catch (e) {
      setError(e?.response?.data?.detail?.[0]?.msg || e?.response?.data?.detail || 'Could not save.')
    } finally {
      setSaving(false)
    }
  }

  const reasonOk = reason.trim().length >= 10
  const scoreOk = score !== '' && Number(score) >= 0 && Number(score) <= 100
  const label = { fontSize: '0.72rem', fontWeight: 700, color: '#6b7280', marginBottom: 6 }
  const input = { fontFamily: 'inherit', fontSize: '0.85rem', padding: '7px 9px', borderRadius: 7, border: '1px solid #d1d5db' }

  return (
    <div style={{ marginTop: 16, padding: 14, border: '1px solid #e5e7eb', borderRadius: 10, background: '#fff' }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, flexWrap: 'wrap', marginBottom: 8 }}>
        <span style={{ fontSize: '0.9rem', fontWeight: 700 }}>Score used for ranking: {Math.round(candidate.ats_score)}</span>
        {candidate.is_overridden && (
          <span style={{ fontSize: '0.8rem', color: '#6b7280' }}>
            Set by HR. The model scored <s>{Math.round(candidate.model_score)}</s>.
          </span>
        )}
        {!editing && (
          <button type="button" className="btn btn-ghost btn-sm" style={{ marginLeft: 'auto' }}
            onClick={(e) => { e.stopPropagation(); setEditing(true) }}>
            {candidate.is_overridden ? 'Change or remove override' : 'Override score'}
          </button>
        )}
      </div>

      {editing && (
        <div onClick={(e) => e.stopPropagation()} style={{ display: 'flex', flexDirection: 'column', gap: 8, marginBottom: 12 }}>
          <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: '0.8rem' }}>
            New score (0 to 100)
            <input type="number" min={0} max={100} value={score} onChange={(e) => setScore(e.target.value)} style={{ ...input, width: 110 }} />
          </label>
          <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: '0.8rem' }}>
            Reason (kept in the score history)
            <textarea rows={2} maxLength={2000} value={reason} onChange={(e) => setReason(e.target.value)}
              placeholder="What the score missed, for example experience described in the cover letter."
              style={{ ...input, resize: 'vertical' }} />
          </label>
          {error && <div role="alert" style={{ fontSize: '0.8rem', color: '#b91c1c' }}>{String(error)}</div>}
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            <button type="button" className="btn btn-primary btn-sm" disabled={!scoreOk || !reasonOk || saving} onClick={() => submit(false)}>
              {saving ? 'Saving…' : 'Save override'}
            </button>
            {candidate.is_overridden && (
              <button type="button" className="btn btn-ghost btn-sm" disabled={!reasonOk || saving} onClick={() => submit(true)}>
                Remove override
              </button>
            )}
            <button type="button" className="btn btn-ghost btn-sm" onClick={() => setEditing(false)}>Cancel</button>
            {!reasonOk && reason && <span style={{ fontSize: '0.75rem', color: '#b45309', alignSelf: 'center' }}>Reason needs at least 10 characters.</span>}
          </div>
        </div>
      )}

      <div style={label}>Score history</div>
      {history === null ? (
        <div style={{ fontSize: '0.8rem', color: '#9ca3af' }}>Loading…</div>
      ) : history.length === 0 ? (
        <div style={{ fontSize: '0.8rem', color: '#9ca3af' }}>No history recorded (scored before history was kept).</div>
      ) : (
        <ol style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: 4 }}>
          {history.map((h) => (
            <li key={h.id} style={{ fontSize: '0.8rem', color: '#374151', display: 'flex', gap: 8, flexWrap: 'wrap' }}>
              <span style={{ color: '#9ca3af', minWidth: 150 }}>{h.created_at ? new Date(h.created_at + (h.created_at.endsWith('Z') ? '' : 'Z')).toLocaleString() : ''}</span>
              <b>{EVENT_LABEL[h.event_type] || h.event_type}</b>
              <span>{h.new_effective_score != null ? `to ${Math.round(h.new_effective_score)}` : ''}</span>
              <span style={{ color: '#6b7280' }}>by {h.actor}</span>
              {h.reason && <q>{h.reason}</q>}
            </li>
          ))}
        </ol>
      )}
    </div>
  )
}
