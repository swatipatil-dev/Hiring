import React, { useState, useEffect, useRef } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { Briefcase, Users, Calendar, Gift, Plus, Eye, Edit3, Trash2, Archive, RotateCcw, GripVertical, ChevronRight, FileText, ClipboardList, Mic, UserCheck, Award, Search, X } from 'lucide-react'
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, PieChart, Pie, Cell, LabelList } from 'recharts'
import api from '../../api/client'
import Header from '../../components/Header'
import StageTimeline from '../../components/StageTimeline'
import ProctoringReport from '../../components/ProctoringReport'
import PipelineNotifications from './PipelineNotifications'
import MailFromPicker from './MailFromPicker'
import CreateTestWizard from '../shared/CreateTestWizard'
import ResumeMatcher from './ResumeMatcher'

const CHART_COLORS = ['#2b7cba', '#3b82f6', '#22c55e', '#8b5cf6', '#ef4444', '#06b6d4', '#f59e0b', '#ec4899']

const OFFER_TEMPLATES = [
  {
    label: 'Standard Full-Time',
    icon: '💼',
    offer_type: 'full_time',
    salary: '800000',
    bonus: '50000',
    terms: '3-month probation period. Benefits include health insurance and 18 days PTO annually. Notice period: 30 days.'
  },
  {
    label: 'Senior / Lead Role',
    icon: '🌟',
    offer_type: 'full_time',
    salary: '1500000',
    bonus: '150000',
    terms: '3-month probation period. Benefits include health insurance, 24 days PTO annually, and ESOPs. Notice period: 60 days.'
  },
  {
    label: 'Contract',
    icon: '📋',
    offer_type: 'contract',
    salary: '600000',
    bonus: '',
    terms: 'Contract duration as agreed. Payment on deliverables. No notice period required. All IP rights assigned to the company.'
  },
  {
    label: 'Internship',
    icon: '🎓',
    offer_type: 'internship',
    salary: '240000',
    bonus: '',
    terms: 'Internship duration: 6 months. Includes mentorship program and certificate of completion. Full-time conversion based on performance.'
  }
]

// Actual stages from DB — 'applied' is only ever a history log entry;
// current_stage is immediately set to 'ats_screening' (or 'hr_review') on apply.
const PIPELINE_STAGES = [
  { key: 'ats_screening', label: 'Applied',    color: '#2b7cba', icon: 'file'      },
  { key: 'hr_review',     label: 'HR Review',  color: '#8b5cf6', icon: 'users'     },
  { key: 'shortlisted',   label: 'Shortlisted',color: '#f59e0b', icon: 'clipboard' },
  { key: 'interview',     label: 'Interview',  color: '#06b6d4', icon: 'mic'       },
  { key: 'interview_completed', label: 'Interviewed', color: '#3b82f6', icon: 'usercheck' },
  { key: 'hired',         label: 'Hired',      color: '#22c55e', icon: 'award'     },
]

// Normalize legacy/transient 'applied' stage → 'ats_screening'
const normalizeStage = (stage) => stage === 'applied' ? 'ats_screening' : (stage || 'ats_screening')

// A candidate is still "unsent" (no ATS-stage email yet) while they rest at one of these
// stages. Any other stage (shortlisted/rejected/interview/...) means they were already
// emailed from the ATS tab, so they must not be re-selected or re-emailed.
const UNSENT_STAGES = new Set(['applied', 'ats_screening', 'hr_review'])
const isMailSent = (c) => !UNSENT_STAGES.has(c.current_stage)

// 'resume-match' is kept as an alias for 'talent-pool' so existing bookmarks still work.
const TAB_MAP = { jobs: 'jobs', pipeline: 'pipeline', interviews: 'interviews', offers: 'offers', ats: 'ats', 'talent-pool': 'resume-match', 'resume-match': 'resume-match', 'test-results': 'test-results', feedback: 'feedback', requirements: 'requirements', notifications: 'notifications' }

