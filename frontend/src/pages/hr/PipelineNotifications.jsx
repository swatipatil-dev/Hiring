import React, { useEffect, useState, useCallback } from 'react'
import { Mail, Send, CheckCircle2, AlertCircle } from 'lucide-react'
import api from '../../api/client'

// One editable step card.
function StepCard({ step, mailboxes, onChange, onTest }) {
  const selectedMailbox = mailboxes.find(m => m.id === step.mailbox_id)
  return (
    <div className="card" style={{ padding: 18, opacity: step.enabled ? 1 : 0.6 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 12 }}>
        <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer', fontWeight: 600 }}>
          <input type="checkbox" checked={step.enabled}
                 onChange={e => onChange({ ...step, enabled: e.target.checked })} />
          {step.label}
        </label>
        <span style={{ flex: 1 }} />
        <button type="button" className="btn btn-secondary" style={{ padding: '4px 10px', fontSize: '0.78rem' }}
                onClick={() => onTest(step)}>
          <Send size={13} style={{ marginRight: 4 }} /> Test
        </button>
      </div>

      <div className="input-group" style={{ marginBottom: 10 }}>
        <label style={{ fontSize: '0.8rem' }}>CC recipient emails <span style={{ color: 'var(--text-tertiary)', fontWeight: 400 }}>(comma separated — only these get CC'd)</span></label>
        <input className="input" placeholder="manager@company.com, lead@company.com"
               value={step._emails_text}
               onChange={e => onChange({ ...step, _emails_text: e.target.value })} />
      </div>

      <div className="input-group" style={{ marginBottom: 0 }}>
        <label style={{ fontSize: '0.8rem' }}>Send from</label>
        <select className="input" value={step.mailbox_id || ''}
                onChange={e => onChange({ ...step, mailbox_id: e.target.value || null })}>
          <option value="">Company default</option>
          {mailboxes.map(m => (
            // A mailbox that cannot send is not replaced by the default: this step's
            // mail is blocked until it is fixed. Selecting it still saves, since an
            // admin may be about to add the credentials.
            <option key={m.id} value={m.id}>
              {m.name}{m.from_email ? ` — ${m.from_email}` : ''}
              {(m.usable ?? m.has_password !== false) ? '' : '  (cannot send — mail will be blocked)'}
            </option>
          ))}
        </select>
        {selectedMailbox && !(selectedMailbox.usable ?? selectedMailbox.has_password !== false) && (
          <span style={{ fontSize: '0.74rem', color: '#dc2626', marginTop: 4, fontWeight: 600 }}>
            {selectedMailbox.name} cannot send{selectedMailbox.unusable_reason ? ` — ${selectedMailbox.unusable_reason}` : ''}.
            This step's emails will not be sent until an admin fixes it under Email Settings.
          </span>
        )}
        {step.enabled && step.effective?.status === 'blocked' && !(selectedMailbox && !(selectedMailbox.usable ?? true)) && (
          <span style={{ fontSize: '0.74rem', color: '#dc2626', marginTop: 4, fontWeight: 600 }}>
            Not sent: {step.effective.reason || 'no working sender is configured'}
          </span>
        )}
        {step.enabled && step.effective?.status === 'ok' && step.effective.from_email && (
          <span style={{ fontSize: '0.74rem', color: 'var(--text-tertiary)', marginTop: 4 }}>
            Currently sends from {step.effective.from_email}
          </span>
        )}
        {mailboxes.length === 0 && (
          <span style={{ fontSize: '0.74rem', color: 'var(--text-tertiary)', marginTop: 4 }}>
            No custom mailboxes yet — an admin can add them under Email Settings.
          </span>
        )}
      </div>
    </div>
  )
}

export default function PipelineNotifications() {
  const [steps, setSteps]       = useState([])
  const [mailboxes, setMailboxes] = useState([])
  const [loading, setLoading]   = useState(true)
  const [saving, setSaving]     = useState(false)
  const [msg, setMsg]           = useState(null)        // {type, text}
  const [testResult, setTestResult] = useState(null)

  const load = useCallback(() => {
    setLoading(true)
    Promise.all([
      api.get('/hr/pipeline-notifications'),
      api.get('/hr/mailboxes').catch(() => ({ data: [] })),
    ]).then(([cfg, mb]) => {
      const incoming = (cfg.data?.steps || []).map(s => ({
        ...s,
        _emails_text: (s.extra_emails || []).join(', '),
      }))
      setSteps(incoming)
      setMailboxes(mb.data || [])
    }).catch(() => setMsg({ type: 'error', text: 'Failed to load notification settings.' }))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => { load() }, [load])

  const updateStep = (idx, next) =>
    setSteps(prev => prev.map((s, i) => (i === idx ? next : s)))

  const parseEmails = (text) =>
    (text || '').split(/[,\n;]+/).map(e => e.trim()).filter(Boolean)

  const handleSave = async () => {
    setMsg(null)
    setSaving(true)
    try {
      const payload = {
        steps: steps.map(s => ({
          step_key:     s.step_key,
          enabled:      s.enabled,
          extra_emails: parseEmails(s._emails_text),
          as_cc:        s.as_cc !== false,
          mailbox_id:   s.mailbox_id || null,
        })),
      }
      const r = await api.put('/hr/pipeline-notifications', payload)
      const incoming = (r.data?.steps || []).map(s => ({
        ...s, _emails_text: (s.extra_emails || []).join(', '),
      }))
      setSteps(incoming)
      setMsg({ type: 'success', text: 'Notification settings saved.' })
    } catch (err) {
      setMsg({ type: 'error', text: err.response?.data?.detail || 'Failed to save settings.' })
    }
    setSaving(false)
  }

  const handleTest = async (step) => {
    const to = window.prompt(
      `Send a test "${step.label}" notification to which email?\n` +
      `(the sender's own address, or a user of your company)`)
    if (!to) return
    setTestResult(null)
    try {
      const r = await api.post('/hr/pipeline-notifications/test', null, {
        params: { step_key: step.step_key, to },
      })
      const cc = r.data?.cc?.length ? ` (CC: ${r.data.cc.join(', ')})` : ''
      if (r.data?.error_type === 'disabled') {
        setTestResult({ type: 'warn', text: `"${step.label}" is switched off, so nothing was sent. Save it as enabled first.` })
      } else if (r.data?.error_type === 'blocked') {
        setTestResult({ type: 'error', text: `"${step.label}" emails are blocked: ${r.data?.error || 'no working sender'}. Nothing was sent.` })
      } else if (!r.data?.sent) {
        setTestResult({ type: 'error', text: `Test failed: ${r.data?.error || 'unknown error'}` })
      } else {
        // The test goes out from the sender this step really uses — its own, or
        // the company default when none is chosen; never anything else.
        const source = r.data?.using_step_smtp ? "this step's mailbox" : 'the company default sender'
        setTestResult({ type: 'success', text: `Test accepted for ${to}${cc}, sent from ${source}.` })
      }
    } catch (err) {
      setTestResult({ type: 'error', text: err.response?.data?.detail || 'Test failed.' })
    }
  }

  if (loading) return <div style={{ padding: 24, color: 'var(--text-tertiary)' }}>Loading…</div>

  return (
    <div style={{ maxWidth: 760 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 6 }}>
        <Mail size={20} style={{ color: 'var(--accent-primary)' }} />
        <h2 style={{ margin: 0, fontSize: '1.1rem' }}>Email Notifications per Pipeline Step</h2>
      </div>
      <p style={{ fontSize: '0.85rem', color: 'var(--text-tertiary)', marginBottom: 18 }}>
        For each hiring step, add the exact email addresses to CC and choose which sender
        mailbox it goes out from. The candidate (or the step's primary recipient) is always
        notified; only the addresses you type here are added as CC — no one else. Sender
        mailboxes are configured by an admin under Email Settings.
      </p>

      {(msg || testResult) && (
        <div style={{
          display: 'flex', alignItems: 'center', gap: 8,
          marginBottom: 14, padding: '10px 14px', borderRadius: 8, fontSize: '0.85rem',
          background: (msg || testResult).type === 'success' ? 'rgba(34,197,94,0.10)'
            : (msg || testResult).type === 'warn' ? 'rgba(245,158,11,0.10)' : 'rgba(239,68,68,0.10)',
          color: (msg || testResult).type === 'success' ? '#16a34a'
            : (msg || testResult).type === 'warn' ? '#b45309' : '#dc2626',
        }}>
          {(msg || testResult).type === 'success' ? <CheckCircle2 size={16} /> : <AlertCircle size={16} />}
          {(msg || testResult).text}
        </div>
      )}

      <div style={{ display: 'grid', gap: 14 }}>
        {steps.map((s, i) => (
          <StepCard key={s.step_key} step={s} mailboxes={mailboxes}
                    onChange={(next) => updateStep(i, next)} onTest={handleTest} />
        ))}
      </div>

      <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 18 }}>
        <button className="btn btn-primary" onClick={handleSave} disabled={saving}>
          {saving ? 'Saving…' : 'Save Settings'}
        </button>
      </div>
    </div>
  )
}
