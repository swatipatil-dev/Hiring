import { useEffect, useState } from 'react'
import api from '../../api/client'
import ConfirmDialog from './ConfirmDialog'

const DIMENSIONS = [
  ['technical_score', 'Technical skills'],
  ['communication_score', 'Communication'],
  ['problem_solving_score', 'Problem solving'],
  ['cultural_fit_score', 'Cultural fit'],
]
const RECS = [
  ['strong_hire', 'Strong hire'], ['hire', 'Hire'], ['proceed', 'Proceed to next round'],
  ['no_hire', 'No hire'], ['strong_no_hire', 'Strong no hire'],
]
const REC_LABEL = Object.fromEntries(RECS)
const STATUS_LABEL = { submitted: 'Submitted', draft: 'Draft in progress', not_started: 'Not started' }

// Stored 0–100; the form works in 0–10 like the rest of the app.
const toForm = (card) => ({
  ...Object.fromEntries(DIMENSIONS.map(([k]) => [k, card?.[k] != null ? card[k] / 10 : ''])),
  strengths: (card?.strengths || []).join('\n'),
  weaknesses: (card?.weaknesses || []).join('\n'),
  recommendations: card?.recommendations || '',
  hire_recommendation: card?.hire_recommendation || '',
  notes: card?.notes || '',
})
const toPayload = (f) => ({
  ...Object.fromEntries(DIMENSIONS.map(([k]) => [k, f[k] === '' ? null : Number(f[k])])),
  strengths: f.strengths, weaknesses: f.weaknesses, recommendations: f.recommendations,
  hire_recommendation: f.hire_recommendation || null, notes: f.notes,
})

/**
 * Independent panel scorecards. Each panellist scores alone; colleagues' scorecards
 * stay hidden until you submit your own, and a submitted scorecard is locked.
 */
