import React, { useEffect, useState, useCallback } from 'react'
import { Route, CheckCircle2, AlertTriangle, RefreshCw, Ban } from 'lucide-react'
import api from '../../api/client'

const AMBER = '#f59e0b'
const RED   = '#dc2626'
const GREEN = '#22c55e'
const GREY  = '#6b7280'

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

const SOURCE_LABEL = {
  requested: 'Chosen sender',
  route: 'Step sender',
  tenant_default: 'Company default',
  platform_auth: 'Platform (verification codes)',
  platform_route: 'Platform sender',
}

// What one email will actually do: send (and from where), be blocked (and why),
// or nothing at all because it is switched off.
export function effectiveView(eff) {
  if (!eff || eff.status === 'ok') {
    return {
      color: 'var(--accent-primary)',
      label: SOURCE_LABEL[eff?.route_source] || 'Sender',
      from: eff?.from_email || '—',
    }
  }
  if (eff.status === 'disabled') {
    return { color: GREY, label: 'Switched off', from: 'Not sent' }
  }
  return { color: RED, label: 'Blocked', from: 'Not sent', reason: eff.reason }
}

function deliveryBanner(delivery) {
  const outbox = delivery.outbox || {}
  const pending = outbox.pending || 0
  if (!delivery.email_enabled) {
    return { color: RED, title: 'Email sending is paused',
             detail: 'EMAIL_ENABLED is false. Messages are kept and deferred; none leave until it is turned back on.' }
  }
  if (!delivery.redis_available) {
    return { color: pending ? RED : AMBER,
             title: pending ? `Queue unavailable — ${pending} email(s) waiting` : 'Queue unavailable',
             detail: 'Redis cannot be reached. Emails are kept in the outbox and go out once it is back; only verification and password-reset codes are sent immediately.' }
  }
  if (delivery.dispatcher_heartbeat_age_s == null || delivery.dispatcher_heartbeat_age_s > 120) {
    return { color: AMBER, title: 'Outbox dispatcher not reporting',
             detail: 'New mail is still sent, but retries and anything committed while the queue was down wait until the email dispatcher service is running.' }
  }
  if ((delivery.queue_depth || 0) > 0) {
    return { color: AMBER, title: `Queued — ${delivery.queue_depth} job(s) waiting`,
             detail: 'If this number does not fall, no email worker is consuming the queue.' }
  }
  return { color: GREEN, title: 'Delivering normally',
           detail: 'Mail goes through the outbox to the email worker, with per-mailbox rate limits and retries.' }
}

