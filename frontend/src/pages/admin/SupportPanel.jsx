import { useEffect, useState, useCallback } from 'react'
import { useSearchParams } from 'react-router-dom'
import { LifeBuoy, Plus, Send, ArrowLeft, X } from 'lucide-react'
import api from '../../api/client'

const CATEGORIES = [
  { v: 'general',   l: 'General' },
  { v: 'billing',   l: 'Billing' },
  { v: 'technical', l: 'Technical' },
  { v: 'feature',   l: 'Feature request' },
  { v: 'other',     l: 'Other' },
]

const PRIORITIES = [
  { v: 'low',    l: 'Low' },
  { v: 'normal', l: 'Normal' },
  { v: 'high',   l: 'High' },
  { v: 'urgent', l: 'Urgent' },
]

const STATUS_BADGE = {
  open:        { cls: 'badge-blue',  l: 'Open' },
  in_progress: { cls: 'badge-amber', l: 'In progress' },
  resolved:    { cls: 'badge-green', l: 'Resolved' },
  closed:      { cls: 'badge-neutral', l: 'Closed' },
}

const fmtDate = (iso) => {
  if (!iso) return ''
  const d = new Date(iso)
  return d.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
}

const EMPTY_FORM = { subject: '', category: 'general', priority: 'normal', body: '' }

