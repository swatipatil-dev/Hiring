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
  // A chosen mailbox that cannot send is NOT replaced by another sender: the
  // email is blocked. Say so here, before HR presses send.
  const usable = selected ? (selected.usable ?? selected.has_password !== false) : false
  const blocked = selected ? !usable : data.source === 'blocked'
  const disabled = !selected && data.source === 'disabled'
  const from = selected ? (usable ? selected.from_email : null) : data.from_email
  const via  = selected ? selected.name : data.source_label

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
            {(m.usable ?? m.has_password !== false) ? '' : '  (cannot send — will be blocked)'}
          </option>
        ))}
      </select>

      {!blocked && !disabled && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 5,
                      fontSize: '0.78rem', color: 'var(--text-tertiary)' }}>
          <Mail size={13} style={{ flexShrink: 0 }} />
          <span>
            Sending from <strong style={{ color: 'var(--text-primary)' }}>{from}</strong>
            {via ? ` · ${via}` : ''}
          </span>
        </div>
      )}

      {data.cc?.length > 0 && (
        <div style={{ fontSize: '0.75rem', color: 'var(--text-tertiary)', marginTop: 3 }}>
          CC for this stage: {data.cc.join(', ')}
        </div>
      )}

      {/* The chosen mailbox cannot send — the email will be blocked, not re-routed. */}
      {selected && !usable && (
        <div style={{ display: 'flex', alignItems: 'flex-start', gap: 6, marginTop: 5,
                      fontSize: '0.75rem', color: '#dc2626', fontWeight: 600 }}>
          <AlertTriangle size={13} style={{ flexShrink: 0, marginTop: 1 }} />
          <span>
            {selected.name} cannot send{selected.unusable_reason ? ` — ${selected.unusable_reason}` : ''}.
            This email will not be sent. Pick another mailbox, or ask an admin to fix it under Email Settings.
          </span>
        </div>
      )}

      {/* Nothing usable for this stage at all. */}
      {!selected && blocked && (
        <div style={{ display: 'flex', alignItems: 'flex-start', gap: 6, marginTop: 5,
                      fontSize: '0.75rem', color: '#dc2626', fontWeight: 600 }}>
          <AlertTriangle size={13} style={{ flexShrink: 0, marginTop: 1 }} />
          <span>This email will not be sent: {data.fallback_reason || 'no working sender is configured'}</span>
        </div>
      )}
      {disabled && (
        <div style={{ display: 'flex', alignItems: 'flex-start', gap: 6, marginTop: 5,
                      fontSize: '0.75rem', color: '#b45309' }}>
          <AlertTriangle size={13} style={{ flexShrink: 0, marginTop: 1 }} />
          <span>This email is switched off in Pipeline Notifications, so nothing will be sent.</span>
        </div>
      )}
    </div>
  )
}
