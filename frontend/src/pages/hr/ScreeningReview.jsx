import { useEffect, useState } from 'react'
import api from '../../api/client'

/**
 * Check a job's resume ranking against independent review.
 *
 * Review: draw a random sample of applicants BELOW the cutoff and judge each one from the
 * resume alone (no score, no rank), with a reason. Report: how well the ranking agrees
 * with reviewers, how many suitable people it put below the cutoff, and each one it
 * missed, with the cause found when someone looked into it.
 */

const CAUSES = [
  ['parse_failure', 'Resume could not be read properly'],
  ['synonym_not_recognised', 'Skill written differently (not recognised)'],
  ['experience_not_detected', 'Experience not detected'],
  ['other', 'Other (describe)'],
]

const box = { border: '1px solid #e5e7eb', borderRadius: 10, padding: '12px 14px', background: '#fff' }
const input = { fontFamily: 'inherit', fontSize: '0.85rem', padding: '7px 9px', borderRadius: 7, border: '1px solid #d1d5db' }
const pct = (x) => (x == null ? '—' : `${Math.round(x * 100)}%`)

function ReviewCard({ jobId, sampleId, cand, onDone }) {
  const [judgement, setJudgement] = useState(cand.my_judgement)
  const [reason, setReason] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState(null)
  const done = !!cand.my_judgement

  const save = async () => {
    setSaving(true); setError(null)
    try {
      await api.post(`/hr/jobs/${jobId}/candidates/${cand.candidate_id}/screening-review`,
        { judgement, reason: reason.trim(), sample_id: sampleId })
      onDone(cand.candidate_id, judgement)
    } catch (e) {
      setError(e?.response?.data?.detail?.[0]?.msg || e?.response?.data?.detail || 'Could not save.')
    } finally { setSaving(false) }
  }

  return (
    <div style={{ ...box, display: 'flex', flexDirection: 'column', gap: 8 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 8 }}>
        <b style={{ fontSize: '0.92rem' }}>{cand.name}</b>
        {cand.resume_path
          ? <a href={`/${cand.resume_path}`} target="_blank" rel="noreferrer" style={{ fontSize: '0.8rem' }}>Open resume</a>
          : <span style={{ fontSize: '0.8rem', color: '#9ca3af' }}>No resume file</span>}
      </div>
      {done ? (
        <div style={{ fontSize: '0.82rem', color: '#374151' }}>
          Reviewed: <b>{cand.my_judgement === 'suitable' ? 'Suitable' : 'Not suitable'}</b>
        </div>
      ) : (
        <>
          <div role="radiogroup" aria-label={`Judgement for ${cand.name}`} style={{ display: 'flex', gap: 6 }}>
            {[['suitable', 'Suitable'], ['not_suitable', 'Not suitable']].map(([k, label]) => (
              <button key={k} type="button" role="radio" aria-checked={judgement === k} onClick={() => setJudgement(k)}
                style={{ ...input, cursor: 'pointer', fontWeight: 700,
                  background: judgement === k ? (k === 'suitable' ? '#dcfce7' : '#f3f4f6') : '#fff',
                  borderColor: judgement === k ? (k === 'suitable' ? '#16a34a' : '#6b7280') : '#d1d5db' }}>
                {label}
              </button>
            ))}
          </div>
          <textarea rows={2} maxLength={2000} value={reason} onChange={(e) => setReason(e.target.value)}
            placeholder="Why? For example, relevant internship in the same domain." style={{ ...input, resize: 'vertical' }} />
          {error && <div role="alert" style={{ fontSize: '0.8rem', color: '#b91c1c' }}>{String(error)}</div>}
          <button type="button" className="btn btn-primary btn-sm" style={{ alignSelf: 'flex-start' }}
            disabled={!judgement || reason.trim().length < 10 || saving} onClick={save}>
            {saving ? 'Saving…' : 'Save judgement'}
          </button>
        </>
      )}
    </div>
  )
}