export default function SupportPanel() {
  const [searchParams, setSearchParams] = useSearchParams()
  const activeId = searchParams.get('ticket') || null

  const [tickets, setTickets] = useState([])
  const [loading, setLoading] = useState(false)
  const [ticket, setTicket] = useState(null)
  const [ticketLoading, setTicketLoading] = useState(false)
  const [reply, setReply] = useState('')
  const [sending, setSending] = useState(false)
  const [showNew, setShowNew] = useState(false)
  const [form, setForm] = useState(EMPTY_FORM)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState(null)
  const [statusFilter, setStatusFilter] = useState('')

  const loadList = useCallback(() => {
    setLoading(true)
    const params = statusFilter ? { status_filter: statusFilter } : {}
    api.get('/support/requests', { params })
      .then(r => setTickets(r.data || []))
      .catch(() => setTickets([]))
      .finally(() => setLoading(false))
  }, [statusFilter])

  const loadTicket = useCallback((id) => {
    if (!id) { setTicket(null); return }
    setTicketLoading(true)
    api.get(`/support/requests/${id}`)
      .then(r => setTicket(r.data))
      .catch(() => setTicket(null))
      .finally(() => setTicketLoading(false))
  }, [])

  useEffect(() => { loadList() }, [loadList])
  useEffect(() => { loadTicket(activeId) }, [activeId, loadTicket])

  const openTicket = (id) => setSearchParams({ ticket: id })
  const closeTicket = () => setSearchParams({})

  const submitNew = async (e) => {
    e.preventDefault()
    setSubmitting(true); setError(null)
    try {
      const r = await api.post('/support/requests', form)
      setShowNew(false)
      setForm(EMPTY_FORM)
      await loadList()
      openTicket(r.data.id)
    } catch (err) {
      setError(err.response?.data?.detail || 'Failed to create ticket')
    }
    setSubmitting(false)
  }

  const submitReply = async (e) => {
    e.preventDefault()
    if (!reply.trim() || !ticket) return
    setSending(true)
    try {
      const r = await api.post(`/support/requests/${ticket.id}/messages`, { body: reply })
      setTicket(r.data)
      setReply('')
      loadList()
    } catch (err) {
      alert(err.response?.data?.detail || 'Failed to send reply')
    }
    setSending(false)
  }

  // ───────────────────────────────────────── thread view
  if (activeId) {
    return (
      <div style={{ maxWidth: 880 }}>
        <button className="btn btn-ghost btn-sm" onClick={closeTicket} style={{ marginBottom: 12 }}>
          <ArrowLeft size={14} /> All tickets
        </button>

        {ticketLoading && <div className="card" style={{ padding: 24 }}>Loading…</div>}

        {!ticketLoading && !ticket && (
          <div className="card" style={{ padding: 24 }}>Ticket not found.</div>
        )}

        {ticket && (
          <>
            <div className="card" style={{ padding: 20, marginBottom: 16 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 16 }}>
                <div style={{ minWidth: 0 }}>
                  <div style={{ fontSize: '1.15rem', fontWeight: 700, marginBottom: 6 }}>{ticket.subject}</div>
                  <div style={{ fontSize: '0.82rem', color: 'var(--text-tertiary)' }}>
                    Opened {fmtDate(ticket.created_at)} · {ticket.category} · priority {ticket.priority}
                  </div>
                </div>
                <span className={`badge ${STATUS_BADGE[ticket.status]?.cls || 'badge-neutral'}`}>
                  {STATUS_BADGE[ticket.status]?.l || ticket.status}
                </span>
              </div>
            </div>

            <div className="card" style={{ padding: 20, marginBottom: 16, display: 'flex', flexDirection: 'column', gap: 12 }}>
              {ticket.messages.map(m => {
                const mine = m.sender_role === 'admin'
                return (
                  <div key={m.id} style={{ display: 'flex', justifyContent: mine ? 'flex-end' : 'flex-start' }}>
                    <div style={{
                      maxWidth: '75%',
                      background: mine ? 'rgba(14,79,160,0.08)' : '#f4f5f7',
                      border: `1px solid ${mine ? 'rgba(14,79,160,0.2)' : 'rgba(0,0,0,0.06)'}`,
                      borderRadius: 12,
                      padding: '10px 14px',
                    }}>
                      <div style={{ fontSize: '0.72rem', color: 'var(--text-tertiary)', marginBottom: 4, fontWeight: 600 }}>
                        {m.sender_name} · {m.sender_role === 'superadmin' ? 'Platform team' : 'You'} · {fmtDate(m.created_at)}
                      </div>
                      <div style={{ whiteSpace: 'pre-wrap', fontSize: '0.92rem', color: '#1f2937' }}>{m.body}</div>
                    </div>
                  </div>
                )
              })}
            </div>

            {ticket.status !== 'closed' && (
              <form onSubmit={submitReply} className="card" style={{ padding: 16 }}>
                <textarea
                  className="textarea"
                  rows={4}
                  placeholder="Write a reply…"
                  value={reply}
                  onChange={e => setReply(e.target.value)}
                  required
                />
                <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 10 }}>
                  <button className="btn btn-primary" disabled={sending || !reply.trim()}>
                    <Send size={14} /> {sending ? 'Sending…' : 'Send reply'}
                  </button>
                </div>
              </form>
            )}
          </>
        )}
      </div>
    )
  }

  // ───────────────────────────────────────── list view
  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 16, gap: 12, flexWrap: 'wrap' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <LifeBuoy size={20} style={{ color: 'var(--accent-primary)' }} />
          <div>
            <div style={{ fontWeight: 700, fontSize: '1.05rem' }}>Help &amp; Support</div>
            <div style={{ fontSize: '0.82rem', color: 'var(--text-tertiary)' }}>
              Reach the HireFlow platform team for help with billing, custom domains, SMTP, or anything else.
            </div>
          </div>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <select
            className="select"
            value={statusFilter}
            onChange={e => setStatusFilter(e.target.value)}
            style={{ minWidth: 140 }}
          >
            <option value="">All statuses</option>
            <option value="open">Open</option>
            <option value="in_progress">In progress</option>
            <option value="resolved">Resolved</option>
            <option value="closed">Closed</option>
          </select>
          <button className="btn btn-primary" onClick={() => setShowNew(true)}>
            <Plus size={14} /> New request
          </button>
        </div>
      </div>

      <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
        {loading ? (
          <div style={{ padding: 24 }}>Loading…</div>
        ) : tickets.length === 0 ? (
          <div style={{ padding: 32, textAlign: 'center', color: 'var(--text-tertiary)' }}>
            No tickets yet. Click <b>New request</b> to start a conversation with the platform team.
          </div>
        ) : (
          <div className="table-wrapper">
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead>
                <tr style={{ background: '#f8fafc' }}>
                  <th style={th}>Subject</th>
                  <th style={th}>Category</th>
                  <th style={th}>Priority</th>
                  <th style={th}>Status</th>
                  <th style={th}>Updated</th>
                </tr>
              </thead>
              <tbody>
                {tickets.map(t => (
                  <tr key={t.id} onClick={() => openTicket(t.id)}
                      style={{ cursor: 'pointer', borderTop: '1px solid #eef0f3' }}>
                    <td style={td}>
                      <div style={{ fontWeight: 600 }}>{t.subject}</div>
                      <div style={{ fontSize: '0.78rem', color: 'var(--text-tertiary)' }}>
                        {t.message_count} message{t.message_count === 1 ? '' : 's'}
                      </div>
                    </td>
                    <td style={td}>{t.category}</td>
                    <td style={td}>{t.priority}</td>
                    <td style={td}>
                      <span className={`badge ${STATUS_BADGE[t.status]?.cls || 'badge-neutral'}`}>
                        {STATUS_BADGE[t.status]?.l || t.status}
                      </span>
                    </td>
                    <td style={{ ...td, color: 'var(--text-tertiary)', fontSize: '0.85rem' }}>{fmtDate(t.updated_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {showNew && (
        <div className="modal-overlay" onClick={() => setShowNew(false)}>
          <div className="modal" onClick={e => e.stopPropagation()} style={{ maxWidth: 560 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
              <h3 style={{ margin: 0 }}>New support request</h3>
              <button className="btn btn-ghost btn-sm" onClick={() => setShowNew(false)}><X size={16} /></button>
            </div>
            <form onSubmit={submitNew} className="auth-form">
              <div className="input-group">
                <label>Subject</label>
                <input
                  className="input"
                  required minLength={3} maxLength={255}
                  value={form.subject}
                  onChange={e => setForm({ ...form, subject: e.target.value })}
                  placeholder="Short summary of the issue"
                />
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
                <div className="input-group">
                  <label>Category</label>
                  <select className="select" value={form.category}
                          onChange={e => setForm({ ...form, category: e.target.value })}>
                    {CATEGORIES.map(c => <option key={c.v} value={c.v}>{c.l}</option>)}
                  </select>
                </div>
                <div className="input-group">
                  <label>Priority</label>
                  <select className="select" value={form.priority}
                          onChange={e => setForm({ ...form, priority: e.target.value })}>
                    {PRIORITIES.map(p => <option key={p.v} value={p.v}>{p.l}</option>)}
                  </select>
                </div>
              </div>
              <div className="input-group">
                <label>Describe the issue</label>
                <textarea
                  className="textarea"
                  rows={6}
                  required
                  value={form.body}
                  onChange={e => setForm({ ...form, body: e.target.value })}
                  placeholder="Provide details — steps to reproduce, error messages, screenshots links, etc."
                />
              </div>
              {error && <div style={{ color: '#dc2626', fontSize: '0.85rem', marginBottom: 8 }}>{error}</div>}
              <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
                <button type="button" className="btn btn-secondary" onClick={() => setShowNew(false)}>Cancel</button>
                <button type="submit" className="btn btn-primary" disabled={submitting}>
                  {submitting ? 'Sending…' : 'Submit'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  )
}

const th = { textAlign: 'left', padding: '12px 16px', fontSize: '0.78rem', fontWeight: 600, color: 'var(--text-tertiary)', textTransform: 'uppercase', letterSpacing: '0.04em' }
const td = { padding: '14px 16px', fontSize: '0.9rem', color: '#1f2937' }
