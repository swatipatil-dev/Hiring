import { useEffect, useState } from 'react'
import { LifeBuoy } from 'lucide-react'
import api from '../../../api/client'
import { Card, CardHeader, Notice } from './ProfileFields'

/*
 * Company admins decide whether platform support may read their company's
 * candidate and interview data, and for how long. Without an active grant the
 * server refuses those reads to support staff. Every read support makes is
 * listed under "Recent support activity".
 */

const fmt = (iso) => (iso ? new Date(iso).toLocaleString() : '—')

export default function SupportAccessCard() {
  const [grants, setGrants] = useState([])
  const [log, setLog] = useState([])
  const [reason, setReason] = useState('')
  const [hours, setHours] = useState(24)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const load = () => {
    api.get('/support/access-grants').then(r => setGrants(r.data)).catch(() => {})
    api.get('/support/access-log?limit=20').then(r => setLog(r.data.entries || [])).catch(() => {})
  }
  useEffect(() => { load() }, [])

  const active = grants.find(g => g.active)

  const grant = async (e) => {
    e.preventDefault()
    setError(''); setBusy(true)
    try {
      await api.post('/support/access-grants', { reason: reason.trim(), hours: Number(hours) })
      setReason('')
      load()
    } catch (err) {
      setError(err.response?.data?.detail?.toString?.() || 'Could not grant access.')
    }
    setBusy(false)
  }

  const revoke = async (id) => {
    setBusy(true)
    try { await api.delete(`/support/access-grants/${id}`); load() } catch { setError('Could not revoke.') }
    setBusy(false)
  }

  return (
    <Card>
      <CardHeader icon={LifeBuoy} title="Support access" />
      <p style={{ fontSize: '0.85rem', color: 'var(--text-secondary)', marginTop: 0 }}>
        Platform support cannot see your candidates, interviews or test results unless you allow it here.
      </p>

      {active ? (
        <div style={{ fontSize: '0.86rem' }}>
          Access is <strong>on</strong> until {fmt(active.expires_at)} — “{active.reason}”.
          <button className="btn btn-secondary btn-sm" style={{ marginLeft: 10 }}
                  onClick={() => revoke(active.id)} disabled={busy}>Revoke now</button>
        </div>
      ) : (
        <form onSubmit={grant} style={{ display: 'grid', gap: 10, maxWidth: 460 }}>
          <label className="mp-edit-field">
            <span>Reason (e.g. the ticket you raised)</span>
            <input className="input" value={reason} onChange={e => setReason(e.target.value)}
                   minLength={3} maxLength={500} required />
          </label>
          <label className="mp-edit-field">
            <span>For</span>
            <select className="input" value={hours} onChange={e => setHours(e.target.value)}>
              <option value={4}>4 hours</option>
              <option value={24}>24 hours</option>
              <option value={72}>3 days</option>
              <option value={168}>7 days</option>
            </select>
          </label>
          <div>
            <button className="btn btn-primary btn-sm" type="submit" disabled={busy}>Allow support access</button>
          </div>
        </form>
      )}

      {log.length > 0 && (
        <details style={{ marginTop: 14, fontSize: '0.82rem' }}>
          <summary style={{ cursor: 'pointer' }}>Recent support activity</summary>
          <ul style={{ paddingLeft: 18, marginBottom: 0 }}>
            {log.map(e => (
              <li key={e.id}>
                {fmt(e.created_at)} — {e.actor_email || 'unknown'} · {e.action}
                {e.resource ? ` · ${e.resource}` : ''}
              </li>
            ))}
          </ul>
        </details>
      )}

      <Notice kind="error">{error}</Notice>
    </Card>
  )
}
