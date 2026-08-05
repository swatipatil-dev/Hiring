import React, { useEffect, useRef, useState } from 'react'
import { Mail, AlertTriangle } from 'lucide-react'
import api from '../../api/client'

/**
 * "Send from" control for the HR send dialogs.
 *
 * Shows which address the mail will actually leave from and lets HR pick a
 * different company mailbox for this send only. The default selection is the
 * mailbox configured for this pipeline stage (HR > Pipeline Notifications), so
 * leaving it alone reproduces the configured behaviour exactly.
 *
 * Props:
 *   stepKey  — pipeline step, e.g. "test_invitation" (see STEP_LABELS server-side)
 *   value    — selected mailbox id, or null for the company default
 *   onChange — (mailboxId|null) => void; called once on load with the stage default
 *   label    — field label
 */
export default function MailFromPicker({ stepKey, value, onChange, label = 'Send from' }) {
  const [data, setData]   = useState(null)
  const [error, setError] = useState(null)
  const seeded = useRef(false)

  useEffect(() => {
    if (!stepKey) return
    let cancelled = false
    seeded.current = false
    setData(null); setError(null)
    api.get('/hr/mail-preview', { params: { step_key: stepKey } })
      .then(r => {
        if (cancelled) return
        setData(r.data)
        // Seed the parent with the stage's configured mailbox exactly once, so
        // the dialog opens on the same sender the stage is already set to.
        if (!seeded.current) {
          seeded.current = true
          onChange(r.data.default_mailbox_id || null)
        }
      })
      .catch(err => {
        if (!cancelled) setError(err.response?.data?.detail || 'Could not load sender info.')
      })
    return () => { cancelled = true }
    // onChange is intentionally omitted — parents pass inline arrows and we only
    // want this to re-run when the step changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stepKey])

  if (error) {
    return (
      <div style={{ fontSize: '0.78rem', color: '#b45309', marginBottom: 10 }}>
        {error}
      </div>
    )
  }
  if (!data) {
    return (
      <div style={{ fontSize: '0.78rem', color: 'var(--text-tertiary)', marginBottom: 10 }}>
        Checking which mailbox this sends from…
      </div>
    )
  }

  const selected = data.mailboxes.find(m => m.id === value) || null
  // A mailbox with no password is skipped server-side, so the real From address
  // stays the stage/company default. Reflect that rather than the chosen name.
  const usable = selected ? selected.has_password !== false : false
  const from = usable ? selected.from_email : data.from_email
  const via  = usable ? selected.name : data.source_label

  return (
    <div className="input-group" style={{ marginBottom: 12 }}>
      <label style={{ fontSize: '0.8rem' }}>{label}</label>
      <select className="input" value={value || ''}
              onChange={e => onChange(e.target.value || null)}>
        <option value="">
          Company default{data.source === 'company_default' && data.from_email ? ` — ${data.from_email}` : ''}
        </option>
        {data.mailboxes.map(m => (
          <option key={m.id} value={m.id}>
            {m.name}{m.from_email ? ` — ${m.from_email}` : ''}
            {m.id === data.default_mailbox_id ? '  (stage default)' : ''}
            {m.has_password === false ? '  (no password — will not send)' : ''}
          </option>
        ))}
      </select>

      <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 5,
                    fontSize: '0.78rem', color: 'var(--text-tertiary)' }}>
        <Mail size={13} style={{ flexShrink: 0 }} />
        <span>
          Sending from <strong style={{ color: 'var(--text-primary)' }}>{from || 'the platform default address'}</strong>
          {via ? ` · ${via}` : ''}
        </span>
      </div>

      {data.cc?.length > 0 && (
        <div style={{ fontSize: '0.75rem', color: 'var(--text-tertiary)', marginTop: 3 }}>
          CC for this stage: {data.cc.join(', ')}
        </div>
      )}

      {/* Chosen a mailbox the server will skip — say so before they hit send. */}
      {selected && !usable && (
        <div style={{ display: 'flex', alignItems: 'flex-start', gap: 6, marginTop: 5,
                      fontSize: '0.75rem', color: '#b45309', fontWeight: 600 }}>
          <AlertTriangle size={13} style={{ flexShrink: 0, marginTop: 1 }} />
          <span>
            {selected.name} has no password set, so this will still send from {from || 'the default address'}.
            An admin can add it under Email Settings.
          </span>
        </div>
      )}

      {/* The stage points at a mailbox that can't be used at all. */}
      {!selected && data.fallback_reason && (
        <div style={{ display: 'flex', alignItems: 'flex-start', gap: 6, marginTop: 5,
                      fontSize: '0.75rem', color: '#b45309' }}>
          <AlertTriangle size={13} style={{ flexShrink: 0, marginTop: 1 }} />
          <span>Stage mailbox unusable: {data.fallback_reason}.</span>
        </div>
      )}
    </div>
  )
}
