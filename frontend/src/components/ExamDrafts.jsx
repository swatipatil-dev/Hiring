import { useState, useEffect, useCallback } from 'react'
import { Edit3, Trash2, Loader2 } from 'lucide-react'
import api from '../api/client'

/**
 * Unfinished test drafts — shared by the Interviewer and HR dashboards.
 *
 * A draft is created implicitly: CreateTestWizard.ensureDraft() fires on the first generated
 * or hand-added question, not on save. Walk away from the wizard after adding one question
 * and a DRAFT interview now owns that job — and because the Step-1 job dropdown hides any job
 * whose has_test is true (and a DRAFT counts), the job silently disappears from the list of
 * jobs you can build a test for. This view is the only way back: resume the draft, or delete
 * it to free the job.
 *
 * GET /interviewer/tests/drafts is company-scoped with no created_by filter
 * (_owned_templates_query), so this deliberately lists EVERY unfinished draft in the company,
 * not just the viewer's. That is the point — the job is blocked by whoever's draft owns it.
 *
 * Props:
 *  - onContinue: (draft) => void   opens the draft in that dashboard's Create Test wizard
 */
export default function ExamDrafts({ onContinue }) {
  const [drafts, setDrafts] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [deletingId, setDeletingId] = useState(null)

  // Never swallow the error: a failed request and an empty result must not look identical,
  // or "no drafts" and "the request died" are the same screen.
  const errText = (err) => err?.response?.data?.detail || err?.message || 'request failed'

  const refresh = useCallback(() => {
    setLoading(true); setError('')
    api.get('/interviewer/tests/drafts')
      .then(r => setDrafts(r.data?.drafts || []))
      .catch(err => { setDrafts([]); setError(`Couldn't load drafts — ${errText(err)}`) })
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => { refresh() }, [refresh])

  const handleDelete = async (d) => {
    const label = d.title || d.job_position || `Draft #${d.id}`
    const questions = d.question_count > 0
      ? ` and its ${d.question_count} question${d.question_count === 1 ? '' : 's'}`
      : ''
    if (!window.confirm(
      `Delete "${label}"${questions}?\n\nThe job becomes available for a new test. This cannot be undone.`
    )) return

    setDeletingId(d.id)
    try {
      await api.delete(`/interviewer/interviews/${d.id}`)
      refresh()
    } catch (err) {
      setError(`Couldn't delete "${label}" — ${errText(err)}`)
    }
    setDeletingId(null)
  }

  return (
    <div className="card">
      <h3 style={{ marginBottom: 6 }}>Unfinished Drafts</h3>
      <div className="text-sm text-muted" style={{ marginBottom: 16 }}>
        A job stays out of the Create Test list while an unfinished draft owns it. Continue the
        draft, or delete it to free the job.
      </div>

      {error && (
        <div className="card mb-16" style={{ padding: '10px 16px', fontSize: '0.85rem', color: '#ef4444' }}>
          {error}
        </div>
      )}

      {loading ? (
        <div style={{ padding: 30, textAlign: 'center', color: 'var(--text-tertiary)' }}>Loading…</div>
      ) : drafts.length === 0 && !error ? (
        <div style={{ padding: 30, textAlign: 'center', color: 'var(--text-tertiary)' }}>
          No unfinished drafts.
        </div>
      ) : drafts.length === 0 ? null : (
        <div className="table-wrapper">
          <table>
            <thead>
              <tr><th>Title</th><th>Type</th><th>Questions</th><th>Last edited</th><th>Action</th></tr>
            </thead>
            <tbody>
              {drafts.map(d => (
                <tr key={d.id}>
                  <td style={{ fontWeight: 600 }}>{d.title || d.job_position || `Draft #${d.id}`}</td>
                  <td><span className="badge badge-blue">{d.interview_configuration?.interview_type || 'N/A'}</span></td>
                  <td>
                    {d.question_count > 0
                      ? <span className="badge badge-green">{d.question_count}</span>
                      : <span className="badge badge-neutral" title="Nothing generated yet">empty</span>}
                  </td>
                  <td className="mono text-sm">
                    {d.updated_at ? new Date(d.updated_at).toLocaleDateString() : '—'}
                  </td>
                  <td>
                    <div className="flex gap-8">
                      <button className="btn btn-ghost btn-sm" onClick={() => onContinue?.(d)}>
                        <Edit3 size={14} /> Continue
                      </button>
                      <button className="btn btn-danger btn-sm"
                        disabled={deletingId === d.id}
                        onClick={() => handleDelete(d)}>
                        {deletingId === d.id
                          ? <><Loader2 size={14} className="spin" /> Deleting…</>
                          : <><Trash2 size={14} /> Delete</>}
                      </button>
                    </div>
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
