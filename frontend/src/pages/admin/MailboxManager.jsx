import React, { useEffect, useState, useCallback } from 'react'
import { Inbox, Plus, Trash2, Edit3, X, Send } from 'lucide-react'
import api from '../../api/client'

const BLANK = {
  id: null, name: '', smtp_host: 'smtp.gmail.com', smtp_port: 587,
  smtp_user: '', smtp_pass: '', smtp_from_email: '', hourly_limit: '',
  is_active: true, has_password: false,
}

// Admin-managed named sender mailboxes. HR picks from these per pipeline step.
export default function MailboxManager() {
  const [list, setList]   = useState([])
  const [form, setForm]   = useState(null)     // null = no form open; else BLANK or an existing row
  const [saving, setSaving] = useState(false)
  const [msg, setMsg]     = useState(null)     // {type: success|warn|error, text}
  const [testing, setTesting] = useState(null) // id of the mailbox being tested

  const [loading, setLoading] = useState(true)
  const load = useCallback(() => {
    setLoading(true)
    api.get('/admin/mailboxes')
      .then(r => setList(r.data || []))
      .catch(err => setMsg({ type: 'error', text: err.response?.data?.detail || 'Could not load mailboxes. Please retry.' }))
      .finally(() => setLoading(false))
  }, [])
  useEffect(() => { load() }, [load])

  const openNew  = () => { setMsg(null); setForm({ ...BLANK }) }
  const openEdit = (m) => { setMsg(null); setForm({ ...m, smtp_pass: '' }) }
  const close    = () => setForm(null)

  const save = async (e) => {
    e.preventDefault()
    setSaving(true); setMsg(null)
    try {
      const payload = {
        name: form.name,
        smtp_host: form.smtp_host || 'smtp.gmail.com',
        smtp_port: Number(form.smtp_port) || 587,
        smtp_user: form.smtp_user,
        smtp_from_email: form.smtp_from_email || form.smtp_user,
        hourly_limit: form.hourly_limit === '' || form.hourly_limit === null ? null : Number(form.hourly_limit),
        is_active: form.is_active !== false,
      }
      if (form.smtp_pass) payload.smtp_pass = form.smtp_pass
      if (form.id) await api.put(`/admin/mailboxes/${form.id}`, payload)
      else         await api.post('/admin/mailboxes', payload)
      setMsg({ type: 'success', text: 'Mailbox saved.' })
      setForm(null)
      load()
    } catch (err) {
      setMsg({ type: 'error', text: err.response?.data?.detail || 'Failed to save mailbox.' })
    }
    setSaving(false)
  }

  // Saving a mailbox verifies nothing — the password is encrypted and stored
  // as typed. Without this button a typo only surfaces when a candidate-facing
  // send fails, and until then the step silently uses the company default.
  const runTest = async (m) => {
    const to = window.prompt(`Send a test message from "${m.name}" to which address?`)
    if (!to) return
    setMsg(null)
    setTesting(m.id)
    try {
      const r = await api.post(`/admin/mailboxes/${m.id}/test`, {
        to,
        subject: `HireFlow test — ${m.name}`,
        body: `<p>This confirms the <b>${m.name}</b> mailbox can send.</p>`,
      })
      const d = r.data || {}
      if (d.sent) {
        setMsg({ type: 'success', text: `${m.name}: sent to ${to} from ${d.config?.smtp_user}.` })
      } else if (d.error_type === 'unusable') {
        // Config problem, not a rejected credential — no connection was attempted.
        setMsg({ type: 'warn', text: `${m.name}: ${d.error}` })
      } else {
        setMsg({ type: 'error', text: `${m.name}: ${d.error || 'send failed'} (${d.error_type || 'unknown'})` })
      }
    } catch (err) {
      setMsg({ type: 'error', text: err.response?.data?.detail || `Could not test ${m.name}.` })
    } finally {
      setTesting(null)
    }
  }

  const remove = async (m) => {
    if (!confirm(`Delete mailbox "${m.name}"? Steps using it revert to the company default.`)) return
    try { await api.delete(`/admin/mailboxes/${m.id}`); load() }
    catch (err) { setMsg({ type: 'error', text: err.response?.data?.detail || 'Failed to delete.' }) }
  }

  return (
    <div className="card" style={{ padding: 24 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 6 }}>
        <Inbox size={18} style={{ color: 'var(--accent-primary)' }} />
        <h3 style={{ margin: 0, fontSize: '1rem' }}>Sender Mailboxes</h3>
        <span style={{ flex: 1 }} />
        {!form && (
          <button type="button" className="btn btn-secondary" style={{ padding: '4px 10px', fontSize: '0.8rem' }} onClick={openNew}>
            <Plus size={14} style={{ marginRight: 4 }} /> Add mailbox
          </button>
        )}
      </div>
      <p style={{ fontSize: '0.82rem', color: 'var(--text-tertiary)', marginBottom: 16 }}>
        Named mailboxes (e.g. “Offers”, “Interviews”). HR selects one of these per pipeline
        step. The company default sender above is used when a step has no mailbox chosen.
      </p>

      {msg && (
        <div style={{
          marginBottom: 14, padding: '10px 14px', borderRadius: 8, fontSize: '0.85rem',
          background: msg.type === 'success' ? 'rgba(34,197,94,0.10)'
            : msg.type === 'warn' ? 'rgba(245,158,11,0.10)' : 'rgba(239,68,68,0.10)',
          color: msg.type === 'success' ? '#16a34a'
            : msg.type === 'warn' ? '#b45309' : '#dc2626',
        }}>{msg.text}</div>
      )}

      {/* List */}
      {list.length > 0 && (
        <div style={{ display: 'grid', gap: 8, marginBottom: form ? 16 : 0 }}>
          {list.map(m => (
            <div key={m.id} style={{
              display: 'flex', alignItems: 'center', gap: 12, padding: '10px 14px',
              border: '1px solid var(--border, #e5e7eb)', borderRadius: 8,
              opacity: m.is_active ? 1 : 0.55,
            }}>
              <div style={{ flex: 1 }}>
                <div style={{ fontWeight: 600, fontSize: '0.9rem' }}>
                  {m.name}{!m.is_active && <span style={{ color: 'var(--text-tertiary)', fontWeight: 400 }}> (inactive)</span>}
                </div>
                <div style={{ fontSize: '0.78rem', color: 'var(--text-tertiary)' }}>
                  {m.smtp_from_email || m.smtp_user} · {m.smtp_host}:{m.smtp_port}
                  {!m.has_password && <span style={{ color: '#f59e0b' }}> · no password set</span>}
                </div>
              </div>
              <button type="button" className="btn btn-secondary" style={{ padding: '4px 12px', fontSize: '0.8rem', display: 'inline-flex', alignItems: 'center', gap: 5 }} onClick={() => runTest(m)} disabled={testing === m.id} title={`Send a test message from ${m.name}`}>
                <Send size={14} /> {testing === m.id ? 'Testing…' : 'Test'}
              </button>
              <button type="button" className="btn btn-secondary" style={{ padding: '4px 12px', fontSize: '0.8rem', display: 'inline-flex', alignItems: 'center', gap: 5 }} onClick={() => openEdit(m)}>
                <Edit3 size={14} /> Edit
              </button>
              <button type="button" className="btn btn-secondary" style={{ padding: '4px 12px', fontSize: '0.8rem', display: 'inline-flex', alignItems: 'center', gap: 5 }} onClick={() => remove(m)} title={`Delete ${m.name}`}>
                <Trash2 size={14} /> Delete
              </button>
            </div>
          ))}
        </div>
      )}

      {/* Empty state — so an empty list reads as "none yet", not a broken screen */}
      {!form && !loading && list.length === 0 && !msg && (
        <div style={{
          padding: '20px 14px', textAlign: 'center', color: 'var(--text-tertiary)',
          fontSize: '0.85rem', border: '1px dashed var(--border-medium, #d6d3cd)', borderRadius: 8,
        }}>
          No mailboxes yet. Click <strong>Add mailbox</strong> to create one (e.g. “Offers”, “Interviews”).
        </div>
      )}

      {/* Add / edit form */}
      {form && (
        <form onSubmit={save} style={{ borderTop: list.length ? '1px solid var(--border, #e5e7eb)' : 'none', paddingTop: list.length ? 16 : 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', marginBottom: 10 }}>
            <h4 style={{ margin: 0, fontSize: '0.9rem' }}>{form.id ? 'Edit mailbox' : 'New mailbox'}</h4>
            <span style={{ flex: 1 }} />
            <button type="button" onClick={close} style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-tertiary)' }}><X size={16} /></button>
          </div>

          <div className="input-group">
            <label>Mailbox name</label>
            <input className="input" required placeholder="Offers" value={form.name}
                   onChange={e => setForm({ ...form, name: e.target.value })} />
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr', gap: 14 }}>
            <div className="input-group">
              <label>SMTP Host</label>
              <input className="input" required value={form.smtp_host}
                     onChange={e => setForm({ ...form, smtp_host: e.target.value })} />
            </div>
            <div className="input-group">
              <label>Port</label>
              <input className="input" type="number" required value={form.smtp_port}
                     onChange={e => setForm({ ...form, smtp_port: e.target.value })} />
            </div>
          </div>

          <div className="input-group">
            <label>From Email (SMTP user)</label>
            <input className="input" type="email" required placeholder="offers@yourcompany.com" value={form.smtp_user}
                   onChange={e => setForm({ ...form, smtp_user: e.target.value })} />
          </div>

          <div className="input-group">
            <label>Password {form.has_password && <span style={{ color: 'var(--text-tertiary)', fontWeight: 400 }}>(leave blank to keep existing)</span>}</label>
            <input className="input" type="password"
                   placeholder={form.has_password ? '••••••••••••••••' : 'mailbox password'}
                   value={form.smtp_pass} onChange={e => setForm({ ...form, smtp_pass: e.target.value })} />
            <div style={{ fontSize: '0.75rem', color: 'var(--text-tertiary)', marginTop: 4 }}>
              Gmail hosts need an App Password (Google Account → Security → App
              passwords). Other hosts, including cPanel mail, take the mailbox’s
              own password. Save, then press <b>Test</b> to confirm it works.
            </div>
          </div>

          <div className="input-group">
            <label>Display From Email <span style={{ color: 'var(--text-tertiary)', fontWeight: 400 }}>(optional)</span></label>
            <input className="input" type="email" value={form.smtp_from_email}
                   onChange={e => setForm({ ...form, smtp_from_email: e.target.value })} />
          </div>

          <div className="input-group">
            <label>Hourly send limit <span style={{ color: 'var(--text-tertiary)', fontWeight: 400 }}>(optional — blank uses the default)</span></label>
            <input className="input" type="number" min={1} placeholder="e.g. 100"
                   value={form.hourly_limit ?? ''}
                   onChange={e => setForm({ ...form, hourly_limit: e.target.value })} />
          </div>

          <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: '0.85rem', cursor: 'pointer', marginBottom: 12 }}>
            <input type="checkbox" checked={form.is_active !== false}
                   onChange={e => setForm({ ...form, is_active: e.target.checked })} />
            Active (selectable by HR)
          </label>

          <div className="flex gap-12" style={{ justifyContent: 'flex-end' }}>
            <button type="button" className="btn btn-secondary" onClick={close}>Cancel</button>
            <button type="submit" className="btn btn-primary" disabled={saving}>
              {saving ? 'Saving…' : 'Save Mailbox'}
            </button>
          </div>
        </form>
      )}
    </div>
  )
}
