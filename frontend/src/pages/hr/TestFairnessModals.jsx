import { useState } from 'react'
import api from '../../api/client'

/**
 * HR dialogs for the fairer side of assessments:
 * - RetestModal: give a candidate another attempt, with a required reason and optional
 *   adjustments. The earlier attempt and its proctoring record are kept.
 * - AdjustmentModal: approve specific adjustments a candidate asked for, or decline.
 * - AdjustmentRequestsModal: every open request for a job, including tests not started
 *   yet (those rows are not in the results table).
 */

const EMPTY = {
  extra_time_percent: 0, audio_monitoring_off: false, gaze_flags_off: false,
  fullscreen_not_required: false, notes: '',
}

const field = { display: 'flex', flexDirection: 'column', gap: 4, fontSize: '0.82rem', color: '#374151' }
const input = { fontFamily: 'inherit', fontSize: '0.85rem', padding: '8px 10px', borderRadius: 8, border: '1px solid #d1d5db' }
const check = { display: 'flex', alignItems: 'center', gap: 8, fontSize: '0.84rem', color: '#1f2937' }

function AccommodationFields({ value, onChange }) {
  const set = (k, v) => onChange({ ...value, [k]: v })
  return (
    <fieldset style={{ border: '1px solid #e5e7eb', borderRadius: 10, padding: '12px 14px', margin: 0, display: 'flex', flexDirection: 'column', gap: 10 }}>
      <legend style={{ fontSize: '0.8rem', fontWeight: 700, padding: '0 6px' }}>Adjustments</legend>
      <label style={field}>
        Extra time (percent)
        <input type="number" min={0} max={200} step={5} value={value.extra_time_percent}
          onChange={(e) => set('extra_time_percent', Math.max(0, Math.min(200, Number(e.target.value) || 0)))}
          style={{ ...input, width: 110 }} />
      </label>
      <label style={check}>
        <input type="checkbox" checked={value.audio_monitoring_off} onChange={(e) => set('audio_monitoring_off', e.target.checked)} />
        Turn microphone monitoring off
      </label>
      <label style={check}>
        <input type="checkbox" checked={value.gaze_flags_off} onChange={(e) => set('gaze_flags_off', e.target.checked)} />
        Don't flag looking away (gaze and head turns)
      </label>
      <label style={check}>
        <input type="checkbox" checked={value.fullscreen_not_required} onChange={(e) => set('fullscreen_not_required', e.target.checked)} />
        Don't require fullscreen
      </label>
      <label style={field}>
        Note for the record (optional)
        <textarea rows={2} maxLength={2000} value={value.notes} onChange={(e) => set('notes', e.target.value)} style={{ ...input, resize: 'vertical' }} />
      </label>
    </fieldset>
  )
}

