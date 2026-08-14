import { useState, useEffect, useRef } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import axios from 'axios'
import {
  Calendar, Clock, MessageSquare, Brain, Star, Send, RefreshCw,
  ChevronDown, ChevronUp, Plus, CheckCircle2, XCircle, Eye, Loader2,
  Edit3, Trash2, BookOpen, Filter, Search, Square, CheckSquare,
  Upload, FileText
} from 'lucide-react'
import api from '../../api/client'
import './InterviewerDashboard.css'
// This dashboard carries its own inlined copy of the create-test wizard; the timing rules are
// shared with the HR one so both produce the same duration.
import { TYPE_LABELS, TYPE_TIME_DEFAULTS, computeDuration } from '../shared/CreateTestWizard'
import ProctoringReport from '../../components/ProctoringReport'
import Header from '../../components/Header'

// Separate axios instance for /interviews/* routes (not under /api/v1)
const qApi = axios.create({ baseURL: '', timeout: 300000 }) // 5 min timeout for AI generation
qApi.interceptors.request.use((config) => {
  const token = localStorage.getItem('hireflow_token')
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

// ─────────────── Coding test-case widgets ───────────────
// Read-only view of a coding question's test cases. Sample cases are shown in
// full; hidden cases are only counted so their contents never leak to candidates.
function TestCasesView({ testCases, compact = false }) {
  if (!Array.isArray(testCases) || testCases.length === 0) {
    return (
      <div style={{ fontSize: '0.78rem', color: '#ef4444', margin: '4px 0 8px' }}>
        ⚠ No test cases — this coding question can't be auto-graded.
      </div>
    )
  }
  const samples = testCases.filter(t => t.is_sample)
  const hiddenCount = testCases.length - samples.length
  const cellStyle = { margin: 0, padding: 8, background: 'var(--bg-hover)', borderRadius: 6, fontSize: '0.76rem', fontFamily: 'var(--font-mono)', overflow: 'auto', whiteSpace: 'pre-wrap' }
  const capStyle = { fontSize: '0.66rem', textTransform: 'uppercase', color: 'var(--text-tertiary)', marginBottom: 2 }
  return (
    <details style={{ marginBottom: 8 }}>
      <summary style={{ cursor: 'pointer', fontSize: compact ? '0.8rem' : '0.85rem', color: 'var(--accent-primary)', fontWeight: 500 }}>
        Test Cases ({testCases.length}{hiddenCount ? ` · ${hiddenCount} hidden` : ''})
      </summary>
      <div style={{ marginTop: 6, display: 'grid', gap: 8 }}>
        {samples.length === 0 && (
          <div style={{ fontSize: '0.78rem', color: 'var(--text-tertiary)' }}>All {testCases.length} case(s) are hidden.</div>
        )}
        {samples.map((t, i) => (
          <div key={i} style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
            <div><div style={capStyle}>Input</div><pre style={cellStyle}>{t.input || '(empty)'}</pre></div>
            <div><div style={capStyle}>Expected Output</div><pre style={cellStyle}>{t.expected_output || '(empty)'}</pre></div>
          </div>
        ))}
        {hiddenCount > 0 && (
          <div style={{ fontSize: '0.74rem', color: 'var(--text-tertiary)' }}>+ {hiddenCount} hidden case{hiddenCount > 1 ? 's' : ''} (not shown to candidates)</div>
        )}
      </div>
    </details>
  )
}

// Editor for a coding question's test cases. `onChange` receives the full new array.
function TestCasesEditor({ testCases, onChange }) {
  const cases = Array.isArray(testCases) ? testCases : []
  const update = (idx, field, value) => onChange(cases.map((c, i) => i === idx ? { ...c, [field]: value } : c))
  const addCase = () => onChange([...cases, { input: '', expected_output: '', is_sample: cases.length === 0, weight: 1 }])
  const removeCase = (idx) => onChange(cases.filter((_, i) => i !== idx))
  const capStyle = { fontSize: '0.66rem', textTransform: 'uppercase', color: 'var(--text-tertiary)', marginBottom: 2 }
  return (
    <div className="input-group">
      <label className="text-sm" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <span>Test Cases ({cases.length})</span>
        <button type="button" onClick={addCase} style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: '0.75rem', padding: '4px 10px', borderRadius: 6, border: '1px solid var(--border-medium)', background: '#fff', color: 'var(--accent-primary)', cursor: 'pointer' }}>
          <Plus size={12} /> Add case
        </button>
      </label>
      {cases.length === 0 && (
        <div style={{ fontSize: '0.78rem', color: '#ef4444', padding: '6px 0' }}>No test cases — the candidate's code can't be auto-graded. Add at least one.</div>
      )}
      <div style={{ display: 'grid', gap: 10 }}>
        {cases.map((c, idx) => (
          <div key={idx} style={{ border: '1px solid var(--border-subtle)', borderRadius: 8, padding: 10 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
              <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: '0.78rem', cursor: 'pointer' }}>
                <input type="checkbox" checked={!!c.is_sample} onChange={e => update(idx, 'is_sample', e.target.checked)} />
                Sample (shown to candidate)
              </label>
              <button type="button" onClick={() => removeCase(idx)} style={{ color: '#ef4444', background: 'none', border: 'none', cursor: 'pointer', fontSize: '0.75rem' }}>Remove</button>
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
              <div>
                <div style={capStyle}>Input (stdin)</div>
                <textarea className="textarea" rows={3} style={{ fontFamily: 'var(--font-mono)', fontSize: '0.78rem' }} value={c.input || ''} onChange={e => update(idx, 'input', e.target.value)} />
              </div>
              <div>
                <div style={capStyle}>Expected Output (stdout)</div>
                <textarea className="textarea" rows={3} style={{ fontFamily: 'var(--font-mono)', fontSize: '0.78rem' }} value={c.expected_output || ''} onChange={e => update(idx, 'expected_output', e.target.value)} />
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

const TAB_MAP = { dashboard: 'dashboard', interviews: 'interviews', 'create-test': 'create-test', 'request-candidates': 'request-candidates', 'edit-exam': 'edit-exam', 'feedback-history': 'feedback-history' }

// Recommendation is an ordered scale, so the encoding is diverging: two poles either
// side of a light neutral. Three tones rather than five — the poles carry the polarity
// and the word carries the intensity, which is why the label is never dropped. A
// five-step version put two adjacent reds at ΔE 9.0, indistinguishable even with full
// colour vision. Values live in InterviewerDashboard.css.
const HIRE_REC = {
  strong_hire:    { label: 'Strong hire',    tone: 'pos' },
  hire:           { label: 'Hire',           tone: 'pos' },
  maybe:          { label: 'Maybe',          tone: 'mid' },
  no_hire:        { label: 'No hire',        tone: 'neg' },
  strong_no_hire: { label: 'Strong no hire', tone: 'neg' },
}

// Fallback used only when GET /interviewer/templates is unreachable (offline / auth issue).
// The same set is seeded into the DB as is_builtin=True via scripts/seed_templates.py, so
// online users see these from the API and never hit this list.
const FALLBACK_TEMPLATES = [
  { id: 'fb-frontend-dev', name: 'Frontend Developer', category: 'Engineering', job_category: 'technical', role_level: 'Mid-level', employment_type: 'Full-time', min_experience_years: 2, required_skills: ['React','JavaScript','TypeScript','HTML','CSS'], preferred_skills: ['Next.js','Redux','Tailwind CSS','Git','REST APIs'], job_description: 'Design and implement responsive, high-performance web interfaces using React and TypeScript. Collaborate with designers to translate UI/UX wireframes into functional components. Optimize application performance and ensure cross-browser compatibility. Participate in code reviews and contribute to frontend architecture decisions.', is_builtin: true, source: 'builtin' },
  { id: 'fb-backend-dev', name: 'Backend Developer', category: 'Engineering', job_category: 'technical', role_level: 'Mid-level', employment_type: 'Full-time', min_experience_years: 3, required_skills: ['Python','FastAPI','PostgreSQL','REST APIs','Docker'], preferred_skills: ['Redis','Celery','AWS','Kubernetes','CI/CD'], job_description: 'Build and maintain scalable backend services and RESTful APIs using Python and FastAPI. Design efficient database schemas and write complex SQL queries. Implement authentication, authorization, and security best practices. Work closely with frontend teams to define API contracts and ensure system reliability.', is_builtin: true, source: 'builtin' },
  { id: 'fb-fullstack-dev', name: 'Full Stack Developer', category: 'Engineering', job_category: 'technical', role_level: 'Senior', employment_type: 'Full-time', min_experience_years: 4, required_skills: ['React','Node.js','Python','PostgreSQL','REST APIs'], preferred_skills: ['TypeScript','Docker','AWS','GraphQL','Redis'], job_description: 'Develop end-to-end web features spanning frontend UI and backend services. Architect robust full-stack solutions that are scalable and maintainable. Lead technical discussions, conduct code reviews, and mentor junior developers. Collaborate with product and design teams to deliver high-quality features on time.', is_builtin: true, source: 'builtin' },
  { id: 'fb-devops', name: 'DevOps Engineer', category: 'Engineering', job_category: 'technical', role_level: 'Senior', employment_type: 'Full-time', min_experience_years: 4, required_skills: ['Docker','Kubernetes','CI/CD','Linux','Terraform'], preferred_skills: ['AWS','GCP','Prometheus','Grafana','Ansible'], job_description: 'Design, implement, and maintain CI/CD pipelines and cloud infrastructure. Manage containerized deployments using Docker and Kubernetes. Monitor system performance and ensure 99.9% uptime SLAs. Drive infrastructure-as-code practices and improve deployment velocity across engineering teams.', is_builtin: true, source: 'builtin' },
  { id: 'fb-qa-engineer', name: 'QA Engineer', category: 'Engineering', job_category: 'technical', role_level: 'Mid-level', employment_type: 'Full-time', min_experience_years: 2, required_skills: ['Selenium','Python','Test Automation','REST API Testing','SQL'], preferred_skills: ['Cypress','Playwright','JIRA','Postman','Performance Testing'], job_description: 'Design and execute comprehensive test plans covering functional, regression, and performance testing. Build and maintain automated test suites using Selenium and Python. Identify, document, and track defects through to resolution. Partner with developers to embed quality practices throughout the SDLC.', is_builtin: true, source: 'builtin' },
  { id: 'fb-mobile-dev', name: 'Mobile Developer', category: 'Engineering', job_category: 'technical', role_level: 'Mid-level', employment_type: 'Full-time', min_experience_years: 3, required_skills: ['React Native','JavaScript','TypeScript','iOS','Android'], preferred_skills: ['Redux','Firebase','Expo','REST APIs','App Store deployment'], job_description: 'Develop cross-platform mobile applications using React Native for iOS and Android. Implement smooth, native-like UI interactions and optimize app performance. Integrate third-party services, push notifications, and device APIs. Collaborate with the backend team to design efficient mobile API contracts.', is_builtin: true, source: 'builtin' },
  { id: 'fb-data-scientist', name: 'Data Scientist', category: 'Data / AI', job_category: 'technical', role_level: 'Senior', employment_type: 'Full-time', min_experience_years: 4, required_skills: ['Python','Machine Learning','Pandas','Scikit-learn','SQL'], preferred_skills: ['TensorFlow','PyTorch','Spark','Tableau','MLflow'], job_description: 'Build and deploy machine learning models to solve complex business problems. Analyze large datasets to extract actionable insights and drive data-driven decisions. Collaborate with engineering teams to productionize ML pipelines. Communicate findings to stakeholders through clear visualizations and reports.', is_builtin: true, source: 'builtin' },
  { id: 'fb-data-analyst', name: 'Data Analyst', category: 'Data / AI', job_category: 'technical', role_level: 'Mid-level', employment_type: 'Full-time', min_experience_years: 2, required_skills: ['SQL','Python','Excel','Tableau','Data Visualization'], preferred_skills: ['Power BI','Google Analytics','Looker','R','Statistics'], job_description: 'Collect, clean, and analyze business data to identify trends and opportunities. Build dashboards and reports that empower stakeholders to make informed decisions. Translate business questions into analytical frameworks and present findings clearly. Collaborate with cross-functional teams to define KPIs and metrics.', is_builtin: true, source: 'builtin' },
  { id: 'fb-ml-engineer', name: 'ML Engineer', category: 'Data / AI', job_category: 'technical', role_level: 'Senior', employment_type: 'Full-time', min_experience_years: 4, required_skills: ['Python','TensorFlow','PyTorch','MLOps','Docker'], preferred_skills: ['Kubernetes','AWS SageMaker','Kafka','Feature Stores','ONNX'], job_description: 'Design and implement scalable ML infrastructure and model serving pipelines. Work with data scientists to take models from experimentation to production. Build monitoring systems to track model performance and data drift. Champion MLOps best practices including automated retraining and A/B testing frameworks.', is_builtin: true, source: 'builtin' },
  { id: 'fb-product-manager', name: 'Product Manager', category: 'Product / Design', job_category: 'mixed', role_level: 'Senior', employment_type: 'Full-time', min_experience_years: 5, required_skills: ['Product Strategy','Roadmapping','Agile','Stakeholder Management','Data Analysis'], preferred_skills: ['Figma','SQL','A/B Testing','OKRs','JIRA'], job_description: 'Define product vision, strategy, and roadmap in alignment with business goals. Gather and prioritize requirements from customers, engineering, and leadership. Own the product lifecycle from ideation through launch and iteration. Use data and user research to continuously improve the product experience.', is_builtin: true, source: 'builtin' },
  { id: 'fb-ux-designer', name: 'UX / UI Designer', category: 'Product / Design', job_category: 'mixed', role_level: 'Mid-level', employment_type: 'Full-time', min_experience_years: 3, required_skills: ['Figma','User Research','Wireframing','Prototyping','Design Systems'], preferred_skills: ['Adobe XD','Usability Testing','HTML/CSS','Motion Design','Accessibility'], job_description: 'Create intuitive, visually compelling user experiences from concept to high-fidelity prototypes. Conduct user research and usability testing to validate design decisions. Maintain and evolve the design system ensuring consistency across products. Collaborate closely with product managers and engineers to ship polished features.', is_builtin: true, source: 'builtin' },
  { id: 'fb-business-analyst', name: 'Business Analyst', category: 'Business', job_category: 'non_technical', role_level: 'Mid-level', employment_type: 'Full-time', min_experience_years: 3, required_skills: ['Requirements Analysis','SQL','Process Mapping','Stakeholder Management','Documentation'], preferred_skills: ['JIRA','Tableau','Agile','Confluence','Risk Analysis'], job_description: 'Elicit, document, and validate business requirements from stakeholders. Map current-state processes and design future-state improvements. Bridge the gap between business needs and technical solutions through clear specifications. Track project progress and manage scope to ensure on-time, on-budget delivery.', is_builtin: true, source: 'builtin' },
]

export default function InterviewerDashboard() {
  const location = useLocation()
  const navigate = useNavigate()

  const segment = location.pathname.split('/').pop()
  const tab = TAB_MAP[segment] || (segment === 'interviewer' ? 'dashboard' : 'dashboard')
  const setTab = (t) => navigate(t === 'dashboard' ? '/interviewer' : `/interviewer/${t}`)

  // ── state ──
  const [interviews, setInterviews] = useState([])
  const [assignments, setAssignments] = useState([])
  const [candidates, setCandidates] = useState([])
  const [loading, setLoading] = useState(false)

  // schedule modal
  const [showSchedule, setShowSchedule] = useState(false)
  const [selJob, setSelJob] = useState(null)
  const [schedForm, setSchedForm] = useState({
    candidate_id: '', title: '', interview_type: 'TECHNICAL',
    difficulty_level: 'MEDIUM', scheduled_datetime: '', duration_minutes: 60,
    meeting_link: '', notes: ''
  })

  // questions
  const [selInterview, setSelInterview] = useState(null)
  const [questions, setQuestions] = useState([])
  const [genLoading, setGenLoading] = useState(false)
  const [genCount, setGenCount] = useState(5)

  // feedback
  const [fbInterview, setFbInterview] = useState('')
  const [fbForm, setFbForm] = useState({
    technical_score: 0, communication_score: 0, problem_solving_score: 0,
    cultural_fit_score: 0, strengths: '', weaknesses: '', recommendations: '',
    hire_recommendation: 'proceed'
  })

  // create test
  const [createdTestId, setCreatedTestId] = useState(null)
  const [ctStep, setCtStep] = useState(1)
  const [ctForm, setCtForm] = useState({
    title: '', description: '',
    job_id: '', job_position: '', job_title: '', job_level: 'Mid-level', role_type: 'Full-time',
    interview_type: 'TECHNICAL', difficulty_level: 'MEDIUM',
    // Duration is derived from these times and the per-type counts via computeDuration().
    time_per_question_type: { ...TYPE_TIME_DEFAULTS },
    scheduled_datetime: '',
    skills: [{ name: '', difficulty: 'MEDIUM', marks_per_question: 1, question_types: [{ type: 'multiple_choice', count_per_set: 3, pool_size: null }] }],
    num_sets: 2,
    total_marks: 100, negative_marking: false, negative_mark_percent: 0, section_cutoff: 30, overall_cutoff: 50,
    conceptual_vs_practical_ratio: '60:40', real_world_scenarios: true,
    bloom_taxonomy_level: 'Apply', coding_focus_areas: '', avoid_topics: '',
    // Tab-switch detection defaults ON — the interviewer opts out deliberately.
    enable_webcam_monitoring: false, enable_tab_switch_detection: true, copy_paste_allowed: false,
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
  const [activeSetTab, setActiveSetTab] = useState(1)             // which set tab is active in Step 5
  // Previous questions popup state
  const [prevQPopup, setPrevQPopup] = useState(null)              // { skillIndex, typeIndex } | null
  const [prevQuestions, setPrevQuestions] = useState([])
  const [prevQLoading, setPrevQLoading] = useState(false)
  const [prevQFilters, setPrevQFilters] = useState({ difficulty: 'all', status: 'all' })
  const [prevQSelected, setPrevQSelected] = useState(new Set())
  const [prevQImporting, setPrevQImporting] = useState(false)
  // Manual MCQ entry + CSV upload state
  const [manualQPopup, setManualQPopup] = useState(null)          // { skillIndex, typeIndex } | null
  const [manualQForm, setManualQForm] = useState({ question_text: '', options: ['', '', '', ''], correct: 0 })
  const [manualQSaving, setManualQSaving] = useState(false)
  const [manualQAddedCount, setManualQAddedCount] = useState(0)
  const [csvTarget, setCsvTarget] = useState(null)               // { skillIndex, typeIndex } | null
  const [csvUploading, setCsvUploading] = useState(false)
  const csvInputRef = useRef(null)
  const [dashStats, setDashStats] = useState(null)
  const [allJobs, setAllJobs] = useState([])
  const [myMeetings, setMyMeetings] = useState([])
  const [meetingFeedback, setMeetingFeedback] = useState(null) // meeting id
  const [mfForm, setMfForm] = useState({
    technical_score: 0, communication_score: 0, problem_solving_score: 0,
    cultural_fit_score: 0, strengths: '', weaknesses: '', recommendations: '',
    hire_recommendation: 'proceed'
  })
  const [mfSubmitting, setMfSubmitting] = useState(false)

  const submitMeetingFeedback = async (e) => {
    e.preventDefault()
    if (!meetingFeedback) return
    setMfSubmitting(true)
    try {
      await api.post(`/interviewer/interviews/${meetingFeedback}/feedback`, {
        ...mfForm,
        strengths: mfForm.strengths.split(',').map(s => s.trim()).filter(Boolean),
        weaknesses: mfForm.weaknesses.split(',').map(s => s.trim()).filter(Boolean),
      })
      // Mark interview as completed
      await api.put(`/interviewer/interviews/${meetingFeedback}`, { status: 'COMPLETED' })
      alert('Feedback submitted and interview marked as completed!')
      setMeetingFeedback(null)
      setMfForm({ technical_score: 0, communication_score: 0, problem_solving_score: 0, cultural_fit_score: 0, strengths: '', weaknesses: '', recommendations: '', hire_recommendation: 'proceed' })
      fetchMyMeetings()
      fetchInterviews()
    } catch (err) {
      alert(err.response?.data?.detail || 'Failed to submit feedback')
    }
    setMfSubmitting(false)
  }

  const fetchMyMeetings = () => {
    api.get('/interviewer/my-meetings')
      .then(r => setMyMeetings(r.data || []))
      .catch(() => setMyMeetings([]))
  }

  const fetchAllJobs = () => {
    api.get('/interviewer/all-jobs')
      .then(r => setAllJobs(r.data || []))
      .catch(err => {
        console.error('Failed to fetch jobs:', err.response?.status, err.response?.data?.detail || err.message)
        setAllJobs([])
      })
  }

  // HR selection for publish
  const [hrList, setHrList] = useState([])
  const [selectedHrId, setSelectedHrId] = useState('')
  const [publishLoading, setPublishLoading] = useState(false)

  const fetchHrList = () => {
    api.get('/interviewer/hr-list')
      .then(r => setHrList(r.data || []))
      .catch(() => setHrList([]))
  }

  // candidate request
  const [crForm, setCrForm] = useState({
    job_title: '', department: '', role_level: 'Mid-level', employment_type: 'Full-time',
    num_candidates: 1, required_skills: '', preferred_skills: '',
    min_experience_years: 0, max_experience_years: '',
    education_requirement: '', job_description: '', job_category: 'technical',
    urgency: 'normal', notes: ''
  })
  const [crSubmitting, setCrSubmitting] = useState(false)
  const [crRequests, setCrRequests] = useState([])

  // Feedback history — interviews this interviewer has already scored.
  const [history, setHistory] = useState([])
  const [historyLoading, setHistoryLoading] = useState(false)
  const [historyError, setHistoryError] = useState('')
  const [historyOpen, setHistoryOpen] = useState(null)   // interview_id of the expanded row
  const [jdPanel, setJdPanel] = useState(null)
  const [templateSearch, setTemplateSearch] = useState('')
  const [templates, setTemplates] = useState([])
  const [aiForm, setAiForm] = useState({ job_title: '', experience_level: 'Mid-level', key_skills: '', focus_areas: '' })
  const [aiGenerating, setAiGenerating] = useState(false)
  const [aiError, setAiError] = useState('')
  const [aiResult, setAiResult] = useState(null)   // last AI response, used by "Save as template"
  const [jdFlash, setJdFlash] = useState('')
  const [saveTplOpen, setSaveTplOpen] = useState(false)
  const [saveTplName, setSaveTplName] = useState('')
  const [saveTplSource, setSaveTplSource] = useState('manual')   // manual | ai

  // ── Edit Exam/Questions tab ──────────────────────────────────────────────
  // These use /interviewer/tests|drafts|skills rather than /interviewer/interviews:
  // that path is route-shadowed server-side and 500s whenever a DRAFT exists.
  const [eeSection, setEeSection] = useState('exams')      // exams | drafts | questions
  const [eeTests, setEeTests] = useState([])
  const [eeDrafts, setEeDrafts] = useState([])
  const [eeLoading, setEeLoading] = useState(false)
  const [eeOpenTest, setEeOpenTest] = useState(null)       // id of the test whose editor is expanded
  const [eeDraftVals, setEeDraftVals] = useState({})       // unsaved edits for the open test
  const [eeSaving, setEeSaving] = useState(false)
  const [eeMsg, setEeMsg] = useState('')
  const [eeError, setEeError] = useState('')          // load failure, shown instead of "nothing here"
  const [eeSkills, setEeSkills] = useState([])
  const [eeSkillSearch, setEeSkillSearch] = useState('')
  const [eeSkillPopup, setEeSkillPopup] = useState(null)   // { skill_name }
  const [eeQuestions, setEeQuestions] = useState([])
  const [eeQLoading, setEeQLoading] = useState(false)
  const [eeEditingQ, setEeEditingQ] = useState(null)       // question id being edited
  const [eeQMsg, setEeQMsg] = useState('')

  // Never swallow the error: a failed request and an empty result must not look identical
  // (that is exactly what hides the broken /interviewer/interviews list elsewhere in here).
  const eeErrText = (err) =>
    err?.response?.data?.detail || err?.message || 'request failed'

  const fetchEeTests = () => {
    setEeLoading(true); setEeError('')
    api.get('/interviewer/tests')
      .then(r => setEeTests(r.data?.tests || []))
      .catch(err => { setEeTests([]); setEeError(`Couldn't load tests — ${eeErrText(err)}`) })
      .finally(() => setEeLoading(false))
  }
  const fetchEeDrafts = () => {
    setEeLoading(true); setEeError('')
    api.get('/interviewer/tests/drafts')
      .then(r => setEeDrafts(r.data?.drafts || []))
      .catch(err => { setEeDrafts([]); setEeError(`Couldn't load drafts — ${eeErrText(err)}`) })
      .finally(() => setEeLoading(false))
  }
  const fetchEeSkills = () => {
    setEeLoading(true); setEeError('')
    api.get('/interviewer/skills')
      .then(r => setEeSkills(r.data?.skills || []))
      .catch(err => { setEeSkills([]); setEeError(`Couldn't load skills — ${eeErrText(err)}`) })
      .finally(() => setEeLoading(false))
  }

  // Load whichever section is showing, whenever the tab or section changes.
  useEffect(() => {
    if (tab !== 'edit-exam') return
    setEeMsg('')
    if (eeSection === 'exams') fetchEeTests()
    else if (eeSection === 'drafts') fetchEeDrafts()
    else if (eeSection === 'questions') fetchEeSkills()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab, eeSection])

  // Question types actually used by a test, so the editor only shows times that matter.
  const eeTypesInTest = (t) => {
    const types = new Set()
    for (const s of (t?.skills_configuration?.skills || [])) {
      for (const qt of (s.question_types || [])) if (qt.type) types.add(qt.type)
    }
    return [...types]
  }

  // Duration is derived, never typed: reuse the wizard's formula so the number shown here
  // is the same one the wizard would compute and store.
  const eeComputedDuration = (t, timeMap) => computeDuration({
    skills: t?.skills_configuration?.skills || [],
    time_per_question_type: timeMap,
  })

  const openEeTestEditor = (t) => {
    if (eeOpenTest === t.id) { setEeOpenTest(null); return }
    const sec = t.security_settings || {}
    setEeOpenTest(t.id)
    setEeMsg('')
    setEeDraftVals({
      time_per_question_type: { ...TYPE_TIME_DEFAULTS, ...(t.interview_configuration?.time_per_question_type || {}) },
      enable_webcam_monitoring: !!sec.enable_webcam_monitoring,
      enable_tab_switch_detection: sec.enable_tab_switch_detection !== false,
      copy_paste_allowed: !!sec.copy_paste_allowed,
    })
  }

  const saveEeTestSettings = async (testId) => {
    setEeSaving(true); setEeMsg('')
    try {
      const r = await api.patch(`/interviewer/tests/${testId}/settings`, {
        ...eeDraftVals,
        apply_to_pending_invites: true,
      })
      const updated = r.data?.test
      setEeTests(ts => ts.map(t => (t.id === testId ? { ...t, ...updated } : t)))
      const n = r.data?.updated_pending_invites || 0
      setEeMsg(`Saved.${n ? ` Also updated ${n} pending invite${n === 1 ? '' : 's'}.` : ''}`)
      setEeOpenTest(null)
    } catch (err) {
      setEeMsg('Failed to save: ' + (err.response?.data?.detail || err.message))
    }
    setEeSaving(false)
  }

  const openEeSkill = async (skill) => {
    setEeSkillPopup(skill); setEeQuestions([]); setEeEditingQ(null); setEeQMsg('')
    setEeQLoading(true)
    try {
      const r = await api.get(`/interviewer/skills/${encodeURIComponent(skill.skill_name)}/questions`)
      setEeQuestions(r.data?.questions || [])
    } catch (err) {
      setEeQMsg('Failed to load questions: ' + (err.response?.data?.detail || err.message))
    }
    setEeQLoading(false)
  }

  // Local edit of a question in the popup (persisted by saveEeQuestion).
  const updateEeQ = (qId, field, value) =>
    setEeQuestions(qs => qs.map(q => (q.id === qId ? { ...q, [field]: value } : q)))

  const updateEeQOption = (qId, idx, value) =>
    setEeQuestions(qs => qs.map(q => {
      if (q.id !== qId) return q
      const opts = [...(q.options || [])]
      opts[idx] = value
      return { ...q, options: opts }
    }))

  const saveEeQuestion = async (q) => {
    setEeQMsg('')
    try {
      const r = await api.put(`/interviewer/questions/${q.id}`, {
        question_text: q.question_text,
        question_type: q.question_type,
        difficulty_level: q.difficulty_level,
        category: q.category,
        options: q.options,
        correct_answer: q.correct_answer,
        sample_answer: q.sample_answer,
        code_template: q.code_template,
        test_cases: q.test_cases,
        points: q.points,
        time_limit_minutes: q.time_limit_minutes,
      })
      setEeQuestions(qs => qs.map(x => (x.id === q.id ? { ...x, ...r.data } : x)))
      setEeEditingQ(null)
      setEeQMsg('Question saved.')
    } catch (err) {
      setEeQMsg('Failed to save: ' + (err.response?.data?.detail || err.message))
    }
  }

  const deleteEeQuestion = async (q) => {
    if (!window.confirm('Permanently delete this question from the bank?')) return
    setEeQMsg('')
    try {
      await api.delete(`/interviewer/questions/${q.id}`)
      setEeQuestions(qs => qs.filter(x => x.id !== q.id))
      setEeSkills(ss => ss.map(s => (s.skill_name === eeSkillPopup?.skill_name
        ? { ...s, question_count: Math.max(0, s.question_count - 1) } : s)))
      setEeQMsg('Question deleted.')
    } catch (err) {
      // 409 = still referenced by a test / candidate history; show the reason verbatim.
      setEeQMsg(err.response?.data?.detail || ('Failed to delete: ' + err.message))
    }
  }

  const fetchCandidateRequests = () => {
    api.get('/interviewer/candidate-requests').then(r => setCrRequests(r.data || [])).catch(() => {})
  }

  const fetchHistory = () => {
    setHistoryLoading(true); setHistoryError('')
    api.get('/interviewer/interviews/history', { params: { limit: 100 } })
      .then(r => setHistory(r.data?.interviews || []))
      .catch(err => { setHistory([]); setHistoryError(`Couldn't load your history — ${eeErrText(err)}`) })
      .finally(() => setHistoryLoading(false))
  }

  useEffect(() => {
    if (tab === 'feedback-history') fetchHistory()
  }, [tab])

  const fetchTemplates = (jobCategory) => {
    const filterFallback = (cat) => cat ? FALLBACK_TEMPLATES.filter(t => t.job_category === cat) : FALLBACK_TEMPLATES
    const params = jobCategory ? { job_category: jobCategory } : {}
    api.get('/interviewer/templates', { params })
      .then(r => {
        const rows = r.data || []
        // If the API returned nothing, fall back to the bundled list so the user is never stuck.
        setTemplates(rows.length > 0 ? rows : filterFallback(jobCategory))
      })
      .catch(() => setTemplates(filterFallback(jobCategory)))
  }

  // Re-fetch templates whenever the chosen job category changes.
  useEffect(() => {
    if (crForm.job_category) fetchTemplates(crForm.job_category)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [crForm.job_category])

  const handleCrSubmit = async (e) => {
    e.preventDefault()
    if (!crForm.job_title.trim()) {
      setJdFlash('Job title is required')
      setTimeout(() => setJdFlash(''), 3000)
      return
    }
    setCrSubmitting(true)
    try {
      await api.post('/interviewer/candidate-requests', {
        ...crForm,
        required_skills: crForm.required_skills ? crForm.required_skills.split(',').map(s => s.trim()).filter(Boolean) : [],
        preferred_skills: crForm.preferred_skills ? crForm.preferred_skills.split(',').map(s => s.trim()).filter(Boolean) : [],
        max_experience_years: crForm.max_experience_years ? parseInt(crForm.max_experience_years) : null,
      })
      fetchCandidateRequests()
      setJdFlash('Request submitted — HR will review it shortly')
      setTimeout(() => setJdFlash(''), 4000)
      // Clear the form so the user can submit another
      setCrForm({
        job_title: '', department: '', role_level: 'Mid-level', employment_type: 'Full-time',
        num_candidates: 1, required_skills: '', preferred_skills: '',
        min_experience_years: 0, max_experience_years: '',
        education_requirement: '', job_description: '', job_category: crForm.job_category,
        urgency: 'normal', notes: ''
      })
      setAiResult(null)
    } catch (err) {
      setJdFlash(err.response?.data?.detail || 'Failed to submit request')
      setTimeout(() => setJdFlash(''), 4000)
    } finally {
      setCrSubmitting(false)
    }
  }

  const openSaveTemplate = (source = 'manual') => {
    setSaveTplSource(source)
    // Sensible default name from the job title
    setSaveTplName((crForm.job_title || '').trim())
    setSaveTplOpen(true)
  }

  const submitSaveTemplate = async () => {
    if (!saveTplName.trim()) return
    const payload = {
      name: saveTplName.trim(),
      category: crForm.department || null,
      job_category: crForm.job_category || 'technical',
      role_level: crForm.role_level,
      employment_type: crForm.employment_type,
      min_experience_years: crForm.min_experience_years || 0,
      required_skills: crForm.required_skills ? crForm.required_skills.split(',').map(s => s.trim()).filter(Boolean) : [],
      preferred_skills: crForm.preferred_skills ? crForm.preferred_skills.split(',').map(s => s.trim()).filter(Boolean) : [],
      job_description: crForm.job_description || '',
      source: saveTplSource,
    }
    try {
      const endpoint = saveTplSource === 'ai' ? '/interviewer/templates/from-ai' : '/interviewer/templates'
      await api.post(endpoint, payload)
      setSaveTplOpen(false)
      setSaveTplName('')
      fetchTemplates(crForm.job_category)
      setJdFlash(`Template "${payload.name}" saved`)
      setTimeout(() => setJdFlash(''), 3000)
    } catch (err) {
      setJdFlash(err.response?.data?.detail || 'Failed to save template')
      setTimeout(() => setJdFlash(''), 4000)
    }
  }

  const deleteTemplate = async (t) => {
    if (t.is_builtin) return
    if (!confirm(`Delete template "${t.name}"?`)) return
    try {
      await api.delete(`/interviewer/templates/${t.id}`)
      fetchTemplates(crForm.job_category)
    } catch (err) {
      setJdFlash(err.response?.data?.detail || 'Failed to delete template')
      setTimeout(() => setJdFlash(''), 3000)
    }
  }

  // ── data fetching ──
  useEffect(() => {
    fetchInterviews()
    fetchAssignments()
    fetchCandidateRequests()
    fetchTemplates(crForm.job_category)
    fetchHrList()
    fetchAllJobs()
    fetchMyMeetings()
    api.get('/interviewer/stats/questions').then(r => setDashStats(r.data)).catch(() => {})
    // Pre-fill interviewer details from user
    const user = JSON.parse(localStorage.getItem('hireflow_user') || '{}')
    if (user.first_name) {
      setCtForm(f => ({
        ...f,
        interviewer_name: `${user.first_name} ${user.last_name}`,
        interviewer_email: user.email || ''
      }))
    }
  }, [])

  const fetchInterviews = () => {
    api.get('/interviewer/interviews', { params: { size: 50 } })
      .then(r => setInterviews(r.data?.interviews || r.data || []))
      .catch(() => {})
  }

  const fetchAssignments = () => {
    api.get('/interviewer/my-assignments')
      .then(r => setAssignments(r.data || []))
      .catch(() => {})
  }

  const fetchCandidates = (jobId) => {
    api.get(`/interviewer/my-assignments/${jobId}/candidates`)
      .then(r => setCandidates(r.data || []))
      .catch(() => setCandidates([]))
  }

  // ── schedule interview ──
  const openSchedule = (job) => {
    setSelJob(job)
    setSchedForm(f => ({ ...f, title: `Interview - ${job.job_title}`, candidate_id: '' }))
    fetchCandidates(job.job_id)
    setShowSchedule(true)
  }

  const handleSchedule = async (e) => {
    e.preventDefault()
    setLoading(true)
    try {
      await api.post('/interviewer/schedule-interview', {
        ...schedForm,
        job_id: selJob.job_id
      })
      setShowSchedule(false)
      fetchInterviews()
      fetchAssignments()
    } catch (err) {
      alert(err.response?.data?.detail || 'Failed to schedule')
    }
    setLoading(false)
  }

  // ── questions (uses POST /interviews/{id}/generate-questions from generator_router) ──
  const [genQTypes, setGenQTypes] = useState(['multiple_choice', 'coding', 'short_answer'])
  const [genCategories, setGenCategories] = useState('')
  const [genMcqAngles, setGenMcqAngles] = useState([])
  const [genDiffDist, setGenDiffDist] = useState({ Easy: 2, Medium: 2, Hard: 1 })
  const [showTestPreview, setShowTestPreview] = useState(false)

  const viewQuestions = async (iv) => {
    setSelInterview(iv)
    setGenLoading(true)
    setQuestions([])
    setShowTestPreview(false)
    setEditingQ(null)
    try {
      // First try to load existing questions via GET
      const existingRes = await qApi.get(`/interviews/${iv.id}/questions`)
      const existingQs = existingRes.data?.questions || []
      if (existingQs.length > 0) {
        setQuestions(existingQs)
        setGenLoading(false)
        return
      }
    } catch (loadErr) {
      // GET failed — try the enhanced interviewer route as fallback
      try {
        const fallbackRes = await api.get(`/interviewer/interviews/${iv.id}/questions`)
        // Enhanced route returns plain array, not { questions: [...] }
        const fallbackQs = Array.isArray(fallbackRes.data) ? fallbackRes.data : (fallbackRes.data?.questions || [])
        if (fallbackQs.length > 0) {
          setQuestions(fallbackQs)
          setGenLoading(false)
          return
        }
      } catch {}
    }
    // No questions exist — try generating (only for DRAFT tests)
    if (iv.status === 'DRAFT' || iv.status === 'GENERATING') {
      try {
        const res = await qApi.post(`/interviews/${iv.id}/generate-questions`, {
          question_count: genCount,
          question_types: genQTypes,
          difficulty_distribution: genDiffDist,
          categories: genCategories ? genCategories.split(',').map(s => s.trim()) : null,
          mcq_angles: (genQTypes.includes('multiple_choice') && genMcqAngles.length) ? genMcqAngles : null
        })
        setQuestions(res.data?.questions || [])
      } catch (err) {
        alert(err.response?.data?.detail || 'Failed to generate questions. Make sure the Gemini API key is configured.')
      }
    } else {
      alert('No questions found for this test.')
    }
    setGenLoading(false)
  }

  // Keep generateQuestions as alias for backward compatibility (used by handleCreateTest flow)
  const generateQuestions = viewQuestions

  const [editingQ, setEditingQ] = useState(null) // id of question being edited

  const updateQ = (qId, field, value) => {
    setQuestions(qs => qs.map(q => q.id === qId ? { ...q, [field]: value } : q))
  }
  const updateOption = (qId, optIdx, value) => {
    setQuestions(qs => qs.map(q => {
      if (q.id !== qId) return q
      const opts = [...(q.options || [])]
      opts[optIdx] = value
      return { ...q, options: opts }
    }))
  }
  const addOption = (qId) => {
    setQuestions(qs => qs.map(q => q.id === qId ? { ...q, options: [...(q.options || []), ''] } : q))
  }
  const removeOption = (qId, optIdx) => {
    setQuestions(qs => qs.map(q => {
      if (q.id !== qId) return q
      const opts = (q.options || []).filter((_, i) => i !== optIdx)
      return { ...q, options: opts }
    }))
  }
  const approveQuestion = (qId) => {
    setQuestions(qs => qs.map(q => q.id === qId ? { ...q, status: 'approved', is_approved: true } : q))
    setEditingQ(null)
  }
  const rejectQuestion = (qId) => {
    setQuestions(qs => qs.map(q => q.id === qId ? { ...q, status: 'rejected', is_approved: false } : q))
    setEditingQ(null)
  }
  const approveAll = () => {
    setQuestions(qs => qs.map(q => ({ ...q, status: 'approved', is_approved: true })))
    setEditingQ(null)
  }
  const deleteQuestion = (qId) => {
    setQuestions(qs => qs.filter(q => q.id !== qId))
  }
  const [replacingQ, setReplacingQ] = useState(null)
  const replaceQuestion = async (qId) => {
    const interviewId = selInterview?.id || createdTestId
    if (!interviewId) return
    setReplacingQ(qId)
    try {
      const res = await qApi.post(`/interviews/${interviewId}/questions/${qId}/regenerate`)
      const newQ = res.data
      setQuestions(qs => qs.map(q => q.id === qId ? { ...newQ, status: 'generated' } : q))
    } catch (err) {
      alert(err.response?.data?.detail || 'Failed to replace question')
    }
    setReplacingQ(null)
  }

  const toggleQType = (t) => {
    setGenQTypes(prev => prev.includes(t) ? prev.filter(x => x !== t) : [...prev, t])
  }

  // MCQ angles — cognitive facets to test for a single topic (sub-selection under multiple_choice)
  const MCQ_ANGLES = [
    { slug: 'theory', label: 'Theory' },
    { slug: 'syntax', label: 'Syntax' },
    { slug: 'output_prediction', label: 'Output Prediction' },
    { slug: 'code_debugging', label: 'Code Debugging' },
    { slug: 'logic', label: 'Logic' },
    { slug: 'best_practice', label: 'Best Practice' },
    { slug: 'complexity', label: 'Complexity' },
    { slug: 'edge_cases', label: 'Edge Cases' },
  ]
  const toggleMcqAngle = (slug) => {
    setGenMcqAngles(prev => prev.includes(slug) ? prev.filter(x => x !== slug) : [...prev, slug])
  }

  const totalPoints = questions.reduce((s, q) => s + (q.points || 10), 0)
  const totalTime = questions.reduce((s, q) => s + (q.time_limit_minutes || 5), 0)

  const finalizeInterview = async () => {
    if (!selInterview) return
    const approved = questions.filter(q => q.status === 'approved')
    if (approved.length === 0) return alert('Approve at least one question first')
    if (!selectedHrId) return alert('Please select an HR to notify before publishing')
    setPublishLoading(true)
    try {
      // Persist edited content AND approval status for each question. (Previously
      // only is_approved/status were sent, so edits to text / code / test cases
      // made in the review screen were silently discarded on publish.)
      await Promise.all(questions.map(q =>
        qApi.put(`/interviews/${selInterview.id}/questions/${q.id}`, {
          question_text: q.question_text,
          category: q.category,
          options: q.options,
          correct_answer: q.correct_answer,
          sample_answer: q.sample_answer,
          code_template: q.code_template,
          test_cases: q.test_cases,
          points: q.points,
          time_limit_minutes: q.time_limit_minutes,
          is_approved: q.status === 'approved',
          status: q.status
        })
      ))
      const res = await api.post(`/interviewer/interviews/${selInterview.id}/publish`, { hr_id: selectedHrId })
      const hr = hrList.find(h => h.id === selectedHrId)
      alert(`Test published with ${approved.length} questions (${approved.reduce((s, q) => s + (q.points || 10), 0)} pts).\n${hr?.name || 'HR'} (${hr?.email || ''}) has been notified.`)
      fetchInterviews()
      setSelInterview(null)
      setQuestions([])
      setShowTestPreview(false)
      setEditingQ(null)
      setSelectedHrId('')
    } catch (err) {
      alert(err.response?.data?.detail || 'Failed to publish')
    }
    setPublishLoading(false)
  }

  // ── feedback ──
  const handleFeedback = async (e) => {
    e.preventDefault()
    if (!fbInterview) return alert('Select an interview first')
    setLoading(true)
    try {
      await api.post(`/interviewer/interviews/${fbInterview}/feedback`, {
        ...fbForm,
        strengths: fbForm.strengths.split(',').map(s => s.trim()),
        weaknesses: fbForm.weaknesses.split(',').map(s => s.trim()),
      })
      alert('Feedback submitted!')
      setFbForm({
        technical_score: 0, communication_score: 0, problem_solving_score: 0,
        cultural_fit_score: 0, strengths: '', weaknesses: '', recommendations: '',
        hire_recommendation: 'proceed'
      })
      setFbInterview('')
    } catch (err) {
      alert(err.response?.data?.detail || 'Failed to submit feedback')
    }
    setLoading(false)
  }

  // ── create test ──
  const loadDraftIntoWizard = async (iv) => {
    const ic = iv.interview_configuration || {}
    const sc = iv.skills_configuration || {}
    const qf = iv.question_format || {}
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
          ? { marks_per_question: 1, ...s }  // new shape, ensure default
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
        // Rebuild skillQuestionPool grouped by skill__type
        const pool = {}
        poolQs.forEach(q => {
          const skillName = q.skill_name || q.category || 'General'
          const qType = q.question_type || 'multiple_choice'
          const key = `${skillName}__${qType}`
          if (!pool[key]) pool[key] = []
          pool[key].push(q)
        })
        setSkillQuestionPool(pool)
        setCtStep(4)  // Go to Generate Questions step
      } else {
        setCtStep(1)
      }
    } catch {
      setCtStep(1)
    }
    setTab('create-test')
  }

  const addSkill = () => setCtForm(f => ({ ...f, skills: [...f.skills, { name: '', difficulty: 'MEDIUM', marks_per_question: 1, question_types: [{ type: 'multiple_choice', count_per_set: 3, pool_size: null }] }] }))
  const removeSkill = (i) => setCtForm(f => ({ ...f, skills: f.skills.filter((_, j) => j !== i) }))
  const updateSkill = (i, key, val) => setCtForm(f => ({ ...f, skills: f.skills.map((s, j) => j === i ? { ...s, [key]: val } : s) }))
  const addSkillType = (skillIdx) => setCtForm(f => {
    // For non-technical interviews, default to MCQ rather than coding.
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

  // Per-skill, per-type question generation
  // Get-or-create the wizard's single DRAFT interview. Uses a synchronous ref +
  // an in-flight promise so multiple skill generations (or rapid clicks) reuse ONE
  // draft instead of each racing to POST a new one. Backend also dedupes by job_id.
  const ensureDraft = async () => {
    const have = draftInterviewId || createdTestId || draftIdRef.current
    if (have) return have
    // Only attach job_id if it's a REAL persisted job. The wizard's job selector can
    // hold a candidate-request id (materialized into a job later at step 5), and a
    // non-existent job_id violates the interviews.job_id foreign key.
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
        draftInFlightRef.current = null  // allow a retry on failure
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
      // Reuse (or create once) the wizard's single DRAFT interview.
      const interviewId = await ensureDraft()

      const poolSize = qt.pool_size || getDefaultPoolSize(qt.count_per_set)
      const res = await qApi.post(`/interviews/${interviewId}/generate-skill-questions`, {
        skill_name: skill.name,
        question_type: qt.type,
        difficulty: skill.difficulty,
        pool_size: poolSize,
        marks_per_question: skill.marks_per_question || 1,
        // Sent explicitly: the config holding this map is only PUT when the test is saved,
        // which happens after generation.
        time_per_question: ctForm.time_per_question_type?.[qt.type],
      })
      const newQuestions = res.data?.questions || []
      setSkillQuestionPool(prev => ({
        ...prev,
        [poolKey]: [...(prev[poolKey] || []), ...newQuestions]
      }))
      // Auto-open popup for review
      setSkillPopup({ skillIndex, typeIndex })
    } catch (err) {
      alert('Failed to generate questions: ' + (err.response?.data?.detail || err.message))
    }
    setGeneratingSkill(null)
  }

  // Accept/reject a pool question (poolKey = "skillName__type")
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

  // Get pool stats for a skill+type combo
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
      alert('Please select a Job Posting or Candidate Request before creating a test.')
      return
    }
    setCtCreating(true)
    try {
      let jobId = ctForm.job_id

      // Check if selected value is a candidate request (not a real job)
      const isRealJob = allJobs.some(j => j.job_id === jobId)
      if (!isRealJob) {
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
        // No DRAFT exists — create full interview
        // Pre-check: only one interview per job allowed
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
          security_settings: { enable_webcam_monitoring: ctForm.enable_webcam_monitoring, enable_tab_switch_detection: ctForm.enable_tab_switch_detection, copy_paste_allowed: ctForm.copy_paste_allowed },
          interviewer_details: { name: ctForm.interviewer_name, email: ctForm.interviewer_email, meeting_link: ctForm.meeting_link || 'N/A' },
          notes: ctForm.notes,
        }
        const res = await api.post('/interviewer/interviews', payload)
        testId = res.data.id
      }

      setCreatedTestId(testId)
      fetchInterviews()

      // Assemble test sets from accepted pool questions
      setGenLoading(true)
      setAssembledSets([])
      setCtStep(5)
      try {
        // Build skill_type_targets from per-skill config
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
        // New response is ConfigureAssemblyResponse with pool_status instead of sets
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

  // ── helpers ──
  const interviewList = Array.isArray(interviews) ? interviews : []
  const completedInterviews = interviewList.filter(iv =>
    (iv.status === 'COMPLETED' || iv.status === 'READY')
  )

  return (
    <>
      <Header title="Interviewer Hub" subtitle="Manage interviews & provide feedback" />


      {/* ═══════════ DASHBOARD TAB ═══════════ */}
      {tab === 'dashboard' && (() => {
        const user = JSON.parse(localStorage.getItem('hireflow_user') || '{}')
        const firstName = user.first_name || 'Interviewer'

        // Derive stats from already-fetched state
        const draftTests    = interviews.filter(i => ['DRAFT','GENERATING'].includes(i.status))
        const readyTests    = interviews.filter(i => i.status === 'READY')
        const scheduledTests = interviews.filter(i => i.status === 'SCHEDULED')
        const completedTests = interviews.filter(i => i.status === 'COMPLETED')
        const upcomingMeetings = myMeetings.slice(0, 5)
        const pendingRequests  = crRequests.filter(r => r.status === 'PENDING' || r.status === 'pending')

        // Pipeline donut data
        const pipelineData = [
          { label: 'Draft',     count: draftTests.length,     color: '#64748b' },
          { label: 'Ready',     count: readyTests.length,     color: '#22c55e' },
          { label: 'Scheduled', count: scheduledTests.length, color: '#3b82f6' },
          { label: 'Completed', count: completedTests.length, color: '#a855f7' },
        ]
        const totalTests = interviews.length || 1

        // SVG donut
        const R = 54, CX = 70, CY = 70, stroke = 18
        const circ = 2 * Math.PI * R
        let cumulative = 0
        const donutSegments = pipelineData.map(seg => {
          const pct = seg.count / totalTests
          const dash = pct * circ
          const offset = circ - cumulative * circ
          cumulative += pct
          return { ...seg, dash, offset }
        })

        // Question type bar data from dashStats
        const qtMap = dashStats?.questions_by_type || {}
        const totalQ = Object.values(qtMap).reduce((s, v) => s + v, 0) || 1
        const qtypeLabels = { multiple_choice: 'MCQ', coding: 'Coding', short_answer: 'Descriptive', case_study: 'Case Study', aptitude: 'Aptitude', communication: 'Communication', mcq: 'MCQ', descriptive: 'Descriptive' }
        const qtColors = { multiple_choice: '#3b82f6', mcq: '#3b82f6', coding: '#f59e0b', short_answer: '#22c55e', descriptive: '#22c55e', case_study: '#8b5cf6', aptitude: '#ec4899', communication: '#06b6d4' }

        const statCard = (label, value, sub, accent, icon) => (
          <div key={label} style={{
            background: 'var(--bg-secondary)', borderRadius: 14, padding: '20px 24px',
            border: `1px solid var(--border-subtle)`, display: 'flex', flexDirection: 'column', gap: 6,
            borderLeft: `4px solid ${accent}`
          }}>
            <div style={{ fontSize: '0.78rem', color: 'var(--text-secondary)', textTransform: 'uppercase', letterSpacing: '0.05em', fontWeight: 600 }}>{label}</div>
            <div style={{ fontSize: '2rem', fontWeight: 700, color: accent, lineHeight: 1 }}>{value}</div>
            {sub && <div style={{ fontSize: '0.75rem', color: 'var(--text-tertiary)' }}>{sub}</div>}
          </div>
        )

        return (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 24 }}>
            {/* Greeting */}
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <div>
                <h2 style={{ margin: 0, fontSize: '1.5rem' }}>Welcome back, {firstName} 👋</h2>
                <div style={{ color: 'var(--text-secondary)', fontSize: '0.88rem', marginTop: 4 }}>
                  Here's a snapshot of your activity
                </div>
              </div>
              <div style={{ display: 'flex', gap: 10 }}>
                <button className="btn btn-primary btn-sm" onClick={() => setTab('create-test')}>+ Create Test</button>
                <button className="btn btn-secondary btn-sm" onClick={() => setTab('interviews')}>My Interviews</button>
              </div>
            </div>

            {/* Stat Cards */}
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 16 }}>
              {statCard('Upcoming Meetings', upcomingMeetings.length, myMeetings.length > 0 ? `${myMeetings.length} total assigned` : 'None assigned yet', '#3b82f6')}
              {statCard('Tests Ready', readyTests.length, `${draftTests.length} draft · ${scheduledTests.length} scheduled`, '#22c55e')}
              {statCard('Assigned Jobs', assignments.length, assignments.length > 0 ? `${assignments.reduce((s, a) => s + (a.candidate_count || 0), 0)} candidates` : 'No jobs assigned', '#f59e0b')}
              {statCard('Candidate Requests', crRequests.length, `${pendingRequests.length} pending review`, '#a855f7')}
            </div>

            {/* Row 2: Donut + Upcoming Meetings */}
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1.6fr', gap: 16 }}>
              {/* Test Pipeline donut */}
              <div style={{ background: 'var(--bg-secondary)', borderRadius: 14, padding: 24, border: '1px solid var(--border-subtle)' }}>
                <div style={{ fontWeight: 700, fontSize: '1rem', marginBottom: 16 }}>Test Pipeline</div>
                <div style={{ display: 'flex', gap: 20, alignItems: 'center' }}>
                  <svg width={140} height={140} viewBox="0 0 140 140">
                    {interviews.length === 0 ? (
                      <circle cx={CX} cy={CY} r={R} fill="none" stroke="var(--border-subtle)" strokeWidth={stroke} />
                    ) : donutSegments.map((seg, i) => seg.count > 0 && (
                      <circle key={i} cx={CX} cy={CY} r={R} fill="none"
                        stroke={seg.color} strokeWidth={stroke}
                        strokeDasharray={`${seg.dash} ${circ}`}
                        strokeDashoffset={seg.offset}
                        style={{ transition: 'stroke-dasharray 0.4s ease' }}
                        transform={`rotate(-90 ${CX} ${CY})`} />
                    ))}
                    <text x={CX} y={CY - 6} textAnchor="middle" style={{ fontSize: 22, fontWeight: 700, fill: 'var(--text-primary)' }}>{interviews.length}</text>
                    <text x={CX} y={CY + 12} textAnchor="middle" style={{ fontSize: 11, fill: 'var(--text-secondary)' }}>total</text>
                  </svg>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 8, flex: 1 }}>
                    {pipelineData.map(seg => (
                      <div key={seg.label} style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                        <div style={{ width: 10, height: 10, borderRadius: '50%', background: seg.color, flexShrink: 0 }} />
                        <div style={{ flex: 1, fontSize: '0.82rem', color: 'var(--text-secondary)' }}>{seg.label}</div>
                        <div style={{ fontWeight: 700, fontSize: '0.9rem', color: seg.color }}>{seg.count}</div>
                      </div>
                    ))}
                  </div>
                </div>
              </div>

              {/* Upcoming Meetings */}
              <div style={{ background: 'var(--bg-secondary)', borderRadius: 14, padding: 24, border: '1px solid var(--border-subtle)' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
                  <div style={{ fontWeight: 700, fontSize: '1rem' }}>Upcoming Meetings</div>
                  <button className="btn btn-ghost btn-sm" style={{ fontSize: '0.75rem' }} onClick={() => setTab('interviews')}>View All →</button>
                </div>
                {upcomingMeetings.length === 0 ? (
                  <div style={{ textAlign: 'center', padding: '28px 0', color: 'var(--text-tertiary)' }}>
                    <Calendar size={28} style={{ marginBottom: 8, opacity: 0.4 }} />
                    <div style={{ fontSize: '0.85rem' }}>No upcoming meetings</div>
                  </div>
                ) : upcomingMeetings.map(m => {
                  const dt = m.scheduled_datetime ? new Date(m.scheduled_datetime) : null
                  return (
                    <div key={m.id} style={{
                      display: 'flex', gap: 12, alignItems: 'flex-start',
                      padding: '10px 0', borderBottom: '1px solid var(--border-subtle)'
                    }}>
                      <div style={{
                        width: 40, height: 40, borderRadius: 10, background: '#3b82f620',
                        display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', flexShrink: 0
                      }}>
                        {dt ? (
                          <>
                            <div style={{ fontSize: '0.65rem', color: '#3b82f6', fontWeight: 700, lineHeight: 1 }}>{dt.toLocaleString('default', { month: 'short' }).toUpperCase()}</div>
                            <div style={{ fontSize: '1rem', color: '#3b82f6', fontWeight: 800, lineHeight: 1 }}>{dt.getDate()}</div>
                          </>
                        ) : <Calendar size={16} style={{ color: '#3b82f6' }} />}
                      </div>
                      <div style={{ flex: 1, minWidth: 0 }}>
                        <div style={{ fontWeight: 600, fontSize: '0.88rem', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                          {m.candidate_name || m.title || 'Interview'}
                        </div>
                        <div style={{ fontSize: '0.75rem', color: 'var(--text-secondary)' }}>
                          {m.job_title || m.interview_type || '—'}
                          {dt && <span style={{ marginLeft: 8 }}>· {dt.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</span>}
                        </div>
                      </div>
                      <div style={{
                        fontSize: '0.68rem', padding: '2px 8px', borderRadius: 8, fontWeight: 600, flexShrink: 0,
                        background: '#3b82f620', color: '#3b82f6'
                      }}>SCHEDULED</div>
                    </div>
                  )
                })}
              </div>
            </div>

            {/* Row 3: Question Bank + Assigned Jobs */}
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
              {/* Question bank by type */}
              <div style={{ background: 'var(--bg-secondary)', borderRadius: 14, padding: 24, border: '1px solid var(--border-subtle)' }}>
                <div style={{ fontWeight: 700, fontSize: '1rem', marginBottom: 4 }}>Question Bank</div>
                <div style={{ fontSize: '0.78rem', color: 'var(--text-secondary)', marginBottom: 16 }}>
                  {dashStats?.total_questions ?? '—'} questions · {dashStats?.total_interviews ?? interviews.length} tests
                </div>
                {Object.keys(qtMap).length === 0 ? (
                  <div>
                    {/* Difficulty breakdown from interviews */}
                    {['DRAFT', 'READY', 'SCHEDULED', 'COMPLETED'].map(s => {
                      const cnt = interviews.filter(i => i.status === s).length
                      const pct = interviews.length ? Math.round((cnt / interviews.length) * 100) : 0
                      const color = { DRAFT: '#64748b', READY: '#22c55e', SCHEDULED: '#3b82f6', COMPLETED: '#a855f7' }[s]
                      return (
                        <div key={s} style={{ marginBottom: 10 }}>
                          <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.78rem', marginBottom: 4 }}>
                            <span style={{ color: 'var(--text-secondary)' }}>{s}</span>
                            <span style={{ fontWeight: 600, color }}>{cnt}</span>
                          </div>
                          <div style={{ height: 6, background: 'var(--bg-hover)', borderRadius: 4 }}>
                            <div style={{ height: '100%', width: `${pct}%`, background: color, borderRadius: 4, transition: 'width 0.5s ease' }} />
                          </div>
                        </div>
                      )
                    })}
                  </div>
                ) : (
                  <div>
                    {Object.entries(qtMap).map(([type, count]) => {
                      const pct = Math.round((count / totalQ) * 100)
                      const color = qtColors[type] || '#94a3b8'
                      return (
                        <div key={type} style={{ marginBottom: 10 }}>
                          <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.78rem', marginBottom: 4 }}>
                            <span style={{ color: 'var(--text-secondary)' }}>{qtypeLabels[type] || type}</span>
                            <span style={{ fontWeight: 600, color }}>{count}</span>
                          </div>
                          <div style={{ height: 6, background: 'var(--bg-hover)', borderRadius: 4 }}>
                            <div style={{ height: '100%', width: `${pct}%`, background: color, borderRadius: 4, transition: 'width 0.5s ease' }} />
                          </div>
                        </div>
                      )
                    })}
                  </div>
                )}
              </div>

              {/* Assigned Jobs */}
              <div style={{ background: 'var(--bg-secondary)', borderRadius: 14, padding: 24, border: '1px solid var(--border-subtle)' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
                  <div style={{ fontWeight: 700, fontSize: '1rem' }}>Assigned Jobs</div>
                  {assignments.length > 3 && <span style={{ fontSize: '0.75rem', color: 'var(--text-tertiary)' }}>Showing top 3</span>}
                </div>
                {assignments.length === 0 ? (
                  <div style={{ textAlign: 'center', padding: '28px 0', color: 'var(--text-tertiary)' }}>
                    <Star size={28} style={{ marginBottom: 8, opacity: 0.4 }} />
                    <div style={{ fontSize: '0.85rem' }}>No jobs assigned yet</div>
                  </div>
                ) : assignments.slice(0, 3).map(a => (
                  <div key={a.job_id || a.id} style={{
                    padding: '10px 0', borderBottom: '1px solid var(--border-subtle)',
                    display: 'flex', justifyContent: 'space-between', alignItems: 'center'
                  }}>
                    <div>
                      <div style={{ fontWeight: 600, fontSize: '0.88rem' }}>{a.job_title || a.title || 'Job'}</div>
                      <div style={{ fontSize: '0.75rem', color: 'var(--text-secondary)' }}>
                        {a.department || '—'}
                        {a.candidate_count > 0 && <span style={{ marginLeft: 8 }}>· {a.candidate_count} candidates</span>}
                      </div>
                    </div>
                    <div style={{ textAlign: 'right' }}>
                      <div style={{ fontSize: '0.75rem', fontWeight: 600, color: '#f59e0b' }}>{a.interviews_scheduled || 0} scheduled</div>
                      <div style={{ fontSize: '0.7rem', color: 'var(--text-tertiary)' }}>{a.shortlisted_count || 0} shortlisted</div>
                    </div>
                  </div>
                ))}
              </div>
            </div>

            {/* Row 4: Recent Tests */}
            {interviews.length > 0 && (
              <div style={{ background: 'var(--bg-secondary)', borderRadius: 14, padding: 24, border: '1px solid var(--border-subtle)' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
                  <div style={{ fontWeight: 700, fontSize: '1rem' }}>Recent Tests</div>
                  <button className="btn btn-ghost btn-sm" style={{ fontSize: '0.75rem' }} onClick={() => setTab('create-test')}>+ New Test</button>
                </div>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(260px, 1fr))', gap: 12 }}>
                  {interviews.slice(0, 6).map(iv => {
                    const statusColor = { DRAFT: '#64748b', GENERATING: '#f59e0b', READY: '#22c55e', SCHEDULED: '#3b82f6', COMPLETED: '#a855f7' }[iv.status] || '#94a3b8'
                    return (
                      <div key={iv.id} style={{
                        padding: '14px 16px', borderRadius: 10,
                        border: `1px solid var(--border-subtle)`, background: 'var(--bg-primary)'
                      }}>
                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 6 }}>
                          <div style={{ fontWeight: 600, fontSize: '0.88rem', flex: 1, marginRight: 8, lineHeight: 1.3 }}>{iv.title || iv.job_title || 'Untitled Test'}</div>
                          <span style={{
                            fontSize: '0.65rem', padding: '2px 7px', borderRadius: 8, fontWeight: 700, flexShrink: 0,
                            background: `${statusColor}22`, color: statusColor
                          }}>{iv.status}</span>
                        </div>
                        <div style={{ fontSize: '0.75rem', color: 'var(--text-secondary)' }}>
                          {iv.job_position || iv.interview_type || '—'}
                          {iv.difficulty_level && <span> · {iv.difficulty_level}</span>}
                        </div>
                      </div>
                    )
                  })}
                </div>
              </div>
            )}
          </div>
        )
      })()}

      {/* ═══════════ INTERVIEWS TAB ═══════════ */}
      {tab === 'interviews' && (() => {
        const testTemplates = interviewList.filter(iv => iv.status !== 'SCHEDULED')
        return (
        <>
          {/* Scheduled Meetings from HR */}
          <div style={{ marginBottom: 24 }}>
            <h3 style={{ marginBottom: 16, fontSize: '1.1rem' }}>My Scheduled Meetings {myMeetings.length > 0 && <span className="badge badge-green" style={{ marginLeft: 8 }}>{myMeetings.length}</span>}</h3>
            {myMeetings.length === 0 ? (
              <div className="card" style={{ padding: 40, textAlign: 'center', color: 'var(--text-tertiary)' }}>
                No meetings scheduled yet. HR will assign interviews to you after candidates pass the test.
              </div>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
                {myMeetings.map(m => {
                  const isOnline = m.mode === 'online'
                  return (
                    <div key={m.id} className="card" style={{ padding: 0, overflow: 'hidden' }}>
                      <div style={{ display: 'flex' }}>
                        <div style={{ width: 5, background: 'var(--accent-primary)', flexShrink: 0 }} />
                        <div style={{ flex: 1, padding: '20px 24px' }}>
                          {/* Header */}
                          <div className="flex-between" style={{ marginBottom: 14 }}>
                            <div>
                              <div style={{ fontWeight: 700, fontSize: '1.05rem', marginBottom: 2 }}>{m.candidate_name}</div>
                              <div style={{ fontSize: '0.82rem', color: 'var(--text-secondary)' }}>{m.job_title}{m.department ? ` — ${m.department}` : ''} — {m.interview_type}</div>
                            </div>
                            <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                              <span style={{ padding: '4px 10px', borderRadius: 6, fontSize: '0.73rem', fontWeight: 600, background: isOnline ? '#eef4fb' : '#fef7ec', color: isOnline ? '#2d5f8a' : '#92610a' }}>
                                {isOnline ? '🖥 Online' : '🏢 Offline'}
                              </span>
                              <span className="badge badge-green">SCHEDULED</span>
                            </div>
                          </div>

                          {/* Candidate Details */}
                          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr 1fr', gap: 10, marginBottom: 14 }}>
                            <div style={{ padding: '8px 12px', background: 'var(--bg-tertiary)', borderRadius: 8 }}>
                              <div style={{ fontSize: '0.63rem', fontWeight: 700, textTransform: 'uppercase', color: 'var(--text-tertiary)', marginBottom: 2 }}>Email</div>
                              <div style={{ fontSize: '0.8rem', wordBreak: 'break-all' }}>{m.candidate_email || '—'}</div>
                            </div>
                            <div style={{ padding: '8px 12px', background: 'var(--bg-tertiary)', borderRadius: 8 }}>
                              <div style={{ fontSize: '0.63rem', fontWeight: 700, textTransform: 'uppercase', color: 'var(--text-tertiary)', marginBottom: 2 }}>Phone</div>
                              <div style={{ fontSize: '0.8rem', fontFamily: 'var(--font-mono)' }}>{m.candidate_phone || '—'}</div>
                            </div>
                            <div style={{ padding: '8px 12px', background: 'var(--bg-tertiary)', borderRadius: 8 }}>
                              <div style={{ fontSize: '0.63rem', fontWeight: 700, textTransform: 'uppercase', color: 'var(--text-tertiary)', marginBottom: 2 }}>Date & Time</div>
                              <div style={{ fontSize: '0.8rem', fontFamily: 'var(--font-mono)' }}>{m.scheduled_datetime ? new Date(m.scheduled_datetime).toLocaleString() : '—'}</div>
                            </div>
                            <div style={{ padding: '8px 12px', background: 'var(--bg-tertiary)', borderRadius: 8 }}>
                              <div style={{ fontSize: '0.63rem', fontWeight: 700, textTransform: 'uppercase', color: 'var(--text-tertiary)', marginBottom: 2 }}>Duration</div>
                              <div style={{ fontSize: '0.8rem' }}>{m.duration_minutes || 60} min</div>
                            </div>
                          </div>

                          {/* Skills */}
                          {m.candidate_skills && m.candidate_skills.length > 0 && (
                            <div style={{ marginBottom: 12 }}>
                              <span style={{ fontSize: '0.7rem', fontWeight: 700, textTransform: 'uppercase', color: 'var(--text-tertiary)', marginRight: 8 }}>Skills:</span>
                              {m.candidate_skills.slice(0, 8).map((s, i) => (
                                <span key={i} style={{ padding: '2px 8px', background: 'var(--accent-light)', color: 'var(--accent-primary)', borderRadius: 12, fontSize: '0.72rem', marginRight: 4, fontWeight: 500 }}>{s}</span>
                              ))}
                              {m.candidate_skills.length > 8 && <span style={{ fontSize: '0.72rem', color: 'var(--text-tertiary)' }}>+{m.candidate_skills.length - 8} more</span>}
                            </div>
                          )}

                          {/* ATS Score */}
                          {m.ats_score != null && (
                            <div style={{ marginBottom: 12 }}>
                              <span style={{ fontSize: '0.7rem', fontWeight: 700, textTransform: 'uppercase', color: 'var(--text-tertiary)', marginRight: 8 }}>ATS Score:</span>
                              <span style={{ fontWeight: 700, color: m.ats_score >= 70 ? 'var(--status-success)' : m.ats_score >= 40 ? 'var(--status-warning)' : 'var(--status-danger)' }}>{m.ats_score}%</span>
                            </div>
                          )}

                          {/* Action Buttons */}
                          <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                            {isOnline && m.meeting_link && (
                              <a href={m.meeting_link} target="_blank" rel="noreferrer" style={{ padding: '7px 16px', borderRadius: 8, background: 'var(--accent-primary)', color: '#fff', fontSize: '0.82rem', fontWeight: 600, textDecoration: 'none' }}>
                                Join Meeting
                              </a>
                            )}
                            {!isOnline && m.location && (
                              <span style={{ padding: '7px 16px', borderRadius: 8, background: 'var(--bg-tertiary)', color: 'var(--text-primary)', fontSize: '0.82rem', border: '1px solid var(--border-subtle)' }}>
                                📍 {m.location}
                              </span>
                            )}
                            {m.resume_path && (
                              <a href={`/${m.resume_path}`} target="_blank" rel="noreferrer" style={{ padding: '7px 16px', borderRadius: 8, background: '#eef6f3', color: 'var(--accent-primary)', fontSize: '0.82rem', fontWeight: 600, textDecoration: 'none', border: '1px solid #c8e6dc' }}>
                                📄 View Resume
                              </a>
                            )}
                            <button onClick={() => { setMeetingFeedback(m.id); setMfForm({ technical_score: 0, communication_score: 0, problem_solving_score: 0, cultural_fit_score: 0, strengths: '', weaknesses: '', recommendations: '', hire_recommendation: 'proceed' }) }}
                              style={{ padding: '7px 16px', borderRadius: 8, background: 'var(--status-warning)', color: '#fff', fontSize: '0.82rem', fontWeight: 600, border: 'none', cursor: 'pointer' }}>
                              ✍ Complete & Give Feedback
                            </button>
                          </div>

                          {/* Inline Feedback Form */}
                          {meetingFeedback === m.id && (
                            <div style={{ marginTop: 16, padding: 20, background: 'var(--bg-tertiary)', borderRadius: 12, border: '1px solid var(--border-subtle)' }}>
                              <div className="flex-between" style={{ marginBottom: 14 }}>
                                <h4 style={{ margin: 0, fontSize: '0.95rem' }}>Interview Feedback — {m.candidate_name}</h4>
                                <button onClick={() => setMeetingFeedback(null)} style={{ background: 'none', border: 'none', cursor: 'pointer', fontSize: '1.1rem', color: 'var(--text-tertiary)' }}>✕</button>
                              </div>
                              <form onSubmit={submitMeetingFeedback}>
                                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginBottom: 14 }}>
                                  {[
                                    { key: 'technical_score', label: 'Technical Skills' },
                                    { key: 'communication_score', label: 'Communication' },
                                    { key: 'problem_solving_score', label: 'Problem Solving' },
                                    { key: 'cultural_fit_score', label: 'Cultural Fit' },
                                  ].map(({ key, label }) => (
                                    <div key={key} className="input-group">
                                      <label>{label} (0-10)</label>
                                      <input className="input" type="number" min={0} max={10} step={0.5}
                                        value={mfForm[key]} onChange={e => setMfForm({ ...mfForm, [key]: parseFloat(e.target.value) || 0 })} />
                                    </div>
                                  ))}
                                </div>
                                <div className="input-group" style={{ marginBottom: 12 }}>
                                  <label>Strengths (comma separated)</label>
                                  <input className="input" placeholder="e.g. Strong problem solving, Good communication"
                                    value={mfForm.strengths} onChange={e => setMfForm({ ...mfForm, strengths: e.target.value })} />
                                </div>
                                <div className="input-group" style={{ marginBottom: 12 }}>
                                  <label>Weaknesses (comma separated)</label>
                                  <input className="input" placeholder="e.g. Needs more experience with databases"
                                    value={mfForm.weaknesses} onChange={e => setMfForm({ ...mfForm, weaknesses: e.target.value })} />
                                </div>
                                <div className="input-group" style={{ marginBottom: 12 }}>
                                  <label>Recommendations</label>
                                  <textarea className="textarea" rows={2} placeholder="Overall assessment..."
                                    value={mfForm.recommendations} onChange={e => setMfForm({ ...mfForm, recommendations: e.target.value })} />
                                </div>
                                <div className="input-group" style={{ marginBottom: 16 }}>
                                  <label>Hiring Recommendation</label>
                                  <select className="select" value={mfForm.hire_recommendation} onChange={e => setMfForm({ ...mfForm, hire_recommendation: e.target.value })}>
                                    <option value="strong_hire">Strong Hire</option>
                                    <option value="hire">Hire</option>
                                    <option value="proceed">Proceed to Next Round</option>
                                    <option value="no_hire">No Hire</option>
                                    <option value="strong_no_hire">Strong No Hire</option>
                                  </select>
                                </div>
                                <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end' }}>
                                  <button type="button" className="btn btn-secondary btn-sm" onClick={() => setMeetingFeedback(null)}>Cancel</button>
                                  <button type="submit" className="btn btn-primary btn-sm" disabled={mfSubmitting}>
                                    {mfSubmitting ? 'Submitting...' : 'Submit Feedback & Complete'}
                                  </button>
                                </div>
                              </form>
                            </div>
                          )}
                        </div>
                      </div>
                    </div>
                  )
                })}
              </div>
            )}
          </div>

          {/* Assigned Jobs */}
          {assignments.length > 0 && (
            <div className="card mb-24">
              <h3 style={{ marginBottom: 16 }}>My Assigned Jobs</h3>
              <div className="table-wrapper">
                <table>
                  <thead><tr><th>Job Title</th><th>Department</th><th>Candidates</th><th>Interviews</th></tr></thead>
                  <tbody>
                    {assignments.map(job => (
                      <tr key={job.job_id}>
                        <td style={{ fontWeight: 600 }}>{job.job_title}</td>
                        <td>{job.department}</td>
                        <td className="mono">{job.candidate_count} ({job.shortlisted_count} shortlisted)</td>
                        <td className="mono">{job.interviews_scheduled}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {/* Test Templates */}
          <div className="card">
            <h3 style={{ marginBottom: 16 }}>My Tests</h3>
            {testTemplates.length === 0 ? (
              <div style={{ padding: 40, textAlign: 'center', color: 'var(--text-tertiary)' }}>
                No tests created yet. Go to Create Test to get started.
              </div>
            ) : (
              <div className="table-wrapper">
                <table>
                  <thead><tr><th>Title</th><th>Type</th><th>Difficulty</th><th>Status</th><th>Date</th><th>Actions</th></tr></thead>
                  <tbody>
                    {testTemplates.map(iv => (
                      <tr key={iv.id}>
                        <td style={{ fontWeight: 600 }}>{iv.title || iv.job_details?.job_position || iv.job_position || `Test #${iv.id}`}</td>
                        <td><span className="badge badge-blue">{iv.interview_configuration?.interview_type || iv.interview_type || 'N/A'}</span></td>
                        <td><span className="badge badge-amber">{iv.interview_configuration?.difficulty_level || iv.difficulty_level || 'N/A'}</span></td>
                        <td><span className={`badge ${iv.status === 'READY' ? 'badge-green' : iv.status === 'DRAFT' ? 'badge-amber' : 'badge-neutral'}`}>{iv.status}</span></td>
                        <td className="mono text-sm">{iv.interview_configuration?.scheduled_datetime ? new Date(iv.interview_configuration.scheduled_datetime).toLocaleDateString() : '—'}</td>
                        <td>
                          {iv.status === 'DRAFT' && (
                            <button className="btn btn-ghost btn-sm" onClick={() => loadDraftIntoWizard(iv)}>
                              <Edit3 size={14} /> Continue
                            </button>
                          )}
                          <button className="btn btn-ghost btn-sm" onClick={() => viewQuestions(iv)}>
                            <Brain size={14} /> Questions
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>

          {/* Proctoring Report — candidate camera-monitoring results for the selected test */}
          {selInterview && (
            <div className="card" style={{ marginTop: 24 }}>
              <div className="flex-between" style={{ marginBottom: 12 }}>
                <h3 style={{ margin: 0 }}>Proctoring — {selInterview.title}</h3>
              </div>
              <ProctoringReport interviewId={selInterview.id} />
            </div>
          )}

          {/* Question Viewer Panel — shows when questions loaded for a test */}
          {selInterview && questions.length > 0 && (
            <div className="card" style={{ marginTop: 24 }}>
              <div className="flex-between" style={{ marginBottom: 16 }}>
                <div className="flex-center gap-8">
                  <h3 style={{ margin: 0 }}>Questions — {selInterview.title}</h3>
                  <span className={`badge ${selInterview.status === 'READY' ? 'badge-green' : 'badge-amber'}`}>{selInterview.status}</span>
                </div>
                <button className="btn btn-ghost btn-sm" onClick={() => { setSelInterview(null); setQuestions([]); setEditingQ(null) }}>
                  <XCircle size={14} /> Close
                </button>
              </div>

              {selInterview.status === 'READY' && (
                <div style={{ padding: '8px 14px', background: '#fefce8', borderRadius: 8, marginBottom: 16, fontSize: '0.82rem', color: '#92400e', border: '1px solid #fde68a' }}>
                  This test is published. Changes to individual questions take effect immediately.
                </div>
              )}

              {/* Summary bar */}
              <div className="card mb-16" style={{ padding: '14px 20px', background: 'var(--bg-hover)' }}>
                <div className="flex gap-16" style={{ alignItems: 'center', flexWrap: 'wrap' }}>
                  <span style={{ fontSize: '0.9rem', fontWeight: 700 }}>{questions.length} Questions</span>
                  <span className="badge badge-green">{questions.filter(q => q.status === 'approved').length} approved</span>
                  <span className="badge badge-neutral">{questions.filter(q => q.status === 'generated').length} pending</span>
                  <span className="mono text-sm text-muted">
                    {questions.reduce((s, q) => s + (q.points || 10), 0)} pts · {questions.reduce((s, q) => s + (q.time_limit_minutes || 5), 0)} min
                  </span>
                  <button className="btn btn-secondary btn-sm" style={{ marginLeft: 'auto' }} onClick={approveAll}>
                    <CheckCircle2 size={14} /> Accept All
                  </button>
                </div>
              </div>

              {/* Publish controls for DRAFT tests */}
              {selInterview.status === 'DRAFT' && (
                <div className="flex gap-8 mb-16" style={{ alignItems: 'center' }}>
                  <select value={selectedHrId} onChange={e => setSelectedHrId(e.target.value)}
                    style={{ padding: '6px 10px', borderRadius: 6, border: '1px solid var(--border)', fontSize: '0.85rem', minWidth: 180 }}>
                    <option value="">— Select HR to Notify —</option>
                    {hrList.map(h => (
                      <option key={h.id} value={h.id}>{h.name} ({h.department})</option>
                    ))}
                  </select>
                  <button className="btn btn-primary btn-sm" disabled={publishLoading} onClick={() => finalizeInterview()}>
                    {publishLoading ? <><Loader2 size={14} className="spin" /> Publishing...</> : <><Send size={14} /> Publish Test</>}
                  </button>
                </div>
              )}

              {/* Question cards */}
              {questions.map((q, i) => {
                const hasEmbeddedOpts = q.question_text && /\s[A-D]\)\s/.test(q.question_text)
                let qText = q.question_text || ''
                let opts = q.options || []
                if (hasEmbeddedOpts && opts.every(o => /^[A-D]$/.test(o))) {
                  const m = qText.match(/^(.*?\?)\s*(A\).+)$/s)
                  if (m) {
                    qText = m[1].trim()
                    opts = m[2].split(/\s(?=[A-D]\))/).map(s => s.replace(/^[A-D]\)\s*/, '').trim())
                  }
                }
                const correctIdx = 'ABCD'.indexOf(q.correct_answer)
                const correctFull = correctIdx >= 0 && opts[correctIdx] ? opts[correctIdx] : q.correct_answer

                return (
                  <div key={q.id} className="card" style={{
                    marginBottom: 12, position: 'relative', overflow: 'hidden',
                    borderLeft: `4px solid ${q.status === 'approved' ? '#22c55e' : 'var(--accent-primary)'}`,
                  }}>
                    <div className="flex-between" style={{ marginBottom: 12 }}>
                      <div className="flex-center gap-8">
                        <span style={{
                          fontFamily: 'var(--font-mono)', fontSize: '0.75rem', fontWeight: 700,
                          color: q.status === 'approved' ? '#fff' : 'var(--accent-primary)',
                          background: q.status === 'approved' ? 'linear-gradient(135deg, #22c55e, #16a34a)' : 'var(--accent-glow)',
                          width: 30, height: 30, display: 'grid', placeItems: 'center', borderRadius: 8
                        }}>Q{i + 1}</span>
                        <span className={`badge ${q.question_type === 'multiple_choice' ? 'badge-blue' : q.question_type === 'coding' ? 'badge-purple' : 'badge-amber'}`}>
                          {(q.question_type || '').replace('_', ' ')}
                        </span>
                        <span className={`badge ${q.difficulty_level === 'Easy' ? 'badge-green' : q.difficulty_level === 'Hard' ? 'badge-red' : 'badge-amber'}`}>{q.difficulty_level}</span>
                        {q.category && <span className="badge badge-neutral">{q.category}</span>}
                        <span className="mono text-sm text-muted">{q.points || 10} pts · {q.time_limit_minutes || 5} min</span>
                      </div>
                      {q.status === 'approved' && <span className="badge badge-green">Accepted</span>}
                    </div>

                    <div style={{ fontSize: '0.95rem', lineHeight: 1.7, fontWeight: 500, marginBottom: 14, color: 'var(--text-primary)' }}>{qText}</div>

                    {q.question_type === 'multiple_choice' && opts.length > 0 && (
                      <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginBottom: 14 }}>
                        {opts.map((opt, j) => {
                          const letter = String.fromCharCode(65 + j)
                          const isCorrect = letter === q.correct_answer || opt === correctFull
                          return (
                            <div key={j} style={{
                              display: 'flex', alignItems: 'center', gap: 12, padding: '10px 16px', borderRadius: 10,
                              background: isCorrect ? 'rgba(34,197,94,0.08)' : 'var(--bg-hover)',
                              border: isCorrect ? '1.5px solid rgba(34,197,94,0.3)' : '1.5px solid transparent',
                            }}>
                              <span style={{
                                width: 28, height: 28, borderRadius: 7, display: 'grid', placeItems: 'center',
                                fontSize: '0.75rem', fontWeight: 700, fontFamily: 'var(--font-mono)',
                                background: isCorrect ? 'linear-gradient(135deg, #22c55e, #16a34a)' : 'var(--bg-primary)',
                                color: isCorrect ? '#fff' : 'var(--text-tertiary)',
                              }}>{letter}</span>
                              <span style={{ flex: 1, fontSize: '0.9rem', fontWeight: isCorrect ? 600 : 400, color: isCorrect ? '#16a34a' : 'var(--text-primary)' }}>{opt}</span>
                              {isCorrect && <CheckCircle2 size={16} style={{ color: '#22c55e' }} />}
                            </div>
                          )
                        })}
                      </div>
                    )}

                    {q.correct_answer && q.question_type !== 'multiple_choice' && (
                      <div style={{ padding: '10px 14px', background: 'rgba(34,197,94,0.05)', borderRadius: 8, marginBottom: 12, border: '1px solid rgba(34,197,94,0.15)' }}>
                        <strong style={{ color: '#22c55e', fontSize: '0.72rem', textTransform: 'uppercase' }}>Answer:</strong>
                        <div style={{ marginTop: 4, fontSize: '0.88rem' }}>{q.correct_answer}</div>
                      </div>
                    )}

                    {q.code_template && (
                      <pre style={{ padding: 12, background: 'var(--bg-hover)', borderRadius: 8, fontSize: '0.8rem', fontFamily: 'var(--font-mono)', overflow: 'auto', marginBottom: 10 }}>{q.code_template}</pre>
                    )}

                    {q.question_type === 'coding' && <TestCasesView testCases={q.test_cases} />}

                    {q.sample_answer && (
                      <details style={{ marginBottom: 8 }}>
                        <summary style={{ cursor: 'pointer', fontSize: '0.85rem', color: 'var(--accent-primary)', fontWeight: 500 }}>Sample Answer</summary>
                        <div style={{ marginTop: 6, padding: '10px 14px', background: 'var(--bg-hover)', borderRadius: 8, fontSize: '0.85rem', lineHeight: 1.6 }}>{q.sample_answer}</div>
                      </details>
                    )}

                    <div style={{ display: 'flex', gap: 10, marginTop: 16, paddingTop: 14, borderTop: '1px solid var(--border-subtle)', alignItems: 'center' }}>
                      {q.status === 'approved' ? (
                        <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '9px 18px', borderRadius: 8, background: 'rgba(34,197,94,0.08)', border: '1px solid rgba(34,197,94,0.2)', color: '#22c55e', fontSize: '0.84rem', fontWeight: 700 }}>
                          <CheckCircle2 size={16} /> Accepted
                        </div>
                      ) : (
                        <button onClick={() => approveQuestion(q.id)} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '9px 22px', borderRadius: 8, border: 'none', background: 'linear-gradient(135deg, #22c55e, #16a34a)', color: '#fff', fontSize: '0.84rem', fontWeight: 700, cursor: 'pointer' }}>
                          <CheckCircle2 size={16} /> Accept
                        </button>
                      )}
                      <button onClick={() => replaceQuestion(q.id)} disabled={replacingQ === q.id} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '9px 22px', borderRadius: 8, border: '1.5px solid var(--border-medium)', background: replacingQ === q.id ? 'var(--bg-hover)' : '#fff', color: 'var(--accent-primary)', fontSize: '0.84rem', fontWeight: 700, cursor: replacingQ === q.id ? 'wait' : 'pointer', opacity: replacingQ === q.id ? 0.7 : 1 }}>
                        {replacingQ === q.id ? <><Loader2 size={16} className="spin" /> Replacing...</> : <><RefreshCw size={16} /> Replace</>}
                      </button>
                      <button onClick={() => deleteQuestion(q.id)} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '9px 22px', borderRadius: 8, border: '1.5px solid #fecaca', background: '#fff', color: '#ef4444', fontSize: '0.84rem', fontWeight: 700, cursor: 'pointer' }}>
                        <Trash2 size={16} /> Delete
                      </button>
                      {q.status === 'approved' && (
                        <button onClick={() => rejectQuestion(q.id)} className="btn btn-ghost btn-sm" style={{ marginLeft: 'auto', color: 'var(--text-tertiary)', fontSize: '0.78rem' }}>Undo</button>
                      )}
                    </div>
                  </div>
                )
              })}
            </div>
          )}

          {/* Loading state for question viewer */}
          {selInterview && genLoading && questions.length === 0 && (
            <div className="card" style={{ marginTop: 24, padding: 40, textAlign: 'center' }}>
              <Loader2 size={32} className="spin" style={{ color: 'var(--accent-primary)', marginBottom: 12 }} />
              <div style={{ fontSize: '0.95rem', fontWeight: 600 }}>Loading questions...</div>
            </div>
          )}
        </>
        )
      })()}

      {/* ═══════════ CREATE TEST TAB ═══════════ */}
      {tab === 'create-test' && (<>
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
                    onChange={e => {
                      const val = e.target.value
                      const job = allJobs.find(j => j.job_id === val)
                      const cr = crRequests.find(r => r.id === val)
                      const typeFromCategory = (cat) => {
                        const c = String(cat || '').toLowerCase()
                        if (c === 'non_technical' || c === 'non-technical') return 'NON_TECHNICAL'
                        if (c === 'technical') return 'TECHNICAL'
                        return null
                      }
                      if (job) {
                        const mapped = typeFromCategory(job.job_category)
                        setCtForm({
                          ...ctForm,
                          job_id: val,
                          job_position: job.job_title,
                          job_title: job.job_title,
                          interview_type: mapped || ctForm.interview_type,
                        })
                      } else if (cr) {
                        const skills = Array.isArray(cr.required_skills)
                          ? cr.required_skills
                          : (cr.required_skills ? cr.required_skills.split(',').map(s => s.trim()) : [])
                        const mapped = typeFromCategory(cr.job_category)
                        const isNonTech = mapped === 'NON_TECHNICAL'
                        const defaultQType = isNonTech ? 'multiple_choice' : 'multiple_choice'
                        setCtForm({ ...ctForm,
                          job_id: val,
                          title: cr.job_title + ' Test',
                          job_position: cr.job_title,
                          job_title: cr.job_title,
                          job_level: cr.role_level || ctForm.job_level,
                          role_type: cr.employment_type || ctForm.role_type,
                          description: cr.job_description || '',
                          notes: cr.notes || '',
                          interview_type: mapped || ctForm.interview_type,
                          skills: skills.length > 0
                            ? skills.map(s => ({ name: s, difficulty: 'MEDIUM', marks_per_question: 1, question_types: [{ type: defaultQType, count_per_set: 3, pool_size: null }] }))
                            : ctForm.skills,
                        })
                      }
                    }}>
                    <option value="">Select a job...</option>
                    {allJobs.filter(j => !j.has_test).length > 0 && <optgroup label="Job Postings">
                      {allJobs.filter(j => !j.has_test).map(j => (
                        <option key={j.job_id} value={j.job_id}>
                          {j.job_title} — {j.department}{j.status !== 'active' ? ` (${j.status})` : ''}
                        </option>
                      ))}
                    </optgroup>}
                    {(() => {
                      const unmatchedRequests = crRequests.filter(r => {
                        // If request is already linked to a job, hide it
                        if (r.job_id) return false
                        return true
                      })
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
                    <select className="select" value={ctForm.interview_type} onChange={e => {
                      const newType = e.target.value
                      setCtForm({ ...ctForm, interview_type: newType })
                    }}>
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
                        return `${c} ${TYPE_LABELS[t] || t} × ${per}m`
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
                  const typeLabels = { multiple_choice: 'MCQ', coding: 'Coding', short_answer: 'Descriptive', case_study: 'Case Study', aptitude: 'Aptitude', communication: 'Communication' }
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
                            display: 'flex', alignItems: 'center', gap: 12, padding: '10px 14px', marginBottom: 8,
                            background: 'var(--bg-primary)', borderRadius: 8,
                            borderLeft: `3px solid ${isReady ? '#22c55e' : stats.total > 0 ? '#f59e0b' : 'var(--border-subtle)'}`,
                          }}>
                            <div style={{ flex: 1 }}>
                              <div style={{ fontWeight: 600, fontSize: '0.85rem' }}>{typeLabels[qt.type] || qt.type}</div>
                              <div style={{ fontSize: '0.72rem', color: 'var(--text-tertiary)', marginTop: 2 }}>
                                {qt.count_per_set}/set &middot; need {needed} for {ctForm.num_sets} sets &middot; pool: {poolSize}
                              </div>
                            </div>
                            <div className="flex gap-6" style={{ alignItems: 'center' }}>
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
                              <div style={{ fontSize: '0.7rem', color: '#ef4444', fontWeight: 600 }}>
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
                      {ctForm.skills.filter(s => s.name).map((s, i) => {
                        const typeLabels = { multiple_choice: 'MCQ', coding: 'Coding', short_answer: 'Descriptive', case_study: 'Case Study', aptitude: 'Aptitude', communication: 'Communication' }
                        return (
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
                        )
                      })}
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
                            fetchInterviews()
                            setCtStep(1)
                            setTab('interviews')
                            setAssembledSets([])
                            setSkillQuestionPool({})
                            setCreatedTestId(null)
                            setDraftInterviewId(null)
                            draftIdRef.current = null
                            draftInFlightRef.current = null
                            setSelectedHrId('')
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
          const typeLabels = { multiple_choice: 'MCQ', coding: 'Coding', short_answer: 'Descriptive', case_study: 'Case Study', aptitude: 'Aptitude', communication: 'Communication' }

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

                        {/* Test cases (coding) */}
                        {q.question_type === 'coding' && <TestCasesView testCases={q.test_cases} compact />}

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
          const typeLabels = { multiple_choice: 'MCQ', coding: 'Coding', short_answer: 'Descriptive', case_study: 'Case Study', aptitude: 'Aptitude', communication: 'Communication' }
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
                        <div key={q.id} onClick={() => toggleSelect(q.id)} style={{
                          display: 'flex', gap: 12, padding: '14px 16px', marginBottom: 10,
                          border: `1px solid ${isSelected ? 'var(--accent-primary)' : 'var(--border-subtle)'}`,
                          borderRadius: 10, cursor: 'pointer', transition: 'all 0.15s',
                          background: isSelected ? 'var(--accent-primary-10, rgba(99,102,241,0.08))' : 'var(--bg-secondary)'
                        }}>
                          <div style={{ flexShrink: 0, paddingTop: 2 }}>
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
                        </div>
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
      </>)}

      {/* Manual MCQ entry modal */}
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

      {/* questions tab removed */}
      {false && (
        <div>
          {/* Config Panel */}
          <div className="card mb-24">
            <h3 style={{ marginBottom: 16 }}>Question Generator</h3>
            <div className="auth-form">
              <div className="grid-2">
                <div className="input-group">
                  <label>Select Interview / Test</label>
                  <select className="select" value={selInterview?.id || ''}
                    onChange={e => {
                      const iv = interviewList.find(i => String(i.id) === e.target.value)
                      setSelInterview(iv || null); setQuestions([]); setEditingQ(null)
                      // Drop coding/technical types from the selected list if we just picked a non-technical interview.
                      const isNonTech = String(iv?.interview_type || '').toUpperCase().replace('-', '_') === 'NON_TECHNICAL'
                      if (isNonTech) {
                        setGenQTypes(prev => prev.filter(t => t !== 'coding' && t !== 'technical'))
                      }
                    }}>
                    <option value="">Select interview...</option>
                    {interviewList.map(iv => (<option key={iv.id} value={iv.id}>{iv.title} ({iv.status})</option>))}
                  </select>
                </div>
                <div className="input-group">
                  <label>Number of Questions</label>
                  <input className="input" type="number" min={1} max={50} value={genCount}
                    onChange={e => setGenCount(parseInt(e.target.value) || 5)} />
                </div>
              </div>
              <div className="input-group">
                <label>Question Types</label>
                <div className="flex gap-8" style={{ flexWrap: 'wrap' }}>
                  {(() => {
                    const isNonTech = String(selInterview?.interview_type || '').toUpperCase().replace('-', '_') === 'NON_TECHNICAL'
                    const allTypes = ['multiple_choice', 'short_answer', 'coding', 'behavioral', 'technical', 'situational']
                    const types = isNonTech ? allTypes.filter(t => t !== 'coding' && t !== 'technical') : allTypes
                    return types.map(t => (
                      <button key={t} type="button" className={`btn btn-sm ${genQTypes.includes(t) ? 'btn-primary' : 'btn-ghost'}`}
                        onClick={() => toggleQType(t)}>{t.replace('_', ' ')}</button>
                    ))
                  })()}
                </div>
              </div>
              {genQTypes.includes('multiple_choice') && (
                <div className="input-group">
                  <label>MCQ Angles (optional — facets to test for each topic)</label>
                  <div className="flex gap-8" style={{ flexWrap: 'wrap' }}>
                    {MCQ_ANGLES.map(a => (
                      <button key={a.slug} type="button"
                        className={`btn btn-sm ${genMcqAngles.includes(a.slug) ? 'btn-primary' : 'btn-ghost'}`}
                        onClick={() => toggleMcqAngle(a.slug)}>{a.label}</button>
                    ))}
                  </div>
                </div>
              )}
              <div className="grid-2">
                <div className="input-group">
                  <label>Difficulty Distribution</label>
                  <div className="flex gap-8">
                    {['Easy', 'Medium', 'Hard'].map(d => (
                      <div key={d} className="flex-center gap-8">
                        <span className="text-sm" style={{ width: 50 }}>{d}</span>
                        <input className="input" type="number" min={0} max={20} style={{ width: 60 }}
                          value={genDiffDist[d] || 0} onChange={e => setGenDiffDist({ ...genDiffDist, [d]: parseInt(e.target.value) || 0 })} />
                      </div>
                    ))}
                  </div>
                </div>
                <div className="input-group">
                  <label>Categories (optional)</label>
                  <input className="input" placeholder="React, Node.js, System Design" value={genCategories} onChange={e => setGenCategories(e.target.value)} />
                </div>
              </div>
              <div style={{ textAlign: 'right' }}>
                <button className="btn btn-primary" disabled={!selInterview || genLoading} onClick={() => generateQuestions(selInterview)}>
                  {genLoading ? <><Loader2 size={16} className="spin" /> Generating...</> : <><Brain size={16} /> Generate Questions</>}
                </button>
              </div>
            </div>
          </div>

          {/* Questions Review */}
          {questions.length > 0 && (
            <>
              <div className="card mb-16" style={{ padding: '12px 20px' }}>
                <div className="flex-between">
                  <div className="flex gap-12">
                    <span className="text-sm"><strong>{questions.length}</strong> questions</span>
                    <span className="text-sm" style={{ color: 'var(--status-success)' }}>{questions.filter(q => q.status === 'approved').length} approved</span>
                    <span className="text-sm" style={{ color: 'var(--status-danger)' }}>{questions.filter(q => q.status === 'rejected').length} rejected</span>
                    <span className="text-sm text-muted">{totalPoints} pts · {totalTime} min</span>
                  </div>
                  <div className="flex gap-8" style={{ alignItems: 'center' }}>
                    <button className="btn btn-secondary btn-sm" onClick={approveAll}><CheckCircle2 size={14} /> Approve All</button>
                    <select
                      value={selectedHrId}
                      onChange={e => setSelectedHrId(e.target.value)}
                      style={{ padding: '6px 10px', borderRadius: 6, border: '1px solid var(--border)', fontSize: '0.85rem', minWidth: 180 }}
                    >
                      <option value="">— Select HR to Notify —</option>
                      {hrList.map(h => (
                        <option key={h.id} value={h.id}>{h.name} ({h.department})</option>
                      ))}
                    </select>
                    <button className="btn btn-primary btn-sm" disabled={publishLoading} onClick={finalizeInterview}>
                      {publishLoading ? <><Loader2 size={14} className="spin" /> Publishing...</> : <><Send size={14} /> Publish Test</>}
                    </button>
                  </div>
                </div>
              </div>

              {questions.map((q, i) => {
                const isEditing = editingQ === q.id
                // Parse options from question_text if options are just ["A","B","C","D"]
                const hasEmbeddedOptions = q.question_text && /\s[A-D]\)\s/.test(q.question_text)
                let parsedQuestion = q.question_text || ''
                let parsedOptions = q.options || []
                if (hasEmbeddedOptions && parsedOptions.every(o => /^[A-D]$/.test(o))) {
                  const match = parsedQuestion.match(/^(.*?\?)\s*(A\).+)$/s)
                  if (match) {
                    parsedQuestion = match[1].trim()
                    const optParts = match[2].split(/\s(?=[A-D]\))/).map(s => s.trim())
                    parsedOptions = optParts.map(p => p.replace(/^[A-D]\)\s*/, ''))
                  }
                }
                // Map correct_answer letter to full text
                const correctIdx = 'ABCD'.indexOf(q.correct_answer)
                const correctText = correctIdx >= 0 && parsedOptions[correctIdx] ? parsedOptions[correctIdx] : q.correct_answer

                return (
                  <div key={q.id} className="card" style={{
                    marginBottom: 14, position: 'relative', overflow: 'hidden',
                    borderLeft: `4px solid ${q.status === 'approved' ? 'var(--status-success)' : q.status === 'rejected' ? 'var(--status-danger)' : 'var(--accent-primary)'}`
                  }}>
                    {/* Accepted overlay shimmer */}
                    {q.status === 'approved' && (
                      <div style={{ position: 'absolute', top: 0, right: 0, width: 80, height: 80, pointerEvents: 'none',
                        background: 'radial-gradient(circle at 100% 0%, rgba(34,197,94,0.06), transparent 70%)' }} />
                    )}

                    {/* ── Question Header ── */}
                    <div className="flex-between" style={{ marginBottom: 14 }}>
                      <div className="flex-center gap-8">
                        <span style={{
                          fontFamily: 'var(--font-mono)', fontSize: '0.75rem', fontWeight: 700,
                          color: q.status === 'approved' ? '#fff' : 'var(--accent-primary)',
                          background: q.status === 'approved' ? 'linear-gradient(135deg, #22c55e, #16a34a)' : 'var(--accent-glow)',
                          width: 30, height: 30, display: 'grid', placeItems: 'center',
                          borderRadius: 8, flexShrink: 0
                        }}>Q{i + 1}</span>
                        <span className={`badge ${q.question_type === 'multiple_choice' ? 'badge-blue' : q.question_type === 'coding' ? 'badge-purple' : 'badge-amber'}`}>
                          {(q.question_type || '').replace('_', ' ')}
                        </span>
                        <span className={`badge ${q.difficulty_level === 'Easy' ? 'badge-green' : q.difficulty_level === 'Hard' ? 'badge-red' : 'badge-amber'}`}>{q.difficulty_level}</span>
                        {q.category && <span className="badge badge-neutral">{q.category}</span>}
                        <span className="mono text-sm text-muted">{q.points || 10} pts · {q.time_limit_minutes || 5} min</span>
                      </div>
                      <div className="flex gap-8">
                        <button className="btn btn-ghost btn-sm" onClick={() => setEditingQ(isEditing ? null : q.id)}>
                          <Edit3 size={14} /> {isEditing ? 'Done' : 'Edit'}
                        </button>
                        <button className="btn btn-ghost btn-sm" style={{ color: 'var(--text-tertiary)' }} onClick={() => deleteQuestion(q.id)}>
                          <Trash2 size={14} />
                        </button>
                      </div>
                    </div>

                    {/* ── Editable Metadata ── */}
                    {isEditing && (
                      <div className="flex gap-8 mb-16" style={{ flexWrap: 'wrap' }}>
                        <div className="input-group" style={{ marginBottom: 0, flex: '1 1 140px' }}>
                          <label className="text-sm">Type</label>
                          <select className="select" value={q.question_type || ''} onChange={e => updateQ(q.id, 'question_type', e.target.value)}>
                            {(() => {
                              const isNonTech = String(selInterview?.interview_type || '').toUpperCase().replace('-', '_') === 'NON_TECHNICAL'
                              const allTypes = ['multiple_choice', 'short_answer', 'coding', 'behavioral', 'technical', 'situational']
                              const types = isNonTech ? allTypes.filter(t => t !== 'coding' && t !== 'technical') : allTypes
                              return types.map(t => (
                                <option key={t} value={t}>{t.replace('_', ' ')}</option>
                              ))
                            })()}
                          </select>
                        </div>
                        <div className="input-group" style={{ marginBottom: 0, flex: '1 1 100px' }}>
                          <label className="text-sm">Difficulty</label>
                          <select className="select" value={q.difficulty_level || 'Medium'} onChange={e => updateQ(q.id, 'difficulty_level', e.target.value)}>
                            <option value="Easy">Easy</option><option value="Medium">Medium</option><option value="Hard">Hard</option>
                          </select>
                        </div>
                        <div className="input-group" style={{ marginBottom: 0, flex: '0 0 80px' }}>
                          <label className="text-sm">Points</label>
                          <input className="input" type="number" min={1} value={q.points || 1} onChange={e => updateQ(q.id, 'points', parseInt(e.target.value) || 1)} />
                        </div>
                        <div className="input-group" style={{ marginBottom: 0, flex: '0 0 80px' }}>
                          <label className="text-sm">Time (min)</label>
                          <input className="input" type="number" min={1} value={q.time_limit_minutes || 5} onChange={e => updateQ(q.id, 'time_limit_minutes', parseInt(e.target.value) || 5)} />
                        </div>
                        <div className="input-group" style={{ marginBottom: 0, flex: '1 1 120px' }}>
                          <label className="text-sm">Category</label>
                          <input className="input" value={q.category || ''} onChange={e => updateQ(q.id, 'category', e.target.value)} />
                        </div>
                      </div>
                    )}

                    {/* ── Question Text ── */}
                    {isEditing ? (
                      <div className="input-group mb-16">
                        <label className="text-sm">Question Text</label>
                        <textarea className="textarea" rows={3} value={q.question_text} onChange={e => updateQ(q.id, 'question_text', e.target.value)} />
                      </div>
                    ) : (
                      <div style={{ fontSize: '0.95rem', lineHeight: 1.7, marginBottom: 14, fontWeight: 500, color: 'var(--text-primary)' }}>
                        {parsedQuestion}
                      </div>
                    )}

                    {/* ── Options (MCQ) — parsed properly ── */}
                    {!isEditing && q.question_type === 'multiple_choice' && parsedOptions.length > 0 && (
                      <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginBottom: 14 }}>
                        {parsedOptions.map((opt, j) => {
                          const letter = String.fromCharCode(65 + j)
                          const isCorrect = letter === q.correct_answer || opt === q.correct_answer || opt === correctText
                          return (
                            <div key={j} style={{
                              display: 'flex', alignItems: 'center', gap: 12,
                              padding: '10px 16px', borderRadius: 10,
                              background: isCorrect ? 'rgba(34,197,94,0.08)' : 'var(--bg-hover)',
                              border: isCorrect ? '1.5px solid rgba(34,197,94,0.3)' : '1.5px solid transparent',
                              transition: 'all 0.15s'
                            }}>
                              <span style={{
                                width: 28, height: 28, borderRadius: 7, flexShrink: 0,
                                display: 'grid', placeItems: 'center',
                                fontSize: '0.75rem', fontWeight: 700,
                                fontFamily: 'var(--font-mono)',
                                background: isCorrect ? 'linear-gradient(135deg, #22c55e, #16a34a)' : 'var(--bg-primary)',
                                color: isCorrect ? '#fff' : 'var(--text-tertiary)',
                                border: isCorrect ? 'none' : '1px solid var(--border-subtle)'
                              }}>{letter}</span>
                              <span style={{
                                flex: 1, fontSize: '0.9rem',
                                fontWeight: isCorrect ? 600 : 400,
                                color: isCorrect ? '#16a34a' : 'var(--text-primary)'
                              }}>
                                {opt}
                              </span>
                              {isCorrect && <CheckCircle2 size={16} style={{ color: '#22c55e', flexShrink: 0 }} />}
                            </div>
                          )
                        })}
                      </div>
                    )}

                    {/* ── Editable Options ── */}
                    {isEditing && q.question_type === 'multiple_choice' && (
                      <div style={{ marginBottom: 12 }}>
                        <div className="flex-between" style={{ marginBottom: 8 }}>
                          <label className="text-sm" style={{ fontWeight: 600 }}>Options</label>
                          <button className="btn btn-ghost btn-sm" onClick={() => addOption(q.id)}><Plus size={12} /> Add</button>
                        </div>
                        {(q.options || []).map((opt, j) => (
                          <div key={j} className="flex gap-8" style={{ alignItems: 'center', marginBottom: 6 }}>
                            <span className="mono text-sm" style={{ width: 24, textAlign: 'center', fontWeight: 600, color: 'var(--text-tertiary)' }}>{String.fromCharCode(65 + j)}</span>
                            <input className="input" style={{ flex: 1 }} value={opt} onChange={e => updateOption(q.id, j, e.target.value)} />
                            <button className={`btn btn-sm ${opt === q.correct_answer ? 'btn-primary' : 'btn-ghost'}`}
                              onClick={() => updateQ(q.id, 'correct_answer', opt)} title="Set correct"><CheckCircle2 size={14} /></button>
                            <button className="btn btn-ghost btn-sm" style={{ color: 'var(--status-danger)' }}
                              onClick={() => removeOption(q.id, j)}><XCircle size={14} /></button>
                          </div>
                        ))}
                      </div>
                    )}

                    {/* ── Correct Answer (non-MCQ) ── */}
                    {!isEditing && q.correct_answer && q.question_type !== 'multiple_choice' && (
                      <div style={{ padding: '10px 14px', background: 'rgba(34,197,94,0.05)', borderRadius: 8, marginBottom: 12, fontSize: '0.88rem', border: '1px solid rgba(34,197,94,0.15)' }}>
                        <strong style={{ color: 'var(--status-success)', fontSize: '0.75rem', textTransform: 'uppercase', letterSpacing: '0.05em' }}>Answer:</strong>
                        <div style={{ marginTop: 4 }}>{q.correct_answer}</div>
                      </div>
                    )}
                    {isEditing && q.question_type !== 'multiple_choice' && (
                      <div className="input-group mb-16">
                        <label className="text-sm">Correct / Expected Answer</label>
                        <textarea className="textarea" rows={2} value={q.correct_answer || ''} onChange={e => updateQ(q.id, 'correct_answer', e.target.value)} />
                      </div>
                    )}

                    {/* ── Sample Answer ── */}
                    {isEditing ? (
                      <div className="input-group mb-16">
                        <label className="text-sm">Sample Answer / Explanation</label>
                        <textarea className="textarea" rows={2} value={q.sample_answer || ''} onChange={e => updateQ(q.id, 'sample_answer', e.target.value)} />
                      </div>
                    ) : q.sample_answer ? (
                      <details style={{ marginBottom: 8 }}>
                        <summary style={{ cursor: 'pointer', fontSize: '0.85rem', color: 'var(--accent-primary)', fontWeight: 500 }}>Sample Answer</summary>
                        <div style={{ marginTop: 6, padding: '10px 14px', background: 'var(--bg-hover)', borderRadius: 8, fontSize: '0.85rem', lineHeight: 1.6 }}>{q.sample_answer}</div>
                      </details>
                    ) : null}

                    {/* ── Code Template ── */}
                    {isEditing && q.question_type === 'coding' && (
                      <div className="input-group">
                        <label className="text-sm">Code Template</label>
                        <textarea className="textarea" rows={4} style={{ fontFamily: 'var(--font-mono)', fontSize: '0.8rem' }}
                          value={q.code_template || ''} onChange={e => updateQ(q.id, 'code_template', e.target.value)} />
                      </div>
                    )}
                    {!isEditing && q.code_template && (
                      <pre style={{ padding: 12, background: 'var(--bg-hover)', borderRadius: 8, fontSize: '0.8rem', fontFamily: 'var(--font-mono)', overflow: 'auto', marginBottom: 8 }}>{q.code_template}</pre>
                    )}

                    {/* ── Test Cases (coding) ── */}
                    {isEditing && q.question_type === 'coding' && (
                      <TestCasesEditor testCases={q.test_cases} onChange={v => updateQ(q.id, 'test_cases', v)} />
                    )}
                    {!isEditing && q.question_type === 'coding' && (
                      <TestCasesView testCases={q.test_cases} />
                    )}

                    {/* ═══ Accept / Replace Buttons ═══ */}
                    <div style={{
                      display: 'flex', gap: 10, marginTop: 18, paddingTop: 16,
                      borderTop: '1px solid var(--border-subtle)', alignItems: 'center'
                    }}>
                      {q.status === 'approved' ? (
                        <div style={{
                          display: 'flex', alignItems: 'center', gap: 8, padding: '9px 18px',
                          borderRadius: 8, background: 'rgba(34,197,94,0.08)', border: '1px solid rgba(34,197,94,0.2)',
                          color: '#22c55e', fontSize: '0.84rem', fontWeight: 700
                        }}>
                          <CheckCircle2 size={16} /> Accepted
                        </div>
                      ) : (
                        <button onClick={() => approveQuestion(q.id)} style={{
                          display: 'flex', alignItems: 'center', gap: 8,
                          padding: '9px 22px', borderRadius: 8, border: 'none',
                          background: 'linear-gradient(135deg, #22c55e, #16a34a)',
                          color: '#fff', fontSize: '0.84rem', fontWeight: 700,
                          cursor: 'pointer', transition: 'all 0.2s',
                          boxShadow: '0 2px 8px rgba(34,197,94,0.25)'
                        }}
                        onMouseEnter={e => e.currentTarget.style.transform = 'translateY(-1px)'}
                        onMouseLeave={e => e.currentTarget.style.transform = 'translateY(0)'}
                        ><CheckCircle2 size={16} /> Accept</button>
                      )}

                      <button onClick={() => replaceQuestion(q.id)} disabled={replacingQ === q.id} style={{
                        display: 'flex', alignItems: 'center', gap: 8,
                        padding: '9px 22px', borderRadius: 8,
                        border: '1.5px solid var(--border-medium)',
                        background: replacingQ === q.id ? 'var(--bg-hover)' : '#fff',
                        color: 'var(--accent-primary)', fontSize: '0.84rem', fontWeight: 700,
                        cursor: replacingQ === q.id ? 'wait' : 'pointer',
                        transition: 'all 0.2s', opacity: replacingQ === q.id ? 0.7 : 1
                      }}
                      onMouseEnter={e => { if (replacingQ !== q.id) e.currentTarget.style.borderColor = 'var(--accent-primary)' }}
                      onMouseLeave={e => e.currentTarget.style.borderColor = 'var(--border-medium)'}
                      >
                        {replacingQ === q.id
                          ? <><Loader2 size={16} className="spin" /> Replacing...</>
                          : <><RefreshCw size={16} /> Replace Question</>
                        }
                      </button>

                      <button onClick={() => deleteQuestion(q.id)} style={{
                        display: 'flex', alignItems: 'center', gap: 8,
                        padding: '9px 22px', borderRadius: 8,
                        border: '1.5px solid #fecaca', background: '#fff',
                        color: '#ef4444', fontSize: '0.84rem', fontWeight: 700,
                        cursor: 'pointer', transition: 'all 0.2s'
                      }}
                      onMouseEnter={e => { e.currentTarget.style.background = '#fef2f2'; e.currentTarget.style.borderColor = '#ef4444' }}
                      onMouseLeave={e => { e.currentTarget.style.background = '#fff'; e.currentTarget.style.borderColor = '#fecaca' }}
                      ><Trash2 size={16} /> Delete</button>

                      {q.status === 'approved' && (
                        <button onClick={() => rejectQuestion(q.id)} className="btn btn-ghost btn-sm" style={{ marginLeft: 'auto', color: 'var(--text-tertiary)', fontSize: '0.78rem' }}>
                          Undo Accept
                        </button>
                      )}
                    </div>

                    {/* ── AI metadata ── */}
                    {q.generation_model && (
                      <div style={{ fontFamily: 'var(--font-mono)', fontSize: '0.68rem', color: 'var(--text-tertiary)', marginTop: 10 }}>
                        Generated by {q.generation_model} {q.generation_temperature ? `· temp ${q.generation_temperature}` : ''}
                      </div>
                    )}
                  </div>
                )
              })}
            </>
          )}

          {!selInterview && questions.length === 0 && (
            <div className="card" style={{ padding: 60, textAlign: 'center', color: 'var(--text-tertiary)' }}>
              <Brain size={48} style={{ marginBottom: 12, opacity: 0.3 }} />
              <div>Select an interview above, configure options, then click Generate</div>
            </div>
          )}
          {selInterview && questions.length === 0 && !genLoading && (
            <div className="card" style={{ padding: 40, textAlign: 'center', color: 'var(--text-tertiary)' }}>No questions generated yet. Click "Generate Questions" above.</div>
          )}
        </div>
      )}

      {/* ═══════════ REQUEST CANDIDATES TAB ═══════════ */}
      {tab === 'request-candidates' && (
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 380px', gap: 20, alignItems: 'start' }}>
          {/* Request Form */}
          <div className="card">
            <h3 style={{ marginBottom: 20 }}>Request Candidates</h3>
            <p className="text-sm text-muted" style={{ marginBottom: 20 }}>
              Submit your hiring requirements to HR. They will source and shortlist candidates matching your criteria.
            </p>
            {/* ── Quick Fill bar ── */}
            <div style={{ display: 'flex', gap: 10, marginBottom: 18 }}>
              <button type="button"
                onClick={() => { setJdPanel(p => p === 'templates' ? null : 'templates'); setAiError('') }}
                style={{
                  display: 'flex', alignItems: 'center', gap: 7, padding: '8px 16px',
                  background: jdPanel === 'templates' ? 'var(--accent-primary)' : 'var(--surface-input)',
                  color: jdPanel === 'templates' ? '#fff' : 'var(--text-primary)',
                  border: `1px solid ${jdPanel === 'templates' ? 'var(--accent-primary)' : 'var(--border-subtle)'}`,
                  borderRadius: 8, cursor: 'pointer', fontSize: '0.85rem', fontWeight: 600,
                  transition: 'all 0.15s',
                }}>
                📋 Use Template
              </button>
              <button type="button"
                onClick={() => { setJdPanel(p => p === 'ai' ? null : 'ai'); setAiError('') }}
                style={{
                  display: 'flex', alignItems: 'center', gap: 7, padding: '8px 16px',
                  background: jdPanel === 'ai' ? '#7c3aed' : 'var(--surface-input)',
                  color: jdPanel === 'ai' ? '#fff' : 'var(--text-primary)',
                  border: `1px solid ${jdPanel === 'ai' ? '#7c3aed' : 'var(--border-subtle)'}`,
                  borderRadius: 8, cursor: 'pointer', fontSize: '0.85rem', fontWeight: 600,
                  transition: 'all 0.15s',
                }}>
                ✨ AI Generate
              </button>
              {jdFlash && (
                <span style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: '0.82rem', color: '#059669', fontWeight: 500, marginLeft: 4 }}>
                  <CheckCircle2 size={14} /> {jdFlash}
                </span>
              )}
            </div>

            {/* ── Templates panel ── */}
            {jdPanel === 'templates' && (
              <div style={{ border: '1px solid var(--border-subtle)', borderRadius: 10, padding: 16, marginBottom: 20, background: 'var(--surface-input)' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 14 }}>
                  <input
                    className="input" placeholder="Search templates…" value={templateSearch}
                    onChange={e => setTemplateSearch(e.target.value)}
                    style={{ flex: 1, padding: '7px 12px', fontSize: '0.85rem' }}
                  />
                  <span style={{ fontSize: '0.78rem', color: 'var(--text-tertiary)', whiteSpace: 'nowrap' }}>
                    {templates.filter(t => `${t.name} ${t.category || ''}`.toLowerCase().includes(templateSearch.toLowerCase())).length} templates
                    <span style={{ marginLeft: 6, color: 'var(--text-tertiary)' }}>· {crForm.job_category.replace('_', '-')}</span>
                  </span>
                </div>
                {templates.length === 0 && (
                  <div style={{ padding: '14px 4px', fontSize: '0.82rem', color: 'var(--text-tertiary)' }}>
                    No templates yet for this category. Generate one with AI or fill the form and click "Save as template".
                  </div>
                )}
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))', gap: 10, maxHeight: 320, overflowY: 'auto' }}>
                  {templates.filter(t =>
                    `${t.name} ${t.category || ''}`.toLowerCase().includes(templateSearch.toLowerCase())
                  ).map(t => (
                    <div key={t.id}
                      onClick={() => {
                        setCrForm(f => ({
                          ...f,
                          role_level: t.role_level || f.role_level,
                          employment_type: t.employment_type || f.employment_type,
                          min_experience_years: t.min_experience_years ?? f.min_experience_years,
                          required_skills: (t.required_skills || []).join(', '),
                          preferred_skills: (t.preferred_skills || []).join(', '),
                          job_description: t.job_description || f.job_description,
                          job_category: t.job_category || f.job_category,
                        }))
                        setJdPanel(null)
                        setTemplateSearch('')
                        setJdFlash(`"${t.name}" template applied`)
                        setTimeout(() => setJdFlash(''), 3000)
                      }}
                      style={{
                        position: 'relative',
                        padding: '10px 12px', border: '1px solid var(--border-subtle)', borderRadius: 8,
                        cursor: 'pointer', background: '#fff', transition: 'all 0.15s',
                      }}
                      onMouseEnter={e => { e.currentTarget.style.borderColor = 'var(--accent-primary)'; e.currentTarget.style.boxShadow = '0 0 0 2px var(--accent-glow)' }}
                      onMouseLeave={e => { e.currentTarget.style.borderColor = 'var(--border-subtle)'; e.currentTarget.style.boxShadow = 'none' }}
                    >
                      {!t.is_builtin && (
                        <button type="button"
                          onClick={(ev) => { ev.stopPropagation(); deleteTemplate(t) }}
                          title="Delete template"
                          style={{ position: 'absolute', top: 6, right: 6, background: 'transparent', border: 'none', color: '#9ca3af', cursor: 'pointer', fontSize: '0.85rem' }}>
                          ✕
                        </button>
                      )}
                      <div style={{ fontWeight: 600, fontSize: '0.87rem', marginBottom: 4 }}>{t.name}</div>
                      <div style={{ display: 'flex', gap: 6, marginBottom: 6, flexWrap: 'wrap' }}>
                        {t.category && <span style={{ fontSize: '0.72rem', background: '#e0f2fe', color: '#0369a1', borderRadius: 4, padding: '1px 7px', fontWeight: 600 }}>{t.category}</span>}
                        {t.role_level && <span style={{ fontSize: '0.72rem', background: '#f0fdf4', color: '#15803d', borderRadius: 4, padding: '1px 7px', fontWeight: 600 }}>{t.role_level}</span>}
                        {t.source === 'ai' && <span style={{ fontSize: '0.72rem', background: '#f3e8ff', color: '#7c3aed', borderRadius: 4, padding: '1px 7px', fontWeight: 600 }}>AI</span>}
                        {t.is_builtin && <span style={{ fontSize: '0.72rem', background: '#fef3c7', color: '#92400e', borderRadius: 4, padding: '1px 7px', fontWeight: 600 }}>Built-in</span>}
                      </div>
                      <div style={{ fontSize: '0.75rem', color: 'var(--text-tertiary)', overflow: 'hidden', display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical' }}>
                        {(t.required_skills || []).join(', ')}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* ── AI Generate panel ── */}
            {jdPanel === 'ai' && (
              <div style={{ border: '1px solid #ede9fe', borderRadius: 10, padding: 16, marginBottom: 20, background: '#faf5ff' }}>
                <div style={{ fontWeight: 600, fontSize: '0.88rem', marginBottom: 14, color: '#7c3aed', display: 'flex', alignItems: 'center', gap: 8 }}>
                  ✨ Generate Job Description with AI
                </div>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10, marginBottom: 12 }}>
                  <div className="input-group" style={{ marginBottom: 0 }}>
                    <label style={{ fontSize: '0.8rem' }}>Job Title *</label>
                    <input className="input" placeholder="e.g. Senior Python Developer"
                      value={aiForm.job_title} onChange={e => setAiForm(f => ({ ...f, job_title: e.target.value }))}
                      style={{ fontSize: '0.85rem' }} />
                  </div>
                  <div className="input-group" style={{ marginBottom: 0 }}>
                    <label style={{ fontSize: '0.8rem' }}>Experience Level</label>
                    <select className="select" value={aiForm.experience_level}
                      onChange={e => setAiForm(f => ({ ...f, experience_level: e.target.value }))}
                      style={{ fontSize: '0.85rem' }}>
                      {['Junior', 'Mid-level', 'Senior', 'Lead'].map(l => <option key={l}>{l}</option>)}
                    </select>
                  </div>
                  <div className="input-group" style={{ marginBottom: 0 }}>
                    <label style={{ fontSize: '0.8rem' }}>Key Skills <span style={{ color: 'var(--text-tertiary)' }}>(optional)</span></label>
                    <input className="input" placeholder="e.g. FastAPI, PostgreSQL, Docker"
                      value={aiForm.key_skills} onChange={e => setAiForm(f => ({ ...f, key_skills: e.target.value }))}
                      style={{ fontSize: '0.85rem' }} />
                  </div>
                  <div className="input-group" style={{ marginBottom: 0 }}>
                    <label style={{ fontSize: '0.8rem' }}>Focus Areas <span style={{ color: 'var(--text-tertiary)' }}>(optional)</span></label>
                    <input className="input" placeholder="e.g. microservices, fintech"
                      value={aiForm.focus_areas} onChange={e => setAiForm(f => ({ ...f, focus_areas: e.target.value }))}
                      style={{ fontSize: '0.85rem' }} />
                  </div>
                </div>
                {aiError && (
                  <div style={{ fontSize: '0.82rem', color: '#dc2626', background: '#fef2f2', border: '1px solid #fecaca', borderRadius: 6, padding: '7px 12px', marginBottom: 10 }}>
                    {aiError}
                  </div>
                )}
                <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
                  <button type="button" disabled={!aiForm.job_title.trim() || aiGenerating}
                    onClick={async () => {
                      setAiGenerating(true)
                      setAiError('')
                      try {
                        const res = await api.post('/interviewer/generate-jd', aiForm)
                        const d = res.data
                        setAiResult(d)
                        setCrForm(f => ({
                          ...f,
                          job_title: f.job_title || aiForm.job_title,
                          job_description: d.job_description || f.job_description,
                          required_skills: d.required_skills?.join(', ') || f.required_skills,
                          preferred_skills: d.preferred_skills?.join(', ') || f.preferred_skills,
                          min_experience_years: d.min_experience_years ?? f.min_experience_years,
                          role_level: d.role_level || f.role_level,
                        }))
                        setJdPanel(null)
                        setJdFlash('AI-generated JD applied — review and edit below. You can save it as a template.')
                        setTimeout(() => setJdFlash(''), 4500)
                      } catch (err) {
                        setAiError(err.response?.data?.detail || 'AI generation failed. Make sure the Gemini API key is configured.')
                      } finally {
                        setAiGenerating(false)
                      }
                    }}
                    style={{
                      display: 'flex', alignItems: 'center', gap: 8, padding: '8px 20px',
                      background: aiGenerating ? '#a78bfa' : '#7c3aed', color: '#fff',
                      border: 'none', borderRadius: 8, cursor: aiGenerating ? 'not-allowed' : 'pointer',
                      fontSize: '0.85rem', fontWeight: 600,
                    }}>
                    {aiGenerating ? <><Loader2 size={15} style={{ animation: 'spin 1s linear infinite' }} /> Generating…</> : <>✨ Generate JD</>}
                  </button>
                  {aiError && (
                    <button type="button"
                      onClick={() => setAiError('')}
                      style={{ background: 'transparent', border: '1px solid #fecaca', color: '#dc2626', borderRadius: 6, padding: '6px 12px', fontSize: '0.78rem', cursor: 'pointer' }}>
                      Dismiss
                    </button>
                  )}
                </div>
              </div>
            )}

            {/* AI result actions — visible once an AI response has been applied to the form */}
            {aiResult && (
              <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 18, padding: '8px 12px', background: '#faf5ff', border: '1px solid #ede9fe', borderRadius: 8 }}>
                <span style={{ fontSize: '0.82rem', color: '#7c3aed', fontWeight: 600 }}>AI-generated JD applied.</span>
                <button type="button" className="btn btn-sm" onClick={() => openSaveTemplate('ai')}
                  style={{ background: '#7c3aed', color: '#fff', borderColor: '#7c3aed' }}>
                  Save as template
                </button>
                <button type="button" className="btn btn-sm btn-ghost" onClick={() => setAiResult(null)}>
                  Dismiss
                </button>
              </div>
            )}

            <form className="auth-form" onSubmit={handleCrSubmit}>
              <div className="input-group">
                <label>Role type *</label>
                <div style={{ display: 'flex', gap: 8 }}>
                  {[
                    { val: 'technical', label: 'Technical', color: '#2563eb' },
                    { val: 'non_technical', label: 'Non-Technical', color: '#059669' },
                    { val: 'mixed', label: 'Mixed', color: '#7c3aed' },
                  ].map(c => (
                    <button key={c.val} type="button"
                      className={`btn btn-sm ${crForm.job_category === c.val ? '' : 'btn-ghost'}`}
                      style={crForm.job_category === c.val ? { background: c.color, color: '#fff', borderColor: c.color } : {}}
                      onClick={() => setCrForm({ ...crForm, job_category: c.val })}>
                      {c.label}
                    </button>
                  ))}
                </div>
                <span className="text-xs text-muted">Affects which templates appear and which question types HR/interviewers can generate later.</span>
              </div>

              <div className="grid-2">
                <div className="input-group">
                  <label>Job Title / Role *</label>
                  <input className="input" required placeholder="e.g. Senior React Developer"
                    value={crForm.job_title} onChange={e => setCrForm({ ...crForm, job_title: e.target.value })} />
                </div>
                <div className="input-group">
                  <label>Department</label>
                  <input className="input" placeholder="e.g. Engineering, Marketing"
                    value={crForm.department} onChange={e => setCrForm({ ...crForm, department: e.target.value })} />
                </div>
              </div>

              <div className="grid-2">
                <div className="input-group">
                  <label>Role Level</label>
                  <select className="select" value={crForm.role_level} onChange={e => setCrForm({ ...crForm, role_level: e.target.value })}>
                    {['Junior', 'Mid-level', 'Senior', 'Lead', 'Principal', 'Manager', 'Director'].map(l => (
                      <option key={l} value={l}>{l}</option>
                    ))}
                  </select>
                </div>
                <div className="input-group">
                  <label>Employment Type</label>
                  <select className="select" value={crForm.employment_type} onChange={e => setCrForm({ ...crForm, employment_type: e.target.value })}>
                    {['Full-time', 'Part-time', 'Contract', 'Intern', 'Freelance'].map(t => (
                      <option key={t} value={t}>{t}</option>
                    ))}
                  </select>
                </div>
              </div>

              <div className="grid-2">
                <div className="input-group">
                  <label>Number of Candidates Needed</label>
                  <input className="input" type="number" min={1} max={50} value={crForm.num_candidates}
                    onChange={e => setCrForm({ ...crForm, num_candidates: parseInt(e.target.value) || 1 })} />
                </div>
                <div className="input-group">
                  <label>Urgency</label>
                  <div style={{ display: 'flex', gap: 8 }}>
                    {[
                      { val: 'low', label: 'Low', color: '#6b7280' },
                      { val: 'normal', label: 'Normal', color: '#2563eb' },
                      { val: 'high', label: 'High', color: '#d97706' },
                      { val: 'critical', label: 'Critical', color: '#dc2626' },
                    ].map(u => (
                      <button key={u.val} type="button"
                        className={`btn btn-sm ${crForm.urgency === u.val ? '' : 'btn-ghost'}`}
                        style={crForm.urgency === u.val ? { background: u.color, color: '#fff', borderColor: u.color } : {}}
                        onClick={() => setCrForm({ ...crForm, urgency: u.val })}>
                        {u.label}
                      </button>
                    ))}
                  </div>
                </div>
              </div>

              <div className="input-group">
                <label>Required Skills *</label>
                <input className="input" placeholder="React, Node.js, TypeScript, PostgreSQL (comma-separated)"
                  value={crForm.required_skills} onChange={e => setCrForm({ ...crForm, required_skills: e.target.value })} />
                <span className="text-xs text-muted">Must-have skills for the role</span>
              </div>

              <div className="input-group">
                <label>Preferred / Nice-to-have Skills</label>
                <input className="input" placeholder="AWS, Docker, GraphQL (comma-separated)"
                  value={crForm.preferred_skills} onChange={e => setCrForm({ ...crForm, preferred_skills: e.target.value })} />
              </div>

              <div className="grid-2">
                <div className="input-group">
                  <label>Min Experience (years)</label>
                  <input className="input" type="number" min={0} max={30} value={crForm.min_experience_years}
                    onChange={e => setCrForm({ ...crForm, min_experience_years: parseInt(e.target.value) || 0 })} />
                </div>
                <div className="input-group">
                  <label>Max Experience (years)</label>
                  <input className="input" type="number" min={0} max={30} placeholder="No limit"
                    value={crForm.max_experience_years} onChange={e => setCrForm({ ...crForm, max_experience_years: e.target.value })} />
                </div>
              </div>

              <div className="input-group">
                <label>Education Requirement</label>
                <select className="select" value={crForm.education_requirement} onChange={e => setCrForm({ ...crForm, education_requirement: e.target.value })}>
                  <option value="">Any / Not specified</option>
                  <option value="High School">High School</option>
                  <option value="Bachelor's">Bachelor's Degree</option>
                  <option value="Master's">Master's Degree</option>
                  <option value="PhD">PhD</option>
                  <option value="Bootcamp / Certification">Bootcamp / Certification</option>
                </select>
              </div>

              <div className="input-group">
                <label>Job Description / Responsibilities</label>
                <textarea className="textarea" rows={4} placeholder="Describe the key responsibilities, day-to-day tasks, and what the role involves..."
                  value={crForm.job_description} onChange={e => setCrForm({ ...crForm, job_description: e.target.value })} />
              </div>

              <div className="input-group">
                <label>Additional Notes for HR</label>
                <textarea className="textarea" rows={2} placeholder="Any specific preferences, team dynamics, or context HR should know..."
                  value={crForm.notes} onChange={e => setCrForm({ ...crForm, notes: e.target.value })} />
              </div>

              <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 10, marginTop: 6 }}>
                <button type="button" className="btn btn-ghost"
                  disabled={!crForm.job_title.trim() || !crForm.required_skills.trim()}
                  onClick={() => openSaveTemplate('manual')}
                  title="Save the current form as a reusable template">
                  Save as template
                </button>
                <button type="submit" className="btn btn-primary" disabled={crSubmitting}>
                  <Plus size={16} /> {crSubmitting ? 'Submitting…' : 'Submit Request'}
                </button>
              </div>
            </form>
          </div>

          {/* ── Save Template modal ── */}
          {saveTplOpen && (
            <div className="modal-overlay" onClick={() => setSaveTplOpen(false)}>
              <div className="modal" onClick={e => e.stopPropagation()} style={{ maxWidth: 420 }}>
                <h3 style={{ marginBottom: 12 }}>Save as template</h3>
                <p className="text-sm text-muted" style={{ marginBottom: 14 }}>
                  Saves the current form's role, skills, and description as a reusable template for your team.
                </p>
                <div className="input-group">
                  <label>Template name *</label>
                  <input className="input" autoFocus
                    placeholder="e.g. Senior React Developer (Fintech)"
                    value={saveTplName}
                    onChange={e => setSaveTplName(e.target.value)}
                    onKeyDown={e => { if (e.key === 'Enter') submitSaveTemplate() }} />
                </div>
                <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 6 }}>
                  <button type="button" className="btn btn-ghost" onClick={() => setSaveTplOpen(false)}>Cancel</button>
                  <button type="button" className="btn btn-primary" disabled={!saveTplName.trim()} onClick={submitSaveTemplate}>
                    Save template
                  </button>
                </div>
              </div>
            </div>
          )}

          {/* Previous Requests */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            <div className="card" style={{ padding: '14px 18px' }}>
              <h4 style={{ margin: '0 0 4px', fontSize: '0.92rem' }}>My Requests</h4>
              <p className="text-xs text-muted" style={{ margin: 0 }}>{crRequests.length} total</p>
            </div>
            {crRequests.length === 0 && (
              <div className="card" style={{ padding: 24, textAlign: 'center', color: 'var(--text-tertiary)', fontSize: '0.85rem' }}>
                No requests submitted yet.
              </div>
            )}
            {crRequests.map(r => (
              <div key={r.id} className="card" style={{ padding: '14px 18px', borderLeft: `4px solid ${
                r.status === 'approved' ? '#16a34a' : r.status === 'rejected' ? '#dc2626' : r.status === 'fulfilled' ? '#2563eb' : '#d97706'
              }` }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'start', marginBottom: 6 }}>
                  <div>
                    <div style={{ fontSize: '0.9rem', fontWeight: 700 }}>{r.job_title}</div>
                    <div className="text-xs text-muted">{r.department || 'No dept'} · {r.role_level} · {r.employment_type}</div>
                  </div>
                  <span className={`badge ${
                    r.status === 'approved' ? 'badge-green' : r.status === 'rejected' ? 'badge-red' : r.status === 'fulfilled' ? 'badge-blue' : 'badge-neutral'
                  }`} style={{ fontSize: '0.65rem', textTransform: 'uppercase' }}>
                    {r.status}
                  </span>
                </div>
                <div className="text-xs" style={{ color: 'var(--text-secondary)', marginBottom: 4 }}>
                  {r.required_skills?.length > 0 && <span>Skills: {r.required_skills.join(', ')}</span>}
                </div>
                <div className="text-xs text-muted">
                  {r.num_candidates} candidate{r.num_candidates > 1 ? 's' : ''} · {r.min_experience_years}–{r.max_experience_years || '∞'} yrs · {
                    { low: '🟢 Low', normal: '🔵 Normal', high: '🟠 High', critical: '🔴 Critical' }[r.urgency] || r.urgency
                  }
                </div>
                {r.hr_notes && (
                  <div style={{ marginTop: 8, padding: '6px 10px', background: 'var(--bg-secondary)', borderRadius: 6, fontSize: '0.78rem', color: 'var(--text-secondary)' }}>
                    <strong>HR:</strong> {r.hr_notes}
                  </div>
                )}
                <div className="text-xs text-muted" style={{ marginTop: 6 }}>
                  {new Date(r.created_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* ═══════════ EDIT EXAM / QUESTIONS TAB ═══════════ */}
      {tab === 'feedback-history' && (() => {
        const n = history.length
        const COMPETENCIES = [
          ['technical_score', 'Technical'],
          ['communication_score', 'Communication'],
          ['problem_solving_score', 'Problem solving'],
          ['cultural_fit_score', 'Cultural fit'],
        ]
        const clamp = (v) => Math.max(0, Math.min(100, Number(v)))
        const statsFor = (key) => {
          const raw = history
            .filter(h => h[key] !== null && h[key] !== undefined)
            .map(h => ({ v: clamp(h[key]), name: h.candidate_name, job: h.job_title }))
          if (!raw.length) return null
          const v = raw.map(p => p.v)
          const min = Math.min(...v), max = Math.max(...v)

          // Ties superimpose exactly — three candidates who all scored 72 would render
          // as one dot, making a clustered scorer look more spread than they are, which
          // is backwards from what this page is for. Give each member of a tie group a
          // vertical tier: 0, +1, -1, +2, -2, so the group grows symmetrically about the
          // baseline. Derived from position in the group, not random, so it is stable
          // across renders. Past five ties the tiers clamp and overlap resumes; with a
          // handful of interviews that is not reachable.
          const seen = new Map()
          const points = raw.map(p => {
            const bucket = Math.round(p.v)
            const i = seen.get(bucket) ?? 0
            seen.set(bucket, i + 1)
            const tier = i === 0 ? 0 : (i % 2 ? Math.ceil(i / 2) : -i / 2)
            return { ...p, tier: Math.max(-2, Math.min(2, tier)) }
          })

          return { points, mean: v.reduce((s, x) => s + x, 0) / v.length, min, max, spread: max - min }
        }
        const rows = COMPETENCIES
          .map(([key, label]) => ({ key, label, s: statsFor(key) }))
          .filter(r => r.s)
        const overall = statsFor('overall_score')

        const WORDS = ['No', 'One', 'Two', 'Three', 'Four', 'Five', 'Six', 'Seven',
                       'Eight', 'Nine', 'Ten', 'Eleven', 'Twelve']
        const countWord = (c) => WORDS[c] ?? String(c)
        const fmtDate = (d) => d ? new Date(d).toLocaleDateString(undefined,
          { day: 'numeric', month: 'short', year: 'numeric' }) : '—'

        // One plain sentence naming what the distribution means. A mean cannot answer
        // "am I calibrated?" — the spread can, so the spread is what gets spoken.
        const verdict = (() => {
          if (!rows.length) return null
          const tightest = rows.reduce((a, b) => (a.s.spread <= b.s.spread ? a : b))
          if (tightest.s.spread < 15) {
            return <>Your <strong>{tightest.label.toLowerCase()}</strong> scores sit within <em>{Math.round(tightest.s.spread)}</em> points
              of each other. That is not a scale you are using.</>
          }
          const anyAgainst = history.some(h => HIRE_REC[h.hire_recommendation]?.tone === 'neg')
          if (!anyAgainst && n >= 3) {
            return <>You have never recommended against a hire, across <em>{n}</em> interviews.
              Worth knowing before your next debrief.</>
          }
          const widest = rows.reduce((a, b) => (a.s.spread >= b.s.spread ? a : b))
          return <>You use most of the scale. Your <strong>{widest.label.toLowerCase()}</strong> scores
            run <em>{Math.round(widest.s.min)}</em> to <em>{Math.round(widest.s.max)}</em>.</>
        })()

        // Every observation as a dot on the shared 0–100 axis, the mean as a rule, the
        // min–max as a band behind. Dots start stacked at the mean and settle outward.
        //
        // Each dot names itself on hover via a CSS-only tooltip — a distribution that
        // cannot answer "who is the 45?" only tells half the story. The marks stay
        // aria-hidden and are deliberately NOT focusable: making 44 dots tab stops would
        // wreck keyboard navigation, and the interview list below is this chart's table
        // view, already reachable and carrying the same numbers.
        const Rail = ({ s }) => (
          <div className="fh__plot" aria-hidden="true">
            <div className="fh__band" style={{ left: `${s.min}%`, width: `${Math.max(s.spread, 0.8)}%` }} />
            {s.points.map((p, i) => (
              <span key={i} className="fh__dot"
                    data-tip={`${p.name} · ${Math.round(p.v)}${p.job ? ` · ${p.job}` : ''}`}
                    style={{ '--to': `${p.v}%`, '--from': `${s.mean}%`,
                             '--tier': p.tier, '--delay': `${i * 45}ms` }} />
            ))}
            <div className="fh__mean" style={{ left: `${s.mean}%` }} />
          </div>
        )

        return (
          <div className="fh">
            <div>
              <h2 className="fh__title">Your scoring record</h2>
              <p className="fh__sub">
                {countWord(n)} interview{n === 1 ? '' : 's'} scored. This is how you rate people —
                every score you have given, on one scale.
              </p>
            </div>

            {historyError && <div className="fh__error">{historyError}</div>}

            {historyLoading ? (
              <div className="fh__empty">Loading your record…</div>
            ) : n === 0 && !historyError ? (
              <div className="fh__empty">
                You haven’t scored an interview yet. Once you do, this page shows how your
                scoring holds up across candidates.
              </div>
            ) : (
              <>
                {rows.length > 0 && (
                  <div>
                    <div className="fh__scale">
                      <span />
                      <span className="fh__scale-track"><span>0</span><span>50</span><span>100</span></span>
                      <span style={{ textAlign: 'right' }}>MEAN</span>
                    </div>
                    {rows.map(r => (
                      <div className="fh__row" key={r.key}>
                        <span className="fh__label">{r.label}</span>
                        <Rail s={r.s} />
                        <span className="fh__val">{r.s.mean.toFixed(0)}</span>
                      </div>
                    ))}
                  </div>
                )}

                {verdict && <p className="fh__verdict">{verdict}</p>}

                <div>
                  <div className="fh__section-head">
                    Every interview, newest first
                  </div>
                  {history.map(h => {
                    const open = historyOpen === h.interview_id
                    const rec = HIRE_REC[h.hire_recommendation]
                      || { label: (h.hire_recommendation || 'Not recorded').replace(/_/g, ' '), tone: 'mid' }
                    const score = h.overall_score === null || h.overall_score === undefined
                      ? null : clamp(h.overall_score)
                    return (
                      <div className="fh__entry" key={h.interview_id}>
                        <button className="fh__entry-btn" aria-expanded={open}
                                onClick={() => setHistoryOpen(open ? null : h.interview_id)}>
                          <span className="fh__who">
                            {h.candidate_name}
                            <span>{h.job_title || 'Job removed'}</span>
                          </span>
                          {score === null ? <span /> : (
                            <div className="fh__plot" aria-hidden="true">
                              {overall && <div className="fh__mean" style={{ left: `${overall.mean}%`, opacity: 0.3 }} />}
                              <span className="fh__dot" style={{ '--to': `${score}%`, '--from': `${score}%` }} />
                            </div>
                          )}
                          <span className="fh__val">{score === null ? '—' : score.toFixed(0)}</span>
                          <span className={`fh__rec fh__rec--${rec.tone}`}>{rec.label}</span>
                        </button>

                        {open && (
                          <div className="fh__detail">
                            <div className="fh__scores">
                              {COMPETENCIES.map(([key, label]) => (
                                <div key={key}>
                                  <div className="fh__score-k">{label}</div>
                                  <div className="fh__score-v">
                                    {h[key] === null || h[key] === undefined ? '—' : Number(h[key]).toFixed(0)}
                                  </div>
                                </div>
                              ))}
                              <div>
                                <div className="fh__score-k">Interviewed</div>
                                <div className="fh__score-v" style={{ fontSize: '0.95rem' }}>{fmtDate(h.interview_date)}</div>
                              </div>
                              <div>
                                <div className="fh__score-k">Scored</div>
                                <div className="fh__score-v" style={{ fontSize: '0.95rem' }}>{fmtDate(h.feedback_date)}</div>
                              </div>
                            </div>
                            {[['Strengths', h.strengths], ['Areas to improve', h.weaknesses],
                              ['Recommendations', h.recommendations], ['Notes', h.additional_notes]]
                              .filter(([, v]) => v && String(v).trim())
                              .map(([label, v]) => (
                                <div key={label}>
                                  <div className="fh__prose-k">{label}</div>
                                  <div className="fh__prose-v">{Array.isArray(v) ? v.join(', ') : String(v)}</div>
                                </div>
                              ))}
                          </div>
                        )}
                      </div>
                    )
                  })}
                </div>
              </>
            )}
          </div>
        )
      })()}

      {tab === 'edit-exam' && (
        <div>
          {/* Section switcher */}
          <div className="flex gap-8 mb-24" style={{ flexWrap: 'wrap' }}>
            {[
              { key: 'exams', label: 'Edit Exam' },
              { key: 'drafts', label: 'Exam Drafts' },
              { key: 'questions', label: 'Edit Questions' },
            ].map(s => (
              <button
                key={s.key}
                className={`btn btn-sm ${eeSection === s.key ? 'btn-primary' : 'btn-secondary'}`}
                onClick={() => { setEeSection(s.key); setEeOpenTest(null) }}
              >
                {s.label}
              </button>
            ))}
          </div>

          {eeMsg && (
            <div className="card mb-16" style={{ padding: '10px 16px', fontSize: '0.85rem' }}>{eeMsg}</div>
          )}
          {eeError && (
            <div className="card mb-16" style={{ padding: '10px 16px', fontSize: '0.85rem', color: '#ef4444' }}>
              {eeError}
            </div>
          )}

          {/* ───────── Section 1: Edit Exam ───────── */}
          {eeSection === 'exams' && (
            <div className="card">
              <h3 style={{ marginBottom: 16 }}>Created Tests</h3>
              {eeLoading ? (
                <div style={{ padding: 30, textAlign: 'center', color: 'var(--text-tertiary)' }}>Loading…</div>
              ) : eeTests.length === 0 ? (
                <div style={{ padding: 30, textAlign: 'center', color: 'var(--text-tertiary)' }}>
                  No tests yet. Create one from the Create Test tab.
                </div>
              ) : eeTests.map(t => {
                const open = eeOpenTest === t.id
                const cfg = t.interview_configuration || {}
                const sec = t.security_settings || {}
                return (
                  <div key={t.id} className="card mb-8" style={{ padding: 14 }}>
                    <div className="flex-between" style={{ alignItems: 'center', flexWrap: 'wrap', gap: 8 }}>
                      <div>
                        <div style={{ fontWeight: 600 }}>{t.title || t.job_position || `Test #${t.id}`}</div>
                        <div className="text-sm" style={{ color: 'var(--text-tertiary)' }}>
                          <span className={`badge ${t.status === 'READY' ? 'badge-green' : 'badge-amber'}`}>{t.status}</span>
                          {' '}· {cfg.duration_minutes} min · {t.question_count} questions
                          {' '}· Camera {sec.enable_webcam_monitoring ? 'on' : 'off'}
                          {' '}· Tab-switch {sec.enable_tab_switch_detection ? 'on' : 'off'}
                          {' '}· Copy/paste {sec.copy_paste_allowed ? 'allowed' : 'blocked'}
                        </div>
                      </div>
                      <button className="btn btn-secondary btn-sm" onClick={() => openEeTestEditor(t)}>
                        <Edit3 size={14} /> {open ? 'Cancel' : 'Edit'}
                      </button>
                    </div>

                    {open && (
                      <div style={{ marginTop: 14, borderTop: '1px solid var(--border-subtle)', paddingTop: 14 }}>
                        <div className="grid-2">
                          <div className="input-group">
                            <label>Time per question (minutes)</label>
                            {eeTypesInTest(t).length === 0 ? (
                              <div className="text-sm" style={{ color: 'var(--text-tertiary)' }}>
                                No skills configured — duration can't be computed for this test.
                              </div>
                            ) : eeTypesInTest(t).map(ty => (
                              <div key={ty} style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
                                <span className="text-sm" style={{ flex: 1, textTransform: 'capitalize' }}>
                                  {ty.replace(/_/g, ' ')}
                                </span>
                                <input className="input" type="number" min={1} style={{ width: 90 }}
                                  value={eeDraftVals.time_per_question_type?.[ty] ?? TYPE_TIME_DEFAULTS[ty] ?? 5}
                                  onChange={e => setEeDraftVals(v => ({
                                    ...v,
                                    time_per_question_type: {
                                      ...v.time_per_question_type,
                                      [ty]: parseInt(e.target.value) || 1,
                                    },
                                  }))} />
                              </div>
                            ))}
                            <div className="text-sm" style={{ marginTop: 8, fontWeight: 600 }}>
                              Total duration: {eeComputedDuration(t, eeDraftVals.time_per_question_type)} min
                              <span style={{ fontWeight: 400, color: 'var(--text-tertiary)' }}>
                                {' '}(was {t.interview_configuration?.duration_minutes} min)
                              </span>
                            </div>
                          </div>
                          <div className="input-group" style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                            <label style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                              <input type="checkbox" checked={eeDraftVals.enable_webcam_monitoring}
                                onChange={e => setEeDraftVals(v => ({ ...v, enable_webcam_monitoring: e.target.checked }))} />
                              Webcam Monitoring
                            </label>
                            <label style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                              <input type="checkbox" checked={eeDraftVals.enable_tab_switch_detection}
                                onChange={e => setEeDraftVals(v => ({ ...v, enable_tab_switch_detection: e.target.checked }))} />
                              Tab Switch Detection
                            </label>
                            <label style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                              <input type="checkbox" checked={eeDraftVals.copy_paste_allowed}
                                onChange={e => setEeDraftVals(v => ({ ...v, copy_paste_allowed: e.target.checked }))} />
                              Allow Copy/Paste
                            </label>
                          </div>
                        </div>
                        <div className="text-sm" style={{ color: 'var(--text-tertiary)', marginBottom: 10 }}>
                          Changes also apply to candidates already invited who have not started yet.
                        </div>
                        <button className="btn btn-primary btn-sm" disabled={eeSaving}
                          onClick={() => saveEeTestSettings(t.id)}>
                          {eeSaving ? 'Saving…' : 'Save Settings'}
                        </button>
                      </div>
                    )}
                  </div>
                )
              })}
            </div>
          )}

          {/* ───────── Section 2: Exam Drafts ───────── */}
          {eeSection === 'drafts' && (
            <div className="card">
              <h3 style={{ marginBottom: 16 }}>Unfinished Drafts</h3>
              {eeLoading ? (
                <div style={{ padding: 30, textAlign: 'center', color: 'var(--text-tertiary)' }}>Loading…</div>
              ) : eeDrafts.length === 0 ? (
                <div style={{ padding: 30, textAlign: 'center', color: 'var(--text-tertiary)' }}>
                  No unfinished drafts.
                </div>
              ) : (
                <div className="table-wrapper">
                  <table>
                    <thead><tr><th>Title</th><th>Type</th><th>Questions</th><th>Last edited</th><th>Action</th></tr></thead>
                    <tbody>
                      {eeDrafts.map(d => (
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
                            <button className="btn btn-ghost btn-sm" onClick={() => loadDraftIntoWizard(d)}>
                              <Edit3 size={14} /> Continue
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          )}

          {/* ───────── Section 3: Edit Questions ───────── */}
          {eeSection === 'questions' && (
            <div className="card">
              <h3 style={{ marginBottom: 12 }}>Question Bank by Skill</h3>
              <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 14 }}>
                <input
                  className="input" placeholder="Search skills…" value={eeSkillSearch}
                  onChange={e => setEeSkillSearch(e.target.value)}
                  style={{ flex: 1, padding: '7px 12px', fontSize: '0.85rem' }}
                />
                <span style={{ fontSize: '0.78rem', color: 'var(--text-tertiary)', whiteSpace: 'nowrap' }}>
                  {eeSkills.filter(s => s.skill_name.toLowerCase().includes(eeSkillSearch.toLowerCase())).length} skills
                </span>
              </div>
              {eeLoading ? (
                <div style={{ padding: 30, textAlign: 'center', color: 'var(--text-tertiary)' }}>Loading…</div>
              ) : eeSkills.length === 0 ? (
                <div style={{ padding: 30, textAlign: 'center', color: 'var(--text-tertiary)' }}>
                  No questions in the bank yet.
                </div>
              ) : (
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))', gap: 10 }}>
                  {eeSkills
                    .filter(s => s.skill_name.toLowerCase().includes(eeSkillSearch.toLowerCase()))
                    .map(s => (
                      <div key={s.skill_name} className="card" style={{ padding: 14, cursor: 'pointer' }}
                        onClick={() => openEeSkill(s)}>
                        <div style={{ fontWeight: 600 }}>{s.skill_name}</div>
                        <div className="text-sm" style={{ color: 'var(--text-tertiary)' }}>
                          {s.question_count} question{s.question_count === 1 ? '' : 's'}
                        </div>
                      </div>
                    ))}
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {/* ═══════════ EDIT QUESTIONS POPUP ═══════════ */}
      {eeSkillPopup && (
        <div className="modal-overlay" onClick={() => setEeSkillPopup(null)}>
          <div style={{
            position: 'fixed', top: '3%', left: '5%', right: '5%', bottom: '3%',
            background: 'var(--bg-primary)', borderRadius: 20, padding: 0,
            display: 'flex', flexDirection: 'column', zIndex: 1001,
            boxShadow: '0 25px 50px rgba(0,0,0,0.25)'
          }} onClick={e => e.stopPropagation()}>
            <div style={{
              padding: '20px 28px', borderBottom: '1px solid var(--border-subtle)',
              display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexShrink: 0
            }}>
              <div>
                <h3 style={{ margin: 0 }}>{eeSkillPopup.skill_name}</h3>
                <div className="text-sm" style={{ color: 'var(--text-tertiary)' }}>
                  {eeQuestions.length} question{eeQuestions.length === 1 ? '' : 's'} in the bank
                </div>
              </div>
              <button className="btn btn-secondary btn-sm" onClick={() => setEeSkillPopup(null)}>Close</button>
            </div>

            {eeQMsg && (
              <div style={{ padding: '10px 28px', fontSize: '0.85rem', borderBottom: '1px solid var(--border-subtle)' }}>
                {eeQMsg}
              </div>
            )}

            <div style={{ flex: 1, overflow: 'auto', padding: '16px 28px' }}>
              {eeQLoading ? (
                <div style={{ padding: 40, textAlign: 'center', color: 'var(--text-tertiary)' }}>Loading…</div>
              ) : eeQuestions.length === 0 ? (
                <div style={{ padding: 40, textAlign: 'center', color: 'var(--text-tertiary)' }}>No questions.</div>
              ) : eeQuestions.map((q, qi) => {
                const isEditing = eeEditingQ === q.id
                return (
                  <div key={q.id} className="card mb-8" style={{ padding: 14 }}>
                    <div className="flex-between" style={{ alignItems: 'flex-start', gap: 8 }}>
                      <div style={{ flex: 1 }}>
                        <div className="text-sm" style={{ color: 'var(--text-tertiary)', marginBottom: 6 }}>
                          Q{qi + 1} · <span className="badge badge-blue">{q.question_type}</span>
                          {' '}<span className="badge badge-amber">{q.difficulty_level}</span>
                          {' '}· {q.points} mark{q.points === 1 ? '' : 's'}
                        </div>
                        {isEditing ? (
                          <textarea className="textarea" rows={3} value={q.question_text || ''}
                            onChange={e => updateEeQ(q.id, 'question_text', e.target.value)} />
                        ) : (
                          <div style={{ whiteSpace: 'pre-wrap' }}>{q.question_text}</div>
                        )}
                      </div>
                      <div style={{ display: 'flex', gap: 4, flexShrink: 0 }}>
                        {isEditing ? (
                          <button className="btn btn-primary btn-sm" onClick={() => saveEeQuestion(q)}>Save</button>
                        ) : (
                          <button className="btn btn-ghost btn-sm" onClick={() => setEeEditingQ(q.id)}>
                            <Edit3 size={14} /> Edit
                          </button>
                        )}
                        <button className="btn btn-ghost btn-sm" style={{ color: '#ef4444' }}
                          onClick={() => deleteEeQuestion(q)}>
                          <Trash2 size={14} />
                        </button>
                      </div>
                    </div>

                    {isEditing && (
                      <div style={{ marginTop: 12 }}>
                        <div className="grid-2">
                          <div className="input-group">
                            <label className="text-sm">Difficulty</label>
                            <select className="select" value={q.difficulty_level || 'Medium'}
                              onChange={e => updateEeQ(q.id, 'difficulty_level', e.target.value)}>
                              <option>Easy</option><option>Medium</option><option>Hard</option>
                            </select>
                          </div>
                          <div className="input-group">
                            <label className="text-sm">Marks</label>
                            <input className="input" type="number" min={1} value={q.points || 1}
                              onChange={e => updateEeQ(q.id, 'points', parseInt(e.target.value) || 1)} />
                          </div>
                        </div>

                        {/* MCQ options */}
                        {Array.isArray(q.options) && q.options.length > 0 && (
                          <div className="input-group">
                            <label className="text-sm">Options (click the circle to mark the correct one)</label>
                            {q.options.map((opt, oi) => {
                              const letter = String.fromCharCode(65 + oi)
                              const isCorrect = (q.correct_answer || '').trim().toUpperCase() === letter
                              return (
                                <div key={oi} style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
                                  <button type="button" className="btn btn-ghost btn-sm"
                                    title="Mark as correct"
                                    style={{ color: isCorrect ? '#22c55e' : 'var(--text-tertiary)' }}
                                    onClick={() => updateEeQ(q.id, 'correct_answer', letter)}>
                                    {isCorrect ? '●' : '○'} {letter}
                                  </button>
                                  <input className="input" style={{ flex: 1 }} value={opt || ''}
                                    onChange={e => updateEeQOption(q.id, oi, e.target.value)} />
                                </div>
                              )
                            })}
                          </div>
                        )}

                        {/* Coding */}
                        {q.question_type === 'coding' && (
                          <>
                            <div className="input-group">
                              <label className="text-sm">Code Template</label>
                              <textarea className="textarea" rows={4}
                                style={{ fontFamily: 'var(--font-mono)', fontSize: '0.8rem' }}
                                value={q.code_template || ''}
                                onChange={e => updateEeQ(q.id, 'code_template', e.target.value)} />
                            </div>
                            <TestCasesEditor testCases={q.test_cases}
                              onChange={v => updateEeQ(q.id, 'test_cases', v)} />
                          </>
                        )}

                        {/* Descriptive */}
                        {q.question_type !== 'coding' && !(Array.isArray(q.options) && q.options.length) && (
                          <div className="input-group">
                            <label className="text-sm">Sample Answer</label>
                            <textarea className="textarea" rows={3} value={q.sample_answer || ''}
                              onChange={e => updateEeQ(q.id, 'sample_answer', e.target.value)} />
                          </div>
                        )}
                      </div>
                    )}

                    {!isEditing && q.question_type === 'coding' && (
                      <TestCasesView testCases={q.test_cases} />
                    )}
                  </div>
                )
              })}
            </div>
          </div>
        </div>
      )}

      {/* ═══════════ SCHEDULE MODAL ═══════════ */}
      {showSchedule && (
        <div className="modal-overlay" onClick={() => setShowSchedule(false)}>
          <div className="modal" onClick={e => e.stopPropagation()}>
            <h2>Schedule Interview — {selJob?.job_title}</h2>
            <form className="auth-form" onSubmit={handleSchedule}>
              <div className="input-group">
                <label>Candidate</label>
                <select className="select" required value={schedForm.candidate_id}
                  onChange={e => setSchedForm({ ...schedForm, candidate_id: e.target.value })}>
                  <option value="">Select candidate...</option>
                  {candidates.map(c => (
                    <option key={c.candidate_id} value={c.candidate_id} disabled={c.has_interview_scheduled}>
                      {c.name} — ATS: {c.ats_score ?? 'N/A'} {c.has_interview_scheduled ? '(already scheduled)' : ''}
                    </option>
                  ))}
                </select>
              </div>
              <div className="input-group">
                <label>Title</label>
                <input className="input" required value={schedForm.title}
                  onChange={e => setSchedForm({ ...schedForm, title: e.target.value })} />
              </div>
              <div className="grid-2">
                <div className="input-group">
                  <label>Type</label>
                  <select className="select" value={schedForm.interview_type}
                    onChange={e => setSchedForm({ ...schedForm, interview_type: e.target.value })}>
                    <option value="TECHNICAL">Technical</option>
                    <option value="NON_TECHNICAL">Non-Technical</option>
                    <option value="MIXED">Mixed</option>
                  </select>
                </div>
                <div className="input-group">
                  <label>Difficulty</label>
                  <select className="select" value={schedForm.difficulty_level}
                    onChange={e => setSchedForm({ ...schedForm, difficulty_level: e.target.value })}>
                    <option value="EASY">Easy</option>
                    <option value="MEDIUM">Medium</option>
                    <option value="HARD">Hard</option>
                  </select>
                </div>
              </div>
              <div className="grid-2">
                <div className="input-group">
                  <label>Date & Time</label>
                  <input className="input" type="datetime-local" required value={schedForm.scheduled_datetime}
                    onChange={e => setSchedForm({ ...schedForm, scheduled_datetime: e.target.value })} />
                </div>
                <div className="input-group">
                  <label>Duration (min)</label>
                  <input className="input" type="number" min={15} value={schedForm.duration_minutes}
                    onChange={e => setSchedForm({ ...schedForm, duration_minutes: parseInt(e.target.value) || 60 })} />
                </div>
              </div>
              <div className="input-group">
                <label>Meeting Link</label>
                <input className="input" placeholder="https://meet.google.com/..." value={schedForm.meeting_link}
                  onChange={e => setSchedForm({ ...schedForm, meeting_link: e.target.value })} />
              </div>
              <div className="input-group">
                <label>Notes</label>
                <textarea className="textarea" rows={2} value={schedForm.notes}
                  onChange={e => setSchedForm({ ...schedForm, notes: e.target.value })} />
              </div>
              <div className="flex gap-12" style={{ justifyContent: 'flex-end', marginTop: 8 }}>
                <button type="button" className="btn btn-secondary" onClick={() => setShowSchedule(false)}>Cancel</button>
                <button type="submit" className="btn btn-primary" disabled={loading}>
                  {loading ? 'Scheduling...' : 'Schedule Interview'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      <style>{`
        .q-num {
          font-family: var(--font-mono);
          font-size: 0.75rem;
          color: var(--accent-primary);
          background: var(--accent-glow);
          width: 24px; height: 24px;
          display: grid; place-items: center;
          border-radius: 6px;
          flex-shrink: 0;
          font-weight: 600;
        }
        .spin { animation: spin 1s linear infinite; }
        @keyframes spin { to { transform: rotate(360deg); } }
      `}</style>
    </>
  )
}