// Read-only view of how outgoing mail is routed: which sender each kind of email
// uses, which are blocked and why, and the state of the delivery pipeline.
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
            : 'Resolving which mailbox each email sends from…'}
        </div>
      </div>
    )
  }

  const { chain = [], steps = [], events = [], delivery = {} } = data
  const outbox = delivery.outbox || {}
  const banner = deliveryBanner(delivery)
  const stepKeys = new Set(steps.map(s => s.step_key))
  const otherEvents = events.filter(e => !stepKeys.has(e.event_type))
  const blockedCount = steps.filter(s => s.effective?.status === 'blocked').length
    + otherEvents.filter(e => e.status === 'blocked').length

  return (
    <div className="card" style={{ padding: 24 }}>
      {header}
      <p style={{ ...muted, marginBottom: 16 }}>
        Where each email comes from. If the sender chosen for an email cannot send, that email
        is <strong>blocked</strong> and listed here — it is never sent from a different address instead.
      </p>

      {/* 1. Delivery status */}
      <div style={{ ...band(banner.color), borderRadius: 8, marginBottom: 12 }}>
        {banner.color === GREEN
          ? <CheckCircle2 size={20} style={{ color: GREEN, flexShrink: 0 }} />
          : <AlertTriangle size={20} style={{ color: banner.color, flexShrink: 0 }} />}
        <div style={{ flex: 1 }}>
          <div style={{ fontWeight: 600, fontSize: '0.92rem' }}>{banner.title}</div>
          <div style={{ ...muted, marginTop: 2 }}>{banner.detail}</div>
        </div>
      </div>
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 20 }}>
        <span style={badge(GREEN)}>{outbox.accepted_24h || 0} accepted (24h)</span>
        <span style={badge(AMBER)}>{outbox.deferred_24h || 0} deferred</span>
        <span style={badge(RED)}>{outbox.blocked_24h || 0} blocked</span>
        <span style={badge(RED)}>{outbox.failed_24h || 0} failed</span>
        <span style={badge(AMBER)}>{outbox.unknown_24h || 0} outcome unknown</span>
        {outbox.pending ? (
          <span style={badge(AMBER)}>
            {outbox.pending} waiting in outbox{outbox.oldest_age_s ? ` (oldest ${Math.round(outbox.oldest_age_s / 60)} min)` : ''}
          </span>
        ) : null}
      </div>

      {blockedCount > 0 && (
        <div style={{ ...band(RED), borderRadius: 8, marginBottom: 20 }}>
          <Ban size={20} style={{ color: RED, flexShrink: 0 }} />
          <div style={{ fontWeight: 600, fontSize: '0.9rem' }}>
            {blockedCount} kind(s) of email will not be sent until a sender is fixed — see below.
          </div>
        </div>
      )}

      {/* 2. Sender chain */}
      <h4 style={{ margin: '0 0 4px', fontSize: '0.9rem' }}>How a sender is chosen</h4>
      <p style={{ ...muted, marginBottom: 10 }}>
        Checked top to bottom. The first sender that applies is used; if it cannot send, the email is blocked.
      </p>
      <div style={{ display: 'grid', gap: 8, marginBottom: 22 }}>
        {chain.map(layer => (
          <div key={layer.layer} style={{
            display: 'flex', alignItems: 'center', gap: 12, padding: '10px 14px',
            border: `1px solid ${layer.active ? 'var(--accent-primary)' : 'var(--border, #e5e7eb)'}`,
            borderRadius: 8,
            opacity: layer.configured === false ? 0.55 : 1,
          }}>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                <span style={{ fontWeight: 600, fontSize: '0.9rem' }}>{layer.label}</span>
                <span style={badge('var(--text-tertiary)')}>{layer.applies_to}</span>
                {layer.active && <span style={badge(GREEN)}>In use</span>}
              </div>
              <div style={{ ...muted, marginTop: 2, wordBreak: 'break-word' }}>
                {layer.sender?.from_email
                  ? <>{layer.sender.from_email}{layer.sender.host ? ` · ${layer.sender.host}:${layer.sender.port}` : ''} — </>
                  : null}
                {layer.detail}
              </div>
            </div>
          </div>
        ))}
      </div>

      {/* 3. Per-step routing */}
      <h4 style={{ margin: '0 0 4px', fontSize: '0.9rem' }}>Pipeline emails</h4>
      <p style={{ ...muted, marginBottom: 10 }}>
        HR assigns senders per step, or switches a step off, under Pipeline Notifications.
      </p>
      <div style={{ display: 'grid', gap: 8, marginBottom: 22 }}>
        {steps.map(s => {
          const eff = effectiveView(s.effective)
          return (
            <div key={s.step_key} style={{
              padding: '10px 14px', borderRadius: 8,
              border: `1px solid ${eff.color === RED ? RED : 'var(--border, #e5e7eb)'}`,
            }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
                <span style={{ fontWeight: 600, fontSize: '0.88rem' }}>{s.label}</span>
                <span style={badge(eff.color)}>{eff.label}</span>
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

              {eff.reason && (
                <div style={{ marginTop: 6, fontSize: '0.78rem', color: RED,
                              display: 'flex', alignItems: 'flex-start', gap: 6 }}>
                  <AlertTriangle size={13} style={{ flexShrink: 0, marginTop: 2 }} />
                  <span>
                    {s.mailbox?.name ? <><strong>{s.mailbox.name}</strong>: </> : null}
                    {eff.reason} This email will not be sent until that is fixed.
                  </span>
                </div>
              )}
            </div>
          )
        })}
      </div>

      {/* 4. Everything else the company sends */}
      {otherEvents.length > 0 && (
        <>
          <h4 style={{ margin: '0 0 4px', fontSize: '0.9rem' }}>Other emails</h4>
          <p style={{ ...muted, marginBottom: 10 }}>
            Verification codes, reminders and API messages.
          </p>
          <div style={{ display: 'grid', gap: 8 }}>
            {otherEvents.map(e => {
              const eff = effectiveView(e)
              return (
                <div key={e.event_type} style={{
                  padding: '8px 14px', borderRadius: 8,
                  border: `1px solid ${eff.color === RED ? RED : 'var(--border, #e5e7eb)'}`,
                }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
                    <span style={{ fontWeight: 600, fontSize: '0.85rem' }}>{e.label}</span>
                    <span style={badge(eff.color)}>{eff.label}</span>
                    <span style={{ flex: 1 }} />
                    <span style={{ ...muted, wordBreak: 'break-all' }}>{eff.from}</span>
                  </div>
                  {eff.reason && (
                    <div style={{ ...muted, color: RED, marginTop: 4 }}>{eff.reason}</div>
                  )}
                </div>
              )
            })}
          </div>
        </>
      )}
    </div>
  )
}
