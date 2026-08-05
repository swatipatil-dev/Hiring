import { Briefcase, Eye } from 'lucide-react'
import api from '../../../api/client'
import { STATUS_BADGES } from '../utils'

export default function ApplicationsTab({ apps, onOpenDetails, reportApiError }) {
  const openDetails = (a) => {
    onOpenDetails({ modal: a, details: null, loading: true })
    api.get(`/candidate/applications/${a.id}`)
      .then(r => onOpenDetails({ modal: a, details: r.data, loading: false }))
      .catch((err) => {
        onOpenDetails({ modal: a, details: null, loading: false })
        reportApiError('Load application details')(err)
      })
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
                  <td><span className={`badge ${STATUS_BADGES[a.status] || 'badge-neutral'}`}>{a.status}</span></td>
                  <td>
                    <button className="btn btn-ghost btn-sm" onClick={() => openDetails(a)}>
                      <Eye size={14} /> View
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