export default function ScorecardPanel({ interviewId, onClose }) {
  const [data, setData] = useState(null)
  const [form, setForm] = useState(toForm(null))
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState(null)
  const [confirming, setConfirming] = useState(false)

  const apply = (d) => { setData(d); setForm(toForm(d.my_scorecard)) }
  useEffect(() => {
    api.get(`/scorecards/interviews/${interviewId}`).then(r => apply(r.data))
      .catch(e => setMsg({ error: e?.response?.data?.detail || 'Could not load scorecards' }))
  }, [interviewId])

  const send = async (submit) => {
    setBusy(true); setMsg(null)
    try {
      const url = `/scorecards/interviews/${interviewId}/mine${submit ? '/submit' : ''}`
      const r = submit ? await api.post(url, toPayload(form)) : await api.put(url, toPayload(form))
      apply(r.data)
      setMsg({ ok: submit ? 'Submitted. Your colleagues’ scorecards are now visible below.' : 'Draft saved — only you can see it.' })
    } catch (e) {
      const d = e?.response?.data?.detail
      setMsg({ error: typeof d === 'string' ? d : `${d?.message || 'Could not save'} ${d?.missing ? `Missing: ${d.missing.join(', ').replace(/_score/g, '').replace(/_/g, ' ')}` : ''}` })
    }
    setBusy(false); setConfirming(false)
  }

  const set = (k) => (e) => setForm(f => ({ ...f, [k]: e.target.value }))

  return (
    <div className="modal-overlay" onClick={() => !busy && onClose?.()}>
      <div className="modal" role="dialog" aria-modal="true" aria-label="Scorecards" style={{ maxWidth: 720 }} onClick={e => e.stopPropagation()}>
        <div className="flex-between" style={{ marginBottom: 12 }}>
          <div>
            <h2 style={{ fontSize: '1.15rem', margin: 0 }}>Scorecards</h2>
            {data && <div className="text-sm text-muted">{data.candidate_name} · {data.job_title}</div>}
          </div>
          <button className="btn btn-ghost btn-sm" onClick={onClose} aria-label="Close">✕</button>
        </div>
        {!data && !msg && <p className="text-muted">Loading…</p>}
        {msg?.error && <p role="alert" style={{ color: '#dc2626' }}>{String(msg.error)}</p>}
        {msg?.ok && <p style={{ color: '#16a34a' }}>{msg.ok}</p>}

        {data && (
          <>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 14 }}>
              {data.panel.map(p => (
                <span key={p.email} className={`badge ${p.status === 'submitted' ? 'badge-green' : p.status === 'draft' ? 'badge-amber' : 'badge-neutral'}`}>
                  {p.name}: {STATUS_LABEL[p.status]}
                </span>
              ))}
            </div>

            {data.is_panelist && data.can_edit && (
              <div className="card" style={{ padding: 16, marginBottom: 16 }}>
                <strong style={{ display: 'block', marginBottom: 10 }}>Your scorecard</strong>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 10 }}>
                  {DIMENSIONS.map(([k, label]) => (
                    <label key={k} style={{ fontSize: '0.8rem' }}>{label} (0–10)
                      <input className="input" type="number" min={0} max={10} step={0.5} value={form[k]} onChange={set(k)} />
                    </label>
                  ))}
                </div>
                <label style={{ fontSize: '0.8rem', display: 'block', marginTop: 10 }}>Strengths (one per line)
                  <textarea className="textarea" rows={2} value={form.strengths} onChange={set('strengths')} />
                </label>
                <label style={{ fontSize: '0.8rem', display: 'block', marginTop: 8 }}>Concerns (one per line)
                  <textarea className="textarea" rows={2} value={form.weaknesses} onChange={set('weaknesses')} />
                </label>
                <label style={{ fontSize: '0.8rem', display: 'block', marginTop: 8 }}>Summary
                  <textarea className="textarea" rows={2} value={form.recommendations} onChange={set('recommendations')} />
                </label>
                <label style={{ fontSize: '0.8rem', display: 'block', marginTop: 8 }}>Recommendation
                  <select className="select" value={form.hire_recommendation} onChange={set('hire_recommendation')}>
                    <option value="">Choose…</option>
                    {RECS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
                  </select>
                </label>
                <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end', marginTop: 14 }}>
                  <button className="btn btn-secondary btn-sm" onClick={() => send(false)} disabled={busy}>Save draft</button>
                  <button className="btn btn-primary btn-sm" onClick={() => setConfirming(true)} disabled={busy}>Submit scorecard</button>
                </div>
              </div>
            )}

            {data.my_scorecard?.status === 'submitted' && <CardView card={data.my_scorecard} title="Your scorecard (submitted, locked)" />}

            {!data.others_visible && (
              <div style={{ padding: '10px 12px', borderRadius: 8, background: 'var(--bg-hover)', fontSize: '0.84rem' }}>
                🔒 {data.hidden_reason}
              </div>
            )}
            {data.others_visible && data.summary && (
              <p className="text-sm" style={{ margin: '8px 0' }}>
                {data.summary.submitted} submitted · average {data.summary.average_overall ?? '—'}/100 ·{' '}
                {Object.entries(data.summary.recommendations).map(([k, v]) => `${REC_LABEL[k] || k}: ${v}`).join(', ')}
              </p>
            )}
            {data.others_visible && data.others?.length === 0 && <p className="text-muted text-sm">No other submitted scorecards yet.</p>}
            {data.others?.map(c => <CardView key={c.id} card={c} title={c.panelist_name} />)}
          </>
        )}

        {confirming && (
          <ConfirmDialog title="Submit your scorecard?" confirmLabel="Submit and lock" busy={busy}
            onConfirm={() => send(true)} onCancel={() => setConfirming(false)}>
            Once submitted it can’t be changed. You’ll then see the other panellists’ scorecards.
          </ConfirmDialog>
        )}
      </div>
    </div>
  )
}

function CardView({ card, title }) {
  return (
    <div className="card" style={{ padding: 14, marginBottom: 10 }}>
      <div className="flex-between" style={{ marginBottom: 6 }}>
        <strong>{title}</strong>
        <span className="text-sm">{REC_LABEL[card.hire_recommendation] || '—'} · {card.overall_score ?? '—'}/100</span>
      </div>
      <div className="text-sm text-muted" style={{ marginBottom: 6 }}>
        {DIMENSIONS.map(([k, l]) => `${l}: ${card[k] != null ? card[k] / 10 : '—'}`).join(' · ')}
      </div>
      {card.strengths?.length > 0 && <div className="text-sm"><strong>Strengths:</strong> {card.strengths.join('; ')}</div>}
      {card.weaknesses?.length > 0 && <div className="text-sm"><strong>Concerns:</strong> {card.weaknesses.join('; ')}</div>}
      {card.recommendations && <div className="text-sm" style={{ marginTop: 4 }}>{card.recommendations}</div>}
    </div>
  )
}
