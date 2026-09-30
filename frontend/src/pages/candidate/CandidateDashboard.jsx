import { useState, useEffect, useRef } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { useAuth } from '../../context/AuthContext'
import { Search, MapPin, Clock, Building2, ChevronRight, Upload, FileText, CheckCircle2, Briefcase, Eye, Calendar, Send, Users, Camera, Image, X, RotateCcw, Sparkles } from 'lucide-react'
import { PieChart, Pie, Cell, Tooltip, ResponsiveContainer } from 'recharts'
import api from '../../api/client'
import StageTimeline from '../../components/StageTimeline'
import CandidateProfile from './CandidateProfile'
import Header from '../../components/Header'
import useCameraStream from './hooks/useCameraStream'
import useApiError from './hooks/useApiError'
import OverviewTab from './tabs/OverviewTab'
import JobsTab from './tabs/JobsTab'
import ApplicationsTab from './tabs/ApplicationsTab'
import SourceFields, { initialSource, sourcePayload } from './SourceFields'
import InterviewsTab from './tabs/InterviewsTab'
import TrackerTab from './tabs/TrackerTab'
import CoursesTab from './tabs/CoursesTab'
import {
  STATUS_BADGES,
  STAGE_LABELS,
  TAB_MAP,
  TEAL,
  TEAL_LIGHT,
  TEAL_MID,
  parseApps,
  rankJobsBySkillMatch,
  computeVisibleSkills,
} from './utils'

