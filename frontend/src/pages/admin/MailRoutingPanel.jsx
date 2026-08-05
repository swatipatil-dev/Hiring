import React, { useEffect, useState, useCallback } from 'react'
import { Route, CheckCircle2, AlertTriangle, RefreshCw } from 'lucide-react'
import api from '../../api/client'

const AMBER = '#f59e0b'
const RED   = '#dc2626'
const GREEN = '#22c55e'

const band = (color) => ({
  display: 'flex', alignItems: 'center', gap: 14, padding: '14px 18px',
  background: `linear-gradient(135deg, ${color}1A, ${color}05)`,
  borderLeft: `3px solid ${color}`,
})

const badge = (color) => ({
  display: 'inline-block', padding: '1px 8px', borderRadius: 20,
  fontSize: '0.7rem', fontWeight: 600, whiteSpace: 'nowrap',
  background: `${color}1A`, color,
})

const muted = { fontSize: '0.78rem', color: 'var(--text-tertiary)' }

// Read-only view of how outgoing mail is routed: which sender layer wins, what
// each pipeline step actually sends from, and whether mail is queued or inline.
export default function MailRoutingPanel() {
  const [data, setData]       = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError]     = useState(null)

  const load = useCallback(() => {
    setLoading(true); setError(null)
    api.get('/admin/email/routing')
      .then(r => setData(r.data))
      .catch(err => setError(err.response?.data?.detail || 'Could not load mail routing.'))
      .finally(() => setLoading(false))
  }, [])
  useEffect(() => { load() }, [load])

  const header = (
    <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 6 }}>
      <Route size={18} style={{ color: 'var(--accent-primary)' }} />
      <h3 style={{ margin: 0, fontSize: '1rem' }}>Mail Routing</h3>
      <span style={{ flex: 1 }} />
      <button type="button" className="btn btn-secondary"
              style={{ padding: '4px 10px', fontSize: '0.8rem' }}
              onClick={load} disabled={loading}>
        <RefreshCw size={14} style={{ marginRight: 4 }} /> {loading ? 'Loading…' : 'Refresh'}
      </button>
    </div>
  )

  if (loading || error || !data) {
    return (
      <div className="card" style={{ padding: 24 }}>
        {header}
        <div style={{ ...muted, padding: '14px 0' }}>
          {error
            ? <span style={{ color: RED }}>{error}</span>
            : 'Resolving which mailbox each email actually sends from…'}
        </div>
      </div>
    )
  }

  const { chain = [], steps = [], delivery = {} } = data
  const active = chain.find(c => c.active) || null

  // Delivery banner. The dangerous state is "queued with jobs waiting": the API
  // reports every send as successful even when nothing is consuming the queue.
  const waiting = delivery.mode === 'queued' && (delivery.queue_depth || 0) > 0
  const deliveryView = !delivery.email_enabled
    ? { color: RED, title: 'Email sending is turned off',
        detail: 'EMAIL_ENABLED is false — every send is skipped. No mail is leaving the system.' }
    : waiting
      ? { color: AMBER, title: `Queued — ${delivery.queue_depth} job(s) waiting`,
          detail: 'If this number does not fall, no email worker is consuming the queue. Sends will be reported as successful while no mail is delivered.' }
      : delivery.mode === 'queued'
        ? { color: GREEN, title: 'Queued delivery',
            detail: 'Mail is handed to the email worker, with per-mailbox rate limiting and retries.' }
        : { color: AMBER, title: 'Inline delivery',
            detail: delivery.sync_forced
              ? 'EMAIL_SYNC is set, so mail sends inside the request. Rate limiting and retries are inactive.'
              : 'No Redis reachable, so mail sends inside the request. Rate limiting and retries are inactive.' }

  const effectiveFor = (s) => {
    if (s.mailbox && s.mailbox.usable) {
      return { from: s.mailbox.from_email, via: `Mailbox: ${s.mailbox.name}`, color: 'var(--accent-primary)' }
    }
    return {
      from: active?.sender?.from_email || '—',
      via: active ? active.label : 'Unresolved',
      color: 'var(--text-tertiary)',
    }
  }

  return (
    <div className="card" style={{ padding: 24 }}>
      {header}
      <p style={{ ...muted, marginBottom: 16 }}>
        Where each email actually comes from. A step falls back to the active sender below
        whenever its own mailbox is missing, inactive, or has no password.
      </p>

      {/* 1. Delivery status */}
      <div style={{ ...band(deliveryView.color), borderRadius: 8, marginBottom: 20 }}>
        {deliveryView.color === GREEN
          ? <CheckCircle2 size={20} style={{ color: GREEN, flexShrink: 0 }} />
          : <AlertTriangle size={20} style={{ color: deliveryView.color, flexShrink: 0 }} />}
        <div style={{ flex: 1 }}>
          <div style={{ fontWeight: 600, fontSize: '0.92rem' }}>{deliveryView.title}</div>
          <div style={{ ...muted, marginTop: 2 }}>{deliveryView.detail}</div>
        </div>
      </div>

      {/* 2. Sender chain */}
      <h4 style={{ margin: '0 0 4px', fontSize: '0.9rem' }}>Sender chain</h4>
      <p style={{ ...muted, marginBottom: 10 }}>
        Checked top to bottom; the first configured layer wins.
      </p>
      <div style={{ display: 'grid', gap: 8, marginBottom: 22 }}>
        {chain.map(layer => (
          <div key={layer.layer} style={{
            display: 'flex', alignItems: 'center', gap: 12, padding: '10px 14px',
            border: `1px solid ${layer.active ? 'var(--accent-primary)' : 'var(--border, #e5e7eb)'}`,
            borderRadius: 8,
            opacity: layer.configured ? 1 : 0.55,
          }}>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                <span style={{ fontWeight: 600, fontSize: '0.9rem' }}>{layer.label}</span>
                <span style={badge('var(--text-tertiary)')}>{layer.applies_to}</span>
                {layer.active && <span style={badge(GREEN)}>Active</span>}
              </div>
              <div style={{ ...muted, marginTop: 2, wordBreak: 'break-word' }}>
                {layer.sender?.from_email
                  ? <>{layer.sender.from_email} · {layer.sender.host}:{layer.sender.port} — </>
                  : null}
                {layer.detail}
              </div>
            </div>
          </div>
        ))}
      </div>

      {/* 3. Per-step routing */}
      <h4 style={{ margin: '0 0 4px', fontSize: '0.9rem' }}>Per-step routing</h4>
      <p style={{ ...muted, marginBottom: 10 }}>
        HR assigns mailboxes per step under Pipeline Notifications.
      </p>
      <div style={{ display: 'grid', gap: 8 }}>
        {steps.map(s => {
          const eff = effectiveFor(s)
          return (
            <div key={s.step_key} style={{
              padding: '10px 14px', borderRadius: 8,
              border: '1px solid var(--border, #e5e7eb)',
            }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
                <span style={{ fontWeight: 600, fontSize: '0.88rem' }}>{s.label}</span>
                <span style={badge(eff.color)}>{eff.via}</span>
                <span style={{ flex: 1 }} />
                <span style={{ ...muted, wordBreak: 'break-all' }}>{eff.from}</span>
              </div>

              {s.cc.length > 0 && (
                <div style={{ ...muted, marginTop: 4 }}>CC: {s.cc.join(', ')}</div>
              )}

              {s.mailbox?.usable && s.mailbox.hourly_limit ? (
                <div style={{ ...muted, marginTop: 4 }}>
                  Limit: {s.mailbox.hourly_limit}/hour
                </div>
              ) : null}

              {/* The whole point of this screen: say why a chosen mailbox is ignored. */}
              {s.fallback_reason && (
                <div style={{ marginTop: 6, fontSize: '0.78rem', color: AMBER,
                              display: 'flex', alignItems: 'flex-start', gap: 6 }}>
                  <AlertTriangle size={13} style={{ flexShrink: 0, marginTop: 2 }} />
                  <span>
                    {s.mailbox?.name ? <><strong>{s.mailbox.name}</strong>: </> : null}
                    {s.fallback_reason}. Sending from the address above instead.
                  </span>
                </div>
              )}

              {/* Disabled steps still send today — the HR toggle is not yet enforced
                  anywhere in the send path, so do not render this as "off". */}
              {!s.enabled && (
                <div style={{ marginTop: 6, fontSize: '0.78rem', color: AMBER,
                              display: 'flex', alignItems: 'flex-start', gap: 6 }}>
                  <AlertTriangle size={13} style={{ flexShrink: 0, marginTop: 2 }} />
                  <span>Disabled in HR settings — not yet enforced; mail still sends.</span>
                </div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
