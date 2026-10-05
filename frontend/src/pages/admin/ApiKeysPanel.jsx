import React, { useEffect, useState, useCallback } from 'react'
import { KeyRound, Plus, X, Copy, RotateCcw, Ban } from 'lucide-react'
import api from '../../api/client'

const SCOPE_HELP = {
  'email:send':        'Send email',
  'email:read':        'Read message status',
  'connections:read':  'View mailbox connections',
  'connections:write': 'Connect and disconnect mailboxes',
  'senders:read':      'View senders',
  'senders:write':     'Add and change senders',
}

const STATE_COLOR = { active: '#16a34a', expired: '#6b7280', revoked: '#dc2626' }

const BLANK = { name: '', mode: 'live', scopes: ['email:send', 'email:read'],
                expires_at: '', allowed_ips: '', hourly_quota: '' }

const muted = { fontSize: '0.78rem', color: 'var(--text-tertiary)' }

// Keys for the company's own applications to call the email API
// (/api/v1/email). A key is shown exactly once, when it is created or rotated.
export default function ApiKeysPanel() {
  const [keys, setKeys]       = useState([])
  const [scopes, setScopes]   = useState(Object.keys(SCOPE_HELP))
  const [form, setForm]       = useState(null)
  const [saving, setSaving]   = useState(false)
  const [msg, setMsg]         = useState(null)
  const [revealed, setRevealed] = useState(null)   // {name, token}

  const load = useCallback(() => {
    api.get('/admin/api-keys')
      .then(r => { setKeys(r.data.api_keys || []); if (r.data.scopes) setScopes(r.data.scopes) })
      .catch(err => setMsg({ type: 'error', text: err.response?.data?.detail?.message || 'Could not load API keys.' }))
  }, [])
  useEffect(() => { load() }, [load])

  const errorText = (err, fallback) => {
    const d = err.response?.data?.detail
    if (typeof d === 'string') return d
    if (d?.message) return d.message
    if (Array.isArray(d) && d[0]?.msg) return d[0].msg
    return fallback
  }

  const create = async (e) => {
    e.preventDefault()
    setSaving(true); setMsg(null)
    try {
      const body = { name: form.name, mode: form.mode, scopes: form.scopes }
      if (form.expires_at) body.expires_at = new Date(form.expires_at).toISOString()
      if (form.allowed_ips.trim()) body.allowed_ips = form.allowed_ips.split(',').map(s => s.trim()).filter(Boolean)
      if (form.hourly_quota) body.hourly_quota = Number(form.hourly_quota)
      const r = await api.post('/admin/api-keys', body)
      setRevealed({ name: r.data.name, token: r.data.token })
      setForm(null)
      load()
    } catch (err) {
      setMsg({ type: 'error', text: errorText(err, 'Could not create the key.') })
    }
    setSaving(false)
  }

  const revoke = async (k) => {
    if (!confirm(`Revoke "${k.name}" (${k.key_prefix}…)? Anything using it stops working immediately.`)) return
    try { await api.post(`/admin/api-keys/${k.id}/revoke`); load() }
    catch (err) { setMsg({ type: 'error', text: errorText(err, 'Could not revoke the key.') }) }
  }

  const rotate = async (k) => {
    const hours = window.prompt(
      `Rotate "${k.name}": a new key is issued, and the old one keeps working for how many hours (0–168)?`, '24')
    if (hours === null) return
    try {
      const r = await api.post(`/admin/api-keys/${k.id}/rotate`, { overlap_hours: Number(hours) || 0 })
      setRevealed({ name: r.data.name, token: r.data.token })
      load()
    } catch (err) {
      setMsg({ type: 'error', text: errorText(err, 'Could not rotate the key.') })
    }
  }

  const toggleScope = (s) => setForm(f => ({
    ...f, scopes: f.scopes.includes(s) ? f.scopes.filter(x => x !== s) : [...f.scopes, s],
  }))

  return (
    <div className="card" style={{ padding: 24 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 6 }}>
        <KeyRound size={18} style={{ color: 'var(--accent-primary)' }} />
        <h3 style={{ margin: 0, fontSize: '1rem' }}>Email API keys</h3>
        <span style={{ flex: 1 }} />
        {!form && (
          <button type="button" className="btn btn-secondary" style={{ padding: '4px 10px', fontSize: '0.8rem' }}
                  onClick={() => { setMsg(null); setForm({ ...BLANK }) }}>
            <Plus size={14} style={{ marginRight: 4 }} /> New key
          </button>
        )}
      </div>
      <p style={{ ...muted, marginBottom: 16 }}>
        Let your own applications send email through your company's mailboxes with
        <code> POST /api/v1/email/messages</code>. <b>Test</b> keys never deliver anything — use them
        while integrating.
      </p>

      {revealed && (
        <div style={{ marginBottom: 14, padding: '12px 14px', borderRadius: 8,
                      background: 'rgba(34,197,94,0.10)', border: '1px solid rgba(34,197,94,0.35)' }}>
          <div style={{ fontWeight: 600, fontSize: '0.88rem', marginBottom: 6 }}>
            Copy the key for “{revealed.name}” now — it will not be shown again.
          </div>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <code data-testid="revealed-token" style={{ flex: 1, wordBreak: 'break-all', fontSize: '0.8rem',
                           background: 'var(--bg-secondary, #f8fafc)', padding: '6px 8px', borderRadius: 6 }}>
              {revealed.token}
            </code>
            <button type="button" className="btn btn-secondary" style={{ padding: '4px 10px' }}
                    onClick={() => navigator.clipboard?.writeText(revealed.token)} title="Copy">
              <Copy size={14} />
            </button>
            <button type="button" className="btn btn-secondary" style={{ padding: '4px 10px' }}
                    onClick={() => setRevealed(null)} title="I have stored it">
              <X size={14} />
            </button>
          </div>
        </div>
      )}

      {msg && (
        <div style={{ marginBottom: 14, padding: '10px 14px', borderRadius: 8, fontSize: '0.85rem',
                      background: 'rgba(239,68,68,0.10)', color: '#dc2626' }}>{msg.text}</div>
      )}

      {keys.length > 0 ? (
        <div style={{ display: 'grid', gap: 8, marginBottom: form ? 16 : 0 }}>
          {keys.map(k => (
            <div key={k.id} style={{
              display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 12, padding: '10px 14px',
              border: '1px solid var(--border, #e5e7eb)', borderRadius: 8,
              opacity: k.state === 'active' ? 1 : 0.6,
            }}>
              <div style={{ flex: '1 1 240px', minWidth: 0 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                  <span style={{ fontWeight: 600, fontSize: '0.9rem' }}>{k.name}</span>
                  <span style={{ ...muted, fontFamily: 'monospace' }}>{k.key_prefix}…</span>
                  <span style={{ fontSize: '0.7rem', fontWeight: 600, padding: '1px 8px', borderRadius: 20,
                                 background: k.mode === 'test' ? '#6366f11A' : '#0ea5e91A',
                                 color: k.mode === 'test' ? '#6366f1' : '#0284c7' }}>
                    {k.mode === 'test' ? 'Test' : 'Live'}
                  </span>
                  <span style={{ fontSize: '0.7rem', fontWeight: 600, color: STATE_COLOR[k.state] }}>{k.state}</span>
                </div>
                <div style={{ ...muted, marginTop: 2 }}>
                  {k.scopes.join(', ')}
                  {k.expires_at ? ` · expires ${new Date(k.expires_at + 'Z').toLocaleString()}` : ''}
                  {k.last_used_at ? ` · last used ${new Date(k.last_used_at + 'Z').toLocaleString()}` : ' · never used'}
                </div>
              </div>
              {k.state === 'active' && (
                <>
                  <button type="button" className="btn btn-secondary" onClick={() => rotate(k)}
                          style={{ padding: '4px 12px', fontSize: '0.8rem', display: 'inline-flex', alignItems: 'center', gap: 5 }}>
                    <RotateCcw size={14} /> Rotate
                  </button>
                  <button type="button" className="btn btn-secondary" onClick={() => revoke(k)}
                          style={{ padding: '4px 12px', fontSize: '0.8rem', display: 'inline-flex', alignItems: 'center', gap: 5 }}>
                    <Ban size={14} /> Revoke
                  </button>
                </>
              )}
            </div>
          ))}
        </div>
      ) : !form && (
        <div style={{ padding: '18px 14px', textAlign: 'center', ...muted,
                      border: '1px dashed var(--border-medium, #d6d3cd)', borderRadius: 8 }}>
          No API keys. Most companies do not need one — they are for sending from your own software.
        </div>
      )}

      {form && (
        <form onSubmit={create} style={{ borderTop: keys.length ? '1px solid var(--border, #e5e7eb)' : 'none', paddingTop: keys.length ? 16 : 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', marginBottom: 10 }}>
            <h4 style={{ margin: 0, fontSize: '0.9rem' }}>New API key</h4>
            <span style={{ flex: 1 }} />
            <button type="button" onClick={() => setForm(null)} aria-label="Close"
                    style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-tertiary)' }}><X size={16} /></button>
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr', gap: 14 }}>
            <div className="input-group">
              <label htmlFor="apikey-name">Name</label>
              <input id="apikey-name" className="input" required placeholder="ATS integration" value={form.name}
                     onChange={e => setForm({ ...form, name: e.target.value })} />
            </div>
            <div className="input-group">
              <label htmlFor="apikey-mode">Mode</label>
              <select id="apikey-mode" className="input" value={form.mode} onChange={e => setForm({ ...form, mode: e.target.value })}>
                <option value="live">Live — sends real email</option>
                <option value="test">Test — never delivers</option>
              </select>
            </div>
          </div>
          <div className="input-group">
            <label>Permissions</label>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))', gap: 6 }}>
              {scopes.map(s => (
                <label key={s} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: '0.84rem', cursor: 'pointer' }}>
                  <input type="checkbox" checked={form.scopes.includes(s)} onChange={() => toggleScope(s)} />
                  {SCOPE_HELP[s] || s} <span style={{ ...muted, fontFamily: 'monospace' }}>{s}</span>
                </label>
              ))}
            </div>
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 14 }}>
            <div className="input-group">
              <label htmlFor="apikey-expiry">Expires <span style={{ ...muted, fontWeight: 400 }}>(optional)</span></label>
              <input id="apikey-expiry" className="input" type="datetime-local" value={form.expires_at}
                     onChange={e => setForm({ ...form, expires_at: e.target.value })} />
            </div>
            <div className="input-group">
              <label htmlFor="apikey-ips">Allowed IPs <span style={{ ...muted, fontWeight: 400 }}>(optional)</span></label>
              <input id="apikey-ips" className="input" placeholder="203.0.113.0/24, 198.51.100.7" value={form.allowed_ips}
                     onChange={e => setForm({ ...form, allowed_ips: e.target.value })} />
            </div>
            <div className="input-group">
              <label htmlFor="apikey-quota">Emails per hour <span style={{ ...muted, fontWeight: 400 }}>(optional)</span></label>
              <input id="apikey-quota" className="input" type="number" min={1} placeholder="500" value={form.hourly_quota}
                     onChange={e => setForm({ ...form, hourly_quota: e.target.value })} />
            </div>
          </div>
          <div className="flex gap-12" style={{ justifyContent: 'flex-end' }}>
            <button type="button" className="btn btn-secondary" onClick={() => setForm(null)}>Cancel</button>
            <button type="submit" className="btn btn-primary" disabled={saving || form.scopes.length === 0}>
              {saving ? 'Creating…' : 'Create key'}
            </button>
          </div>
        </form>
      )}
    </div>
  )
}
