import { useState } from 'react'
import { Briefcase, Eye } from 'lucide-react'
import api from '../../../api/client'
import { STATUS_BADGES } from '../utils'
import ConfirmDialog from '../../../components/workflow/ConfirmDialog'

export default function ApplicationsTab({ apps, onOpenDetails, reportApiError, onChanged }) {
  const [withdrawing, setWithdrawing] = useState(null)   // the app being withdrawn
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const openDetails = (a) => {
    onOpenDetails({ modal: a, details: null, loading: true })
    api.get(`/candidate/applications/${a.id}`)
      .then(r => onOpenDetails({ modal: a, details: r.data, loading: false }))
      .catch((err) => {
        onOpenDetails({ modal: a, details: null, loading: false })
        reportApiError('Load application details')(err)
      })
  }

  const withdraw = async () => {
    setBusy(true); setError('')
    try {
      await api.post(`/candidate/applications/${withdrawing.id}/withdraw`, { confirm: true, reason: reason || null })
      setWithdrawing(null); setReason('')
      onChanged?.()
    } catch (e) {
      setError(e?.response?.data?.detail || 'Could not withdraw the application')
    }
    setBusy(false)
  }

  return (
    <div className="card">
      <h3 style={{ marginBottom: 16 }}>My Applications</h3>
      {apps.length === 0 ? (
        <div style={{ padding: 40, textAlign: 'center', color: 'var(--text-tertiary)' }}>
          <Briefcase size={48} style={{ marginBottom: 12, opacity: 0.3 }} />
          <div>No applications yet. Browse jobs and apply!</div>
        </div>
      ) : (
        <div className="table-wrapper">
          <table>
            <thead><tr><th>Position</th><th>Applied</th><th>Status</th><th>Details</th></tr></thead>
            <tbody>
              {apps.map(a => (
                <tr key={a.id}>
                  <td style={{ fontWeight: 600 }}>{a.job}</td>
                  <td className="mono text-sm text-muted">{a.applied}</td>
                  <td>
                    <span className={`badge ${STATUS_BADGES[a.status] || 'badge-neutral'}`}>{a.statusLabel || a.status}</span>
                    {a.statusMessage && <div className="text-sm text-muted" style={{ marginTop: 4, maxWidth: 280 }}>{a.statusMessage}</div>}
                  </td>
                  <td style={{ whiteSpace: 'nowrap' }}>
                    <button className="btn btn-ghost btn-sm" onClick={() => openDetails(a)}>
                      <Eye size={14} /> View
                    </button>
                    {a.canWithdraw && (
                      <button className="btn btn-ghost btn-sm" style={{ color: '#dc2626' }}
                        onClick={() => { setError(''); setWithdrawing(a) }}>
                        Withdraw
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {withdrawing && (
        <ConfirmDialog title={`Withdraw your application for ${withdrawing.job}?`}
          confirmLabel="Withdraw application" danger busy={busy}
          onConfirm={withdraw} onCancel={() => setWithdrawing(null)}>
          <p style={{ marginBottom: 10 }}>
            The recruiting team will be told, and any upcoming interview or test for this job will be cancelled.
            You can’t undo this yourself — you would need to apply again or contact the recruiter.
          </p>
          <label className="text-sm" style={{ display: 'block' }}>Reason (optional, shared with the recruiter)
            <textarea className="textarea" rows={2} maxLength={2000} value={reason} onChange={e => setReason(e.target.value)} />
          </label>
          {error && <p role="alert" style={{ color: '#dc2626', marginTop: 8 }}>{String(error)}</p>}
        </ConfirmDialog>
      )}
    </div>
  )
}