function Shell({ title, onClose, children, footer }) {
  return (
    <div className="modal-overlay" onClick={onClose} style={{ zIndex: 1200 }}>
      <div className="modal" onClick={(e) => e.stopPropagation()} role="dialog" aria-modal="true" aria-label={title}
        style={{ maxWidth: 520, width: '100%', padding: 0, display: 'flex', flexDirection: 'column', maxHeight: '90vh' }}>
        <div style={{ padding: '18px 22px 12px', borderBottom: '1px solid #e5e7eb', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <h2 style={{ margin: 0, fontSize: '1.1rem' }}>{title}</h2>
          <button className="btn btn-ghost btn-sm" onClick={onClose} aria-label="Close">×</button>
        </div>
        <div style={{ padding: '16px 22px', overflow: 'auto', display: 'flex', flexDirection: 'column', gap: 14 }}>{children}</div>
        <div style={{ padding: '12px 22px 18px', borderTop: '1px solid #e5e7eb', display: 'flex', gap: 8, justifyContent: 'flex-end' }}>{footer}</div>
      </div>
    </div>
  )
}

export function RetestModal({ row, onClose, onDone }) {
  const [reason, setReason] = useState('')
  const [adjust, setAdjust] = useState(false)
  const [acc, setAcc] = useState(EMPTY)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState(null)
  const ok = reason.trim().length >= 10

  const submit = async () => {
    setSaving(true); setError(null)
    try {
      const r = await api.post(`/hr/test-results/${row.id}/retest`, {
        reason: reason.trim(), accommodations: adjust ? acc : null,
      })
      onDone?.(r.data)
    } catch (e) {
      setError(e?.response?.data?.detail || 'The retest could not be created.')
      setSaving(false)
    }
  }

  return (
    <Shell title={`Grant a retest to ${row.candidate_name}`} onClose={onClose} footer={<>
      <button className="btn btn-ghost btn-sm" onClick={onClose}>Cancel</button>
      <button className="btn btn-primary btn-sm" onClick={submit} disabled={!ok || saving}>
        {saving ? 'Creating…' : 'Grant retest'}
      </button>
    </>}>
      <p style={{ margin: 0, fontSize: '0.86rem', color: '#4b5563', lineHeight: 1.55 }}>
        The candidate gets a new attempt at {row.title}, invited by email. This attempt
        (attempt {row.attempt_number || 1}), its score and its proctoring record stay as they are.
      </p>
      <label style={field}>
        Reason (recorded in the candidate's history)
        <textarea rows={3} maxLength={2000} value={reason} onChange={(e) => setReason(e.target.value)}
          placeholder="For example: test ended by a power cut, confirmed with the candidate."
          style={{ ...input, resize: 'vertical' }} />
        {!ok && reason && <span style={{ fontSize: '0.75rem', color: '#b45309' }}>At least 10 characters.</span>}
      </label>
      <label style={check}>
        <input type="checkbox" checked={adjust} onChange={(e) => setAdjust(e.target.checked)} />
        Give this attempt adjustments
      </label>
      {adjust && <AccommodationFields value={acc} onChange={setAcc} />}
      {error && <p role="alert" style={{ margin: 0, fontSize: '0.82rem', color: '#b91c1c' }}>{error}</p>}
    </Shell>
  )
}

export function AdjustmentModal({ row, onClose, onDone }) {
  const [acc, setAcc] = useState({ ...EMPTY, ...(row.accommodations || {}) })
  const [saving, setSaving] = useState(null)
  const [error, setError] = useState(null)
  const interviewId = row.open_interview_id || row.id

  const decide = async (status) => {
    setSaving(status); setError(null)
    try {
      const r = await api.patch(`/hr/interviews/${interviewId}/accommodations`, { status, ...acc })
      onDone?.(r.data)
    } catch (e) {
      setError(e?.response?.data?.detail || 'The decision could not be saved.')
      setSaving(null)
    }
  }

  return (
    <Shell title={`Adjustments for ${row.candidate_name}`} onClose={onClose} footer={<>
      <button className="btn btn-ghost btn-sm" onClick={() => decide('declined')} disabled={!!saving}>
        {saving === 'declined' ? 'Declining…' : 'Decline'}
      </button>
      <button className="btn btn-primary btn-sm" onClick={() => decide('approved')} disabled={!!saving}>
        {saving === 'approved' ? 'Approving…' : 'Approve these adjustments'}
      </button>
    </>}>
      {row.accommodation_request ? (
        <div>
          <div style={{ fontSize: '0.8rem', fontWeight: 700, marginBottom: 4 }}>The candidate asked</div>
          <q style={{ display: 'block', fontSize: '0.86rem', lineHeight: 1.55, background: '#f9fafb', borderRadius: 8, padding: '10px 12px' }}>
            {row.accommodation_request}
          </q>
        </div>
      ) : (
        <p style={{ margin: 0, fontSize: '0.86rem', color: '#4b5563' }}>No request text was given.</p>
      )}
      <AccommodationFields value={acc} onChange={setAcc} />
      <p style={{ margin: 0, fontSize: '0.78rem', color: '#6b7280', lineHeight: 1.5 }}>
        Approving applies only to this candidate's attempt and must happen before they start.
        Extra time lengthens their questions; monitoring changes are enforced by the proctoring
        service, not the candidate's browser.
      </p>
      {error && <p role="alert" style={{ margin: 0, fontSize: '0.82rem', color: '#b91c1c' }}>{error}</p>}
    </Shell>
  )
}

export function AdjustmentRequestsModal({ rows, onClose, onPick }) {
  return (
    <Shell title="Adjustment requests" onClose={onClose} footer={
      <button className="btn btn-ghost btn-sm" onClick={onClose}>Close</button>
    }>
      {rows.length === 0 ? (
        <p style={{ margin: 0, fontSize: '0.86rem', color: '#4b5563' }}>No open requests.</p>
      ) : rows.map((r) => (
        <div key={r.id} style={{ border: '1px solid #e5e7eb', borderRadius: 10, padding: '12px 14px', display: 'flex', flexDirection: 'column', gap: 6 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'baseline' }}>
            <b style={{ fontSize: '0.9rem' }}>{r.candidate_name}</b>
            <span style={{ fontSize: '0.78rem', color: '#6b7280' }}>{r.title}{r.retest_pending ? ' (retest)' : ''}</span>
          </div>
          <q style={{ fontSize: '0.84rem', color: '#374151', lineHeight: 1.5 }}>{r.accommodation_request}</q>
          <button className="btn btn-primary btn-sm" style={{ alignSelf: 'flex-start' }} onClick={() => onPick(r)}>Decide</button>
        </div>
      ))}
    </Shell>
  )
}
