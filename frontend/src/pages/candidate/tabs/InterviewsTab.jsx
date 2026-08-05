import { Calendar, Send } from 'lucide-react'
import api from '../../../api/client'

export default function InterviewsTab({
  myInterviews,
  myOffers,
  activeInterview,
  setActiveInterview,
  interviewQuestions,
  setInterviewQuestions,
  setMyInterviews,
  setMyOffers,
  navigate,
  refreshApps,
  reportApiError,
}) {
  const respondToOffer = async (offerId, decision) => {
    if (!window.confirm(`Are you sure you want to ${decision} this offer?`)) return
    try {
      await api.post(`/evaluation/offers/${offerId}/respond`, { decision, notes: '' })
      api.get('/candidate/offers').then(r => setMyOffers(r.data?.offers || [])).catch(reportApiError('Refresh offers'))
      refreshApps()
    } catch (err) {
      alert(err.response?.data?.detail || `Failed to ${decision} offer`)
    }
  }

  const submitAnswers = async () => {
    try {
      await api.post(`/candidate/interviews/${activeInterview.id}/submit`, {})
    } catch (err) {
      reportApiError('Submit interview answers')(err)
      alert(err.response?.data?.detail || 'Failed to submit answers. Please try again.')
      return
    }
    alert('Answers submitted! Your interview is complete.')
    setActiveInterview(null)
    setInterviewQuestions([])
    api.get('/candidate/interviews').then(r => setMyInterviews(r.data || [])).catch(reportApiError('Refresh interviews after submit'))
  }

  if (activeInterview) {
    return (
      <div>
        <div className="flex-between mb-16">
          <div>
            <h3>{activeInterview.title}</h3>
            <span className="text-sm text-muted">{activeInterview.job_title} — {activeInterview.interview_type} — {activeInterview.difficulty_level}</span>
          </div>
          <button className="btn btn-secondary btn-sm" onClick={() => { setActiveInterview(null); setInterviewQuestions([]) }}>
            Back to Interviews
          </button>
        </div>

        {interviewQuestions.length === 0 ? (
          <div className="card" style={{ padding: 40, textAlign: 'center', color: 'var(--text-tertiary)' }}>
            Questions are not yet available. The interviewer needs to approve questions first.
          </div>
        ) : (
          interviewQuestions.map((q, i) => (
            <div key={q.id} className="card mb-16" style={{ marginBottom: 12 }}>
              <div className="flex-center gap-8" style={{ marginBottom: 10 }}>
                <span style={{
                  fontFamily: 'var(--font-mono)', fontSize: '0.75rem', color: 'var(--accent-primary)',
                  background: 'var(--accent-glow)', width: 28, height: 28, display: 'grid', placeItems: 'center',
                  borderRadius: 6, fontWeight: 600, flexShrink: 0
                }}>Q{i + 1}</span>
                <span className={`badge ${q.question_type === 'mcq' ? 'badge-blue' : q.question_type === 'coding' ? 'badge-purple' : 'badge-amber'}`}>
                  {q.question_type}
                </span>
                <span className="badge badge-neutral">{q.difficulty_level}</span>
                <span className="text-sm text-muted" style={{ marginLeft: 'auto' }}>{q.points} pts · {q.time_limit_minutes} min</span>
              </div>
              <div style={{ fontSize: '0.95rem', lineHeight: 1.6, marginBottom: 12 }}>{q.question_text}</div>
              {q.options && q.options.length > 0 && (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                  {q.options.map((opt, j) => (
                    <label key={j} style={{
                      display: 'flex', alignItems: 'center', gap: 10, padding: '8px 12px',
                      background: 'var(--bg-hover)', borderRadius: 8, cursor: 'pointer', fontSize: '0.9rem'
                    }}>
                      <input type="radio" name={`q-${q.id}`} />
                      <span>{String.fromCharCode(65 + j)}. {opt}</span>
                    </label>
                  ))}
                </div>
              )}
              {q.question_type === 'coding' && (
                <textarea className="textarea" rows={6} placeholder="Write your code here..."
                  style={{ fontFamily: 'var(--font-mono)', fontSize: '0.85rem', marginTop: 8 }} />
              )}
              {q.question_type === 'descriptive' && (
                <textarea className="textarea" rows={4} placeholder="Write your answer here..." style={{ marginTop: 8 }} />
              )}
            </div>
          ))
        )}

        {interviewQuestions.length > 0 && (
          <div style={{ textAlign: 'right', marginTop: 16 }}>
            <button className="btn btn-primary" onClick={submitAnswers}>
              <Send size={16} /> Submit Answers
            </button>
          </div>
        )}
      </div>
    )
  }

  const tests = myInterviews.filter(iv => !iv.is_scheduled_interview)
  const interviews = myInterviews.filter(iv => iv.is_scheduled_interview)

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
      <div className="card">
        <h3 style={{ marginBottom: 16, display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{ width: 8, height: 8, borderRadius: '50%', background: '#f59e0b', display: 'inline-block' }} />
          Scheduled Tests
        </h3>
        {tests.length === 0 ? (
          <div style={{ padding: 30, textAlign: 'center', color: 'var(--text-tertiary)' }}>
            <div>No tests scheduled yet</div>
          </div>
        ) : (
          <div className="table-wrapper">
            <table>
              <thead><tr><th>Title</th><th>Job</th><th>Type</th><th>Date</th><th>Duration</th><th>Status</th><th>Action</th></tr></thead>
              <tbody>
                {tests.map(iv => (
                  <tr key={iv.id}>
                    <td style={{ fontWeight: 600 }}>{iv.title}</td>
                    <td>{iv.job_title}</td>
                    <td><span className="badge badge-amber">{iv.interview_type}</span></td>
                    <td className="mono text-sm">{iv.scheduled_datetime ? new Date(iv.scheduled_datetime).toLocaleString() : '—'}</td>
                    <td className="mono">{iv.duration_minutes} min</td>
                    <td><span className={`badge ${iv.status === 'READY' ? 'badge-green' : iv.status === 'SCHEDULED' ? 'badge-amber' : 'badge-neutral'}`}>{iv.status}</span></td>
                    <td>
                      {(iv.status === 'READY' || iv.status === 'SCHEDULED') ? (
                        <button className="btn btn-sm" style={{ background: '#f59e0b', color: '#fff', border: 'none', borderRadius: 6, padding: '4px 12px', cursor: 'pointer', fontSize: '0.82rem', fontWeight: 600 }} onClick={() => navigate(`/test/${iv.id}`)}>
                          Take Test
                        </button>
                      ) : (
                        <span className="text-muted text-sm">{iv.status === 'COMPLETED' ? 'Completed' : 'Not ready'}</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <div className="card">
        <h3 style={{ marginBottom: 16, display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{ width: 8, height: 8, borderRadius: '50%', background: '#2b7cba', display: 'inline-block' }} />
          Scheduled Interviews
        </h3>
        {interviews.length === 0 ? (
          <div style={{ padding: 30, textAlign: 'center', color: 'var(--text-tertiary)' }}>
            <Calendar size={36} style={{ marginBottom: 10, opacity: 0.3 }} />
            <div>No interviews scheduled yet</div>
          </div>
        ) : (
          <div className="table-wrapper">
            <table>
              <thead><tr><th>Job</th><th>Type</th><th>Panel</th><th>Date & Time</th><th>Duration</th><th>Mode</th><th>Status</th></tr></thead>
              <tbody>
                {interviews.map(iv => {
                  const panel = Array.isArray(iv.panel_members) ? iv.panel_members : []
                  const panelText = panel.length > 0 ? panel.map(p => p.name).join(', ') : (iv.interviewer_name || '—')
                  return (
                  <tr key={iv.id}>
                    <td style={{ fontWeight: 600 }}>{iv.job_title}</td>
                    <td><span className="badge badge-blue">{iv.interview_type}</span></td>
                    <td>{panelText}{panel.length > 1 ? <span className="text-muted text-sm"> ({panel.length}-member panel)</span> : null}</td>
                    <td className="mono text-sm">{iv.scheduled_datetime ? new Date(iv.scheduled_datetime).toLocaleString() : '—'}</td>
                    <td className="mono">{iv.duration_minutes} min</td>
                    <td>
                      {iv.meeting_link
                        ? <a href={iv.meeting_link} target="_blank" rel="noreferrer" style={{ color: '#2563eb', fontWeight: 600, fontSize: '0.82rem' }}>Join Meeting</a>
                        : (iv.location
                            ? <span className="text-sm" style={{ fontWeight: 500 }}>📍 {iv.location}</span>
                            : <span className="text-muted text-sm">Offline</span>)}
                    </td>
                    <td><span className={`badge ${iv.status === 'COMPLETED' ? 'badge-green' : iv.status === 'SCHEDULED' ? 'badge-blue' : 'badge-neutral'}`}>{iv.status}</span></td>
                  </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <div className="card">
        <h3 style={{ marginBottom: 16, display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{ width: 8, height: 8, borderRadius: '50%', background: '#16a34a', display: 'inline-block' }} />
          Offer Letters
        </h3>
        {myOffers.length === 0 ? (
          <div style={{ padding: 30, textAlign: 'center', color: 'var(--text-tertiary)' }}>
            <div>No offers yet</div>
          </div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            {myOffers.map(offer => (
              <div key={offer.offer_id} style={{ border: '1px solid var(--border-subtle)', borderRadius: 10, padding: '16px 20px', background: offer.status === 'accepted' ? '#f0fdf4' : offer.status === 'rejected' ? '#fff1f2' : 'var(--bg-card)' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: 8 }}>
                  <div>
                    <div style={{ fontWeight: 700, fontSize: '1rem' }}>{offer.job_title}</div>
                    <div style={{ fontSize: '0.82rem', color: 'var(--text-secondary)', marginTop: 4 }}>
                      {offer.offer_type} &nbsp;·&nbsp; ₹{Number(offer.salary).toLocaleString('en-IN')} / year
                      {offer.bonus ? ` + ₹${Number(offer.bonus).toLocaleString('en-IN')} bonus` : ''}
                    </div>
                    <div style={{ fontSize: '0.8rem', color: 'var(--text-tertiary)', marginTop: 4 }}>
                      Start: {offer.start_date} &nbsp;·&nbsp; Expires: {offer.expiry_date}
                    </div>
                  </div>
                  <span className={`badge ${offer.status === 'accepted' ? 'badge-green' : offer.status === 'rejected' ? 'badge-red' : 'badge-amber'}`}>
                    {offer.status === 'sent' ? 'Pending Response' : offer.status === 'accepted' ? 'Accepted' : 'Declined'}
                  </span>
                </div>
                {offer.status === 'sent' && (
                  <div style={{ display: 'flex', gap: 10, marginTop: 14 }}>
                    <button
                      onClick={() => respondToOffer(offer.offer_id, 'accept')}
                      style={{ background: '#16a34a', color: '#fff', border: 'none', borderRadius: 6, padding: '7px 20px', cursor: 'pointer', fontWeight: 600, fontSize: '0.85rem' }}
                    >
                      Accept Offer
                    </button>
                    <button
                      onClick={() => respondToOffer(offer.offer_id, 'reject')}
                      style={{ background: '#fff', color: '#dc2626', border: '1px solid #dc2626', borderRadius: 6, padding: '7px 20px', cursor: 'pointer', fontWeight: 600, fontSize: '0.85rem' }}
                    >
                      Decline
                    </button>
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