export default function HRDashboard() {
  const location = useLocation()
  const navigate = useNavigate()

  const segment = location.pathname.split('/').pop()
  const tab = TAB_MAP[segment] || (segment === 'hr' ? 'overview' : 'overview')
  const setTab = (t) => navigate(t === 'overview' ? '/hr' : `/hr/${t}`)

  // Canonicalise the old Resume Matcher URL. Without this the page still renders, but
  // no sidebar item highlights, because NavLink matches on path.
  useEffect(() => {
    if (segment === 'resume-match') navigate('/hr/talent-pool', { replace: true })
  }, [segment, navigate])

  const fetchJobsId = useRef(0)   // incremented on every fetchJobs call; stale callbacks check this
  // Create Test wizard (shared with the interviewer). When showCreateTest is true the
  // Job Postings screen renders the wizard; createTestJobId pre-selects a job.
  const [showCreateTest, setShowCreateTest] = useState(false)
  const [createTestJobId, setCreateTestJobId] = useState(null)
  const [jobs, setJobs] = useState([])
  const [jobsLoading, setJobsLoading] = useState(true)
  const [interviews, setInterviews] = useState([])
  const [offers, setOffers] = useState([])
  const [departments, setDepartments] = useState([])
  const [showModal, setShowModal] = useState(false)
  const [selectedCandidate, setSelectedCandidate] = useState(null)
  const [jobForm, setJobForm] = useState({
    title: '', description: '', requirements: '', department_id: '',
    location: '', employment_type: 'full-time', salary_min: '', salary_max: '',
    experience_years: 0, required_skills: ''
  })
  const [submitting, setSubmitting] = useState(false)
  const [editJob, setEditJob] = useState(null)
  const [jobPipelines, setJobPipelines] = useState({})
  const [jobFilters, setJobFilters] = useState({ status: '', department: '', employment_type: '', location: '' })
  const [pipelineCandidates, setPipelineCandidates] = useState({})
  const [pipelineStageFilter, setPipelineStageFilter] = useState(null)
  const [pipelineSearch, setPipelineSearch] = useState('')
  const [pipelineJobFilter, setPipelineJobFilter] = useState('')
  const [pipelineExpFilter, setPipelineExpFilter] = useState('')
  const [testResults, setTestResults] = useState([])
  const [testResultJob, setTestResultJob] = useState(null)
  const [testResultCandidates, setTestResultCandidates] = useState([])
  const [answerDetail, setAnswerDetail] = useState(null) // { loading, candidate_name, data }
  const [regrading, setRegrading] = useState(false)
  const [regradeError, setRegradeError] = useState('')
  const [proctorDetail, setProctorDetail] = useState(null) // the clicked test-result row `r`

  const openAnswerDetail = async (r) => {
    setAnswerDetail({ loading: true, candidate_name: r.candidate_name, data: null })
    try {
      const res = await api.get(`/hr/test-results/${r.id}/answers`)
      setAnswerDetail({ loading: false, candidate_name: r.candidate_name, data: res.data })
    } catch {
      setAnswerDetail({ loading: false, candidate_name: r.candidate_name, data: null, error: true })
    }
  }

  // Re-grade coding answers that were deferred because the judge was down. Uses the
  // HR JWT, so the sweep is scoped to this company; the systemd timer covers the
  // platform-wide case.
  const regradeInterview = async (interviewId) => {
    setRegrading(true); setRegradeError('')
    try {
      await api.post(`/judge/regrade-pending?interview_id=${interviewId}`)
      const res = await api.get(`/hr/test-results/${interviewId}/answers`)
      setAnswerDetail(prev => ({ ...prev, loading: false, data: res.data }))
      await refreshTestResults()
    } catch (err) {
      const d = err.response?.data?.detail
      setRegradeError(
        (typeof d === 'object' ? d?.message : d) ||
        'Re-grade failed. The judge may still be unavailable.'
      )
    }
    setRegrading(false)
  }
  const [feedbackData, setFeedbackData] = useState([])
  const [feedbackJob, setFeedbackJob] = useState(null)
  const [feedbackCandidates, setFeedbackCandidates] = useState([])
  // HR-submitted interview feedback (from the Scheduled Interviews cards)
  const [fbModal, setFbModal] = useState(null)   // the interview being reviewed
  const [fbSaving, setFbSaving] = useState(false)
  const [hrFbForm, setHrFbForm] = useState({
    technical_score: 70, communication_score: 70, problem_solving_score: 70,
    cultural_fit_score: 70, strengths: '', weaknesses: '', recommendations: '',
    hire_recommendation: 'proceed', additional_notes: '',
  })
  const [showOfferModal, setShowOfferModal] = useState(null)
  const [offerStep, setOfferStep] = useState(1)
  const [offerForm, setOfferForm] = useState({ offer_type: 'full_time', salary: '', bonus: '', start_date: '', expiry_date: '', terms: '' })
  const [offerEmail, setOfferEmail] = useState({ subject: '', body: '' })
  const [offerMailboxId, setOfferMailboxId] = useState(undefined)
  const [offerMailPreview, setOfferMailPreview] = useState(null)

  // Server-backed: templates belong to the company, not the browser. The starter
  // set is seeded server-side on first read, so it is not duplicated here.
  const [mailTemplates, setMailTemplates] = useState([])
  const [templatesError, setTemplatesError] = useState('')
  const [showTemplateManager, setShowTemplateManager] = useState(false)
  const [showComposeMail, setShowComposeMail] = useState(false)
  const [composeMail, setComposeMail] = useState({ to: '', subject: '', body: '' })
  const [composeMailboxId, setComposeMailboxId] = useState(undefined)
  const [composeSending, setComposeSending] = useState(false)
  const [composeError, setComposeError] = useState('')
  const [offerMailSending, setOfferMailSending] = useState(false)
  const [offerMailError, setOfferMailError] = useState('')
  const [composeDropdownOpen, setComposeDropdownOpen] = useState(false)
  const [templateStageFilter, setTemplateStageFilter] = useState('all')
  const [templateForm, setTemplateForm] = useState({ id: null, stage: 'offer', name: '', subject: '', body: '' })
  const [editingTemplate, setEditingTemplate] = useState(null) // id of template being edited
  const [viewJob, setViewJob] = useState(null)
  const [atsJob, setAtsJob] = useState(null)
  const [atsCandidates, setAtsCandidates] = useState([])
  const [atsLoading, setAtsLoading] = useState(false)
  const [atsSearch, setAtsSearch] = useState('')
  const [expandedCandidate, setExpandedCandidate] = useState(null)
  const [selectedCandidates, setSelectedCandidates] = useState(new Set())
  const [topN, setTopN] = useState(5)
  const [topPercentile, setTopPercentile] = useState(25)
  const [selectionMode, setSelectionMode] = useState('top_n') // 'top_n' | 'top_percentile' | 'manual'
  const [interviewers, setInterviewers] = useState([])
  const [panelStaff, setPanelStaff] = useState([])        // HR/Admin users pickable as panelists
  const [manualName, setManualName] = useState('')
  const [manualEmail, setManualEmail] = useState('')
  const [selInterviewer, setSelInterviewer] = useState('')
  const [selectMode, setSelectMode] = useState(false)

  // Candidate stage tracker modal
  const [trackerModal, setTrackerModal] = useState(null)   // candidate stage history data
  const [trackerLoadingHR, setTrackerLoadingHR] = useState(false)

  const openStageTracker = async (candidateId) => {
    setTrackerLoadingHR(true)
    setTrackerModal({ loading: true })
    try {
      const r = await api.get(`/hr/candidates/${candidateId}/stage-history`)
      setTrackerModal(r.data)
    } catch {
      setTrackerModal(null)
    } finally {
      setTrackerLoadingHR(false)
    }
  }

  // Schedule interview from test results
  const [scheduleModal, setScheduleModal] = useState(null) // {candidate_id, candidate_name, candidate_email, job_id, job_title, interviewer_name, interviewer_email}
  const [schedForm, setSchedForm] = useState({ interview_type: 'TECHNICAL', mode: 'online', scheduled_datetime: '', duration_minutes: 60, meeting_link: '', location: '', notes: '', send_email: true, email_subject: '', email_body: '', interviewer_ids: [], manual_panelists: [] })
  const [schedMailboxId, setSchedMailboxId] = useState(undefined)
  const [schedSubmitting, setSchedSubmitting] = useState(false)

  // When the schedule modal opens, clear the manual-add inputs and (re)load the
  // selectable people. NOTE: we do NOT reset schedForm here — the opener already
  // sets it (and pre-selects the job's interviewer); resetting would race that.
  useEffect(() => {
    if (scheduleModal) {
      setManualName(''); setManualEmail('')
      api.get('/hr/interviewers').then(r => setInterviewers(r.data || [])).catch(() => {})
      api.get('/hr/panel-staff').then(r => setPanelStaff(r.data || [])).catch(() => {})
    }
  }, [scheduleModal])

  // Add a typed panelist (name + email) to the panel, deduped by email.
  const addManualPanelist = () => {
    const name = manualName.trim()
    const email = manualEmail.trim()
    if (!email) return
    setSchedForm(f => (
      f.manual_panelists.some(m => m.email.toLowerCase() === email.toLowerCase())
        ? f
        : { ...f, manual_panelists: [...f.manual_panelists, { name: name || email, email }] }
    ))
    setManualName(''); setManualEmail('')
  }

  // Round 2 (Coding) selection + invite email, from the test-results sheet
  const [round2Sel, setRound2Sel] = useState(() => new Set())  // candidate_ids selected for Round 2
  const [round2Modal, setRound2Modal] = useState(null)         // { job_id, job_title, rows:[{candidate_id,candidate_name,candidate_email}] }
  const [round2Sending, setRound2Sending] = useState(false)
  const [round2Deadline, setRound2Deadline] = useState('')   // toolbar date for Round 2 (like ATS)
  const [round2TopN, setRound2TopN] = useState(5)            // "Top N Candidates" quick-select count
  const [round2Email, setRound2Email] = useState({ deadline: '', testLink: '', subject: '', body: '' })
  const [round2MailboxId, setRound2MailboxId] = useState(undefined)
  // A candidate qualifies for Round 2 when their completed Round 1 (MCQ) hasn't been advanced yet.
  const round2Eligible = (r) => r.round2_pending === true && r.candidate_stage !== 'rejected'

  // Select the top-N eligible candidates by test score (highest first) — like the ATS sheet's Top N.
  const applyRound2TopN = () => {
    const ranked = testResultCandidates.filter(round2Eligible)
      .sort((a, b) => (b.test_score ?? -1) - (a.test_score ?? -1))
    setRound2Sel(new Set(ranked.slice(0, Math.max(1, round2TopN)).map(r => r.candidate_id)))
  }

  const openRound2Modal = (rows) => {
    if (!rows.length) return
    const jobTitle = rows[0].job_title || 'the position'
    setRound2Email({
      deadline: round2Deadline || '',
      testLink: `${window.location.origin}/candidate/interviews`,
      subject: `Round 2 (Coding) – ${jobTitle}`,
      body: `Congratulations {{candidate_name}}!\n\nYou have cleared Round 1 (MCQ) for ${jobTitle} and are invited to Round 2 — the Coding assessment.\n\nTest Link: {{test_link}}\nDeadline: {{deadline}}\n\nPlease ensure a stable internet connection and a quiet environment.\n\nBest of luck!\nHireFlow Recruitment Team`,
    })
    setRound2Modal({ job_id: rows[0].job_id, job_title: jobTitle, rows })
  }

  const sendRound2 = async () => {
    if (!round2Modal) return
    if (!round2Email.deadline) return alert('Select a deadline date for Round 2')
    setRound2Sending(true)
    try {
      const res = await api.post(`/hr/jobs/${round2Modal.job_id}/send-round2-invite`, {
        candidate_ids: round2Modal.rows.map(r => r.candidate_id),
        test_deadline: round2Email.deadline || '',
        test_link: round2Email.testLink,
        invite_subject: round2Email.subject,
        invite_body: round2Email.body,
        mailbox_id: round2MailboxId ?? null,
      })
      alert(res.data.message)
      setRound2Modal(null)
      setRound2Sel(new Set())
      await refreshTestResults()
    } catch (err) { alert(err.response?.data?.detail || 'Failed to send Round 2') }
    setRound2Sending(false)
  }

  // Report what actually happened. dispatch() queues the mail when Redis is up, so
  // the worker may not have sent it yet — claiming "Sent!" here would be the same
  // lie this button used to tell, just better disguised.
  const describeSendResult = (data) =>
    data?.mode === 'async' ? 'Queued for delivery.'
      : data?.sent === false ? `Could not send: ${data?.error || 'unknown error'}`
        : 'Sent.'

  const handleSendComposeMail = async () => {
    if (!composeMail.to) { setComposeError('Please enter a recipient email.'); return }
    if (!composeMail.subject) { setComposeError('Please enter a subject.'); return }
    if (!composeMail.body?.trim()) { setComposeError('Please enter a message.'); return }
    setComposeSending(true); setComposeError('')
    try {
      const res = await api.post('/hr/send-mail', {
        to: composeMail.to,
        cc: [],
        subject: composeMail.subject,
        body: composeMail.body,
        mailbox_id: composeMailboxId ?? null,
      })
      alert(`${describeSendResult(res.data)} (${composeMail.to})`)
      setShowComposeMail(false)
      setComposeMail({ to: '', subject: '', body: '' })
    } catch (err) {
      setComposeError(err.response?.data?.detail || 'Failed to send mail')
    }
    setComposeSending(false)
  }

  const handleSendOfferMail = async () => {
    setOfferMailSending(true); setOfferMailError('')
    try {
      const res = await api.post('/hr/send-mail', {
        to: offerMailPreview.emailTo,
        cc: [],
        subject: offerMailPreview.emailSubject,
        body: offerMailPreview.emailBody,
        mailbox_id: offerMailboxId ?? null,
        step_key: 'offer_letter',
      })
      alert(`${describeSendResult(res.data)} (${offerMailPreview.candidate_name})`)
      setOfferMailPreview(null)
    } catch (err) {
      setOfferMailError(err.response?.data?.detail || 'Failed to send offer email')
    }
    setOfferMailSending(false)
  }

  const loadMailTemplates = async () => {
    try {
      const res = await api.get('/hr/mail-templates')
      setMailTemplates(res.data || [])
      setTemplatesError('')
      return res.data || []
    } catch (err) {
      // Not swallowed: an empty template list and a failed load look identical in
      // the UI otherwise, and HR would think their templates had been deleted.
      setTemplatesError(err.response?.data?.detail || 'Could not load mail templates')
      return null
    }
  }

  // One-shot: lift any templates still sitting in this browser onto the server, so
  // upgrading does not appear to delete the ones HR wrote before the change.
  const migrateLocalTemplates = async (serverTemplates) => {
    let local
    try { local = JSON.parse(localStorage.getItem('hf_mail_templates') || 'null') }
    catch { local = null }
    if (!Array.isArray(local) || local.length === 0) {
      localStorage.removeItem('hf_mail_templates')
      return
    }
    const known = new Set((serverTemplates || []).map(t => (t.name || '').trim().toLowerCase()))
    const missing = local.filter(t => t?.name && !known.has(t.name.trim().toLowerCase()))
    for (const t of missing) {
      try {
        await api.post('/hr/mail-templates', {
          stage: t.stage || 'custom', name: t.name,
          subject: t.subject || '', body: t.body || '',
        })
      } catch { /* name collision or invalid row — skip it, keep the rest */ }
    }
    localStorage.removeItem('hf_mail_templates')
    if (missing.length) await loadMailTemplates()
  }

  useEffect(() => {
    let cancelled = false
    loadMailTemplates().then(rows => {
      if (!cancelled && rows) migrateLocalTemplates(rows)
    })
    return () => { cancelled = true }
  }, [])

  const saveTemplate = async () => {
    if (!templateForm.name.trim() || !templateForm.subject.trim() || !templateForm.body.trim()) {
      alert('Please fill in all fields.'); return
    }
    const payload = {
      stage: templateForm.stage, name: templateForm.name,
      subject: templateForm.subject, body: templateForm.body,
    }
    try {
      if (editingTemplate === 'new') await api.post('/hr/mail-templates', payload)
      else await api.put(`/hr/mail-templates/${editingTemplate}`, payload)
      setEditingTemplate(null)
      await loadMailTemplates()
    } catch (err) {
      alert(err.response?.data?.detail || 'Failed to save template')
    }
  }

  const deleteTemplate = async (tpl) => {
    if (!window.confirm(`Delete "${tpl.name}"?`)) return
    try {
      await api.delete(`/hr/mail-templates/${tpl.id}`)
      await loadMailTemplates()
    } catch (err) {
      alert(err.response?.data?.detail || 'Failed to delete template')
    }
  }

  const refreshTestResults = async () => {
    const res = await api.get('/hr/test-results')
    const data = res.data || []
    setTestResults(data)
    // Also refresh the open sheet if showing
    if (testResultJob) {
      const updated = data.filter(r => r.job_id === testResultJob.job_id)
      setTestResultCandidates(updated)
    }
  }

  const handleTestAction = async (r, action) => {
    if (action === 'reject') {
      const reason = prompt('Rejection reason (optional):')
      try {
        await api.post('/hr/test-results/action', { candidate_id: r.candidate_id, action: 'reject', rejection_reason: reason })
        alert(`${r.candidate_name} rejected`)
        await refreshTestResults()
      } catch (err) { alert(err.response?.data?.detail || 'Failed') }
    } else {
      try {
        const res = await api.post('/hr/test-results/action', { candidate_id: r.candidate_id, action: 'select' })
        alert(`${r.candidate_name} moved to Phone Screen. Now schedule an interview.`)
        await refreshTestResults()
      } catch (err) { alert(err.response?.data?.detail || 'Failed') }
    }
  }

  const handleScheduleInterview = async (e) => {
    e.preventDefault()
    if (!schedForm.scheduled_datetime) return alert('Select date & time')
    if ((schedForm.interviewer_ids?.length || 0) + (schedForm.manual_panelists?.length || 0) === 0)
      return alert('Add at least one panel member (pick a staff member or add one manually)')
    if (schedForm.mode === 'online' && !schedForm.meeting_link) return alert('Enter meeting link for online interview')
    if (schedForm.mode === 'offline' && !schedForm.location) return alert('Enter location for offline interview')
    setSchedSubmitting(true)
    try {
      const res = await api.post('/hr/schedule-interview', { ...schedForm, candidate_id: scheduleModal.candidate_id, job_id: scheduleModal.job_id, mailbox_id: schedMailboxId ?? null })
      alert(res.data.message + '\nCandidate moved to Scheduled Interviews.')
      setScheduleModal(null)
      setTestResultJob(null)
      setTestResultCandidates([])
      await refreshTestResults()
      api.get('/hr/interviews').then(r => setInterviews(r.data || [])).catch(() => {})
      setTab('interviews')
    } catch (err) { alert(err.response?.data?.detail || 'Failed to schedule') }
    setSchedSubmitting(false)
  }

  // Open the HR feedback modal for an interview, prefilling from existing feedback.
  const openFeedback = (iv) => {
    const existing = feedbackData.find(f => f.interview_id === iv.id)
    const parseList = (v) => {
      if (Array.isArray(v)) return v.join(', ')
      try { const a = JSON.parse(v || '[]'); return Array.isArray(a) ? a.join(', ') : (v || '') } catch { return v || '' }
    }
    setHrFbForm(existing ? {
      technical_score: existing.technical_score ?? 70,
      communication_score: existing.communication_score ?? 70,
      problem_solving_score: existing.problem_solving_score ?? 70,
      cultural_fit_score: existing.cultural_fit_score ?? 70,
      strengths: parseList(existing.strengths),
      weaknesses: parseList(existing.weaknesses),
      recommendations: existing.recommendations || '',
      hire_recommendation: existing.hire_recommendation || 'proceed',
      additional_notes: existing.additional_notes || '',
    } : {
      technical_score: 70, communication_score: 70, problem_solving_score: 70,
      cultural_fit_score: 70, strengths: '', weaknesses: '', recommendations: '',
      hire_recommendation: 'proceed', additional_notes: '',
    })
    setFbModal(iv)
  }

  const submitHrFeedback = async () => {
    if (!fbModal) return
    setFbSaving(true)
    try {
      await api.post(`/hr/interviews/${fbModal.id}/feedback`, {
        ...hrFbForm,
        technical_score: Number(hrFbForm.technical_score) || 0,
        communication_score: Number(hrFbForm.communication_score) || 0,
        problem_solving_score: Number(hrFbForm.problem_solving_score) || 0,
        cultural_fit_score: Number(hrFbForm.cultural_fit_score) || 0,
        strengths: hrFbForm.strengths.split(',').map(s => s.trim()).filter(Boolean),
        weaknesses: hrFbForm.weaknesses.split(',').map(s => s.trim()).filter(Boolean),
      })
      setFbModal(null)
      api.get('/hr/interviews').then(r => setInterviews(r.data || [])).catch(() => {})
      api.get('/hr/feedback').then(r => setFeedbackData(r.data || [])).catch(() => {})
    } catch (err) {
      alert(err.response?.data?.detail || 'Failed to save feedback')
    }
    setFbSaving(false)
  }

  // Requirements
  const [reqData, setReqData] = useState(null)
  const [reqLoading, setReqLoading] = useState(false)
  const [expandedReqJob, setExpandedReqJob] = useState(null)
  const [reqStatusUpdating, setReqStatusUpdating] = useState(null)
  const [closingReqId, setClosingReqId] = useState(null)

  const fetchRequirements = async () => {
    setReqLoading(true)
    try {
      const r = await api.get('/hr/my-candidate-requirements')
      setReqData(r.data)
    } catch { setReqData(null) }
    setReqLoading(false)
  }

  const updateReqStatus = async (reqId, status, hrNotes) => {
    setReqStatusUpdating(reqId)
    try {
      await api.put(`/hr/candidate-requests/${reqId}`, { status, hr_notes: hrNotes || '' })
      fetchRequirements()
    } catch (err) {
      alert(err.response?.data?.detail || 'Failed to update')
    }
    setReqStatusUpdating(null)
  }

  const [pendingApprovalReqId, setPendingApprovalReqId] = useState(null)

  const approveAndCreateJob = async (req, job = {}) => {
    // DON'T approve yet — only open the form. Approval happens when form is SUBMITTED.
    setPendingApprovalReqId(req.id)
    const deptMatch = departments.find(d => d.name?.toLowerCase() === (req.department || job.department || '').toLowerCase())
    const formData = {
      title: job.job_title || req.job_title || '',
      description: req.job_description || '',
      requirements: [
        req.role_level ? `Role Level: ${req.role_level}` : '',
        req.education_requirement ? `Education: ${req.education_requirement}` : '',
        req.min_experience_years ? `Experience: ${req.min_experience_years}–${req.max_experience_years || '∞'} years` : '',
        req.notes || '',
      ].filter(Boolean).join('\n'),
      department_id: job.department_id || deptMatch?.id || '',
      location: job.location || '',
      employment_type: (req.employment_type || job.employment_type || 'full-time').toLowerCase().replace(' ', '-'),
      salary_min: '',
      salary_max: '',
      experience_years: req.min_experience_years || 0,
      required_skills: [...(req.required_skills || []), ...(req.preferred_skills || [])].join(', ') || (job.required_skills || []).join(', '),
    }
    setJobForm(formData)

    // If a draft job already exists for this requirement, approve it instead of creating a new one
    if (job.job_id && job.status === 'draft') {
      setEditJob({ id: job.job_id, status: 'draft', title: job.job_title })
      setIsApprovalMode(true)
    } else {
      setEditJob(null)
      setIsApprovalMode(false)
    }
    setShowModal(true)
    setTab('jobs')
  }

  // Notifications
  const [notifications, setNotifications] = useState([])
  const [showNotifPanel, setShowNotifPanel] = useState(false)
  const unreadCount = notifications.filter(n => !n.is_read).length

  const fetchNotifications = () => {
    api.get('/hr/notifications').then(r => setNotifications(r.data || [])).catch(() => {})
  }

  const markRead = async (id) => {
    await api.put(`/hr/notifications/${id}/read`).catch(() => {})
    setNotifications(prev => prev.map(n => n.id === id ? { ...n, is_read: true } : n))
  }

  const markAllRead = async () => {
    await api.put('/hr/notifications/read-all').catch(() => {})
    setNotifications(prev => prev.map(n => ({ ...n, is_read: true })))
  }

  // The status filter is server-side. With no ?status= the HR list returns every
  // job including closed ones, so a closed posting stays visible (with a Closed
  // badge and a Reopen button) instead of vanishing. Defaulted so the ~12 bare
  // fetchJobs() call sites still work.
  const fetchJobs = (statusFilter = jobFilters.status) => {
    const currentFetchId = ++fetchJobsId.current  // stamp this fetch; stale callbacks will bail
    setJobsLoading(true)
    setPipelineCandidates({})
    setJobPipelines({})
    return api.get('/hr/jobs', { params: statusFilter ? { status: statusFilter } : {} }).then(r => {
      if (fetchJobsId.current !== currentFetchId) return  // a newer fetch started; discard
      const data = r.data || []
      setJobs(data)
      setJobsLoading(false)
      data.forEach(j => {
        api.get(`/hr/jobs/${j.id}/candidates?per_page=100`).then(cr => {
          if (fetchJobsId.current !== currentFetchId) return  // stale; discard
          const candidates = cr.data?.candidates || []
          const stages = {}
          candidates.forEach(c => {
            const s = normalizeStage(c.current_stage)
            stages[s] = (stages[s] || 0) + 1
          })
          setJobPipelines(prev => ({ ...prev, [j.id]: { total: cr.data?.total_candidates || candidates.length, stages } }))
          setPipelineCandidates(prev => {
            const next = { ...prev }
            candidates.forEach(c => {
              const stage = normalizeStage(c.current_stage)
              const existing = next[stage] || []
              // Skip if this candidate_id is already in the bucket
              if (!existing.some(x => x.candidate_id === c.candidate_id)) {
                next[stage] = [...existing, { ...c, job_title: j.title }]
              }
            })
            return next
          })
        }).catch(() => {})
      })
    }).catch(() => { if (fetchJobsId.current === currentFetchId) setJobsLoading(false) })
  }

  // Owns the initial job fetch too, so changing the status filter re-queries the
  // server rather than re-filtering a list it was already excluded from.
  useEffect(() => {
    fetchJobs()
  }, [jobFilters.status])

  useEffect(() => {
    fetchRequirements()
    fetchNotifications()
    api.get('/hr/departments').then(r => setDepartments(r.data || [])).catch(() => {})
    api.get('/hr/interviews').then(r => setInterviews(r.data || [])).catch(() => {})
    api.get('/hr/offers').then(r => setOffers(r.data || [])).catch(() => {})
    api.get('/hr/test-results').then(r => setTestResults(r.data || [])).catch(() => {})
    api.get('/hr/feedback').then(r => setFeedbackData(r.data || [])).catch(() => {})
    // Poll notifications every 30s
    const notifInterval = setInterval(fetchNotifications, 30000)
    return () => clearInterval(notifInterval)
  }, [])

  // Refresh test results every time the test-results tab is opened
  useEffect(() => {
    if (tab === 'test-results') refreshTestResults()
    if (tab === 'feedback') api.get('/hr/feedback').then(r => setFeedbackData(r.data || [])).catch(() => {})
  }, [tab])

  const handleCreateJob = async (e) => {
    e.preventDefault()
    // Safety: if editJob is set, this shouldn't be called — use handleUpdateJob instead
    if (editJob) return
    // Require salary when approving from candidate request
    if (pendingApprovalReqId && (!jobForm.salary_min || !jobForm.salary_max)) {
      alert('Salary range is required when approving a job from a candidate request.')
      return
    }
    setSubmitting(true)
    try {
      await api.post('/hr/jobs', {
        ...jobForm,
        salary_min: jobForm.salary_min ? parseFloat(jobForm.salary_min) : null,
        salary_max: jobForm.salary_max ? parseFloat(jobForm.salary_max) : null,
        experience_years: parseInt(jobForm.experience_years) || 0,
        required_skills: jobForm.required_skills.split(',').map(s => s.trim()).filter(Boolean),
      })
      // If this was from a candidate request approval, approve the request now
      if (pendingApprovalReqId) {
        try {
          await api.put(`/hr/candidate-requests/${pendingApprovalReqId}`, { status: 'approved' })
        } catch {}
        setPendingApprovalReqId(null)
        fetchRequirements()
      }
      setShowModal(false)
      setJobForm({ title: '', description: '', requirements: '', department_id: '', location: '', employment_type: 'full-time', salary_min: '', salary_max: '', experience_years: 0, required_skills: '' })
      fetchJobs()
    } catch (err) {
      alert(err.response?.data?.detail || 'Failed to create job')
    }
    setSubmitting(false)
  }

  // Soft close — archives the job (status='closed'), reversible via handleReopenJob.
  // Closing the modal happens HERE, not in the onClick: doing it there meant a
  // cancelled confirm still dismissed the modal, which looked identical to success.
  const handleCloseJob = async (jobId) => {
    if (!confirm('Close this job posting?\n\nIt stops accepting applications and is hidden from candidates. You can reopen it later from the Closed filter.')) return
    try {
      const res = await api.delete(`/hr/jobs/${jobId}`)
      alert(res.data?.message || 'Job closed')
      setViewJob(null)
      fetchJobs()
    } catch (err) {
      alert(err.response?.data?.detail || 'Failed to close job')
    }
  }

  const handleReopenJob = async (jobId) => {
    if (!confirm('Reopen this job posting?\n\nIt becomes active again and visible to candidates.')) return
    try {
      const res = await api.put(`/hr/jobs/${jobId}/reopen`)
      alert(res.data?.message || 'Job reopened')
      setViewJob(null)
      fetchJobs()
    } catch (err) {
      alert(err.response?.data?.detail || 'Failed to reopen job')
    }
  }

  const handleDeleteJobPermanent = async (jobId) => {
    if (!confirm('Permanently DELETE this job and ALL of its data — candidates, ATS scores, interviews, tests and offers?\n\nThis cannot be undone.')) return
    try {
      const res = await api.delete(`/hr/jobs/${jobId}/permanent`)
      alert(res.data?.message || 'Job deleted')
      setViewJob(null)
      fetchJobs()
    } catch (err) {
      alert(err.response?.data?.detail || 'Failed to delete job')
    }
  }

  const openEditJob = (j) => {
    setEditJob(j)
    setIsApprovalMode(false)
    setJobForm({
      title: j.title, description: j.description || '', requirements: j.requirements || '',
      department_id: j.department_id || '', location: j.location || '',
      employment_type: j.employment_type || 'full-time',
      salary_min: j.salary_min || '', salary_max: j.salary_max || '',
      experience_years: j.experience_years || 0,
      required_skills: Array.isArray(j.required_skills) ? j.required_skills.join(', ') : (j.required_skills || '')
    })
    setShowModal(true)
  }

  const [isApprovalMode, setIsApprovalMode] = useState(false)

  const handleUpdateJob = async (e) => {
    e.preventDefault()
    // Only require salary and use approve endpoint when explicitly in approval mode
    if (isApprovalMode) {
      if (!jobForm.salary_min || !jobForm.salary_max) {
        alert('Salary range is required to approve this job posting.')
        return
      }
    }
    setSubmitting(true)
    try {
      if (isApprovalMode) {
        // Approve: changes status from draft → active
        await api.put(`/hr/jobs/${editJob.id}/approve`, {
          salary_min: parseFloat(jobForm.salary_min),
          salary_max: parseFloat(jobForm.salary_max),
          location: jobForm.location || undefined,
          employment_type: jobForm.employment_type || undefined,
          description: jobForm.description || undefined,
          requirements: jobForm.requirements || undefined,
        })
      } else {
        // Regular update: just saves changes, doesn't change status
        await api.put(`/hr/jobs/${editJob.id}`, {
          ...jobForm,
          salary_min: jobForm.salary_min ? parseFloat(jobForm.salary_min) : null,
          salary_max: jobForm.salary_max ? parseFloat(jobForm.salary_max) : null,
          experience_years: parseInt(jobForm.experience_years) || 0,
          required_skills: jobForm.required_skills.split(',').map(s => s.trim()).filter(Boolean),
        })
      }
      // If this was from a candidate request approval, approve the request now
      if (pendingApprovalReqId) {
        try {
          await api.put(`/hr/candidate-requests/${pendingApprovalReqId}`, { status: 'approved' })
        } catch {}
        fetchRequirements()
      }
      setShowModal(false)
      setEditJob(null)
      setIsApprovalMode(false)
      setPendingApprovalReqId(null)
      setJobForm({ title: '', description: '', requirements: '', department_id: '', location: '', employment_type: 'full-time', salary_min: '', salary_max: '', experience_years: 0, required_skills: '' })
      fetchJobs()
    } catch (err) {
      alert(err.response?.data?.detail || 'Failed to update job')
    }
    setSubmitting(false)
  }

  const openAtsSheet = async (job) => {
    setAtsJob(job)
    setAtsLoading(true)
    setAtsCandidates([])
    setAtsSearch('')
    setExpandedCandidate(null)
    setSelectedCandidates(new Set())
    setSelectMode(false)
    setSelInterviewer('')
    try {
      const res = await api.get(`/hr/jobs/${job.id}/candidates?sort_by=ats_score&order=desc&per_page=100`)
      setAtsCandidates(res.data?.candidates || [])
    } catch (err) {
      setAtsCandidates([])
    }
    api.get('/hr/interviewers').then(r => setInterviewers(r.data || [])).catch(() => {})
    api.get(`/hr/jobs/${job.id}/tests`).then(r => setJobTests(r.data || [])).catch(() => setJobTests([]))
    setAtsLoading(false)
  }

  const handleSelectTopN = () => {
    const pool = atsCandidates.filter(c => !isMailSent(c))
    const top = pool.slice(0, topN).map(c => c.candidate_id)
    setSelectedCandidates(new Set(top))
  }

  const handleSelectTopPercentile = () => {
    const pool = atsCandidates.filter(c => !isMailSent(c))
    if (!pool.length) return
    const count = Math.max(1, Math.ceil(pool.length * topPercentile / 100))
    const top = pool.slice(0, count).map(c => c.candidate_id)
    setSelectedCandidates(new Set(top))
  }

  const toggleCandidate = (id) => {
    // Already-emailed candidates can't be (re)selected.
    const cand = atsCandidates.find(c => c.candidate_id === id)
    if (cand && isMailSent(cand)) return
    setSelectedCandidates(prev => {
      const next = new Set(prev)
      next.has(id) ? next.delete(id) : next.add(id)
      return next
    })
  }

  const [testDeadline, setTestDeadline] = useState('')
  const [sendingInvites, setSendingInvites] = useState(false)
  const [showEmailPreview, setShowEmailPreview] = useState(false)
  const [inviteTplOpen, setInviteTplOpen] = useState(false)
  const [rejectTplOpen, setRejectTplOpen] = useState(false)
  const [jobTests, setJobTests] = useState([])
  const [selectedTestId, setSelectedTestId] = useState('')
  // 'combined' = one test (MCQ + coding); 'two_round' = Round 1 MCQ now, coding sent as Round 2 later.
  const [testStructure, setTestStructure] = useState('combined')
  // Proctoring the interviewer configured on the selected test. `proctorBaseline` is what
  // the test itself says; `proctorCfg` is what HR currently has ticked. Overrides are only
  // sent once HR clicks Reconfigure, and they apply to this batch of invites only — the
  // interviewer's test is never modified.
  // Per-send question timing. Like the proctoring block, this lands on this batch's clones
  // only — the template and already-sent invites are untouched.
  const [timeCfg, setTimeCfg] = useState(null)
  const [timeBaseline, setTimeBaseline] = useState(null)
  const [timeCounts, setTimeCounts] = useState({})     // {question_type: how many on this test}
  const TYPE_TIME_FALLBACK = { multiple_choice: 1, coding: 15, short_answer: 5, case_study: 5, aptitude: 5, communication: 5 }

  const [proctorCfg, setProctorCfg] = useState(null)
  const [proctorBaseline, setProctorBaseline] = useState(null)
  const [proctorApplied, setProctorApplied] = useState(false)
  const [emailTemplates, setEmailTemplates] = useState({
    testLink: `${window.location.origin}/candidate/interviews`,
    inviteSubject: '',
    inviteBody: '',
    rejectSubject: '',
    rejectBody: '',
  })

  // "Send from" mailbox per outgoing mail. undefined = MailFromPicker has not
  // seeded it yet; it fills each one with the stage's configured mailbox on load.
  const [inviteMailboxId, setInviteMailboxId] = useState(undefined)
  const [rejectMailboxId, setRejectMailboxId] = useState(undefined)

  // The proctoring flags, in display order. `copy_paste_allowed` is stored inverted
  // relative to the other two (true = candidate MAY copy/paste); the label says so, and we
  // pass the value through unchanged so this screen agrees with the Create Test wizard.
  const PROCTOR_FIELDS = [
    { key: 'enable_webcam_monitoring', label: 'Webcam Monitoring' },
    { key: 'enable_tab_switch_detection', label: 'Tab Switch Detection' },
    { key: 'require_fullscreen', label: 'Require Fullscreen' },
    { key: 'copy_paste_allowed', label: 'Allow Copy / Paste' },
  ]

  const resetProctoring = () => {
    setProctorCfg(null)
    setProctorBaseline(null)
    setProctorApplied(false)
    setTimeCfg(null)
    setTimeBaseline(null)
    setTimeCounts({})
  }

  // Seed both the live values and the baseline from whichever test HR just picked.
  const onSelectTest = (testId) => {
    setSelectedTestId(testId)
    const test = jobTests.find(t => String(t.id) === String(testId))
    if (!test) return resetProctoring()
    const settings = PROCTOR_FIELDS.reduce((acc, f) => {
      acc[f.key] = !!test.security_settings?.[f.key]
      return acc
    }, {})
    setProctorCfg(settings)
    setProctorBaseline(settings)
    setProctorApplied(false)
    // Question timing for this test: what it runs at today, and what HR is changing it to.
    const times = { ...(test.time_per_question_type || {}) }
    setTimeCounts(test.question_type_counts || {})
    setTimeCfg(times)
    setTimeBaseline(times)
  }

  // Total minutes = every question at its type's time. This mirrors the candidate's clock,
  // which is literally the sum of their questions' time_limit_minutes.
  // Round 1 of a two-round send carries only non-coding questions (see _qtype_in_round), so
  // coding must be left out of the estimate — a coding time set here would never apply.
  const timeCountsForRound = Object.fromEntries(
    Object.entries(timeCounts || {})
      .filter(([type]) => testStructure !== 'two_round' || type !== 'coding')
  )

  const sendDuration = (times) => Object.entries(timeCountsForRound)
    .reduce((total, [type, n]) => total + n * (times?.[type] ?? TYPE_TIME_FALLBACK[type] ?? 5), 0)

  const timeChangedTypes = (timeCfg && timeBaseline)
    ? Object.keys(timeCountsForRound).filter(t =>
        (timeCfg[t] ?? TYPE_TIME_FALLBACK[t] ?? 5) !== (timeBaseline[t] ?? TYPE_TIME_FALLBACK[t] ?? 5))
    : []

  const proctorChangedKeys = (proctorCfg && proctorBaseline)
    ? PROCTOR_FIELDS.filter(f => proctorCfg[f.key] !== proctorBaseline[f.key])
    : []
  // Edits that HR made but never confirmed with Reconfigure. Sending in this state would
  // silently drop them, so the Send button is blocked until they apply or reset.
  const proctorUnapplied = (proctorChangedKeys.length + timeChangedTypes.length) > 0 && !proctorApplied

  const openEmailPreview = () => {
    if (selectedCandidates.size === 0) return alert('Select at least one candidate')
    if (!testDeadline) return alert('Set a test deadline')
    // Fetch available tests for this job
    api.get(`/hr/jobs/${atsJob.id}/tests`).then(r => setJobTests(r.data || [])).catch(() => setJobTests([]))
    setSelectedTestId('')
    setTestStructure('combined')
    resetProctoring()
    const jobTitle = atsJob?.title || 'the position'
    setEmailTemplates({
      testLink: `${window.location.origin}/candidate/interviews`,
      inviteSubject: `Test Invitation – ${jobTitle}`,
      inviteBody: `Dear {{candidate_name}},\n\nCongratulations! Your profile impressed our team, and we are pleased to invite you to the next stage for the ${jobTitle} role — an online assessment designed to let your skills shine.\n\nTest Link: {{test_link}}\nComplete By: ${testDeadline}\n\nThis is your opportunity to show us what you can do. For the best experience, choose a quiet environment with a stable internet connection before you begin.\n\nBest of luck — we're rooting for you!\n\nWarm regards,\nThe Talent Acquisition Team`,
      rejectSubject: `Application Update – ${jobTitle}`,
      rejectBody: `Dear {{candidate_name}},\n\nThank you for the time and genuine interest you showed in the ${jobTitle} role, and for allowing us to consider your application.\n\nAfter careful and thoughtful review, we have decided to move forward with other candidates whose experience more closely aligns with the specific requirements of this position. This was not an easy decision — we were genuinely impressed by much of your background.\n\nPlease don't be discouraged. We warmly encourage you to apply for future opportunities that match your skills and aspirations, and we wish you great success in your career journey.\n\nWith appreciation,\nThe Talent Acquisition Team`,
    })
    setShowEmailPreview(true)
  }

  const handleSendAfterReview = async () => {
    setSendingInvites(true)
    try {
      const res = await api.post(`/hr/jobs/${atsJob.id}/send-test-invite`, {
        candidate_ids: [...selectedCandidates],
        test_deadline: testDeadline,
        test_id: selectedTestId ? parseInt(selectedTestId) : null,
        test_link: emailTemplates.testLink,
        // Manual Select: only the picked candidates get the invite; nobody else is emailed
        // (no auto-rejection). Top-N / Top-Percentile still auto-reject the rest.
        reject_others: selectionMode !== 'manual',
        two_round: testStructure === 'two_round',
        // Only sent once HR confirms with Reconfigure; applies to these invites only.
        proctoring: proctorApplied ? proctorCfg : undefined,
        // Same gate: only the times HR actually changed are sent, and only for this batch.
        time_per_question_type: (proctorApplied && timeChangedTypes.length) ? timeCfg : undefined,
        invite_subject: emailTemplates.inviteSubject,
        invite_body: emailTemplates.inviteBody,
        reject_subject: emailTemplates.rejectSubject,
        reject_body: emailTemplates.rejectBody,
        // Per-send sender overrides; null = use the stage's configured mailbox.
        mailbox_id: inviteMailboxId ?? null,
        reject_mailbox_id: rejectMailboxId ?? null,
      })
      alert(res.data.message)
      setShowEmailPreview(false)
      await openAtsSheet(atsJob)
    } catch (err) {
      alert(err.response?.data?.detail || 'Failed to send')
    }
    setSendingInvites(false)
  }

  const handleAssignInterviewer = async () => {
    if (!selInterviewer) return alert('Select an interviewer')
    try {
      const res = await api.put(`/hr/jobs/${atsJob.id}/assign-interviewer?interviewer_id=${selInterviewer}`)
      alert(`Interviewer ${res.data.interviewer_name} assigned to this job!`)
      fetchJobs()
    } catch (err) {
      alert(err.response?.data?.detail || 'Failed to assign interviewer')
    }
  }

  const handleSendOffer = async () => {
    try {
      await api.post('/hr/offers', {
        candidate_id: showOfferModal.candidate_id,
        offer_type: offerForm.offer_type,
        salary: parseFloat(offerForm.salary),
        bonus: offerForm.bonus ? parseFloat(offerForm.bonus) : null,
        start_date: offerForm.start_date,
        expiry_date: offerForm.expiry_date,
        terms: offerForm.terms,
        mailbox_id: offerMailboxId ?? null,
      })
      alert(`Offer letter sent to ${showOfferModal.candidate_name} (${showOfferModal.candidate_email})!`)
      setShowOfferModal(null)
      setOfferStep(1)
      setOfferForm({ offer_type: 'full_time', salary: '', bonus: '', start_date: '', expiry_date: '', terms: '' })
      setOfferEmail({ subject: '', body: '' })
      api.get('/hr/offers').then(r => setOffers(r.data || [])).catch(() => {})
    } catch (err) {
      const detail = err.response?.data?.detail
      const msg = Array.isArray(detail) ? detail.map(d => d.msg || d).join(', ') : (typeof detail === 'string' ? detail : 'Failed to send offer')
      alert(msg)
    }
  }

  const handleDuplicateJob = async (j) => {
    setJobForm({
      title: j.title + ' (Copy)', description: j.description || '', requirements: j.requirements || '',
      department_id: j.department_id || '', location: j.location || '',
      employment_type: j.employment_type || 'full-time',
      salary_min: j.salary_min || '', salary_max: j.salary_max || '',
      experience_years: j.experience_years || 0,
      required_skills: Array.isArray(j.required_skills) ? j.required_skills.join(', ') : (j.required_skills || '')
    })
    setEditJob(null)
    setShowModal(true)
  }

  const getInitials = (name) => {
    if (!name) return '?'
    const parts = name.trim().split(' ').filter(Boolean)
    if (parts.length === 1) return parts[0].substring(0, 2).toUpperCase()
    return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase()
  }
  const AVATAR_COLORS = ['#6366f1', '#8b5cf6', '#ec4899', '#06b6d4', '#2b7cba', '#10b981', '#f59e0b']
  const avatarColor = (name) => AVATAR_COLORS[(name || '').charCodeAt(0) % AVATAR_COLORS.length]

  return (
    <>
      <Header
        title="HR Dashboard"
        subtitle="Manage recruitment pipeline"
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
                background: 'none', border: 'none', color: '#2b7cba', fontSize: '0.8rem',
                cursor: 'pointer', fontWeight: 600
              }}>Mark all read</button>
            )}
          </div>
          {notifications.length === 0 ? (
            <div style={{ padding: 24, textAlign: 'center', color: '#9ca3af', fontSize: '0.85rem' }}>No notifications</div>
          ) : (
            notifications.map(n => (
              <div
                key={n.id}
                onClick={() => { markRead(n.id); if (n.type === 'test_published') setTab('ats'); if (n.type === 'candidate_request_new') setTab('requirements'); if (n.type === 'job_pending_approval') setTab('jobs') }}
                style={{
                  padding: '12px 16px', borderBottom: '1px solid #f9fafb', cursor: 'pointer',
                  background: n.is_read ? '#fff' : '#f0f7ff', transition: 'background 0.2s'
                }}
              >
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 }}>
                  <span style={{ fontWeight: n.is_read ? 400 : 700, fontSize: '0.85rem', color: '#111827' }}>{n.title}</span>
                  {!n.is_read && <span style={{ width: 8, height: 8, borderRadius: '50%', background: '#2b7cba', flexShrink: 0 }} />}
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


      {tab === 'overview' && (() => {
        // Compute chart data from real jobs/interviews
        const deptData = Object.entries(jobs.reduce((acc, j) => { acc[j.department || 'Other'] = (acc[j.department || 'Other'] || 0) + 1; return acc }, {}))
          .map(([name, value]) => ({ name, value })).sort((a, b) => b.value - a.value)

        const rawStages = Object.values(jobPipelines).reduce((acc, p) => {
          Object.entries(p.stages || {}).forEach(([stage, count]) => {
            const s = normalizeStage(stage)
            acc[s] = (acc[s] || 0) + count
          })
          return acc
        }, {})
        const stageData = PIPELINE_STAGES.map(s => ({ stage: s.label, count: rawStages[s.key] || 0, color: s.color }))

        const CustomTooltip = ({ active, payload, label }) => {
          if (!active || !payload?.length) return null
          return (
            <div style={{ background: '#fff', border: '1px solid #e5e7eb', borderRadius: 8, padding: '8px 14px', fontSize: '0.85rem', boxShadow: '0 4px 12px rgba(0,0,0,0.08)' }}>
              <div style={{ color: '#9ca3af', marginBottom: 2, fontSize: '0.75rem', fontFamily: 'var(--font-display)' }}>{label || payload[0]?.name}</div>
              <div style={{ color: '#2b7cba', fontWeight: 700 }}>{payload[0].value}</div>
            </div>
          )
        }

        return (
          <>
            {/* Stats Cards */}
            <div className="stats-grid">
              {[
                { label: 'Job Postings', value: jobs.length, cls: 'amber' },
                { label: 'Active Jobs', value: jobs.filter(j => j.status === 'active' || j.status === 'open').length, cls: 'blue' },
                { label: 'Interviews', value: interviews.length, cls: 'green' },
                { label: 'Offers', value: offers.length, cls: 'purple' },
              ].map((s, i) => (
                <div key={i} className={`stat-card ${s.cls}`}>
                  <div className="stat-label">{s.label}</div>
                  <div className="stat-value">{s.value}</div>
                </div>
              ))}
            </div>

            {/* Charts Row */}
            <div className="grid-2 mb-24">
              {/* Hiring Pipeline / Candidates by Stage */}
              <div className="card">
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
                  <h3>Hiring Pipeline</h3>
                  <span style={{ fontSize: '0.78rem', color: 'var(--text-tertiary)', fontFamily: 'var(--font-mono)' }}>
                    {stageData.reduce((s, d) => s + d.count, 0)} total candidates
                  </span>
                </div>
                {stageData.every(d => d.count === 0) ? (
                  <div style={{ height: 260, display: 'grid', placeItems: 'center', color: 'var(--text-tertiary)' }}>No candidate data yet</div>
                ) : (
                  <ResponsiveContainer width="100%" height={260}>
                    <BarChart data={stageData} barSize={30} margin={{ top: 20, right: 8, left: 0, bottom: 50 }}>
                      <XAxis dataKey="stage" tick={{ fill: '#9ca3af', fontSize: 10, fontFamily: 'var(--font-display)' }} axisLine={false} tickLine={false} angle={-20} textAnchor="end" height={55} />
                      <YAxis allowDecimals={false} tick={{ fill: '#9ca3af', fontSize: 11 }} axisLine={false} tickLine={false} width={28} />
                      <Tooltip content={<CustomTooltip />} />
                      <Bar dataKey="count" radius={[6, 6, 0, 0]}>
                        {stageData.map((s, i) => (
                          <Cell key={i} fill={s.color} fillOpacity={0.85} />
                        ))}
                        <LabelList dataKey="count" position="top" style={{ fontSize: 11, fontWeight: 700, fill: '#374151', fontFamily: 'var(--font-mono)' }}
                          formatter={(v) => v > 0 ? v : ''} />
                      </Bar>
                    </BarChart>
                  </ResponsiveContainer>
                )}
              </div>

              {/* Jobs by Department */}
              <div className="card">
                <h3 style={{ marginBottom: 16 }}>Jobs by Department</h3>
                {deptData.length > 0 ? (
                  <>
                    <ResponsiveContainer width="100%" height={200}>
                      <PieChart>
                        <Pie data={deptData} cx="50%" cy="50%" innerRadius={50} outerRadius={85}
                          dataKey="value" stroke="#fff" strokeWidth={2} paddingAngle={2}>
                          {deptData.map((_, i) => (
                            <Cell key={i} fill={CHART_COLORS[i % CHART_COLORS.length]} />
                          ))}
                        </Pie>
                        <Tooltip content={<CustomTooltip />} />
                      </PieChart>
                    </ResponsiveContainer>
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10, justifyContent: 'center', marginTop: 8 }}>
                      {deptData.map((d, i) => (
                        <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: '0.78rem', color: '#6b7280' }}>
                          <div style={{ width: 8, height: 8, borderRadius: 2, background: CHART_COLORS[i % CHART_COLORS.length] }} />
                          {d.name} ({d.value})
                        </div>
                      ))}
                    </div>
                  </>
                ) : (
                  <div style={{ height: 260, display: 'grid', placeItems: 'center', color: 'var(--text-tertiary)' }}>No department data</div>
                )}
              </div>
            </div>

            {/* Recent Jobs Table */}
            <div className="card">
              <div className="flex-between mb-16">
                <h3>Recent Job Postings</h3>
                <button className="btn btn-primary btn-sm" onClick={() => { setEditJob(null); setJobForm({ title: '', description: '', requirements: '', department_id: '', location: '', employment_type: 'full-time', salary_min: '', salary_max: '', experience_years: 0, required_skills: '' }); setShowModal(true) }}>
                  <Plus size={14} /> New Job
                </button>
              </div>
              {jobs.length === 0 ? (
                <div style={{ padding: 40, textAlign: 'center', color: 'var(--text-tertiary)' }}>No job postings yet.</div>
              ) : (
                <div className="table-wrapper">
                  <table>
                    <thead><tr><th>Title</th><th>Department</th><th>Location</th><th>Status</th><th>Posted</th></tr></thead>
                    <tbody>
                      {jobs.slice(0, 5).map(j => (
                        <tr key={j.id}>
                          <td style={{ fontWeight: 600 }}>{j.title}</td>
                          <td>{j.department}</td>
                          <td>{j.location}</td>
                          <td><span className={`badge ${j.status === 'active' ? 'badge-green' : 'badge-neutral'}`}>{j.status}</span></td>
                          <td className="mono text-sm text-muted">{j.created_at ? new Date(j.created_at).toLocaleDateString() : '—'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          </>
        )
      })()}

      {tab === 'jobs' && (() => {
        const openCreateJob = () => { setEditJob(null); setJobForm({ title: '', description: '', requirements: '', department_id: '', location: '', employment_type: 'full-time', salary_min: '', salary_max: '', experience_years: 0, required_skills: '' }); setShowModal(true) }
        const openCreateTest = (jobId = null) => { setCreateTestJobId(jobId); setShowCreateTest(true) }

        // Full-screen Create Test wizard, rendered inside the Job Postings area.
        if (showCreateTest) {
          return (
            <div>
              <div className="flex-between mb-24">
                <div>
                  <h3 style={{ fontSize: '1.2rem' }}>Create Test</h3>
                  <span className="text-sm text-muted">Build an AI-generated assessment for a job posting</span>
                </div>
                <button className="btn btn-secondary" onClick={() => { setShowCreateTest(false); setCreateTestJobId(null) }}>
                  ← Back to Job Postings
                </button>
              </div>
              <CreateTestWizard
                role="hr"
                showCandidateRequests={false}
                preselectedJobId={createTestJobId}
                onSaved={fetchJobs}
                onPublished={fetchJobs}
                onNavigateAway={() => { setShowCreateTest(false); setCreateTestJobId(null) }}
              />
            </div>
          )
        }

        const filtered = jobs.filter(j => {
          if (jobFilters.status && j.status !== jobFilters.status) return false
          if (jobFilters.department && j.department !== jobFilters.department) return false
          if (jobFilters.employment_type && j.employment_type !== jobFilters.employment_type) return false
          if (jobFilters.location && !j.location?.toLowerCase().includes(jobFilters.location.toLowerCase())) return false
          return true
        })
        const daysOpen = (d) => d ? Math.max(1, Math.floor((Date.now() - new Date(d).getTime()) / 86400000)) : '—'
        const uniqueDepts = [...new Set(jobs.map(j => j.department).filter(Boolean))]
        const uniqueTypes = [...new Set(jobs.map(j => j.employment_type).filter(Boolean))]

        return (
          <div>
            {/* Header */}
            <div className="flex-between mb-24">
              <div>
                <h3 style={{ fontSize: '1.2rem' }}>Job Postings ({jobs.length})</h3>
                <span className="text-sm text-muted">Create jobs, build assessments, and track hiring progress</span>
              </div>
              <div className="flex gap-10" style={{ alignItems: 'center' }}>
                <button className="btn btn-secondary" onClick={() => openCreateTest(null)} title="Build an AI-generated test for a job">
                  <ClipboardList size={16} /> Create Test
                </button>
                <button className="btn btn-primary" onClick={openCreateJob}>
                  <Plus size={16} /> Create Job
                </button>
              </div>
            </div>

            {/* Filters */}
            <div className="card mb-24" style={{ padding: 20 }}>
              <div className="flex-center gap-8 mb-16" style={{ color: 'var(--text-secondary)', fontWeight: 600, fontSize: '0.9rem' }}>
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><polygon points="22 3 2 3 10 12.46 10 19 14 21 14 12.46 22 3"/></svg>
                Filters
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 14 }}>
                <div className="input-group" style={{ marginBottom: 0 }}>
                  <label>Status</label>
                  <select className="select" value={jobFilters.status} onChange={e => setJobFilters({ ...jobFilters, status: e.target.value })}>
                    <option value="">All Status</option>
                    <option value="active">Active</option>
                    <option value="closed">Closed</option>
                    <option value="draft">Draft</option>
                  </select>
                </div>
                <div className="input-group" style={{ marginBottom: 0 }}>
                  <label>Department</label>
                  <select className="select" value={jobFilters.department} onChange={e => setJobFilters({ ...jobFilters, department: e.target.value })}>
                    <option value="">All Departments</option>
                    {uniqueDepts.map(d => <option key={d} value={d}>{d}</option>)}
                  </select>
                </div>
                <div className="input-group" style={{ marginBottom: 0 }}>
                  <label>Employment Type</label>
                  <select className="select" value={jobFilters.employment_type} onChange={e => setJobFilters({ ...jobFilters, employment_type: e.target.value })}>
                    <option value="">All Types</option>
                    {uniqueTypes.map(t => <option key={t} value={t}>{t}</option>)}
                  </select>
                </div>
                <div className="input-group" style={{ marginBottom: 0 }}>
                  <label>Location</label>
                  <input className="input" placeholder="Search location..." value={jobFilters.location}
                    onChange={e => setJobFilters({ ...jobFilters, location: e.target.value })} />
                </div>
              </div>
            </div>

            {/* Job Cards Grid */}
            {filtered.length === 0 ? (
              <div className="card" style={{ padding: 60, textAlign: 'center', color: 'var(--text-tertiary)' }}>No jobs match your filters</div>
            ) : (
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(340px, 1fr))', gap: 18 }}>
                {filtered.map(j => {
                  const pipeline = jobPipelines[j.id] || { total: 0, stages: {} }
                  const stageList = [
                    { key: 'applied', label: 'Applied', color: '#3b82f6' },
                    { key: 'screening', label: 'AI Screened', color: '#8b5cf6' },
                    { key: 'interview', label: 'Interviewing', color: '#2b7cba' },
                    { key: 'hired', label: 'Offered/Hired', color: '#22c55e' },
                  ]
                  return (
                    <div key={j.id} className="card" style={{ padding: 0, display: 'flex', flexDirection: 'column' }}>
                      {/* Card Header */}
                      <div style={{ padding: '20px 22px 0' }}>
                        <div className="flex-between" style={{ marginBottom: 4 }}>
                          <h3 style={{ fontSize: '1.05rem', margin: 0 }}>{j.title}</h3>
                          <span className={`badge ${j.status === 'active' ? 'badge-green' : j.status === 'closed' ? 'badge-neutral' : 'badge-amber'}`}>
                            {j.status === 'active' ? 'Open' : j.status === 'closed' ? 'Closed' : j.status}
                          </span>
                        </div>
                        <div style={{ fontSize: '0.78rem', color: 'var(--text-tertiary)', fontFamily: 'var(--font-mono)', marginBottom: 16 }}>
                          REQ-{j.id?.slice(0, 8)?.toUpperCase() || '000'}
                        </div>

                        {/* Details */}
                        <div style={{ display: 'grid', gridTemplateColumns: '120px 1fr', gap: '8px 12px', fontSize: '0.88rem', marginBottom: 18 }}>
                          <span style={{ color: 'var(--text-tertiary)', display: 'flex', alignItems: 'center', gap: 6 }}>
                            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>
                            Department:
                          </span>
                          <span style={{ fontWeight: 500 }}>{j.department}</span>

                          <span style={{ color: 'var(--text-tertiary)', display: 'flex', alignItems: 'center', gap: 6 }}>
                            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>
                            Days Open:
                          </span>
                          <span style={{ fontWeight: 500 }}>{daysOpen(j.created_at)} days</span>

                          <span style={{ color: 'var(--text-tertiary)', display: 'flex', alignItems: 'center', gap: 6 }}>
                            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z"/><circle cx="12" cy="10" r="3"/></svg>
                            Location:
                          </span>
                          <span style={{ fontWeight: 500 }}>{j.location}</span>

                          <span style={{ color: 'var(--text-tertiary)', display: 'flex', alignItems: 'center', gap: 6 }}>
                            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect x="2" y="7" width="20" height="14" rx="2" ry="2"/><path d="M16 21V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v16"/></svg>
                            Type:
                          </span>
                          <span style={{ fontWeight: 500 }}>{j.employment_type}</span>
                        </div>
                      </div>

                      {/* Pipeline by Stage */}
                      <div style={{ padding: '0 22px 16px', flex: 1 }}>
                        <div style={{ fontWeight: 700, fontSize: '0.82rem', marginBottom: 10, fontFamily: 'var(--font-display)' }}>Pipeline by Stage</div>
                        {stageList.map(s => (
                          <div key={s.key} className="flex-between" style={{ fontSize: '0.84rem', padding: '3px 0' }}>
                            <span style={{ color: s.color, fontWeight: 500 }}>{s.label}</span>
                            <span style={{ fontFamily: 'var(--font-mono)', fontWeight: 600, color: 'var(--text-primary)' }}>
                              {pipeline.stages[s.key] || 0}
                            </span>
                          </div>
                        ))}
                      </div>

                      {/* Card Footer */}
                      <div style={{
                        padding: '12px 22px', borderTop: '1px solid var(--border-subtle)',
                        display: 'flex', gap: 10, alignItems: 'center'
                      }}>
                        <button className="btn btn-primary btn-sm" style={{ flex: 1, justifyContent: 'center', borderRadius: 8 }}
                          onClick={() => setViewJob(j)}>
                          Open Job
                        </button>
                        <button className="btn btn-secondary btn-sm" style={{ flex: 1, justifyContent: 'center', borderRadius: 8 }}
                          onClick={() => openCreateTest(j.id)} title="Create an assessment for this job">
                          <ClipboardList size={14} /> Create Test
                        </button>
                      </div>
                    </div>
                  )
                })}
              </div>
            )}
          </div>
        )
      })()}

      {tab === 'pipeline' && (() => {
        const stageIcon = (icon) => {
          if (icon === 'file')      return <FileText size={17} />
          if (icon === 'users')     return <Users size={17} />
          if (icon === 'clipboard') return <ClipboardList size={17} />
          if (icon === 'mic')       return <Mic size={17} />
          if (icon === 'usercheck') return <UserCheck size={17} />
          if (icon === 'award')     return <Award size={17} />
        }

        const expBucket = (yrs) => {
          if (yrs === null || yrs === undefined) return null
          if (yrs <= 1)  return '0–1 Yrs'
          if (yrs <= 3)  return '1–3 Yrs'
          if (yrs <= 5)  return '3–5 Yrs'
          if (yrs <= 7)  return '5–7 Yrs'
          return '7+ Yrs'
        }

        const getInitials = (name) => {
          if (!name) return '?'
          const parts = name.trim().split(' ').filter(Boolean)
          if (parts.length === 1) return parts[0].substring(0, 2).toUpperCase()
          return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase()
        }

        const AVATAR_COLORS = ['#6366f1', '#8b5cf6', '#ec4899', '#06b6d4', '#2b7cba', '#10b981', '#f59e0b']
        const avatarColor = (name) => AVATAR_COLORS[(name || '').charCodeAt(0) % AVATAR_COLORS.length]

        // Deduplicate by candidate_id — safety net against any residual duplication
        const allCandidates = Object.values(pipelineCandidates).flat().filter(
          (c, idx, arr) => arr.findIndex(x => x.candidate_id === c.candidate_id) === idx
        )
        const uniqueJobs = [...new Set(allCandidates.map(c => c.job_title).filter(Boolean))].sort()
        const EXP_RANGES = ['0–1 Yrs', '1–3 Yrs', '3–5 Yrs', '5–7 Yrs', '7+ Yrs']

        let filtered = pipelineStageFilter
          ? (pipelineCandidates[pipelineStageFilter] || [])
          : allCandidates
        if (pipelineSearch) {
          const q = pipelineSearch.toLowerCase()
          filtered = filtered.filter(c =>
            c.name?.toLowerCase().includes(q) ||
            c.email?.toLowerCase().includes(q) ||
            c.phone?.toLowerCase().includes(q) ||
            (c.extracted_skills || []).some(s => s.toLowerCase().includes(q)) ||
            (c.matched_skills || []).some(s => s.toLowerCase().includes(q))
          )
        }
        if (pipelineJobFilter) filtered = filtered.filter(c => c.job_title === pipelineJobFilter)
        if (pipelineExpFilter) filtered = filtered.filter(c => expBucket(c.experience_years) === pipelineExpFilter)

        const thSt = { padding: '11px 20px', textAlign: 'left', fontSize: '0.7rem', fontWeight: 700, color: 'var(--text-tertiary)', textTransform: 'uppercase', letterSpacing: '0.07em', fontFamily: 'var(--font-display)', whiteSpace: 'nowrap' }
        const hasFilter = pipelineStageFilter || pipelineSearch || pipelineJobFilter || pipelineExpFilter

        return (
          <div>
            {/* Header */}
            <div className="flex-between mb-24">
              <div>
                <h3 style={{ fontSize: '1.2rem' }}>Hiring Pipeline</h3>
                <span className="text-sm text-muted">
                  {jobsLoading ? 'Loading…' : `${allCandidates.length} candidates across all jobs`}
                </span>
              </div>
            </div>

            {/* Stage summary cards */}
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(6, 1fr)', gap: 14, marginBottom: 24 }}>
              {PIPELINE_STAGES.map(stage => {
                const count = (pipelineCandidates[stage.key] || []).length
                const isActive = pipelineStageFilter === stage.key
                return (
                  <div
                    key={stage.key}
                    onClick={() => setPipelineStageFilter(isActive ? null : stage.key)}
                    style={{
                      background: 'var(--bg-elevated)',
                      border: `1.5px solid ${isActive ? stage.color : 'var(--border-subtle)'}`,
                      borderRadius: 14, padding: '18px 18px 16px',
                      cursor: 'pointer', transition: 'all 0.18s',
                      boxShadow: isActive ? `0 0 0 3px ${stage.color}20` : '0 1px 3px rgba(0,0,0,0.04)',
                    }}
                  >
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
                      <div>
                        <div style={{ fontSize: '2rem', fontWeight: 800, color: 'var(--text-primary)', lineHeight: 1, fontFamily: 'var(--font-display)' }}>
                          {jobsLoading ? '–' : count}
                        </div>
                        <div style={{ fontSize: '0.65rem', fontWeight: 700, color: 'var(--text-tertiary)', textTransform: 'uppercase', letterSpacing: '0.07em', marginTop: 7 }}>
                          {stage.label}
                        </div>
                      </div>
                      <div style={{ width: 36, height: 36, borderRadius: 10, background: `${stage.color}18`, display: 'grid', placeItems: 'center', color: stage.color, flexShrink: 0 }}>
                        {stageIcon(stage.icon)}
                      </div>
                    </div>
                  </div>
                )
              })}
            </div>

            {/* Search + Filters */}
            <div className="card" style={{ padding: '14px 18px', marginBottom: 16 }}>
              <div style={{ display: 'flex', gap: 12, alignItems: 'center' }}>
                <div style={{ flex: 1, display: 'flex', alignItems: 'center', gap: 8, background: 'var(--surface-input)', border: '1px solid var(--border-subtle)', borderRadius: 10, padding: '0 14px' }}>
                  <Search size={14} color="var(--text-tertiary)" />
                  <input
                    type="text"
                    placeholder="Search by name, email, phone, or skill..."
                    value={pipelineSearch}
                    onChange={e => setPipelineSearch(e.target.value)}
                    style={{ border: 'none', background: 'none', padding: '9px 0', fontSize: '0.84rem', flex: 1, outline: 'none', color: 'var(--text-primary)' }}
                  />
                  {pipelineSearch && <X size={14} color="var(--text-tertiary)" style={{ cursor: 'pointer', flexShrink: 0 }} onClick={() => setPipelineSearch('')} />}
                </div>
                <select className="select" value={pipelineJobFilter} onChange={e => setPipelineJobFilter(e.target.value)} style={{ minWidth: 170 }}>
                  <option value="">All Jobs</option>
                  {uniqueJobs.map(j => <option key={j} value={j}>{j}</option>)}
                </select>
                <select className="select" value={pipelineExpFilter} onChange={e => setPipelineExpFilter(e.target.value)} style={{ minWidth: 140 }}>
                  <option value="">All Experience</option>
                  {EXP_RANGES.map(r => <option key={r} value={r}>{r}</option>)}
                </select>
              </div>

              {/* Active filter chips */}
              {hasFilter && (
                <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginTop: 12, flexWrap: 'wrap' }}>
                  {pipelineStageFilter && (() => {
                    const s = PIPELINE_STAGES.find(x => x.key === pipelineStageFilter)
                    return (
                      <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5, padding: '3px 11px', borderRadius: 20, background: `${s.color}18`, color: s.color, fontSize: '0.76rem', fontWeight: 700, cursor: 'pointer' }}
                        onClick={() => setPipelineStageFilter(null)}>
                        {s.label}: {(pipelineCandidates[pipelineStageFilter] || []).length} <X size={11} />
                      </span>
                    )
                  })()}
                  {pipelineExpFilter && (
                    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5, padding: '3px 11px', borderRadius: 20, background: '#f59e0b18', color: '#f59e0b', fontSize: '0.76rem', fontWeight: 700, cursor: 'pointer' }}
                      onClick={() => setPipelineExpFilter('')}>
                      {pipelineExpFilter} <X size={11} />
                    </span>
                  )}
                  {pipelineJobFilter && (
                    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5, padding: '3px 11px', borderRadius: 20, background: '#06b6d418', color: '#06b6d4', fontSize: '0.76rem', fontWeight: 700, cursor: 'pointer' }}
                      onClick={() => setPipelineJobFilter('')}>
                      {pipelineJobFilter} <X size={11} />
                    </span>
                  )}
                  <span style={{ fontSize: '0.8rem', color: 'var(--text-tertiary)' }}>
                    {filtered.length} matching candidate{filtered.length !== 1 ? 's' : ''}
                  </span>
                </div>
              )}
            </div>

            {/* Candidate Table */}
            {jobsLoading ? (
              <div className="card" style={{ padding: 60, textAlign: 'center', color: 'var(--text-tertiary)' }}>Loading pipeline data…</div>
            ) : filtered.length === 0 ? (
              <div className="card" style={{ padding: 60, textAlign: 'center', color: 'var(--text-tertiary)' }}>No candidates match your filters</div>
            ) : (
              <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
                <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                  <thead>
                    <tr style={{ background: 'var(--bg-hover)', borderBottom: '1px solid var(--border-subtle)' }}>
                      <th style={thSt}>Candidate</th>
                      <th style={thSt}>Role</th>
                      <th style={{ ...thSt, textAlign: 'center' }}>ATS Score</th>
                      <th style={thSt}>Experience</th>
                      <th style={thSt}>Stage</th>
                    </tr>
                  </thead>
                  <tbody>
                    {filtered.map((c, i) => {
                      const score = Math.round(c.ats_score || 0)
                      const scoreColor = score >= 70 ? '#22c55e' : score >= 50 ? '#f59e0b' : '#ef4444'
                      const stage = PIPELINE_STAGES.find(s => s.key === normalizeStage(c.current_stage))
                      return (
                        <tr key={c.candidate_id || i}
                          style={{ borderBottom: '1px solid var(--border-subtle)', transition: 'background 0.12s', cursor: 'pointer' }}
                          onClick={() => c.candidate_id && openStageTracker(c.candidate_id)}
                          onMouseEnter={e => e.currentTarget.style.background = 'var(--bg-hover)'}
                          onMouseLeave={e => e.currentTarget.style.background = ''}>
                          <td style={{ padding: '13px 20px' }}>
                            <div style={{ display: 'flex', alignItems: 'center', gap: 11 }}>
                              <div style={{
                                width: 36, height: 36, borderRadius: '50%', flexShrink: 0,
                                background: avatarColor(c.name), color: '#fff',
                                display: 'grid', placeItems: 'center',
                                fontSize: '0.75rem', fontWeight: 700, fontFamily: 'var(--font-display)',
                              }}>
                                {getInitials(c.name)}
                              </div>
                              <div>
                                <div style={{ fontWeight: 600, fontSize: '0.88rem', color: 'var(--text-primary)' }}>{c.name}</div>
                                <div style={{ fontSize: '0.74rem', color: 'var(--text-tertiary)', marginTop: 1 }}>{c.email}</div>
                              </div>
                            </div>
                          </td>
                          <td style={{ padding: '13px 20px', fontSize: '0.86rem', color: 'var(--text-secondary)' }}>{c.job_title}</td>
                          <td style={{ padding: '13px 20px', textAlign: 'center' }}>
                            <span style={{
                              display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
                              width: 38, height: 38, borderRadius: '50%',
                              background: `${scoreColor}20`, color: scoreColor,
                              fontSize: '0.8rem', fontWeight: 800, fontFamily: 'var(--font-mono)',
                              border: `2px solid ${scoreColor}40`,
                            }}>
                              {score}
                            </span>
                          </td>
                          <td style={{ padding: '13px 20px', fontSize: '0.85rem', color: 'var(--text-secondary)' }}>
                            {expBucket(c.experience_years) || '—'}
                          </td>
                          <td style={{ padding: '13px 20px' }}>
                            {stage && (
                              <span style={{
                                padding: '3px 11px', borderRadius: 20, fontSize: '0.73rem', fontWeight: 600,
                                background: `${stage.color}18`, color: stage.color, whiteSpace: 'nowrap',
                              }}>
                                {stage.label}
                              </span>
                            )}
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )
      })()}

      {tab === 'interviews' && (() => {
        const scheduledInterviews = interviews.filter(iv => iv.candidate_id && iv.is_scheduled_interview)
        const renderInterviewCard = (iv, i) => {
          const isOnline = iv.mode === 'online'
          const statusColor = iv.status === 'COMPLETED' ? '#15803d' : iv.status === 'SCHEDULED' ? '#2563eb' : iv.status === 'CANCELLED' ? '#dc2626' : '#b45309'
          const statusBg = iv.status === 'COMPLETED' ? '#ecfdf5' : iv.status === 'SCHEDULED' ? '#eff6ff' : iv.status === 'CANCELLED' ? '#fef2f2' : '#fef3eb'
          return (
            <div key={iv.id || i} className="card" style={{ padding: 0, overflow: 'hidden' }}>
              <div style={{ display: 'flex' }}>
                <div style={{ width: 5, background: statusColor, flexShrink: 0 }} />
                <div style={{ flex: 1, padding: '20px 24px' }}>
                  <div className="flex-between" style={{ marginBottom: 14 }}>
                    <div>
                      <div style={{ fontWeight: 700, fontSize: '1.05rem', marginBottom: 2 }}>{iv.candidate_name}</div>
                      <div style={{ fontSize: '0.84rem', color: 'var(--text-secondary)' }}>{iv.job_title} — {iv.interview_type}</div>
                    </div>
                    <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                      <span style={{ padding: '4px 10px', borderRadius: 6, fontSize: '0.75rem', fontWeight: 600, background: isOnline ? '#dbeafe' : '#fef3c7', color: isOnline ? '#1d4ed8' : '#92400e' }}>
                        {isOnline ? '🖥 Online' : '🏢 Offline'}
                      </span>
                      <span style={{ padding: '5px 14px', borderRadius: 8, fontSize: '0.76rem', fontWeight: 700, color: statusColor, background: statusBg, border: `1px solid ${statusColor}25`, textTransform: 'uppercase' }}>
                        {iv.status}
                      </span>
                    </div>
                  </div>
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr 1fr', gap: 12, marginBottom: 14 }}>
                    <div style={{ padding: '8px 12px', background: '#f9fafb', borderRadius: 8 }}>
                      <div style={{ fontSize: '0.65rem', fontWeight: 700, textTransform: 'uppercase', color: '#9ca3af', marginBottom: 2 }}>Email</div>
                      <div style={{ fontSize: '0.82rem', wordBreak: 'break-all' }}>{iv.candidate_email || '—'}</div>
                    </div>
                    <div style={{ padding: '8px 12px', background: '#f9fafb', borderRadius: 8 }}>
                      <div style={{ fontSize: '0.65rem', fontWeight: 700, textTransform: 'uppercase', color: '#9ca3af', marginBottom: 2 }}>Phone</div>
                      <div style={{ fontSize: '0.82rem', fontFamily: 'var(--font-mono)' }}>{iv.candidate_phone || '—'}</div>
                    </div>
                    <div style={{ padding: '8px 12px', background: '#f9fafb', borderRadius: 8 }}>
                      <div style={{ fontSize: '0.65rem', fontWeight: 700, textTransform: 'uppercase', color: '#9ca3af', marginBottom: 2 }}>Interviewer</div>
                      <div style={{ fontSize: '0.82rem' }}>{iv.interviewer_name}</div>
                      <div style={{ fontSize: '0.72rem', color: '#9ca3af' }}>{iv.interviewer_email}</div>
                    </div>
                    <div style={{ padding: '8px 12px', background: '#f9fafb', borderRadius: 8 }}>
                      <div style={{ fontSize: '0.65rem', fontWeight: 700, textTransform: 'uppercase', color: '#9ca3af', marginBottom: 2 }}>Schedule</div>
                      <div style={{ fontSize: '0.82rem', fontFamily: 'var(--font-mono)' }}>{iv.scheduled_datetime ? new Date(iv.scheduled_datetime).toLocaleString() : '—'}</div>
                      <div style={{ fontSize: '0.72rem', color: '#9ca3af' }}>{iv.duration_minutes} min</div>
                    </div>
                  </div>
                  <div style={{ display: 'flex', gap: 12, alignItems: 'center' }}>
                    {isOnline && iv.meeting_link && (
                      <a href={iv.meeting_link} target="_blank" rel="noreferrer" style={{ padding: '6px 14px', borderRadius: 8, background: '#2563eb', color: '#fff', fontSize: '0.8rem', fontWeight: 600, textDecoration: 'none' }}>
                        Join Meeting
                      </a>
                    )}
                    {!isOnline && iv.location && (
                      <span style={{ padding: '6px 14px', borderRadius: 8, background: '#f3f4f6', color: '#374151', fontSize: '0.8rem' }}>
                        📍 {iv.location}
                      </span>
                    )}
                    {iv.resume_path && (
                      <a href={`/${iv.resume_path}`} target="_blank" rel="noreferrer" style={{ padding: '6px 14px', borderRadius: 8, background: '#f0fdf4', color: '#15803d', fontSize: '0.8rem', fontWeight: 600, textDecoration: 'none', border: '1px solid #bbf7d0' }}>
                        📄 View Resume
                      </a>
                    )}
                    {/* Give / edit interview feedback — works for online AND offline */}
                    {(() => {
                      const fb = feedbackData.find(f => f.interview_id === iv.id)
                      return (
                        <button type="button" onClick={() => openFeedback(iv)}
                          style={{ padding: '6px 14px', borderRadius: 8, background: fb ? '#f5f3ff' : '#7c3aed', color: fb ? '#7c3aed' : '#fff', fontSize: '0.8rem', fontWeight: 600, border: fb ? '1px solid #ddd6fe' : 'none', cursor: 'pointer' }}>
                          {fb ? `Edit Feedback · ${Math.round(fb.overall_score || 0)}%` : '📝 Give Feedback'}
                        </button>
                      )
                    })()}
                  </div>
                </div>
              </div>
            </div>
          )
        }
        return (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 28 }}>
            {/* Only interviews scheduled via the explicit "Schedule Interview" action
                in Test Results are shown here. Auto-cloned candidate tests are tracked
                in the Test Results tab, not surfaced as "scheduled". */}

            {/* ── Scheduled Interviews ── */}
            <div>
              <div className="flex-between" style={{ marginBottom: 16 }}>
                <div>
                  <h3 style={{ fontSize: '1.1rem', display: 'flex', alignItems: 'center', gap: 8 }}>
                    <span style={{ width: 8, height: 8, borderRadius: '50%', background: '#2563eb', display: 'inline-block' }} />
                    Scheduled Interviews
                  </h3>
                  <span className="text-sm text-muted">{scheduledInterviews.length} interview{scheduledInterviews.length !== 1 ? 's' : ''} scheduled</span>
                </div>
              </div>
              {scheduledInterviews.length === 0 ? (
                <div className="card" style={{ padding: 40, textAlign: 'center', color: 'var(--text-tertiary)' }}>No interviews scheduled yet. Select candidates from Test Results to schedule.</div>
              ) : (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
                  {scheduledInterviews.map((iv, i) => renderInterviewCard(iv, i))}
                </div>
              )}
            </div>
          </div>
        )
      })()}

      {tab === 'offers' && (() => {
        const stageMeta = {
          offer:            { label: 'Offer Letter',       color: '#15803d', bg: '#dcfce7', border: '#86efac' },
          rejection:        { label: 'Rejection',          color: '#dc2626', bg: '#fef2f2', border: '#fca5a5' },
          interview_invite: { label: 'Interview Invite',   color: '#2563eb', bg: '#eff6ff', border: '#93c5fd' },
          welcome:          { label: 'Welcome Onboard',    color: '#7c3aed', bg: '#f5f3ff', border: '#c4b5fd' },
          custom:           { label: 'Custom',             color: '#6b7280', bg: '#f9fafb', border: '#d1d5db' },
        }
        const applyTemplateVars = (text, o) => text
          .replace(/\{\{candidate_name\}\}/g, o?.candidate_name || 'Candidate')
          .replace(/\{\{job_title\}\}/g, o?.job_title || 'the position')
          .replace(/\{\{salary\}\}/g, Number(o?.salary || 0).toLocaleString())
          .replace(/\{\{bonus\}\}/g, Number(o?.bonus || 0).toLocaleString())
          .replace(/\{\{start_date\}\}/g, o?.start_date || 'TBD')
          .replace(/\{\{expiry_date\}\}/g, o?.expiry_date || 'TBD')
          .replace(/\{\{offer_type\}\}/g, (o?.offer_type || '').replace(/_/g, ' '))

        const filteredTemplates = templateStageFilter === 'all' ? mailTemplates : mailTemplates.filter(t => t.stage === templateStageFilter)

        return (
        <div>
          <div className="flex-between mb-24">
            <div>
              <h3 style={{ fontSize: '1.2rem' }}>Offer Letters</h3>
              <span className="text-sm text-muted">{offers.length} offers — click Send Mail to review and send</span>
            </div>
            <button
              className="btn btn-primary btn-sm"
              onClick={() => { setComposeMail({ to: '', subject: '', body: '' }); setShowComposeMail(true) }}
              style={{ display: 'flex', alignItems: 'center', gap: 7 }}
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect x="2" y="4" width="20" height="16" rx="2"/><path d="m22 7-8.97 5.7a1.94 1.94 0 0 1-2.06 0L2 7"/></svg>
              Send Mail
            </button>
          </div>

          {/* ── Mail Template Manager ── */}
          <div className="card" style={{ marginBottom: 28, padding: 0, overflow: 'hidden', border: '1.5px solid var(--accent-primary)' }}>
              {/* Header */}
              <div style={{ padding: '16px 22px', background: 'var(--accent-primary)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <div>
                  <div style={{ fontWeight: 700, fontSize: '1rem', color: '#fff' }}>Mail Templates</div>
                  <div style={{ fontSize: '0.78rem', color: 'rgba(255,255,255,0.75)', marginTop: 2 }}>
                    Create reusable templates for different hiring stages. Use <code style={{ background: 'rgba(255,255,255,0.15)', padding: '1px 5px', borderRadius: 4, fontSize: '0.75rem' }}>{'{{candidate_name}}'}</code>, <code style={{ background: 'rgba(255,255,255,0.15)', padding: '1px 5px', borderRadius: 4, fontSize: '0.75rem' }}>{'{{job_title}}'}</code>, <code style={{ background: 'rgba(255,255,255,0.15)', padding: '1px 5px', borderRadius: 4, fontSize: '0.75rem' }}>{'{{salary}}'}</code>, <code style={{ background: 'rgba(255,255,255,0.15)', padding: '1px 5px', borderRadius: 4, fontSize: '0.75rem' }}>{'{{start_date}}'}</code> as variables.
                  </div>
                </div>
                <button
                  onClick={() => { setEditingTemplate('new'); setTemplateForm({ id: null, stage: 'offer', name: '', subject: '', body: '' }) }}
                  style={{ background: '#fff', color: 'var(--accent-primary)', border: 'none', padding: '7px 16px', borderRadius: 8, fontWeight: 700, fontSize: '0.83rem', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 6 }}
                >
                  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
                  Add Template
                </button>
              </div>

              {/* Stage filter tabs */}
              <div style={{ padding: '10px 22px', borderBottom: '1px solid var(--border-subtle)', display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                {['all', 'offer', 'rejection', 'interview_invite', 'welcome', 'custom'].map(s => (
                  <button key={s} onClick={() => setTemplateStageFilter(s)} style={{ padding: '4px 14px', borderRadius: 20, fontSize: '0.78rem', fontWeight: 600, border: '1px solid', cursor: 'pointer', transition: '0.15s', background: templateStageFilter === s ? (stageMeta[s]?.bg || '#f0fdf4') : '#fff', color: templateStageFilter === s ? (stageMeta[s]?.color || 'var(--accent-primary)') : '#6b7280', borderColor: templateStageFilter === s ? (stageMeta[s]?.border || 'var(--accent-primary)') : '#e5e7eb' }}>
                    {s === 'all' ? `All (${mailTemplates.length})` : `${stageMeta[s]?.label || s} (${mailTemplates.filter(t => t.stage === s).length})`}
                  </button>
                ))}
              </div>

              {/* Add / Edit form */}
              {editingTemplate && (
                <div style={{ padding: '18px 22px', background: '#f8faf9', borderBottom: '1px solid var(--border-subtle)' }}>
                  <div style={{ fontWeight: 700, fontSize: '0.9rem', marginBottom: 14, color: 'var(--text-primary)' }}>
                    {editingTemplate === 'new' ? 'New Template' : 'Edit Template'}
                  </div>
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginBottom: 12 }}>
                    <div>
                      <label style={{ fontSize: '0.75rem', fontWeight: 600, color: '#374151', display: 'block', marginBottom: 4 }}>Template Name *</label>
                      <input className="input" placeholder="e.g. Formal Offer Letter" value={templateForm.name} onChange={e => setTemplateForm(f => ({ ...f, name: e.target.value }))} style={{ fontSize: '0.87rem' }} />
                    </div>
                    <div>
                      <label style={{ fontSize: '0.75rem', fontWeight: 600, color: '#374151', display: 'block', marginBottom: 4 }}>Stage</label>
                      <select className="input" value={templateForm.stage} onChange={e => setTemplateForm(f => ({ ...f, stage: e.target.value }))} style={{ fontSize: '0.87rem' }}>
                        <option value="offer">Offer Letter</option>
                        <option value="rejection">Rejection</option>
                        <option value="interview_invite">Interview Invite</option>
                        <option value="welcome">Welcome Onboard</option>
                        <option value="custom">Custom</option>
                      </select>
                    </div>
                  </div>
                  <div style={{ marginBottom: 12 }}>
                    <label style={{ fontSize: '0.75rem', fontWeight: 600, color: '#374151', display: 'block', marginBottom: 4 }}>Subject Line *</label>
                    <input className="input" placeholder="e.g. Offer Letter – {{job_title}}" value={templateForm.subject} onChange={e => setTemplateForm(f => ({ ...f, subject: e.target.value }))} style={{ fontSize: '0.87rem' }} />
                  </div>
                  <div style={{ marginBottom: 14 }}>
                    <label style={{ fontSize: '0.75rem', fontWeight: 600, color: '#374151', display: 'block', marginBottom: 4 }}>Email Body *</label>
                    <textarea className="textarea" rows={8} placeholder="Write your template here. Use {{candidate_name}}, {{job_title}}, {{salary}}, {{bonus}}, {{start_date}}, {{expiry_date}}, {{offer_type}} as variables." value={templateForm.body} onChange={e => setTemplateForm(f => ({ ...f, body: e.target.value }))} style={{ fontSize: '0.84rem', lineHeight: 1.7 }} />
                  </div>
                  <div style={{ display: 'flex', gap: 10 }}>
                    <button
                      onClick={saveTemplate}
                      style={{ background: 'var(--accent-primary)', color: '#fff', border: 'none', padding: '7px 18px', borderRadius: 7, fontWeight: 600, fontSize: '0.84rem', cursor: 'pointer' }}
                    >
                      {editingTemplate === 'new' ? 'Save Template' : 'Update Template'}
                    </button>
                    <button onClick={() => setEditingTemplate(null)} style={{ background: '#f3f4f6', color: '#374151', border: 'none', padding: '7px 18px', borderRadius: 7, fontWeight: 600, fontSize: '0.84rem', cursor: 'pointer' }}>Cancel</button>
                  </div>
                </div>
              )}

              {/* Templates grid */}
              <div style={{ padding: '18px 22px' }}>
                {templatesError && (
                  <div style={{ padding: '10px 14px', marginBottom: 12, borderRadius: 8, background: '#fef2f2', border: '1px solid #fca5a5', color: '#dc2626', fontSize: '0.84rem' }}>
                    {templatesError} <button onClick={loadMailTemplates} style={{ marginLeft: 8, background: 'none', border: 'none', color: '#dc2626', fontWeight: 700, cursor: 'pointer', textDecoration: 'underline' }}>Retry</button>
                  </div>
                )}
                {!templatesError && filteredTemplates.length === 0 && (
                  <div style={{ padding: '30px 0', textAlign: 'center', color: '#9ca3af', fontSize: '0.9rem' }}>No templates for this stage yet.</div>
                )}
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))', gap: 14 }}>
                  {filteredTemplates.map(tpl => {
                    const sm = stageMeta[tpl.stage] || stageMeta.custom
                    return (
                      <div key={tpl.id} style={{ border: `1px solid ${sm.border}`, borderRadius: 10, overflow: 'hidden', background: '#fff', display: 'flex', flexDirection: 'column' }}>
                        <div style={{ padding: '12px 14px', background: sm.bg, borderBottom: `1px solid ${sm.border}` }}>
                          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 }}>
                            <span style={{ fontWeight: 700, fontSize: '0.9rem', color: 'var(--text-primary)' }}>{tpl.name}</span>
                            <span style={{ padding: '2px 9px', borderRadius: 12, fontSize: '0.68rem', fontWeight: 700, background: sm.bg, color: sm.color, border: `1px solid ${sm.border}` }}>{sm.label}</span>
                          </div>
                          <div style={{ fontSize: '0.78rem', color: '#6b7280', fontStyle: 'italic', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{tpl.subject}</div>
                        </div>
                        <div style={{ padding: '10px 14px', fontSize: '0.78rem', color: '#6b7280', lineHeight: 1.6, flex: 1, maxHeight: 72, overflow: 'hidden', maskImage: 'linear-gradient(to bottom, #000 60%, transparent)' }}>
                          {tpl.body}
                        </div>
                        <div style={{ padding: '10px 14px', borderTop: `1px solid ${sm.border}`, display: 'flex', gap: 8 }}>
                          <button
                            onClick={() => { setEditingTemplate(tpl.id); setTemplateForm({ ...tpl }) }}
                            style={{ flex: 1, padding: '5px 0', borderRadius: 6, border: '1px solid #e5e7eb', background: '#fff', fontSize: '0.78rem', fontWeight: 600, cursor: 'pointer', color: '#374151' }}
                          >Edit</button>
                          <button
                            onClick={() => deleteTemplate(tpl)}
                            style={{ padding: '5px 12px', borderRadius: 6, border: '1px solid #fca5a5', background: '#fff', fontSize: '0.78rem', fontWeight: 600, cursor: 'pointer', color: '#dc2626' }}
                          >Delete</button>
                        </div>
                      </div>
                    )
                  })}
                </div>
              </div>
            </div>

          {offers.length === 0 ? (
            <div className="card" style={{ padding: 60, textAlign: 'center', color: 'var(--text-tertiary)' }}>
              No offers created yet. Go to Interview Feedback to select candidates and create offers.
            </div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
              {offers.map((o, i) => {
                const statusColor = o.status === 'accepted' ? '#15803d' : o.status === 'rejected' ? '#dc2626' : o.status === 'sent' ? '#2563eb' : '#b45309'
                const statusBg = o.status === 'accepted' ? '#ecfdf5' : o.status === 'rejected' ? '#fef2f2' : o.status === 'sent' ? '#eff6ff' : '#fef3eb'
                return (
                  <div key={o.id || i} className="card" style={{ padding: 0, overflow: 'hidden' }}>
                    <div style={{ display: 'flex' }}>
                      {/* Left: status strip */}
                      <div style={{ width: 5, background: statusColor, flexShrink: 0 }} />

                      {/* Main content */}
                      <div style={{ flex: 1, padding: '20px 24px' }}>
                        <div className="flex-between" style={{ marginBottom: 14 }}>
                          <div>
                            <div style={{ fontWeight: 700, fontSize: '1.05rem', marginBottom: 2 }}>{o.candidate_name}</div>
                            <div style={{ fontSize: '0.84rem', color: 'var(--text-secondary)' }}>{o.job_title}</div>
                          </div>
                          <span style={{ padding: '5px 14px', borderRadius: 8, fontSize: '0.76rem', fontWeight: 700, color: statusColor, background: statusBg, border: `1px solid ${statusColor}25`, textTransform: 'uppercase', fontFamily: 'var(--font-display)', letterSpacing: '0.04em' }}>
                            {o.status}
                          </span>
                        </div>

                        {/* Contact + Offer details */}
                        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 16, marginBottom: 16 }}>
                          <div style={{ padding: '10px 14px', background: '#f9fafb', borderRadius: 8 }}>
                            <div style={{ fontSize: '0.68rem', fontWeight: 700, textTransform: 'uppercase', color: '#9ca3af', marginBottom: 3 }}>Email</div>
                            <div style={{ fontSize: '0.84rem', fontWeight: 500, wordBreak: 'break-all' }}>{o.candidate_email || o.candidate_name?.split(' ')[0]?.toLowerCase() + '@email.com'}</div>
                          </div>
                          <div style={{ padding: '10px 14px', background: '#f9fafb', borderRadius: 8 }}>
                            <div style={{ fontSize: '0.68rem', fontWeight: 700, textTransform: 'uppercase', color: '#9ca3af', marginBottom: 3 }}>Phone</div>
                            <div style={{ fontSize: '0.84rem', fontWeight: 500, fontFamily: 'var(--font-mono)' }}>{o.candidate_phone || '—'}</div>
                          </div>
                          <div style={{ padding: '10px 14px', background: '#f9fafb', borderRadius: 8 }}>
                            <div style={{ fontSize: '0.68rem', fontWeight: 700, textTransform: 'uppercase', color: '#9ca3af', marginBottom: 3 }}>Offer Type</div>
                            <div style={{ fontSize: '0.84rem', fontWeight: 500 }}>{(o.offer_type || '').replace(/_/g, ' ')}</div>
                          </div>
                        </div>

                        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr 1fr', gap: 12, marginBottom: 16 }}>
                          <div style={{ textAlign: 'center', padding: '8px 0' }}>
                            <div style={{ fontSize: '1.15rem', fontWeight: 800, color: '#15803d', fontFamily: 'var(--font-display)' }}>₹{Number(o.salary || 0).toLocaleString()}</div>
                            <div style={{ fontSize: '0.68rem', color: '#9ca3af', fontWeight: 600, textTransform: 'uppercase' }}>Salary</div>
                          </div>
                          <div style={{ textAlign: 'center', padding: '8px 0' }}>
                            <div style={{ fontSize: '1.15rem', fontWeight: 800, color: '#2563eb', fontFamily: 'var(--font-display)' }}>₹{Number(o.bonus || 0).toLocaleString()}</div>
                            <div style={{ fontSize: '0.68rem', color: '#9ca3af', fontWeight: 600, textTransform: 'uppercase' }}>Bonus</div>
                          </div>
                          <div style={{ textAlign: 'center', padding: '8px 0' }}>
                            <div style={{ fontSize: '0.88rem', fontWeight: 600, color: 'var(--text-primary)' }}>{o.start_date || '—'}</div>
                            <div style={{ fontSize: '0.68rem', color: '#9ca3af', fontWeight: 600, textTransform: 'uppercase' }}>Start Date</div>
                          </div>
                          <div style={{ textAlign: 'center', padding: '8px 0' }}>
                            <div style={{ fontSize: '0.88rem', fontWeight: 600, color: 'var(--text-primary)' }}>{o.expiry_date || '—'}</div>
                            <div style={{ fontSize: '0.68rem', color: '#9ca3af', fontWeight: 600, textTransform: 'uppercase' }}>Expires</div>
                          </div>
                        </div>

                        {/* Actions */}
                        <div className="flex-between">
                          <div style={{ fontSize: '0.76rem', color: '#9ca3af', fontFamily: 'var(--font-mono)' }}>
                            {o.sent_at ? `Sent: ${new Date(o.sent_at).toLocaleString()}` : 'Not sent yet'}
                          </div>
                          <button className="btn btn-primary btn-sm" onClick={() => {
                            const defaultTpl = mailTemplates.find(t => t.stage === 'offer') || null
                            setOfferMailPreview({
                              ...o,
                              emailTo: o.candidate_email || '',
                              emailSubject: defaultTpl ? applyTemplateVars(defaultTpl.subject, o) : `Offer Letter – ${o.job_title || 'the position'}`,
                              emailBody: defaultTpl ? applyTemplateVars(defaultTpl.body, o) : `Dear ${o.candidate_name || 'Candidate'},\n\nWe are pleased to extend an offer for ${o.job_title || 'the position'}.\n\nHireFlow Recruitment Team`,
                              _selectedTemplateId: defaultTpl?.id || null,
                            })
                          }}>
                            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect x="2" y="4" width="20" height="16" rx="2"/><path d="m22 7-8.97 5.7a1.94 1.94 0 0 1-2.06 0L2 7"/></svg>
                            Send Mail
                          </button>
                        </div>
                      </div>
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </div>
        )
      })()}

      {/* ═══════════ COMPOSE MAIL MODAL ═══════════ */}
      {showComposeMail && (
        <div className="modal-overlay" onClick={() => setShowComposeMail(false)} style={{ zIndex: 1100 }}>
          <div className="modal" onClick={e => e.stopPropagation()} style={{ maxWidth: 620, maxHeight: '90vh', overflow: 'auto' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 20, paddingBottom: 16, borderBottom: '2px solid var(--border-subtle)' }}>
              <h2 style={{ margin: 0 }}>Compose Mail</h2>
              <button onClick={() => setShowComposeMail(false)} style={{ background: 'none', border: 'none', cursor: 'pointer', color: '#9ca3af' }}>
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
              </button>
            </div>

            {/* Template selector */}
            <div style={{ marginBottom: 18 }}>
              <div style={{ fontSize: '0.72rem', fontWeight: 700, color: '#6b7280', marginBottom: 8, textTransform: 'uppercase', letterSpacing: '0.06em' }}>Use a Template</div>
              <div style={{ position: 'relative', display: 'inline-block' }}>
                <button
                  onClick={() => setComposeDropdownOpen(v => !v)}
                  style={{ display: 'flex', alignItems: 'center', gap: 7, padding: '7px 14px', borderRadius: 8, border: '1px solid var(--border-subtle)', background: '#f9fafb', color: '#374151', fontSize: '0.84rem', fontWeight: 600, cursor: 'pointer' }}
                >
                  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect x="2" y="4" width="20" height="16" rx="2"/><path d="m22 7-8.97 5.7a1.94 1.94 0 0 1-2.06 0L2 7"/></svg>
                  Select Template
                  <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" style={{ transform: composeDropdownOpen ? 'rotate(180deg)' : 'none', transition: '0.15s' }}><polyline points="6 9 12 15 18 9"/></svg>
                </button>
                {composeDropdownOpen && (
                  <div style={{ position: 'absolute', top: '110%', left: 0, background: '#fff', border: '1px solid #e5e7eb', borderRadius: 10, boxShadow: '0 8px 24px rgba(0,0,0,0.12)', zIndex: 50, minWidth: 260, overflow: 'hidden' }}>
                    {['offer', 'rejection', 'interview_invite', 'welcome', 'custom'].map(stage => {
                      const grpTpls = mailTemplates.filter(t => t.stage === stage)
                      if (!grpTpls.length) return null
                      const stageLabels = { offer: 'Offer Letter', rejection: 'Rejection', interview_invite: 'Interview Invite', welcome: 'Welcome Onboard', custom: 'Custom' }
                      const stageColors = { offer: '#15803d', rejection: '#dc2626', interview_invite: '#2563eb', welcome: '#7c3aed', custom: '#6b7280' }
                      return (
                        <div key={stage}>
                          <div style={{ padding: '6px 14px 4px', fontSize: '0.68rem', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.06em', color: stageColors[stage] || '#6b7280', background: '#f9fafb', borderBottom: '1px solid #f3f4f6' }}>
                            {stageLabels[stage] || stage}
                          </div>
                          {grpTpls.map(tpl => (
                            <button key={tpl.id}
                              onClick={() => { setComposeMail(prev => ({ ...prev, subject: tpl.subject, body: tpl.body })); setComposeDropdownOpen(false) }}
                              style={{ display: 'block', width: '100%', textAlign: 'left', padding: '9px 14px', border: 'none', background: 'none', cursor: 'pointer', fontSize: '0.83rem', color: '#1e293b', borderBottom: '1px solid #f3f4f6' }}
                              onMouseOver={e => e.currentTarget.style.background = '#f9fafb'}
                              onMouseOut={e => e.currentTarget.style.background = 'none'}
                            >
                              {tpl.name}
                            </button>
                          ))}
                        </div>
                      )
                    })}
                  </div>
                )}
              </div>
            </div>

            <MailFromPicker stepKey="custom_mail" label="Send from"
              value={composeMailboxId} onChange={setComposeMailboxId} />

            {/* To */}
            <div className="input-group" style={{ marginBottom: 14 }}>
              <label>To (Email)</label>
              <input className="input" type="email" placeholder="candidate@email.com" value={composeMail.to}
                onChange={e => setComposeMail(m => ({ ...m, to: e.target.value }))} />
            </div>

            {/* Subject */}
            <div className="input-group" style={{ marginBottom: 14 }}>
              <label>Subject</label>
              <input className="input" placeholder="Email subject..." value={composeMail.subject}
                onChange={e => setComposeMail(m => ({ ...m, subject: e.target.value }))} />
            </div>

            {/* Body */}
            <div className="input-group" style={{ marginBottom: 20 }}>
              <label>Message</label>
              <textarea className="textarea" rows={10} placeholder="Write your message here..." value={composeMail.body}
                onChange={e => setComposeMail(m => ({ ...m, body: e.target.value }))}
                style={{ fontSize: '0.88rem', lineHeight: 1.7 }} />
            </div>

            {/* Footer */}
            {composeError && (
              <div style={{ color: 'var(--danger, #dc2626)', fontSize: '0.85rem', marginBottom: 10 }}>{composeError}</div>
            )}
            <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end', borderTop: '1px solid var(--border-subtle)', paddingTop: 16 }}>
              <button className="btn btn-secondary" onClick={() => setShowComposeMail(false)}>Cancel</button>
              <button className="btn btn-primary" disabled={composeSending} onClick={handleSendComposeMail}>
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg>
                {composeSending ? 'Sending…' : 'Send Mail'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Offer Email Preview Overlay */}
      {offerMailPreview && (
        <div className="modal-overlay" onClick={() => setOfferMailPreview(null)} style={{ zIndex: 1100 }}>
          <div className="modal" onClick={e => e.stopPropagation()} style={{ maxWidth: 660, maxHeight: '90vh', overflow: 'auto' }}>
            <h2 style={{ margin: 0, marginBottom: 20, paddingBottom: 16, borderBottom: '2px solid var(--border-subtle)' }}>
              {offerMailPreview._editing ? 'Edit Offer Email' : 'Review Offer Email'}
            </h2>

            {/* Template cards */}
            {mailTemplates.length > 0 && (
              <div style={{ marginBottom: 20 }}>
                <div style={{ fontSize: '0.72rem', fontWeight: 700, color: '#6b7280', marginBottom: 10, textTransform: 'uppercase', letterSpacing: '0.06em' }}>
                  Templates
                </div>
                <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                  {mailTemplates.map(tpl => {
                    const stageMeta = {
                      offer:            { color: '#15803d', bg: '#f0fdf4', border: '#86efac', activeBg: '#dcfce7' },
                      rejection:        { color: '#dc2626', bg: '#fef2f2', border: '#fca5a5', activeBg: '#fee2e2' },
                      interview_invite: { color: '#2563eb', bg: '#eff6ff', border: '#93c5fd', activeBg: '#dbeafe' },
                      welcome:          { color: '#7c3aed', bg: '#f5f3ff', border: '#c4b5fd', activeBg: '#ede9fe' },
                      custom:           { color: '#6b7280', bg: '#f9fafb', border: '#d1d5db', activeBg: '#f3f4f6' },
                    }
                    const sm = stageMeta[tpl.stage] || stageMeta.custom
                    const isActive = offerMailPreview._selectedTemplateId === tpl.id
                    const applyTpl = (tpl) => setOfferMailPreview(prev => {
                      const sub = tpl.subject
                        .replace(/\{\{candidate_name\}\}/g, prev.candidate_name || 'Candidate')
                        .replace(/\{\{job_title\}\}/g, prev.job_title || 'the position')
                        .replace(/\{\{salary\}\}/g, Number(prev.salary || 0).toLocaleString())
                        .replace(/\{\{bonus\}\}/g, Number(prev.bonus || 0).toLocaleString())
                        .replace(/\{\{start_date\}\}/g, prev.start_date || 'TBD')
                        .replace(/\{\{expiry_date\}\}/g, prev.expiry_date || 'TBD')
                        .replace(/\{\{offer_type\}\}/g, (prev.offer_type || '').replace(/_/g, ' '))
                      const bod = tpl.body
                        .replace(/\{\{candidate_name\}\}/g, prev.candidate_name || 'Candidate')
                        .replace(/\{\{job_title\}\}/g, prev.job_title || 'the position')
                        .replace(/\{\{salary\}\}/g, Number(prev.salary || 0).toLocaleString())
                        .replace(/\{\{bonus\}\}/g, Number(prev.bonus || 0).toLocaleString())
                        .replace(/\{\{start_date\}\}/g, prev.start_date || 'TBD')
                        .replace(/\{\{expiry_date\}\}/g, prev.expiry_date || 'TBD')
                        .replace(/\{\{offer_type\}\}/g, (prev.offer_type || '').replace(/_/g, ' '))
                      return { ...prev, emailSubject: sub, emailBody: bod, _selectedTemplateId: tpl.id }
                    })
                    return (
                      <button
                        key={tpl.id}
                        onClick={() => applyTpl(tpl)}
                        style={{ padding: '6px 14px', borderRadius: 20, border: `1.5px solid ${isActive ? sm.color : sm.border}`, background: isActive ? sm.activeBg : sm.bg, color: sm.color, fontSize: '0.8rem', fontWeight: isActive ? 700 : 500, cursor: 'pointer', transition: 'all 0.15s', display: 'flex', alignItems: 'center', gap: 5, boxShadow: isActive ? `0 0 0 2px ${sm.border}` : 'none' }}
                      >
                        {isActive && <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3"><polyline points="20 6 9 17 4 12"/></svg>}
                        {tpl.name}
                      </button>
                    )
                  })}
                </div>
              </div>
            )}

            {/* Candidate info */}
            <div style={{ padding: '14px 18px', background: '#eff6ff', borderRadius: 10, marginBottom: 20, display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8, fontSize: '0.84rem' }}>
              <div><strong style={{ color: '#2563eb' }}>To:</strong> {offerMailPreview.candidate_name}</div>
              <div><strong style={{ color: '#2563eb' }}>Email:</strong> {offerMailPreview.emailTo}</div>
              <div><strong style={{ color: '#2563eb' }}>Job:</strong> {offerMailPreview.job_title}</div>
              <div><strong style={{ color: '#2563eb' }}>Salary:</strong> ₹{Number(offerMailPreview.salary || 0).toLocaleString()}</div>
            </div>

            {/* Email content */}
            <div style={{ border: '1px solid #e5e7eb', borderRadius: 12, overflow: 'hidden', marginBottom: 20 }}>
              {/* Header */}
              <div style={{ padding: '12px 18px', background: '#f9fafb', borderBottom: '1px solid #e5e7eb' }}>
                <div style={{ display: 'grid', gridTemplateColumns: '50px 1fr', gap: '4px 10px', fontSize: '0.82rem' }}>
                  <span style={{ color: '#9ca3af', fontWeight: 600 }}>From:</span>
                  <span>HireFlow &lt;noreply@hireflow.com&gt;</span>
                  <span style={{ color: '#9ca3af', fontWeight: 600 }}>To:</span>
                  <span>{offerMailPreview.candidate_name} &lt;{offerMailPreview.emailTo}&gt;</span>
                </div>
              </div>

              {/* Subject */}
              <div style={{ padding: '14px 18px', borderBottom: '1px solid #f3f4f6' }}>
                {offerMailPreview._editing ? (
                  <div className="input-group" style={{ marginBottom: 0 }}>
                    <label>Subject</label>
                    <input className="input" value={offerMailPreview.emailSubject}
                      onChange={e => setOfferMailPreview({ ...offerMailPreview, emailSubject: e.target.value })} />
                  </div>
                ) : (
                  <div style={{ fontSize: '0.95rem', fontWeight: 600 }}>{offerMailPreview.emailSubject}</div>
                )}
              </div>

              {/* Body */}
              <div style={{ padding: '18px 20px' }}>
                {offerMailPreview._editing ? (
                  <div className="input-group" style={{ marginBottom: 0 }}>
                    <label>Email Body</label>
                    <textarea className="textarea" rows={14} value={offerMailPreview.emailBody}
                      onChange={e => setOfferMailPreview({ ...offerMailPreview, emailBody: e.target.value })}
                      style={{ fontSize: '0.88rem', lineHeight: 1.7 }} />
                  </div>
                ) : (
                  <div style={{ fontSize: '0.9rem', lineHeight: 1.8, color: 'var(--text-primary)', whiteSpace: 'pre-wrap' }}>
                    {offerMailPreview.emailBody}
                  </div>
                )}
              </div>
            </div>

            {offerMailError && (
              <div style={{ color: 'var(--danger, #dc2626)', fontSize: '0.85rem', marginBottom: 10 }}>{offerMailError}</div>
            )}
            <div className="flex gap-12" style={{ justifyContent: 'flex-end' }}>
              <button className="btn btn-secondary" onClick={() => setOfferMailPreview(null)}>Cancel</button>
              {!offerMailPreview._editing && (
                <button className="btn btn-secondary" onClick={() => setOfferMailPreview({ ...offerMailPreview, _editing: true })}>
                  <Edit3 size={14} /> Edit Before Sending
                </button>
              )}
              <button className="btn btn-primary" disabled={offerMailSending} onClick={handleSendOfferMail}>
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg>
                {offerMailSending ? 'Sending…' : 'Send Email'}
              </button>
            </div>
          </div>
        </div>
      )}

      {tab === 'ats' && (
        <div>
          <div className="flex-between mb-24">
            <div>
              <h3 style={{ fontSize: '1.2rem' }}>ATS Scores</h3>
              <span className="text-sm text-muted">Select a job to view candidate ATS rankings</span>
            </div>
          </div>
          {jobs.filter(j => j.status === 'active').length === 0 ? (
            <div className="card" style={{ padding: 60, textAlign: 'center', color: 'var(--text-tertiary)' }}>No active jobs found. Jobs must be approved before ATS scores can be viewed.</div>
          ) : (
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: 16 }}>
              {jobs.filter(j => j.status === 'active').map(j => {
                const pipeline = jobPipelines[j.id] || { total: 0 }
                return (
                  <div key={j.id} className="card" style={{ padding: '20px 22px', display: 'flex', flexDirection: 'column', gap: 14 }}>
                    <div className="flex-between">
                      <span className={`badge ${j.status === 'active' ? 'badge-green' : 'badge-neutral'}`}>{j.status === 'active' ? 'Open' : j.status}</span>
                      <span style={{ fontSize: '0.7rem', fontFamily: 'var(--font-mono)', color: 'var(--text-tertiary)' }}>
                        REQ-{j.id?.slice(0, 8)?.toUpperCase()}
                      </span>
                    </div>
                    <div>
                      <h3 style={{ fontSize: '1rem', margin: 0, marginBottom: 4 }}>{j.title}</h3>
                      <div className="text-sm text-muted">{j.department}</div>
                    </div>
                    <div className="flex-between" style={{ fontSize: '0.84rem', color: 'var(--text-secondary)' }}>
                      <span>{j.location}</span>
                      <span className="mono">{pipeline.total || 0} candidates</span>
                    </div>
                    <button className="btn btn-primary btn-sm" style={{ width: '100%', justifyContent: 'center' }}
                      onClick={() => openAtsSheet(j)}>
                      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z"/><path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z"/></svg>
                      Open ATS Scores
                    </button>
                  </div>
                )
              })}
            </div>
          )}
        </div>
      )}

      {tab === 'resume-match' && <ResumeMatcher />}

      {/* ═══════════ TEST RESULTS TAB — same pattern as ATS ═══════════ */}
      {tab === 'test-results' && (() => {
        // Group results by job — match by job_id first, then job_title as fallback
        const jobGroups = {}
        testResults.forEach(r => {
          const matchedJob = jobs.find(j => j.id === r.job_id) || jobs.find(j => j.title?.toLowerCase() === r.job_title?.toLowerCase())
          const key = matchedJob ? matchedJob.id : (r.job_id || 'unknown')
          const displayTitle = r.job_title || matchedJob?.title || 'Unknown Job'
          if (!jobGroups[key]) jobGroups[key] = { title: displayTitle, job_id: key, results: [] }
          jobGroups[key].results.push(r)
        })
        return (
          <div>
            <div className="flex-between mb-24">
              <div>
                <h3 style={{ fontSize: '1.2rem' }}>Test Results</h3>
                <span className="text-sm text-muted">Select a job to view completed test results</span>
              </div>
            </div>
            {jobs.length === 0 ? (
              <div className="card" style={{ padding: 60, textAlign: 'center', color: 'var(--text-tertiary)' }}>No jobs found. Create a job posting first.</div>
            ) : (
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: 16 }}>
                {(() => {
                  // Build unified card list: HR's known jobs first, then orphaned groups
                  const knownJobIds = new Set(jobs.map(j => j.id))
                  const orphanedGroups = Object.entries(jobGroups)
                    .filter(([key]) => !knownJobIds.has(key))
                    .map(([key, group]) => ({
                      id: key, title: group.title, department: '', location: '', _orphan: true, _group: group
                    }))
                  const allCards = [
                    ...jobs.map(j => ({ ...j, _orphan: false, _group: jobGroups[j.id] })),
                    ...orphanedGroups,
                  ]
                  return allCards.map(j => {
                    const group = j._group
                    const completedResults = group ? group.results.filter(r => !r.pending) : []
                    const pendingResults = group ? group.results.filter(r => r.pending) : []
                    const resultCount = completedResults.length
                    const pendingCount = pendingResults.length
                    const pipeline = jobPipelines[j.id] || { total: 0 }
                    return (
                      <div key={j.id} className="card" style={{ padding: '20px 22px', display: 'flex', flexDirection: 'column', gap: 14 }}>
                        <div className="flex-between">
                          <span className={`badge ${resultCount > 0 ? 'badge-green' : pendingCount > 0 ? 'badge-amber' : 'badge-neutral'}`}>
                            {resultCount > 0 ? `${resultCount} Completed` : pendingCount > 0 ? `${pendingCount} Sent — Pending` : 'No Results'}
                          </span>
                          <span style={{ fontSize: '0.7rem', fontFamily: 'var(--font-mono)', color: 'var(--text-tertiary)' }}>
                            REQ-{j.id?.slice(0, 8)?.toUpperCase()}
                          </span>
                        </div>
                        <div>
                          <h3 style={{ fontSize: '1rem', margin: 0, marginBottom: 4 }}>{j.title}</h3>
                          <div className="text-sm text-muted">{j.department}</div>
                        </div>
                        <div className="flex-between" style={{ fontSize: '0.84rem', color: 'var(--text-secondary)' }}>
                          <span>{j.location}</span>
                          <span className="mono">{pipeline.total || 0} candidates</span>
                        </div>
                        {resultCount > 0 ? (
                          <button className="btn btn-primary btn-sm" style={{ width: '100%', justifyContent: 'center' }}
                            onClick={() => { setTestResultJob(group); setTestResultCandidates(completedResults); setRound2Sel(new Set()); setRound2Deadline('') }}>
                            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M9 5H7a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V7a2 2 0 0 0-2-2h-2"/><rect x="9" y="3" width="6" height="4" rx="1"/><path d="m9 14 2 2 4-4"/></svg>
                            View Test Results
                          </button>
                        ) : pendingCount > 0 ? (
                          <button className="btn btn-secondary btn-sm" style={{ width: '100%', justifyContent: 'center', opacity: 0.7, cursor: 'default', background: '#fef3c7', color: '#92400e', border: '1px solid #fcd34d' }} disabled>
                            ⏳ Awaiting Submission ({pendingCount})
                          </button>
                        ) : (
                          <button className="btn btn-secondary btn-sm" style={{ width: '100%', justifyContent: 'center', opacity: 0.6, cursor: 'default' }} disabled>
                            No Test Results Yet
                          </button>
                        )}
                      </div>
                    )
                  })
                })()}
              </div>
            )}
          </div>
        )
      })()}

      {/* Test Results Sheet Overlay — same as ATS sheet */}
      {testResultJob && (
        <div className="modal-overlay" onClick={() => { setTestResultJob(null); setTestResultCandidates([]); setRound2Sel(new Set()); setRound2Deadline('') }} style={{ alignItems: 'flex-start', paddingTop: 40 }}>
          <div onClick={e => e.stopPropagation()} style={{
            background: '#fff', borderRadius: 18, width: '95%', maxWidth: 1000,
            maxHeight: '90vh', overflow: 'hidden', display: 'flex', flexDirection: 'column',
            boxShadow: '0 24px 80px rgba(0,0,0,0.15)', border: '1px solid var(--border-subtle)'
          }}>
            <div style={{ padding: '24px 28px 16px', borderBottom: '2px solid var(--border-subtle)', flexShrink: 0 }}>
              <div className="flex-between">
                <div>
                  <h2 style={{ margin: 0, fontSize: '1.3rem' }}>{testResultJob.title} — Test Results</h2>
                  <div className="text-sm text-muted" style={{ marginTop: 4 }}>{testResultCandidates.length} candidates completed</div>
                </div>
                <button className="btn btn-ghost btn-sm" onClick={() => { setTestResultJob(null); setTestResultCandidates([]); setRound2Sel(new Set()); setRound2Deadline('') }} style={{ fontSize: '1.2rem', padding: '6px 12px' }}>x</button>
              </div>
            </div>

            {/* ── Round 2 (Coding) controls bar — Top-N quick-select, pick date, Review & Send (mirrors the ATS sheet) ── */}
            {(() => {
              // Always show the Round 2 controls bar while the sheet is open.
              const mcqRows = testResultCandidates.filter(r => r.two_round)
              const eligible = mcqRows.filter(round2Eligible)  // completed Round 1, not yet advanced
              const sentCount = mcqRows.length - eligible.length
              const none = eligible.length === 0
              return (
                <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap',
                  padding: '10px 14px', background: 'linear-gradient(135deg, #faf5ff 0%, #f5f3ff 60%, #fdf4ff 100%)',
                  borderBottom: '1px solid #e9d5ff' }}>
                  <span style={{ fontSize: '0.78rem', fontWeight: 700, color: '#6b21a8', textTransform: 'uppercase', letterSpacing: '0.04em' }}>
                    Round 2 — Coding
                  </span>
                  {/* Top N quick-select (acts on candidates still pending Round 2) */}
                  <div style={{ display: 'flex', alignItems: 'center', gap: 0, background: '#fff', borderRadius: 8,
                    border: '1px solid #d8b4fe', overflow: 'hidden', opacity: none ? 0.5 : 1 }}>
                    <span style={{ background: '#f5f3ff', padding: '7px 10px', fontSize: '0.82rem', fontWeight: 700,
                      color: '#6d28d9', borderRight: '1px solid #e9d5ff' }}>Top N Candidates</span>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '0 10px' }}>
                      <input type="number" min={1} max={Math.max(1, eligible.length)} value={round2TopN} disabled={none}
                        onChange={e => setRound2TopN(Math.max(1, Math.min(eligible.length || 1, parseInt(e.target.value) || 1)))}
                        style={{ width: 50, padding: '5px 6px', fontSize: '0.85rem', textAlign: 'center',
                          border: '1px solid #e9d5ff', borderRadius: 6, background: '#faf5ff', outline: 'none', fontWeight: 600, color: '#1e1b4b' }} />
                      <span style={{ fontSize: '0.78rem', color: '#7c3aed', fontWeight: 500 }}>of {eligible.length}</span>
                    </div>
                    <button onClick={applyRound2TopN} disabled={none}
                      style={{ border: 'none', background: none ? '#c4b5fd' : 'linear-gradient(135deg, #8b5cf6, #7c3aed)', color: '#fff',
                        padding: '7px 16px', fontSize: '0.82rem', fontWeight: 700, cursor: none ? 'not-allowed' : 'pointer' }}>Apply</button>
                  </div>

                  {/* Selected + date + Review & Send */}
                  {round2Sel.size > 0 && (
                    <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '5px 12px',
                      background: 'linear-gradient(135deg, #ecfdf5, #f0fdf4)', borderRadius: 8,
                      border: '1px solid #86efac', boxShadow: '0 1px 3px rgba(34,197,94,0.1)' }}>
                      <div style={{ background: '#16a34a', color: '#fff', borderRadius: 6, padding: '2px 8px',
                        fontSize: '0.8rem', fontWeight: 700, minWidth: 24, textAlign: 'center' }}>{round2Sel.size}</div>
                      <span style={{ fontSize: '0.8rem', fontWeight: 600, color: '#15803d', whiteSpace: 'nowrap' }}>selected</span>
                      <div style={{ width: 1, height: 18, background: '#bbf7d0' }} />
                      <input type="date" className="input" title="Round 2 deadline"
                        min={new Date().toISOString().split('T')[0]}
                        style={{ width: 150, padding: '4px 8px', fontSize: '0.8rem', border: '1px solid #bbf7d0', borderRadius: 6, background: '#fff' }}
                        value={round2Deadline} onChange={e => setRound2Deadline(e.target.value)} />
                      <button onClick={() => openRound2Modal(testResultCandidates.filter(r => round2Sel.has(r.candidate_id) && round2Eligible(r)))}
                        style={{ border: 'none', background: 'linear-gradient(135deg, #16a34a, #15803d)', color: '#fff',
                          padding: '6px 14px', fontSize: '0.8rem', fontWeight: 700, borderRadius: 6, cursor: 'pointer', whiteSpace: 'nowrap' }}>
                        Review &amp; Send
                      </button>
                    </div>
                  )}
                  <span style={{ marginLeft: 'auto', fontSize: '0.74rem', color: none ? '#9333ea' : '#9ca3af', fontWeight: none ? 600 : 400 }}>
                    {mcqRows.length === 0
                      ? 'No two-round candidates — send a two-round (MCQ→Coding) test invite to enable Round 2'
                      : none
                        ? `All ${sentCount} Round-1 passer${sentCount !== 1 ? 's' : ''} already sent Round 2`
                        : `${eligible.length} pending Round 2${sentCount ? ` · ${sentCount} already sent` : ''} — tick rows or use Top N`}
                  </span>
                </div>
              )
            })()}
            <div style={{ overflow: 'auto', flex: 1 }}>
              <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                <thead>
                  <tr style={{ background: '#f9fafb', position: 'sticky', top: 0, zIndex: 2 }}>
                    {(() => {
                      const eligible = testResultCandidates.filter(round2Eligible)
                      const allSel = eligible.length > 0 && eligible.every(r => round2Sel.has(r.candidate_id))
                      return (
                        <th style={{ padding: '12px 10px', borderBottom: '1px solid var(--border-subtle)', width: 34 }} title="Select Round 1 (MCQ) passers for Round 2">
                          <input type="checkbox" disabled={eligible.length === 0} checked={allSel}
                            onChange={e => setRound2Sel(e.target.checked ? new Set(eligible.map(r => r.candidate_id)) : new Set())} />
                        </th>
                      )
                    })()}
                    {['#', 'Candidate', 'Email', 'Phone', 'Test', 'Round', 'Score', 'Coding', 'Red Flags', 'Stage', 'Action', 'Completed'].map(h => (
                      <th key={h} style={{ padding: '12px 16px', fontSize: '0.72rem', fontWeight: 700, fontFamily: 'var(--font-display)', textTransform: 'uppercase', letterSpacing: '0.06em', color: '#9ca3af', textAlign: 'left', borderBottom: '1px solid var(--border-subtle)', whiteSpace: 'nowrap' }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {testResultCandidates.map((r, i) => (
                    <tr key={r.id} style={{ borderBottom: '1px solid #f3f4f6', transition: 'background 0.1s' }}
                      onMouseEnter={e => e.currentTarget.style.background = '#f9fafb'}
                      onMouseLeave={e => e.currentTarget.style.background = 'transparent'}>
                      <td style={{ padding: '14px 10px', textAlign: 'center' }}>
                        {round2Eligible(r) && (
                          <input type="checkbox" checked={round2Sel.has(r.candidate_id)}
                            onChange={e => setRound2Sel(prev => {
                              const next = new Set(prev)
                              if (e.target.checked) next.add(r.candidate_id); else next.delete(r.candidate_id)
                              return next
                            })} />
                        )}
                      </td>
                      <td style={{ padding: '14px 16px', fontFamily: 'var(--font-mono)', fontSize: '0.8rem', color: '#9ca3af', fontWeight: 600 }}>{i + 1}</td>
                      <td style={{ padding: '14px 16px', fontWeight: 600, fontSize: '0.92rem' }}>{r.candidate_name}</td>
                      <td style={{ padding: '14px 16px', fontSize: '0.84rem', color: 'var(--text-secondary)' }}>{r.candidate_email}</td>
                      <td style={{ padding: '14px 16px', fontSize: '0.82rem', fontFamily: 'var(--font-mono)', color: 'var(--text-tertiary)' }}>{r.candidate_phone || '—'}</td>
                      <td style={{ padding: '14px 16px', fontWeight: 500, fontSize: '0.88rem' }}>{r.title}</td>
                      <td style={{ padding: '14px 16px', whiteSpace: 'nowrap' }}>
                        {r.two_round ? (
                          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5 }}>
                            <span title="Round 1 (MCQ)" style={{ padding: '3px 7px', borderRadius: 10, fontSize: '0.72rem', fontWeight: 700, background: r.mcq_done ? '#dcfce7' : '#eff6ff', color: r.mcq_done ? '#15803d' : '#2563eb' }}>R1{r.mcq_done ? ' ✓' : ''}</span>
                            {r.round2_status === 'completed' ? (
                              <span title="Round 2 (Coding) completed" style={{ padding: '3px 7px', borderRadius: 10, fontSize: '0.72rem', fontWeight: 700, background: '#ede9fe', color: '#6d28d9' }}>R2 ✓</span>
                            ) : r.round2_status === 'sent' ? (
                              <span title="Round 2 sent, awaiting submission" style={{ padding: '3px 7px', borderRadius: 10, fontSize: '0.72rem', fontWeight: 700, background: '#e0e7ff', color: '#4338ca' }}>R2 sent</span>
                            ) : (
                              <span title="Round 2 (Coding) not sent yet" style={{ padding: '3px 7px', borderRadius: 10, fontSize: '0.72rem', fontWeight: 700, background: '#fef3c7', color: '#92400e', border: '1px solid #fde68a' }}>⏳ R2</span>
                            )}
                          </span>
                        ) : (
                          <span style={{ padding: '3px 8px', borderRadius: 10, fontSize: '0.74rem', fontWeight: 600, background: '#f3f4f6', color: '#6b7280' }}>Combined</span>
                        )}
                      </td>
                      <td style={{ padding: '14px 16px' }}>
                        {r.test_score != null ? (
                          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                            <span style={{
                              fontWeight: 700, fontSize: '0.95rem',
                              color: r.total_marks && (r.test_score / r.total_marks) >= 0.7 ? '#15803d' : r.total_marks && (r.test_score / r.total_marks) >= 0.4 ? '#d97706' : '#dc2626'
                            }}>
                              {r.test_score}
                            </span>
                            <span style={{ fontSize: '0.75rem', color: '#9ca3af' }}>/ {r.total_marks || '—'}</span>
                            <button onClick={() => openAnswerDetail(r)} title="View answers"
                              style={{ marginLeft: 4, padding: '2px 8px', borderRadius: 6, border: '1px solid #d1d5db', background: '#fff', color: '#374151', fontSize: '0.72rem', fontWeight: 600, cursor: 'pointer' }}>
                              View
                            </button>
                          </div>
                        ) : (
                          <span className="text-muted text-sm">—</span>
                        )}
                      </td>
                      <td style={{ padding: '14px 16px' }}>
                        {r.coding_total > 0 && r.coding_verdict ? (
                          <span style={{
                            display: 'inline-flex', alignItems: 'center', gap: 6, padding: '3px 10px',
                            borderRadius: 12, fontSize: '0.78rem', fontWeight: 700,
                            background: ({ AC: '#f0fdf4', WA: '#fef2f2', TLE: '#fffbeb', MLE: '#fffbeb', RE: '#f5f3ff', CE: '#f5f3ff' }[r.coding_verdict] || '#f3f4f6'),
                            color: ({ AC: '#15803d', WA: '#dc2626', TLE: '#d97706', MLE: '#d97706', RE: '#7c3aed', CE: '#7c3aed' }[r.coding_verdict] || '#6b7280'),
                          }}>
                            {r.coding_verdict} · {r.coding_score}/{r.coding_max_score}
                          </span>
                        ) : (
                          <span className="text-muted text-sm">—</span>
                        )}
                      </td>
                      <td style={{ padding: '14px 16px' }}>
                        {r.red_flags > 0 ? (
                          <button onClick={() => setProctorDetail(r)} title="View flag types & evidence frames"
                            style={{ display: 'inline-flex', alignItems: 'center', gap: 4, padding: '3px 10px', borderRadius: 12, fontSize: '0.78rem', fontWeight: 700, border: 'none', cursor: 'pointer', background: r.red_flags >= 5 ? '#fef2f2' : '#fffbeb', color: r.red_flags >= 5 ? '#dc2626' : '#d97706' }}>
                            ⚑ {r.red_flags}
                          </button>
                        ) : (
                          <span style={{ padding: '3px 10px', borderRadius: 12, fontSize: '0.78rem', background: '#f0fdf4', color: '#15803d' }}>✓ Clean</span>
                        )}
                      </td>
                      <td style={{ padding: '14px 16px' }}>
                        <span className={`badge ${r.candidate_stage === 'rejected' ? 'badge-red' : r.candidate_stage === 'interview' || r.candidate_stage === 'technical_interview' ? 'badge-green' : r.candidate_stage === 'hr_review' ? 'badge-blue' : 'badge-amber'}`}>
                          {r.candidate_stage === 'interview' ? 'Phone Screen' : r.candidate_stage === 'technical_interview' ? 'Tech Interview' : r.candidate_stage === 'hr_review' ? 'HR Review' : r.candidate_stage || 'Completed'}
                        </span>
                      </td>
                      <td style={{ padding: '14px 16px' }}>
                        {(() => {
                          const reject = (
                            <button onClick={() => handleTestAction(r, 'reject')} style={{ padding: '4px 10px', borderRadius: 6, border: 'none', background: '#ef4444', color: '#fff', fontSize: '0.75rem', fontWeight: 600, cursor: 'pointer' }}>Reject</button>
                          )
                          const scheduleBtn = (
                            <button onClick={() => {
                              const _preId = interviewers.find(x => x.email === r.interviewer_email)?.id
                              setScheduleModal({ candidate_id: r.candidate_id, candidate_name: r.candidate_name, candidate_email: r.candidate_email, job_id: r.job_id, job_title: r.job_title, interviewer_name: r.interviewer_name, interviewer_email: r.interviewer_email })
                              setSchedForm({ interview_type: 'TECHNICAL', mode: 'online', scheduled_datetime: '', duration_minutes: 60, meeting_link: '', location: '', notes: '', send_email: true, email_subject: `Interview Scheduled — ${r.job_title}`, email_body: `Dear ${r.candidate_name},\n\nCongratulations! You have been shortlisted for the ${r.job_title} position.\n\nYour interview has been scheduled. Details:\n- Interview Type: {{interview_type}}\n- Date & Time: {{datetime}}\n- Mode: {{mode}}\n- {{meeting_details}}\n- Panel: {{interviewer_name}}\n\nPlease be on time and prepared.\n\nBest regards,\nHR Team`, interviewer_ids: _preId ? [_preId] : [], manual_panelists: [] })
                            }} style={{ padding: '4px 10px', borderRadius: 6, border: 'none', background: '#2b7cba', color: '#fff', fontSize: '0.75rem', fontWeight: 600, cursor: 'pointer' }}>Schedule Interview</button>
                          )
                          const txt = (s, c) => <span style={{ fontSize: '0.75rem', color: c, fontWeight: 600 }}>{s}</span>

                          if (r.candidate_stage === 'rejected') return txt('Rejected', '#dc2626')
                          if (r.candidate_stage === 'technical_interview') return txt('Interview Scheduled', '#15803d')
                          if (['interview_completed', 'hired', 'offer_sent'].includes(r.candidate_stage))
                            return <button onClick={() => setTab('feedback')} style={{ padding: '4px 10px', borderRadius: 6, border: 'none', background: '#7c3aed', color: '#fff', fontSize: '0.75rem', fontWeight: 600, cursor: 'pointer' }}>View Feedback</button>

                          if (r.two_round) {
                            // Gate everything on Round 2 attendance.
                            if (r.round2_pending) return (   // R1 done, R2 not sent → send coding round
                              <div style={{ display: 'flex', gap: 4 }}>
                                <button onClick={() => openRound2Modal([r])} style={{ padding: '4px 10px', borderRadius: 6, border: 'none', background: '#7c3aed', color: '#fff', fontSize: '0.75rem', fontWeight: 600, cursor: 'pointer' }}>Send Round 2 (Coding)</button>
                                {reject}
                              </div>
                            )
                            if (r.round2_status === 'sent')   // R2 sent but not attended yet → no schedule
                              return txt('Round 2 sent — awaiting submission', '#4338ca')
                            if (r.round2_status === 'completed')  // attended Round 2 → schedule interview
                              return <div style={{ display: 'flex', gap: 4 }}>{scheduleBtn}{reject}</div>
                            return txt('Round 1 in progress', '#92400e')  // defensive: R2 not attended
                          }

                          // Combined / single test → schedule after the test is done.
                          return <div style={{ display: 'flex', gap: 4 }}>{scheduleBtn}{reject}</div>
                        })()}
                      </td>
                      <td style={{ padding: '14px 16px', fontFamily: 'var(--font-mono)', fontSize: '0.82rem', color: 'var(--text-secondary)' }}>{r.completed_at ? new Date(r.completed_at).toLocaleString() : '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}

      {/* ═══════════ ROUND 2 (CODING) INVITE — review email before sending ═══════════ */}
      {round2Modal && (
        <div className="modal-overlay" onClick={() => setRound2Modal(null)} style={{ zIndex: 1100 }}>
          <div className="modal" onClick={e => e.stopPropagation()} style={{ maxWidth: 680, maxHeight: '90vh', overflow: 'auto' }}>
            <h2 style={{ margin: 0, marginBottom: 6, paddingBottom: 14, borderBottom: '2px solid var(--border-subtle)' }}>
              Send Round 2 — Coding
            </h2>
            <div className="text-sm text-muted" style={{ marginBottom: 18 }}>
              {round2Modal.job_title} · {round2Modal.rows.length} candidate{round2Modal.rows.length !== 1 ? 's' : ''} who cleared Round 1 (MCQ)
            </div>

            {/* Recipients */}
            <div style={{ padding: 14, background: '#f5f3ff', borderRadius: 10, border: '1px solid #ddd6fe', marginBottom: 18 }}>
              <div style={{ fontSize: '0.72rem', fontWeight: 700, textTransform: 'uppercase', color: '#7c3aed', marginBottom: 8 }}>Recipients</div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                {round2Modal.rows.map(r => (
                  <span key={r.candidate_id} style={{ padding: '3px 10px', borderRadius: 12, fontSize: '0.78rem', background: '#ede9fe', color: '#5b21b6', fontWeight: 600 }}>
                    {r.candidate_name}
                  </span>
                ))}
              </div>
              <div style={{ fontSize: '0.76rem', color: '#7c3aed', marginTop: 8 }}>
                Each candidate gets a new <strong>Round 2 (Coding)</strong> test with the coding questions from the same assessment. They’ll see it in “My Interviews”.
              </div>
            </div>

            {/* Deadline + Test link */}
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1.4fr', gap: 12, marginBottom: 16 }}>
              <div className="input-group">
                <label>Deadline date</label>
                <input className="input" type="date" value={round2Email.deadline}
                  min={new Date().toISOString().split('T')[0]}
                  onChange={e => setRound2Email({ ...round2Email, deadline: e.target.value })} title="Round 2 deadline" />
              </div>
              <div className="input-group">
                <label>Test Link (in email)</label>
                <input className="input" value={round2Email.testLink}
                  onChange={e => setRound2Email({ ...round2Email, testLink: e.target.value })} />
              </div>
            </div>

            {/* Email subject + body */}
            <div style={{ padding: 18, background: '#faf5ff', borderRadius: 12, border: '1px solid #e9d5ff', marginBottom: 22 }}>
              <div style={{ fontSize: '0.78rem', fontWeight: 700, textTransform: 'uppercase', color: '#7c3aed', marginBottom: 12, fontFamily: 'var(--font-display)' }}>
                Round 2 Invitation Email
              </div>
              <MailFromPicker stepKey="test_invitation" label="Send from"
                value={round2MailboxId} onChange={setRound2MailboxId} />
              <div className="input-group" style={{ marginBottom: 12 }}>
                <label>Subject</label>
                <input className="input" value={round2Email.subject}
                  onChange={e => setRound2Email({ ...round2Email, subject: e.target.value })} />
              </div>
              <div className="input-group">
                <label>Body</label>
                <textarea className="textarea" rows={9} value={round2Email.body}
                  onChange={e => setRound2Email({ ...round2Email, body: e.target.value })}
                  style={{ fontSize: '0.88rem', lineHeight: 1.7 }} />
                <span style={{ fontSize: '0.72rem', color: 'var(--text-tertiary)' }}>
                  Use {'{{test_link}}'} for the test URL, {'{{candidate_name}}'} for name, {'{{deadline}}'} for deadline
                </span>
              </div>
            </div>

            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 10 }}>
              <button className="btn btn-ghost" onClick={() => setRound2Modal(null)} disabled={round2Sending}>Cancel</button>
              <button className="btn" style={{ background: '#7c3aed', color: '#fff', fontWeight: 600 }} onClick={sendRound2} disabled={round2Sending}>
                {round2Sending ? 'Sending…' : `Send Round 2 to ${round2Modal.rows.length}`}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ═══════════ ANSWER DETAIL MODAL ═══════════ */}
      {answerDetail && (
        <div className="modal-overlay" onClick={() => setAnswerDetail(null)} style={{ alignItems: 'flex-start', paddingTop: 40 }}>
          <div onClick={e => e.stopPropagation()} style={{
            background: '#fff', borderRadius: 18, width: '95%', maxWidth: 760,
            maxHeight: '90vh', overflow: 'hidden', display: 'flex', flexDirection: 'column',
            boxShadow: '0 24px 80px rgba(0,0,0,0.15)', border: '1px solid var(--border-subtle)'
          }}>
            <div style={{ padding: '20px 24px 14px', borderBottom: '2px solid var(--border-subtle)', flexShrink: 0 }}>
              <div className="flex-between">
                <div>
                  <h2 style={{ margin: 0, fontSize: '1.2rem' }}>Answers — {answerDetail.candidate_name}</h2>
                  {answerDetail.data && (
                    <div className="text-sm text-muted" style={{ marginTop: 4 }}>
                      Score {answerDetail.data.test_score ?? '—'} / {answerDetail.data.total_marks ?? '—'}
                    </div>
                  )}
                </div>
                <button className="btn btn-ghost btn-sm" onClick={() => setAnswerDetail(null)} style={{ fontSize: '1.2rem', padding: '6px 12px' }}>x</button>
              </div>
            </div>
            <div style={{ overflow: 'auto', flex: 1, padding: '16px 24px 24px' }}>
              {answerDetail.loading ? (
                <div style={{ padding: 40, textAlign: 'center', color: 'var(--text-tertiary)' }}>Loading…</div>
              ) : answerDetail.error || !answerDetail.data ? (
                <div style={{ padding: 40, textAlign: 'center', color: '#dc2626' }}>Could not load answers.</div>
              ) : answerDetail.data.answers.length === 0 ? (
                <div style={{ padding: 40, textAlign: 'center', color: 'var(--text-tertiary)' }}>No answer detail recorded.</div>
              ) : (<>
                {answerDetail.data.has_pending_regrade && (
                  <div style={{ padding: '12px 14px', marginBottom: 14, borderRadius: 10, background: '#fffbeb', border: '1px solid #fcd34d', fontSize: '0.85rem', color: '#92400e' }}>
                    <strong>Grading deferred.</strong> The code judge was unavailable when this
                    test was submitted, so one or more coding answers were saved but not graded —
                    and were left out of the total shown above.
                    <button className="btn btn-sm" disabled={regrading} onClick={() => regradeInterview(answerDetail.data.interview_id)}
                      style={{ marginLeft: 10, background: '#92400e', color: '#fff', border: 'none', padding: '4px 12px', borderRadius: 6, fontWeight: 600, cursor: 'pointer', fontSize: '0.8rem' }}>
                      {regrading ? 'Re-grading…' : 'Re-grade now'}
                    </button>
                    {regradeError && <div style={{ marginTop: 8, color: '#dc2626' }}>{regradeError}</div>}
                  </div>
                )}
                {answerDetail.data.answers.map((a, i) => (
                  <div key={a.question_id || i} style={{ border: '1px solid #e5e7eb', borderRadius: 10, padding: 14, marginBottom: 12 }}>
                    <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 6 }}>
                      <span style={{ fontSize: '0.7rem', fontWeight: 700, textTransform: 'uppercase', letterSpacing: 0.5, color: '#6b7280', background: '#f3f4f6', padding: '2px 8px', borderRadius: 6 }}>{a.question_type || 'question'}</span>
                      <span style={{ fontSize: '0.78rem', color: '#9ca3af' }}>Q{i + 1}</span>
                      {a.pending_regrade && (
                        <span style={{ fontSize: '0.68rem', fontWeight: 700, color: '#92400e', background: '#fef3c7', border: '1px solid #fcd34d', padding: '2px 8px', borderRadius: 6 }}>
                          NOT GRADED
                        </span>
                      )}
                    </div>
                    {a.question_text && <div style={{ fontSize: '0.9rem', color: '#111', marginBottom: 10, lineHeight: 1.5 }}>{a.question_text}</div>}

                    {a.type === 'communication' ? (
                      <div>
                        <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, marginBottom: 8 }}>
                          <span style={{ fontSize: '0.8rem', color: '#374151', fontWeight: 600 }}>Communication score</span>
                          <span style={{ fontSize: '1.2rem', fontWeight: 700, color: '#1a73e8' }}>{Math.round(a.communication_score || 0)}<span style={{ fontSize: '0.78rem', color: '#9ca3af', fontWeight: 500 }}>/100</span></span>
                          {a.awarded != null && <span style={{ fontSize: '0.78rem', color: '#9ca3af' }}>(+{a.awarded} pts)</span>}
                        </div>
                        {a.audio_url && (
                          <audio controls src={a.audio_url} style={{ width: '100%', marginBottom: 10 }} />
                        )}
                        {a.transcript && (
                          <div style={{ background: '#f9fafb', border: '1px solid #e5e7eb', borderRadius: 8, padding: '10px 12px', marginBottom: 10, fontSize: '0.86rem', lineHeight: 1.6, color: '#111' }}>
                            {a.transcript}
                          </div>
                        )}
                        {a.breakdown && (
                          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(110px, 1fr))', gap: 10, marginBottom: 8 }}>
                            {[
                              ['Pace', `${Math.round(a.breakdown.words_per_minute || 0)} wpm`, a.breakdown.pace_score],
                              ['Clarity', `${a.breakdown.filler_count || 0} fillers`, a.breakdown.filler_score],
                              ['Voice', '', a.breakdown.acoustic_score],
                              ['Fluency', `${a.breakdown.pause_count || 0} pauses`, a.breakdown.fluency_score],
                            ].map(([label, sub, val]) => (
                              <div key={label}>
                                <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.78rem', marginBottom: 3 }}>
                                  <span style={{ color: '#4b5563' }}>{label}</span>
                                  <span style={{ fontWeight: 700 }}>{Math.round(val || 0)}</span>
                                </div>
                                <div style={{ height: 6, background: '#eef2f7', borderRadius: 4, overflow: 'hidden' }}>
                                  <div style={{ height: '100%', width: `${Math.round(val || 0)}%`, background: '#1a73e8' }} />
                                </div>
                                {sub && <span style={{ fontSize: '0.7rem', color: '#9ca3af' }}>{sub}</span>}
                              </div>
                            ))}
                          </div>
                        )}
                        {Array.isArray(a.breakdown?.feedback) && a.breakdown.feedback.length > 0 && (
                          <ul style={{ margin: '8px 0 0', paddingLeft: 18, color: '#4b5563', fontSize: '0.8rem', lineHeight: 1.6 }}>
                            {a.breakdown.feedback.map((f, j) => <li key={j}>{f}</li>)}
                          </ul>
                        )}
                      </div>
                    ) : (
                      <div style={{ fontSize: '0.86rem', lineHeight: 1.6 }}>
                        <div><span style={{ color: '#6b7280' }}>Answer: </span><span style={{ fontWeight: 600 }}>{a.selected_option ?? '—'}</span>
                          {a.is_correct != null && (
                            <span style={{ marginLeft: 8, color: a.is_correct ? '#15803d' : '#dc2626', fontWeight: 700 }}>{a.is_correct ? '✓ Correct' : '✗ Incorrect'}</span>
                          )}
                        </div>
                        {!a.is_correct && a.correct_answer && (
                          <div style={{ color: '#15803d', marginTop: 2 }}><span style={{ color: '#6b7280' }}>Correct: </span>{a.correct_answer}</div>
                        )}
                      </div>
                    )}
                  </div>
                ))}
              </>)}
            </div>
          </div>
        </div>
      )}

      {/* ═══════════ PROCTORING FLAGS MODAL ═══════════ */}
      {proctorDetail && (
        <div className="modal-overlay" onClick={() => setProctorDetail(null)} style={{ alignItems: 'flex-start', paddingTop: 40 }}>
          <div onClick={e => e.stopPropagation()} style={{
            background: '#fff', borderRadius: 18, width: '95%', maxWidth: 860,
            maxHeight: '90vh', overflow: 'hidden', display: 'flex', flexDirection: 'column',
            boxShadow: '0 24px 80px rgba(0,0,0,0.15)', border: '1px solid var(--border-subtle)'
          }}>
            <div style={{ padding: '20px 24px 14px', borderBottom: '2px solid var(--border-subtle)', flexShrink: 0 }}>
              <div className="flex-between">
                <div>
                  <h2 style={{ margin: 0, fontSize: '1.2rem' }}>Proctoring Flags — {proctorDetail.candidate_name}</h2>
                  <div className="text-sm text-muted" style={{ marginTop: 4 }}>
                    {proctorDetail.title} · {proctorDetail.red_flags} flag{proctorDetail.red_flags === 1 ? '' : 's'}
                  </div>
                </div>
                <button className="btn btn-ghost btn-sm" onClick={() => setProctorDetail(null)} style={{ fontSize: '1.2rem', padding: '6px 12px' }}>x</button>
              </div>
            </div>
            <div style={{ overflow: 'auto', flex: 1, padding: '16px 24px 24px' }}>
              {(proctorDetail.proctor_interview_ids?.length ? proctorDetail.proctor_interview_ids : [proctorDetail.id])
                .map(id => <ProctoringReport key={id} interviewId={id} />)}
            </div>
          </div>
        </div>
      )}

      {/* ═══════════ STAGE TRACKER MODAL ═══════════ */}
      {trackerModal && (
        <div className="modal-overlay" onClick={() => setTrackerModal(null)} style={{ alignItems: 'flex-start', paddingTop: 40 }}>
          <div onClick={e => e.stopPropagation()} style={{
            background: 'var(--bg-elevated)', borderRadius: 18, width: '95%', maxWidth: 560,
            maxHeight: '90vh', overflow: 'auto', boxShadow: '0 24px 80px rgba(0,0,0,0.18)',
            border: '1px solid var(--border-subtle)', padding: '28px 28px 24px'
          }}>
            {trackerModal.loading ? (
              <div style={{ padding: 40, textAlign: 'center', color: 'var(--text-tertiary)' }}>Loading…</div>
            ) : (() => {
              const STAGE_MAP = {
                'applied': 0, 'ats_screening': 1, 'screening': 1,
                'hr_review': 2, 'shortlisted': 3,
                'test_sent': 4, 'test_in_progress': 4, 'test_submitted': 4, 'test_completed': 4,
                'interview': 5, 'technical_interview': 5,
                'interview_completed': 6,
                'hired': 7, 'offer_extended': 7, 'offer_sent': 7,
              }
              const rawStage = (trackerModal.current_stage || 'applied').toLowerCase().replace(/\s+/g, '_')
              const isRejected = rawStage === 'rejected' || rawStage === 'offer_rejected'
              let stageIdx = isRejected ? 0 : (STAGE_MAP[rawStage] ?? 0)
              if (isRejected) {
                const lastGood = [...(trackerModal.stage_history || [])].reverse().find(
                  h => !['rejected', 'offer_rejected'].includes((h.to_stage || '').toLowerCase())
                )
                stageIdx = lastGood ? (STAGE_MAP[(lastGood.to_stage || '').toLowerCase()] ?? 0) : 0
              }
              const ts = {}
              ;[...(trackerModal.stage_history || [])].forEach(h => {
                const idx = STAGE_MAP[(h.to_stage || '').toLowerCase().replace(/\s+/g, '_')]
                if (idx !== undefined && ts[idx] === undefined && h.changed_at) {
                  ts[idx] = new Date(h.changed_at).toLocaleString()
                }
              })
              return (
                <div>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 20 }}>
                    <div>
                      <div style={{ fontWeight: 700, fontSize: '1.1rem' }}>{trackerModal.name}</div>
                      <div style={{ fontSize: '0.82rem', color: 'var(--text-secondary)', marginTop: 3 }}>
                        {trackerModal.email}{trackerModal.job_title ? ` · ${trackerModal.job_title}` : ''}
                      </div>
                    </div>
                    <button onClick={() => setTrackerModal(null)} style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-tertiary)', fontSize: '1.3rem', lineHeight: 1, padding: '2px 6px' }}>×</button>
                  </div>
                  <StageTimeline currentStage={stageIdx} timestamps={ts} isRejected={isRejected} />
                  {(trackerModal.stage_history || []).length > 0 && (
                    <div style={{ marginTop: 20 }}>
                      <div style={{ fontSize: '0.75rem', fontWeight: 700, textTransform: 'uppercase', color: 'var(--text-tertiary)', letterSpacing: '0.06em', marginBottom: 10 }}>History</div>
                      <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                        {[...(trackerModal.stage_history || [])].reverse().map((h, i) => (
                          <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 10, fontSize: '0.8rem', padding: '8px 12px', borderRadius: 8, background: 'var(--bg-canvas)' }}>
                            <span style={{ color: 'var(--text-tertiary)', fontFamily: 'var(--font-mono)', fontSize: '0.7rem', minWidth: 130 }}>{h.changed_at ? new Date(h.changed_at).toLocaleString() : '—'}</span>
                            <span style={{ color: 'var(--text-secondary)' }}>{h.from_stage || '—'}</span>
                            <span style={{ color: 'var(--text-tertiary)' }}>→</span>
                            <span style={{ fontWeight: 600, color: 'var(--text-primary)' }}>{h.to_stage}</span>
                            {h.notes && <span style={{ color: 'var(--text-tertiary)', fontSize: '0.72rem', marginLeft: 'auto' }}>{h.notes}</span>}
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              )
            })()}
          </div>
        </div>
      )}

      {/* ═══════════ SCHEDULE INTERVIEW MODAL ═══════════ */}
      {scheduleModal && (
        <div className="modal-overlay" onClick={() => setScheduleModal(null)} style={{ alignItems: 'flex-start', paddingTop: 30, overflowY: 'auto' }}>
          <div onClick={e => e.stopPropagation()} style={{ background: '#fff', borderRadius: 16, width: '95%', maxWidth: 520, padding: '28px 32px', boxShadow: '0 24px 80px rgba(0,0,0,0.15)', maxHeight: '90vh', overflowY: 'auto', marginBottom: 30 }}>
            <div className="flex-between" style={{ marginBottom: 20 }}>
              <div>
                <h2 style={{ margin: 0, fontSize: '1.2rem' }}>Schedule Panel Interview</h2>
                <div className="text-sm text-muted" style={{ marginTop: 4 }}>{scheduleModal.candidate_name} — {scheduleModal.job_title}</div>
              </div>
              <button className="btn btn-ghost btn-sm" onClick={() => setScheduleModal(null)} style={{ fontSize: '1.2rem', padding: '4px 10px' }}>x</button>
            </div>

            <div style={{ padding: '12px 16px', background: '#f0f7ff', borderRadius: 10, marginBottom: 16, fontSize: '0.85rem' }}>
              <div style={{ fontWeight: 700, marginBottom: 8 }}>Interview Panel <span style={{ fontWeight: 400, color: '#6b7280' }}>— pick staff and/or add people manually</span></div>

              {/* Interviewers with a profile */}
              {interviewers.length > 0 && (
                <div style={{ marginBottom: 10 }}>
                  <div style={{ fontSize: '0.72rem', textTransform: 'uppercase', letterSpacing: '0.04em', color: '#6b7280', marginBottom: 4 }}>Interviewers</div>
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
                    {interviewers.map(iv => {
                      const checked = (schedForm.interviewer_ids || []).includes(iv.id)
                      return (
                        <label key={iv.id} style={{ display: 'inline-flex', alignItems: 'center', gap: 6, cursor: 'pointer', padding: '6px 10px', borderRadius: 8, border: checked ? '2px solid #2b7cba' : '1px solid #d1d5db', background: checked ? '#e0efff' : '#fff', fontSize: '0.82rem' }}>
                          <input type="checkbox" checked={checked} onChange={() => setSchedForm(f => ({ ...f, interviewer_ids: checked ? f.interviewer_ids.filter(id => id !== iv.id) : [...f.interviewer_ids, iv.id] }))} />
                          <span style={{ fontWeight: 600 }}>{iv.name}</span>
                          <span style={{ color: '#6b7280' }}>{iv.department && iv.department !== '—' ? `· ${iv.department}` : ''}</span>
                        </label>
                      )
                    })}
                  </div>
                </div>
              )}

              {/* Other staff (HR / Admin) — added as ad-hoc panelists by email */}
              {panelStaff.length > 0 && (
                <div style={{ marginBottom: 10 }}>
                  <div style={{ fontSize: '0.72rem', textTransform: 'uppercase', letterSpacing: '0.04em', color: '#6b7280', marginBottom: 4 }}>Other staff</div>
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
                    {panelStaff.map(u => {
                      const checked = (schedForm.manual_panelists || []).some(m => m.email.toLowerCase() === u.email.toLowerCase())
                      return (
                        <label key={u.email} style={{ display: 'inline-flex', alignItems: 'center', gap: 6, cursor: 'pointer', padding: '6px 10px', borderRadius: 8, border: checked ? '2px solid #2b7cba' : '1px solid #d1d5db', background: checked ? '#e0efff' : '#fff', fontSize: '0.82rem' }}>
                          <input type="checkbox" checked={checked} onChange={() => setSchedForm(f => ({ ...f, manual_panelists: checked ? f.manual_panelists.filter(m => m.email.toLowerCase() !== u.email.toLowerCase()) : [...f.manual_panelists, { name: u.name, email: u.email }] }))} />
                          <span style={{ fontWeight: 600 }}>{u.name}</span>
                          <span style={{ color: '#6b7280' }}>· {u.role}</span>
                        </label>
                      )
                    })}
                  </div>
                </div>
              )}

              {/* Add manually (name + email) */}
              <div style={{ marginBottom: 8 }}>
                <div style={{ fontSize: '0.72rem', textTransform: 'uppercase', letterSpacing: '0.04em', color: '#6b7280', marginBottom: 4 }}>Add manually</div>
                <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                  <input className="input" placeholder="Name" value={manualName} onChange={e => setManualName(e.target.value)} style={{ flex: '1 1 110px', minWidth: 0 }} />
                  <input className="input" type="email" placeholder="email@company.com" value={manualEmail}
                         onChange={e => setManualEmail(e.target.value)}
                         onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); addManualPanelist() } }}
                         style={{ flex: '1 1 160px', minWidth: 0 }} />
                  <button type="button" className="btn btn-secondary" onClick={addManualPanelist}>Add</button>
                </div>
              </div>

              {/* Manually-added / picked-staff chips */}
              {(schedForm.manual_panelists || []).length > 0 && (
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginBottom: 8 }}>
                  {(schedForm.manual_panelists || []).map(m => (
                    <span key={m.email} style={{ display: 'inline-flex', alignItems: 'center', gap: 6, padding: '4px 8px', borderRadius: 12, background: '#e0efff', fontSize: '0.78rem' }}>
                      {m.name || m.email}
                      <button type="button" onClick={() => setSchedForm(f => ({ ...f, manual_panelists: f.manual_panelists.filter(x => x.email !== m.email) }))}
                              style={{ border: 'none', background: 'none', cursor: 'pointer', color: '#6b7280', fontWeight: 700, lineHeight: 1 }}>&times;</button>
                    </span>
                  ))}
                </div>
              )}

              {(() => {
                const total = (schedForm.interviewer_ids || []).length + (schedForm.manual_panelists || []).length
                return total > 0
                  ? <div style={{ fontSize: '0.78rem', color: '#15803d' }}>{total} panelist{total > 1 ? 's' : ''} selected</div>
                  : <div style={{ fontSize: '0.78rem', color: '#9ca3af' }}>No panel members yet — pick staff above or add one manually.</div>
              })()}
            </div>

            <form className="auth-form" onSubmit={handleScheduleInterview}>
              <div className="input-group">
                <label>Interview Type</label>
                <select className="select" value={schedForm.interview_type} onChange={e => setSchedForm({ ...schedForm, interview_type: e.target.value })}>
                  <option value="TECHNICAL">Technical</option>
                  <option value="HR">HR Round</option>
                  <option value="BEHAVIORAL">Behavioral</option>
                  <option value="MIXED">Mixed</option>
                </select>
              </div>

              <div className="input-group">
                <label>Mode</label>
                <div style={{ display: 'flex', gap: 12 }}>
                  {['online', 'offline'].map(m => (
                    <label key={m} style={{ display: 'flex', alignItems: 'center', gap: 6, cursor: 'pointer', padding: '8px 16px', borderRadius: 8, border: schedForm.mode === m ? '2px solid #2b7cba' : '1px solid #e5e7eb', background: schedForm.mode === m ? '#f0f7ff' : '#fff', fontWeight: schedForm.mode === m ? 600 : 400, fontSize: '0.9rem' }}>
                      <input type="radio" name="mode" value={m} checked={schedForm.mode === m} onChange={() => setSchedForm({ ...schedForm, mode: m })} style={{ display: 'none' }} />
                      {m === 'online' ? '🖥 Online' : '🏢 Offline'}
                    </label>
                  ))}
                </div>
              </div>

              {schedForm.mode === 'online' && (
                <div className="input-group">
                  <label>Meeting Link *</label>
                  <input className="input" required placeholder="https://meet.google.com/..." value={schedForm.meeting_link} onChange={e => setSchedForm({ ...schedForm, meeting_link: e.target.value })} />
                </div>
              )}

              {schedForm.mode === 'offline' && (
                <div className="input-group">
                  <label>Location *</label>
                  <input className="input" required placeholder="Office address / Room number" value={schedForm.location} onChange={e => setSchedForm({ ...schedForm, location: e.target.value })} />
                </div>
              )}

              <div className="grid-2">
                <div className="input-group">
                  <label>Date & Time *</label>
                  <input className="input" type="datetime-local" required value={schedForm.scheduled_datetime} onChange={e => setSchedForm({ ...schedForm, scheduled_datetime: e.target.value })} />
                </div>
                <div className="input-group">
                  <label>Duration (min)</label>
                  <input className="input" type="number" min={15} max={240} value={schedForm.duration_minutes} onChange={e => setSchedForm({ ...schedForm, duration_minutes: parseInt(e.target.value) || 60 })} />
                </div>
              </div>

              <div className="input-group">
                <label>Notes (optional)</label>
                <textarea className="textarea" rows={2} placeholder="Any special instructions..." value={schedForm.notes} onChange={e => setSchedForm({ ...schedForm, notes: e.target.value })} />
              </div>

              {/* Send Email Section */}
              <div style={{ marginTop: 16, padding: 16, background: '#f8fafc', borderRadius: 10, border: '1px solid #e5e7eb' }}>
                <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer', marginBottom: schedForm.send_email ? 14 : 0 }}>
                  <input type="checkbox" checked={schedForm.send_email} onChange={e => setSchedForm({ ...schedForm, send_email: e.target.checked })} style={{ width: 16, height: 16 }} />
                  <span style={{ fontWeight: 600, fontSize: '0.9rem' }}>Send Email to Candidate</span>
                  {scheduleModal.candidate_email && <span style={{ fontSize: '0.8rem', color: '#6b7280' }}>({scheduleModal.candidate_email})</span>}
                </label>

                {schedForm.send_email && (
                  <>
                    <MailFromPicker stepKey="interview_scheduled" label="Send from"
                      value={schedMailboxId} onChange={setSchedMailboxId} />
                    <div className="input-group" style={{ marginBottom: 10 }}>
                      <label style={{ fontSize: '0.8rem' }}>Subject</label>
                      <input className="input" value={schedForm.email_subject} onChange={e => setSchedForm({ ...schedForm, email_subject: e.target.value })} />
                    </div>
                    <div className="input-group">
                      <label style={{ fontSize: '0.8rem' }}>Body <span className="text-muted">(use {'{{interview_type}}'}, {'{{datetime}}'}, {'{{mode}}'}, {'{{meeting_details}}'})</span></label>
                      <textarea className="textarea" rows={6} value={schedForm.email_body} onChange={e => setSchedForm({ ...schedForm, email_body: e.target.value })} />
                    </div>
                  </>
                )}
              </div>

              <div style={{ display: 'flex', gap: 12, justifyContent: 'flex-end', marginTop: 16 }}>
                <button type="button" className="btn btn-secondary" onClick={() => setScheduleModal(null)}>Cancel</button>
                <button type="submit" className="btn btn-primary" disabled={schedSubmitting}>
                  {schedSubmitting ? 'Scheduling...' : schedForm.send_email ? 'Schedule & Send Email' : 'Schedule Interview'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* ═══════════ HR INTERVIEW FEEDBACK MODAL ═══════════ */}
      {fbModal && (
        <div className="modal-overlay" onClick={() => setFbModal(null)} style={{ alignItems: 'flex-start', paddingTop: 30, overflowY: 'auto' }}>
          <div onClick={e => e.stopPropagation()} style={{ background: '#fff', borderRadius: 16, width: '95%', maxWidth: 560, padding: '28px 32px', boxShadow: '0 24px 80px rgba(0,0,0,0.15)', maxHeight: '90vh', overflowY: 'auto', marginBottom: 30 }}>
            <div className="flex-between" style={{ marginBottom: 6 }}>
              <h2 style={{ margin: 0, fontSize: '1.2rem' }}>Interview Feedback</h2>
              <button className="btn btn-ghost btn-sm" onClick={() => setFbModal(null)} style={{ fontSize: '1.2rem', padding: '4px 10px' }}>x</button>
            </div>
            <div className="text-sm text-muted" style={{ marginBottom: 18 }}>
              {fbModal.candidate_name} — {fbModal.job_title} · {fbModal.mode === 'online' ? '🖥 Online' : '🏢 Offline'}
            </div>

            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14 }}>
              {[
                ['technical_score', 'Technical'],
                ['communication_score', 'Communication'],
                ['problem_solving_score', 'Problem Solving'],
                ['cultural_fit_score', 'Cultural Fit'],
              ].map(([key, label]) => (
                <div className="input-group" key={key}>
                  <label>{label} <span style={{ color: 'var(--text-tertiary)', fontWeight: 400 }}>(0–100)</span></label>
                  <input className="input" type="number" min={0} max={100} value={hrFbForm[key]}
                    onChange={e => setHrFbForm({ ...hrFbForm, [key]: e.target.value })} />
                </div>
              ))}
            </div>

            <div className="input-group">
              <label>Strengths <span style={{ color: 'var(--text-tertiary)', fontWeight: 400 }}>(comma separated)</span></label>
              <input className="input" placeholder="Clear communicator, strong DSA" value={hrFbForm.strengths}
                onChange={e => setHrFbForm({ ...hrFbForm, strengths: e.target.value })} />
            </div>
            <div className="input-group">
              <label>Weaknesses <span style={{ color: 'var(--text-tertiary)', fontWeight: 400 }}>(comma separated)</span></label>
              <input className="input" placeholder="Limited system-design depth" value={hrFbForm.weaknesses}
                onChange={e => setHrFbForm({ ...hrFbForm, weaknesses: e.target.value })} />
            </div>
            <div className="input-group">
              <label>Recommendation</label>
              <select className="select" value={hrFbForm.hire_recommendation}
                onChange={e => setHrFbForm({ ...hrFbForm, hire_recommendation: e.target.value })}>
                <option value="strong_hire">Strong Hire</option>
                <option value="hire">Hire</option>
                <option value="proceed">Proceed</option>
                <option value="no_hire">No Hire</option>
                <option value="strong_no_hire">Strong No Hire</option>
              </select>
            </div>
            <div className="input-group">
              <label>Recommendations / summary</label>
              <textarea className="textarea" rows={2} value={hrFbForm.recommendations}
                onChange={e => setHrFbForm({ ...hrFbForm, recommendations: e.target.value })} />
            </div>
            <div className="input-group">
              <label>Additional notes <span style={{ color: 'var(--text-tertiary)', fontWeight: 400 }}>(optional)</span></label>
              <textarea className="textarea" rows={2} value={hrFbForm.additional_notes}
                onChange={e => setHrFbForm({ ...hrFbForm, additional_notes: e.target.value })} />
            </div>

            <div className="flex gap-12" style={{ justifyContent: 'flex-end', marginTop: 8 }}>
              <button type="button" className="btn btn-secondary" onClick={() => setFbModal(null)}>Cancel</button>
              <button type="button" className="btn btn-primary" onClick={submitHrFeedback} disabled={fbSaving}>
                {fbSaving ? 'Saving…' : 'Save Feedback'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ═══════════ FEEDBACK TAB ═══════════ */}
      {tab === 'feedback' && (() => {
        const fbJobGroups = {}
        feedbackData.forEach(f => {
          const key = f.job_id || 'unknown'
          if (!fbJobGroups[key]) fbJobGroups[key] = { title: f.job_title, job_id: key, feedbacks: [] }
          fbJobGroups[key].feedbacks.push(f)
        })
        // Build a title→group map as fallback for feedbacks with null job_id
        const fbTitleGroups = {}
        Object.values(fbJobGroups).forEach(g => {
          if (g.title && g.title !== '—') fbTitleGroups[g.title.toLowerCase().trim()] = g
        })
        return (
          <div>
            <div className="flex-between mb-24">
              <div>
                <h3 style={{ fontSize: '1.2rem' }}>Interview Feedback</h3>
                <span className="text-sm text-muted">Review interviewer feedback, then select candidates for offer</span>
              </div>
              <button className="btn btn-secondary btn-sm" onClick={() => api.get('/hr/feedback').then(r => setFeedbackData(r.data || [])).catch(() => {})} style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg>
                Refresh
              </button>
            </div>
            {jobs.length === 0 ? (
              <div className="card" style={{ padding: 60, textAlign: 'center', color: 'var(--text-tertiary)' }}>No jobs found</div>
            ) : (
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: 16 }}>
                {jobs.map(j => {
                  // Match by job_id first, fall back to matching by title
                  const group = fbJobGroups[j.id] || fbTitleGroups[j.title?.toLowerCase().trim()]
                  const fbCount = group ? group.feedbacks.length : 0
                  const hireCount = group ? group.feedbacks.filter(f => f.hire_recommendation === 'strong_hire' || f.hire_recommendation === 'hire').length : 0
                  return (
                    <div key={j.id} className="card" style={{ padding: '20px 22px', display: 'flex', flexDirection: 'column', gap: 14 }}>
                      <div className="flex-between">
                        <span className={`badge ${fbCount > 0 ? 'badge-green' : 'badge-neutral'}`}>
                          {fbCount > 0 ? `${fbCount} Feedback` : 'No Feedback'}
                        </span>
                        <span style={{ fontSize: '0.7rem', fontFamily: 'var(--font-mono)', color: 'var(--text-tertiary)' }}>
                          REQ-{j.id?.slice(0, 8)?.toUpperCase()}
                        </span>
                      </div>
                      <div>
                        <h3 style={{ fontSize: '1rem', margin: 0, marginBottom: 4 }}>{j.title}</h3>
                        <div className="text-sm text-muted">{j.department}</div>
                      </div>
                      {fbCount > 0 && (
                        <div className="flex-between" style={{ fontSize: '0.84rem' }}>
                          <span style={{ color: '#15803d', fontWeight: 600 }}>{hireCount} recommended</span>
                          <span className="text-muted">{fbCount - hireCount} others</span>
                        </div>
                      )}
                      {fbCount > 0 ? (
                        <button className="btn btn-primary btn-sm" style={{ width: '100%', justifyContent: 'center' }}
                          onClick={() => { setFeedbackJob(group); setFeedbackCandidates(group.feedbacks) }}>
                          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>
                          View Feedback
                        </button>
                      ) : (
                        <button className="btn btn-secondary btn-sm" style={{ width: '100%', justifyContent: 'center', opacity: 0.6 }} disabled>
                          No Feedback Yet
                        </button>
                      )}
                    </div>
                  )
                })}
              </div>
            )}
          </div>
        )
      })()}

      {/* Feedback Sheet Overlay */}
      {feedbackJob && (
        <div className="modal-overlay" onClick={() => { setFeedbackJob(null); setFeedbackCandidates([]) }} style={{ alignItems: 'flex-start', paddingTop: 40 }}>
          <div onClick={e => e.stopPropagation()} style={{
            background: '#fff', borderRadius: 18, width: '95%', maxWidth: 1100,
            maxHeight: '90vh', overflow: 'hidden', display: 'flex', flexDirection: 'column',
            boxShadow: '0 24px 80px rgba(0,0,0,0.15)', border: '1px solid var(--border-subtle)'
          }}>
            <div style={{ padding: '24px 28px 16px', borderBottom: '2px solid var(--border-subtle)', flexShrink: 0 }}>
              <div className="flex-between">
                <div>
                  <h2 style={{ margin: 0, fontSize: '1.3rem' }}>{feedbackJob.title} — Interview Feedback</h2>
                  <div className="text-sm text-muted" style={{ marginTop: 4 }}>{feedbackCandidates.length} feedback entries — select a candidate to send offer</div>
                </div>
                <button className="btn btn-ghost btn-sm" onClick={() => { setFeedbackJob(null); setFeedbackCandidates([]) }} style={{ fontSize: '1.2rem', padding: '6px 12px' }}>x</button>
              </div>
            </div>
            <div style={{ overflow: 'auto', flex: 1 }}>
              {feedbackCandidates.map((f, i) => {
                const avg = ((f.technical_score + f.communication_score + f.problem_solving_score + f.cultural_fit_score) / 4)
                const recColor = f.hire_recommendation === 'strong_hire' ? '#15803d' : f.hire_recommendation === 'hire' ? '#22c55e' : f.hire_recommendation === 'no_hire' ? '#ef4444' : '#dc2626'
                const recLabel = (f.hire_recommendation || '').replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase())
                return (
                  <div key={f.feedback_id} style={{ padding: '20px 28px', borderBottom: '1px solid #f3f4f6' }}>
                    <div className="flex-between" style={{ marginBottom: 12 }}>
                      <div>
                        <div style={{ fontWeight: 700, fontSize: '1rem' }}>{f.candidate_name}</div>
                        <div style={{ fontSize: '0.82rem', color: 'var(--text-secondary)' }}>{f.candidate_email} {f.candidate_phone ? `· ${f.candidate_phone}` : ''}</div>
                      </div>
                      <div className="flex gap-8">
                        <span style={{ padding: '4px 12px', borderRadius: 6, fontSize: '0.78rem', fontWeight: 700, color: recColor, background: `${recColor}12`, border: `1px solid ${recColor}30` }}>
                          {recLabel}
                        </span>
                        <button className="btn btn-primary btn-sm" onClick={() => setShowOfferModal(f)}>
                          <Gift size={14} /> Send Offer
                        </button>
                      </div>
                    </div>

                    {/* Score bars */}
                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5, 1fr)', gap: 12, marginBottom: 12 }}>
                      {[
                        { label: 'Technical', val: f.technical_score, color: '#3b82f6' },
                        { label: 'Communication', val: f.communication_score, color: '#8b5cf6' },
                        { label: 'Problem Solving', val: f.problem_solving_score, color: '#2b7cba' },
                        { label: 'Cultural Fit', val: f.cultural_fit_score, color: '#22c55e' },
                        { label: 'Average', val: avg, color: '#1a1d23' },
                      ].map(s => (
                        <div key={s.label} style={{ textAlign: 'center' }}>
                          <div style={{ width: 48, height: 48, borderRadius: '50%', border: `3px solid ${s.color}`, display: 'inline-grid', placeItems: 'center', fontWeight: 800, fontSize: '0.9rem', color: s.color, background: `${s.color}08` }}>
                            {Math.round(s.val)}
                          </div>
                          <div style={{ fontSize: '0.68rem', color: '#9ca3af', marginTop: 4, fontFamily: 'var(--font-display)', fontWeight: 600 }}>{s.label}</div>
                        </div>
                      ))}
                    </div>

                    {/* Strengths & Weaknesses */}
                    <div className="grid-2" style={{ gap: 12 }}>
                      <div style={{ padding: '10px 14px', background: '#f0fdf4', borderRadius: 8, fontSize: '0.84rem' }}>
                        <div style={{ fontWeight: 700, fontSize: '0.7rem', textTransform: 'uppercase', color: '#15803d', marginBottom: 4 }}>Strengths</div>
                        <div style={{ color: '#166534', lineHeight: 1.5 }}>{(() => { try { const arr = JSON.parse(f.strengths); return Array.isArray(arr) ? arr.join(', ') : f.strengths } catch { return f.strengths || '—' } })()}</div>
                      </div>
                      <div style={{ padding: '10px 14px', background: '#fef2f2', borderRadius: 8, fontSize: '0.84rem' }}>
                        <div style={{ fontWeight: 700, fontSize: '0.7rem', textTransform: 'uppercase', color: '#dc2626', marginBottom: 4 }}>Weaknesses</div>
                        <div style={{ color: '#991b1b', lineHeight: 1.5 }}>{(() => { try { const arr = JSON.parse(f.weaknesses); return Array.isArray(arr) ? arr.join(', ') : f.weaknesses } catch { return f.weaknesses || '—' } })()}</div>
                      </div>
                    </div>
                    {f.recommendations && (
                      <div style={{ marginTop: 8, padding: '8px 14px', background: '#f9fafb', borderRadius: 8, fontSize: '0.82rem', color: '#6b7280' }}>
                        <strong>Notes:</strong> {f.recommendations} {f.additional_notes ? ` — ${f.additional_notes}` : ''}
                      </div>
                    )}
                  </div>
                )
              })}
            </div>
          </div>
        </div>
      )}

      {/* Offer Letter Modal — 2 Step: Details → Email Preview */}
      {showOfferModal && (
        <div className="modal-overlay" onClick={() => { setShowOfferModal(null); setOfferStep(1) }} style={{ zIndex: 1100 }}>
          <div className="modal" onClick={e => e.stopPropagation()} style={{ maxWidth: 680, maxHeight: '90vh', overflow: 'auto' }}>

            {/* Step indicator */}
            <div className="flex gap-8" style={{ marginBottom: 20 }}>
              <button className={`btn btn-sm ${offerStep === 1 ? 'btn-primary' : 'btn-ghost'}`} onClick={() => setOfferStep(1)}>1. Offer Details</button>
              <button className={`btn btn-sm ${offerStep === 2 ? 'btn-primary' : 'btn-ghost'}`} disabled={!offerForm.salary}>2. Preview Email</button>
            </div>

            {/* Candidate info bar */}
            <div style={{ padding: '12px 16px', background: '#eff6ff', borderRadius: 10, marginBottom: 20, fontSize: '0.84rem', color: '#2563eb' }}>
              <strong>To:</strong> {showOfferModal.candidate_name} ({showOfferModal.candidate_email})
              <br /><strong>Job:</strong> {showOfferModal.job_title}
              · <strong>Recommendation:</strong> {(showOfferModal.hire_recommendation || '').replace(/_/g, ' ')}
              · <strong>Avg Score:</strong> {Math.round((showOfferModal.technical_score + showOfferModal.communication_score + showOfferModal.problem_solving_score + showOfferModal.cultural_fit_score) / 4)}
            </div>

            {/* Step 1: Offer Details */}
            {offerStep === 1 && (
              <div className="auth-form">

                {/* Template Selector */}
                <div className="input-group" style={{ marginBottom: 20 }}>
                  <label style={{ marginBottom: 10, display: 'block', fontWeight: 600, color: '#374151' }}>
                    Select Template <span style={{ fontWeight: 400, color: '#9ca3af', fontSize: '0.8rem' }}>(auto-fills form fields)</span>
                  </label>
                  <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
                    {OFFER_TEMPLATES.map(t => (
                      <button
                        key={t.label}
                        type="button"
                        onClick={() => setOfferForm(prev => ({ ...prev, offer_type: t.offer_type, salary: t.salary, bonus: t.bonus, terms: t.terms }))}
                        style={{
                          display: 'flex', alignItems: 'center', gap: 6,
                          padding: '8px 14px', borderRadius: 8, border: '1.5px solid #e5e7eb',
                          background: offerForm.offer_type === t.offer_type && offerForm.terms === t.terms ? '#eff6ff' : '#fff',
                          borderColor: offerForm.offer_type === t.offer_type && offerForm.terms === t.terms ? '#3b82f6' : '#e5e7eb',
                          color: offerForm.offer_type === t.offer_type && offerForm.terms === t.terms ? '#2563eb' : '#374151',
                          fontSize: '0.82rem', fontWeight: 500, cursor: 'pointer',
                          transition: 'all 0.15s ease',
                          boxShadow: offerForm.offer_type === t.offer_type && offerForm.terms === t.terms ? '0 0 0 3px #dbeafe' : 'none'
                        }}
                      >
                        <span>{t.icon}</span>
                        {t.label}
                      </button>
                    ))}
                  </div>
                </div>

                <div className="grid-2">
                  <div className="input-group">
                    <label>Offer Type</label>
                    <select className="select" value={offerForm.offer_type} onChange={e => setOfferForm({ ...offerForm, offer_type: e.target.value })}>
                      <option value="full_time">Full Time</option>
                      <option value="contract">Contract</option>
                      <option value="internship">Internship</option>
                    </select>
                  </div>
                  <div className="input-group">
                    <label>Salary (Annual)</label>
                    <input className="input" type="number" required placeholder="e.g. 800000" value={offerForm.salary} onChange={e => setOfferForm({ ...offerForm, salary: e.target.value })} />
                  </div>
                </div>
                <div className="grid-2">
                  <div className="input-group">
                    <label>Bonus</label>
                    <input className="input" type="number" placeholder="Optional" value={offerForm.bonus} onChange={e => setOfferForm({ ...offerForm, bonus: e.target.value })} />
                  </div>
                  <div className="input-group">
                    <label>Start Date</label>
                    <input className="input" type="date" required value={offerForm.start_date} onChange={e => setOfferForm({ ...offerForm, start_date: e.target.value })} />
                  </div>
                </div>
                <div className="input-group">
                  <label>Offer Expiry Date</label>
                  <input className="input" type="date" required value={offerForm.expiry_date} onChange={e => setOfferForm({ ...offerForm, expiry_date: e.target.value })} />
                </div>
                <div className="input-group">
                  <label>Terms & Conditions</label>
                  <textarea className="textarea" rows={3} required placeholder="Offer terms, probation period, benefits..." value={offerForm.terms} onChange={e => setOfferForm({ ...offerForm, terms: e.target.value })} />
                </div>
                <div className="flex gap-12" style={{ justifyContent: 'flex-end', marginTop: 8 }}>
                  <button type="button" className="btn btn-secondary" onClick={() => { setShowOfferModal(null); setOfferStep(1) }}>Cancel</button>
                  <button type="button" className="btn btn-primary" disabled={!offerForm.salary || !offerForm.start_date || !offerForm.expiry_date}
                    onClick={() => {
                      const name = showOfferModal.candidate_name
                      const job = showOfferModal.job_title
                      setOfferEmail({
                        subject: `Offer Letter – ${job}`,
                        body: `Dear ${name},\n\nWe are pleased to extend an offer of employment for the position of ${job}.\n\nPosition: ${job}\nEmployment Type: ${offerForm.offer_type.replace(/_/g, ' ')}\nAnnual Salary: ₹${Number(offerForm.salary).toLocaleString()}\n${offerForm.bonus ? `Signing Bonus: ₹${Number(offerForm.bonus).toLocaleString()}\n` : ''}Start Date: ${offerForm.start_date}\nOffer Expires: ${offerForm.expiry_date}\n\nTerms & Conditions:\n${offerForm.terms}\n\nPlease respond to this offer by the expiry date using the candidate portal.\n\nCongratulations and welcome to the team!\nHireFlow Recruitment Team`
                      })
                      setOfferStep(2)
                    }}>
                    Next: Preview Email
                  </button>
                </div>
              </div>
            )}

            {/* Step 2: Email Preview & Edit */}
            {offerStep === 2 && (
              <div>
                {/* Email Preview Card */}
                <div style={{ border: '1px solid #e5e7eb', borderRadius: 12, overflow: 'hidden', marginBottom: 20 }}>
                  {/* Email header */}
                  <div style={{ padding: '14px 20px', background: '#f9fafb', borderBottom: '1px solid #e5e7eb' }}>
                    <div style={{ display: 'grid', gridTemplateColumns: '60px 1fr', gap: '6px 12px', fontSize: '0.84rem' }}>
                      <span style={{ color: '#9ca3af', fontWeight: 600 }}>To:</span>
                      <span>{showOfferModal.candidate_name} &lt;{showOfferModal.candidate_email}&gt;</span>
                    </div>
                  </div>
                  {/* Sender + subject. The From address used to be hardcoded here; it
                      now reports the mailbox this offer will really go out from. */}
                  <div style={{ padding: '12px 20px', borderBottom: '1px solid #f3f4f6' }}>
                    <MailFromPicker stepKey="offer_letter" label="From"
                      value={offerMailboxId} onChange={setOfferMailboxId} />
                    <div className="input-group" style={{ marginBottom: 0 }}>
                      <label>Subject</label>
                      <input className="input" value={offerEmail.subject} onChange={e => setOfferEmail({ ...offerEmail, subject: e.target.value })} />
                    </div>
                  </div>
                  {/* Body */}
                  <div style={{ padding: '16px 20px' }}>
                    <div className="input-group" style={{ marginBottom: 0 }}>
                      <label>Email Body</label>
                      <textarea className="textarea" rows={14} value={offerEmail.body}
                        onChange={e => setOfferEmail({ ...offerEmail, body: e.target.value })}
                        style={{ fontSize: '0.88rem', lineHeight: 1.7, fontFamily: 'var(--font-body)' }} />
                    </div>
                  </div>
                </div>

                {/* Offer Summary */}
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 10, marginBottom: 20 }}>
                  <div style={{ padding: '10px 14px', background: '#f0fdf4', borderRadius: 8, textAlign: 'center' }}>
                    <div style={{ fontSize: '0.68rem', fontWeight: 700, textTransform: 'uppercase', color: '#15803d' }}>Salary</div>
                    <div style={{ fontSize: '1.1rem', fontWeight: 800, color: '#166534' }}>₹{Number(offerForm.salary || 0).toLocaleString()}</div>
                  </div>
                  <div style={{ padding: '10px 14px', background: '#eff6ff', borderRadius: 8, textAlign: 'center' }}>
                    <div style={{ fontSize: '0.68rem', fontWeight: 700, textTransform: 'uppercase', color: '#2563eb' }}>Start Date</div>
                    <div style={{ fontSize: '0.9rem', fontWeight: 600, color: '#1d4ed8' }}>{offerForm.start_date || '—'}</div>
                  </div>
                  <div style={{ padding: '10px 14px', background: '#fef3eb', borderRadius: 8, textAlign: 'center' }}>
                    <div style={{ fontSize: '0.68rem', fontWeight: 700, textTransform: 'uppercase', color: '#c2660f' }}>Expires</div>
                    <div style={{ fontSize: '0.9rem', fontWeight: 600, color: '#b45309' }}>{offerForm.expiry_date || '—'}</div>
                  </div>
                </div>

                <div className="flex gap-12" style={{ justifyContent: 'space-between' }}>
                  <button type="button" className="btn btn-secondary" onClick={() => setOfferStep(1)}>Back to Details</button>
                  <div className="flex gap-8">
                    <button type="button" className="btn btn-secondary" onClick={() => { setShowOfferModal(null); setOfferStep(1) }}>Cancel</button>
                    <button type="button" className="btn btn-primary" onClick={handleSendOffer}>
                      <Gift size={16} /> Send Offer Letter
                    </button>
                  </div>
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      {showModal && (
        <div className="modal-overlay" onClick={() => { setShowModal(false); setEditJob(null); setIsApprovalMode(false); setPendingApprovalReqId(null) }}>
          <div className="modal" onClick={e => e.stopPropagation()}>
            <h2>{isApprovalMode ? 'Approve Job Posting' : editJob ? 'Edit Job Posting' : pendingApprovalReqId ? 'Create & Approve Job' : 'Create Job Posting'}</h2>
            <form className="auth-form" onSubmit={editJob ? handleUpdateJob : handleCreateJob}>
              <div className="input-group">
                <label>Job Title *</label>
                <input className="input" placeholder="e.g. Senior Frontend Developer" required
                  value={jobForm.title} onChange={e => setJobForm({ ...jobForm, title: e.target.value })} />
              </div>
              <div className="grid-2">
                <div className="input-group">
                  <label>Department *</label>
                  <select className="select" required value={jobForm.department_id}
                    onChange={e => setJobForm({ ...jobForm, department_id: e.target.value })}>
                    <option value="">Select department...</option>
                    {departments.map(d => (
                      <option key={d.id} value={d.id}>{d.name}</option>
                    ))}
                  </select>
                </div>
                <div className="input-group">
                  <label>Location *</label>
                  <input className="input" placeholder="Remote / Bangalore / NYC" required
                    value={jobForm.location} onChange={e => setJobForm({ ...jobForm, location: e.target.value })} />
                </div>
              </div>
              <div className="grid-2">
                <div className="input-group">
                  <label>Employment Type</label>
                  <select className="select" value={jobForm.employment_type}
                    onChange={e => setJobForm({ ...jobForm, employment_type: e.target.value })}>
                    <option value="full-time">Full-time</option>
                    <option value="part-time">Part-time</option>
                    <option value="contract">Contract</option>
                    <option value="internship">Internship</option>
                  </select>
                </div>
                <div className="input-group">
                  <label>Experience (years)</label>
                  <input className="input" type="number" min={0} value={jobForm.experience_years}
                    onChange={e => setJobForm({ ...jobForm, experience_years: e.target.value })} />
                </div>
              </div>
              <div className="grid-2">
                <div className="input-group">
                  <label>Salary Min</label>
                  <input className="input" type="number" placeholder="e.g. 500000"
                    value={jobForm.salary_min} onChange={e => setJobForm({ ...jobForm, salary_min: e.target.value })} />
                </div>
                <div className="input-group">
                  <label>Salary Max</label>
                  <input className="input" type="number" placeholder="e.g. 900000"
                    value={jobForm.salary_max} onChange={e => setJobForm({ ...jobForm, salary_max: e.target.value })} />
                </div>
              </div>
              <div className="input-group">
                <label>Description *</label>
                <textarea className="textarea" placeholder="Job description..." rows={3} required
                  value={jobForm.description} onChange={e => setJobForm({ ...jobForm, description: e.target.value })} />
              </div>
              <div className="input-group">
                <label>Requirements *</label>
                <textarea className="textarea" placeholder="Job requirements..." rows={2} required
                  value={jobForm.requirements} onChange={e => setJobForm({ ...jobForm, requirements: e.target.value })} />
              </div>
              <div className="input-group">
                <label>Required Skills * (comma-separated)</label>
                <input className="input" placeholder="React, TypeScript, Node.js..." required
                  value={jobForm.required_skills} onChange={e => setJobForm({ ...jobForm, required_skills: e.target.value })} />
              </div>
              <div className="flex gap-12" style={{ justifyContent: 'flex-end', marginTop: 8 }}>
                <button type="button" className="btn btn-secondary" onClick={() => { setShowModal(false); setEditJob(null); setIsApprovalMode(false); setPendingApprovalReqId(null) }}>Cancel</button>
                <button type="submit" className="btn btn-primary" disabled={submitting}>
                  {submitting ? 'Saving...' : isApprovalMode ? 'Approve & Publish' : editJob ? 'Update Job' : pendingApprovalReqId ? 'Create & Approve' : 'Create Posting'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* ═══════════ EMAIL PREVIEW & EDIT ═══════════ */}
      {showEmailPreview && (() => {
        // Manual Select sends no rejections; Top-N / Top-% auto-reject the remaining unsent
        // candidates (already-emailed candidates are never counted or re-emailed).
        const manualMode = selectionMode === 'manual'
        const rejectCount = manualMode ? 0 : atsCandidates.filter(c => !isMailSent(c) && !selectedCandidates.has(c.candidate_id)).length
        return (
        <div className="modal-overlay" onClick={() => setShowEmailPreview(false)} style={{ zIndex: 1100 }}>
          <div className="modal" onClick={e => e.stopPropagation()} style={{ maxWidth: 750, maxHeight: '90vh', overflow: 'auto' }}>
            <h2 style={{ margin: 0, marginBottom: 20, paddingBottom: 16, borderBottom: '2px solid var(--border-subtle)' }}>
              Review Emails Before Sending
            </h2>

            {/* Summary */}
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginBottom: 24 }}>
              <div style={{ padding: 14, background: '#ecfdf5', borderRadius: 10, border: '1px solid #bbf7d0' }}>
                <div style={{ fontSize: '0.72rem', fontWeight: 700, textTransform: 'uppercase', color: '#15803d', marginBottom: 4 }}>Test Invite</div>
                <div style={{ fontSize: '1.3rem', fontWeight: 800, color: '#15803d' }}>{selectedCandidates.size}</div>
                <div style={{ fontSize: '0.8rem', color: '#16a34a' }}>candidates will receive test link</div>
              </div>
              <div style={{ padding: 14, background: manualMode ? '#f9fafb' : '#fef2f2', borderRadius: 10, border: `1px solid ${manualMode ? '#e5e7eb' : '#fecaca'}` }}>
                <div style={{ fontSize: '0.72rem', fontWeight: 700, textTransform: 'uppercase', color: manualMode ? '#6b7280' : '#dc2626', marginBottom: 4 }}>Auto Reject</div>
                <div style={{ fontSize: '1.3rem', fontWeight: 800, color: manualMode ? '#6b7280' : '#dc2626' }}>{rejectCount}</div>
                <div style={{ fontSize: '0.8rem', color: manualMode ? '#9ca3af' : '#ef4444' }}>{manualMode ? 'manual select sends no rejections' : 'candidates will receive rejection'}</div>
              </div>
            </div>

            {/* Assign Test */}
            <div style={{ padding: 16, background: '#eff6ff', borderRadius: 12, border: '1px solid #bfdbfe', marginBottom: 20 }}>
              <div style={{ fontSize: '0.78rem', fontWeight: 700, textTransform: 'uppercase', color: '#2563eb', marginBottom: 10, fontFamily: 'var(--font-display)' }}>
                Assign Test (created by interviewer)
              </div>
              {jobTests.length > 0 ? (
                <div>
                  <select className="select" value={selectedTestId} onChange={e => onSelectTest(e.target.value)}
                    style={{ marginBottom: 8 }}>
                    <option value="">No test — send link only</option>
                    {jobTests.filter(t => t.status !== 'DRAFT').map(t => (
                      <option key={t.id} value={t.id}>
                        {t.title} ({t.interview_type} · {t.difficulty_level} · {t.duration_minutes}min · {t.status})
                      </option>
                    ))}
                  </select>
                  {selectedTestId && (
                    <div style={{ fontSize: '0.8rem', color: '#2563eb', padding: '6px 10px', background: '#dbeafe', borderRadius: 6 }}>
                      This test will be cloned and assigned to each selected candidate. They will see it in "My Interviews".
                    </div>
                  )}
                  {!selectedTestId && (
                    <div style={{ fontSize: '0.78rem', color: '#6b7280' }}>
                      No tests available? Ask the interviewer to create one via "Create Test" tab and link it to this job.
                    </div>
                  )}
                </div>
              ) : (
                <div style={{ fontSize: '0.84rem', color: '#6b7280' }}>
                  No tests created for this job yet. The assigned interviewer needs to create a test via "Create Test" and link it to this job.
                </div>
              )}

              {/* Proctoring the interviewer configured on this test. Editable here, but the
                  override applies to these invites only — the interviewer's test is untouched. */}
              {selectedTestId && proctorCfg && (
                <div style={{ marginTop: 14, paddingTop: 14, borderTop: '1px dashed #bfdbfe' }}>
                  <div style={{ fontSize: '0.78rem', fontWeight: 700, textTransform: 'uppercase', color: '#2563eb', marginBottom: 4, fontFamily: 'var(--font-display)' }}>
                    Proctoring for this send
                  </div>
                  <div style={{ fontSize: '0.76rem', color: '#3b82f6', marginBottom: 10 }}>
                    Configured by the interviewer when the test was created. Adjust for these candidates only.
                  </div>

                  <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                    {PROCTOR_FIELDS.map(f => {
                      const changed = proctorCfg[f.key] !== proctorBaseline[f.key]
                      return (
                        <label key={f.key} style={{
                          display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer',
                          padding: '8px 10px', borderRadius: 8,
                          border: `1.5px solid ${changed ? '#f59e0b' : '#bfdbfe'}`,
                          background: changed ? '#fffbeb' : '#fff',
                        }}>
                          <input type="checkbox" checked={proctorCfg[f.key]}
                            onChange={e => {
                              setProctorCfg({ ...proctorCfg, [f.key]: e.target.checked })
                              setProctorApplied(false)
                            }} />
                          <span style={{ fontSize: '0.85rem', fontWeight: 700, color: '#1e3a8a' }}>{f.label}</span>
                          {changed && (
                            <span style={{ fontSize: '0.72rem', fontWeight: 700, color: '#b45309' }}>
                              changed from {proctorBaseline[f.key] ? 'on' : 'off'}
                            </span>
                          )}
                        </label>
                      )
                    })}
                  </div>

                  {/* Exam length for this send. The candidate's clock is the sum of their
                      questions' time limits, so this edits those rather than a bare total. */}
                  {Object.keys(timeCountsForRound).length > 0 && (
                    <div style={{ marginTop: 14, paddingTop: 12, borderTop: '1px dashed #bfdbfe' }}>
                      <div style={{ fontSize: '0.78rem', fontWeight: 700, textTransform: 'uppercase', color: '#2563eb', marginBottom: 4, fontFamily: 'var(--font-display)' }}>
                        Exam length for this send
                      </div>
                      <div style={{ fontSize: '0.76rem', color: '#3b82f6', marginBottom: 10 }}>
                        Minutes allowed per question. Applies to these candidates only.
                      </div>
                      {Object.entries(timeCountsForRound).map(([type, n]) => {
                        const cur = timeCfg?.[type] ?? TYPE_TIME_FALLBACK[type] ?? 5
                        const base = timeBaseline?.[type] ?? TYPE_TIME_FALLBACK[type] ?? 5
                        const changed = cur !== base
                        return (
                          <div key={type} style={{
                            display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6,
                            padding: '6px 10px', borderRadius: 8,
                            border: `1.5px solid ${changed ? '#f59e0b' : '#bfdbfe'}`,
                            background: changed ? '#fffbeb' : '#fff',
                          }}>
                            <span style={{ flex: 1, fontSize: '0.85rem', fontWeight: 700, color: '#1e3a8a', textTransform: 'capitalize' }}>
                              {type.replace(/_/g, ' ')} <span style={{ fontWeight: 400, color: '#3b82f6' }}>× {n}</span>
                            </span>
                            <input className="input" type="number" min={1} style={{ width: 80 }}
                              value={cur}
                              onChange={e => {
                                setTimeCfg({ ...(timeCfg || {}), [type]: parseInt(e.target.value) || 1 })
                                setProctorApplied(false)
                              }} />
                            <span style={{ fontSize: '0.76rem', color: '#3b82f6' }}>min</span>
                            {changed && (
                              <span style={{ fontSize: '0.72rem', fontWeight: 700, color: '#b45309' }}>was {base}</span>
                            )}
                          </div>
                        )
                      })}
                      <div style={{ fontSize: '0.82rem', fontWeight: 700, color: '#1e3a8a', marginTop: 8 }}>
                        Total: {sendDuration(timeCfg)} min
                        {timeChangedTypes.length > 0 && (
                          <span style={{ fontWeight: 400, color: '#b45309' }}>
                            {' '}(was {sendDuration(timeBaseline)} min)
                          </span>
                        )}
                      </div>
                    </div>
                  )}

                  <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginTop: 10, flexWrap: 'wrap' }}>
                    <button className="btn btn-secondary"
                      style={{ padding: '6px 14px', fontSize: '0.82rem' }}
                      disabled={proctorChangedKeys.length === 0 && timeChangedTypes.length === 0}
                      onClick={() => setProctorApplied(true)}>
                      Reconfigure
                    </button>
                    {proctorUnapplied && (
                      <span style={{ fontSize: '0.78rem', fontWeight: 700, color: '#b45309' }}>
                        {proctorChangedKeys.length + timeChangedTypes.length} change
                        {(proctorChangedKeys.length + timeChangedTypes.length) > 1 ? 's' : ''} pending
                      </span>
                    )}
                    {proctorApplied && (proctorChangedKeys.length + timeChangedTypes.length) > 0 && (
                      <>
                        <span style={{ fontSize: '0.78rem', fontWeight: 700, color: '#15803d' }}>
                          ✓ Override applied to this send
                        </span>
                        <button onClick={() => { setProctorCfg(proctorBaseline); setTimeCfg(timeBaseline); setProctorApplied(false) }}
                          style={{ background: 'none', border: 'none', padding: 0, cursor: 'pointer', fontSize: '0.78rem', color: '#2563eb', textDecoration: 'underline' }}>
                          Reset to test defaults
                        </button>
                      </>
                    )}
                  </div>

                  {proctorUnapplied && (
                    <div style={{ fontSize: '0.78rem', color: '#92400e', background: '#fef3c7', border: '1px solid #fde68a', borderRadius: 6, padding: '6px 10px', marginTop: 8 }}>
                      You have unapplied proctoring changes. Click <strong>Reconfigure</strong> to apply them, or untick to discard.
                    </div>
                  )}
                </div>
              )}

              {/* Test structure: single combined test, or split into MCQ → Coding rounds */}
              {selectedTestId && (
                <div style={{ marginTop: 12, paddingTop: 12, borderTop: '1px dashed #bfdbfe' }}>
                  <div style={{ fontSize: '0.74rem', fontWeight: 700, textTransform: 'uppercase', color: '#2563eb', marginBottom: 8 }}>
                    Test Structure
                  </div>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                    {[
                      { v: 'combined', t: 'MCQ + Coding in one test', d: 'Candidate takes all questions (MCQ and coding) in a single sitting.' },
                      { v: 'two_round', t: 'Two rounds — Round 1: MCQ, Round 2: Coding', d: 'Send MCQ now. After reviewing scores, advance shortlisted candidates to the Coding round.' },
                    ].map(opt => (
                      <label key={opt.v} style={{ display: 'flex', gap: 10, alignItems: 'flex-start', cursor: 'pointer',
                        padding: 10, borderRadius: 8, border: `1.5px solid ${testStructure === opt.v ? '#2563eb' : '#bfdbfe'}`,
                        background: testStructure === opt.v ? '#dbeafe' : '#fff' }}>
                        <input type="radio" name="testStructure" value={opt.v} checked={testStructure === opt.v}
                          onChange={() => setTestStructure(opt.v)} style={{ marginTop: 3 }} />
                        <span>
                          <span style={{ fontSize: '0.85rem', fontWeight: 700, color: '#1e3a8a' }}>{opt.t}</span>
                          <span style={{ display: 'block', fontSize: '0.76rem', color: '#3b82f6' }}>{opt.d}</span>
                        </span>
                      </label>
                    ))}
                  </div>
                  {testStructure === 'two_round' && (
                    <div style={{ fontSize: '0.78rem', color: '#92400e', background: '#fef3c7', border: '1px solid #fde68a', borderRadius: 6, padding: '6px 10px', marginTop: 8 }}>
                      This invite sends <strong>Round 1 (MCQ only)</strong>. Coding questions are held back for Round 2.
                    </div>
                  )}
                </div>
              )}
            </div>

            {/* Test Link */}
            <div className="input-group" style={{ marginBottom: 20 }}>
              <label>Test Link (in email)</label>
              <input className="input" value={emailTemplates.testLink}
                onChange={e => setEmailTemplates({ ...emailTemplates, testLink: e.target.value })} />
              <span style={{ fontSize: '0.75rem', color: 'var(--text-tertiary)' }}>This URL will replace {'{{test_link}}'} in the invite email</span>
            </div>

            {/* Invite Email Template */}
            <div style={{ padding: 20, background: '#f0fdf4', borderRadius: 12, border: '1px solid #bbf7d0', marginBottom: 20 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
                <div style={{ fontSize: '0.78rem', fontWeight: 700, textTransform: 'uppercase', color: '#15803d', fontFamily: 'var(--font-display)' }}>
                  Test Invitation Email
                </div>
              </div>
              {/* Invite template button */}
              {mailTemplates.filter(t => t.stage === 'interview_invite').length > 0 && (
                <div style={{ position: 'relative', display: 'inline-block', marginBottom: 12 }}>
                  <button
                    onClick={() => { setInviteTplOpen(v => !v); setRejectTplOpen(false) }}
                    style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '6px 14px', borderRadius: 8, border: '1.5px solid #93c5fd', background: '#eff6ff', color: '#2563eb', fontSize: '0.8rem', fontWeight: 600, cursor: 'pointer' }}
                  >
                    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect x="2" y="4" width="20" height="16" rx="2"/><path d="m22 7-8.97 5.7a1.94 1.94 0 0 1-2.06 0L2 7"/></svg>
                    Use Template
                    <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" style={{ transform: inviteTplOpen ? 'rotate(180deg)' : 'none', transition: '0.15s' }}><polyline points="6 9 12 15 18 9"/></svg>
                  </button>
                  {inviteTplOpen && (
                    <div style={{ position: 'absolute', top: '110%', left: 0, background: '#fff', border: '1px solid #e5e7eb', borderRadius: 10, boxShadow: '0 8px 24px rgba(0,0,0,0.12)', zIndex: 50, minWidth: 220, overflow: 'hidden' }}>
                      {mailTemplates.filter(t => t.stage === 'interview_invite').map(tpl => (
                        <button key={tpl.id}
                          onClick={() => { setEmailTemplates(prev => ({ ...prev, inviteSubject: tpl.subject, inviteBody: tpl.body })); setInviteTplOpen(false) }}
                          style={{ display: 'block', width: '100%', textAlign: 'left', padding: '10px 14px', border: 'none', background: 'none', cursor: 'pointer', fontSize: '0.84rem', color: '#1e293b', borderBottom: '1px solid #f3f4f6' }}
                          onMouseOver={e => e.currentTarget.style.background = '#eff6ff'}
                          onMouseOut={e => e.currentTarget.style.background = 'none'}
                        >
                          <div style={{ fontWeight: 600 }}>{tpl.name}</div>
                          <div style={{ fontSize: '0.72rem', color: '#6b7280', marginTop: 2 }}>{tpl.subject}</div>
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              )}
              <MailFromPicker stepKey="test_invitation" label="Send invite from"
                value={inviteMailboxId} onChange={setInviteMailboxId} />
              <div className="input-group" style={{ marginBottom: 12 }}>
                <label>Subject</label>
                <input className="input" value={emailTemplates.inviteSubject}
                  onChange={e => setEmailTemplates({ ...emailTemplates, inviteSubject: e.target.value })} />
              </div>
              <div className="input-group">
                <label>Body</label>
                <textarea className="textarea" rows={8} value={emailTemplates.inviteBody}
                  onChange={e => setEmailTemplates({ ...emailTemplates, inviteBody: e.target.value })}
                  style={{ fontSize: '0.88rem', lineHeight: 1.7 }} />
                <span style={{ fontSize: '0.72rem', color: 'var(--text-tertiary)' }}>
                  Use {'{{test_link}}'} for the test URL, {'{{candidate_name}}'} for name, {'{{deadline}}'} for deadline
                </span>
              </div>
            </div>

            {/* Rejection Email Template — hidden in Manual Select (no rejections are sent) */}
            {!manualMode && (
            <div style={{ padding: 20, background: '#fef2f2', borderRadius: 12, border: '1px solid #fecaca', marginBottom: 24 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
                <div style={{ fontSize: '0.78rem', fontWeight: 700, textTransform: 'uppercase', color: '#dc2626', fontFamily: 'var(--font-display)' }}>
                  Rejection Email
                </div>
              </div>
              {/* Rejection template button */}
              {mailTemplates.filter(t => t.stage === 'rejection').length > 0 && (
                <div style={{ position: 'relative', display: 'inline-block', marginBottom: 12 }}>
                  <button
                    onClick={() => { setRejectTplOpen(v => !v); setInviteTplOpen(false) }}
                    style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '6px 14px', borderRadius: 8, border: '1.5px solid #fca5a5', background: '#fef2f2', color: '#dc2626', fontSize: '0.8rem', fontWeight: 600, cursor: 'pointer' }}
                  >
                    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect x="2" y="4" width="20" height="16" rx="2"/><path d="m22 7-8.97 5.7a1.94 1.94 0 0 1-2.06 0L2 7"/></svg>
                    Use Template
                    <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" style={{ transform: rejectTplOpen ? 'rotate(180deg)' : 'none', transition: '0.15s' }}><polyline points="6 9 12 15 18 9"/></svg>
                  </button>
                  {rejectTplOpen && (
                    <div style={{ position: 'absolute', top: '110%', left: 0, background: '#fff', border: '1px solid #e5e7eb', borderRadius: 10, boxShadow: '0 8px 24px rgba(0,0,0,0.12)', zIndex: 50, minWidth: 220, overflow: 'hidden' }}>
                      {mailTemplates.filter(t => t.stage === 'rejection').map(tpl => (
                        <button key={tpl.id}
                          onClick={() => { setEmailTemplates(prev => ({ ...prev, rejectSubject: tpl.subject, rejectBody: tpl.body })); setRejectTplOpen(false) }}
                          style={{ display: 'block', width: '100%', textAlign: 'left', padding: '10px 14px', border: 'none', background: 'none', cursor: 'pointer', fontSize: '0.84rem', color: '#1e293b', borderBottom: '1px solid #f3f4f6' }}
                          onMouseOver={e => e.currentTarget.style.background = '#fef2f2'}
                          onMouseOut={e => e.currentTarget.style.background = 'none'}
                        >
                          <div style={{ fontWeight: 600 }}>{tpl.name}</div>
                          <div style={{ fontSize: '0.72rem', color: '#6b7280', marginTop: 2 }}>{tpl.subject}</div>
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              )}
              <MailFromPicker stepKey="candidate_rejected" label="Send rejection from"
                value={rejectMailboxId} onChange={setRejectMailboxId} />
              <div className="input-group" style={{ marginBottom: 12 }}>
                <label>Subject</label>
                <input className="input" value={emailTemplates.rejectSubject}
                  onChange={e => setEmailTemplates({ ...emailTemplates, rejectSubject: e.target.value })} />
              </div>
              <div className="input-group">
                <label>Body</label>
                <textarea className="textarea" rows={6} value={emailTemplates.rejectBody}
                  onChange={e => setEmailTemplates({ ...emailTemplates, rejectBody: e.target.value })}
                  style={{ fontSize: '0.88rem', lineHeight: 1.7 }} />
              </div>
            </div>
            )}

            {/* Actions */}
            <div style={{ display: 'flex', gap: 12, justifyContent: 'flex-end', borderTop: '1px solid var(--border-subtle)', paddingTop: 16 }}>
              <button className="btn btn-secondary" onClick={() => setShowEmailPreview(false)}>Cancel</button>
              <button className="btn btn-primary" onClick={handleSendAfterReview}
                title={proctorUnapplied ? 'Apply or discard your proctoring changes first' : undefined}
                disabled={sendingInvites || proctorUnapplied}>
                {sendingInvites ? 'Sending...' : `Send ${selectedCandidates.size} Invites${rejectCount > 0 ? ` & ${rejectCount} Rejections` : ''}`}
              </button>
            </div>
          </div>
        </div>
        )
      })()}

      {/* ═══════════ VIEW JOB DETAIL ═══════════ */}
      {viewJob && (
        <div className="modal-overlay" onClick={() => setViewJob(null)}>
          <div className="modal" onClick={e => e.stopPropagation()} style={{ maxWidth: 680, maxHeight: '90vh', overflow: 'auto' }}>
            <div className="flex-between" style={{ marginBottom: 20, paddingBottom: 16, borderBottom: '2px solid var(--border-subtle)' }}>
              <div>
                <h2 style={{ margin: 0 }}>{viewJob.title}</h2>
                <div className="text-sm text-muted" style={{ marginTop: 4 }}>
                  REQ-{viewJob.id?.slice(0, 8)?.toUpperCase()} &middot; {viewJob.department}
                </div>
              </div>
              <span className={`badge ${viewJob.status === 'active' ? 'badge-green' : 'badge-neutral'}`} style={{ fontSize: '0.8rem' }}>
                {viewJob.status === 'active' ? 'Open' : viewJob.status === 'closed' ? 'Closed' : viewJob.status}
              </span>
            </div>

            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16, marginBottom: 20 }}>
              <div style={{ padding: '14px 16px', background: 'var(--surface-input)', borderRadius: 10 }}>
                <div style={{ fontSize: '0.72rem', fontWeight: 700, textTransform: 'uppercase', color: 'var(--text-tertiary)', marginBottom: 4, fontFamily: 'var(--font-display)' }}>Location</div>
                <div style={{ fontWeight: 500 }}>{viewJob.location || '—'}</div>
              </div>
              <div style={{ padding: '14px 16px', background: 'var(--surface-input)', borderRadius: 10 }}>
                <div style={{ fontSize: '0.72rem', fontWeight: 700, textTransform: 'uppercase', color: 'var(--text-tertiary)', marginBottom: 4, fontFamily: 'var(--font-display)' }}>Employment Type</div>
                <div style={{ fontWeight: 500 }}>{viewJob.employment_type || '—'}</div>
              </div>
              <div style={{ padding: '14px 16px', background: 'var(--surface-input)', borderRadius: 10 }}>
                <div style={{ fontSize: '0.72rem', fontWeight: 700, textTransform: 'uppercase', color: 'var(--text-tertiary)', marginBottom: 4, fontFamily: 'var(--font-display)' }}>Experience Required</div>
                <div style={{ fontWeight: 500 }}>{viewJob.experience_years || 0}+ years</div>
              </div>
              <div style={{ padding: '14px 16px', background: 'var(--surface-input)', borderRadius: 10 }}>
                <div style={{ fontSize: '0.72rem', fontWeight: 700, textTransform: 'uppercase', color: 'var(--text-tertiary)', marginBottom: 4, fontFamily: 'var(--font-display)' }}>Salary Range</div>
                <div style={{ fontWeight: 500 }}>
                  {viewJob.salary_min || viewJob.salary_max
                    ? `${viewJob.salary_min ? Number(viewJob.salary_min).toLocaleString() : '—'} - ${viewJob.salary_max ? Number(viewJob.salary_max).toLocaleString() : '—'}`
                    : 'Not specified'}
                </div>
              </div>
            </div>

            <div style={{ marginBottom: 16 }}>
              <div style={{ fontSize: '0.72rem', fontWeight: 700, textTransform: 'uppercase', color: 'var(--text-tertiary)', marginBottom: 6, fontFamily: 'var(--font-display)' }}>Description</div>
              <div style={{ fontSize: '0.92rem', lineHeight: 1.7, color: 'var(--text-secondary)', whiteSpace: 'pre-wrap' }}>{viewJob.description || 'No description'}</div>
            </div>

            <div style={{ marginBottom: 16 }}>
              <div style={{ fontSize: '0.72rem', fontWeight: 700, textTransform: 'uppercase', color: 'var(--text-tertiary)', marginBottom: 6, fontFamily: 'var(--font-display)' }}>Requirements</div>
              <div style={{ fontSize: '0.92rem', lineHeight: 1.7, color: 'var(--text-secondary)', whiteSpace: 'pre-wrap' }}>{viewJob.requirements || 'No requirements listed'}</div>
            </div>

            <div style={{ marginBottom: 20 }}>
              <div style={{ fontSize: '0.72rem', fontWeight: 700, textTransform: 'uppercase', color: 'var(--text-tertiary)', marginBottom: 8, fontFamily: 'var(--font-display)' }}>Required Skills</div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                {(Array.isArray(viewJob.required_skills) ? viewJob.required_skills : []).map((s, i) => (
                  <span key={i} style={{ padding: '4px 12px', background: 'var(--accent-light)', color: 'var(--accent-primary)', borderRadius: 6, fontSize: '0.82rem', fontWeight: 600 }}>{s}</span>
                ))}
                {(!viewJob.required_skills || viewJob.required_skills.length === 0) && <span className="text-muted text-sm">No skills listed</span>}
              </div>
            </div>

            {viewJob.assigned_interviewer_name && (
              <div style={{ padding: '12px 16px', background: 'var(--surface-input)', borderRadius: 10, marginBottom: 20 }}>
                <div style={{ fontSize: '0.72rem', fontWeight: 700, textTransform: 'uppercase', color: 'var(--text-tertiary)', marginBottom: 4, fontFamily: 'var(--font-display)' }}>Assigned Interviewer</div>
                <div style={{ fontWeight: 500 }}>{viewJob.assigned_interviewer_name} ({viewJob.assigned_interviewer_email})</div>
              </div>
            )}

            <div style={{ fontSize: '0.78rem', color: 'var(--text-tertiary)', fontFamily: 'var(--font-mono)', marginBottom: 20 }}>
              Created: {viewJob.created_at ? new Date(viewJob.created_at).toLocaleString() : '—'}
            </div>

            <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end', borderTop: '1px solid var(--border-subtle)', paddingTop: 16 }}>
              <button className="btn btn-secondary" onClick={() => setViewJob(null)}>Close</button>
              {viewJob.status === 'closed' ? (
                <button className="btn btn-ghost" style={{ color: 'var(--status-success)' }} onClick={() => handleReopenJob(viewJob.id)}>
                  <RotateCcw size={15} /> Reopen Job
                </button>
              ) : (
                /* Archive, not Trash2 — the neighbouring Delete Job is irreversible and
                   destroys every candidate, interview and offer attached to the job. */
                <button className="btn btn-ghost" style={{ color: 'var(--status-danger)' }} onClick={() => handleCloseJob(viewJob.id)}>
                  <Archive size={15} /> Close Job
                </button>
              )}
              <button className="btn" style={{ background: 'var(--status-danger)', color: '#fff' }} onClick={() => handleDeleteJobPermanent(viewJob.id)}>
                <Trash2 size={15} /> Delete Job
              </button>
              <button className="btn btn-primary" onClick={() => { openEditJob(viewJob); setViewJob(null) }}>
                <Edit3 size={15} /> Edit Job
              </button>
              <button className="btn btn-secondary" title="Build an AI-generated test for this job"
                onClick={() => { setCreateTestJobId(viewJob.id); setShowCreateTest(true); setViewJob(null) }}>
                <ClipboardList size={15} /> Create Test
              </button>
              {viewJob.status === 'draft' && (
                <button className="btn btn-primary" style={{ background: '#22c55e' }} onClick={() => {
                  openEditJob(viewJob)
                  setIsApprovalMode(true)
                  setViewJob(null)
                }}>
                  Approve & Publish
                </button>
              )}
            </div>
          </div>
        </div>
      )}

      {/* ═══════════ ATS CANDIDATE SHEET ═══════════ */}
      {atsJob && (
        <div className="modal-overlay" onClick={() => setAtsJob(null)} style={{ alignItems: 'flex-start', paddingTop: 40 }}>
          <div onClick={e => e.stopPropagation()} style={{
            background: '#fff', borderRadius: 18, width: '95%', maxWidth: 1100,
            maxHeight: '90vh', overflow: 'hidden', display: 'flex', flexDirection: 'column',
            boxShadow: '0 24px 80px rgba(0,0,0,0.15)', border: '1px solid var(--border-subtle)'
          }}>
            {/* Sheet Header */}
            <div style={{ padding: '20px 28px 16px', borderBottom: '2px solid var(--border-subtle)', flexShrink: 0 }}>
              <div className="flex-between" style={{ marginBottom: 14 }}>
                <div>
                  <h2 style={{ margin: 0, fontSize: '1.3rem' }}>{atsJob.title}</h2>
                  <div className="text-sm text-muted" style={{ marginTop: 4 }}>
                    {atsJob.department} &middot; {atsJob.location} &middot; {atsCandidates.length} candidates
                    {selectedCandidates.size > 0 && <span style={{ color: 'var(--accent-primary)', fontWeight: 600 }}> &middot; {selectedCandidates.size} selected</span>}
                  </div>
                </div>
                <div className="flex gap-8">
                  <div style={{ background: 'var(--surface-input)', border: '1px solid var(--border-subtle)', borderRadius: 8, padding: '0 12px', display: 'flex', alignItems: 'center', gap: 8, width: 220 }}>
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#9ca3af" strokeWidth="2"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/></svg>
                    <input className="input" style={{ border: 'none', background: 'none', padding: '7px 0', boxShadow: 'none', fontSize: '0.85rem' }}
                      placeholder="Search..." value={atsSearch} onChange={e => setAtsSearch(e.target.value)} />
                  </div>
                  <button className="btn btn-ghost btn-sm" onClick={() => setAtsJob(null)} style={{ fontSize: '1.1rem', padding: '6px 10px' }}>x</button>
                </div>
              </div>
              {/* Selection & Assign Bar */}
              <div style={{
                display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'center',
                padding: '10px 14px',
                background: 'linear-gradient(135deg, #f0f4ff 0%, #faf5ff 50%, #fff1f2 100%)',
                borderRadius: 10,
                border: '1px solid #e0e7ff'
              }}>
                {/* Unified Selection Control */}
                <div style={{
                  display: 'flex', alignItems: 'center', gap: 0,
                  background: '#fff', borderRadius: 8,
                  border: '1px solid #c7d2fe',
                  boxShadow: '0 1px 3px rgba(99,102,241,0.08)',
                  overflow: 'hidden'
                }}>
                  {/* Mode Dropdown */}
                  <select
                    value={selectionMode}
                    onChange={e => {
                      setSelectionMode(e.target.value)
                      if (e.target.value === 'manual') { setSelectMode(true) } else { setSelectMode(false) }
                      setSelectedCandidates(new Set())
                    }}
                    style={{
                      border: 'none', background: '#eef2ff', padding: '7px 10px', fontSize: '0.82rem',
                      fontWeight: 700, color: '#4338ca', cursor: 'pointer', outline: 'none',
                      borderRight: '1px solid #c7d2fe', minWidth: 130,
                      appearance: 'auto'
                    }}
                  >
                    <option value="top_n">Top N Candidates</option>
                    <option value="top_percentile">Top Percentile %</option>
                    <option value="manual">Manual Select</option>
                  </select>

                  {/* Dynamic Input based on mode */}
                  {selectionMode === 'top_n' && (
                    <div style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '0 10px' }}>
                      <input
                        type="number" min={1} max={atsCandidates.length || 50}
                        value={topN}
                        onChange={e => setTopN(parseInt(e.target.value) || 1)}
                        style={{
                          width: 50, padding: '5px 6px', fontSize: '0.85rem', textAlign: 'center',
                          border: '1px solid #e0e7ff', borderRadius: 6, background: '#f8fafc',
                          outline: 'none', fontWeight: 600, color: '#1e1b4b'
                        }}
                      />
                      <span style={{ fontSize: '0.78rem', color: '#6366f1', fontWeight: 500 }}>
                        of {atsCandidates.length}
                      </span>
                    </div>
                  )}
                  {selectionMode === 'top_percentile' && (
                    <div style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '0 10px' }}>
                      <input
                        type="number" min={1} max={100}
                        value={topPercentile}
                        onChange={e => setTopPercentile(Math.min(100, Math.max(1, parseInt(e.target.value) || 1)))}
                        style={{
                          width: 50, padding: '5px 6px', fontSize: '0.85rem', textAlign: 'center',
                          border: '1px solid #e0e7ff', borderRadius: 6, background: '#f8fafc',
                          outline: 'none', fontWeight: 600, color: '#1e1b4b'
                        }}
                      />
                      <span style={{ fontSize: '0.78rem', color: '#6366f1', fontWeight: 500 }}>
                        % = {atsCandidates.length ? Math.max(1, Math.ceil(atsCandidates.length * topPercentile / 100)) : 0} candidates
                      </span>
                    </div>
                  )}
                  {selectionMode === 'manual' && (
                    <div style={{ padding: '6px 12px', fontSize: '0.78rem', color: '#7c3aed', fontWeight: 500 }}>
                      Click rows to pick
                    </div>
                  )}

                  {/* Apply Button */}
                  {selectionMode !== 'manual' && (
                    <button
                      onClick={selectionMode === 'top_n' ? handleSelectTopN : handleSelectTopPercentile}
                      style={{
                        border: 'none', background: 'linear-gradient(135deg, #6366f1, #8b5cf6)',
                        color: '#fff', padding: '7px 16px', fontSize: '0.82rem', fontWeight: 700,
                        cursor: 'pointer', letterSpacing: '0.02em',
                        transition: 'all 0.15s ease'
                      }}
                      onMouseEnter={e => e.target.style.background = 'linear-gradient(135deg, #4f46e5, #7c3aed)'}
                      onMouseLeave={e => e.target.style.background = 'linear-gradient(135deg, #6366f1, #8b5cf6)'}
                    >
                      Apply
                    </button>
                  )}
                </div>

                {/* Selected Count + Actions */}
                {selectedCandidates.size > 0 && (
                  <div style={{
                    display: 'flex', alignItems: 'center', gap: 10,
                    padding: '5px 12px',
                    background: 'linear-gradient(135deg, #ecfdf5, #f0fdf4)',
                    borderRadius: 8,
                    border: '1px solid #86efac',
                    boxShadow: '0 1px 3px rgba(34,197,94,0.1)'
                  }}>
                    <div style={{
                      background: '#16a34a', color: '#fff', borderRadius: 6,
                      padding: '2px 8px', fontSize: '0.8rem', fontWeight: 700,
                      minWidth: 24, textAlign: 'center'
                    }}>
                      {selectedCandidates.size}
                    </div>
                    <span style={{ fontSize: '0.8rem', fontWeight: 600, color: '#15803d', whiteSpace: 'nowrap' }}>
                      selected
                    </span>
                    <div style={{ width: 1, height: 18, background: '#bbf7d0' }} />
                    <input type="date" className="input"
                      style={{
                        width: 140, padding: '4px 8px', fontSize: '0.8rem',
                        border: '1px solid #bbf7d0', borderRadius: 6, background: '#fff'
                      }}
                      value={testDeadline} onChange={e => setTestDeadline(e.target.value)} title="Test deadline" />
                    <button
                      onClick={openEmailPreview}
                      style={{
                        border: 'none', background: 'linear-gradient(135deg, #16a34a, #15803d)',
                        color: '#fff', padding: '6px 14px', fontSize: '0.8rem', fontWeight: 700,
                        borderRadius: 6, cursor: 'pointer', whiteSpace: 'nowrap',
                        transition: 'all 0.15s ease'
                      }}
                      onMouseEnter={e => e.target.style.opacity = '0.9'}
                      onMouseLeave={e => e.target.style.opacity = '1'}
                    >
                      Review & Send
                    </button>
                  </div>
                )}

                {/* Interviewer Assign - pushed right */}
                <div style={{ marginLeft: 'auto', display: 'flex', gap: 8, alignItems: 'center' }}>
                  <select className="select"
                    style={{
                      width: 200, padding: '6px 10px', fontSize: '0.82rem',
                      border: '1px solid #c7d2fe', borderRadius: 8, background: '#fff'
                    }}
                    value={selInterviewer} onChange={e => setSelInterviewer(e.target.value)}>
                    <option value="">Assign Interviewer...</option>
                    {interviewers.map(iv => (
                      <option key={iv.id} value={iv.id}>{iv.name} ({iv.department})</option>
                    ))}
                  </select>
                  {selInterviewer && (
                    <button className="btn btn-primary btn-sm" onClick={handleAssignInterviewer}
                      style={{ padding: '6px 14px', fontSize: '0.82rem' }}>Assign</button>
                  )}
                </div>
              </div>
            </div>

            {/* Color band legend */}
            <div style={{ display: 'flex', gap: 16, alignItems: 'center', padding: '8px 16px', fontSize: '0.74rem', color: '#6b7280', flexWrap: 'wrap' }}>
              <span style={{ fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.04em', color: '#9ca3af', fontFamily: 'var(--font-display)' }}>ATS Band:</span>
              {[
                { c: '#22c55e', t: '#f0fdf4', label: 'Selected', range: 'score ≥ 70' },
                { c: '#f59e0b', t: '#fffbeb', label: 'Pending',  range: '50 – 69' },
                { c: '#ef4444', t: '#fef2f2', label: 'Rejected', range: '< 50' },
              ].map(b => (
                <span key={b.label} style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                  <span style={{ width: 14, height: 14, borderRadius: 3, background: b.t, borderLeft: `4px solid ${b.c}` }} />
                  <strong style={{ color: b.c }}>{b.label}</strong>
                  <span style={{ fontFamily: 'var(--font-mono)', color: '#9ca3af' }}>({b.range})</span>
                </span>
              ))}
            </div>

            {/* Candidate Table */}
            <div style={{ overflow: 'auto', flex: 1 }}>
              {atsLoading ? (
                <div style={{ padding: 60, textAlign: 'center', color: 'var(--text-tertiary)' }}>Loading candidates...</div>
              ) : atsCandidates.length === 0 ? (
                <div style={{ padding: 60, textAlign: 'center', color: 'var(--text-tertiary)' }}>No candidates have applied for this job yet</div>
              ) : (
                <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                  <thead>
                    <tr style={{ background: '#f9fafb', position: 'sticky', top: 0, zIndex: 2 }}>
                      {selectMode && <th style={{ padding: '12px 10px', borderBottom: '1px solid var(--border-subtle)', textAlign: 'center', width: 36 }}>
                        {(() => {
                          const selectable = atsCandidates.filter(c => !isMailSent(c))
                          const allSelected = selectable.length > 0 && selectable.every(c => selectedCandidates.has(c.candidate_id))
                          return (
                            <input type="checkbox" checked={allSelected}
                              onChange={() => allSelected ? setSelectedCandidates(new Set()) : setSelectedCandidates(new Set(selectable.map(c => c.candidate_id)))} />
                          )
                        })()}
                      </th>}
                      <th style={{ padding: '12px 16px', fontSize: '0.72rem', fontWeight: 700, fontFamily: 'var(--font-display)', textTransform: 'uppercase', letterSpacing: '0.06em', color: '#9ca3af', textAlign: 'left', borderBottom: '1px solid var(--border-subtle)' }}>#</th>
                      <th style={{ padding: '12px 16px', fontSize: '0.72rem', fontWeight: 700, fontFamily: 'var(--font-display)', textTransform: 'uppercase', letterSpacing: '0.06em', color: '#9ca3af', textAlign: 'left', borderBottom: '1px solid var(--border-subtle)' }}>Candidate</th>
                      <th style={{ padding: '12px 16px', fontSize: '0.72rem', fontWeight: 700, fontFamily: 'var(--font-display)', textTransform: 'uppercase', letterSpacing: '0.06em', color: '#9ca3af', textAlign: 'left', borderBottom: '1px solid var(--border-subtle)' }}>Contact</th>
                      <th style={{ padding: '12px 16px', fontSize: '0.72rem', fontWeight: 700, fontFamily: 'var(--font-display)', textTransform: 'uppercase', letterSpacing: '0.06em', color: '#9ca3af', textAlign: 'center', borderBottom: '1px solid var(--border-subtle)' }}>ATS Score</th>
                      <th style={{ padding: '12px 16px', fontSize: '0.72rem', fontWeight: 700, fontFamily: 'var(--font-display)', textTransform: 'uppercase', letterSpacing: '0.06em', color: '#9ca3af', textAlign: 'left', borderBottom: '1px solid var(--border-subtle)' }}>Stage</th>
                      <th style={{ padding: '12px 16px', fontSize: '0.72rem', fontWeight: 700, fontFamily: 'var(--font-display)', textTransform: 'uppercase', letterSpacing: '0.06em', color: '#9ca3af', textAlign: 'left', borderBottom: '1px solid var(--border-subtle)' }}>Exp</th>
                      <th style={{ padding: '12px 16px', fontSize: '0.72rem', fontWeight: 700, fontFamily: 'var(--font-display)', textTransform: 'uppercase', letterSpacing: '0.06em', color: '#9ca3af', textAlign: 'center', borderBottom: '1px solid var(--border-subtle)' }}>Details</th>
                    </tr>
                  </thead>
                  <tbody>
                    {atsCandidates
                      .filter(c => !atsSearch || c.name?.toLowerCase().includes(atsSearch.toLowerCase()) || c.email?.toLowerCase().includes(atsSearch.toLowerCase()))
                      .map((c, i) => {
                        const scoreColor = c.ats_score >= 80 ? '#22c55e' : c.ats_score >= 60 ? '#2b7cba' : c.ats_score >= 40 ? '#f59e0b' : '#ef4444'
                        // Selection band by ATS score: green (selected) >=70, yellow (pending) 50-69, red (rejected) <50
                        const band = c.ats_score >= 70 ? 'green' : c.ats_score >= 50 ? 'yellow' : 'red'
                        const bandColor = band === 'green' ? '#22c55e' : band === 'yellow' ? '#f59e0b' : '#ef4444'
                        const bandTint  = band === 'green' ? '#f0fdf4' : band === 'yellow' ? '#fffbeb' : '#fef2f2'
                        const isExpanded = expandedCandidate === c.candidate_id
                        const sent = isMailSent(c)
                        return (
                          <React.Fragment key={c.candidate_id}>
                            <tr style={{
                                borderBottom: '1px solid #f3f4f6', transition: 'background 0.1s', cursor: 'pointer',
                                background: selectedCandidates.has(c.candidate_id) ? '#fef3eb' : bandTint
                              }}
                              onClick={() => selectMode ? toggleCandidate(c.candidate_id) : setExpandedCandidate(isExpanded ? null : c.candidate_id)}>
                              {/* Checkbox */}
                              {selectMode && <td style={{ padding: '14px 10px', textAlign: 'center', borderLeft: `4px solid ${bandColor}` }} onClick={e => { e.stopPropagation(); if (!sent) toggleCandidate(c.candidate_id) }}>
                                <input type="checkbox" checked={selectedCandidates.has(c.candidate_id)} disabled={sent} readOnly
                                  title={sent ? 'Already emailed — cannot be selected' : undefined}
                                  style={sent ? { cursor: 'not-allowed', opacity: 0.4 } : undefined} />
                              </td>}
                              {/* Rank */}
                              <td style={{ padding: '14px 16px', fontFamily: 'var(--font-mono)', fontSize: '0.8rem', color: selectedCandidates.has(c.candidate_id) ? 'var(--accent-primary)' : '#9ca3af', fontWeight: 600, borderLeft: selectMode ? undefined : `4px solid ${bandColor}` }}>
                                {i + 1}
                              </td>
                              {/* Name */}
                              <td style={{ padding: '14px 16px' }}>
                                <div style={{ fontWeight: 600, fontSize: '0.92rem' }}>{c.name}</div>
                                {c.plagiarism_flag && (
                                  <div title="Resume overlaps heavily with the job description" style={{
                                    display: 'inline-flex', alignItems: 'center', gap: 4, marginTop: 4,
                                    padding: '1px 7px', borderRadius: 4, background: '#fef2f2',
                                    color: '#dc2626', border: '1px solid #fecaca',
                                    fontSize: '0.68rem', fontWeight: 700,
                                  }}>
                                    ⚠ Possible JD copy{c.jd_overlap != null ? ` (${Math.round(c.jd_overlap * 100)}%)` : ''}
                                  </div>
                                )}
                              </td>
                              {/* Contact */}
                              <td style={{ padding: '14px 16px' }}>
                                <div style={{ fontSize: '0.84rem', color: 'var(--text-secondary)' }}>{c.email}</div>
                                <div style={{ fontSize: '0.8rem', color: 'var(--text-tertiary)', fontFamily: 'var(--font-mono)' }}>{c.phone || '—'}</div>
                              </td>
                              {/* ATS Score */}
                              <td style={{ padding: '14px 16px', textAlign: 'center' }}>
                                <div style={{
                                  display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
                                  width: 52, height: 52, borderRadius: '50%',
                                  border: `3px solid ${scoreColor}`,
                                  background: `${scoreColor}10`,
                                  fontFamily: 'var(--font-display)', fontWeight: 800, fontSize: '1rem',
                                  color: scoreColor
                                }}>
                                  {Math.round(c.ats_score)}
                                </div>
                                <div style={{ fontSize: '0.68rem', fontWeight: 600, color: scoreColor, marginTop: 2, textTransform: 'uppercase', fontFamily: 'var(--font-display)' }}>
                                  {c.ats_level || (c.ats_score >= 80 ? 'Excellent' : c.ats_score >= 60 ? 'Good' : c.ats_score >= 40 ? 'Fair' : 'Low')}
                                </div>
                              </td>
                              {/* Stage */}
                              <td style={{ padding: '14px 16px' }}>
                                <span className={`badge ${c.current_stage === 'hired' ? 'badge-green' : c.current_stage === 'interview' ? 'badge-amber' : 'badge-blue'}`}>
                                  {c.current_stage}
                                </span>
                                {sent && (() => {
                                  // One label for all — colour still conveys the outcome
                                  // (green = selected, red = rejected, grey = other).
                                  const sentStyle = c.current_stage === 'shortlisted'
                                    ? { bg: '#ecfdf5', fg: '#15803d', bd: '#bbf7d0', label: '✉ Sent' }
                                    : c.current_stage === 'rejected'
                                      ? { bg: '#fef2f2', fg: '#dc2626', bd: '#fecaca', label: '✉ Sent' }
                                      : { bg: '#f3f4f6', fg: '#4b5563', bd: '#e5e7eb', label: '✉ Sent' }
                                  return (
                                    <div title="This candidate has already been emailed from the ATS tab" style={{
                                      display: 'inline-flex', alignItems: 'center', gap: 4, marginTop: 5,
                                      padding: '1px 7px', borderRadius: 4, background: sentStyle.bg,
                                      color: sentStyle.fg, border: `1px solid ${sentStyle.bd}`,
                                      fontSize: '0.68rem', fontWeight: 700, whiteSpace: 'nowrap',
                                    }}>
                                      {sentStyle.label}
                                    </div>
                                  )
                                })()}
                              </td>
                              {/* Experience */}
                              <td style={{ padding: '14px 16px', fontFamily: 'var(--font-mono)', fontSize: '0.85rem', fontWeight: 500 }}>
                                {c.experience_years || 0}y
                              </td>
                              {/* Expand */}
                              <td style={{ padding: '14px 16px', textAlign: 'center' }}>
                                <button className="btn btn-ghost btn-sm" style={{ padding: 4 }}>
                                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                                    <polyline points={isExpanded ? '18 15 12 9 6 15' : '6 9 12 15 18 9'} />
                                  </svg>
                                </button>
                              </td>
                            </tr>

                            {/* Expanded Detail Row */}
                            {isExpanded && (
                              <tr>
                                <td colSpan={selectMode ? 8 : 7} style={{ padding: '0 16px 20px', background: '#fafbfc' }}>
                                  <div style={{ padding: '18px 20px', background: '#fff', borderRadius: 12, border: '1px solid var(--border-subtle)', marginTop: 4 }}>
                                    {/* ATS Score Breakdown Detail (recruiter rubric) */}
                                    <div style={{ marginBottom: 20, padding: 16, background: '#f9fafb', borderRadius: 10 }}>
                                      {(() => {
                                        const bd = c.scoring_breakdown
                                        const ind = (bd && bd.individual_scores) || {}
                                        const wc  = (bd && bd.weighted_contributions) || {}
                                        const sub = (bd && bd.sub_scores) || {}
                                        const edu = sub.education || {}
                                        const exp = sub.experience || {}
                                        const skl = sub.skills || {}
                                        const rating = c.rating || (bd && bd.rating)
                                        const rec = c.hiring_recommendation || (bd && bd.hiring_recommendation)
                                        const strengths = c.strengths || (bd && bd.strengths) || []
                                        const weaknesses = c.weaknesses || (bd && bd.weaknesses) || []
                                        const missingPreferred = c.missing_preferred_skills || (bd && bd.missing_preferred_skills) || []
                                        const FACTORS = [
                                          { key:'education', label:'Education', weight:35, color:'#6366f1', icon:'🎓', desc:'Degree match (40) + College tier (30) + CGPA (30)' },
                                          { key:'skills_match', label:'Skills', weight:25, color:'#3b82f6', icon:'🎯', desc:'Required skills (70) + Preferred skills (30)' },
                                          { key:'experience', label:'Experience', weight:15, color:'#22c55e', icon:'📈', desc:'Relevant exp (50) + Internships (25) + GitHub/OSS/Hackathons (25)' },
                                          { key:'certification', label:'Certifications', weight:10, color:'#f59e0b', icon:'📜', desc:'Count + relevance of professional certifications' },
                                          { key:'semantic', label:'Semantic Match', weight:5, color:'#8b5cf6', icon:'🧠', desc:'Meaning-level similarity of the resume to the JD' },
                                          { key:'project', label:'Projects', weight:5, color:'#ec4899', icon:'🧩', desc:'Relevance of projects to the JD' },
                                          { key:'keyword', label:'Keywords', weight:5, color:'#0ea5e9', icon:'🔑', desc:'JD keyword coverage in the resume' },
                                        ]
                                        const ratingColors = { 'Excellent':'#16a34a','Very Good':'#0d9488','Good':'#d97706','Average':'#ea580c','Poor':'#dc2626' }
                                        const rColor = ratingColors[rating] || scoreColor
                                        const secHeader = { fontSize:'0.72rem', fontWeight:700, textTransform:'uppercase', color:'#9ca3af', marginBottom:8, fontFamily:'var(--font-display)' }
                                        const contrib = (f) => (wc[f.key] != null ? wc[f.key] : ((ind[f.key]||0) * f.weight / 100))
                                        const SubBar = ({ label, val, max, color }) => (
                                          <div style={{ marginBottom: 7 }}>
                                            <div style={{ display:'flex', justifyContent:'space-between', fontSize:'0.76rem', marginBottom:3 }}>
                                              <span>{label}</span>
                                              <span style={{ fontFamily:'var(--font-mono)', fontWeight:700, color }}>{val==null?'—':Math.round(val)}<span style={{ color:'#9ca3af' }}>/{max}</span></span>
                                            </div>
                                            <div style={{ height:6, background:'#eef2f6', borderRadius:3, overflow:'hidden' }}>
                                              <div style={{ width:`${val==null?0:Math.min(100,(val/max)*100)}%`, height:'100%', background:color }} />
                                            </div>
                                          </div>
                                        )

                                        if (!bd) {
                                          return (
                                            <div style={{ padding:12, background:'#fffbeb', border:'1px solid #fde68a', borderRadius:8, fontSize:'0.82rem', color:'#92400e' }}>
                                              Detailed breakdown isn't available for this candidate (scored before the new rubric). Overall ATS: <strong>{Math.round(c.ats_score)}/100</strong>. Re-score the candidate to see the full breakdown.
                                            </div>
                                          )
                                        }
                                        const chartData = FACTORS.map(f => ({ name: f.label, score: Math.round(ind[f.key]||0), fill: f.color }))

                                        return (
                                          <>
                                            {/* Header: score + rating + recommendation */}
                                            <div style={{ display:'flex', alignItems:'center', gap:16, flexWrap:'wrap', marginBottom:14 }}>
                                              <div style={{ display:'inline-flex', alignItems:'center', justifyContent:'center', width:66, height:66, borderRadius:'50%', border:`4px solid ${rColor}`, background:`${rColor}12`, fontFamily:'var(--font-display)', fontWeight:800, fontSize:'1.4rem', color:rColor }}>{Math.round(c.ats_score)}</div>
                                              <div style={{ minWidth:0 }}>
                                                <span style={{ display:'inline-block', padding:'3px 14px', borderRadius:20, background:`${rColor}18`, color:rColor, fontWeight:800, fontSize:'0.82rem', letterSpacing:0.3 }}>{rating || '—'}</span>
                                                <div style={{ fontSize:'0.9rem', color:'#374151', marginTop:6, fontWeight:600 }}>💼 {rec || 'No recommendation'}</div>
                                              </div>
                                            </div>

                                            {c.plagiarism_flag && (
                                              <div style={{ marginBottom:12, padding:'10px 14px', borderRadius:8, background:'#fef2f2', border:'1px solid #fecaca', color:'#b91c1c', fontSize:'0.8rem', lineHeight:1.5 }}>
                                                <strong>⚠ Possible JD copy.</strong> This resume/profile shares{c.jd_overlap != null ? ` ${Math.round(c.jd_overlap * 100)}%` : ' a high amount'} of verbatim text with the job description — the candidate may have pasted the JD to inflate their match. Review the resume before trusting the score.
                                              </div>
                                            )}

                                            {/* Factor score cards */}
                                            <div style={secHeader}>Weighted Factor Breakdown</div>
                                            <div style={{ display:'grid', gridTemplateColumns:'repeat(auto-fit,minmax(230px,1fr))', gap:10, marginBottom:16 }}>
                                              {FACTORS.map(f => { const v = Math.round(ind[f.key]||0); return (
                                                <div key={f.key} style={{ padding:12, background:'#fff', borderRadius:8, border:'1px solid #e5e7eb' }}>
                                                  <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center', marginBottom:6 }}>
                                                    <span style={{ fontWeight:700, fontSize:'0.82rem', fontFamily:'var(--font-display)' }}>{f.icon} {f.label}</span>
                                                    <span style={{ fontFamily:'var(--font-mono)', fontWeight:800, color:f.color }}>{v}<span style={{ fontSize:'0.7rem', color:'#9ca3af' }}>/100</span></span>
                                                  </div>
                                                  <div style={{ height:7, background:'#f3f4f6', borderRadius:4, overflow:'hidden', marginBottom:6 }}>
                                                    <div style={{ width:`${Math.min(v,100)}%`, height:'100%', background:f.color, transition:'width 0.6s ease' }} />
                                                  </div>
                                                  <div style={{ fontSize:'0.7rem', color:'#6b7280', lineHeight:1.45 }}>{f.desc}</div>
                                                  <div style={{ fontSize:'0.66rem', color:'#9ca3af', marginTop:4, fontFamily:'var(--font-mono)' }}>Weight {f.weight}% → contributes {contrib(f).toFixed(1)} pts</div>
                                                </div>
                                              )})}
                                            </div>

                                            {/* Sub-scores + factor chart */}
                                            <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:16, marginBottom:16 }}>
                                              <div>
                                                <div style={secHeader}>🎓 Education — {Math.round(ind.education||0)}/100</div>
                                                <SubBar label="Degree match" val={edu.degree_match} max={40} color="#6366f1" />
                                                <SubBar label={`College tier${edu.tier ? ' ('+edu.tier.replace('_',' ')+')' : ''}`} val={edu.college_tier} max={30} color="#8b5cf6" />
                                                <SubBar label={`CGPA${edu.cgpa_found ? '' : ' (not found)'}`} val={edu.cgpa} max={30} color="#a855f7" />
                                                <div style={{ ...secHeader, marginTop:12 }}>📈 Experience — {Math.round(ind.experience||0)}/100</div>
                                                <SubBar label="Relevant experience" val={exp.relevant_experience} max={50} color="#22c55e" />
                                                <SubBar label={`Internships (${exp.internship_count||0})`} val={exp.internships} max={25} color="#16a34a" />
                                                <SubBar label="GitHub / OSS / Hackathons" val={exp.github_oss_hackathons} max={25} color="#15803d" />
                                                <div style={{ ...secHeader, marginTop:12 }}>🎯 Skills — {Math.round(ind.skills_match||0)}/100</div>
                                                <SubBar label="Required skills" val={skl.required_skills} max={70} color="#3b82f6" />
                                                <SubBar label={`Preferred skills${skl.has_preferred ? '' : ' (none listed)'}`} val={skl.preferred_skills} max={30} color="#0ea5e9" />
                                              </div>
                                              <div>
                                                <div style={secHeader}>Factor Scores (out of 100)</div>
                                                <ResponsiveContainer width="100%" height={300}>
                                                  <BarChart data={chartData} layout="vertical" margin={{ left: 20, right: 28, top: 4, bottom: 4 }}>
                                                    <XAxis type="number" domain={[0,100]} hide />
                                                    <YAxis type="category" dataKey="name" width={92} tick={{ fontSize: 11 }} />
                                                    <Tooltip formatter={(v)=>[`${v}/100`, 'Score']} />
                                                    <Bar dataKey="score" radius={[0,4,4,0]} isAnimationActive={false}>
                                                      {chartData.map((e,i)=><Cell key={i} fill={e.fill} />)}
                                                      <LabelList dataKey="score" position="right" style={{ fontSize:11, fontWeight:700 }} />
                                                    </Bar>
                                                  </BarChart>
                                                </ResponsiveContainer>
                                              </div>
                                            </div>

                                            {/* Weighted calculation table */}
                                            <div style={secHeader}>Weighted Calculation</div>
                                            <div style={{ background:'#fff', border:'1px solid #e5e7eb', borderRadius:8, overflow:'hidden', marginBottom:16 }}>
                                              <table style={{ width:'100%', borderCollapse:'collapse', fontSize:'0.78rem' }}>
                                                <thead>
                                                  <tr style={{ background:'#f9fafb', textAlign:'left', color:'#6b7280' }}>
                                                    <th style={{ padding:'7px 12px' }}>Factor</th>
                                                    <th style={{ padding:'7px 12px' }}>Score</th>
                                                    <th style={{ padding:'7px 12px' }}>Weight</th>
                                                    <th style={{ padding:'7px 12px' }}>Contribution</th>
                                                  </tr>
                                                </thead>
                                                <tbody>
                                                  {FACTORS.map(f => (
                                                    <tr key={f.key} style={{ borderTop:'1px solid #f1f5f9' }}>
                                                      <td style={{ padding:'7px 12px' }}>{f.icon} {f.label}</td>
                                                      <td style={{ padding:'7px 12px', fontFamily:'var(--font-mono)' }}>{Math.round(ind[f.key]||0)}</td>
                                                      <td style={{ padding:'7px 12px', fontFamily:'var(--font-mono)' }}>{f.weight}%</td>
                                                      <td style={{ padding:'7px 12px', fontFamily:'var(--font-mono)', fontWeight:700 }}>{contrib(f).toFixed(2)}</td>
                                                    </tr>
                                                  ))}
                                                  {bd.bonus && bd.bonus.rural_bonus > 0 && (
                                                    <tr style={{ borderTop:'1px solid #f1f5f9', color:'#0d9488' }}>
                                                      <td style={{ padding:'7px 12px' }}>🏡 Rural address bonus</td>
                                                      <td /><td />
                                                      <td style={{ padding:'7px 12px', fontFamily:'var(--font-mono)', fontWeight:700 }}>+{bd.bonus.rural_bonus.toFixed(2)}</td>
                                                    </tr>
                                                  )}
                                                  <tr style={{ borderTop:'2px solid #e5e7eb', background:'#f9fafb', fontWeight:800 }}>
                                                    <td style={{ padding:'9px 12px' }}>Final ATS Score (capped at 100)</td>
                                                    <td /><td />
                                                    <td style={{ padding:'9px 12px', fontFamily:'var(--font-mono)', color:scoreColor }}>{Math.round(c.ats_score)}</td>
                                                  </tr>
                                                </tbody>
                                              </table>
                                            </div>

                                            {/* Strengths / Weaknesses */}
                                            <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:16 }}>
                                              <div style={{ padding:12, background:'#f0fdf4', border:'1px solid #bbf7d0', borderRadius:8 }}>
                                                <div style={{ ...secHeader, color:'#15803d', marginBottom:6 }}>✅ Strengths</div>
                                                {strengths.length ? strengths.map((s,i)=>(<div key={i} style={{ fontSize:'0.8rem', color:'#166534', marginBottom:3 }}>• {s}</div>)) : <span style={{ color:'#9ca3af', fontSize:'0.8rem' }}>None identified</span>}
                                              </div>
                                              <div style={{ padding:12, background:'#fef2f2', border:'1px solid #fecaca', borderRadius:8 }}>
                                                <div style={{ ...secHeader, color:'#b91c1c', marginBottom:6 }}>⚠️ Weaknesses</div>
                                                {weaknesses.length ? weaknesses.map((s,i)=>(<div key={i} style={{ fontSize:'0.8rem', color:'#991b1b', marginBottom:3 }}>• {s}</div>)) : <span style={{ color:'#9ca3af', fontSize:'0.8rem' }}>None identified</span>}
                                              </div>
                                            </div>

                                            {/* Missing skills */}
                                            <div style={{ marginTop:14 }}>
                                              <div style={secHeader}>Missing Required Skills</div>
                                              <div style={{ display:'flex', flexWrap:'wrap', gap:4 }}>
                                                {(c.missing_skills||[]).length ? c.missing_skills.map((s,i)=>(<span key={i} style={{ padding:'2px 8px', background:'#fef2f2', color:'#dc2626', borderRadius:4, fontSize:'0.74rem', fontWeight:500 }}>{s}</span>)) : <span style={{ color:'#9ca3af', fontSize:'0.8rem' }}>None — all required skills present</span>}
                                              </div>
                                              {missingPreferred.length > 0 && (
                                                <>
                                                  <div style={{ ...secHeader, marginTop:10 }}>Missing Preferred Skills</div>
                                                  <div style={{ display:'flex', flexWrap:'wrap', gap:4 }}>
                                                    {missingPreferred.map((s,i)=>(<span key={i} style={{ padding:'2px 8px', background:'#fff7ed', color:'#c2410c', borderRadius:4, fontSize:'0.74rem', fontWeight:500 }}>{s}</span>))}
                                                  </div>
                                                </>
                                              )}
                                            </div>
                                          </>
                                        )
                                      })()}
                                    </div>

                                    {/* Contact + Photo + Skills */}
                                    <div style={{ display: 'grid', gridTemplateColumns: 'auto 1fr 1fr 1fr', gap: 20, marginBottom: 16, alignItems: 'start' }}>
                                      {/* Photo column */}
                                      <div style={{ textAlign: 'center' }}>
                                        <div style={{ fontSize: '0.72rem', fontWeight: 700, textTransform: 'uppercase', color: '#9ca3af', marginBottom: 6, fontFamily: 'var(--font-display)' }}>Photo</div>
                                        {c.photo_url
                                          ? <img src={c.photo_url} alt={c.name} style={{ width: 72, height: 72, borderRadius: 10, objectFit: 'cover', border: '2px solid var(--border-medium, #e5e7eb)' }} onError={e => { e.target.style.display = 'none' }} />
                                          : <div style={{ width: 72, height: 72, borderRadius: 10, background: avatarColor(c.name), color: '#fff', display: 'grid', placeItems: 'center', fontSize: '1.4rem', fontWeight: 700 }}>{getInitials(c.name)}</div>
                                        }
                                      </div>
                                      <div>
                                        <div style={{ fontSize: '0.72rem', fontWeight: 700, textTransform: 'uppercase', color: '#9ca3af', marginBottom: 6, fontFamily: 'var(--font-display)' }}>Contact Info</div>
                                        <div style={{ fontSize: '0.88rem', marginBottom: 4 }}><strong>Email:</strong> {c.email}</div>
                                        <div style={{ fontSize: '0.88rem', marginBottom: 4 }}><strong>Phone:</strong> {c.phone || 'Not provided'}</div>
                                        <div style={{ fontSize: '0.88rem' }}><strong>Experience:</strong> {c.experience_years || 0} years</div>
                                        {c.resume_path && (
                                          <a href={`/${c.resume_path}`} target="_blank" rel="noreferrer"
                                             style={{ display: 'inline-block', marginTop: 10, padding: '6px 14px', borderRadius: 8,
                                               background: '#f0fdf4', color: '#15803d', fontSize: '0.8rem', fontWeight: 600,
                                               textDecoration: 'none', border: '1px solid #bbf7d0' }}>
                                            📄 View Resume
                                          </a>
                                        )}
                                      </div>
                                      <div>
                                        <div style={{ fontSize: '0.72rem', fontWeight: 700, textTransform: 'uppercase', color: '#9ca3af', marginBottom: 6, fontFamily: 'var(--font-display)' }}>Matched Skills</div>
                                        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                                          {(c.matched_skills || []).length > 0 ? c.matched_skills.map((s, si) => (
                                            <span key={si} style={{ padding: '2px 8px', background: '#ecfdf5', color: '#15803d', borderRadius: 4, fontSize: '0.76rem', fontWeight: 500 }}>{s}</span>
                                          )) : <span style={{ color: '#9ca3af', fontSize: '0.84rem' }}>None</span>}
                                        </div>
                                      </div>
                                      <div>
                                        <div style={{ fontSize: '0.72rem', fontWeight: 700, textTransform: 'uppercase', color: '#9ca3af', marginBottom: 6, fontFamily: 'var(--font-display)' }}>Missing Skills</div>
                                        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                                          {(c.missing_skills || []).length > 0 ? c.missing_skills.map((s, si) => (
                                            <span key={si} style={{ padding: '2px 8px', background: '#fef2f2', color: '#dc2626', borderRadius: 4, fontSize: '0.76rem', fontWeight: 500 }}>{s}</span>
                                          )) : <span style={{ color: '#9ca3af', fontSize: '0.84rem' }}>None</span>}
                                        </div>
                                      </div>
                                    </div>
                                    <div>
                                      <div style={{ fontSize: '0.72rem', fontWeight: 700, textTransform: 'uppercase', color: '#9ca3af', marginBottom: 6, fontFamily: 'var(--font-display)' }}>All Extracted Skills</div>
                                      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                                        {(c.extracted_skills || []).length > 0 ? c.extracted_skills.map((s, si) => (
                                          <span key={si} style={{ padding: '2px 8px', background: '#f3f4f6', color: '#374151', borderRadius: 4, fontSize: '0.76rem', fontWeight: 500 }}>{s}</span>
                                        )) : <span style={{ color: '#9ca3af', fontSize: '0.84rem' }}>No skills extracted</span>}
                                      </div>
                                    </div>
                                    <div style={{ marginTop: 12, fontSize: '0.78rem', color: '#9ca3af', fontFamily: 'var(--font-mono)' }}>
                                      Applied: {c.applied_at ? new Date(c.applied_at).toLocaleString() : '—'}
                                    </div>
                                  </div>
                                </td>
                              </tr>
                            )}
                          </React.Fragment>
                        )
                      })}
                  </tbody>
                </table>
              )}
            </div>
          </div>
        </div>
      )}

      {/* ==================== REQUIREMENTS TAB ==================== */}
      {tab === 'requirements' && (() => {
        const urgencyColor = { critical: '#ef4444', high: '#f59e0b', normal: '#3b82f6', low: '#9ca3af' }
        const statusColor = { pending: '#f59e0b', approved: '#22c55e', rejected: '#ef4444', fulfilled: '#8b5cf6' }

        const allJobs = reqData?.jobs || []
        const unmatchedRequests = reqData?.unmatched_requests || []
        const incomingRequests = unmatchedRequests.filter(r => r.status !== 'rejected' && r.status !== 'fulfilled')
        const closedRequests = unmatchedRequests.filter(r => r.status === 'rejected' || r.status === 'fulfilled')
        const pendingJobs = allJobs.filter(j => j.candidate_requests.some(r => r.status === 'pending'))
        const completedJobs = allJobs.filter(j => j.candidate_requests.length > 0 && j.candidate_requests.every(r => r.status !== 'pending'))
        const noReqJobs = allJobs.filter(j => j.candidate_requests.length === 0)

        const UnmatchedReqCard = ({ req }) => (
          <div className="card" style={{ padding: 18 }}>
            <div className="flex-between" style={{ alignItems: 'flex-start', gap: 12, marginBottom: 10 }}>
              <div>
                <h3 style={{ fontSize: '1rem', margin: 0 }}>{req.job_title}</h3>
                <div style={{ fontSize: '0.78rem', color: 'var(--text-tertiary)', marginTop: 4 }}>
                  {req.department || 'No department'} · {req.requested_by_name}
                </div>
              </div>
              <span style={{ padding: '2px 8px', borderRadius: 12, fontSize: '0.7rem', fontWeight: 600, background: (urgencyColor[req.urgency] || '#9ca3af') + '22', color: urgencyColor[req.urgency] || '#9ca3af' }}>
                {req.urgency || 'normal'}
              </span>
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '5px 16px', fontSize: '0.8rem', color: '#4b5563', marginBottom: 10 }}>
              <div><strong>Role Level:</strong> {req.role_level || '—'}</div>
              <div><strong>Candidates:</strong> {req.num_candidates}</div>
              <div><strong>Experience:</strong> {req.min_experience_years}–{req.max_experience_years || '∞'} yrs</div>
              <div><strong>Education:</strong> {req.education_requirement || '—'}</div>
            </div>
            {req.required_skills?.length > 0 && (
              <div style={{ display: 'flex', gap: 5, flexWrap: 'wrap', marginBottom: 10 }}>
                {req.required_skills.map((s, i) => <span key={i} style={{ padding: '2px 8px', background: '#fef3c7', color: '#92400e', borderRadius: 12, fontSize: '0.7rem' }}>{s}</span>)}
              </div>
            )}
            {req.job_description && <div style={{ fontSize: '0.8rem', color: '#6b7280', lineHeight: 1.5, marginBottom: 10 }}>{req.job_description}</div>}
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
              {closingReqId === req.id ? (
                <>
                  <span style={{ fontSize: '0.78rem', alignSelf: 'center', color: '#6b7280' }}>Close as:</span>
                  <button className="btn btn-sm" disabled={reqStatusUpdating === req.id} onClick={() => { updateReqStatus(req.id, 'fulfilled'); setClosingReqId(null) }} style={{ background: '#22c55e', color: '#fff', border: 'none', padding: '5px 14px', borderRadius: 6, fontSize: '0.8rem', cursor: 'pointer' }}>Hired</button>
                  <button className="btn btn-sm" disabled={reqStatusUpdating === req.id} onClick={() => { updateReqStatus(req.id, 'rejected'); setClosingReqId(null) }} style={{ background: '#ef4444', color: '#fff', border: 'none', padding: '5px 14px', borderRadius: 6, fontSize: '0.8rem', cursor: 'pointer' }}>Rejected</button>
                  <button className="btn btn-sm" onClick={() => setClosingReqId(null)} style={{ background: 'transparent', color: '#6b7280', border: '1px solid var(--border-subtle)', padding: '5px 12px', borderRadius: 6, fontSize: '0.8rem', cursor: 'pointer' }}>Cancel</button>
                </>
              ) : (
                <>
                  <button className="btn btn-sm" disabled={reqStatusUpdating === req.id} onClick={() => approveAndCreateJob(req)} style={{ background: '#22c55e', color: '#fff', border: 'none', padding: '5px 14px', borderRadius: 6, fontSize: '0.8rem', cursor: 'pointer' }}>Approve & Create Job</button>
                  <button className="btn btn-sm" disabled={reqStatusUpdating === req.id} onClick={() => { const notes = prompt('Rejection reason (optional):'); updateReqStatus(req.id, 'rejected', notes) }} style={{ background: '#ef4444', color: '#fff', border: 'none', padding: '5px 14px', borderRadius: 6, fontSize: '0.8rem', cursor: 'pointer' }}>Reject</button>
                  <button className="btn btn-sm" title="Close this request (mark Hired or Rejected)" disabled={reqStatusUpdating === req.id} onClick={() => setClosingReqId(req.id)} style={{ background: 'transparent', color: 'var(--status-danger)', border: '1px solid var(--border-subtle)', padding: '5px 10px', borderRadius: 6, fontSize: '0.8rem', cursor: 'pointer' }}><Trash2 size={14} /></button>
                </>
              )}
            </div>
          </div>
        )

        const ClosedReqCard = ({ req }) => (
          <div className="card" style={{ padding: 18, opacity: 0.9 }}>
            <div className="flex-between" style={{ alignItems: 'flex-start', gap: 12, marginBottom: 10 }}>
              <div>
                <h3 style={{ fontSize: '1rem', margin: 0 }}>{req.job_title}</h3>
                <div style={{ fontSize: '0.78rem', color: 'var(--text-tertiary)', marginTop: 4 }}>
                  {req.department || 'No department'} · {req.requested_by_name}
                </div>
              </div>
              <span className={`badge ${req.status === 'fulfilled' ? 'badge-green' : 'badge-red'}`}>
                {req.status === 'fulfilled' ? 'HIRED' : 'REJECTED'}
              </span>
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '5px 16px', fontSize: '0.8rem', color: '#4b5563', marginBottom: 10 }}>
              <div><strong>Role Level:</strong> {req.role_level || '—'}</div>
              <div><strong>Candidates:</strong> {req.num_candidates}</div>
              <div><strong>Experience:</strong> {req.min_experience_years}–{req.max_experience_years || '∞'} yrs</div>
              <div><strong>Education:</strong> {req.education_requirement || '—'}</div>
            </div>
            {req.required_skills?.length > 0 && (
              <div style={{ display: 'flex', gap: 5, flexWrap: 'wrap', marginBottom: 10 }}>
                {req.required_skills.map((s, i) => <span key={i} style={{ padding: '2px 8px', background: '#f3f4f6', color: '#6b7280', borderRadius: 12, fontSize: '0.7rem' }}>{s}</span>)}
              </div>
            )}
            {req.hr_notes && (
              <div style={{ padding: '6px 10px', background: '#f3f4f6', borderRadius: 6, fontSize: '0.78rem', color: '#6b7280', marginBottom: 8 }}>
                <strong>HR Notes:</strong> {req.hr_notes}
              </div>
            )}
            <div style={{ fontSize: '0.7rem', color: '#9ca3af' }}>Requested: {new Date(req.created_at).toLocaleDateString()}</div>
          </div>
        )

        const ReqCard = ({ job }) => {
          const isExpanded = expandedReqJob === job.job_id
          const pendingReqs = job.candidate_requests.filter(r => r.status === 'pending')
          const completedReqs = job.candidate_requests.filter(r => r.status !== 'pending')
          const hasCritical = job.candidate_requests.some(r => r.urgency === 'critical')
          const hasHigh = job.candidate_requests.some(r => r.urgency === 'high')

          return (
            <div className="card" style={{ padding: 0, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
              {/* Card Header */}
              <div style={{ padding: '20px 22px 0' }}>
                <div className="flex-between" style={{ marginBottom: 4 }}>
                  <h3 style={{ fontSize: '1.05rem', margin: 0 }}>{job.job_title}</h3>
                  <span className={`badge ${job.status === 'active' ? 'badge-green' : 'badge-neutral'}`}>
                    {job.status === 'active' ? 'Open' : job.status}
                  </span>
                </div>
                <div style={{ fontSize: '0.78rem', color: 'var(--text-tertiary)', fontFamily: 'var(--font-mono)', marginBottom: 14 }}>
                  REQ-{job.job_id?.slice(0, 8)?.toUpperCase() || '000'}
                </div>
                <div style={{ display: 'grid', gridTemplateColumns: '120px 1fr', gap: '7px 12px', fontSize: '0.87rem', marginBottom: 16 }}>
                  <span style={{ color: 'var(--text-tertiary)', display: 'flex', alignItems: 'center', gap: 6 }}>
                    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>
                    Department:
                  </span>
                  <span style={{ fontWeight: 500 }}>{job.department || '—'}</span>
                  <span style={{ color: 'var(--text-tertiary)', display: 'flex', alignItems: 'center', gap: 6 }}>
                    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect x="2" y="7" width="20" height="14" rx="2"/><path d="M16 21V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v16"/></svg>
                    Type:
                  </span>
                  <span style={{ fontWeight: 500 }}>{job.employment_type || '—'}</span>
                  {job.location && <>
                    <span style={{ color: 'var(--text-tertiary)', display: 'flex', alignItems: 'center', gap: 6 }}>
                      <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z"/><circle cx="12" cy="10" r="3"/></svg>
                      Location:
                    </span>
                    <span style={{ fontWeight: 500 }}>{job.location}</span>
                  </>}
                </div>
              </div>

              {/* Requirement Summary */}
              <div style={{ padding: '0 22px 16px', flex: 1 }}>
                <div style={{ fontWeight: 700, fontSize: '0.82rem', marginBottom: 10, fontFamily: 'var(--font-display)' }}>Requirements Summary</div>
                <div className="flex-between" style={{ fontSize: '0.84rem', padding: '3px 0' }}>
                  <span style={{ color: '#f59e0b', fontWeight: 500 }}>Pending</span>
                  <span style={{ fontFamily: 'var(--font-mono)', fontWeight: 600, color: 'var(--text-primary)' }}>{pendingReqs.length}</span>
                </div>
                <div className="flex-between" style={{ fontSize: '0.84rem', padding: '3px 0' }}>
                  <span style={{ color: '#22c55e', fontWeight: 500 }}>Completed</span>
                  <span style={{ fontFamily: 'var(--font-mono)', fontWeight: 600, color: 'var(--text-primary)' }}>{completedReqs.length}</span>
                </div>
                <div className="flex-between" style={{ fontSize: '0.84rem', padding: '3px 0' }}>
                  <span style={{ color: 'var(--text-tertiary)', fontWeight: 500 }}>Candidates</span>
                  <span style={{ fontFamily: 'var(--font-mono)', fontWeight: 600, color: 'var(--text-primary)' }}>{job.candidate_count || 0}</span>
                </div>
                {(hasCritical || hasHigh) && (
                  <div style={{ marginTop: 8, padding: '5px 10px', borderRadius: 8, background: hasCritical ? '#fef2f2' : '#fffbeb', border: `1px solid ${hasCritical ? '#fecaca' : '#fde68a'}`, fontSize: '0.75rem', fontWeight: 600, color: hasCritical ? '#dc2626' : '#d97706' }}>
                    {hasCritical ? '⚠ Critical urgency request' : '↑ High urgency request'}
                  </div>
                )}
              </div>

              {/* Card Footer */}
              <div style={{ padding: '12px 22px', borderTop: '1px solid var(--border-subtle)', display: 'flex', gap: 10 }}>
                <button
                  className="btn btn-primary btn-sm"
                  style={{ flex: 1, justifyContent: 'center', borderRadius: 8 }}
                  onClick={() => setExpandedReqJob(isExpanded ? null : job.job_id)}
                >
                  {isExpanded ? 'Close Details' : 'Open Details'}
                </button>
              </div>

              {/* Expanded Details Panel */}
              {isExpanded && (
                <div style={{ borderTop: '2px solid var(--accent-primary)', background: '#f8faf9' }}>
                  {/* Skills */}
                  <div style={{ padding: '14px 22px', borderBottom: '1px solid #e5e7eb' }}>
                    <div style={{ fontSize: '0.78rem', fontWeight: 700, color: '#374151', marginBottom: 8, textTransform: 'uppercase', letterSpacing: '0.05em' }}>Required Skills</div>
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                      {(job.required_skills || []).map((s, i) => (
                        <span key={i} style={{ padding: '3px 10px', background: '#e0e7ff', color: '#3730a3', borderRadius: 20, fontSize: '0.75rem', fontWeight: 500 }}>{s}</span>
                      ))}
                      {(!job.required_skills || job.required_skills.length === 0) && <span style={{ fontSize: '0.8rem', color: '#9ca3af' }}>No skills specified</span>}
                    </div>
                  </div>

                  {/* Pending Requests */}
                  {pendingReqs.length > 0 && (
                    <div style={{ padding: '14px 22px', borderBottom: '1px solid #e5e7eb' }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12 }}>
                        <div style={{ width: 10, height: 10, borderRadius: '50%', background: '#f59e0b' }} />
                        <span style={{ fontSize: '0.82rem', fontWeight: 700, color: '#92400e', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                          Pending ({pendingReqs.length})
                        </span>
                      </div>
                      {pendingReqs.map(req => (
                        <div key={req.id} style={{ padding: 14, border: '1px solid #fde68a', borderRadius: 10, marginBottom: 10, background: '#fffdf5' }}>
                          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 8 }}>
                            <div>
                              <span style={{ fontWeight: 600, fontSize: '0.9rem' }}>{req.requested_by_name}</span>
                              <span style={{ marginLeft: 8, fontSize: '0.78rem', color: '#6b7280' }}>{req.requested_by_email}</span>
                            </div>
                            <div style={{ display: 'flex', gap: 6 }}>
                              {req.urgency && (
                                <span style={{ padding: '2px 8px', borderRadius: 12, fontSize: '0.7rem', fontWeight: 600, background: (urgencyColor[req.urgency] || '#9ca3af') + '22', color: urgencyColor[req.urgency] || '#9ca3af' }}>
                                  {req.urgency}
                                </span>
                              )}
                              <span style={{ padding: '2px 8px', borderRadius: 12, fontSize: '0.7rem', fontWeight: 600, background: '#fef3c7', color: '#92400e' }}>pending</span>
                            </div>
                          </div>
                          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '5px 20px', fontSize: '0.8rem', color: '#4b5563', marginBottom: 10 }}>
                            <div><strong>Role Level:</strong> {req.role_level || '—'}</div>
                            <div><strong>Candidates Needed:</strong> {req.num_candidates}</div>
                            <div><strong>Experience:</strong> {req.min_experience_years}–{req.max_experience_years || '∞'} yrs</div>
                            <div><strong>Education:</strong> {req.education_requirement || '—'}</div>
                          </div>
                          {req.required_skills?.length > 0 && (
                            <div style={{ marginBottom: 7 }}>
                              <span style={{ fontSize: '0.72rem', fontWeight: 600, color: '#374151' }}>Required: </span>
                              {req.required_skills.map((s, i) => <span key={i} style={{ padding: '2px 8px', background: '#fef3c7', color: '#92400e', borderRadius: 12, fontSize: '0.7rem', marginRight: 4 }}>{s}</span>)}
                            </div>
                          )}
                          {req.preferred_skills?.length > 0 && (
                            <div style={{ marginBottom: 7 }}>
                              <span style={{ fontSize: '0.72rem', fontWeight: 600, color: '#374151' }}>Preferred: </span>
                              {req.preferred_skills.map((s, i) => <span key={i} style={{ padding: '2px 8px', background: '#e0e7ff', color: '#3730a3', borderRadius: 12, fontSize: '0.7rem', marginRight: 4 }}>{s}</span>)}
                            </div>
                          )}
                          {req.job_description && <div style={{ fontSize: '0.8rem', color: '#6b7280', marginBottom: 8, lineHeight: 1.5 }}>{req.job_description}</div>}
                          {req.notes && <div style={{ fontSize: '0.8rem', color: '#6b7280', fontStyle: 'italic', marginBottom: 8 }}>Note: {req.notes}</div>}
                          <div style={{ display: 'flex', gap: 8, marginTop: 10 }}>
                            <button className="btn btn-sm" disabled={reqStatusUpdating === req.id} onClick={() => approveAndCreateJob(req, job)} style={{ background: '#22c55e', color: '#fff', border: 'none', padding: '5px 14px', borderRadius: 6, fontSize: '0.8rem', cursor: 'pointer' }}>Approve & Create Job</button>
                            <button className="btn btn-sm" disabled={reqStatusUpdating === req.id} onClick={() => { const notes = prompt('Rejection reason (optional):'); updateReqStatus(req.id, 'rejected', notes) }} style={{ background: '#ef4444', color: '#fff', border: 'none', padding: '5px 14px', borderRadius: 6, fontSize: '0.8rem', cursor: 'pointer' }}>Reject</button>
                            <button className="btn btn-sm" disabled={reqStatusUpdating === req.id} onClick={() => updateReqStatus(req.id, 'fulfilled')} style={{ background: '#8b5cf6', color: '#fff', border: 'none', padding: '5px 14px', borderRadius: 6, fontSize: '0.8rem', cursor: 'pointer' }}>Mark Fulfilled</button>
                          </div>
                          <div style={{ marginTop: 7, fontSize: '0.7rem', color: '#9ca3af' }}>Requested: {new Date(req.created_at).toLocaleDateString()}</div>
                        </div>
                      ))}
                    </div>
                  )}

                  {/* Completed Requests */}
                  {completedReqs.length > 0 && (
                    <div style={{ padding: '14px 22px 18px' }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12 }}>
                        <div style={{ width: 10, height: 10, borderRadius: '50%', background: '#22c55e' }} />
                        <span style={{ fontSize: '0.82rem', fontWeight: 700, color: '#15803d', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                          Completed ({completedReqs.length})
                        </span>
                      </div>
                      {completedReqs.map(req => (
                        <div key={req.id} style={{ padding: 14, border: '1px solid #e5e7eb', borderRadius: 10, marginBottom: 10, background: '#fff', opacity: 0.85 }}>
                          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 8 }}>
                            <div>
                              <span style={{ fontWeight: 600, fontSize: '0.9rem' }}>{req.requested_by_name}</span>
                              <span style={{ marginLeft: 8, fontSize: '0.78rem', color: '#6b7280' }}>{req.requested_by_email}</span>
                            </div>
                            <div style={{ display: 'flex', gap: 6 }}>
                              {req.urgency && (
                                <span style={{ padding: '2px 8px', borderRadius: 12, fontSize: '0.7rem', fontWeight: 600, background: (urgencyColor[req.urgency] || '#9ca3af') + '18', color: urgencyColor[req.urgency] || '#9ca3af' }}>
                                  {req.urgency}
                                </span>
                              )}
                              <span style={{ padding: '2px 8px', borderRadius: 12, fontSize: '0.7rem', fontWeight: 600, background: (statusColor[req.status] || '#9ca3af') + '22', color: statusColor[req.status] || '#9ca3af' }}>
                                {req.status}
                              </span>
                            </div>
                          </div>
                          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '5px 20px', fontSize: '0.8rem', color: '#4b5563', marginBottom: 8 }}>
                            <div><strong>Role Level:</strong> {req.role_level || '—'}</div>
                            <div><strong>Candidates Needed:</strong> {req.num_candidates}</div>
                            <div><strong>Experience:</strong> {req.min_experience_years}–{req.max_experience_years || '∞'} yrs</div>
                            <div><strong>Education:</strong> {req.education_requirement || '—'}</div>
                          </div>
                          {req.hr_notes && (
                            <div style={{ padding: '6px 10px', background: '#f3f4f6', borderRadius: 6, fontSize: '0.78rem', color: '#6b7280' }}>
                              <strong>HR Notes:</strong> {req.hr_notes}
                            </div>
                          )}
                          <div style={{ marginTop: 7, fontSize: '0.7rem', color: '#9ca3af' }}>Requested: {new Date(req.created_at).toLocaleDateString()}</div>
                        </div>
                      ))}
                    </div>
                  )}

                  {job.candidate_requests.length === 0 && (
                    <div style={{ padding: '20px 22px', color: '#9ca3af', fontSize: '0.85rem' }}>No requirements submitted for this job yet.</div>
                  )}
                </div>
              )}
            </div>
          )
        }

        return (
          <div>
            <div className="flex-between" style={{ marginBottom: 24 }}>
              <div>
                <h2 style={{ fontSize: '1.2rem', fontWeight: 700 }}>My Job Requirements</h2>
                <p className="text-muted text-sm">Candidate requirements for jobs assigned to you</p>
              </div>
              <button className="btn btn-secondary btn-sm" onClick={fetchRequirements} disabled={reqLoading}>
                {reqLoading ? 'Loading...' : 'Refresh'}
              </button>
            </div>

            {reqLoading && !reqData && <div style={{ padding: 40, textAlign: 'center', color: '#9ca3af' }}>Loading requirements...</div>}

            {reqData && allJobs.length === 0 && unmatchedRequests.length === 0 && (
              <div className="card" style={{ padding: 40, textAlign: 'center', color: '#9ca3af' }}>
                <p style={{ fontSize: '1.1rem', marginBottom: 8 }}>No jobs assigned to you yet</p>
                <p className="text-sm">Jobs will appear here when they are assigned to your HR profile.</p>
              </div>
            )}

            {/* Unmatched Incoming Requests */}
            {incomingRequests.length > 0 && (
              <div style={{ marginBottom: 32 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 16 }}>
                  <div style={{ width: 12, height: 12, borderRadius: '50%', background: '#3b82f6', boxShadow: '0 0 0 3px #dbeafe' }} />
                  <h3 style={{ fontSize: '0.9rem', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.06em', color: '#1d4ed8', margin: 0 }}>
                    Incoming Requests — {incomingRequests.length}
                  </h3>
                </div>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(340px, 1fr))', gap: 18 }}>
                  {incomingRequests.map(req => <UnmatchedReqCard key={req.id} req={req} />)}
                </div>
              </div>
            )}

            {/* Closed Requests (Hired / Rejected) */}
            {closedRequests.length > 0 && (
              <div style={{ marginBottom: 32 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 16 }}>
                  <div style={{ width: 12, height: 12, borderRadius: '50%', background: '#78716c', boxShadow: '0 0 0 3px #e7e5e0' }} />
                  <h3 style={{ fontSize: '0.9rem', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.06em', color: '#57534e', margin: 0 }}>
                    Closed — {closedRequests.length}
                  </h3>
                </div>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(340px, 1fr))', gap: 18 }}>
                  {closedRequests.map(req => <ClosedReqCard key={req.id} req={req} />)}
                </div>
              </div>
            )}

            {/* Pending Section */}
            {pendingJobs.length > 0 && (
              <div style={{ marginBottom: 32 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 16 }}>
                  <div style={{ width: 12, height: 12, borderRadius: '50%', background: '#f59e0b', boxShadow: '0 0 0 3px #fef3c7' }} />
                  <h3 style={{ fontSize: '0.9rem', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.06em', color: '#92400e', margin: 0 }}>
                    Pending — {pendingJobs.length} job{pendingJobs.length !== 1 ? 's' : ''}
                  </h3>
                </div>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(340px, 1fr))', gap: 18 }}>
                  {pendingJobs.map(job => <ReqCard key={job.job_id} job={job} />)}
                </div>
              </div>
            )}

            {/* Completed Section */}
            {completedJobs.length > 0 && (
              <div style={{ marginBottom: 32 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 16 }}>
                  <div style={{ width: 12, height: 12, borderRadius: '50%', background: '#22c55e', boxShadow: '0 0 0 3px #dcfce7' }} />
                  <h3 style={{ fontSize: '0.9rem', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.06em', color: '#15803d', margin: 0 }}>
                    Completed — {completedJobs.length} job{completedJobs.length !== 1 ? 's' : ''}
                  </h3>
                </div>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(340px, 1fr))', gap: 18 }}>
                  {completedJobs.map(job => <ReqCard key={job.job_id} job={job} />)}
                </div>
              </div>
            )}

            {/* No Requirements Jobs */}
            {noReqJobs.length > 0 && (
              <div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 16 }}>
                  <div style={{ width: 12, height: 12, borderRadius: '50%', background: '#d1d5db', boxShadow: '0 0 0 3px #f3f4f6' }} />
                  <h3 style={{ fontSize: '0.9rem', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.06em', color: '#6b7280', margin: 0 }}>
                    No Requests Yet — {noReqJobs.length} job{noReqJobs.length !== 1 ? 's' : ''}
                  </h3>
                </div>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(340px, 1fr))', gap: 18 }}>
                  {noReqJobs.map(job => <ReqCard key={job.job_id} job={job} />)}
                </div>
              </div>
            )}
          </div>
        )
      })()}

      {tab === 'notifications' && <PipelineNotifications />}
    </>
  )
}
