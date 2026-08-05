import React, { useEffect, useMemo, useState } from 'react'
import { AlertCircle, ArrowRight, Briefcase } from 'lucide-react'
import api from '../../api/client'

/**
 * Confirmation step for promoting a pooled resume into a real Candidate.
 *
 * The parser fills name/email/phone from the resume, but it deliberately returns
 * blanks rather than guesses — and a Candidate row requires a name and email. So HR
 * reviews and corrects here before anything is written to the ATS.
 */
export default function MoveToATSModal({ entry, activeJobs = [], onClose, onMoved }) {
  const nameParts = (entry.full_name || '').trim().split(/\s+/).filter(Boolean)

  const [firstName, setFirstName] = useState(nameParts[0] || '')
  const [lastName, setLastName] = useState(nameParts.slice(1).join(' ') || '')
  const [email, setEmail] = useState(entry.email || '')
  const [phone, setPhone] = useState(entry.phone || '')
  const [jobId, setJobId] = useState(entry.best_match?.job_id || '')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  // Best match first, then the runner-ups — same order as the record. Any remaining
  // open role follows, unscored: someone imported from the portal with no resume has
  // no matches at all, and without these they could never be moved anywhere.
  const jobOptions = useMemo(() => {
    // Jobs this person is already on. The backend 409s on a duplicate, but only after
    // submitting — knowing up front lets us disable the option instead.
    const taken = new Set((entry.applications || []).map(a => a.job_id).filter(Boolean))
    const scored = [entry.best_match, ...(entry.alternates || [])].filter(Boolean)
    const seen = new Set()
    const ranked = scored.filter(j => !seen.has(j.job_id) && seen.add(j.job_id))
    const rest = (activeJobs || [])
      .filter(j => !seen.has(j.job_id))
      .map(j => ({ ...j, ats_score: null }))
    return [...ranked, ...rest].map(j => ({ ...j, taken: taken.has(j.job_id) }))
  }, [entry, activeJobs])

  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  // An unscored entry has no best match to preselect, so fall back to the first role
  // they aren't already on — otherwise the select shows a job the form won't accept.
  useEffect(() => {
    const selected = jobOptions.find(j => j.job_id === jobId)
    if (!selected || selected.taken) {
      const first = jobOptions.find(j => !j.taken)
      if (first) setJobId(first.job_id)
    }
  }, [jobId, jobOptions])

  const emailValid = /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email.trim())
  const canSubmit = firstName.trim() && lastName.trim() && emailValid && jobId && !saving

  const submit = async () => {
    if (!canSubmit) return
    setSaving(true)
    setError('')
    try {
      const r = await api.post(`/hr/resume-pool/${entry.id}/move-to-ats`, {
        job_id: jobId,
        first_name: firstName.trim(),
        last_name: lastName.trim(),
        email: email.trim(),
        phone: phone.trim() || null,
      })
      onMoved(r.data)
    } catch (e) {
      setError(e.response?.data?.detail || 'Could not move this resume to the ATS.')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="modal-overlay" onClick={onClose} style={{ zIndex: 1100 }}>
      <div className="modal" onClick={e => e.stopPropagation()} style={{ maxWidth: 540 }}>
        <h2 style={{ fontSize: '1.15rem' }}>Move to ATS</h2>

        <p className="text-sm text-muted" style={{ marginTop: -12, marginBottom: 20 }}>
          Creates a candidate on the selected job and runs the same pipeline as an
          applicant. Check the details below — they were read from{' '}
          {entry.source === 'registered' ? 'their portal profile' : 'the resume'}.
        </p>

        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14 }}>
          <div className="input-group">
            <label>First name *</label>
            <input className="input" value={firstName} autoFocus
                   onChange={e => setFirstName(e.target.value)} placeholder="Priya" />
          </div>
          <div className="input-group">
            <label>Last name *</label>
            <input className="input" value={lastName}
                   onChange={e => setLastName(e.target.value)} placeholder="Sharma" />
          </div>
        </div>

        <div className="input-group" style={{ marginTop: 14 }}>
          <label>Email *</label>
          <input className="input" type="email" value={email}
                 onChange={e => setEmail(e.target.value)} placeholder="name@example.com" />
          {email && !emailValid && (
            <span className="text-sm" style={{ color: '#ef4444' }}>Enter a valid email address</span>
          )}
        </div>

        <div className="input-group" style={{ marginTop: 14 }}>
          <label>Phone</label>
          <input className="input" value={phone}
                 onChange={e => setPhone(e.target.value)} placeholder="Optional" />
        </div>

        <div className="input-group" style={{ marginTop: 14 }}>
          <label>Job *</label>
          <select className="select" value={jobId} onChange={e => setJobId(e.target.value)}>
            {jobOptions.length === 0 && <option value="">No open roles</option>}
            {jobOptions.map((j, i) => (
              <option key={j.job_id} value={j.job_id} disabled={j.taken}>
                {j.job_title}
                {j.ats_score == null
                  ? (j.department ? ` · ${j.department}` : '')
                  : ` · ${Math.round(j.ats_score)}%${i === 0 ? ' (best match)' : ''}`}
                {j.taken ? ' · already applied' : ''}
              </option>
            ))}
          </select>
        </div>

        {error && (
          <div style={{
            marginTop: 16, padding: '10px 12px', borderRadius: 8,
            background: '#ef444414', border: '1px solid #ef444440', color: '#b91c1c',
            display: 'flex', alignItems: 'flex-start', gap: 8, fontSize: '0.85rem',
          }}>
            <AlertCircle size={15} style={{ flexShrink: 0, marginTop: 1 }} /> {error}
          </div>
        )}

        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 10, marginTop: 24 }}>
          <button className="btn btn-secondary" onClick={onClose} disabled={saving}>Cancel</button>
          <button className="btn btn-primary" onClick={submit} disabled={!canSubmit}>
            {saving ? 'Moving…' : <><Briefcase size={15} /> Move to ATS <ArrowRight size={15} /></>}
          </button>
        </div>
      </div>
    </div>
  )
}