export default function CandidateDashboard() {
  const location = useLocation()
  const navigate = useNavigate()
  const { refreshToken } = useAuth()

  const segment = location.pathname.split('/').pop()
  const tab = TAB_MAP[segment] || (segment === 'candidate' ? 'overview' : 'overview')
  const setTab = (t) => {
    const route = t === 'overview' ? '/candidate'
                : t === 'tracker'  ? '/candidate/status'
                : t === 'profile'  ? '/candidate/profile'
                : `/candidate/${t}`
    navigate(route)
  }

  const [jobs, setJobs] = useState([])
  const [apps, setApps] = useState([])
  const [appDetails, setAppDetails] = useState(null)
  const [myInterviews, setMyInterviews] = useState([])
  const [myOffers, setMyOffers] = useState([])
  const [interviewQuestions, setInterviewQuestions] = useState([])
  const [activeInterview, setActiveInterview] = useState(null)
  const [applyModal, setApplyModal] = useState(null)
  const [detailsModal, setDetailsModal] = useState(null)
  const [selectedApp, setSelectedApp] = useState(null)
  const [resumeFile, setResumeFile] = useState(null)
  const [profile, setProfile] = useState(null)
  const [profileSaving, setProfileSaving] = useState(false)
  const [profileMsg, setProfileMsg] = useState('')
  const [viewAppModal, setViewAppModal] = useState(null)
  const [viewAppDetails, setViewAppDetails] = useState(null)
  const [viewAppLoading, setViewAppLoading] = useState(false)
  const [trackerLoading, setTrackerLoading] = useState(false)
  const [quickApplyModal, setQuickApplyModal] = useState(null)
  const [quickApplyStep, setQuickApplyStep] = useState('check')
  const [quickApplyLoading, setQuickApplyLoading] = useState(false)
  const [quickApplyError, setQuickApplyError] = useState('')
  const fileRef = useRef()
  const { errors: apiErrors, report: reportApiError, clear: clearApiErrors } = useApiError()

  const refreshApps = () =>
    api.get('/candidate/applications')
      .then(r => setApps(parseApps(r.data)))
      .catch(reportApiError('Load applications'))

  useEffect(() => {
    api.get('/candidate/jobs?page=1&per_page=50').then(r => {
      const jobList = (r.data?.jobs || []).map(j => ({
        id: j.id,
        title: j.title,
        department: j.department || 'General',
        location: j.location || 'Remote',
        type: j.employment_type || 'Full-time',
        posted: new Date(j.created_at).toLocaleDateString(),
        skills: j.required_skills || [],
        description: j.description,
        requirements: j.requirements,
      }))
      setJobs(jobList)
    }).catch((err) => { setJobs([]); reportApiError('Load jobs')(err) })
    api.get('/candidate/applications')
      .then(r => setApps(parseApps(r.data)))
      .catch((err) => { setApps([]); reportApiError('Load applications')(err) })
    api.get('/candidate/interviews').then(r => setMyInterviews(r.data || [])).catch(reportApiError('Load interviews'))
    api.get('/candidate/offers').then(r => setMyOffers(r.data?.offers || [])).catch(reportApiError('Load offers'))
    api.get('/candidate/profile').then(r => setProfile(r.data)).catch(reportApiError('Load profile'))
    fetchNotifications()
    // Poll notifications every 30s
    const notifInterval = setInterval(fetchNotifications, 30000)
    return () => clearInterval(notifInterval)
  }, [])

  // Refresh applications every time the tracker tab is opened so stage is always current
  useEffect(() => {
    if (tab === 'tracker') refreshApps()
  }, [location.pathname])

  const [applyError, setApplyError] = useState('')
  const [applyLoading, setApplyLoading] = useState(false)
  const [coverNote, setCoverNote] = useState('')
  const [applySource, setApplySource] = useState(() => initialSource())
  const [coverGenLoading, setCoverGenLoading] = useState(false)

  // Notifications
  const [notifications, setNotifications] = useState([])
  const [showNotifPanel, setShowNotifPanel] = useState(false)
  const unreadCount = notifications.filter(n => !n.is_read).length

  const fetchNotifications = () => {
    api.get('/candidate/notifications').then(r => setNotifications(r.data || [])).catch(() => {})
  }

  const markRead = async (id) => {
    await api.put(`/candidate/notifications/${id}/read`).catch(() => {})
    setNotifications(prev => prev.map(n => n.id === id ? { ...n, is_read: true } : n))
  }

  const markAllRead = async () => {
    await api.put('/candidate/notifications/read-all').catch(() => {})
    setNotifications(prev => prev.map(n => ({ ...n, is_read: true })))
  }

  // Photo capture (camera stream + file upload). Stream auto-cleans on unmount.
  const camera = useCameraStream()
  const {
    photo, photoPreview, photoSource, cameraOpen,
    videoRef: cameraVideoRef, canvasRef: cameraCanvasRef, fileInputRef: photoFileRef,
    openCamera, capturePhoto, closeCamera, handlePhotoFile, clearPhoto, openFilePicker,
  } = camera

  // Surface camera-permission errors via the apply form's error banner.
  useEffect(() => {
    if (camera.cameraError) setApplyError(camera.cameraError)
  }, [camera.cameraError])

  const closeQuickApply = () => {
    camera.reset()
    setQuickApplyModal(null)
    setQuickApplyStep('check')
    setQuickApplyError('')
  }

  const handleQuickApply = async () => {
    if (!photo) {
      setQuickApplyError('Please upload or capture your photo for identity verification')
      return
    }
    const fd = new FormData()
    fd.append('photo', photo)
    Object.entries(sourcePayload(applySource)).forEach(([k, v]) => fd.append(k, v))
    setQuickApplyLoading(true)
    try {
      await api.post(`/candidate/jobs/${quickApplyModal.id}/quick-apply`, fd, {
        headers: { 'Content-Type': 'multipart/form-data' }
      })
      refreshToken().catch(() => {})
      refreshApps()
      closeQuickApply()
      setTab('applications')
    } catch (err) {
      setQuickApplyError(err.response?.data?.detail || 'Quick Apply failed. Please try again.')
    } finally {
      setQuickApplyLoading(false)
    }
  }

  const handleApply = async (e) => {
    e.preventDefault()
    setApplyError('')

    if (!resumeFile) {
      setApplyError('Please upload your resume')
      return
    }

    if (!photo) {
      setApplyError('Please upload or capture your photo for identity verification')
      return
    }

    // Gather form fields
    const form = e.target
    const firstName = form.first_name?.value?.trim()
    const lastName = form.last_name?.value?.trim()
    const email = form.email?.value?.trim()

    if (!firstName || !lastName || !email) {
      setApplyError('First name, last name, and email are required')
      return
    }

    const applicationData = {
      first_name: firstName,
      last_name: lastName,
      email: email,
      phone: form.phone?.value?.trim() || null,
      current_salary: null,
      expected_salary: form.expected_salary?.value ? parseFloat(form.expected_salary.value) : null,
      notice_period: form.notice_period?.value?.trim() || null,
      linkedin_url: form.linkedin_url?.value?.trim() || null,
      github_url: form.github_url?.value?.trim() || null,
      portfolio_url: null,
      additional_info: coverNote.trim() || null,
      ...sourcePayload(applySource),
    }

    const formData = new FormData()
    formData.append('resume', resumeFile)
    formData.append('application', JSON.stringify(applicationData))
    if (photo) formData.append('photo', photo)

    setApplyLoading(true)
    try {
      await api.post(`/candidate/jobs/${applyModal.id}/apply`, formData, {
        headers: { 'Content-Type': 'multipart/form-data' }
      })
      // Refresh token so sidebar picks up the company branding from this application
      refreshToken().catch(() => {})
      api.get('/candidate/applications')
        .then(r => setApps(parseApps(r.data)))
        .catch(reportApiError('Refresh applications after apply'))
      setApplyModal(null)
      setResumeFile(null)
      setCoverNote('')
      setTab('applications')
    } catch (err) {
      setApplyError(err.response?.data?.detail || 'Application failed. Please try again.')
    } finally {
      setApplyLoading(false)
    }
  }

  // Draft a cover note with AI from the candidate's profile + this job.
  const handleGenerateCoverLetter = async () => {
    if (!applyModal?.id) return
    setCoverGenLoading(true)
    setApplyError('')
    try {
      const r = await api.post('/candidate/cover-letter/generate', { job_id: applyModal.id })
      setCoverNote(r.data?.cover_letter || '')
    } catch (err) {
      setApplyError(err.response?.data?.detail || 'Could not generate a cover letter right now. You can write one manually.')
    } finally {
      setCoverGenLoading(false)
    }
  }

  return (
    <>
      <Header
        title="Candidate Portal"
        subtitle="Find your next opportunity"
        onBellClick={() => setShowNotifPanel(p => !p)}
        unreadCount={unreadCount}
      />
      {showNotifPanel && (
        <div style={{
          position: 'fixed', right: 16, top: 60, width: 380, maxHeight: 420, overflowY: 'auto',
          background: '#fff', border: '1px solid #e5e7eb', borderRadius: 12,
          boxShadow: '0 8px 32px rgba(0,0,0,0.12)', zIndex: 9999, padding: 0
        }}>
          <div style={{
            display: 'flex', justifyContent: 'space-between', alignItems: 'center',
            padding: '14px 16px 10px', borderBottom: '1px solid #f3f4f6'
          }}>
            <span style={{ fontWeight: 700, fontSize: '0.95rem' }}>Notifications</span>
            {unreadCount > 0 && (
              <button onClick={markAllRead} style={{
                background: 'none', border: 'none', color: TEAL, fontSize: '0.8rem',
                cursor: 'pointer', fontWeight: 600
              }}>Mark all read</button>
            )}
          </div>
          {notifications.length === 0 ? (
            <div style={{ padding: 24, textAlign: 'center', color: '#9ca3af', fontSize: '0.85rem' }}>No notifications yet</div>
          ) : (
            notifications.map(n => (
              <div
                key={n.id}
                onClick={() => {
                  markRead(n.id)
                  setShowNotifPanel(false)
                  if (n.type === 'interview_scheduled' || n.type === 'offer') setTab('interviews')
                  else if (n.type === 'stage_change' || n.type === 'application_received') setTab('tracker')
                }}
                style={{
                  padding: '12px 16px', borderBottom: '1px solid #f9fafb', cursor: 'pointer',
                  background: n.is_read ? '#fff' : '#f0fdfa', transition: 'background 0.2s'
                }}
              >
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 }}>
                  <span style={{ fontWeight: n.is_read ? 400 : 700, fontSize: '0.85rem', color: '#111827' }}>{n.title}</span>
                  {!n.is_read && <span style={{ width: 8, height: 8, borderRadius: '50%', background: TEAL, flexShrink: 0 }} />}
                </div>
                <div style={{ fontSize: '0.8rem', color: '#6b7280', lineHeight: 1.4 }}>{n.message}</div>
                <div style={{ fontSize: '0.7rem', color: '#9ca3af', marginTop: 4 }}>
                  {new Date(n.created_at).toLocaleString()}
                </div>
              </div>
            ))
          )}
        </div>
      )}

      {apiErrors.length > 0 && (
        <div className="card" style={{
          marginBottom: 16, padding: '12px 16px', borderLeft: '3px solid #c44536',
          background: '#fef2f0', color: '#7a2a20'
        }}>
          <div className="flex-between" style={{ marginBottom: apiErrors.length > 1 ? 6 : 0 }}>
            <strong style={{ fontSize: '0.9rem' }}>
              Couldn't load some data
            </strong>
            <button
              onClick={clearApiErrors}
              style={{ background: 'none', border: 'none', cursor: 'pointer', color: '#7a2a20', fontSize: '0.85rem' }}
            >
              Dismiss
            </button>
          </div>
          <ul style={{ margin: 0, paddingLeft: 18, fontSize: '0.82rem', lineHeight: 1.6 }}>
            {apiErrors.map((e, i) => (
              <li key={i}><strong>{e.context}:</strong> {e.message}</li>
            ))}
          </ul>
        </div>
      )}

      {tab === 'overview' && (
        <OverviewTab
          profile={profile}
          apps={apps}
          myInterviews={myInterviews}
          myOffers={myOffers}
          jobs={jobs}
          navigate={navigate}
          onViewJob={(job) => setDetailsModal(job)}
        />
      )}

      {tab === 'jobs' && (
        <JobsTab
          jobs={jobs}
          onViewDetails={(job) => setDetailsModal(job)}
          onQuickApply={(job) => { clearPhoto(); setQuickApplyStep('check'); setQuickApplyError(''); setQuickApplyModal(job) }}
        />
      )}

      {tab === 'applications' && (
        <ApplicationsTab
          apps={apps}
          reportApiError={reportApiError}
          onChanged={() => { refreshApps(); api.get('/candidate/interviews').then(r => setMyInterviews(r.data || [])).catch(() => {}) }}
          onOpenDetails={({ modal, details, loading }) => {
            setViewAppModal(modal)
            setViewAppDetails(details)
            setViewAppLoading(loading)
          }}
        />
      )}

      {tab === 'interviews' && (
        <InterviewsTab
          myInterviews={myInterviews}
          myOffers={myOffers}
          activeInterview={activeInterview}
          setActiveInterview={setActiveInterview}
          interviewQuestions={interviewQuestions}
          setInterviewQuestions={setInterviewQuestions}
          setMyInterviews={setMyInterviews}
          setMyOffers={setMyOffers}
          navigate={navigate}
          refreshApps={refreshApps}
          reportApiError={reportApiError}
        />
      )}

      {tab === 'tracker' && (
        <TrackerTab
          apps={apps}
          selectedApp={selectedApp}
          setSelectedApp={setSelectedApp}
          appDetails={appDetails}
          setAppDetails={setAppDetails}
          trackerLoading={trackerLoading}
          setTrackerLoading={setTrackerLoading}
          refreshApps={refreshApps}
          reportApiError={reportApiError}
        />
      )}

      {tab === 'courses' && (
        <CoursesTab profile={profile} jobs={jobs} apps={apps} />
      )}

      {tab === 'profile' && (
        <CandidateProfile />
      )}

      {detailsModal && (
        <div className="modal-overlay" onClick={() => setDetailsModal(null)}>
          <div className="modal job-details-modal" onClick={e => e.stopPropagation()}>

            {/* ── Header ── */}
            <div className="jdm-header">
              <div className="jdm-header-left">
                <span className="badge badge-amber" style={{ marginBottom: 10 }}>{detailsModal.type}</span>
                <h2 className="jdm-title">{detailsModal.title}</h2>
                <div className="jdm-meta-row">
                  <span className="jdm-meta-item"><Building2 size={13} />{detailsModal.department}</span>
                  <span className="jdm-meta-sep">·</span>
                  <span className="jdm-meta-item"><MapPin size={13} />{detailsModal.location}</span>
                  <span className="jdm-meta-sep">·</span>
                  <span className="jdm-meta-item"><Clock size={13} />Posted {detailsModal.posted}</span>
                </div>
              </div>
              <button className="jdm-close" onClick={() => setDetailsModal(null)} aria-label="Close">✕</button>
            </div>

            {/* ── Scrollable body ── */}
            <div className="jdm-body">

              {detailsModal.description && (
                <div className="jdm-section">
                  <div className="jdm-section-label">About the Role</div>
                  <div className="jdm-text">{detailsModal.description}</div>
                </div>
              )}

              {detailsModal.requirements && (
                <div className="jdm-section">
                  <div className="jdm-section-label">Requirements</div>
                  <div className="jdm-text">{detailsModal.requirements}</div>
                </div>
              )}

              {(detailsModal.skills || []).length > 0 && (
                <div className="jdm-section">
                  <div className="jdm-section-label">Skills Required</div>
                  <div className="jdm-skills">
                    {detailsModal.skills.map(s => (
                      <span key={s} className="skill-tag jdm-skill-tag">{s}</span>
                    ))}
                  </div>
                </div>
              )}

              {!detailsModal.description && !detailsModal.requirements && (detailsModal.skills || []).length === 0 && (
                <div style={{ color: 'var(--text-tertiary)', padding: '24px 0', textAlign: 'center', fontSize: '0.9rem' }}>
                  No additional details available for this position.
                </div>
              )}
            </div>

            {/* ── Footer ── */}
            <div className="jdm-footer">
              <button className="btn btn-secondary" onClick={() => setDetailsModal(null)}>Close</button>
              <button className="btn btn-secondary" onClick={() => { setDetailsModal(null); clearPhoto(); setQuickApplyStep('check'); setQuickApplyError(''); setQuickApplyModal(detailsModal) }}>
                <Briefcase size={15} /> Quick Apply
              </button>
              <button className="btn btn-primary" onClick={() => { setDetailsModal(null); setApplyModal(detailsModal) }}>
                <Briefcase size={15} /> Apply Now
              </button>
            </div>

          </div>
        </div>
      )}

      <style>{`
        .job-details-modal {
          max-width: 640px;
          width: 100%;
          max-height: 88vh;
          display: flex;
          flex-direction: column;
          padding: 0;
          overflow: hidden;
        }

        .jdm-header {
          display: flex;
          align-items: flex-start;
          justify-content: space-between;
          gap: 16px;
          padding: 28px 28px 20px;
          border-bottom: 1px solid var(--border-subtle);
          flex-shrink: 0;
        }

        .jdm-header-left {
          display: flex;
          flex-direction: column;
        }

        .jdm-title {
          font-size: 1.35rem;
          font-weight: 700;
          line-height: 1.3;
          margin: 0 0 10px 0;
        }

        .jdm-meta-row {
          display: flex;
          align-items: center;
          flex-wrap: wrap;
          gap: 6px;
          font-size: 0.78rem;
          color: var(--text-tertiary);
        }

        .jdm-meta-item {
          display: flex;
          align-items: center;
          gap: 5px;
        }

        .jdm-meta-sep {
          opacity: 0.35;
        }

        .jdm-close {
          background: var(--bg-hover);
          border: 1px solid var(--border-subtle);
          color: var(--text-secondary);
          border-radius: 6px;
          width: 30px;
          height: 30px;
          display: grid;
          place-items: center;
          cursor: pointer;
          font-size: 0.8rem;
          flex-shrink: 0;
          margin-top: 2px;
          transition: background 0.15s ease, color 0.15s ease;
        }

        .jdm-close:hover {
          background: var(--surface-input);
          color: var(--text-primary);
        }

        .jdm-body {
          flex: 1;
          overflow-y: auto;
          padding: 24px 28px;
          display: flex;
          flex-direction: column;
          gap: 24px;
        }

        .jdm-body::-webkit-scrollbar {
          width: 4px;
        }

        .jdm-body::-webkit-scrollbar-track {
          background: transparent;
        }

        .jdm-body::-webkit-scrollbar-thumb {
          background: var(--border-subtle);
          border-radius: 2px;
        }

        .jdm-section {
          display: flex;
          flex-direction: column;
          gap: 10px;
        }

        .jdm-section-label {
          font-size: 0.68rem;
          font-family: var(--font-mono);
          font-weight: 600;
          letter-spacing: 0.1em;
          text-transform: uppercase;
          color: var(--accent-primary);
          padding-bottom: 8px;
          border-bottom: 1px solid var(--border-subtle);
        }

        .jdm-text {
          font-size: 0.88rem;
          line-height: 1.7;
          color: var(--text-secondary);
          white-space: pre-wrap;
        }

        .jdm-skills {
          display: flex;
          flex-wrap: wrap;
          gap: 8px;
        }

        .jdm-skill-tag {
          font-size: 0.75rem;
          padding: 5px 12px;
        }

        .jdm-footer {
          display: flex;
          align-items: center;
          justify-content: flex-end;
          gap: 10px;
          padding: 16px 28px;
          border-top: 1px solid var(--border-subtle);
          flex-shrink: 0;
          background: var(--surface-input);
        }
      `}</style>

      {/* Job details modal — opened from Applications "View" button */}
      {viewAppModal && (
        <div className="modal-overlay" onClick={() => { setViewAppModal(null); setViewAppDetails(null) }}>
          <div className="modal" onClick={e => e.stopPropagation()} style={{ maxWidth: 620, width: '92%' }}>
            {/* Header */}
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 20 }}>
              <div>
                <h2 style={{ margin: 0, fontSize: '1.25rem' }}>{viewAppModal.job}</h2>
                <div style={{ marginTop: 8, display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
                  <span className={`badge ${STATUS_BADGES[viewAppModal.status] || 'badge-neutral'}`}>
                    {STAGE_LABELS[viewAppModal.status] || viewAppModal.status}
                  </span>
                  <span style={{ fontSize: '0.78rem', color: 'var(--text-tertiary)' }}>Applied on {viewAppModal.applied}</span>
                </div>
              </div>
              <button className="btn btn-ghost btn-sm" onClick={() => { setViewAppModal(null); setViewAppDetails(null) }}>✕</button>
            </div>

            {viewAppLoading ? (
              <div style={{ padding: 32, textAlign: 'center', color: 'var(--text-tertiary)' }}>Loading details…</div>
            ) : viewAppDetails ? (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
                {/* Meta row */}
                <div style={{ display: 'flex', gap: 20, flexWrap: 'wrap' }}>
                  {viewAppDetails.job?.location && (
                    <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: '0.85rem', color: 'var(--text-secondary)' }}>
                      <MapPin size={14} /> {viewAppDetails.job.location}
                    </div>
                  )}
                  {viewAppDetails.job?.employment_type && (
                    <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: '0.85rem', color: 'var(--text-secondary)', textTransform: 'capitalize' }}>
                      <Clock size={14} /> {viewAppDetails.job.employment_type}
                    </div>
                  )}
                </div>

                {/* Description */}
                {viewAppDetails.job?.description && (
                  <div>
                    <div style={{ fontWeight: 700, marginBottom: 6, fontSize: '0.88rem', color: 'var(--text-primary)' }}>Job Description</div>
                    <div style={{ fontSize: '0.84rem', color: 'var(--text-secondary)', lineHeight: 1.7, whiteSpace: 'pre-wrap', maxHeight: 200, overflowY: 'auto' }}>
                      {viewAppDetails.job.description}
                    </div>
                  </div>
                )}

                {/* Requirements */}
                {viewAppDetails.job?.requirements && (
                  <div>
                    <div style={{ fontWeight: 700, marginBottom: 6, fontSize: '0.88rem', color: 'var(--text-primary)' }}>Requirements</div>
                    <div style={{ fontSize: '0.84rem', color: 'var(--text-secondary)', lineHeight: 1.7, whiteSpace: 'pre-wrap', maxHeight: 160, overflowY: 'auto' }}>
                      {viewAppDetails.job.requirements}
                    </div>
                  </div>
                )}
              </div>
            ) : (
              <div style={{ padding: 32, textAlign: 'center', color: 'var(--text-tertiary)' }}>Could not load job details.</div>
            )}
          </div>
        </div>
      )}

      {applyModal && (
        <div className="modal-overlay" onClick={() => setApplyModal(null)}>
          <div className="modal" onClick={e => e.stopPropagation()}>
            <h2>Apply for {applyModal.title}</h2>
            <form onSubmit={handleApply} className="auth-form">
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
                <div className="input-group">
                  <label>First Name *</label>
                  <input className="input" name="first_name" required defaultValue={profile?.first_name || ''} />
                </div>
                <div className="input-group">
                  <label>Last Name *</label>
                  <input className="input" name="last_name" required defaultValue={profile?.last_name || ''} />
                </div>
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
                <div className="input-group">
                  <label>Email *</label>
                  <input className="input" name="email" type="email" required defaultValue={profile?.email || ''} />
                </div>
                <div className="input-group">
                  <label>Phone</label>
                  <input className="input" name="phone" defaultValue={profile?.phone || ''} />
                </div>
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
                <div className="input-group">
                  <label>Notice Period <span style={{ fontWeight: 400, color: 'var(--text-tertiary)' }}>(optional)</span></label>
                  <input className="input" name="notice_period" placeholder="e.g. 30 days" defaultValue={profile?.notice_period || ''} />
                </div>
                <div className="input-group">
                  <label>Expected Salary <span style={{ fontWeight: 400, color: 'var(--text-tertiary)' }}>(optional)</span></label>
                  <input className="input" name="expected_salary" type="number" placeholder="Annual, e.g. 700000" />
                </div>
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
                <div className="input-group">
                  <label>LinkedIn URL <span style={{ fontWeight: 400, color: 'var(--text-tertiary)' }}>(optional)</span></label>
                  <input className="input" name="linkedin_url" placeholder="linkedin.com/in/..." defaultValue={profile?.links?.linkedin_url || ''} />
                </div>
                <div className="input-group">
                  <label>GitHub URL <span style={{ fontWeight: 400, color: 'var(--text-tertiary)' }}>(optional)</span></label>
                  <input className="input" name="github_url" placeholder="github.com/..." defaultValue={profile?.links?.github_url || ''} />
                </div>
              </div>
              <div className="input-group">
                <label>Resume (PDF/DOCX) *</label>
                <button type="button" className="upload-zone" onClick={() => fileRef.current?.click()}>
                  <input ref={fileRef} type="file" accept=".pdf,.docx,.doc" style={{ display: 'none' }}
                    onChange={e => setResumeFile(e.target.files[0])} />
                  {resumeFile ? (
                    <div className="flex-center gap-8">
                      <FileText size={20} style={{ color: 'var(--accent-primary)' }} />
                      <div>
                        <div style={{ fontWeight: 500 }}>{resumeFile.name}</div>
                        <div className="text-sm text-muted">{(resumeFile.size / 1024).toFixed(0)} KB</div>
                      </div>
                      <CheckCircle2 size={18} style={{ color: 'var(--status-success)', marginLeft: 'auto' }} />
                    </div>
                  ) : (
                    <div style={{ textAlign: 'center' }}>
                      <Upload size={28} style={{ color: 'var(--text-tertiary)', marginBottom: 8 }} />
                      <div className="text-sm text-muted">Click to upload your resume</div>
                      <div className="text-sm" style={{ color: 'var(--text-tertiary)', fontSize: '0.75rem' }}>PDF or DOCX, max 10MB</div>
                    </div>
                  )}
                </button>
              </div>
              <div className="input-group">
                <label>Photo *</label>
                {photoPreview ? (
                  <div style={{ display: 'flex', alignItems: 'center', gap: 16, padding: 12, background: 'var(--bg-hover)', borderRadius: 10, border: '1px solid var(--border-subtle)' }}>
                    <img src={photoPreview} alt="Your photo" style={{ width: 80, height: 80, objectFit: 'cover', borderRadius: 10, border: '2px solid var(--border-medium)' }} />
                    <div style={{ flex: 1 }}>
                      <div style={{ fontWeight: 500, fontSize: '0.9rem' }}>{photoSource === 'camera' ? 'Captured photo' : photo?.name || 'Uploaded photo'}</div>
                      <div className="text-sm text-muted">{photo ? `${(photo.size / 1024).toFixed(0)} KB` : ''}</div>
                    </div>
                    {photoSource === 'camera' ? (
                      <button type="button" onClick={() => { clearPhoto(); openCamera() }} style={{
                        display: 'flex', alignItems: 'center', gap: 5, background: 'none', border: '1px solid var(--border-medium)',
                        borderRadius: 6, cursor: 'pointer', color: 'var(--accent-primary)', padding: '5px 10px', fontSize: '0.8rem', fontWeight: 600
                      }}>
                        <RotateCcw size={14} /> Retake
                      </button>
                    ) : (
                      <button type="button" onClick={() => { clearPhoto(); photoFileRef.current?.click() }} style={{
                        display: 'flex', alignItems: 'center', gap: 5, background: 'none', border: '1px solid var(--border-medium)',
                        borderRadius: 6, cursor: 'pointer', color: 'var(--accent-primary)', padding: '5px 10px', fontSize: '0.8rem', fontWeight: 600
                      }}>
                        <Upload size={14} /> Re-upload
                      </button>
                    )}
                  </div>
                ) : cameraOpen ? (
                  <div style={{ position: 'relative', borderRadius: 10, overflow: 'hidden', border: '2px solid var(--accent-primary)', background: '#000' }}>
                    <video ref={cameraVideoRef} autoPlay playsInline muted style={{ width: '100%', display: 'block', maxHeight: 260 }} />
                    <canvas ref={cameraCanvasRef} style={{ display: 'none' }} />
                    <div style={{ display: 'flex', justifyContent: 'center', gap: 12, padding: '10px 0', background: 'rgba(0,0,0,0.6)' }}>
                      <button type="button" onClick={capturePhoto} style={{ background: '#fff', border: 'none', borderRadius: '50%', width: 48, height: 48, cursor: 'pointer', display: 'grid', placeItems: 'center', boxShadow: '0 2px 8px rgba(0,0,0,0.3)' }}>
                        <Camera size={22} color="#1a73e8" />
                      </button>
                      <button type="button" onClick={closeCamera} style={{ background: 'rgba(255,255,255,0.15)', border: 'none', borderRadius: '50%', width: 48, height: 48, cursor: 'pointer', display: 'grid', placeItems: 'center' }}>
                        <X size={22} color="#fff" />
                      </button>
                    </div>
                  </div>
                ) : (
                  <div style={{ display: 'flex', gap: 10 }}>
                    <button type="button" className="upload-zone" style={{ flex: 1, padding: 16 }} onClick={() => photoFileRef.current?.click()}>
                      <input ref={photoFileRef} type="file" accept="image/*" style={{ display: 'none' }} onChange={handlePhotoFile} />
                      <div style={{ textAlign: 'center' }}>
                        <Image size={24} style={{ color: 'var(--text-tertiary)', marginBottom: 4 }} />
                        <div className="text-sm text-muted">Upload Photo</div>
                        <div style={{ color: 'var(--text-tertiary)', fontSize: '0.7rem' }}>JPG, PNG</div>
                      </div>
                    </button>
                    <button type="button" className="upload-zone" style={{ flex: 1, padding: 16 }} onClick={openCamera}>
                      <div style={{ textAlign: 'center' }}>
                        <Camera size={24} style={{ color: 'var(--text-tertiary)', marginBottom: 4 }} />
                        <div className="text-sm text-muted">Take Photo</div>
                        <div style={{ color: 'var(--text-tertiary)', fontSize: '0.7rem' }}>Use webcam</div>
                      </div>
                    </button>
                  </div>
                )}
              </div>
              <div className="input-group">
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8 }}>
                  <label style={{ margin: 0 }}>Cover Note (optional)</label>
                  <button
                    type="button"
                    onClick={handleGenerateCoverLetter}
                    disabled={coverGenLoading}
                    style={{
                      display: 'inline-flex', alignItems: 'center', gap: 5,
                      background: coverGenLoading ? 'var(--bg-hover)' : TEAL_LIGHT,
                      color: TEAL, border: `1px solid ${TEAL_MID}`, borderRadius: 7,
                      padding: '4px 10px', fontSize: '0.74rem', fontWeight: 600,
                      cursor: coverGenLoading ? 'default' : 'pointer', fontFamily: 'var(--font-body)',
                    }}
                  >
                    <Sparkles size={13} /> {coverGenLoading ? 'Drafting…' : 'Generate with AI'}
                  </button>
                </div>
                <textarea
                  className="textarea"
                  name="cover_note"
                  placeholder="Why are you interested in this role? Or let AI draft one for you."
                  rows={4}
                  value={coverNote}
                  onChange={e => setCoverNote(e.target.value)}
                />
              </div>
              <SourceFields value={applySource} onChange={setApplySource} />
              {applyError && (
                <div style={{ color: '#ef4444', fontSize: '0.85rem', fontWeight: 500, padding: '8px 12px', background: '#fef2f2', borderRadius: 6 }}>
                  {applyError}
                </div>
              )}
              <div className="flex gap-12" style={{ justifyContent: 'flex-end', marginTop: 8 }}>
                <button type="button" className="btn btn-secondary" onClick={() => { closeCamera(); clearPhoto(); setApplyModal(null); setApplyError(''); setCoverNote('') }}>Cancel</button>
                <button type="submit" className="btn btn-primary" disabled={applyLoading}>
                  <Briefcase size={16} /> {applyLoading ? 'Submitting...' : 'Submit Application'}
                </button>
              </div>
            </form>

            <style>{`
              /* These are <button> now — they were <div onClick>, i.e. unreachable by
                 keyboard — so they need the UA reset and a visible focus ring. */
              .upload-zone {
                display: block;
                width: 100%;
                font: inherit;
                color: inherit;
                text-align: center;
                background: none;
                -webkit-appearance: none;
                appearance: none;
                border: 2px dashed var(--border-medium);
                border-radius: 10px;
                padding: 24px;
                cursor: pointer;
                transition: all 0.2s;
              }
              .upload-zone:hover {
                border-color: var(--accent-primary);
                background: var(--accent-glow);
              }
              .upload-zone:focus-visible {
                outline: 2px solid var(--accent-primary);
                outline-offset: 2px;
              }
              @media (prefers-reduced-motion: reduce) { .upload-zone { transition: none; } }
            `}</style>
          </div>
        </div>
      )}

      {quickApplyModal && (
        <div className="modal-overlay" onClick={closeQuickApply}>
          <div className="modal" onClick={e => e.stopPropagation()} style={{ maxWidth: 480 }}>

            {quickApplyStep === 'check' && (
              <>
                <h2 style={{ marginBottom: 4 }}>Quick Apply</h2>
                <div style={{ fontSize: '0.88rem', color: 'var(--text-secondary)', marginBottom: 20 }}>
                  {quickApplyModal.title}
                </div>

                <div style={{ background: 'var(--surface-input)', border: '1px solid var(--border-subtle)', borderRadius: 10, padding: '14px 16px', marginBottom: 16 }}>
                  <div style={{ fontSize: '0.78rem', fontWeight: 600, color: 'var(--text-tertiary)', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 12 }}>
                    Profile Checklist
                  </div>
                  {[
                    ['Resume uploaded',   !!profile?.resume_url],
                    ['Work experience',   (profile?.work_experiences?.length || 0) > 0],
                    ['Skills added',      (profile?.skills?.length || 0) > 0],
                  ].map(([label, ok]) => (
                    <div key={label} style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 8 }}>
                      {ok
                        ? <CheckCircle2 size={16} style={{ color: 'var(--status-success)', flexShrink: 0 }} />
                        : <X size={16} style={{ color: '#ef4444', flexShrink: 0 }} />}
                      <span style={{ fontSize: '0.88rem', color: ok ? 'var(--text-primary)' : 'var(--text-secondary)' }}>{label}</span>
                    </div>
                  ))}
                </div>

                {!profile?.resume_url && (
                  <div style={{ color: '#9a3412', fontSize: '0.85rem', padding: '8px 12px', background: '#fef2f2', border: '1px solid #fca5a5', borderRadius: 6, marginBottom: 14 }}>
                    You must upload a resume to your profile before using Quick Apply.
                  </div>
                )}

                {quickApplyError && (
                  <div style={{ color: '#ef4444', fontSize: '0.85rem', padding: '8px 12px', background: '#fef2f2', borderRadius: 6, marginBottom: 14 }}>
                    {quickApplyError}
                  </div>
                )}

                <div className="flex gap-12" style={{ justifyContent: 'flex-end' }}>
                  <button className="btn btn-secondary" onClick={() => { closeQuickApply(); navigate('/candidate/profile') }}>
                    Go to Profile
                  </button>
                  <button
                    className="btn btn-primary"
                    disabled={!profile?.resume_url}
                    onClick={() => { setQuickApplyError(''); setQuickApplyStep('photo') }}
                  >
                    Confirm &amp; Continue
                  </button>
                </div>
              </>
            )}

            {quickApplyStep === 'photo' && (
              <>
                <h2 style={{ marginBottom: 4 }}>Quick Apply — Photo</h2>
                <div style={{ fontSize: '0.88rem', color: 'var(--text-secondary)', marginBottom: 20 }}>
                  {quickApplyModal.title}
                </div>

                <div className="input-group">
                  <label>Photo * <span style={{ fontWeight: 400, color: 'var(--text-tertiary)', fontSize: '0.8rem' }}>(required for identity verification)</span></label>
                  {photoPreview ? (
                    <div style={{ display: 'flex', alignItems: 'center', gap: 16, padding: 12, background: 'var(--bg-hover)', borderRadius: 10, border: '1px solid var(--border-subtle)' }}>
                      <img src={photoPreview} alt="Your photo" style={{ width: 80, height: 80, objectFit: 'cover', borderRadius: 10, border: '2px solid var(--border-medium)' }} />
                      <div style={{ flex: 1 }}>
                        <div style={{ fontWeight: 500, fontSize: '0.9rem' }}>{photoSource === 'camera' ? 'Captured photo' : photo?.name || 'Uploaded photo'}</div>
                        <div className="text-sm text-muted">{photo ? `${(photo.size / 1024).toFixed(0)} KB` : ''}</div>
                      </div>
                      {photoSource === 'camera' ? (
                        <button type="button" onClick={() => { clearPhoto(); openCamera() }} style={{ display: 'flex', alignItems: 'center', gap: 5, background: 'none', border: '1px solid var(--border-medium)', borderRadius: 6, cursor: 'pointer', color: 'var(--accent-primary)', padding: '5px 10px', fontSize: '0.8rem', fontWeight: 600 }}>
                          <RotateCcw size={14} /> Retake
                        </button>
                      ) : (
                        <button type="button" onClick={() => { clearPhoto(); photoFileRef.current?.click() }} style={{ display: 'flex', alignItems: 'center', gap: 5, background: 'none', border: '1px solid var(--border-medium)', borderRadius: 6, cursor: 'pointer', color: 'var(--accent-primary)', padding: '5px 10px', fontSize: '0.8rem', fontWeight: 600 }}>
                          <Upload size={14} /> Re-upload
                        </button>
                      )}
                    </div>
                  ) : cameraOpen ? (
                    <div style={{ position: 'relative', borderRadius: 10, overflow: 'hidden', border: '2px solid var(--accent-primary)', background: '#000' }}>
                      <video ref={cameraVideoRef} autoPlay playsInline muted style={{ width: '100%', display: 'block', maxHeight: 260 }} />
                      <canvas ref={cameraCanvasRef} style={{ display: 'none' }} />
                      <div style={{ display: 'flex', justifyContent: 'center', gap: 12, padding: '10px 0', background: 'rgba(0,0,0,0.6)' }}>
                        <button type="button" onClick={capturePhoto} style={{ background: '#fff', border: 'none', borderRadius: '50%', width: 48, height: 48, cursor: 'pointer', display: 'grid', placeItems: 'center', boxShadow: '0 2px 8px rgba(0,0,0,0.3)' }}>
                          <Camera size={22} color="#1a73e8" />
                        </button>
                        <button type="button" onClick={closeCamera} style={{ background: 'rgba(255,255,255,0.15)', border: 'none', borderRadius: '50%', width: 48, height: 48, cursor: 'pointer', display: 'grid', placeItems: 'center' }}>
                          <X size={22} color="#fff" />
                        </button>
                      </div>
                    </div>
                  ) : (
                    <div style={{ display: 'flex', gap: 10 }}>
                      <button type="button" className="upload-zone" style={{ flex: 1, padding: 16 }} onClick={() => photoFileRef.current?.click()}>
                        <input ref={photoFileRef} type="file" accept="image/*" style={{ display: 'none' }} onChange={handlePhotoFile} />
                        <div style={{ textAlign: 'center' }}>
                          <Image size={24} style={{ color: 'var(--text-tertiary)', marginBottom: 4 }} />
                          <div className="text-sm text-muted">Upload Photo</div>
                          <div style={{ color: 'var(--text-tertiary)', fontSize: '0.7rem' }}>JPG, PNG</div>
                        </div>
                      </button>
                      <button type="button" className="upload-zone" style={{ flex: 1, padding: 16 }} onClick={openCamera}>
                        <div style={{ textAlign: 'center' }}>
                          <Camera size={24} style={{ color: 'var(--text-tertiary)', marginBottom: 4 }} />
                          <div className="text-sm text-muted">Take Photo</div>
                          <div style={{ color: 'var(--text-tertiary)', fontSize: '0.7rem' }}>Use webcam</div>
                        </div>
                      </button>
                    </div>
                  )}
                </div>

                <div style={{ marginTop: 14 }}>
                  <SourceFields value={applySource} onChange={setApplySource} />
                </div>

                {quickApplyError && (
                  <div style={{ color: '#ef4444', fontSize: '0.85rem', padding: '8px 12px', background: '#fef2f2', borderRadius: 6, marginTop: 12 }}>
                    {quickApplyError}
                  </div>
                )}

                <div className="flex gap-12" style={{ justifyContent: 'flex-end', marginTop: 16 }}>
                  <button className="btn btn-secondary" onClick={() => { clearPhoto(); closeCamera(); setQuickApplyStep('check') }}>Back</button>
                  <button
                    className="btn btn-primary"
                    disabled={!photo || quickApplyLoading}
                    onClick={handleQuickApply}
                  >
                    <Briefcase size={16} /> {quickApplyLoading ? 'Submitting...' : 'Submit Application'}
                  </button>
                </div>
              </>
            )}

          <style>{`
            /* Same reset as the block above — these zones are buttons now. */
            .upload-zone {
              display: block;
              width: 100%;
              font: inherit;
              color: inherit;
              text-align: center;
              background: none;
              -webkit-appearance: none;
              appearance: none;
              border: 2px dashed var(--border-medium);
              border-radius: 10px;
              padding: 24px;
              cursor: pointer;
              transition: all 0.2s;
            }
            .upload-zone:hover {
              border-color: var(--accent-primary);
              background: var(--accent-glow);
            }
            .upload-zone:focus-visible {
              outline: 2px solid var(--accent-primary);
              outline-offset: 2px;
            }
            @media (prefers-reduced-motion: reduce) { .upload-zone { transition: none; } }
          `}</style>
        </div>
        </div>
      )}
    </>
  )
}

