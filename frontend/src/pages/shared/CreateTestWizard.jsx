import { useState, useEffect, useRef } from 'react'
import axios from 'axios'
import {
  Plus, XCircle, Loader2, Brain, Eye, BookOpen, CheckCircle2,
  RefreshCw, Filter, Square, CheckSquare, Send, Upload, FileText
} from 'lucide-react'
import api from '../../api/client'

// Separate axios instance for /interviews/* routes (not under /api/v1)
const qApi = axios.create({ baseURL: '', timeout: 300000 }) // 5 min timeout for AI generation
qApi.interceptors.request.use((config) => {
  const token = localStorage.getItem('hireflow_token')
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

export const TYPE_LABELS = {
  multiple_choice: 'MCQ', coding: 'Coding', short_answer: 'Descriptive',
  case_study: 'Case Study', aptitude: 'Aptitude', communication: 'Communication',
}

// Minutes granted per question, by type — what a new test starts with. HR can change any of
// them per test; the backend keeps its own fallback for tests saved before this setting existed
// (generator_router._TYPE_TIME_DEFAULTS), which stays at the old values so their clocks don't move.
export const TYPE_TIME_DEFAULTS = {
  multiple_choice: 1, coding: 15, short_answer: 5,
  case_study: 5, aptitude: 5, communication: 5,
}

/**
 * Total test duration = every question in a set, at its type's configured time.
 *
 * This is the only source of the test's duration: it is what gets stored on the interview and
 * what each question's own time_limit_minutes is stamped from, and the candidate's countdown is
 * the sum of those (OnlineTest.jsx). Keeping one formula is what stops the advertised duration
 * and the real clock from drifting apart.
 */
export const computeDuration = (form) => (form.skills || [])
  .filter(s => s.name.trim())
  .reduce((total, s) => total + s.question_types.reduce(
    (sub, qt) => sub + (qt.count_per_set || 0) * (form.time_per_question_type?.[qt.type] ?? TYPE_TIME_DEFAULTS[qt.type] ?? 5),
    0), 0)

/**
 * Shared 5-step "Create Test" wizard used by both the Interviewer and HR dashboards.
 *
 * The backend test-creation endpoints (/interviewer/interviews*, /interviews/*) are
 * guarded only by an authenticated-user check, and /interviewer/all-jobs is
 * company-scoped, so this component works identically for HR and interviewers.
 * The only role-specific piece is the "candidate requests" option group, which is
 * gated behind `showCandidateRequests`.
 *
 * Props:
 *  - role: 'interviewer' | 'hr'                (informational)
 *  - showCandidateRequests: bool              (interviewer-only CR option group + auto-create-job)
 *  - preselectedJobId: string | null          (pre-select a job in Step 1, e.g. HR "Create Test" on a job)
 *  - openDraft: object | null                 (an existing DRAFT interview to load for editing)
 *  - onDraftConsumed: () => void              (called once openDraft has been loaded)
 *  - onSaved: () => void                      (called after the test is created/updated — refresh lists)
 *  - onPublished: () => void                  (called after a successful publish — refresh lists)
 *  - onNavigateAway: () => void               (called after publish to leave the wizard)
 */
export default function CreateTestWizard({
  role = 'interviewer',
  showCandidateRequests = false,
  preselectedJobId = null,
  openDraft = null,
  onDraftConsumed,
  onSaved,
  onPublished,
  onNavigateAway,
}) {
  // ── create test state ──
  const [createdTestId, setCreatedTestId] = useState(null)
  const [ctStep, setCtStep] = useState(1)
  const [ctForm, setCtForm] = useState({
    title: '', description: '',
    job_id: '', job_position: '', job_title: '', job_level: 'Mid-level', role_type: 'Full-time',
    interview_type: 'TECHNICAL', difficulty_level: 'MEDIUM',
    // Duration is not stored here — it is derived from these times and the per-type counts
    // via computeDuration(), so the two can never disagree.
    time_per_question_type: { ...TYPE_TIME_DEFAULTS },
    scheduled_datetime: '',
    skills: [{ name: '', difficulty: 'MEDIUM', marks_per_question: 1, question_types: [{ type: 'multiple_choice', count_per_set: 3, pool_size: null }] }],
    num_sets: 2,
    total_marks: 100, negative_marking: false, negative_mark_percent: 0, section_cutoff: 30, overall_cutoff: 50,
    conceptual_vs_practical_ratio: '60:40', real_world_scenarios: true,
    bloom_taxonomy_level: 'Apply', coding_focus_areas: '', avoid_topics: '',
    // Tab-switch detection defaults ON — the interviewer opts out deliberately.
    enable_webcam_monitoring: false, enable_tab_switch_detection: true, require_fullscreen: true, copy_paste_allowed: false,
    interviewer_name: '', interviewer_email: '', meeting_link: '',
    notes: ''
  })
  const [ctCreating, setCtCreating] = useState(false)
  // Per-skill question generation pool
  const [skillQuestionPool, setSkillQuestionPool] = useState({})  // { "React__multiple_choice": [q1, q2...] }
  const [generatingSkill, setGeneratingSkill] = useState(null)    // "skill__type" currently generating
  const [skillPopup, setSkillPopup] = useState(null)              // { skillIndex, typeIndex } for popup, or null
  const [draftInterviewId, setDraftInterviewId] = useState(null)  // DRAFT interview ID for per-skill gen
  const draftIdRef = useRef(null)         // synchronous draft id — avoids the async-state race that spawned dup drafts
  const draftInFlightRef = useRef(null)   // in-flight draft-create promise — concurrent gens share one create
  const [assembledSets, setAssembledSets] = useState([])          // assembled test sets
  const [activeSetTab, setActiveSetTab] = useState(1)
  // Previous questions popup state
  const [prevQPopup, setPrevQPopup] = useState(null)              // { skillIndex, typeIndex } | null
  const [prevQuestions, setPrevQuestions] = useState([])
  const [prevQLoading, setPrevQLoading] = useState(false)
  const [prevQFilters, setPrevQFilters] = useState({ difficulty: 'all', status: 'all' })
  const [prevQSelected, setPrevQSelected] = useState(new Set())
  const [prevQImporting, setPrevQImporting] = useState(false)

  // Manual MCQ entry + CSV bulk import. Both feed the same pool the AI generator fills,
  // so a question typed by hand is indistinguishable downstream from a generated one.
  const [manualQPopup, setManualQPopup] = useState(null)          // { skillIndex, typeIndex } | null
  const [manualQForm, setManualQForm] = useState({ question_text: '', options: ['', '', '', ''], correct: 0 })
  const [manualQSaving, setManualQSaving] = useState(false)
  const [manualQAddedCount, setManualQAddedCount] = useState(0)
  const [csvTarget, setCsvTarget] = useState(null)                // { skillIndex, typeIndex } | null
  const [csvUploading, setCsvUploading] = useState(false)
  const csvInputRef = useRef(null)   // one hidden <input type=file> shared by every MCQ pool card

  // Set when picking a job pulled its unfinished draft in, so the wizard filling itself is
  // explained rather than looking like stale state.
  const [resumedDraft, setResumedDraft] = useState(null)

  const [replacingQ, setReplacingQ] = useState(null)
  const [genLoading, setGenLoading] = useState(false)

  const [allJobs, setAllJobs] = useState([])
  const [crRequests, setCrRequests] = useState([])

  // HR selection for publish
  const [hrList, setHrList] = useState([])
  const [selectedHrId, setSelectedHrId] = useState('')
  const [publishLoading, setPublishLoading] = useState(false)

  const fetchAllJobs = () => {
    api.get('/interviewer/all-jobs')
      .then(r => setAllJobs(r.data || []))
      .catch(err => {
        console.error('Failed to fetch jobs:', err.response?.status, err.response?.data?.detail || err.message)
        setAllJobs([])
      })
  }
  const fetchHrList = () => {
    api.get('/interviewer/hr-list').then(r => setHrList(r.data || [])).catch(() => setHrList([]))
  }
  const fetchCandidateRequests = () => {
    api.get('/interviewer/candidate-requests').then(r => setCrRequests(r.data || [])).catch(() => {})
  }

  // ── initial load ──
  useEffect(() => {
    fetchAllJobs()
    fetchHrList()
    if (showCandidateRequests) fetchCandidateRequests()
    // Pre-fill interviewer details from the logged-in user
    const user = JSON.parse(localStorage.getItem('hireflow_user') || '{}')
    if (user.first_name) {
      setCtForm(f => ({
        ...f,
        interviewer_name: `${user.first_name} ${user.last_name || ''}`.trim(),
        interviewer_email: user.email || ''
      }))
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const typeFromCategory = (cat) => {
    const c = String(cat || '').toLowerCase()
    if (c === 'non_technical' || c === 'non-technical') return 'NON_TECHNICAL'
    if (c === 'technical') return 'TECHNICAL'
    return null
  }

  // Apply a job/candidate-request selection to the form (shared by the dropdown + preselect).
  /**
   * Can this job still be picked? A job whose only test is an unfinished DRAFT can — you
   * resume that draft. A job with a finished test cannot: POST /interviews/draft answers 409.
   *
   * Fails closed. has_test with an unrecognised status counts as blocked, not resumable,
   * so a status we don't know about never opens an edit path we can't honour.
   */
  const isResumableDraft = (job) =>
    !!job?.has_test && String(job?.test_status || '').toUpperCase() === 'DRAFT' && !!job?.test_id

  // Pull the job's unfinished draft into the wizard. /interviewer/tests/drafts returns the
  // same _test_payload shape loadDraftIntoWizard already consumes, so there is nothing to
  // reshape and no new endpoint.
  const jobDraftLoadingRef = useRef(null)   // job_id currently loading — guards a double-change
  const loadDraftForJob = async (job) => {
    if (jobDraftLoadingRef.current === job.job_id) return
    jobDraftLoadingRef.current = job.job_id
    setResumedDraft(null)
    try {
      const r = await api.get('/interviewer/tests/drafts')
      const draft = (r.data?.drafts || []).find(d => String(d.id) === String(job.test_id))
      if (draft) {
        await loadDraftIntoWizard(draft)
        setResumedDraft(draft)
      }
    } catch {
      // Non-fatal: the wizard still works as a fresh one, and ensureDraft() will reuse the
      // same draft server-side anyway. Staying silent beats blocking on a banner.
    }
    jobDraftLoadingRef.current = null
  }

  const applyJobSelection = (val) => {
    const job = allJobs.find(j => j.job_id === val)
    const cr = crRequests.find(r => r.id === val)
    if (job) {
      const mapped = typeFromCategory(job.job_category)
      setCtForm(prev => ({
        ...prev,
        job_id: val,
        job_position: job.job_title,
        job_title: job.job_title,
        interview_type: mapped || prev.interview_type,
      }))
      // The draft is the source of truth and deliberately overwrites what was just set.
      // Skipped when the parent handed us an openDraft: the Exam Drafts tab sets both that
      // and preselectedJobId, and that path already loads the same draft.
      if (isResumableDraft(job) && !openDraft) loadDraftForJob(job)
      else if (!isResumableDraft(job)) setResumedDraft(null)
    } else if (cr) {
      const skills = Array.isArray(cr.required_skills)
        ? cr.required_skills
        : (cr.required_skills ? cr.required_skills.split(',').map(s => s.trim()) : [])
      const mapped = typeFromCategory(cr.job_category)
      const defaultQType = 'multiple_choice'
      setCtForm(prev => ({
        ...prev,
        job_id: val,
        title: cr.job_title + ' Test',
        job_position: cr.job_title,
        job_title: cr.job_title,
        job_level: cr.role_level || prev.job_level,
        role_type: cr.employment_type || prev.role_type,
        description: cr.job_description || '',
        notes: cr.notes || '',
        interview_type: mapped || prev.interview_type,
        skills: skills.length > 0
          ? skills.map(s => ({ name: s, difficulty: 'MEDIUM', marks_per_question: 1, question_types: [{ type: defaultQType, count_per_set: 3, pool_size: null }] }))
          : prev.skills,
      }))
    }
  }

  // Pre-select a job passed in by the parent (e.g. HR clicked "Create Test" on a job card).
  const preselectDoneRef = useRef(false)
  useEffect(() => {
    if (preselectDoneRef.current) return
    if (!preselectedJobId || allJobs.length === 0) return
    if (allJobs.some(j => j.job_id === preselectedJobId)) {
      applyJobSelection(preselectedJobId)
      preselectDoneRef.current = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [allJobs, preselectedJobId])

  // Load an existing DRAFT interview for editing.
  const openDraftDoneRef = useRef(false)
  useEffect(() => {
    if (!openDraft || openDraftDoneRef.current) return
    openDraftDoneRef.current = true
    loadDraftIntoWizard(openDraft)
    onDraftConsumed?.()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [openDraft])

  const loadDraftIntoWizard = async (iv) => {
    const ic = iv.interview_configuration || {}
    const sc = iv.skills_configuration || {}
    const er = iv.evaluation_rules || {}
    const ag = iv.ai_generation_rules || {}
    const sec = iv.security_settings || {}
    const intd = iv.interviewer_details || {}

    setCtForm(f => ({
      ...f,
      title: iv.title || '',
      description: iv.description || '',
      job_id: iv.job_id || '',
      job_position: iv.job_position || '',
      job_title: iv.job_title || ic.job_title || '',
      job_level: ic.job_level || 'Mid-level',
      role_type: ic.role_type || 'Full-time',
      interview_type: ic.interview_type || 'TECHNICAL',
      difficulty_level: ic.difficulty_level || 'MEDIUM',
      // Tests saved before this setting existed carry no map — fall back to the defaults,
      // which reproduce the times those tests were generated with.
      time_per_question_type: { ...TYPE_TIME_DEFAULTS, ...(ic.time_per_question_type || {}) },
      scheduled_datetime: ic.scheduled_datetime || '',
      skills: (sc.skills && sc.skills.length > 0)
        ? sc.skills.map(s => s.question_types
          ? { marks_per_question: 1, ...s }
          : { name: s.name || '', difficulty: s.difficulty_override || s.difficulty || 'MEDIUM', marks_per_question: s.marks_per_question || 1, question_types: [{ type: s.question_type || 'multiple_choice', count_per_set: s.question_count || 3, pool_size: null }] }
        )
        : [{ name: '', difficulty: 'MEDIUM', marks_per_question: 1, question_types: [{ type: 'multiple_choice', count_per_set: 3, pool_size: null }] }],
      num_sets: iv.num_sets || 2,
      total_marks: er.total_marks || 100,
      negative_marking: er.negative_marking || false,
      negative_mark_percent: er.negative_mark_percent || 0,
      section_cutoff: er.section_cutoff || 30,
      overall_cutoff: er.overall_cutoff || 50,
      conceptual_vs_practical_ratio: ag.conceptual_vs_practical_ratio || '60:40',
      real_world_scenarios: ag.real_world_scenarios ?? true,
      bloom_taxonomy_level: ag.bloom_taxonomy_level || 'Apply',
      coding_focus_areas: Array.isArray(ag.coding_focus_areas) ? ag.coding_focus_areas.join(', ') : (ag.coding_focus_areas || ''),
      avoid_topics: Array.isArray(ag.avoid_topics) ? ag.avoid_topics.join(', ') : (ag.avoid_topics || ''),
      enable_webcam_monitoring: sec.enable_webcam_monitoring || false,
      enable_tab_switch_detection: sec.enable_tab_switch_detection !== false,
      require_fullscreen: sec.require_fullscreen !== false,
      copy_paste_allowed: sec.copy_paste_allowed || false,
      interviewer_name: intd.name || f.interviewer_name,
      interviewer_email: intd.email || f.interviewer_email,
      meeting_link: intd.meeting_link || '',
      notes: iv.notes || '',
    }))
    setCreatedTestId(iv.id)
    setDraftInterviewId(iv.id)
    draftIdRef.current = iv.id
    draftInFlightRef.current = null

    // Check if pool questions already exist — rebuild skillQuestionPool from DB
    try {
      const res = await qApi.get(`/interviews/${iv.id}/questions`)
      const qs = Array.isArray(res.data) ? res.data : (res.data?.questions || [])
      const poolQs = qs.filter(q => q.is_pool_question)
      if (poolQs.length > 0) {
        const pool = {}
        poolQs.forEach(q => {
          const skillName = q.skill_name || q.category || 'General'
          const qType = q.question_type || 'multiple_choice'
          const key = `${skillName}__${qType}`
          if (!pool[key]) pool[key] = []
          pool[key].push(q)
        })
        setSkillQuestionPool(pool)
        setCtStep(4)
      } else {
        setCtStep(1)
      }
    } catch {
      setCtStep(1)
    }
  }

  const addSkill = () => setCtForm(f => ({ ...f, skills: [...f.skills, { name: '', difficulty: 'MEDIUM', marks_per_question: 1, question_types: [{ type: 'multiple_choice', count_per_set: 3, pool_size: null }] }] }))
  const removeSkill = (i) => setCtForm(f => ({ ...f, skills: f.skills.filter((_, j) => j !== i) }))
  const updateSkill = (i, key, val) => setCtForm(f => ({ ...f, skills: f.skills.map((s, j) => j === i ? { ...s, [key]: val } : s) }))
  const addSkillType = (skillIdx) => setCtForm(f => {
    const defaultType = (f.interview_type === 'NON_TECHNICAL') ? 'multiple_choice' : 'coding'
    return { ...f, skills: f.skills.map((s, j) => j === skillIdx ? { ...s, question_types: [...s.question_types, { type: defaultType, count_per_set: 2, pool_size: null }] } : s) }
  })
  const removeSkillType = (skillIdx, typeIdx) => setCtForm(f => ({ ...f, skills: f.skills.map((s, j) => j === skillIdx ? { ...s, question_types: s.question_types.filter((_, k) => k !== typeIdx) } : s) }))
  const updateSkillType = (skillIdx, typeIdx, key, val) => setCtForm(f => ({ ...f, skills: f.skills.map((s, j) => j === skillIdx ? { ...s, question_types: s.question_types.map((qt, k) => k === typeIdx ? { ...qt, [key]: val } : qt) } : s) }))
  const getDefaultPoolSize = (countPerSet) => {
    const minimum = countPerSet * (ctForm.num_sets || 2)
    return minimum + Math.max(Math.ceil(minimum * 0.5), 3)
  }
  const getPoolKey = (skillName, qType) => `${skillName}__${qType}`

  // Get-or-create the wizard's single DRAFT interview.
  const ensureDraft = async () => {
    const have = draftInterviewId || createdTestId || draftIdRef.current
    if (have) return have
    const realJobId = allJobs.some(j => j.job_id === ctForm.job_id) ? ctForm.job_id : null
    if (!draftInFlightRef.current) {
      draftInFlightRef.current = api.post('/interviewer/interviews/draft', {
        title: ctForm.title || `${ctForm.job_position || 'Test'} Draft`,
        job_id: realJobId,
        job_position: ctForm.job_position || '',
        job_title: ctForm.job_title || '',
        interview_type: ctForm.interview_type,
        difficulty_level: ctForm.difficulty_level,
      }).then(r => {
        draftIdRef.current = r.data.id
        setDraftInterviewId(r.data.id)
        return r.data.id
      }).catch(err => {
        draftInFlightRef.current = null
        throw err
      })
    }
    return draftInFlightRef.current
  }

  const generateSkillQuestions = async (skillIndex, typeIndex) => {
    const skill = ctForm.skills[skillIndex]
    if (!skill.name.trim()) { alert('Please enter a skill name first'); return }
    const qt = skill.question_types[typeIndex]
    const poolKey = getPoolKey(skill.name, qt.type)

    setGeneratingSkill(poolKey)
    try {
      const interviewId = await ensureDraft()
      const poolSize = qt.pool_size || getDefaultPoolSize(qt.count_per_set)
      const res = await qApi.post(`/interviews/${interviewId}/generate-skill-questions`, {
        skill_name: skill.name,
        question_type: qt.type,
        difficulty: skill.difficulty,
        pool_size: poolSize,
        marks_per_question: skill.marks_per_question || 1,
        language: qt.type === 'coding' ? (qt.language || 'python') : undefined,
        // Sent explicitly: the config holding this map is only PUT when the test is saved,
        // which happens after generation.
        time_per_question: ctForm.time_per_question_type?.[qt.type],
      })
      const newQuestions = res.data?.questions || []
      setSkillQuestionPool(prev => ({
        ...prev,
        [poolKey]: [...(prev[poolKey] || []), ...newQuestions]
      }))
      setSkillPopup({ skillIndex, typeIndex })
    } catch (err) {
      alert('Failed to generate questions: ' + (err.response?.data?.detail || err.message))
    }
    setGeneratingSkill(null)
  }

  const acceptPoolQuestion = async (poolKey, qId) => {
    const interviewId = draftInterviewId || createdTestId
    if (!interviewId) return
    try {
      await qApi.put(`/interviews/${interviewId}/questions/${qId}`, { is_approved: true, status: 'approved' })
      setSkillQuestionPool(prev => ({
        ...prev,
        [poolKey]: (prev[poolKey] || []).map(q => q.id === qId ? { ...q, status: 'approved', is_approved: true } : q)
      }))
    } catch (err) {
      alert('Failed to accept question: ' + (err.response?.data?.detail || err.message))
    }
  }
  const rejectPoolQuestion = async (poolKey, qId) => {
    const interviewId = draftInterviewId || createdTestId
    if (!interviewId) return
    try {
      await qApi.put(`/interviews/${interviewId}/questions/${qId}`, { is_approved: false, status: 'rejected' })
      setSkillQuestionPool(prev => ({
        ...prev,
        [poolKey]: (prev[poolKey] || []).map(q => q.id === qId ? { ...q, status: 'rejected', is_approved: false } : q)
      }))
    } catch (err) {
      alert('Failed to reject question')
    }
  }
  const regeneratePoolQuestion = async (poolKey, qId) => {
    const interviewId = draftInterviewId || createdTestId
    if (!interviewId) return
    setReplacingQ(qId)
    try {
      const res = await qApi.post(`/interviews/${interviewId}/questions/${qId}/regenerate`)
      const newQ = res.data
      setSkillQuestionPool(prev => ({
        ...prev,
        [poolKey]: (prev[poolKey] || []).map(q => q.id === qId ? { ...newQ, status: 'generated' } : q)
      }))
    } catch (err) {
      alert('Failed to regenerate question')
    }
    setReplacingQ(null)
  }
  const acceptAllPoolQuestions = async (poolKey) => {
    const interviewId = draftInterviewId || createdTestId
    if (!interviewId) return
    const pool = skillQuestionPool[poolKey] || []
    const pending = pool.filter(q => q.status === 'generated')
    const results = await Promise.allSettled(pending.map(q =>
      qApi.put(`/interviews/${interviewId}/questions/${q.id}`, { is_approved: true, status: 'approved' })
    ))
    const failed = results.filter(r => r.status === 'rejected').length
    if (failed > 0) alert(`${failed} question(s) could not be accepted. Please try again.`)
    const succeededIds = new Set(
      pending.filter((_, i) => results[i].status === 'fulfilled').map(q => q.id)
    )
    setSkillQuestionPool(prev => ({
      ...prev,
      [poolKey]: (prev[poolKey] || []).map(q => succeededIds.has(q.id) ? { ...q, status: 'approved', is_approved: true } : q)
    }))
  }

  const getPoolStats = (poolKey) => {
    const pool = skillQuestionPool[poolKey] || []
    return {
      total: pool.length,
      accepted: pool.filter(q => q.status === 'approved').length,
      rejected: pool.filter(q => q.status === 'rejected').length,
      pending: pool.filter(q => q.status === 'generated').length,
    }
  }

  const openPreviousQuestionsPopup = async (skillIndex, typeIndex) => {
    const skill = ctForm.skills[skillIndex]
    if (!skill?.name?.trim()) { alert('Please enter a skill name first'); return }
    const qt = skill.question_types[typeIndex]
    setPrevQPopup({ skillIndex, typeIndex })
    setPrevQFilters({ difficulty: 'all', status: 'all' })
    setPrevQSelected(new Set())
    setPrevQuestions([])
    setPrevQLoading(true)
    try {
      const res = await qApi.get('/interviews/questions/search', {
        params: { skill_name: skill.name, question_type: qt.type, limit: 200 }
      })
      setPrevQuestions(res.data || [])
    } catch (err) {
      alert('Failed to load previous questions: ' + (err.response?.data?.detail || err.message))
    }
    setPrevQLoading(false)
  }

  const importSelectedQuestions = async () => {
    if (!prevQPopup || prevQSelected.size === 0) return
    const skill = ctForm.skills[prevQPopup.skillIndex]
    const qt = ctForm.skills[prevQPopup.skillIndex]?.question_types[prevQPopup.typeIndex]
    if (!skill || !qt) return
    const poolKey = getPoolKey(skill.name, qt.type)

    let interviewId
    try {
      interviewId = await ensureDraft()
    } catch (err) {
      alert('Failed to create draft interview: ' + (err.response?.data?.detail || err.message))
      return
    }

    setPrevQImporting(true)
    try {
      const res = await qApi.post(`/interviews/${interviewId}/import-questions`, {
        question_ids: [...prevQSelected]
      })
      const imported = res.data?.questions || []
      setSkillQuestionPool(prev => ({
        ...prev,
        [poolKey]: [...(prev[poolKey] || []), ...imported]
      }))
      setPrevQPopup(null)
    } catch (err) {
      alert('Failed to import questions: ' + (err.response?.data?.detail || err.message))
    }
    setPrevQImporting(false)
  }

  // ─────────── Manual MCQ entry + CSV upload ───────────
  const openManualQuestion = (skillIndex, typeIndex) => {
    const skill = ctForm.skills[skillIndex]
    if (!skill?.name?.trim()) { alert('Please enter a skill name first'); return }
    setManualQForm({ question_text: '', options: ['', '', '', ''], correct: 0 })
    setManualQAddedCount(0)
    setManualQPopup({ skillIndex, typeIndex })
  }

  const addManualQuestion = async () => {
    if (!manualQPopup) return
    const skill = ctForm.skills[manualQPopup.skillIndex]
    const qt = skill?.question_types[manualQPopup.typeIndex]
    if (!skill || !qt) return
    const qtext = manualQForm.question_text.trim()
    const opts = manualQForm.options.map(o => o.trim())
    if (!qtext) { alert('Please enter the question text'); return }
    if (opts.some(o => !o)) { alert('Please fill in all four options'); return }
    const poolKey = getPoolKey(skill.name, qt.type)
    const letter = ['A', 'B', 'C', 'D'][manualQForm.correct] || 'A'

    let interviewId
    try {
      interviewId = await ensureDraft()
    } catch (err) {
      alert('Failed to create draft interview: ' + (err.response?.data?.detail || err.message))
      return
    }

    setManualQSaving(true)
    try {
      const res = await qApi.post(`/interviews/${interviewId}/manual-questions`, {
        skill_name: skill.name,
        question_type: 'multiple_choice',
        difficulty: skill.difficulty,
        marks_per_question: skill.marks_per_question || 1,
        questions: [{ question_text: qtext, options: opts, correct_answer: letter }],
      })
      const added = res.data?.questions || []
      if (added.length === 0) {
        // The backend de-dupes by normalized question text and returns an empty list
        // rather than an error, so an empty response is the duplicate signal.
        alert(res.data?.message || 'Question not added (possibly a duplicate)')
      } else {
        setSkillQuestionPool(prev => ({
          ...prev,
          [poolKey]: [...(prev[poolKey] || []), ...added],
        }))
        setManualQAddedCount(c => c + added.length)
        // Clear the form so the user can immediately add another
        setManualQForm({ question_text: '', options: ['', '', '', ''], correct: 0 })
      }
    } catch (err) {
      alert('Failed to add question: ' + (err.response?.data?.detail || err.message))
    }
    setManualQSaving(false)
  }

  const triggerCsvUpload = (skillIndex, typeIndex) => {
    const skill = ctForm.skills[skillIndex]
    if (!skill?.name?.trim()) { alert('Please enter a skill name first'); return }
    setCsvTarget({ skillIndex, typeIndex })
    if (csvInputRef.current) { csvInputRef.current.value = ''; csvInputRef.current.click() }
  }

  const handleCsvSelected = async (e) => {
    const fileEl = e.target
    const file = fileEl.files?.[0]
    if (!file || !csvTarget) return
    const skill = ctForm.skills[csvTarget.skillIndex]
    const qt = skill?.question_types[csvTarget.typeIndex]
    if (!skill || !qt) { fileEl.value = ''; return }
    const poolKey = getPoolKey(skill.name, qt.type)

    let interviewId
    try {
      interviewId = await ensureDraft()
    } catch (err) {
      alert('Failed to create draft interview: ' + (err.response?.data?.detail || err.message))
      fileEl.value = ''
      return
    }

    setCsvUploading(true)
    try {
      const fd = new FormData()
      fd.append('file', file)
      fd.append('skill_name', skill.name)
      fd.append('question_type', 'multiple_choice')
      fd.append('difficulty', skill.difficulty)
      fd.append('marks_per_question', String(skill.marks_per_question || 1))
      const res = await qApi.post(`/interviews/${interviewId}/manual-questions/csv`, fd, {
        headers: { 'Content-Type': 'multipart/form-data' },
      })
      const added = res.data?.questions || []
      setSkillQuestionPool(prev => ({
        ...prev,
        [poolKey]: [...(prev[poolKey] || []), ...added],
      }))
      alert(res.data?.message || `Imported ${added.length} question(s)`)
    } catch (err) {
      alert('Failed to import CSV: ' + (err.response?.data?.detail || err.message))
    }
    setCsvUploading(false)
    fileEl.value = ''
  }

  const downloadCsvTemplate = () => {
    const rows = [
      ['question', 'option_a', 'option_b', 'option_c', 'option_d', 'correct_option'],
      ['What is 2 + 2?', '3', '4', '5', '6', 'B'],
      ['Capital of France?', 'Paris', 'London', 'Berlin', 'Rome', 'A'],
    ]
    const csv = rows.map(r => r.map(c => `"${String(c).replace(/"/g, '""')}"`).join(',')).join('\r\n')
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = 'mcq_template.csv'
    a.click()
    URL.revokeObjectURL(url)
  }

  const handleCreateTest = async () => {
    if (!ctForm.job_id) {
      alert('Please select a Job Posting before creating a test.')
      return
    }
    setCtCreating(true)
    try {
      let jobId = ctForm.job_id

      // Check if selected value is a candidate request (not a real job) — interviewer only
      const isRealJob = allJobs.some(j => j.job_id === jobId)
      if (!isRealJob && showCandidateRequests) {
        const cr = crRequests.find(r => r.id === jobId)
        if (cr) {
          try {
            const skills = Array.isArray(cr.required_skills) ? cr.required_skills : (cr.required_skills ? cr.required_skills.split(',').map(s => s.trim()) : [])
            const deptRes = await api.get('/interviewer/my-assignments')
            const deptId = (deptRes.data || []).find(j => j.department?.toLowerCase() === (cr.department || '').toLowerCase())?.department_id || ''
            const jobRes = await api.post('/interviewer/auto-create-job', {
              title: cr.job_title,
              description: cr.job_description || cr.job_title,
              requirements: [cr.role_level, cr.education_requirement, cr.notes].filter(Boolean).join('. ') || cr.job_title,
              department: cr.department || '',
              employment_type: (cr.employment_type || 'full-time').toLowerCase().replace(' ', '-'),
              experience_years: cr.min_experience_years || 0,
              required_skills: skills,
            })
            jobId = jobRes.data.job_id
            await fetchAllJobs()
          } catch (err) {
            alert(err.response?.data?.detail || 'Failed to auto-create job from request.')
            setCtCreating(false)
            return
          }
        }
      }

      let testId = draftInterviewId

      if (testId) {
        // Update existing DRAFT interview with full config
        const updatePayload = {
          title: ctForm.title,
          description: ctForm.description,
          job_id: jobId || null,
          job_details: {
            job_position: ctForm.job_position,
            job_title: ctForm.job_title,
            job_level: ctForm.job_level,
            role_type: ctForm.role_type,
          },
          interview_configuration: {
            interview_type: ctForm.interview_type,
            difficulty_level: ctForm.difficulty_level,
            duration_minutes: computeDuration(ctForm),
            time_per_question_type: ctForm.time_per_question_type,
            scheduled_datetime: ctForm.scheduled_datetime || new Date().toISOString(),
          },
          skills_configuration: {
            skills: ctForm.skills.filter(s => s.name.trim()),
            number_of_topics: ctForm.skills.filter(s => s.name.trim()).length,
            topic_coverage_percent: 80,
          },
          question_format: {
            total_questions: ctForm.skills.filter(s => s.name.trim()).reduce((sum, s) => sum + s.question_types.reduce((ts, qt) => ts + qt.count_per_set, 0), 0),
            include_explanations: false, shuffle_questions: false, shuffle_options: false,
          },
          evaluation_rules: {
            total_marks: ctForm.total_marks,
            negative_marking: ctForm.negative_marking,
            negative_mark_percent: ctForm.negative_mark_percent,
            section_cutoff: ctForm.section_cutoff,
            overall_cutoff: ctForm.overall_cutoff,
          },
          ai_generation_rules: {
            conceptual_vs_practical_ratio: ctForm.conceptual_vs_practical_ratio,
            real_world_scenarios: ctForm.real_world_scenarios,
            bloom_taxonomy_level: ctForm.bloom_taxonomy_level,
            coding_focus_areas: ctForm.coding_focus_areas ? ctForm.coding_focus_areas.split(',').map(s => s.trim()) : [],
            avoid_topics: ctForm.avoid_topics ? ctForm.avoid_topics.split(',').map(s => s.trim()) : [],
          },
          security_settings: {
            enable_webcam_monitoring: ctForm.enable_webcam_monitoring,
            enable_tab_switch_detection: ctForm.enable_tab_switch_detection,
            require_fullscreen: ctForm.require_fullscreen,
            copy_paste_allowed: ctForm.copy_paste_allowed,
          },
          interviewer_details: {
            name: ctForm.interviewer_name,
            email: ctForm.interviewer_email,
            meeting_link: ctForm.meeting_link || 'N/A',
          },
          notes: ctForm.notes,
          num_sets: ctForm.num_sets,
        }
        await api.put(`/interviewer/interviews/${testId}`, updatePayload)
      } else {
        // No DRAFT exists — create full interview. Pre-check: only one interview per job allowed
        if (jobId) {
          try {
            const existingRes = await api.get('/interviewer/interviews', { params: { size: 100 } })
            const allInterviews = existingRes.data?.interviews || existingRes.data || []
            const existingForJob = allInterviews.find(iv => iv.job_id === jobId)
            if (existingForJob) {
              alert(`An interview already exists for this job (ID: ${existingForJob.id}, Status: ${existingForJob.status}).`)
              setCtCreating(false)
              return
            }
          } catch { /* proceed */ }
        }
        const payload = {
          title: ctForm.title,
          description: ctForm.description,
          job_id: jobId || null,
          job_details: { job_position: ctForm.job_position, job_title: ctForm.job_title, job_level: ctForm.job_level, role_type: ctForm.role_type },
          interview_configuration: { interview_type: ctForm.interview_type, difficulty_level: ctForm.difficulty_level, duration_minutes: computeDuration(ctForm), time_per_question_type: ctForm.time_per_question_type, scheduled_datetime: ctForm.scheduled_datetime || new Date().toISOString() },
          skills_configuration: { skills: ctForm.skills.filter(s => s.name.trim()), number_of_topics: ctForm.skills.filter(s => s.name.trim()).length, topic_coverage_percent: 80 },
          question_format: { total_questions: ctForm.skills.filter(s => s.name.trim()).reduce((sum, s) => sum + s.question_types.reduce((ts, qt) => ts + qt.count_per_set, 0), 0), include_explanations: false, shuffle_questions: false, shuffle_options: false },
          evaluation_rules: { total_marks: ctForm.total_marks, negative_marking: ctForm.negative_marking, negative_mark_percent: ctForm.negative_mark_percent, section_cutoff: ctForm.section_cutoff, overall_cutoff: ctForm.overall_cutoff },
          ai_generation_rules: { conceptual_vs_practical_ratio: ctForm.conceptual_vs_practical_ratio, real_world_scenarios: ctForm.real_world_scenarios, bloom_taxonomy_level: ctForm.bloom_taxonomy_level, coding_focus_areas: ctForm.coding_focus_areas ? ctForm.coding_focus_areas.split(',').map(s => s.trim()) : [], avoid_topics: ctForm.avoid_topics ? ctForm.avoid_topics.split(',').map(s => s.trim()) : [] },
          security_settings: { enable_webcam_monitoring: ctForm.enable_webcam_monitoring, enable_tab_switch_detection: ctForm.enable_tab_switch_detection, require_fullscreen: ctForm.require_fullscreen, copy_paste_allowed: ctForm.copy_paste_allowed },
          interviewer_details: { name: ctForm.interviewer_name, email: ctForm.interviewer_email, meeting_link: ctForm.meeting_link || 'N/A' },
          notes: ctForm.notes,
        }
        const res = await api.post('/interviewer/interviews', payload)
        testId = res.data.id
      }

      setCreatedTestId(testId)
      onSaved?.()

      // Assemble test sets from accepted pool questions
      setGenLoading(true)
      setAssembledSets([])
      setCtStep(5)
      try {
        const skillTypeTargets = ctForm.skills
          .filter(s => s.name.trim())
          .flatMap(s => s.question_types.map(qt => ({
            skill_name: s.name,
            question_type: qt.type,
            count_per_set: qt.count_per_set,
          })))

        const assembleRes = await qApi.post(`/interviews/${testId}/assemble-sets`, {
          num_sets: ctForm.num_sets || 2,
          skill_type_targets: skillTypeTargets,
        })
        const poolStatus = assembleRes.data?.pool_status || []
        setAssembledSets(poolStatus)
        setActiveSetTab(1)
      } catch (aErr) {
        const detail = aErr.response?.data?.detail
        const msg = typeof detail === 'string' ? detail : (detail?.msg || JSON.stringify(detail) || aErr.message)
        alert('Test saved but set assembly failed: ' + msg)
      }
      setGenLoading(false)
    } catch (err) {
      const detail = err.response?.data?.detail
      const msg = typeof detail === 'string' ? detail
        : Array.isArray(detail) ? detail.map(d => d.msg || d.loc?.join('.') || JSON.stringify(d)).join('\n')
        : (detail?.msg || JSON.stringify(detail) || err.message || 'Failed to create test')
      alert(msg)
    }
    setCtCreating(false)
  }

  const typeLabels = TYPE_LABELS

  return (
    <>
      <div>
        {/* Step indicator */}
        <div className="flex gap-8 mb-24">
          {[
            { n: 1, label: 'Job & Interview' },
            { n: 2, label: 'Skills & Questions' },
            { n: 3, label: 'Rules & Security' },
            { n: 4, label: 'Generate Questions' },
            { n: 5, label: 'Test Sets' },
          ].map(({ n, label }) => (
            <button key={n} className={`btn btn-sm ${ctStep === n ? 'btn-primary' : ctStep > n ? 'btn-secondary' : 'btn-ghost'}`}
              onClick={() => setCtStep(n)}>
              {n}. {label}
            </button>
          ))}
        </div>

        {/* Step 1: Job & Interview Details */}
        {ctStep === 1 && (
          <div className="card">
            <h3 style={{ marginBottom: 20 }}>Job & Interview Details</h3>
            <div className="auth-form">
              <div className="input-group" style={{ marginBottom: 16, padding: 14, background: 'var(--accent-light)', borderRadius: 10, border: '1px solid var(--border-accent)' }}>
                <label style={{ color: 'var(--accent-primary)' }}>Link to Job Posting <span style={{ color: '#ef4444' }}>*</span> (required for publishing)</label>
                <select className="select" value={ctForm.job_id}
                  onChange={e => applyJobSelection(e.target.value)}>
                  <option value="">Select a job...</option>
                  {allJobs.length > 0 && <optgroup label="Job Postings">
                    {allJobs.map(j => {
                      const draft = isResumableDraft(j)
                      // Blocked jobs stay VISIBLE but disabled. Filtering them out made a job
                      // vanish with no explanation — and worse, a preselected job that had a
                      // draft had no matching <option>, so the select fell back to showing
                      // "Select a job..." while ctForm.job_id was in fact set.
                      const blocked = j.has_test && !draft
                      const suffix = draft
                        ? ' (draft in progress)'
                        : blocked
                          ? ' (test already exists)'
                          : j.status !== 'active' ? ` (${j.status})` : ''
                      return (
                        <option key={j.job_id} value={j.job_id} disabled={blocked}>
                          {j.job_title} — {j.department}{suffix}
                        </option>
                      )
                    })}
                  </optgroup>}
                  {showCandidateRequests && (() => {
                    const unmatchedRequests = crRequests.filter(r => !r.job_id)
                    return unmatchedRequests.length > 0 ? (
                      <optgroup label="My Candidate Requests (No job created yet)">
                        {unmatchedRequests.map(r => (
                          <option key={'cr-' + r.id} value={r.id}>
                            {r.job_title} — {r.department || 'New'} (will auto-create job)
                          </option>
                        ))}
                      </optgroup>
                    ) : null
                  })()}
                </select>
                {resumedDraft && (
                  <div style={{ marginTop: 10, fontSize: '0.8rem', color: 'var(--accent-primary)', fontWeight: 600 }}>
                    Resuming an unfinished draft for this job —{' '}
                    {resumedDraft.question_count > 0
                      ? `${resumedDraft.question_count} question${resumedDraft.question_count === 1 ? '' : 's'} already in the pool.`
                      : 'nothing generated in it yet.'}
                  </div>
                )}
              </div>
              <div className="grid-2">
                <div className="input-group">
                  <label>Test Title *</label>
                  <input className="input" required placeholder="e.g. Senior React Developer Test"
                    value={ctForm.title} onChange={e => setCtForm({ ...ctForm, title: e.target.value })} />
                </div>
                <div className="input-group">
                  <label>Description</label>
                  <input className="input" placeholder="Brief description of the test"
                    value={ctForm.description} onChange={e => setCtForm({ ...ctForm, description: e.target.value })} />
                </div>
              </div>
              <div className="grid-2">
                <div className="input-group">
                  <label>Job Position *</label>
                  <input className="input" required placeholder="e.g. Frontend Developer"
                    value={ctForm.job_position} onChange={e => setCtForm({ ...ctForm, job_position: e.target.value })} />
                </div>
                <div className="input-group">
                  <label>Job Title *</label>
                  <input className="input" required placeholder="e.g. Senior Frontend Developer"
                    value={ctForm.job_title} onChange={e => setCtForm({ ...ctForm, job_title: e.target.value })} />
                </div>
              </div>
              <div className="grid-2">
                <div className="input-group">
                  <label>Job Level</label>
                  <select className="select" value={ctForm.job_level} onChange={e => setCtForm({ ...ctForm, job_level: e.target.value })}>
                    {['Fresher', 'Junior', 'Mid-level', 'Senior', 'Lead', 'Manager'].map(l => <option key={l} value={l}>{l}</option>)}
                  </select>
                </div>
                <div className="input-group">
                  <label>Role Type</label>
                  <select className="select" value={ctForm.role_type} onChange={e => setCtForm({ ...ctForm, role_type: e.target.value })}>
                    {['Full-time', 'Part-time', 'Contract', 'Internship'].map(r => <option key={r} value={r}>{r}</option>)}
                  </select>
                </div>
              </div>
              <div className="grid-2">
                <div className="input-group">
                  <label>Interview Type</label>
                  <select className="select" value={ctForm.interview_type} onChange={e => setCtForm({ ...ctForm, interview_type: e.target.value })}>
                    <option value="TECHNICAL">Technical</option>
                    <option value="NON_TECHNICAL">Non-Technical</option>
                    <option value="MIXED">Mixed</option>
                  </select>
                </div>
                <div className="input-group">
                  <label>Difficulty Level</label>
                  <select className="select" value={ctForm.difficulty_level} onChange={e => setCtForm({ ...ctForm, difficulty_level: e.target.value })}>
                    <option value="EASY">Easy</option>
                    <option value="MEDIUM">Medium</option>
                    <option value="HARD">Hard</option>
                  </select>
                </div>
              </div>
              {/* Time per question type. The total duration below is computed from these and
                  the per-type counts set on Step 2 — there is no separate duration input. */}
              <label style={{ fontWeight: 600, display: 'block', marginBottom: 8 }}>Time Per Question Type (min)</label>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 12, marginBottom: 12 }}>
                {Object.keys(TYPE_TIME_DEFAULTS)
                  // NON_TECHNICAL tests hide the Coding type on Step 2, so its time is meaningless here.
                  .filter(t => t !== 'coding' || ctForm.interview_type !== 'NON_TECHNICAL')
                  .map(t => (
                    <div className="input-group" key={t} style={{ marginBottom: 0 }}>
                      <label style={{ fontSize: '0.82rem' }}>{TYPE_LABELS[t]}</label>
                      <input className="input" type="number" min={1} max={180}
                        value={ctForm.time_per_question_type[t] ?? TYPE_TIME_DEFAULTS[t]}
                        onChange={e => setCtForm({
                          ...ctForm,
                          time_per_question_type: {
                            ...ctForm.time_per_question_type,
                            [t]: parseInt(e.target.value) || TYPE_TIME_DEFAULTS[t],
                          },
                        })} />
                    </div>
                  ))}
              </div>
              {(() => {
                const total = computeDuration(ctForm)
                return (
                  <div style={{ padding: '10px 14px', background: 'var(--accent-glow)', borderRadius: 8, marginBottom: 12, fontSize: '0.85rem' }}>
                    <strong>Total Duration:</strong>{' '}
                    {total > 0
                      ? <>{total} min <span style={{ color: 'var(--text-tertiary)' }}>· calculated from the question counts on Step 2</span></>
                      : <span style={{ color: 'var(--text-tertiary)' }}>— add skills and question counts on Step 2</span>}
                  </div>
                )
              })()}
              <div style={{ textAlign: 'right', marginTop: 12 }}>
                <button className="btn btn-primary" onClick={() => setCtStep(2)}>Next: Skills & Questions</button>
              </div>
            </div>
          </div>
        )}

        {/* Step 2: Skills & Question Types */}
        {ctStep === 2 && (
          <div className="card">
            <h3 style={{ marginBottom: 20 }}>Skills & Question Types</h3>
            <div className="auth-form">
              <div className="flex-between mb-16">
                <label style={{ fontWeight: 600 }}>Skills to Test</label>
                <button className="btn btn-ghost btn-sm" onClick={addSkill}><Plus size={14} /> Add Skill</button>
              </div>
              {ctForm.skills.map((s, si) => (
                <div key={si} className="card" style={{ marginBottom: 16, padding: '16px 20px', background: 'var(--bg-hover)', border: '1px solid var(--border-subtle)', borderRadius: 12 }}>
                  <div className="flex gap-12 mb-16" style={{ alignItems: 'flex-end' }}>
                    <div className="input-group" style={{ flex: 2, marginBottom: 0 }}>
                      <label>Skill Name</label>
                      <input className="input" placeholder="e.g. React, Node.js" value={s.name}
                        onChange={e => updateSkill(si, 'name', e.target.value)} />
                    </div>
                    <div className="input-group" style={{ flex: 1, marginBottom: 0 }}>
                      <label>Difficulty</label>
                      <select className="select" value={s.difficulty}
                        onChange={e => updateSkill(si, 'difficulty', e.target.value)}>
                        <option value="EASY">Easy</option><option value="MEDIUM">Medium</option><option value="HARD">Hard</option>
                      </select>
                    </div>
                    <div className="input-group" style={{ flex: 0.8, marginBottom: 0 }}>
                      <label>Weightage</label>
                      <input className="input" type="number" min={1} max={100} value={s.marks_per_question}
                        onChange={e => updateSkill(si, 'marks_per_question', parseInt(e.target.value) || 1)} />
                    </div>
                    {ctForm.skills.length > 1 && (
                      <button className="btn btn-danger btn-sm" style={{ marginBottom: 2 }} onClick={() => removeSkill(si)}>
                        <XCircle size={14} />
                      </button>
                    )}
                  </div>

                  {/* Question types for this skill */}
                  <div style={{ marginBottom: 8 }}>
                    <div className="flex-between" style={{ marginBottom: 8 }}>
                      <label style={{ fontSize: '0.82rem', fontWeight: 600, color: 'var(--text-secondary)' }}>Question Types</label>
                      <button className="btn btn-ghost btn-sm" style={{ fontSize: '0.75rem', padding: '2px 8px' }}
                        onClick={() => addSkillType(si)}><Plus size={12} /> Add Type</button>
                    </div>
                    {s.question_types.map((qt, ti) => (
                      <div key={ti} className="flex gap-8 mb-8" style={{ alignItems: 'center' }}>
                        <select className="select" value={qt.type} style={{ flex: 1.5 }}
                          onChange={e => updateSkillType(si, ti, 'type', e.target.value)}>
                          <option value="multiple_choice">MCQ</option>
                          {ctForm.interview_type !== 'NON_TECHNICAL' && (
                            <option value="coding">Coding</option>
                          )}
                          <option value="short_answer">Descriptive</option>
                          <option value="case_study">Case Study</option>
                          <option value="aptitude">Aptitude</option>
                          <option value="communication">Communication</option>
                        </select>
                        {qt.type === 'coding' && (
                          <select className="select" value={qt.language || 'python'} style={{ flex: 1.2 }}
                            title="Programming language candidates must answer this coding question in"
                            onChange={e => updateSkillType(si, ti, 'language', e.target.value)}>
                            <option value="python">Python</option>
                            <option value="java">Java</option>
                            <option value="cpp">C++</option>
                            <option value="javascript">JavaScript</option>
                            <option value="sql">SQL</option>
                          </select>
                        )}
                        <div style={{ flex: 1, display: 'flex', alignItems: 'center', gap: 4 }}>
                          <input className="input" type="number" min={1} max={20} value={qt.count_per_set}
                            onChange={e => updateSkillType(si, ti, 'count_per_set', parseInt(e.target.value) || 1)}
                            style={{ width: '100%' }} />
                          <span style={{ fontSize: '0.72rem', color: 'var(--text-tertiary)', whiteSpace: 'nowrap' }}>/set</span>
                        </div>
                        <div style={{ flex: 1, display: 'flex', alignItems: 'center', gap: 4 }}>
                          <input className="input" type="number" min={1} max={100}
                            value={qt.pool_size || getDefaultPoolSize(qt.count_per_set)}
                            onChange={e => updateSkillType(si, ti, 'pool_size', parseInt(e.target.value) || null)}
                            style={{ width: '100%' }}
                            title="Total questions to generate for review" />
                          <span style={{ fontSize: '0.72rem', color: 'var(--text-tertiary)', whiteSpace: 'nowrap' }}>pool</span>
                        </div>
                        {s.question_types.length > 1 && (
                          <button className="btn btn-ghost btn-sm" style={{ padding: '2px 6px', color: '#ef4444' }}
                            onClick={() => removeSkillType(si, ti)}>
                            <XCircle size={12} />
                          </button>
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              ))}

              {/* Summary */}
              {(() => {
                const validSkills = ctForm.skills.filter(s => s.name.trim())
                if (validSkills.length === 0) return null
                const typeTotals = {}
                let totalMarksPerSet = 0
                validSkills.forEach(s => s.question_types.forEach(qt => {
                  typeTotals[qt.type] = (typeTotals[qt.type] || 0) + qt.count_per_set
                  totalMarksPerSet += qt.count_per_set * (s.marks_per_question || 1)
                }))
                const totalPerSet = Object.values(typeTotals).reduce((a, b) => a + b, 0)
                const totalMinutes = computeDuration(ctForm)
                return (
                  <div style={{ padding: '10px 14px', background: 'var(--accent-glow)', borderRadius: 8, marginBottom: 16, fontSize: '0.82rem' }}>
                    <strong>Per set:</strong> {totalPerSet} questions &middot; {totalMarksPerSet} marks &middot; {totalMinutes} min
                    {' '}({Object.entries(typeTotals).map(([t, c]) => {
                      const per = ctForm.time_per_question_type?.[t] ?? TYPE_TIME_DEFAULTS[t] ?? 5
                      return `${c} ${typeLabels[t] || t} × ${per}m`
                    }).join(', ')})
                    {' '} <strong>Sets:</strong> {ctForm.num_sets}
                  </div>
                )
              })()}

              <div className="flex gap-12" style={{ justifyContent: 'space-between', marginTop: 16 }}>
                <button className="btn btn-secondary" onClick={() => setCtStep(1)}>Back</button>
                <button className="btn btn-primary" onClick={() => setCtStep(3)}>Next: Rules & Security</button>
              </div>
            </div>
          </div>
        )}

        {/* Step 3: Evaluation Rules, AI Rules & Security */}
        {ctStep === 3 && (
          <div className="card">
            <h3 style={{ marginBottom: 20 }}>Evaluation Rules & Security</h3>
            <div className="auth-form">
              <h4 style={{ marginBottom: 12 }}>Evaluation</h4>
              <div className="grid-2">
                <div className="input-group">
                  <label>Total Marks</label>
                  <input className="input" type="number" value={ctForm.total_marks}
                    onChange={e => setCtForm({ ...ctForm, total_marks: parseInt(e.target.value) || 100 })} />
                </div>
                <div className="input-group">
                  <label>Overall Cutoff (%)</label>
                  <input className="input" type="number" min={0} max={100} value={ctForm.overall_cutoff}
                    onChange={e => setCtForm({ ...ctForm, overall_cutoff: parseInt(e.target.value) || 0 })} />
                </div>
              </div>
              <div className="grid-2">
                <div className="input-group">
                  <label>Number of Test Sets</label>
                  <input className="input" type="number" min={1} max={10} value={ctForm.num_sets}
                    onChange={e => setCtForm({ ...ctForm, num_sets: parseInt(e.target.value) || 2 })} />
                  <span style={{ fontSize: '0.72rem', color: 'var(--text-tertiary)', marginTop: 4 }}>
                    Each candidate is randomly assigned one set
                  </span>
                </div>
              </div>
              <div className="grid-2">
                <div className="input-group">
                  <label>Section Cutoff (%)</label>
                  <input className="input" type="number" min={0} max={100} value={ctForm.section_cutoff}
                    onChange={e => setCtForm({ ...ctForm, section_cutoff: parseInt(e.target.value) || 0 })} />
                </div>
                <div className="input-group">
                  <label style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <input type="checkbox" checked={ctForm.negative_marking}
                      onChange={e => setCtForm({ ...ctForm, negative_marking: e.target.checked })} />
                    Negative Marking
                  </label>
                  {ctForm.negative_marking && (
                    <input className="input" type="number" min={0} max={100} placeholder="Negative mark %"
                      value={ctForm.negative_mark_percent}
                      onChange={e => setCtForm({ ...ctForm, negative_mark_percent: parseInt(e.target.value) || 0 })} />
                  )}
                </div>
              </div>

              <h4 style={{ marginTop: 24, marginBottom: 12 }}>AI Generation Rules</h4>
              <div className="grid-2">
                {ctForm.interview_type !== 'NON_TECHNICAL' && (
                  <div className="input-group">
                    <label>Coding Focus Areas (comma-separated)</label>
                    <input className="input" placeholder="algorithms, data structures..."
                      value={ctForm.coding_focus_areas} onChange={e => setCtForm({ ...ctForm, coding_focus_areas: e.target.value })} />
                  </div>
                )}
                <div className="input-group">
                  <label>Avoid Topics (comma-separated)</label>
                  <input className="input" placeholder="regex, bitwise..."
                    value={ctForm.avoid_topics} onChange={e => setCtForm({ ...ctForm, avoid_topics: e.target.value })} />
                </div>
              </div>
              <div className="input-group">
                <label style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <input type="checkbox" checked={ctForm.real_world_scenarios}
                    onChange={e => setCtForm({ ...ctForm, real_world_scenarios: e.target.checked })} />
                  Include Real-World Scenarios
                </label>
              </div>

              <h4 style={{ marginTop: 24, marginBottom: 12 }}>Security Settings</h4>
              <div className="grid-2">
                <div className="input-group" style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                  <label style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <input type="checkbox" checked={ctForm.enable_webcam_monitoring}
                      onChange={e => setCtForm({ ...ctForm, enable_webcam_monitoring: e.target.checked })} />
                    Webcam Monitoring
                  </label>
                  <label style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <input type="checkbox" checked={ctForm.enable_tab_switch_detection}
                      onChange={e => setCtForm({ ...ctForm, enable_tab_switch_detection: e.target.checked })} />
                    Tab Switch Detection
                  </label>
                  <label style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <input type="checkbox" checked={ctForm.require_fullscreen}
                      onChange={e => setCtForm({ ...ctForm, require_fullscreen: e.target.checked })} />
                    Require Fullscreen
                  </label>
                  <label style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <input type="checkbox" checked={ctForm.copy_paste_allowed}
                      onChange={e => setCtForm({ ...ctForm, copy_paste_allowed: e.target.checked })} />
                    Allow Copy/Paste
                  </label>
                </div>
              </div>

              <div className="flex gap-12" style={{ justifyContent: 'space-between', marginTop: 16 }}>
                <button className="btn btn-secondary" onClick={() => setCtStep(2)}>Back</button>
                <button className="btn btn-primary" onClick={() => setCtStep(4)}>Next: Generate Questions</button>
              </div>
            </div>
          </div>
        )}

        {/* Step 4: Generate Questions */}
        {ctStep === 4 && (
          <div className="card">
            <h3 style={{ marginBottom: 20 }}>Generate AI Questions</h3>
            <div className="auth-form">
              <div className="mb-16">
                <label style={{ fontWeight: 600 }}>Generate questions for each skill and type</label>
                <div style={{ fontSize: '0.78rem', color: 'var(--text-tertiary)', marginTop: 4 }}>
                  Questions are generated into a pool. Review and approve the best ones, then assemble into test sets.
                </div>
              </div>

              {/* Per-skill, per-type generation cards */}
              {ctForm.skills.filter(s => s.name.trim()).map((s, i) => {
                const realIndex = ctForm.skills.findIndex(sk => sk === s)
                return (
                  <div key={i} className="card" style={{ marginBottom: 16, padding: '16px 20px', background: 'var(--bg-hover)', borderRadius: 12 }}>
                    <div style={{ fontWeight: 700, fontSize: '0.95rem', marginBottom: 4 }}>{s.name}</div>
                    <div style={{ fontSize: '0.78rem', color: 'var(--text-tertiary)', marginBottom: 12 }}>
                      {s.difficulty} &middot; {s.marks_per_question || 1} marks/question
                    </div>
                    {s.question_types.map((qt, ti) => {
                      const poolKey = getPoolKey(s.name, qt.type)
                      const stats = getPoolStats(poolKey)
                      const needed = qt.count_per_set * (ctForm.num_sets || 2)
                      const poolSize = qt.pool_size || getDefaultPoolSize(qt.count_per_set)
                      const isReady = stats.accepted >= needed
                      return (
                        <div key={ti} style={{
                          // Wraps rather than overflowing: body has overflow-x:hidden, so a row that
                          // runs past the right edge silently swallows its last buttons (Add Manually,
                          // CSV) with no scrollbar to reach them.
                          display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 12, rowGap: 8,
                          padding: '10px 14px', marginBottom: 8,
                          background: 'var(--bg-primary)', borderRadius: 8,
                          borderLeft: `3px solid ${isReady ? '#22c55e' : stats.total > 0 ? '#f59e0b' : 'var(--border-subtle)'}`,
                        }}>
                          <div style={{ flex: 1, minWidth: 180 }}>
                            <div style={{ fontWeight: 600, fontSize: '0.85rem' }}>{typeLabels[qt.type] || qt.type}</div>
                            <div style={{ fontSize: '0.72rem', color: 'var(--text-tertiary)', marginTop: 2 }}>
                              {qt.count_per_set}/set &middot; need {needed} for {ctForm.num_sets} sets &middot; pool: {poolSize}
                            </div>
                          </div>
                          <div className="flex gap-6" style={{ alignItems: 'center', flexWrap: 'wrap', justifyContent: 'flex-end', minWidth: 0 }}>
                            {stats.total > 0 && (<>
                              <span className="badge badge-green" style={{ fontSize: '0.7rem' }}>{stats.accepted} accepted</span>
                              <span className="badge badge-neutral" style={{ fontSize: '0.7rem' }}>{stats.pending} pending</span>
                              {stats.rejected > 0 && <span className="badge badge-red" style={{ fontSize: '0.7rem' }}>{stats.rejected} rej</span>}
                              <button className="btn btn-ghost btn-sm" style={{ fontSize: '0.72rem', padding: '2px 6px' }}
                                onClick={() => setSkillPopup({ skillIndex: realIndex, typeIndex: ti })}>
                                <Eye size={11} /> View
                              </button>
                            </>)}
                            <button className="btn btn-primary btn-sm" style={{ whiteSpace: 'nowrap', fontSize: '0.78rem' }}
                              disabled={generatingSkill === poolKey}
                              onClick={() => generateSkillQuestions(realIndex, ti)}>
                              {generatingSkill === poolKey
                                ? <><Loader2 size={12} className="spin" /> Generating...</>
                                : <><Brain size={12} /> {stats.total > 0 ? 'More' : `Generate ${poolSize}`}</>}
                            </button>
                            <button className="btn btn-secondary btn-sm" style={{ whiteSpace: 'nowrap', fontSize: '0.78rem' }}
                              onClick={() => openPreviousQuestionsPopup(realIndex, ti)}>
                              <BookOpen size={12} /> Use Previous
                            </button>
                            {qt.type === 'multiple_choice' && (<>
                              <button className="btn btn-secondary btn-sm" style={{ whiteSpace: 'nowrap', fontSize: '0.78rem' }}
                                onClick={() => openManualQuestion(realIndex, ti)}>
                                <Plus size={12} /> Add Manually
                              </button>
                              <button className="btn btn-secondary btn-sm" style={{ whiteSpace: 'nowrap', fontSize: '0.78rem' }}
                                disabled={csvUploading}
                                onClick={() => triggerCsvUpload(realIndex, ti)}>
                                {csvUploading
                                  ? <><Loader2 size={12} className="spin" /> Uploading...</>
                                  : <><Upload size={12} /> CSV</>}
                              </button>
                            </>)}
                          </div>
                          {stats.total > 0 && stats.accepted < needed && (
                            <div style={{ flexBasis: '100%', fontSize: '0.7rem', color: '#ef4444', fontWeight: 600 }}>
                              Need {needed - stats.accepted} more
                            </div>
                          )}
                        </div>
                      )
                    })}
                  </div>
                )
              })}

              {ctForm.skills.filter(s => s.name.trim()).length === 0 && (
                <div style={{ padding: 32, textAlign: 'center', color: 'var(--text-tertiary)' }}>
                  No skills configured. Go back to Step 2 to add skills.
                </div>
              )}

              <div className="flex gap-12" style={{ justifyContent: 'space-between', marginTop: 16 }}>
                <button className="btn btn-secondary" onClick={() => setCtStep(3)}>Back</button>
                <button className="btn btn-primary" onClick={() => setCtStep(5)}>Next: Test Sets</button>
              </div>
            </div>
          </div>
        )}

        {/* Step 5: Test Sets */}
        {ctStep === 5 && (
          <div>
            {/* Pre-assembly: review + create button */}
            {assembledSets.length === 0 && !genLoading && (
              <div className="card" style={{ marginBottom: 16 }}>
                <h3 style={{ marginBottom: 20 }}>Review & Generate Test Sets</h3>

                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16, marginBottom: 24 }}>
                  <div className="card" style={{ background: 'var(--bg-hover)', padding: 16 }}>
                    <h4 style={{ marginBottom: 8, color: 'var(--accent-primary)' }}>Job Details</h4>
                    <div className="text-sm"><strong>Title:</strong> {ctForm.title || '—'}</div>
                    <div className="text-sm"><strong>Position:</strong> {ctForm.job_position} — {ctForm.job_title}</div>
                    <div className="text-sm"><strong>Level:</strong> {ctForm.job_level} · {ctForm.role_type}</div>
                  </div>
                  <div className="card" style={{ background: 'var(--bg-hover)', padding: 16 }}>
                    <h4 style={{ marginBottom: 8, color: 'var(--accent-primary)' }}>Interview Config</h4>
                    <div className="text-sm"><strong>Type:</strong> {ctForm.interview_type} · {ctForm.difficulty_level}</div>
                    <div className="text-sm"><strong>Duration:</strong> {computeDuration(ctForm)} min</div>
                    <div className="text-sm text-muted" style={{ fontSize: '0.78rem' }}>
                      {Object.keys(TYPE_TIME_DEFAULTS)
                        .filter(t => t !== 'coding' || ctForm.interview_type !== 'NON_TECHNICAL')
                        .map(t => `${TYPE_LABELS[t]} ${ctForm.time_per_question_type?.[t] ?? TYPE_TIME_DEFAULTS[t]}m`)
                        .join(' · ')}
                    </div>
                  </div>
                  <div className="card" style={{ background: 'var(--bg-hover)', padding: 16 }}>
                    <h4 style={{ marginBottom: 8, color: 'var(--accent-primary)' }}>Skills & Question Pool ({ctForm.skills.filter(s => s.name).length})</h4>
                    {ctForm.skills.filter(s => s.name).map((s, i) => (
                      <div key={i} className="text-sm" style={{ marginBottom: 6 }}>
                        <strong>{s.name}</strong> ({s.difficulty}, {s.marks_per_question || 1} marks/Q)
                        {s.question_types.map((qt, ti) => {
                          const poolKey = getPoolKey(s.name, qt.type)
                          const st = getPoolStats(poolKey)
                          const needed = qt.count_per_set * (ctForm.num_sets || 2)
                          return (
                            <div key={ti} style={{ marginLeft: 12, fontSize: '0.78rem', color: 'var(--text-secondary)' }}>
                              {typeLabels[qt.type] || qt.type}: {qt.count_per_set}/set
                              {st.total > 0 && (
                                <span style={{ marginLeft: 6, color: st.accepted >= needed ? '#22c55e' : '#f59e0b', fontWeight: 600 }}>
                                  ({st.accepted}/{needed} accepted)
                                </span>
                              )}
                              {st.total === 0 && <span style={{ marginLeft: 6, color: '#ef4444', fontWeight: 600 }}>(not generated)</span>}
                            </div>
                          )
                        })}
                      </div>
                    ))}
                    {ctForm.skills.filter(s => s.name).length === 0 && <div className="text-sm text-muted">No skills added</div>}
                  </div>
                  <div className="card" style={{ background: 'var(--bg-hover)', padding: 16 }}>
                    <h4 style={{ marginBottom: 8, color: 'var(--accent-primary)' }}>Evaluation</h4>
                    <div className="text-sm"><strong>Total Marks:</strong> {ctForm.total_marks}</div>
                    <div className="text-sm"><strong>Cutoff:</strong> Section {ctForm.section_cutoff}% · Overall {ctForm.overall_cutoff}%</div>
                    <div className="text-sm"><strong>Negative Marking:</strong> {ctForm.negative_marking ? `Yes (${ctForm.negative_mark_percent}%)` : 'No'}</div>
                    <div className="text-sm"><strong>Test Sets:</strong> {ctForm.num_sets}</div>
                  </div>
                  <div className="card" style={{ background: 'var(--bg-hover)', padding: 16 }}>
                    <h4 style={{ marginBottom: 8, color: 'var(--accent-primary)' }}>Security</h4>
                    <div className="text-sm"><strong>Webcam:</strong> {ctForm.enable_webcam_monitoring ? 'Yes' : 'No'}</div>
                    <div className="text-sm"><strong>Tab Detection:</strong> {ctForm.enable_tab_switch_detection ? 'Yes' : 'No'}</div>
                    <div className="text-sm"><strong>Fullscreen:</strong> {ctForm.require_fullscreen ? 'Required' : 'Not required'}</div>
                    <div className="text-sm"><strong>Copy/Paste:</strong> {ctForm.copy_paste_allowed ? 'Allowed' : 'Blocked'}</div>
                  </div>
                </div>

                <div className="input-group">
                  <label>Interviewer</label>
                  <div className="grid-2">
                    <input className="input" placeholder="Your Name" value={ctForm.interviewer_name}
                      onChange={e => setCtForm({ ...ctForm, interviewer_name: e.target.value })} />
                    <input className="input" placeholder="Your Email" value={ctForm.interviewer_email}
                      onChange={e => setCtForm({ ...ctForm, interviewer_email: e.target.value })} />
                  </div>
                </div>
                <div className="input-group">
                  <label>Meeting Link (optional)</label>
                  <input className="input" placeholder="https://meet.google.com/..." value={ctForm.meeting_link}
                    onChange={e => setCtForm({ ...ctForm, meeting_link: e.target.value })} />
                </div>
                <div className="input-group">
                  <label>Notes (optional)</label>
                  <textarea className="textarea" rows={2} value={ctForm.notes}
                    onChange={e => setCtForm({ ...ctForm, notes: e.target.value })} />
                </div>

                <div className="flex gap-12" style={{ justifyContent: 'space-between', marginTop: 16 }}>
                  <button className="btn btn-secondary" onClick={() => setCtStep(4)}>Back</button>
                  <button className="btn btn-primary" disabled={ctCreating} onClick={handleCreateTest}>
                    {ctCreating ? <><Loader2 size={16} className="spin" /> Creating & Assembling Sets...</> : <><Plus size={16} /> Generate Test Sets</>}
                  </button>
                </div>
              </div>
            )}

            {/* Loading state */}
            {genLoading && (
              <div className="card" style={{ padding: 60, textAlign: 'center' }}>
                <Loader2 size={40} className="spin" style={{ color: 'var(--accent-primary)', marginBottom: 16 }} />
                <div style={{ fontSize: '1.05rem', fontWeight: 600, marginBottom: 6 }}>Assembling Test Sets...</div>
                <div className="text-sm text-muted">Building {ctForm.num_sets} sets from your accepted question pool</div>
              </div>
            )}

            {/* Assembly configured — pool readiness */}
            {!genLoading && assembledSets.length > 0 && (
              <>
                {/* Pool Readiness Summary & Publish bar */}
                <div className="card mb-16" style={{ padding: '14px 20px' }}>
                  <div className="flex-between">
                    <div className="flex gap-16" style={{ alignItems: 'center', flexWrap: 'wrap' }}>
                      <span style={{ fontSize: '0.9rem', fontWeight: 700 }}>
                        Pool Ready — Questions will be randomly assembled per candidate
                      </span>
                      {assembledSets.map((p, i) => (
                        <span key={i} className={`badge ${p.status === 'ready' ? 'badge-green' : p.status === 'tight' ? 'badge-yellow' : 'badge-red'}`}
                          style={{ background: p.status === 'ready' ? '#22c55e22' : p.status === 'tight' ? '#f59e0b22' : '#ef444422',
                                   color: p.status === 'ready' ? '#22c55e' : p.status === 'tight' ? '#f59e0b' : '#ef4444',
                                   border: `1px solid ${p.status === 'ready' ? '#22c55e44' : p.status === 'tight' ? '#f59e0b44' : '#ef444444'}`,
                                   padding: '4px 10px', borderRadius: 6, fontSize: '0.8rem', fontWeight: 600 }}>
                          {p.skill_name}/{p.question_type}: {p.available} pool / {p.needed} per set ({p.status})
                        </span>
                      ))}
                    </div>
                    <div className="flex gap-8" style={{ alignItems: 'center' }}>
                      <select value={selectedHrId} onChange={e => setSelectedHrId(e.target.value)}
                        style={{ padding: '6px 10px', borderRadius: 6, border: '1px solid var(--border)', fontSize: '0.85rem', minWidth: 180 }}>
                        <option value="">-- Select HR to Notify --</option>
                        {hrList.map(h => (
                          <option key={h.id} value={h.id}>{h.name} ({h.department})</option>
                        ))}
                      </select>
                      <button className="btn btn-primary btn-sm" disabled={publishLoading} onClick={async () => {
                        if (!selectedHrId) { alert('Please select an HR to notify before publishing'); return }
                        setPublishLoading(true)
                        try {
                          const testId = createdTestId || draftInterviewId
                          await api.post(`/interviewer/interviews/${testId}/publish`, { hr_id: selectedHrId })
                          const hr = hrList.find(h => h.id === selectedHrId)
                          alert(`Test published!\n${hr?.name || 'HR'} (${hr?.email || ''}) has been notified.\nUnique question sets will be assembled for each candidate at invite time.`)
                          onPublished?.()
                          setCtStep(1)
                          setAssembledSets([])
                          setSkillQuestionPool({})
                          setCreatedTestId(null)
                          setDraftInterviewId(null)
                          draftIdRef.current = null
                          draftInFlightRef.current = null
                          setSelectedHrId('')
                          onNavigateAway?.()
                        } catch (err) {
                          alert(err.response?.data?.detail || 'Failed to publish test')
                        }
                        setPublishLoading(false)
                      }}>{publishLoading ? <><Loader2 size={14} className="spin" /> Publishing...</> : <><Send size={14} /> Publish Test</>}</button>
                    </div>
                  </div>
                </div>

                {/* Pool details table */}
                {(() => {
                  const totalQsPerSet = assembledSets.reduce((s, p) => s + p.needed, 0)
                  const totalPool = assembledSets.reduce((s, p) => s + p.available, 0)

                  return (
                    <>
                      <div className="card mb-16" style={{ padding: '10px 16px', background: 'var(--bg-hover)' }}>
                        <span className="text-sm"><strong>Pool Summary</strong> — {totalQsPerSet} questions per candidate · {totalPool} total in pool</span>
                      </div>
                      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.85rem' }}>
                        <thead>
                          <tr style={{ borderBottom: '2px solid var(--border)' }}>
                            <th style={{ textAlign: 'left', padding: '8px 12px' }}>Skill</th>
                            <th style={{ textAlign: 'left', padding: '8px 12px' }}>Type</th>
                            <th style={{ textAlign: 'center', padding: '8px 12px' }}>Per Candidate</th>
                            <th style={{ textAlign: 'center', padding: '8px 12px' }}>Pool Size</th>
                            <th style={{ textAlign: 'center', padding: '8px 12px' }}>Status</th>
                          </tr>
                        </thead>
                        <tbody>
                          {assembledSets.map((p, i) => (
                            <tr key={i} style={{ borderBottom: '1px solid var(--border-subtle)' }}>
                              <td style={{ padding: '8px 12px', fontWeight: 600 }}>{p.skill_name}</td>
                              <td style={{ padding: '8px 12px' }}>{(p.question_type || '').replace('_', ' ')}</td>
                              <td style={{ textAlign: 'center', padding: '8px 12px' }}>{p.needed}</td>
                              <td style={{ textAlign: 'center', padding: '8px 12px', fontWeight: 600 }}>{p.available}</td>
                              <td style={{ textAlign: 'center', padding: '8px 12px' }}>
                                <span style={{
                                  padding: '2px 8px', borderRadius: 4, fontSize: '0.75rem', fontWeight: 700,
                                  background: p.status === 'ready' ? '#22c55e22' : p.status === 'tight' ? '#f59e0b22' : '#ef444422',
                                  color: p.status === 'ready' ? '#22c55e' : p.status === 'tight' ? '#f59e0b' : '#ef4444',
                                }}>
                                  {p.status === 'ready' ? 'Ready' : p.status === 'tight' ? 'Tight' : 'Insufficient'}
                                </span>
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                      <div className="text-sm text-muted" style={{ marginTop: 12, padding: '8px 12px', background: 'var(--bg-hover)', borderRadius: 8 }}>
                        Each candidate will receive a unique, randomly assembled question set from the approved pool when HR sends the test invite.
                      </div>
                    </>
                  )
                })()}
              </>
            )}

            {/* No pool configured — only show after assembly was attempted */}
            {!genLoading && assembledSets.length === 0 && createdTestId && (
              <div className="card" style={{ padding: 48, textAlign: 'center', color: 'var(--text-tertiary)' }}>
                <Brain size={48} style={{ marginBottom: 12, opacity: 0.3 }} />
                <div>Pool configuration failed. Make sure you have enough accepted questions in your pool.</div>
                <button className="btn btn-secondary" style={{ marginTop: 16 }} onClick={() => { setAssembledSets([]); setCtStep(4) }}>
                  Back to Generate Questions
                </button>
              </div>
            )}
          </div>
        )}
      </div>

      {/* ═══ Question Review Popup Modal ═══ */}
      {skillPopup !== null && (() => {
        const skill = ctForm.skills[skillPopup.skillIndex]
        if (!skill) return null
        const qt = skill.question_types[skillPopup.typeIndex]
        if (!qt) return null
        const poolKey = getPoolKey(skill.name, qt.type)
        const pool = skillQuestionPool[poolKey] || []
        const accepted = pool.filter(q => q.status === 'approved').length
        const pending = pool.filter(q => q.status === 'generated').length
        const rejected = pool.filter(q => q.status === 'rejected').length
        const needed = qt.count_per_set * (ctForm.num_sets || 2)

        return (
          <div className="modal-overlay" onClick={() => setSkillPopup(null)}>
            <div style={{
              position: 'fixed', top: '3%', left: '5%', right: '5%', bottom: '3%',
              background: 'var(--bg-primary)', borderRadius: 20, padding: 0,
              display: 'flex', flexDirection: 'column', zIndex: 1001,
              boxShadow: '0 25px 50px rgba(0,0,0,0.25)'
            }} onClick={e => e.stopPropagation()}>
              {/* Header */}
              <div style={{
                padding: '20px 28px', borderBottom: '1px solid var(--border-subtle)',
                display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexShrink: 0
              }}>
                <div>
                  <h3 style={{ margin: 0, fontSize: '1.1rem' }}>Review Questions — {skill.name} ({typeLabels[qt.type] || qt.type})</h3>
                  <div className="flex gap-8" style={{ marginTop: 6 }}>
                    <span className="badge badge-green">{accepted} accepted</span>
                    <span className="badge badge-neutral">{pending} pending</span>
                    {rejected > 0 && <span className="badge badge-red">{rejected} rejected</span>}
                    <span style={{ fontSize: '0.75rem', color: 'var(--text-tertiary)' }}>
                      {pool.length} total · Need {needed} accepted ({qt.count_per_set}/set x {ctForm.num_sets} sets)
                    </span>
                  </div>
                </div>
                <div className="flex gap-8">
                  <button className="btn btn-secondary btn-sm" onClick={() => acceptAllPoolQuestions(poolKey)}>
                    <CheckCircle2 size={14} /> Accept All Pending
                  </button>
                  <button className="btn btn-ghost btn-sm" onClick={() => setSkillPopup(null)}>
                    <XCircle size={16} /> Close
                  </button>
                </div>
              </div>

              {/* Body — scrollable question list */}
              <div style={{ flex: 1, overflow: 'auto', padding: '16px 28px' }}>
                {pool.length === 0 && (
                  <div style={{ padding: 40, textAlign: 'center', color: 'var(--text-tertiary)' }}>
                    No questions generated yet. Click "Generate" on the skill row.
                  </div>
                )}
                {pool.map((q, qi) => {
                  const hasEmbeddedOpts = q.question_text && /\s[A-D]\)\s/.test(q.question_text)
                  let qText = q.question_text || ''
                  let opts = q.options || []
                  if (hasEmbeddedOpts && opts.every(o => /^[A-D]$/.test(o))) {
                    const m = qText.match(/^(.*?\?)\s*(A\).+)$/s)
                    if (m) {
                      qText = m[1].trim()
                      opts = m[2].split(/\s(?=[A-D]\))/).map(ss => ss.replace(/^[A-D]\)\s*/, '').trim())
                    }
                  }
                  const correctIdx = 'ABCD'.indexOf(q.correct_answer)
                  const correctFull = correctIdx >= 0 && opts[correctIdx] ? opts[correctIdx] : q.correct_answer

                  return (
                    <div key={q.id} className="card" style={{
                      marginBottom: 10, position: 'relative', overflow: 'hidden',
                      borderLeft: `4px solid ${q.status === 'approved' ? '#22c55e' : q.status === 'rejected' ? '#ef4444' : 'var(--accent-primary)'}`,
                      opacity: q.status === 'rejected' ? 0.5 : 1,
                      transition: 'all 0.2s'
                    }}>
                      {/* Question header */}
                      <div className="flex-between" style={{ marginBottom: 10 }}>
                        <div className="flex-center gap-8">
                          <span style={{
                            fontFamily: 'var(--font-mono)', fontSize: '0.72rem', fontWeight: 700,
                            color: q.status === 'approved' ? '#fff' : q.status === 'rejected' ? '#fff' : 'var(--accent-primary)',
                            background: q.status === 'approved' ? '#22c55e' : q.status === 'rejected' ? '#ef4444' : 'var(--accent-glow)',
                            width: 28, height: 28, display: 'grid', placeItems: 'center', borderRadius: 7
                          }}>Q{qi + 1}</span>
                          <span className={`badge ${q.question_type === 'multiple_choice' ? 'badge-blue' : q.question_type === 'coding' ? 'badge-purple' : 'badge-amber'}`}>
                            {(q.question_type || '').replace('_', ' ')}
                          </span>
                          <span className={`badge ${q.difficulty_level === 'Easy' ? 'badge-green' : q.difficulty_level === 'Hard' ? 'badge-red' : 'badge-amber'}`}>{q.difficulty_level}</span>
                          <span className="mono text-sm text-muted">{q.points || 10} pts</span>
                        </div>
                        {q.status === 'approved' && <span className="badge badge-green">Accepted</span>}
                        {q.status === 'rejected' && <span className="badge badge-red">Rejected</span>}
                      </div>

                      {/* Question text */}
                      <div style={{ fontSize: '0.92rem', lineHeight: 1.65, fontWeight: 500, marginBottom: 10, color: 'var(--text-primary)' }}>
                        {qText}
                      </div>

                      {/* MCQ Options */}
                      {q.question_type === 'multiple_choice' && opts.length > 0 && (
                        <div style={{ display: 'flex', flexDirection: 'column', gap: 6, marginBottom: 10 }}>
                          {opts.map((opt, j) => {
                            const letter = String.fromCharCode(65 + j)
                            const isCorrect = letter === q.correct_answer || opt === correctFull
                            return (
                              <div key={j} style={{
                                display: 'flex', alignItems: 'center', gap: 10,
                                padding: '8px 14px', borderRadius: 8,
                                background: isCorrect ? 'rgba(34,197,94,0.08)' : 'var(--bg-hover)',
                                border: isCorrect ? '1.5px solid rgba(34,197,94,0.3)' : '1.5px solid transparent',
                              }}>
                                <span style={{
                                  width: 24, height: 24, borderRadius: 6, flexShrink: 0,
                                  display: 'grid', placeItems: 'center', fontSize: '0.7rem', fontWeight: 700,
                                  background: isCorrect ? '#22c55e' : 'var(--bg-primary)',
                                  color: isCorrect ? '#fff' : 'var(--text-tertiary)',
                                  border: isCorrect ? 'none' : '1px solid var(--border-subtle)'
                                }}>{letter}</span>
                                <span style={{ flex: 1, fontSize: '0.85rem', fontWeight: isCorrect ? 600 : 400, color: isCorrect ? '#16a34a' : 'var(--text-primary)' }}>{opt}</span>
                                {isCorrect && <CheckCircle2 size={14} style={{ color: '#22c55e' }} />}
                              </div>
                            )
                          })}
                        </div>
                      )}

                      {/* Code template */}
                      {q.code_template && (
                        <pre style={{ padding: 10, background: 'var(--bg-hover)', borderRadius: 8, fontSize: '0.78rem', fontFamily: 'var(--font-mono)', overflow: 'auto', marginBottom: 8 }}>{q.code_template}</pre>
                      )}

                      {/* Sample/correct answer for non-MCQ */}
                      {q.sample_answer && q.question_type !== 'multiple_choice' && (
                        <div style={{ padding: '8px 12px', background: 'rgba(34,197,94,0.05)', borderRadius: 8, marginBottom: 8, border: '1px solid rgba(34,197,94,0.15)' }}>
                          <strong style={{ color: '#22c55e', fontSize: '0.7rem', textTransform: 'uppercase' }}>Sample Answer:</strong>
                          <div style={{ marginTop: 3, fontSize: '0.83rem' }}>{q.sample_answer}</div>
                        </div>
                      )}

                      {/* Action buttons */}
                      <div style={{ display: 'flex', gap: 8, marginTop: 10, paddingTop: 10, borderTop: '1px solid var(--border-subtle)' }}>
                        {q.status === 'approved' ? (
                          <div style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '6px 14px', borderRadius: 7, background: 'rgba(34,197,94,0.08)', color: '#22c55e', fontSize: '0.8rem', fontWeight: 700 }}>
                            <CheckCircle2 size={14} /> Accepted
                          </div>
                        ) : q.status === 'rejected' ? (
                          <div style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '6px 14px', borderRadius: 7, background: 'rgba(239,68,68,0.08)', color: '#ef4444', fontSize: '0.8rem', fontWeight: 700 }}>
                            <XCircle size={14} /> Rejected
                          </div>
                        ) : (
                          <>
                            <button onClick={() => acceptPoolQuestion(poolKey, q.id)} style={{
                              display: 'flex', alignItems: 'center', gap: 6, padding: '6px 16px', borderRadius: 7,
                              border: 'none', background: 'linear-gradient(135deg, #22c55e, #16a34a)', color: '#fff',
                              fontSize: '0.8rem', fontWeight: 700, cursor: 'pointer'
                            }}><CheckCircle2 size={14} /> Accept</button>
                            <button onClick={() => rejectPoolQuestion(poolKey, q.id)} style={{
                              display: 'flex', alignItems: 'center', gap: 6, padding: '6px 16px', borderRadius: 7,
                              border: '1.5px solid #fecaca', background: '#fff', color: '#ef4444',
                              fontSize: '0.8rem', fontWeight: 700, cursor: 'pointer'
                            }}><XCircle size={14} /> Reject</button>
                          </>
                        )}
                        <button onClick={() => regeneratePoolQuestion(poolKey, q.id)} disabled={replacingQ === q.id} style={{
                          display: 'flex', alignItems: 'center', gap: 6, padding: '6px 16px', borderRadius: 7,
                          border: '1.5px solid var(--border-medium)', background: replacingQ === q.id ? 'var(--bg-hover)' : '#fff',
                          color: 'var(--accent-primary)', fontSize: '0.8rem', fontWeight: 700,
                          cursor: replacingQ === q.id ? 'wait' : 'pointer', opacity: replacingQ === q.id ? 0.7 : 1
                        }}>
                          {replacingQ === q.id ? <><Loader2 size={14} className="spin" /> Replacing...</> : <><RefreshCw size={14} /> Regenerate</>}
                        </button>
                        {(q.status === 'approved' || q.status === 'rejected') && (
                          <button className="btn btn-ghost btn-sm" style={{ marginLeft: 'auto', fontSize: '0.75rem' }}
                            onClick={() => {
                              setSkillQuestionPool(prev => ({
                                ...prev,
                                [poolKey]: (prev[poolKey] || []).map(pq => pq.id === q.id ? { ...pq, status: 'generated', is_approved: false } : pq)
                              }))
                            }}>Undo</button>
                        )}
                      </div>
                    </div>
                  )
                })}
              </div>

              {/* Footer */}
              <div style={{
                padding: '16px 28px', borderTop: '1px solid var(--border-subtle)',
                display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexShrink: 0
              }}>
                <span style={{ fontSize: '0.85rem', color: 'var(--text-secondary)' }}>
                  {accepted >= needed
                    ? <span style={{ color: '#22c55e', fontWeight: 600 }}>Enough questions accepted ({accepted}/{needed})</span>
                    : <span style={{ color: '#f59e0b', fontWeight: 600 }}>Need {needed - accepted} more accepted questions</span>
                  }
                </span>
                <button className="btn btn-primary" onClick={() => setSkillPopup(null)}>Done</button>
              </div>
            </div>
          </div>
        )
      })()}

      {/* ═══ Previous Questions Popup Modal ═══ */}
      {prevQPopup !== null && (() => {
        const skill = ctForm.skills[prevQPopup.skillIndex]
        const qt = skill?.question_types[prevQPopup.typeIndex]
        if (!skill || !qt) return null
        const diffColors = { easy: '#22c55e', Easy: '#22c55e', medium: '#f59e0b', Medium: '#f59e0b', hard: '#ef4444', Hard: '#ef4444', expert: '#8b5cf6', Expert: '#8b5cf6' }
        const statusColors = { approved: '#22c55e', generated: '#3b82f6', rejected: '#ef4444', modified: '#f59e0b' }

        const filtered = prevQuestions.filter(q => {
          if (prevQFilters.difficulty !== 'all' && q.difficulty_level?.toLowerCase() !== prevQFilters.difficulty.toLowerCase()) return false
          if (prevQFilters.status !== 'all' && q.status !== prevQFilters.status) return false
          return true
        })

        const toggleSelect = (id) => {
          setPrevQSelected(prev => {
            const next = new Set(prev)
            next.has(id) ? next.delete(id) : next.add(id)
            return next
          })
        }
        const toggleAll = () => {
          if (prevQSelected.size === filtered.length) {
            setPrevQSelected(new Set())
          } else {
            setPrevQSelected(new Set(filtered.map(q => q.id)))
          }
        }

        return (
          <div className="modal-overlay" onClick={() => setPrevQPopup(null)}>
            <div style={{
              position: 'fixed', top: '3%', left: '5%', right: '5%', bottom: '3%',
              background: 'var(--bg-primary)', borderRadius: 20, padding: 0,
              display: 'flex', flexDirection: 'column', zIndex: 1002,
              boxShadow: '0 25px 50px rgba(0,0,0,0.25)'
            }} onClick={e => e.stopPropagation()}>

              {/* Header */}
              <div style={{
                padding: '20px 28px', borderBottom: '1px solid var(--border-subtle)',
                display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexShrink: 0
              }}>
                <div>
                  <h3 style={{ margin: 0 }}>
                    <BookOpen size={18} style={{ marginRight: 8, verticalAlign: 'middle' }} />
                    Previous Questions — {skill.name} ({typeLabels[qt.type] || qt.type})
                  </h3>
                  <div style={{ fontSize: '0.8rem', color: 'var(--text-secondary)', marginTop: 4 }}>
                    {prevQLoading ? 'Loading...' : `${prevQuestions.length} total · ${filtered.length} shown · ${prevQSelected.size} selected`}
                  </div>
                </div>
                <button className="btn btn-ghost btn-sm" onClick={() => setPrevQPopup(null)}>✕ Close</button>
              </div>

              {/* Filters */}
              <div style={{
                padding: '12px 28px', borderBottom: '1px solid var(--border-subtle)',
                display: 'flex', gap: 12, alignItems: 'center', flexShrink: 0, flexWrap: 'wrap'
              }}>
                <Filter size={14} style={{ color: 'var(--text-secondary)' }} />
                <select className="input" style={{ width: 'auto', padding: '4px 10px', fontSize: '0.82rem' }}
                  value={prevQFilters.difficulty}
                  onChange={e => { setPrevQFilters(f => ({ ...f, difficulty: e.target.value })); setPrevQSelected(new Set()) }}>
                  <option value="all">All Difficulties</option>
                  <option value="easy">Easy</option>
                  <option value="medium">Medium</option>
                  <option value="hard">Hard</option>
                  <option value="expert">Expert</option>
                </select>
                <select className="input" style={{ width: 'auto', padding: '4px 10px', fontSize: '0.82rem' }}
                  value={prevQFilters.status}
                  onChange={e => { setPrevQFilters(f => ({ ...f, status: e.target.value })); setPrevQSelected(new Set()) }}>
                  <option value="all">All Statuses</option>
                  <option value="approved">Approved</option>
                  <option value="generated">Generated</option>
                  <option value="rejected">Rejected</option>
                </select>
                {filtered.length > 0 && (
                  <button className="btn btn-ghost btn-sm" style={{ fontSize: '0.78rem' }} onClick={toggleAll}>
                    {prevQSelected.size === filtered.length
                      ? <><CheckSquare size={13} /> Deselect All</>
                      : <><Square size={13} /> Select All ({filtered.length})</>}
                  </button>
                )}
              </div>

              {/* Question List */}
              <div style={{ flex: 1, overflowY: 'auto', padding: '12px 28px' }}>
                {prevQLoading ? (
                  <div style={{ textAlign: 'center', padding: 48, color: 'var(--text-secondary)' }}>
                    <Loader2 size={24} className="spin" style={{ marginBottom: 8 }} />
                    <div>Loading previous questions…</div>
                  </div>
                ) : filtered.length === 0 ? (
                  <div style={{ textAlign: 'center', padding: 48, color: 'var(--text-secondary)' }}>
                    <BookOpen size={32} style={{ marginBottom: 12, opacity: 0.4 }} />
                    <div>No previous questions found for <strong>{skill.name}</strong> with these filters.</div>
                  </div>
                ) : (
                  filtered.map((q, idx) => {
                    const isSelected = prevQSelected.has(q.id)
                    const opts = Array.isArray(q.options) ? q.options : (typeof q.options === 'string' ? (() => { try { return JSON.parse(q.options) } catch { return [] } })() : [])
                    return (
                      <label key={q.id} style={{
                        display: 'flex', gap: 12, padding: '14px 16px', marginBottom: 10,
                        border: `1px solid ${isSelected ? 'var(--accent-primary)' : 'var(--border-subtle)'}`,
                        borderRadius: 10, cursor: 'pointer', transition: 'all 0.15s',
                        background: isSelected ? 'var(--accent-primary-10, rgba(99,102,241,0.08))' : 'var(--bg-secondary)'
                      }}>
                        {/* A real checkbox, clipped rather than display:none so it stays
                            focusable and in the accessibility tree. The row is a <label>, so
                            clicking anywhere on it still toggles, exactly as before. */}
                        <input type="checkbox" className="q-select__input"
                               checked={isSelected} onChange={() => toggleSelect(q.id)} />
                        <div style={{ flexShrink: 0, paddingTop: 2 }} aria-hidden="true">
                          {isSelected
                            ? <CheckSquare size={18} style={{ color: 'var(--accent-primary)' }} />
                            : <Square size={18} style={{ color: 'var(--text-tertiary)' }} />}
                        </div>
                        <div style={{ flex: 1, minWidth: 0 }}>
                          <div style={{ fontWeight: 500, marginBottom: 6, lineHeight: 1.4 }}>
                            <span style={{ color: 'var(--text-tertiary)', fontSize: '0.75rem', marginRight: 6 }}>#{idx + 1}</span>
                            {q.question_text}
                          </div>
                          {opts.length > 0 && (
                            <div style={{ marginBottom: 6 }}>
                              {opts.map((opt, oi) => (
                                <div key={oi} style={{
                                  fontSize: '0.78rem', padding: '2px 0',
                                  fontWeight: q.correct_answer === String.fromCharCode(65 + oi) ? 600 : 400,
                                  color: q.correct_answer === String.fromCharCode(65 + oi) ? '#22c55e' : 'var(--text-secondary)'
                                }}>
                                  {String.fromCharCode(65 + oi)}. {opt}
                                </div>
                              ))}
                            </div>
                          )}
                          {q.sample_answer && (
                            <div style={{ fontSize: '0.78rem', color: 'var(--text-secondary)', marginBottom: 4 }}>
                              <strong>Answer:</strong> {q.sample_answer.slice(0, 120)}{q.sample_answer.length > 120 ? '…' : ''}
                            </div>
                          )}
                          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginTop: 6 }}>
                            <span style={{
                              fontSize: '0.7rem', padding: '2px 8px', borderRadius: 12, fontWeight: 600,
                              background: `${diffColors[q.difficulty_level] || '#94a3b8'}22`,
                              color: diffColors[q.difficulty_level] || '#94a3b8'
                            }}>{q.difficulty_level}</span>
                            <span style={{
                              fontSize: '0.7rem', padding: '2px 8px', borderRadius: 12,
                              background: 'var(--bg-hover)', color: 'var(--text-secondary)'
                            }}>{typeLabels[q.question_type] || q.question_type}</span>
                            <span style={{
                              fontSize: '0.7rem', padding: '2px 8px', borderRadius: 12, fontWeight: 600,
                              background: `${statusColors[q.status] || '#94a3b8'}22`,
                              color: statusColors[q.status] || '#94a3b8'
                            }}>{q.status}</span>
                            {q.skill_name && q.skill_name !== skill.name && (
                              <span style={{ fontSize: '0.7rem', padding: '2px 8px', borderRadius: 12, background: 'var(--bg-hover)', color: 'var(--text-tertiary)' }}>
                                {q.skill_name}
                              </span>
                            )}
                          </div>
                        </div>
                      </label>
                    )
                  })
                )}
              </div>

              {/* Footer */}
              <div style={{
                padding: '16px 28px', borderTop: '1px solid var(--border-subtle)',
                display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexShrink: 0
              }}>
                <span style={{ fontSize: '0.85rem', color: 'var(--text-secondary)' }}>
                  {prevQSelected.size > 0
                    ? <><CheckSquare size={14} style={{ verticalAlign: 'middle', marginRight: 4 }} />{prevQSelected.size} question{prevQSelected.size !== 1 ? 's' : ''} selected</>
                    : 'Click questions to select them'}
                </span>
                <div style={{ display: 'flex', gap: 10 }}>
                  <button className="btn btn-secondary" onClick={() => setPrevQPopup(null)}>Cancel</button>
                  <button className="btn btn-primary"
                    disabled={prevQSelected.size === 0 || prevQImporting}
                    onClick={importSelectedQuestions}>
                    {prevQImporting
                      ? <><Loader2 size={13} className="spin" /> Importing…</>
                      : <><Plus size={13} /> Add Selected to Pool ({prevQSelected.size})</>}
                  </button>
                </div>
              </div>
            </div>
          </div>
        )
      })()}

      {/* ─────────── Add MCQ manually ─────────── */}
      {manualQPopup !== null && (() => {
        const skill = ctForm.skills[manualQPopup.skillIndex]
        const qt = skill?.question_types[manualQPopup.typeIndex]
        if (!skill || !qt) return null
        return (
          <div className="modal-overlay" onClick={() => setManualQPopup(null)} style={{ zIndex: 1200 }}>
            <div className="modal" onClick={e => e.stopPropagation()} style={{ maxWidth: 640, maxHeight: '90vh', overflow: 'auto' }}>
              <h2 style={{ marginTop: 0, marginBottom: 6 }}>Add MCQ manually</h2>
              <div style={{ fontSize: '0.8rem', color: 'var(--text-tertiary)', marginBottom: 16 }}>
                Skill: <strong>{skill.name}</strong> · Multiple Choice
                {manualQAddedCount > 0 && <span style={{ color: '#16a34a', marginLeft: 8, fontWeight: 600 }}>· {manualQAddedCount} added</span>}
              </div>
              <div className="input-group" style={{ marginBottom: 14 }}>
                <label>Question</label>
                <textarea className="textarea" rows={3} value={manualQForm.question_text}
                  onChange={e => setManualQForm(f => ({ ...f, question_text: e.target.value }))}
                  placeholder="Enter the question" />
              </div>
              <div style={{ fontSize: '0.72rem', fontWeight: 700, textTransform: 'uppercase', color: 'var(--text-tertiary)', marginBottom: 8 }}>
                Options — select the radio for the correct answer
              </div>
              {['A', 'B', 'C', 'D'].map((L, i) => (
                <div key={L} style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 8 }}>
                  <input type="radio" name="manualCorrect" checked={manualQForm.correct === i}
                    onChange={() => setManualQForm(f => ({ ...f, correct: i }))} title="Mark as correct" />
                  <span style={{ fontWeight: 700, width: 16, color: manualQForm.correct === i ? '#16a34a' : 'var(--text-tertiary)' }}>{L}</span>
                  <input className="input" style={{ flex: 1 }} value={manualQForm.options[i]}
                    onChange={e => setManualQForm(f => { const o = [...f.options]; o[i] = e.target.value; return { ...f, options: o } })}
                    placeholder={`Option ${L}`} />
                </div>
              ))}
              <div style={{ display: 'flex', gap: 10, justifyContent: 'space-between', alignItems: 'center', marginTop: 18, borderTop: '1px solid var(--border-subtle)', paddingTop: 14 }}>
                <button onClick={downloadCsvTemplate} style={{ background: 'none', border: 'none', color: 'var(--accent-primary, #6366f1)', fontSize: '0.76rem', cursor: 'pointer', display: 'inline-flex', alignItems: 'center', gap: 4, padding: 0 }}>
                  <FileText size={12} /> Download CSV template
                </button>
                <div style={{ display: 'flex', gap: 10 }}>
                  <button className="btn btn-secondary" onClick={() => setManualQPopup(null)}>Done</button>
                  <button className="btn btn-primary" onClick={addManualQuestion} disabled={manualQSaving}>
                    {manualQSaving ? <><Loader2 size={13} className="spin" /> Adding...</> : <><Plus size={13} /> Add to Pool</>}
                  </button>
                </div>
              </div>
            </div>
          </div>
        )
      })()}

      {/* Hidden CSV file input (shared across all MCQ pool cards) */}
      <input ref={csvInputRef} type="file" accept=".csv,text/csv" style={{ display: 'none' }} onChange={handleCsvSelected} />
    </>
  )
}