function MissedRow({ jobId, m, onSaved }) {
  const [cause, setCause] = useState(m.investigation?.cause || m.suggested_cause)
  const [notes, setNotes] = useState(m.investigation?.notes || '')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState(null)
  const save = async () => {
    setSaving(true); setError(null)
    try {
      await api.post(`/hr/jobs/${jobId}/candidates/${m.candidate_id}/missed-investigation`, { cause, notes })
      onSaved()
    } catch (e) {
      setError(e?.response?.data?.detail || 'Could not save.')
    } finally { setSaving(false) }
  }
  return (
    <div style={{ ...box, display: 'flex', flexDirection: 'column', gap: 6 }}>
      <div style={{ display: 'flex', gap: 8, alignItems: 'baseline', flexWrap: 'wrap' }}>
        <b>{m.name}</b>
        <span style={{ fontSize: '0.8rem', color: '#6b7280' }}>
          {m.parse_status === 'needs_review' ? 'Not ranked (resume unreadable)' : `Score ${m.score != null ? Math.round(m.score) : '—'}, rank ${m.rank_at_review ?? '—'}`}
          {m.blind ? ', found in a blind sample' : ''}
        </span>
      </div>
      <q style={{ fontSize: '0.82rem', color: '#374151' }}>{m.reviewer_reason}</q>
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
        <label style={{ fontSize: '0.8rem' }}>
          Cause{' '}
          <select value={cause} onChange={(e) => setCause(e.target.value)} style={input}>
            {CAUSES.map(([k, label]) => <option key={k} value={k}>{label}{k === m.suggested_cause && !m.investigation ? ' (suggested)' : ''}</option>)}
          </select>
        </label>
        <input value={notes} onChange={(e) => setNotes(e.target.value)} placeholder="What you found" style={{ ...input, flex: 1, minWidth: 180 }} />
        <button type="button" className="btn btn-ghost btn-sm" onClick={save} disabled={saving || (cause === 'other' && !notes.trim())}>
          {m.investigation ? 'Update' : 'Record cause'}
        </button>
      </div>
      {error && <div role="alert" style={{ fontSize: '0.8rem', color: '#b91c1c' }}>{String(error)}</div>}
    </div>
  )
}

