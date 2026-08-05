import { useEffect, useState, useCallback } from 'react'
import { useSearchParams } from 'react-router-dom'
import { LifeBuoy, Send, ArrowLeft, CheckCircle2, RotateCcw, XCircle } from 'lucide-react'
import api from '../../api/client'
import Header from '../../components/Header'

const STATUS_BADGE = {
  open:        { cls: 'badge-blue',    l: 'Open' },
  in_progress: { cls: 'badge-amber',   l: 'In progress' },
  resolved:    { cls: 'badge-green',   l: 'Resolved' },
  closed:      { cls: 'badge-neutral', l: 'Closed' },
}

const fmtDate = (iso) => {
  if (!iso) return ''
  return new Date(iso).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
}

export default function SupportTicketsPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const activeId = searchParams.get('ticket') || null

  const [tickets, setTickets] = useState([])
  const [loading, setLoading] = useState(false)
  const [companies, setCompanies] = useState([])
  const [statusFilter, setStatusFilter] = useState('')
  const [companyFilter, setCompanyFilter] = useState('')

  const [ticket, setTicket] = useState(null)
  const [ticketLoading, setTicketLoading] = useState(false)
  const [reply, setReply] = useState('')
  const [sending, setSending] = useState(false)
  const [statusBusy, setStatusBusy] = useState(false)

  const loadList = useCallback(() => {
    setLoading(true)
    const params = {}
    if (statusFilter)  params.status_filter = statusFilter
    if (companyFilter) params.company_id   = companyFilter
    api.get('/support/admin/requests', { params })
      .then(r => setTickets(r.data || []))
      .catch(() => setTickets([]))
      .finally(() => setLoading(false))
  }, [statusFilter, companyFilter])

  const loadTicket = useCallback((id) => {
    if (!id) { setTicket(null); return }
    setTicketLoading(true)
    api.get(`/support/admin/requests/${id}`)
      .then(r => setTicket(r.data))
      .catch(() => setTicket(null))
      .finally(() => setTicketLoading(false))
  }, [])

  useEffect(() => {
    api.get('/superadmin/companies').then(r => setCompanies(r.data || [])).catch(() => {})
  }, [])

  useEffect(() => { loadList() }, [loadList])
  useEffect(() => { loadTicket(activeId) }, [activeId, loadTicket])

  const openTicket = (id) => setSearchParams({ ticket: id })
  const closeTicket = () => setSearchParams({})

  const submitReply = async (e) => {
    e.preventDefault()
    if (!reply.trim() || !ticket) return
    setSending(true)
    try {
      const r = await api.post(`/support/admin/requests/${ticket.id}/messages`, { body: reply })
      setTicket(r.data)
      setReply('')
      loadList()
    } catch (err) {
      alert(err.response?.data?.detail || 'Failed to send reply')
    }
    setSending(false)
  }

  const changeStatus = async (newStatus) => {
    if (!ticket) return
    setStatusBusy(true)
    try {
      const r = await api.put(`/support/admin/requests/${ticket.id}/status`, { status: newStatus })
      setTicket(r.data)
      loadList()
    } catch (err) {
      alert(err.response?.data?.detail || 'Failed to update status')
    }
    setStatusBusy(false)
  }

  // ───────────────────────────────────────── thread view
  if (activeId) {
    return (
      <>
        <Header title="Support" subtitle="Client admin help requests" />
        <div style={{ maxWidth: 920 }}>
          <button className="btn btn-ghost btn-sm" onClick={closeTicket} style={{ marginBottom: 12 }}>
            <ArrowLeft size={14} /> All tickets
          </button>

          {ticketLoading && <div className="card" style={{ padding: 24 }}>Loading…</div>}
          {!ticketLoading && !ticket && <div className="card" style={{ padding: 24 }}>Ticket not found.</div>}

          {ticket && (
            <>
              <div className="card" style={{ padding: 20, marginBottom: 16 }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 16, flexWrap: 'wrap' }}>
                  <div style={{ minWidth: 0 }}>
                    <div style={{ fontSize: '1.15rem', fontWeight: 700, marginBottom: 6 }}>{ticket.subject}</div>
                    <div style={{ fontSize: '0.82rem', color: 'var(--text-tertiary)' }}>
                      From <b>{ticket.requester_name}</b> · {ticket.company_name || 'Unknown company'} ·
                      {' '}opened {fmtDate(ticket.created_at)} · {ticket.category} · priority {ticket.priority}
                    </div>
                  </div>
                  <span className={`badge ${STATUS_BADGE[ticket.status]?.cls || 'badge-neutral'}`}>
                    {STATUS_BADGE[ticket.status]?.l || ticket.status}
                  </span>
                </div>

                <div style={{ display: 'flex', gap: 8, marginTop: 14, flexWrap: 'wrap' }}>
                  {ticket.status !== 'in_progress' && ticket.status !== 'closed' && (
                    <button className="btn btn-sm btn-secondary" disabled={statusBusy}
                            onClick={() => changeStatus('in_progress')}>
                      <RotateCcw size={14} /> Mark in progress
                    </button>
                  )}
                  {ticket.status !== 'resolved' && ticket.status !== 'closed' && (
                    <button className="btn btn-sm btn-primary" disabled={statusBusy}
                            onClick={() => changeStatus('resolved')}>
                      <CheckCircle2 size={14} /> Mark resolved
                    </button>
                  )}
                  {ticket.status !== 'closed' && (
                    <button className="btn btn-sm btn-danger" disabled={statusBusy}
                            onClick={() => changeStatus('closed')}>
                      <XCircle size={14} /> Close
                    </button>
                  )}
                  {ticket.status === 'closed' && (
                    <button className="btn btn-sm btn-secondary" disabled={statusBusy}
                            onClick={() => changeStatus('open')}>
                      <RotateCcw size={14} /> Reopen
                    </button>
                  )}
                </div>
              </div>

              <div className="card" style={{ padding: 20, marginBottom: 16, display: 'flex', flexDirection: 'column', gap: 12 }}>
                {ticket.messages.map(m => {
                  const mine = m.sender_role === 'superadmin'
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
                          {m.sender_name} · {m.sender_role === 'superadmin' ? 'Platform team' : 'Client admin'} · {fmtDate(m.created_at)}
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
      </>
    )
  }

  // ───────────────────────────────────────── list view
  return (
    <>
      <Header title="Support Tickets" subtitle="All client help requests" />
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 16, gap: 12, flexWrap: 'wrap' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <LifeBuoy size={20} style={{ color: 'var(--accent-primary)' }} />
          <div>
            <div style={{ fontWeight: 700, fontSize: '1.05rem' }}>Client support inbox</div>
            <div style={{ fontSize: '0.82rem', color: 'var(--text-tertiary)' }}>
              Tickets opened by admins of every client company. Reply to claim a ticket.
            </div>
          </div>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <select className="select" value={statusFilter} onChange={e => setStatusFilter(e.target.value)} style={{ minWidth: 140 }}>
            <option value="">All statuses</option>
            <option value="open">Open</option>
            <option value="in_progress">In progress</option>
            <option value="resolved">Resolved</option>
            <option value="closed">Closed</option>
          </select>
          <select className="select" value={companyFilter} onChange={e => setCompanyFilter(e.target.value)} style={{ minWidth: 180 }}>
            <option value="">All companies</option>
            {companies.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
        </div>
      </div>

      <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
        {loading ? (
          <div style={{ padding: 24 }}>Loading…</div>
        ) : tickets.length === 0 ? (
          <div style={{ padding: 32, textAlign: 'center', color: 'var(--text-tertiary)' }}>
            No tickets match the current filters.
          </div>
        ) : (
          <div className="table-wrapper">
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead>
                <tr style={{ background: '#f8fafc' }}>
                  <th style={th}>Subject</th>
                  <th style={th}>Company</th>
                  <th style={th}>Requester</th>
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
                        {t.category} · {t.message_count} message{t.message_count === 1 ? '' : 's'}
                      </div>
                    </td>
                    <td style={td}>{t.company_name || '—'}</td>
                    <td style={td}>{t.requester_name}</td>
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
    </>
  )
}

const th = { textAlign: 'left', padding: '12px 16px', fontSize: '0.78rem', fontWeight: 600, color: 'var(--text-tertiary)', textTransform: 'uppercase', letterSpacing: '0.04em' }
const td = { padding: '14px 16px', fontSize: '0.9rem', color: '#1f2937' }
