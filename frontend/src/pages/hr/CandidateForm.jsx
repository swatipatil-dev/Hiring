import React, { useEffect, useState } from 'react'
import { AlertCircle, Save } from 'lucide-react'
import api from '../../api/client'

const STAGES = [
  { value: 'new', label: 'New prospect' },
  { value: 'ongoing', label: 'Ongoing' },
  { value: 'archived', label: 'Archived' },
]

const errText = (e, fallback) => {
  const d = e?.response?.data?.detail
  if (typeof d === 'string' && d) return d
  if (Array.isArray(d) && d.length) {
    const parts = d.map(x => (typeof x === 'string' ? x : x?.msg)).filter(Boolean)
    if (parts.length) return parts.join('; ')
  }
  return fallback
}

/**
 * Add or edit a person in the talent pool.
 *
 * Only a name is required. Sourcing usually starts with less than a full record — a
 * name off a conference badge, a referral with no email yet — and demanding a complete
 * profile up front would push HR back into a spreadsheet.
 */
export default function CandidateForm({ entry, onClose, onSaved }) {
  const editing = Boolean(entry?.id)
  const [form, setForm] = useState({
    full_name: entry?.full_name || '',
    email: entry?.email || '',
    phone: entry?.phone || '',
    job_role: entry?.job_role || '',
    college: entry?.college || '',
    year_of_passing: entry?.year_of_passing || '',
    source_channel: entry?.source_channel || '',
    notes: entry?.notes || '',
    stage: entry?.stage || 'new',
    skills: (entry?.extracted_skills || []).join(', '),
  })
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const set = (k, v) => setForm(f => ({ ...f, [k]: v }))
  const canSave = form.full_name.trim() && !saving

  const submit = async () => {
    if (!canSave) return
    setSaving(true)
    setError('')

    const body = {
      full_name: form.full_name.trim(),
      email: form.email.trim() || null,
      phone: form.phone.trim() || null,
      job_role: form.job_role.trim() || null,
      college: form.college.trim() || null,
      year_of_passing: form.year_of_passing ? Number(form.year_of_passing) : null,
      source_channel: form.source_channel.trim() || null,
      notes: form.notes.trim() || null,
      stage: form.stage,
      skills: form.skills.split(',').map(s => s.trim()).filter(Boolean),
    }

    try {
      const r = editing
        ? await api.patch(`/hr/resume-pool/${entry.id}`, body)
        : await api.post('/hr/resume-pool', body)
      onSaved(r.data)
    } catch (e) {
      setError(errText(e, editing ? 'Could not save these changes.' : 'Could not add this person.'))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="modal-overlay" onClick={onClose} style={{ zIndex: 1100 }}>
      <div className="modal" onClick={e => e.stopPropagation()} style={{ maxWidth: 640 }}>
        <h2 style={{ fontSize: '1.15rem' }}>
          {editing ? `Edit ${entry.full_name || 'profile'}` : 'Add someone to the pool'}
        </h2>
        <p className="text-sm text-muted" style={{ marginTop: -12, marginBottom: 20 }}>
          {editing
            ? 'Changes save straight away. Anything you leave blank is cleared.'
            : 'Only a name is required — fill in the rest whenever you learn it.'}
        </p>

        <div className="input-group">
          <label>Full name *</label>
          <input className="input" value={form.full_name} autoFocus
                 onChange={e => set('full_name', e.target.value)} placeholder="Priya Sharma" />
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14, marginTop: 14 }}>
          <div className="input-group">
            <label>Email</label>
            <input className="input" type="email" value={form.email}
                   onChange={e => set('email', e.target.value)} placeholder="name@example.com" />
          </div>
          <div className="input-group">
            <label>Phone</label>
            <input className="input" value={form.phone}
                   onChange={e => set('phone', e.target.value)} placeholder="+91 98765 43210" />
          </div>
          <div className="input-group">
            <label>Role</label>
            <input className="input" value={form.job_role}
                   onChange={e => set('job_role', e.target.value)} placeholder="Backend Engineer" />
          </div>
          <div className="input-group">
            <label>Stage</label>
            <select className="select" value={form.stage} onChange={e => set('stage', e.target.value)}>
              {STAGES.map(s => <option key={s.value} value={s.value}>{s.label}</option>)}
            </select>
          </div>
          <div className="input-group">
            <label>College</label>
            <input className="input" value={form.college}
                   onChange={e => set('college', e.target.value)} placeholder="NIT Trichy" />
          </div>
          <div className="input-group">
            <label>Year of passing</label>
            <input className="input" type="number" value={form.year_of_passing}
                   onChange={e => set('year_of_passing', e.target.value)} placeholder="2021" />
          </div>
        </div>

        <div className="input-group" style={{ marginTop: 14 }}>
          <label>Where did they come from?</label>
          <input className="input" value={form.source_channel}
                 onChange={e => set('source_channel', e.target.value)}
                 placeholder="LinkedIn, referral, campus drive…" />
        </div>

        <div className="input-group" style={{ marginTop: 14 }}>
          <label>Skills</label>
          <input className="input" value={form.skills}
                 onChange={e => set('skills', e.target.value)}
                 placeholder="python, fastapi, mysql" />
          <span className="text-sm text-muted">Separate with commas.</span>
        </div>

        <div className="input-group" style={{ marginTop: 14 }}>
          <label>Notes</label>
          <textarea className="textarea" value={form.notes} rows={4}
                    onChange={e => set('notes', e.target.value)}
                    placeholder="Interview feedback, what they are looking for, when to follow up…" />
        </div>

        {error && (
          <div className="rm-alert rm-alert--err" style={{ marginTop: 16 }}>
            <AlertCircle size={15} /> {error}
          </div>
        )}

        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 10, marginTop: 24 }}>
          <button className="btn btn-secondary" onClick={onClose} disabled={saving}>Cancel</button>
          <button className="btn btn-primary" onClick={submit} disabled={!canSave}>
            {saving ? 'Saving…' : <><Save size={15} /> {editing ? 'Save changes' : 'Add to pool'}</>}
          </button>
        </div>
      </div>
    </div>
  )
}