export default function ScreeningReview({ job, onClose }) {
  const [tab, setTab] = useState('review')
  const [cutoff, setCutoff] = useState(50)
  const [size, setSize] = useState(10)
  const [sample, setSample] = useState(null)
  const [report, setReport] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  const loadReport = () =>
    api.get(`/hr/jobs/${job.id}/screening-report?cutoff_score=${cutoff}`)
      .then((r) => setReport(r.data)).catch(() => setReport(null))
  useEffect(() => { if (tab === 'report') loadReport() }, [tab, cutoff])   // eslint-disable-line react-hooks/exhaustive-deps

  const draw = async () => {
    setBusy(true); setError(null)
    try {
      const r = await api.post(`/hr/jobs/${job.id}/review-samples`, { cutoff_score: Number(cutoff), size: Number(size) })
      setSample(r.data)
    } catch (e) {
      setError(e?.response?.data?.detail || 'Could not draw a sample.')
    } finally { setBusy(false) }
  }

  const markDone = (cid, judgement) =>
    setSample((s) => ({ ...s, candidates: s.candidates.map((c) => (c.candidate_id === cid ? { ...c, my_judgement: judgement } : c)) }))

  const tabBtn = (k, label) => (
    <button type="button" onClick={() => setTab(k)} aria-pressed={tab === k}
      style={{ ...input, cursor: 'pointer', fontWeight: 700, background: tab === k ? '#111827' : '#fff', color: tab === k ? '#fff' : '#374151' }}>
      {label}
    </button>
  )

  return (
    <div className="modal-overlay" onClick={onClose} style={{ zIndex: 1200 }}>
      <div className="modal" onClick={(e) => e.stopPropagation()} role="dialog" aria-modal="true" aria-label="Check this ranking"
        style={{ maxWidth: 760, width: '100%', maxHeight: '90vh', overflow: 'auto', display: 'flex', flexDirection: 'column', gap: 14 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <h2 style={{ margin: 0, fontSize: '1.15rem' }}>Check this ranking: {job.title}</h2>
          <button className="btn btn-ghost btn-sm" onClick={onClose} aria-label="Close">×</button>
        </div>
        <p style={{ margin: 0, fontSize: '0.86rem', color: '#4b5563', lineHeight: 1.55 }}>
          Judge a random sample of applicants the ranking put below the cutoff, from their resume alone. If you find
          suitable people there, the report shows how many more there are likely to be and helps you find out why.
        </p>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          {tabBtn('review', 'Blind review')}{tabBtn('report', 'Report')}
          <label style={{ fontSize: '0.82rem', marginLeft: 'auto' }}>
            Shortlist cutoff score{' '}
            <input type="number" min={0} max={100} value={cutoff} onChange={(e) => setCutoff(e.target.value)} style={{ ...input, width: 70 }} />
          </label>
        </div>

        {tab === 'review' && (
          <>
            {!sample ? (
              <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                <label style={{ fontSize: '0.82rem' }}>
                  Sample size{' '}
                  <input type="number" min={1} max={50} value={size} onChange={(e) => setSize(e.target.value)} style={{ ...input, width: 70 }} />
                </label>
                <button className="btn btn-primary btn-sm" onClick={draw} disabled={busy}>{busy ? 'Drawing…' : 'Draw a sample'}</button>
                {error && <span role="alert" style={{ fontSize: '0.8rem', color: '#b91c1c' }}>{String(error)}</span>}
              </div>
            ) : sample.candidates.length === 0 ? (
              <div style={{ fontSize: '0.86rem', color: '#6b7280' }}>Nobody below the cutoff is left to review.</div>
            ) : (
              <>
                <div style={{ fontSize: '0.82rem', color: '#6b7280' }}>
                  {sample.candidates.filter((c) => c.my_judgement).length} of {sample.candidates.length} reviewed,
                  drawn from {sample.below_cutoff_population} below the cutoff. Scores are hidden on purpose.
                </div>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))', gap: 10 }}>
                  {sample.candidates.map((c) => (
                    <ReviewCard key={c.candidate_id} jobId={job.id} sampleId={sample.sample_id} cand={c} onDone={markDone} />
                  ))}
                </div>
              </>
            )}
          </>
        )}

        {tab === 'report' && (
          !report ? <div style={{ fontSize: '0.86rem', color: '#6b7280' }}>Loading…</div> : (
            <>
              {report.reviewed === 0 ? (
                <div style={{ fontSize: '0.86rem', color: '#6b7280' }}>No judgements recorded yet. Start with a blind review.</div>
              ) : (
                <>
                  <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 10 }}>
                    {[
                      ['Judged', report.reviewed],
                      ['Agreement with ranking', pct(report.agreement)],
                      ["Cohen's kappa", report.kappa ?? '—'],
                      ['Suitable people below cutoff', pct(report.miss_rate)],
                    ].map(([k, v]) => (
                      <div key={k} style={box}><div style={{ fontSize: '0.75rem', color: '#6b7280' }}>{k}</div><div style={{ fontSize: '1.2rem', fontWeight: 800 }}>{v}</div></div>
                    ))}
                  </div>
                  {report.blind_sample.estimated_missed_below_cutoff != null && (
                    <div style={{ ...box, background: '#fffbeb', borderColor: '#fde68a', fontSize: '0.86rem' }}>
                      In the blind sample, {report.blind_sample.missed} of {report.blind_sample.reviewed} applicants below the
                      cutoff were suitable. Across all {report.population.below_cutoff} below the cutoff, that suggests about{' '}
                      <b>{report.blind_sample.estimated_missed_below_cutoff}</b> suitable people the ranking missed.
                    </div>
                  )}
                  <table style={{ borderCollapse: 'collapse', fontSize: '0.84rem' }}>
                    <thead><tr><th /><th style={{ padding: '4px 10px' }}>Reviewer: suitable</th><th style={{ padding: '4px 10px' }}>Reviewer: not suitable</th></tr></thead>
                    <tbody>
                      <tr><th style={{ textAlign: 'left', padding: '4px 10px' }}>Above cutoff</th><td style={{ padding: '4px 10px' }}>{report.confusion.suitable_above}</td><td style={{ padding: '4px 10px' }}>{report.confusion.not_suitable_above}</td></tr>
                      <tr><th style={{ textAlign: 'left', padding: '4px 10px' }}>Below cutoff</th><td style={{ padding: '4px 10px', fontWeight: 800, color: '#b45309' }}>{report.confusion.suitable_below}</td><td style={{ padding: '4px 10px' }}>{report.confusion.not_suitable_below}</td></tr>
                    </tbody>
                  </table>
                </>
              )}
              <h3 style={{ margin: '6px 0 0', fontSize: '0.95rem' }}>Missed suitable candidates ({report.missed.length})</h3>
              {report.missed.length === 0
                ? <div style={{ fontSize: '0.84rem', color: '#6b7280' }}>None found so far.</div>
                : report.missed.map((m) => <MissedRow key={m.candidate_id} jobId={job.id} m={m} onSaved={loadReport} />)}
            </>
          )
        )}
      </div>
    </div>
  )
}
