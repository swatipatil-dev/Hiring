import { useState, useEffect, useRef, useCallback, useMemo, lazy, Suspense } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { motion, AnimatePresence } from 'framer-motion'
import api from '../../api/client'
import {
  isSupported as fullscreenSupported, isFullscreen,
  requestFullscreen, exitFullscreen, onFullscreenChange,
} from '../../utils/fullscreen'
import VoiceAnswer from './VoiceAnswer'
import ErrorBoundary from '../../components/ErrorBoundary'
import ExplanationForm from '../../components/ExplanationForm'

// Monaco is loaded lazily and isolated behind an ErrorBoundary so that if it fails
// to initialize (e.g. its web worker), the exam degrades to a plain code box instead
// of blanking the whole page.
const MonacoCodeEditor = lazy(() => import('../../components/MonacoCodeEditor'))

// Fallback language list if GET /judge/languages can't be reached; the live
// catalog (with Monaco syntax ids) replaces this on mount.
const FALLBACK_LANGS = [
  { key: 'python', label: 'Python 3', monaco: 'python' },
  { key: 'java', label: 'Java 21', monaco: 'java' },
  { key: 'cpp', label: 'C++ 17', monaco: 'cpp' },
  { key: 'javascript', label: 'JavaScript', monaco: 'javascript' },
  { key: 'sql', label: 'SQL', monaco: 'sql' },
]
const MONACO_FALLBACK = { python: 'python', java: 'java', cpp: 'cpp', javascript: 'javascript', sql: 'sql' }

// ── "Editorial Exam Hall" design tokens (mirror frontend/src/index.css) ──
// Warm ivory paper surfaces + deep teal accent for the light panes; a warm-charcoal
// palette for the code editor. Fraunces (display) / DM Sans (body) / JetBrains Mono.
const T = {
  // light "paper" surfaces
  canvas: '#f3f1ed', paper: '#ffffff', paperAlt: '#faf9f7', hover: '#f5f3ef',
  ink: '#1c1917', ink2: '#57534e', ink3: '#a8a29e',
  line: '#e7e5e0', line2: '#d6d3cd',
  // teal accent + warm tan
  teal: '#0e4fa0', tealDeep: '#0a3b7d', tealSoft: '#eef6f3', tealGlow: 'rgba(14,79,160,0.10)',
  tan: '#c17f3e', tanSoft: '#f6ecdf',
  // status (warm)
  ok: '#2d8a6e', okSoft: '#e8f3ee', warn: '#c17f3e', warnSoft: '#f6ecdf',
  danger: '#c44536', dangerSoft: '#f7e9e6', info: '#3b6fa0', infoSoft: '#eaf0f6',
  purple: '#7c5cbf', purpleSoft: '#f0ecf8',
  // warm-charcoal editor
  edBg: '#211b17', edHead: '#2a221d', edLine: '#3a312b', edText: '#e8e2d9',
  edDim: '#8a7f74', edTeal: '#4ecdb5', edAmber: '#e0a05a',
  // type
  serif: "'Fraunces','Georgia',serif", sans: "'DM Sans',system-ui,sans-serif",
  mono: "'JetBrains Mono','Fira Code',monospace",
  // depth + motion
  shSm: '0 1px 3px rgba(28,25,23,.06),0 1px 2px rgba(28,25,23,.04)',
  shMd: '0 4px 14px rgba(28,25,23,.08),0 1px 5px rgba(28,25,23,.04)',
  shLg: '0 12px 40px rgba(28,25,23,.10),0 3px 10px rgba(28,25,23,.05)',
  ease: 'cubic-bezier(0.16,1,0.3,1)',
}

const S = { CURRENT: 'current', NOT_ATTEMPTED: 'not_attempted', ANSWERED: 'answered', NOT_ANSWERED: 'not_answered', REVIEW: 'review' }

const S_BG = { [S.CURRENT]: T.teal, [S.NOT_ATTEMPTED]: T.paper, [S.ANSWERED]: T.ok, [S.NOT_ANSWERED]: T.tan, [S.REVIEW]: T.purple }
const S_FG = { [S.CURRENT]: '#fff', [S.NOT_ATTEMPTED]: T.ink2, [S.ANSWERED]: '#fff', [S.NOT_ANSWERED]: '#fff', [S.REVIEW]: '#fff' }
const S_BD = { [S.CURRENT]: T.teal, [S.NOT_ATTEMPTED]: T.line2, [S.ANSWERED]: T.ok, [S.NOT_ANSWERED]: T.tan, [S.REVIEW]: T.purple }

// Shared motion presets — per-question reveal + a soft staggered chrome entrance.
const qReveal = {
  initial: { opacity: 0, y: 14 },
  animate: { opacity: 1, y: 0 },
  exit: { opacity: 0, y: -10 },
  transition: { duration: 0.32, ease: [0.16, 1, 0.3, 1] },
}

function pad(n) { return String(n).padStart(2, '0') }

// Strip leaked solution queries from a coding problem statement before showing it
// to candidates: drop the example's "Input:" block (the SQL/data the solution would
// produce) and any bare SQL statement lines, keeping the prose and example Output.
const SQL_STMT_LINE = /^\s*(SELECT|INSERT|UPDATE|DELETE|CREATE|DROP|ALTER|WITH|MERGE|TRUNCATE)\b/i
function sanitizeStatement(text) {
  if (!text) return text
  const out = []
  let skipExampleInput = false
  for (const line of text.split(/\r?\n/)) {
    const t = line.trim()
    if (/^input:?$/i.test(t)) { skipExampleInput = true; continue }   // bare example "Input:" label
    if (/^output:?$/i.test(t)) { skipExampleInput = false; out.push(line); continue }
    if (skipExampleInput) {
      if (t === '') skipExampleInput = false                          // blank line ends the example input
      continue
    }
    if (SQL_STMT_LINE.test(line)) continue                            // drop any standalone SQL statement (answer leak)
    out.push(line)
  }
  // drop a now-dangling trailing "Example:" with nothing under it
  while (out.length && (out[out.length - 1].trim() === '' || /^example:?$/i.test(out[out.length - 1].trim()))) {
    out.pop()
  }
  return out.join('\n').replace(/\n{3,}/g, '\n\n').trim()
}

// A coding question whose answer is a SQL query — its sample "input" is a
// CREATE/INSERT seed script, so we never surface it as Sample Input (the table
// preview + expected output already live in the statement).
function isSqlQuestion(q) {
  if (!q) return false
  if ((q.category || '').toLowerCase().includes('sql')) return true
  return /^\s*--/.test(q.code_template || '')
}

// For non-SQL coding questions the generator appends a worked "Example:" block to
// question_text. We render Sample Input / Sample Output as their own labelled
// blocks (CodeKata-style), so strip that trailing example from the prose to avoid
// showing it twice. The "Input:/Output:" FORMAT lines (which have text after the
// colon) are kept — only a bare "Example:" / "Sample Input:" heading triggers the cut.
function stripWorkedExample(text) {
  if (!text) return text
  const lines = text.split(/\r?\n/)
  const i = lines.findIndex(l =>
    /^\s*(example|examples|sample\s*(input|output|test)?|sample)\s*:?\s*$/i.test(l))
  if (i === -1) return text.trim()
  return lines.slice(0, i).join('\n').replace(/\n{3,}/g, '\n\n').trim()
}

// MediaPipe 468-point face mesh connections (subset of tesselation for performance)
const FACE_MESH_TESS = [
  // Forehead
  10,338,297, 297,332,284, 284,251,389, 389,356,454, 454,323,361, 361,288,397,
  10,109,67, 67,103,54, 54,21,162, 162,127,234, 234,93,132, 132,58,172,
  // Nose bridge
  168,6,197, 197,195,5, 5,4,1, 1,19,94, 94,2,164,
  6,351,168, 168,122,6, 122,193,6, 193,417,6,
  // Nose sides
  4,45,51, 51,5,281, 281,275,4, 275,440,4,
  // Cheeks left
  93,132,58, 58,172,136, 136,150,149, 149,176,148, 148,152,377, 377,400,378, 378,379,365,
  // Cheeks right
  323,361,288, 288,397,365, 365,379,378, 378,400,377, 377,152,148,
  // Jaw
  10,338,297, 338,299,297, 297,332,284, 332,333,284, 284,251,389, 251,301,389,
  389,356,454, 356,368,454, 454,323,361, 323,324,361,
  10,109,67, 109,108,67, 67,103,54, 103,69,54, 54,21,162, 21,71,162,
  162,127,234, 127,139,234, 234,93,132, 93,137,132,
  // Around eyes - left
  33,7,163, 7,163,144, 144,145,153, 153,154,155, 155,133,173, 173,157,158, 158,159,160, 160,161,246, 246,33,7,
  // Around eyes - right
  362,382,381, 381,380,374, 374,373,390, 390,249,263, 263,466,388, 388,387,386, 386,385,384, 384,398,362,
  // Between eyes
  168,6,197, 6,122,196, 196,3,51, 3,196,419, 419,248,281,
  // Mouth area
  61,146,91, 91,181,84, 84,17,314, 314,405,321, 321,375,291,
  61,185,40, 40,39,37, 37,0,267, 267,269,270, 270,409,291,
  // Upper lip
  61,146,91, 146,77,91, 91,181,84, 181,82,84, 84,13,312, 13,312,311, 311,310,415, 415,308,324,
  // Lower lip
  61,185,40, 185,80,40, 40,39,37, 39,0,37, 0,267,269, 267,321,269, 269,270,409, 270,291,409,
]

// Face oval contour (36 points around the face)
const FACE_OVAL = [
  10,338,297,332,284,251,389,356,454,323,361,288,397,365,379,378,400,377,152,
  148,176,149,150,136,172,58,132,93,234,127,162,21,54,103,67,109,10
]

// Lip contour
const LIPS = [61,146,91,181,84,17,314,405,321,375,291,409,270,269,267,0,37,39,40,185,61]

// Eye contours
const LEFT_EYE_CONTOUR = [33,7,163,144,145,153,154,155,133,173,157,158,159,160,161,246,33]
const RIGHT_EYE_CONTOUR = [362,382,381,380,374,373,390,249,263,466,388,387,386,385,384,398,362]

function groupBySection(questions) {
  const g = {}
  questions.forEach((q, i) => {
    const sec = q.question_type || 'General'
    if (!g[sec]) g[sec] = []
    g[sec].push({ ...q, gi: i })
  })
  return g
}

// Friendly labels for proctoring flags shown in warning/termination messages.
const PROCTOR_FLAG_LABELS = {
  NO_FACE: 'No face detected', MULTIPLE_FACES: 'Multiple people detected',
  IDENTITY_MISMATCH: 'Identity mismatch', PHONE_DETECTED: 'Phone detected',
  PHONE_GESTURE: 'Phone-use gesture', MULTIPLE_VOICES: 'Multiple voices',
  CANDIDATE_LEFT: 'You left the camera view', TAB_SWITCH: 'You left the test tab',
  FULLSCREEN_EXIT: 'You left fullscreen',
  HEAD_TURNED: 'Looking away', SUSPICIOUS_GAZE: 'Gaze off-screen',
  LEANING_FORWARD: 'Leaning out of frame', HEAD_DOWN: 'Looking down',
}

export default function OnlineTest() {
  const { interviewId } = useParams()
  const navigate = useNavigate()
  const [questions, setQuestions] = useState([])
  const [idx, setIdx] = useState(0)
  const [answers, setAnswers] = useState({})
  const [statuses, setStatuses] = useState({})
  const [secs, setSecs] = useState(0)
  const [loading, setLoading] = useState(true)
  const [title, setTitle] = useState('')
  const [done, setDone] = useState(false)
  const [alreadySubmitted, setAlreadySubmitted] = useState(false)
  // A restored attempt whose time had already run out. Distinct from `done`: nothing has
  // been submitted yet, and distinct from `alreadySubmitted`: the server has not recorded
  // a submission either.
  const [timeExpired, setTimeExpired] = useState(false)
  const [confirmOpen, setConfirmOpen] = useState(false)
  const [probTab, setProbTab] = useState('problem') // problem | submissions
  const [submissions, setSubmissions] = useState({}) // { [questionId]: Attempt[] }
  // Per-question chosen language { [questionId]: langKey } — declared up here (not
  // with the other coding state below) because the localStorage-persistence effect
  // references it; declaring it later would hit a temporal-dead-zone error.
  const [langByQ, setLangByQ] = useState({})
  const timer = useRef(null)
  const stateRestored = useRef(false)
  const STATE_KEY = `hireflow_test_state_${interviewId}`

  // Webcam monitoring flag from interview's security_settings (null = loading)
  const [enableWebcamMonitoring, setEnableWebcamMonitoring] = useState(null)
  // Both default to the stricter setting until the interview record says otherwise.
  const [enableTabSwitchDetection, setEnableTabSwitchDetection] = useState(true)
  const [copyPasteAllowed, setCopyPasteAllowed] = useState(false)
  // Defaults ON to match the server: a test created before this flag existed still runs
  // fullscreen rather than silently losing the protection.
  const [requireFullscreen, setRequireFullscreen] = useState(true)
  const [fullscreenLost, setFullscreenLost] = useState(false)
  const [fullscreenUnavailable, setFullscreenUnavailable] = useState(false)
  const [fullscreenExits, setFullscreenExits] = useState(0)
  // Was the page fullscreen a moment ago? Only a true -> false transition is a
  // violation. Arriving without fullscreen means the request was refused on the
  // instructions page, which is not the candidate's doing and must not cost a strike.
  const wasFullscreenRef = useRef(false)

  // Pre-test connectivity check (skipped — device check screen removed)
  const [connectivityChecked, setConnectivityChecked] = useState(true)
  const [checkCamReady, setCheckCamReady] = useState(false)
  const [checkMicReady, setCheckMicReady] = useState(false)
  const [checkMicLevel, setCheckMicLevel] = useState(0)
  const [checkError, setCheckError] = useState(null)
  const checkVideoRef = useRef(null)
  const checkStreamRef = useRef(null)
  const checkAudioCtxRef = useRef(null)
  const checkAnimRef = useRef(null)

  // Camera & Mic proctoring
  const [camReady, setCamReady] = useState(false)
  const [camError, setCamError] = useState(false)  // camera/mic permission denied or unavailable
  const [micLevel, setMicLevel] = useState(0)
  const streamRef = useRef(null)
  const videoRef = useRef(null)
  const audioCtxRef = useRef(null)
  const animRef = useRef(null)

  // Proctoring WebSocket state
  const [proctorSession, setProctorSession] = useState(null)
  // `pending` = no analysis received yet; render neutral rather than a false red "No Face".
  const [faceMatch, setFaceMatch] = useState({ match: false, score: 0.0, detected: false, pending: true })
  const [cheatingScore, setCheatingScore] = useState(0)
  const [cheatingDecision, setCheatingDecision] = useState('NORMAL')
  const [proctorFlags, setProctorFlags] = useState([])
  const [faceRegistered, setFaceRegistered] = useState(false)
  const [moduleScores, setModuleScores] = useState({})
  const [tabSwitchCount, setTabSwitchCount] = useState(0)
  const [tabSwitchWarning, setTabSwitchWarning] = useState(false)
  const [multiFaceAlert, setMultiFaceAlert] = useState(false)
  const [multiFaceCount, setMultiFaceCount] = useState(0)
  const multiFaceTimerRef = useRef(null)
  const [headTurnAlert, setHeadTurnAlert] = useState(false)
  const [headTurnCount, setHeadTurnCount] = useState(0)
  const headTurnTimerRef = useRef(null)
  const [faceData, setFaceData] = useState(null)
  const overlayCanvasRef = useRef(null)
  const wsVideoRef = useRef(null)
  // Mic -> server PCM stream: the socket, its AudioContext, and the worklet/processor
  // node. Separate from audioCtxRef, which drives the on-screen level meter only.
  const wsAudioRef = useRef(null)
  const pcmCtxRef = useRef(null)
  const pcmNodeRef = useRef(null)
  const canvasRef = useRef(document.createElement('canvas'))

  // ── Frame pacing ──────────────────────────────────────────────────────────
  // Send the next frame only once the server has answered the previous one. A fixed
  // interval outruns the pipeline on slower machines, so frames queue up server-side
  // and the displayed face-match result drifts further behind real time the longer the
  // test runs (a page refresh emptied the queue, which is why it "fixed" itself).
  const inFlightRef = useRef(false)
  const lastSendAtRef = useRef(0)
  const lastAnalysisAtRef = useRef(0)
  const pumpTimerRef = useRef(null)
  const mismatchStreakRef = useRef(0)
  const lastGoodScoreRef = useRef(0)
  const [procMs, setProcMs] = useState(0)
  const [analysisAge, setAnalysisAge] = useState(0)
  // Live-enrollment state. Refs (not state) because the WS handlers close over them and
  // must see the current value without re-subscribing.
  const faceRegisteredRef = useRef(false)   // mirrors faceRegistered for the WS callbacks
  const enrollStartedRef = useRef(false)    // one live-enrollment attempt per session
  const startLiveEnrollmentRef = useRef(null)
  const enrollTimerRef = useRef(null)       // so retries can be cancelled on unmount
  const reconnectTimerRef = useRef(null)    // pending WS reconnect, cancellable
  const teardownRef = useRef(false)         // set by stopCamera: stop reconnecting
  const videoReadyRef = useRef(false)       // set on <video> onLoadedData — real frames exist
  const [enrollHint, setEnrollHint] = useState(null)  // why enrollment is still waiting

  // Single source of truth for the face-match verdict, shared by the camera-box border
  // and the status badge. These used to disagree: the badge was debounced but the border
  // read raw `!faceMatch.match`, so during start-up the box was outlined alarming red
  // while the badge underneath calmly said "Checking…".
  const faceState = useMemo(() => {
    const stale      = analysisAge > 3000
    const pending    = faceMatch.pending || stale
    const verified   = !pending && faceMatch.detected && faceMatch.hasReference && faceMatch.match
    const unverified = !pending && faceMatch.detected && !faceMatch.hasReference   // no reference photo enrolled
    const noFace     = !pending && !faceMatch.detected
    // Only call it a mismatch once it has persisted for a few frames.
    const mismatch   = !pending && faceMatch.detected && faceMatch.hasReference
                       && !faceMatch.match && faceMatch.mismatchConfirmed
    return { stale, pending, verified, unverified, noFace, mismatch }
  }, [faceMatch, analysisAge])

  const MIN_SEND_GAP_MS = 200   // 5 FPS ceiling
  const PUMP_TICK_MS = 120      // how often the pump re-checks
  const IN_FLIGHT_TIMEOUT_MS = 2500  // watchdog: never let a lost reply stall the pump
  const MISMATCH_CONFIRM_FRAMES = 3  // consecutive bad frames before showing "Mismatch"

  // Auto-termination: warning banner + terminate overlay
  const [proctorWarning, setProctorWarning] = useState(null)   // {flag, strike, max, severity}
  const [terminated, setTerminated] = useState(null)           // {reason, message}
  const warningTimerRef = useRef(null)
  const terminatedRef = useRef(false)
  const submittedRef = useRef(false)   // one auto-submit per mount for an expired attempt

  // Fetch the proctoring flags from the interview record. Defaults are the stricter
  // option (tab-switch on, copy/paste blocked) so a test whose settings never loaded
  // behaves the way it did when these two were applied unconditionally.
  useEffect(() => {
    let cancelled = false
    ;(async () => {
      try {
        const r = await api.get('/candidate/interviews')
        if (cancelled) return
        const m = (r.data || []).find(i => String(i.id) === String(interviewId))
        setEnableWebcamMonitoring(!!m?.enable_webcam_monitoring)
        setEnableTabSwitchDetection(m?.enable_tab_switch_detection !== false)
        setCopyPasteAllowed(!!m?.copy_paste_allowed)
        setRequireFullscreen(m?.require_fullscreen !== false)
      } catch {
        if (!cancelled) {
          setEnableWebcamMonitoring(false)
          setEnableTabSwitchDetection(true)
          setCopyPasteAllowed(false)
          setRequireFullscreen(true)
        }
      }
    })()
    return () => { cancelled = true }
  }, [interviewId])

  // Start proctoring session + camera (only when webcam monitoring is enabled)
  useEffect(() => {
    if (enableWebcamMonitoring !== true) return
    let cancelled = false
    let createdSessionId = null;

    (async () => {
      // 1. Start camera & mic
      try {
        const stream = await navigator.mediaDevices.getUserMedia({
          video: { width: 640, height: 480, frameRate: 15 },
          audio: true,
        })
        if (cancelled) { stream.getTracks().forEach(t => t.stop()); return }
        streamRef.current = stream
        // Unconditional: getUserMedia has resolved, so the stream is valid and usable.
        // The old `readyState === 'live'` snapshot could be false on a *fresh* permission
        // grant (the track is still 'starting'), which left camReady false forever — the
        // <video> never mounted, videoRef stayed null, and no frame ever reached the
        // server. That is the core reason face detection "only worked after a reload":
        // on reload the permission is pre-granted and the track is already live.
        setCamReady(true)

        // Mic level meter
        try {
          const audioCtx = new (window.AudioContext || window.webkitAudioContext)()
          audioCtxRef.current = audioCtx
          const source = audioCtx.createMediaStreamSource(stream)
          const analyser = audioCtx.createAnalyser()
          analyser.fftSize = 256
          source.connect(analyser)
          const dataArray = new Uint8Array(analyser.frequencyBinCount)
          const update = () => {
            analyser.getByteFrequencyData(dataArray)
            const avg = dataArray.reduce((a, b) => a + b, 0) / dataArray.length
            setMicLevel(Math.min(100, Math.round((avg / 128) * 100)))
            animRef.current = requestAnimationFrame(update)
          }
          update()
        } catch {}
      } catch {
        console.warn('Camera/mic access denied')
        // Surface it instead of silently proceeding unmonitored — this test
        // requires webcam monitoring, so make the failure visible to the candidate.
        setCamReady(false)
        setCamError(true)
        return  // don't start a proctoring session without a camera
      }

      // 2. Start proctoring session via REST
      try {
        const user = JSON.parse(localStorage.getItem('hireflow_user') || '{}')
        // The consent the candidate gave on the instructions page (see
        // MonitoringDisclosure in TestInstructions.jsx), stored with this session.
        let monitoringConsent = null
        try {
          monitoringConsent = JSON.parse(sessionStorage.getItem(`hf_monitoring_consent_${interviewId}`) || 'null')
        } catch { /* absent or unreadable: start without it */ }
        const res = await api.post('/proctor/sessions/start', {
          candidate_id: user.id || 'unknown',
          test_id: interviewId,
          interview_id: Number(interviewId),
          ...(monitoringConsent ? { monitoring_consent: monitoringConsent } : {}),
        })
        // Remember what we created so teardown can release it. StrictMode mounts this
        // effect twice in dev, and navigating away mid-test tears it down at any point —
        // either way the server would otherwise hold an orphaned session (a whole
        // MediaPipe graph, a YOLO model and a 4-thread executor) until the idle reaper.
        createdSessionId = res.data.session_id
        if (cancelled) {
          api.post(`/proctor/sessions/${createdSessionId}/stop`).catch(() => {})
          createdSessionId = null
          return
        }
        const session = res.data
        setProctorSession(session)
        setFaceRegistered(session.face_registered)

        // 3. Connect video WebSocket (with auto-reconnect)
        const wsProto = window.location.protocol === 'https:' ? 'wss' : 'ws'
        const wsUrl = `${wsProto}://${window.location.host}${session.ws_video_url}`
        let reconnectAttempts = 0
        const connectWs = () => {
        const ws = new WebSocket(wsUrl)
        wsVideoRef.current = ws

        ws.onopen = () => {
          console.log('Proctor video WS connected')
          reconnectAttempts = 0
          if (pumpTimerRef.current) clearTimeout(pumpTimerRef.current)  // avoid dup pump on reconnect
          inFlightRef.current = false

          // Live enrollment: only for candidates with no application photo on file. The
          // session is flagged LIVE_ENROLLED server-side (weaker assurance than a vetted
          // photo).
          //
          // This must NOT run while the server is still loading the stored photo
          // (`enrollment_pending`): self-enrolling in that window would replace a vetted
          // reference with a snapshot of whoever is at the camera, who would then match
          // themselves and pass identity verification unconditionally. The server tells
          // us when the lookup has resolved, via `enrollment_pending` on each analysis
          // frame — see the analysis handler below, which calls this.
          startLiveEnrollmentRef.current = () => {
            if (enrollStartedRef.current || faceRegisteredRef.current) return
            enrollStartedRef.current = true
            let tries = 0
            const tryEnroll = async () => {
              // Server-side registration may land mid-retry — stop the moment it does.
              if (faceRegisteredRef.current) return
              tries++
              const video = videoRef.current
              const canvas = canvasRef.current
              // Only capture once the <video> has decoded a real frame. Before this,
              // videoRef may be null (exam DOM still mounting on a cold first load) or the
              // frame is black — enrolling from either wasted attempts and, worse, could
              // register a black baseline. This wait is why enrollment no longer needs a
              // reload to succeed on first load.
              if (videoReadyRef.current && video && video.readyState >= 2 && canvas) {
                canvas.width = 640; canvas.height = 480
                canvas.getContext('2d').drawImage(video, 0, 0, 640, 480)
                const b64 = canvas.toDataURL('image/jpeg', 0.85).split(',')[1]
                try {
                  const er = await api.post(`/proctor/sessions/${session.session_id}/enroll-face`, { image_base64: b64 })
                  if (er.data?.face_registered) {
                    faceRegisteredRef.current = true
                    setFaceRegistered(true)
                    setEnrollHint(null)
                    return
                  }
                  // Surface why: the server rejects dark/distant frames rather than
                  // enrolling a baseline that would mismatch the candidate all session.
                  if (er.data?.message) setEnrollHint(er.data.message)
                } catch { /* fall through to retry */ }
              }
              // NEVER permanently give up. The previous 20-try cap latched the session with
              // no reference — the candidate then had to reload to be verified at all, which
              // is the reported bug. Instead keep retrying for the life of the session:
              // fast (1.5s) while the camera is warming up, then a calm 5s cadence so a
              // candidate who steps away or fixes lighting is picked up automatically.
              const delay = tries < 20 ? 1500 : 5000
              enrollTimerRef.current = setTimeout(tryEnroll, delay)
            }
            // 1.2s, not 600ms: the first frames after getUserMedia are typically still
            // auto-exposing, and enrolling from one of those is what produced a baseline
            // that later matched the candidate's own face at ~3%.
            enrollTimerRef.current = setTimeout(tryEnroll, 1200)
          }

          // Paced frame pump: at most one frame in flight, capped at 5 FPS. Effective
          // rate self-tunes to the server's throughput, so the result on screen is never
          // more than one processing interval old however long the session runs.
          const sendFrame = () => {
            const video = videoRef.current
            if (!video || video.readyState < 2) return
            const canvas = canvasRef.current
            canvas.width = 640
            canvas.height = 480
            const ctx = canvas.getContext('2d')
            ctx.drawImage(video, 0, 0, 640, 480)
            // Send as base64 JPEG (strip data:image/jpeg;base64, prefix)
            const dataUrl = canvas.toDataURL('image/jpeg', 0.8)
            const b64 = dataUrl.split(',')[1]
            if (!b64) return
            inFlightRef.current = true
            lastSendAtRef.current = Date.now()
            ws.send(b64)
          }

          const pump = () => {
            pumpTimerRef.current = setTimeout(pump, PUMP_TICK_MS)
            if (ws.readyState !== WebSocket.OPEN) return
            const now = Date.now()
            // Watchdog — a dropped reply or a server-side error before its response is
            // sent must never stall the pump permanently.
            if (inFlightRef.current && now - lastSendAtRef.current > IN_FLIGHT_TIMEOUT_MS) {
              inFlightRef.current = false
            }
            if (inFlightRef.current) return
            if (now - lastSendAtRef.current < MIN_SEND_GAP_MS) return
            sendFrame()
          }
          pump()
        }

        ws.onmessage = (evt) => {
          try {
            const msg = JSON.parse(evt.data)
            // Release the pump on any server reply that concludes a frame. Done up front
            // (and again in `finally`) so a malformed payload can't strand the sender.
            if (msg.event === 'analysis' || msg.event === 'error' || msg.event === 'skipped') {
              inFlightRef.current = false
              // Only a real analysis counts as freshness — a skipped frame is not a result.
              if (msg.event === 'analysis') lastAnalysisAtRef.current = Date.now()
            }
            if (msg.event === 'terminate') {
              terminateTest(msg.reason, msg.message)
              return
            } else if (msg.event === 'warning') {
              setProctorWarning({ severity: msg.severity, flag: msg.flag, strike: msg.strike, max: msg.max, flags: msg.flags })
              clearTimeout(warningTimerRef.current)
              warningTimerRef.current = setTimeout(() => setProctorWarning(null), msg.severity === 'serious' ? 6000 : 2500)
              return
            } else if (msg.event === 'face_registered') {
              faceRegisteredRef.current = !!msg.success
              setFaceRegistered(msg.success)
            } else if (msg.event === 'analysis') {
              // The stored-photo lookup has resolved. If it found nothing, this candidate
              // genuinely has no reference on file — now it's safe to self-enroll.
              if (msg.enrollment_pending === false && !faceRegisteredRef.current
                  && msg.face && !msg.face.has_reference) {
                startLiveEnrollmentRef.current?.()
              }
              setProcMs(msg.processing_ms || 0)
              setCheatingScore(msg.cheating_score || 0)
              setCheatingDecision(msg.decision || 'NORMAL')
              setProctorFlags(msg.flags || [])
              setModuleScores(msg.module_scores || {})
              // Debug face matching - check browser console (F12)
              if (msg.face && msg.face.identity_match !== undefined) {
                console.log('[FACE]', 'match:', msg.face.identity_match, 'sim:', msg.face.similarity_score,
                  'proc_ms:', msg.processing_ms, 'flags:', msg.flags)
              }
              if (msg.face) {
                const count = msg.face.count || 0
                // Keep faceRegistered truthful: once the live analysis confirms a reference
                // photo exists on the server, the "no application photo" banner must never
                // show again, regardless of what the initial session/enrollment reported.
                if (msg.face.has_reference) setFaceRegistered(true)
                // Debounce mismatch: the matcher fails safe to (false, 0.0) on any detection
                // hiccup (motion blur, brief occlusion), so a single frame must not flip the
                // badge to "Mismatch" and show a scary 0%. Require a short sustained streak.
                const rawMismatch = msg.face.detected && msg.face.has_reference && !msg.face.identity_match
                mismatchStreakRef.current = rawMismatch ? mismatchStreakRef.current + 1 : 0
                const mismatchConfirmed = mismatchStreakRef.current >= MISMATCH_CONFIRM_FRAMES
                if (msg.face.similarity_score > 0) lastGoodScoreRef.current = msg.face.similarity_score
                setFaceMatch({
                  match: msg.face.identity_match,
                  mismatchConfirmed,
                  hasReference: msg.face.has_reference,
                  // Hold the last real score while a mismatch is still unconfirmed, so a
                  // transient detection miss doesn't render as a confident "0%".
                  score: (rawMismatch && !mismatchConfirmed) ? lastGoodScoreRef.current : msg.face.similarity_score,
                  detected: msg.face.detected,
                  pending: false,
                  count,
                  gaze: msg.face.gaze_direction,
                  headTurned: msg.face.head_turned,
                  yaw: msg.face.yaw,
                  lipMoving: msg.face.lip_moving || false,
                  blinkDetected: msg.face.blink_detected || false,
                })
                // Store face data for landmark overlay
                setFaceData({
                  landmarks: msg.face.landmarks,
                  face_bbox: msg.face.face_bbox,
                  all_face_bboxes: msg.face.all_face_bboxes,
                  eye_boxes: msg.face.eye_boxes,
                  headTurned: msg.face.head_turned,
                  identity_match: msg.face.identity_match,
                  gaze: msg.face.gaze_direction,
                  detected: msg.face.detected,
                  lipMoving: msg.face.lip_moving || false,
                })
                // Trigger alerts for head turn
                if (msg.face.head_turned) {
                  setHeadTurnCount(prev => prev + 1)
                  setHeadTurnAlert(true)
                  clearTimeout(headTurnTimerRef.current)
                  headTurnTimerRef.current = setTimeout(() => setHeadTurnAlert(false), 3000)
                }
                // Trigger red alert for multiple faces
                if (count > 1) {
                  setMultiFaceCount(prev => prev + 1)
                  setMultiFaceAlert(true)
                  clearTimeout(multiFaceTimerRef.current)
                  multiFaceTimerRef.current = setTimeout(() => setMultiFaceAlert(false), 4000)
                }
              }
            }
          } catch (parseErr) {
            console.error('Proctor WS parse error:', parseErr, evt.data?.substring?.(0, 100))
          } finally {
            // Belt-and-braces: an unparseable reply still ends the in-flight frame.
            inFlightRef.current = false
          }
        }

        ws.onclose = (e) => {
          console.log('Proctor video WS closed:', e.code, e.reason)
          if (pumpTimerRef.current) clearTimeout(pumpTimerRef.current)
          // Must reset, or the pump on the reconnected socket inherits a stuck `true`
          // and sends nothing until the watchdog fires.
          inFlightRef.current = false
          // Auto-reconnect with capped exponential backoff, unless tearing down or
          // the server rejected the session (4004 = session not found).
          //
          // `teardownRef` covers the case `cancelled` cannot: stopCamera() (from submit or
          // auto-termination) closes this socket while the component stays mounted, so
          // `cancelled` is still false. Reconnecting there races a session the server has
          // already dropped and just yields a 4004 round-trip.
          if (!cancelled && !teardownRef.current && e.code !== 4004 && reconnectAttempts < 5) {
            reconnectAttempts++
            const delay = Math.min(1000 * 2 ** reconnectAttempts, 15000)
            console.log(`Proctor WS reconnecting in ${delay}ms (attempt ${reconnectAttempts})`)
            reconnectTimerRef.current = setTimeout(() => {
              if (!cancelled && !teardownRef.current) connectWs()
            }, delay)
          }
        }
        ws.onerror = (e) => console.error('Proctor WS error:', e)
        }  // end connectWs
        connectWs()

        // 4. Stream mic audio. The MediaStream above was already requested with
        //    `audio: true`, so this adds no permission prompt. Until now nothing ever
        //    opened this socket, so the server's audio module sat on an empty buffer and
        //    reported MIC_MUTED for every session — speaker diarization and level
        //    measurement had no input at all.
        //    Raw audio is analysed in memory and never persisted (AudioProctor docstring).
        const startAudioStream = async () => {
          if (!session.ws_audio_url || !streamRef.current) return
          // Approved accommodation: microphone monitoring is off for this attempt. The
          // server ignores audio anyway; not opening the stream means none is sent.
          if (session.audio_monitoring_off) return
          if (!streamRef.current.getAudioTracks().length) {
            console.warn('Proctor audio: stream carries no audio track')
            return
          }
          try {
            const aws = new WebSocket(`${wsProto}://${window.location.host}${session.ws_audio_url}`)
            aws.binaryType = 'arraybuffer'
            wsAudioRef.current = aws

            aws.onopen = async () => {
              try {
                const ctx = new (window.AudioContext || window.webkitAudioContext)()
                pcmCtxRef.current = ctx
                const source = ctx.createMediaStreamSource(streamRef.current)

                if (ctx.audioWorklet) {
                  await ctx.audioWorklet.addModule('/audio-pcm-worklet.js')
                  const node = new AudioWorkletNode(ctx, 'pcm-downsampler')
                  node.port.onmessage = (e) => {
                    if (aws.readyState === WebSocket.OPEN) aws.send(e.data)
                  }
                  // Connect to the destination via a muted gain node: some browsers
                  // suspend a worklet whose output goes nowhere, which would stop the
                  // stream without any error to notice.
                  const sink = ctx.createGain()
                  sink.gain.value = 0
                  source.connect(node)
                  node.connect(sink)
                  sink.connect(ctx.destination)
                  pcmNodeRef.current = node
                } else {
                  // Deprecated, but the only option on older Safari/Firefox builds.
                  const proc = ctx.createScriptProcessor(4096, 1, 1)
                  const ratio = ctx.sampleRate / 16000
                  proc.onaudioprocess = (e) => {
                    if (aws.readyState !== WebSocket.OPEN) return
                    const input = e.inputBuffer.getChannelData(0)
                    const out = new Int16Array(Math.floor(input.length / ratio))
                    for (let i = 0; i < out.length; i++) {
                      const s = Math.max(-1, Math.min(1, input[Math.floor(i * ratio)]))
                      out[i] = s < 0 ? s * 0x8000 : s * 0x7fff
                    }
                    aws.send(out.buffer)
                  }
                  source.connect(proc)
                  proc.connect(ctx.destination)
                  pcmNodeRef.current = proc
                }
              } catch (err) {
                console.warn('Proctor audio: capture setup failed', err)
              }
            }
            aws.onclose = () => { wsAudioRef.current = null }
            aws.onerror = (e) => console.warn('Proctor audio WS error:', e)
          } catch (err) {
            console.warn('Proctor audio: socket failed', err)
          }
        }
        startAudioStream()

      } catch (err) {
        console.warn('Proctoring session start failed:', err)
      }
    })()

    return () => {
      cancelled = true
      if (pumpTimerRef.current) clearTimeout(pumpTimerRef.current)
      // The enrollment retry loop schedules up to 6 more ticks; without this they keep
      // POSTing /enroll-face and calling setState after the component is gone.
      if (enrollTimerRef.current) clearTimeout(enrollTimerRef.current)
      if (reconnectTimerRef.current) clearTimeout(reconnectTimerRef.current)
      if (warningTimerRef.current) clearTimeout(warningTimerRef.current)
      if (headTurnTimerRef.current) clearTimeout(headTurnTimerRef.current)
      if (multiFaceTimerRef.current) clearTimeout(multiFaceTimerRef.current)
      inFlightRef.current = false
      if (wsVideoRef.current) wsVideoRef.current.close()
      // Tear the mic capture down before stopping the tracks, or the worklet keeps
      // pumping into a closed socket for as long as the context lives.
      if (pcmNodeRef.current) { try { pcmNodeRef.current.disconnect() } catch {} pcmNodeRef.current = null }
      if (pcmCtxRef.current) { try { pcmCtxRef.current.close() } catch {} pcmCtxRef.current = null }
      if (wsAudioRef.current) { try { wsAudioRef.current.close() } catch {} wsAudioRef.current = null }
      if (streamRef.current) streamRef.current.getTracks().forEach(t => t.stop())
      if (audioCtxRef.current) audioCtxRef.current.close()
      if (animRef.current) cancelAnimationFrame(animRef.current)
      // Release the server-side session. submitTest()/stopCamera() already do this on the
      // normal path; this covers every other way the effect can unmount (StrictMode's
      // double mount in dev, navigating away, an interviewId change) which otherwise
      // stranded a fully-built pipeline server-side.
      if (createdSessionId) {
        api.post(`/proctor/sessions/${createdSessionId}/stop`).catch(() => {})
        createdSessionId = null
      }
    }
  }, [interviewId, enableWebcamMonitoring])

  // Staleness monitor — if analyses stop arriving, the badge must stop reading as a
  // confident verdict. Makes a recurrence of the frame-backlog lag immediately visible
  // instead of silently showing an out-of-date result.
  useEffect(() => {
    if (enableWebcamMonitoring !== true) return
    const t = setInterval(() => {
      if (!lastAnalysisAtRef.current) return
      setAnalysisAge(Date.now() - lastAnalysisAtRef.current)
    }, 1000)
    return () => clearInterval(t)
  }, [enableWebcamMonitoring])

  // Tab switch / visibility change detection — only when the test enables it.
  useEffect(() => {
    if (!enableTabSwitchDetection) return
    const handleVisibility = () => {
      if (document.hidden) {
        setTabSwitchCount(prev => {
          const newCount = prev + 1
          // Show warning flash
          setTabSwitchWarning(true)
          setTimeout(() => setTabSwitchWarning(false), 3000)
          // Recorded for the reviewer. Tab switches never end the test (server policy).
          if (proctorSession?.session_id) {
            api.post(`/proctor/sessions/${proctorSession.session_id}/tab-switch`, {
              count: newCount,
              timestamp: Date.now(),
            }).then(r => {
              if (r.data?.terminate) {
                terminateTest(r.data.reason || 'TAB_SWITCH', r.data.message)
              } else if (r.data?.warning) {
                setProctorWarning({ severity: 'recorded', flag: 'TAB_SWITCH', occurrence: r.data.occurrence })
                clearTimeout(warningTimerRef.current)
                warningTimerRef.current = setTimeout(() => setProctorWarning(null), 6000)
              }
            }).catch(() => {})
          }
          return newCount
        })
      }
    }
    document.addEventListener('visibilitychange', handleVisibility)
    return () => document.removeEventListener('visibilitychange', handleVisibility)
  }, [proctorSession, enableTabSwitchDetection])

  // Fullscreen enforcement.
  //
  // This cannot stop anyone reaching another tab — Escape and F11 always work and no
  // page may suppress them. What it does is make leaving conspicuous: the questions go
  // behind an overlay, and each exit is recorded for the reviewer, like a tab switch.
  // Exits never end the test.
  //
  // Entering fullscreen needs a user gesture, so it happens on the Start button in
  // TestInstructions. Here we only observe, and offer a button to get back.
  useEffect(() => {
    if (!requireFullscreen || done || terminated) return

    // No fullscreen API at all (iOS Safari). Tell the proctor once so HR can see this
    // run was not fullscreen-monitored, then leave the candidate alone — blocking here
    // would mean nobody on an iPhone can take an assessment.
    if (!fullscreenSupported()) {
      setFullscreenUnavailable(true)
      if (proctorSession?.session_id) {
        api.post(`/proctor/sessions/${proctorSession.session_id}/fullscreen-unavailable`,
                 { reason: 'unsupported' }).catch(() => {})
      }
      return
    }

    wasFullscreenRef.current = isFullscreen()
    // Refused on the way in rather than exited: prompt, but do not penalise.
    setFullscreenLost(!wasFullscreenRef.current)

    const unsubscribe = onFullscreenChange((active) => {
      const left = wasFullscreenRef.current && !active
      wasFullscreenRef.current = active
      setFullscreenLost(!active)
      if (!left) return

      setFullscreenExits((prev) => {
        const n = prev + 1
        if (proctorSession?.session_id) {
          api.post(`/proctor/sessions/${proctorSession.session_id}/fullscreen-exit`, {
            count: n,
            timestamp: Date.now(),
          }).then(r => {
            if (r.data?.terminate) {
              terminateTest(r.data.reason || 'FULLSCREEN_EXIT', r.data.message)
            } else if (r.data?.warning) {
              setProctorWarning({ severity: 'recorded', flag: 'FULLSCREEN_EXIT', occurrence: r.data.occurrence })
              clearTimeout(warningTimerRef.current)
              warningTimerRef.current = setTimeout(() => setProctorWarning(null), 6000)
            }
          }).catch(() => {})
        }
        return n
      })
    })
    return unsubscribe
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [requireFullscreen, proctorSession, done, terminated])

  // Copy-paste blocking, right-click prevention, DevTools detection.
  // Copy/paste and right-click are suppressed only when the test forbids copy/paste;
  // DevTools detection is an integrity check and always runs.
  useEffect(() => {
    const blockCopy = (e) => { e.preventDefault(); console.warn('Copy blocked during test') }
    const blockPaste = (e) => { e.preventDefault(); console.warn('Paste blocked during test') }
    const blockContextMenu = (e) => { e.preventDefault(); console.warn('Right-click blocked during test') }

    // DevTools detection via resize heuristic
    let devToolsOpen = false
    const checkDevTools = () => {
      const widthThreshold = window.outerWidth - window.innerWidth > 160
      const heightThreshold = window.outerHeight - window.innerHeight > 160
      if ((widthThreshold || heightThreshold) && !devToolsOpen) {
        devToolsOpen = true
        console.warn('DevTools detected')
        if (proctorSession?.session_id) {
          api.post(`/proctor/sessions/${proctorSession.session_id}/tab-switch`, {
            count: 999,
            timestamp: Date.now(),
          }).catch(() => {})
        }
      } else if (!widthThreshold && !heightThreshold) {
        devToolsOpen = false
      }
    }

    if (!copyPasteAllowed) {
      document.addEventListener('copy', blockCopy)
      document.addEventListener('paste', blockPaste)
      document.addEventListener('contextmenu', blockContextMenu)
    }
    const devToolsInterval = setInterval(checkDevTools, 2000)

    return () => {
      document.removeEventListener('copy', blockCopy)
      document.removeEventListener('paste', blockPaste)
      document.removeEventListener('contextmenu', blockContextMenu)
      clearInterval(devToolsInterval)
    }
  }, [proctorSession, copyPasteAllowed])

  useEffect(() => {
    (async () => {
      try {
        // 1. Check if this interview has already been submitted (status === COMPLETED)
        try {
          const iv = await api.get('/candidate/interviews')
          const m = (iv.data || []).find(i => String(i.id) === String(interviewId))
          if (m) setTitle(m.job_position || m.title || m.job_title || '')
          if (m && String(m.status).toUpperCase() === 'COMPLETED') {
            try { localStorage.removeItem(STATE_KEY) } catch {}
            setAlreadySubmitted(true)
            setLoading(false)
            return
          }
        } catch {}

        // 2. Fetch questions
        let data = []
        try { const r = await api.get(`/mcq/interviews/${interviewId}/questions`); data = r.data?.questions || r.data || [] }
        catch { const r = await api.get(`/candidate/interviews/${interviewId}/questions`); data = r.data || [] }
        setQuestions(data)

        // 3. Restore saved progress from localStorage if present, else initialize
        let restored = false
        try {
          const raw = localStorage.getItem(STATE_KEY)
          if (raw) {
            const saved = JSON.parse(raw)
            if (saved && Array.isArray(saved.questionIds) && saved.questionIds.length === data.length
                && saved.questionIds.every((id, i) => String(id) === String(data[i]?.id))) {
              const elapsed = Math.max(0, Math.floor((Date.now() - (saved.savedAt || Date.now())) / 1000))
              const remaining = Math.max(0, (saved.secs || 0) - elapsed)
              // Drop any restored coding answer that is just the leaked starter
              // template (or an old default placeholder) — the editor must start
              // blank so the candidate writes their own code. Real typed answers
              // are kept.
              const savedAnswers = saved.answers || {}
              const cleanedAnswers = {}
              for (const [k, v] of Object.entries(savedAnswers)) {
                const qq = data[Number(k)]
                if (qq && qq.question_type === 'coding' && typeof v === 'string') {
                  const val = v.trim()
                  const tpl = (qq.code_template || '').trim()
                  if (!val || val === tpl || val === '# Write your code here') continue
                }
                cleanedAnswers[k] = v
              }
              setAnswers(cleanedAnswers)
              setStatuses(saved.statuses || {})
              setSubmissions(saved.submissions || {})
              setLangByQ(saved.langByQ || {})
              setIdx(typeof saved.idx === 'number' ? saved.idx : 0)
              setSecs(remaining)
              // The saved attempt has already run out of time. Restoring it as-is left the
              // page in a zombie state: secs=0 so the countdown effect never started, so
              // the "p <= 1 -> submitTest()" path that normally ends an expired test could
              // never fire — the candidate saw 00:00:00 and "Final minute!" forever, and a
              // reload restored the same dead state. Flag it and let the effect below
              // finish the attempt properly.
              if (remaining <= 0) setTimeExpired(true)
              restored = true
            }
          }
        } catch {}

        if (!restored) {
          setSecs(data.reduce((s, q) => s + (q.time_limit_minutes || 5), 0) * 60)
          const init = {}; data.forEach((_, i) => { init[i] = S.NOT_ATTEMPTED }); if (data.length) init[0] = S.CURRENT; setStatuses(init)
        }
        stateRestored.current = true
      } catch {} finally { setLoading(false) }
    })()
  }, [interviewId])

  // Connectivity check: start camera + mic when check screen is shown, clean up when done
  useEffect(() => {
    if (loading || done || alreadySubmitted || !questions.length || connectivityChecked) return
    let cancelled = false

    const startCheck = async () => {
      setCheckError(null)
      try {
        const stream = await navigator.mediaDevices.getUserMedia({
          video: { width: 640, height: 480, frameRate: 15 },
          audio: true,
        })
        if (cancelled) { stream.getTracks().forEach(t => t.stop()); return }
        checkStreamRef.current = stream

        // Camera ready when video track is live
        const vt = stream.getVideoTracks()[0]
        if (vt?.readyState === 'live') setCheckCamReady(true)

        // Attach to preview video element
        if (checkVideoRef.current) {
          checkVideoRef.current.srcObject = stream
          checkVideoRef.current.play().catch(() => {})
        }

        // Mic level meter
        const at = stream.getAudioTracks()[0]
        if (at?.readyState === 'live') {
          setCheckMicReady(true)
          try {
            const ctx = new (window.AudioContext || window.webkitAudioContext)()
            checkAudioCtxRef.current = ctx
            const src = ctx.createMediaStreamSource(stream)
            const analyser = ctx.createAnalyser()
            analyser.fftSize = 256
            src.connect(analyser)
            const data = new Uint8Array(analyser.frequencyBinCount)
            const tick = () => {
              analyser.getByteFrequencyData(data)
              const avg = data.reduce((a, b) => a + b, 0) / data.length
              setCheckMicLevel(Math.min(100, Math.round((avg / 128) * 100)))
              checkAnimRef.current = requestAnimationFrame(tick)
            }
            tick()
          } catch {}
        }
      } catch (err) {
        if (!cancelled) {
          const msg = err.name === 'NotAllowedError'
            ? 'Camera/microphone access was denied. Please allow access in your browser settings and try again.'
            : 'Could not access camera or microphone. Please ensure they are connected and not in use by another app.'
          setCheckError(msg)
          setCheckCamReady(false)
          setCheckMicReady(false)
        }
      }
    }

    startCheck()

    return () => {
      cancelled = true
      if (checkAnimRef.current) cancelAnimationFrame(checkAnimRef.current)
      if (checkAudioCtxRef.current) { checkAudioCtxRef.current.close(); checkAudioCtxRef.current = null }
      if (checkStreamRef.current) { checkStreamRef.current.getTracks().forEach(t => t.stop()); checkStreamRef.current = null }
    }
  }, [loading, done, alreadySubmitted, questions.length, connectivityChecked])

  // Persist test progress to localStorage on every change (debounced via refs)
  useEffect(() => {
    if (loading || done || alreadySubmitted || !stateRestored.current || !questions.length) return
    try {
      const payload = {
        questionIds: questions.map(q => q.id),
        answers,
        statuses,
        idx,
        secs,
        submissions,
        langByQ,
        savedAt: Date.now(),
      }
      localStorage.setItem(STATE_KEY, JSON.stringify(payload))
    } catch {}
  }, [answers, statuses, idx, secs, submissions, langByQ, loading, done, alreadySubmitted, questions])

  // `secs` must be in the deps: the guard below bails while it is still 0, and on a cold
  // load `secs` is populated from the questions fetch *after* `loading` flips false. With
  // only [loading, done] the effect never re-ran, so the countdown never started and the
  // timer sat at 00:00:00 (rendering "Final minute!", since secs < 60). A reload masked it
  // because `secs` is restored synchronously from localStorage before first paint.
  //
  // Finish an attempt that expired while the candidate was away. Their saved answers are
  // already restored into state, so this submits exactly what the live countdown would
  // have submitted had the tab stayed open — reloading cannot buy extra time, and the
  // page can never sit at 00:00:00 doing nothing.
  useEffect(() => {
    if (!timeExpired || loading || done || alreadySubmitted) return
    if (submittedRef.current) return
    submittedRef.current = true
    submitTest()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [timeExpired, loading, done, alreadySubmitted])

  // Depend on `secs > 0`, not `secs`: the boolean only changes when the timer crosses
  // zero, so the interval is created once. Depending on `secs` itself would tear down and
  // recreate the interval every tick, restarting its 1000ms window each time and drifting
  // the countdown over a long test.
  useEffect(() => {
    if (loading || done || secs <= 0) return
    timer.current = setInterval(() => setSecs(p => { if (p <= 1) { clearInterval(timer.current); submitTest(); return 0 } return p - 1 }), 1000)
    return () => clearInterval(timer.current)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loading, done, secs > 0])

  // Draw face landmarks + bboxes on overlay canvas.
  // Dormant while the candidate-facing camera preview is hidden — the canvas is not
  // mounted, so this exits immediately. Kept for whenever the preview is shown again.
  useEffect(() => {
    const canvas = overlayCanvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    const cw = canvas.width
    const ch = canvas.height
    ctx.clearRect(0, 0, cw, ch)

    if (!faceData) return

    // Draw all face bounding boxes
    if (faceData.all_face_bboxes) {
      faceData.all_face_bboxes.forEach((bb, i) => {
        // Mirror X for scaleX(-1) video
        const bx = cw - (bb[0] + bb[2]) * cw
        const by = bb[1] * ch
        const bw = bb[2] * cw
        const bh = bb[3] * ch
        const isPrimary = i === 0
        ctx.strokeStyle = isPrimary ? (faceData.identity_match ? '#22c55e' : '#ef4444') : '#ef4444'
        ctx.lineWidth = isPrimary ? 2 : 2.5
        ctx.setLineDash(isPrimary ? [] : [4, 3])
        ctx.strokeRect(bx, by, bw, bh)
        ctx.setLineDash([])
        // Label extra faces
        if (!isPrimary) {
          ctx.fillStyle = '#ef4444'
          ctx.font = 'bold 9px monospace'
          ctx.fillText(`EXTRA FACE`, bx + 2, by - 3)
        }
      })
    }

    // Determine alert state for coloring
    const isAlert = faceData.headTurned || !faceData.identity_match || !faceData.detected

    // Draw 468-point face mesh wireframe (tesselation + contours)
    if (faceData.landmarks && faceData.landmarks.length > 100) {
      const lms = faceData.landmarks
      const px = (i) => cw - lms[i][0] * cw  // mirror X
      const py = (i) => lms[i][1] * ch

      // Draw tesselation mesh lines — RED when alert, GREEN when normal
      ctx.strokeStyle = isAlert ? 'rgba(239,68,68,0.35)' : 'rgba(34,197,94,0.15)'
      ctx.lineWidth = isAlert ? 0.8 : 0.5
      for (let t = 0; t < FACE_MESH_TESS.length; t += 3) {
        const a = FACE_MESH_TESS[t], b = FACE_MESH_TESS[t+1], c = FACE_MESH_TESS[t+2]
        if (a < lms.length && b < lms.length && c < lms.length) {
          ctx.beginPath()
          ctx.moveTo(px(a), py(a))
          ctx.lineTo(px(b), py(b))
          ctx.lineTo(px(c), py(c))
          ctx.closePath()
          ctx.stroke()
        }
      }

      // Draw face oval contour — bold red on alert
      ctx.strokeStyle = isAlert ? 'rgba(239,68,68,0.9)' : 'rgba(34,197,94,0.5)'
      ctx.lineWidth = isAlert ? 2 : 1.2
      ctx.beginPath()
      for (let i = 0; i < FACE_OVAL.length; i++) {
        const idx = FACE_OVAL[i]
        if (idx < lms.length) {
          if (i === 0) ctx.moveTo(px(idx), py(idx))
          else ctx.lineTo(px(idx), py(idx))
        }
      }
      ctx.closePath()
      ctx.stroke()

      // Draw lips contour
      ctx.strokeStyle = isAlert ? 'rgba(239,68,68,0.7)' : 'rgba(234,88,12,0.5)'
      ctx.lineWidth = isAlert ? 1.5 : 1
      ctx.beginPath()
      for (let i = 0; i < LIPS.length; i++) {
        const idx = LIPS[i]
        if (idx < lms.length) {
          if (i === 0) ctx.moveTo(px(idx), py(idx))
          else ctx.lineTo(px(idx), py(idx))
        }
      }
      ctx.closePath()
      ctx.stroke()

      // Draw eye contours
      ctx.strokeStyle = isAlert ? 'rgba(239,68,68,0.7)' : 'rgba(59,130,246,0.6)'
      ctx.lineWidth = isAlert ? 1.5 : 1
      for (const eyeIdx of [LEFT_EYE_CONTOUR, RIGHT_EYE_CONTOUR]) {
        ctx.beginPath()
        for (let i = 0; i < eyeIdx.length; i++) {
          const idx = eyeIdx[i]
          if (idx < lms.length) {
            if (i === 0) ctx.moveTo(px(idx), py(idx))
            else ctx.lineTo(px(idx), py(idx))
          }
        }
        ctx.closePath()
        ctx.stroke()
      }

      // Draw iris circles
      if (lms.length > 473) {
        ctx.strokeStyle = isAlert ? 'rgba(239,68,68,0.8)' : 'rgba(16,185,129,0.7)'
        ctx.lineWidth = isAlert ? 1.5 : 1
        for (const irisCenter of [468, 473]) {
          if (irisCenter < lms.length) {
            const ix = px(irisCenter), iy = py(irisCenter)
            ctx.beginPath()
            ctx.arc(ix, iy, 3, 0, Math.PI * 2)
            ctx.stroke()
            ctx.fillStyle = isAlert ? 'rgba(239,68,68,0.9)' : 'rgba(16,185,129,0.8)'
            ctx.beginPath()
            ctx.arc(ix, iy, 1.5, 0, Math.PI * 2)
            ctx.fill()
          }
        }
      }
    }

    // Draw eye boxes
    if (faceData.eye_boxes) {
      ctx.strokeStyle = '#3b82f6'
      ctx.lineWidth = 1.5
      faceData.eye_boxes.forEach(eb => {
        const ex = cw - (eb[0] + eb[2]) * cw
        const ey = eb[1] * ch
        const ew = eb[2] * cw
        const eh = eb[3] * ch
        ctx.strokeRect(ex, ey, ew, eh)
      })
    }

    // Draw gaze direction indicator
    if (faceData.gaze && faceData.gaze !== 'CENTER' && faceData.gaze !== 'UNKNOWN' && faceData.face_bbox) {
      const fb = faceData.face_bbox
      const cx = cw - (fb[0] + fb[2] / 2) * cw
      const cy = (fb[1] + fb[3] / 2) * ch
      ctx.fillStyle = '#f59e0b'
      ctx.font = 'bold 10px monospace'
      const arrow = faceData.gaze === 'LEFT' ? '◄ LOOKING LEFT' : '► LOOKING RIGHT'
      ctx.fillText(arrow, cx - 35, cy - fb[3] * ch / 2 - 5)
    }

    // Draw warning text overlay
    if (faceData.headTurned) {
      ctx.fillStyle = 'rgba(239,68,68,0.85)'
      ctx.font = 'bold 11px sans-serif'
      ctx.fillText('⚠ HEAD TURNED', 4, ch - 6)
    } else if (!faceData.detected) {
      ctx.fillStyle = 'rgba(239,68,68,0.85)'
      ctx.font = 'bold 11px sans-serif'
      ctx.fillText('⚠ NO FACE', 4, ch - 6)
    } else if (!faceData.identity_match) {
      ctx.fillStyle = 'rgba(239,68,68,0.85)'
      ctx.font = 'bold 11px sans-serif'
      ctx.fillText('⚠ FACE MISMATCH', 4, ch - 6)
    }

    // Draw lip movement indicator
    if (faceData.lipMoving) {
      ctx.fillStyle = 'rgba(234,88,12,0.85)'
      ctx.font = 'bold 10px sans-serif'
      ctx.fillText('🔊 SPEAKING', 4, ch - 18)
    }
  }, [faceData])

  const hrs = Math.floor(secs / 3600), mins = Math.floor((secs % 3600) / 60), sec = secs % 60

  const goTo = useCallback((ni) => {
    setStatuses(p => {
      const n = { ...p }
      if (p[idx] === S.CURRENT) n[idx] = answers[idx] !== undefined ? S.ANSWERED : S.NOT_ANSWERED
      if (p[idx] === S.REVIEW) n[idx] = S.REVIEW
      n[ni] = n[ni] === S.REVIEW ? S.REVIEW : S.CURRENT
      return n
    })
    setIdx(ni)
  }, [idx, answers])

  const selectAnswer = (oi) => {
    setAnswers(p => ({ ...p, [idx]: oi }))
    setStatuses(p => ({ ...p, [idx]: S.CURRENT }))
  }

  const toggleReview = () => setStatuses(p => ({ ...p, [idx]: p[idx] === S.REVIEW ? (answers[idx] !== undefined ? S.ANSWERED : S.CURRENT) : S.REVIEW }))

  const [scoreResult, setScoreResult] = useState(null)

  const stopCamera = () => {
    // Tell the socket's onclose handler this is an intentional teardown, so it does not
    // schedule a reconnect to a session we are about to stop.
    teardownRef.current = true
    // Stop frame capture
    if (pumpTimerRef.current) {
      clearTimeout(pumpTimerRef.current)
      pumpTimerRef.current = null
    }
    if (reconnectTimerRef.current) {
      clearTimeout(reconnectTimerRef.current)
      reconnectTimerRef.current = null
    }
    if (enrollTimerRef.current) {
      clearTimeout(enrollTimerRef.current)
      enrollTimerRef.current = null
    }
    inFlightRef.current = false
    // Close WebSocket
    if (wsVideoRef.current) {
      wsVideoRef.current.close()
      wsVideoRef.current = null
    }
    // Stop proctoring session
    if (proctorSession?.session_id) {
      api.post(`/proctor/sessions/${proctorSession.session_id}/stop`).catch(() => {})
    }
    if (streamRef.current) {
      streamRef.current.getTracks().forEach(t => t.stop())
      streamRef.current = null
    }
    if (audioCtxRef.current) {
      audioCtxRef.current.close()
      audioCtxRef.current = null
    }
    if (animRef.current) {
      cancelAnimationFrame(animRef.current)
      animRef.current = null
    }
    // The old <video> is gone; a fresh one must re-signal readiness via onLoadedData.
    videoReadyRef.current = false
    setCamReady(false)
    setMicLevel(0)
  }

  // Auto-terminate: lock the screen with a reason, then submit whatever's answered (once).
  const terminateTest = (reason, message) => {
    if (terminatedRef.current) return
    terminatedRef.current = true
    // Hand the screen back. The test is over; trapping them in fullscreen to read a
    // result they cannot act on is just rude.
    exitFullscreen()
    setTerminated({ reason, message: message || 'The test ended early after repeated proctoring warnings.' })
    submitTest()
  }

  const submitTest = async () => {
    clearInterval(timer.current)
    stopCamera()
    // Release the screen on the way out, whichever branch below completes. Holding a
    // finished candidate in fullscreen to read their score is pointless, and it also
    // stops the exit firing as a violation after the test is already over.
    exitFullscreen()
    try {
      const res = await api.post(`/candidate/interviews/${interviewId}/submit`, { answers: questions.map((q, i) => q.question_type === 'coding'
        ? { question_id: q.id, selected_option: (typeof answers[i] === 'string' ? answers[i] : ''), language: (q.language || langByQ[q.id] || 'python') }
        : { question_id: q.id, selected_option: answers[i] !== undefined ? q.options?.[answers[i]] : null }) })
      setScoreResult(res.data)
      try { localStorage.removeItem(STATE_KEY) } catch {}
      setDone(true)
    } catch (err) {
      if (err?.response?.status === 403) {
        try { localStorage.removeItem(STATE_KEY) } catch {}
        setAlreadySubmitted(true)
        return
      }
      // Retry once on network/server error before giving up
      try {
        const res = await api.post(`/candidate/interviews/${interviewId}/submit`, { answers: questions.map((q, i) => q.question_type === 'coding'
        ? { question_id: q.id, selected_option: (typeof answers[i] === 'string' ? answers[i] : ''), language: (q.language || langByQ[q.id] || 'python') }
        : { question_id: q.id, selected_option: answers[i] !== undefined ? q.options?.[answers[i]] : null }) })
        setScoreResult(res.data)
        try { localStorage.removeItem(STATE_KEY) } catch {}
        setDone(true)
      } catch {
        alert('Submission failed. Please check your connection and try again.')
      }
    }
  }

  const paletteStatus = (i) => {
    if (i === idx && statuses[i] !== S.REVIEW) return S.CURRENT
    if (statuses[i] === S.REVIEW) return S.REVIEW
    if (answers[i] !== undefined) return S.ANSWERED
    if (statuses[i] === S.NOT_ANSWERED) return S.NOT_ANSWERED
    return S.NOT_ATTEMPTED
  }

  const [codeTab, setCodeTab] = useState('testcase')  // testcase | result
  const [codeRunning, setCodeRunning] = useState(false)
  const [langCatalog, setLangCatalog] = useState([])  // [{key,label,monaco}]
  const [customInput, setCustomInput] = useState('')  // stdin for the free "Run Code"
  const [runOutput, setRunOutput] = useState(null)    // last /judge/run result
  const [gradeData, setGradeData] = useState(null)    // last /judge/grade-question result
  const [codeMsg, setCodeMsg] = useState('')          // message / error / unavailable text
  const [judgeUnavailable, setJudgeUnavailable] = useState(false)

  const JUDGE_UNAVAILABLE_MSG ='⚠ Automatic code checking is temporarily unavailable.\n\nDon\'t worry — your code is saved automatically. Click "Submit Test" to finish; your solution will be graded once the service is back.'

  // Append a per-question evaluate attempt to the Submissions tab history.
  const recordSubmission = (qid, attempt) => setSubmissions(prev => ({
    ...prev,
    [qid]: [...(prev[qid] || []), { ts: Date.now(), ...attempt }],
  }))

  const sections = groupBySection(questions)
  const q = questions[idx]
  const hasCodingQuestions = questions.some(q => q.question_type === 'coding')
  const isCoding = hasCodingQuestions && q && q.question_type === 'coding'
  const isCommunication = q && q.question_type === 'communication'

  // Run-attempt budget per coding question. Only graded attempts (verdict set)
  // count — 'pending' (sandbox-down) runs are free. Derived from the same
  // submissions state the Submissions tab uses, so it stays in sync and the
  // server (POST /grade-question) enforces the same cap authoritatively.
  const MAX_RUNS = 3
  const runsUsed = (submissions[q?.id] || []).filter(s => s.verdict).length
  const runsLeft = Math.max(0, MAX_RUNS - runsUsed)

  // Load this coding question's persisted attempt history from the DB (source of
  // truth; survives refresh / other devices). Falls back to the localStorage cache.
  useEffect(() => {
    if (!isCoding || !q?.id || !interviewId) return
    let cancelled = false
    api.get('/judge/interview-submissions', { params: { interview_id: Number(interviewId), question_id: q.id } })
      .then(({ data }) => {
        if (!cancelled && Array.isArray(data?.submissions)) {
          setSubmissions(prev => ({ ...prev, [q.id]: data.submissions }))
        }
      })
      .catch(() => {})
    return () => { cancelled = true }
  }, [isCoding, q?.id, interviewId])

  // Current editor value, the candidate's chosen language for THIS question, and
  // the matching Monaco syntax id (from the live catalog, with a static fallback).
  // The editor starts BLANK — no starter/stub code is shown so the candidate
  // writes their solution from scratch. Only their own typed answer is restored.
  const codeVal = (typeof answers[idx] === 'string' ? answers[idx] : '')
  // If HR assigned a language to this coding question, it is REQUIRED — lock the
  // editor to it. Otherwise the candidate picks (default python).
  const forcedLang = (q && q.language && String(q.language).trim()) ? String(q.language).trim().toLowerCase() : null
  const curLang = forcedLang || (q && langByQ[q.id]) || 'python'
  const monacoLang = (langCatalog.find(l => l.key === curLang)?.monaco) || MONACO_FALLBACK[curLang] || 'plaintext'

  // Language catalog for the picker (drives which languages are actually runnable).
  useEffect(() => {
    api.get('/judge/languages')
      .then(({ data }) => { if (Array.isArray(data?.languages) && data.languages.length) setLangCatalog(data.languages) })
      .catch(() => {})
  }, [])

  // Reset the run/grade panels and prefill the custom-input box when the active
  // coding question changes (sample input as a sensible default for "Run Code").
  useEffect(() => {
    setRunOutput(null); setGradeData(null); setCodeMsg(''); setCodeTab('testcase')
    setCustomInput(isCoding ? (q?.sample_test_cases?.[0]?.input || '') : '')
  }, [q?.id, isCoding])

  if (loading) return <div style={x.screen}><div style={x.spin}/><style>{kf}</style></div>

  if (alreadySubmitted) return (
    <div style={x.screen}>
      <div style={x.doneCard}>
        <div style={{ ...x.doneCheck, background: '#fef3c7' }}>
          <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="#d97706" strokeWidth="2.5">
            <circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/>
          </svg>
        </div>
        <h2 style={x.doneTitle}>Test Already Submitted</h2>
        <p style={x.doneSub}>
          Each attempt can only be submitted once. If something went wrong, tell the hiring
          team below; they can offer another attempt.
        </p>
        <div style={{ margin: '4px 0 18px' }}><ExplanationForm interviewId={interviewId} /></div>
        <button style={x.doneBtn} onClick={() => navigate('/candidate/interviews')}>Back to Dashboard</button>
      </div>
      <style>{kf}</style>
    </div>
  )

  // A test the proctor ended early also lands here once it auto-submits. It used to show
  // the ordinary "Test Submitted" card, so the candidate never saw why it stopped.
  if (done && terminated) return (
    <div style={x.screen}>
      <div style={x.doneCard}>
        <div style={{ ...x.doneCheck, background: '#fef3c7' }}>
          <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="#d97706" strokeWidth="2.5">
            <circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/>
          </svg>
        </div>
        <h2 style={x.doneTitle}>Your test ended early</h2>
        <p style={x.doneSub}>{terminated.message}</p>
        <p style={x.doneSub}>
          Your answers up to that point have been submitted. A person on the hiring team reviews
          what happened before any decision is made, and can offer another attempt.
        </p>
        <div style={{ margin: '4px 0 18px' }}><ExplanationForm interviewId={interviewId} /></div>
        <button style={x.doneBtn} onClick={() => navigate('/candidate/interviews')}>Back to Dashboard</button>
      </div>
      <style>{kf}</style>
    </div>
  )

  if (done) return (
    <div style={x.screen}>
      <div style={x.doneCard}>
        <div style={x.doneCheck}><svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="#34a853" strokeWidth="2.5"><polyline points="20 6 9 17 4 12"/></svg></div>
        <h2 style={x.doneTitle}>Test Submitted</h2>
        <p style={x.doneSub}>Thank you for completing the test. Our team will review your results and get back to you soon.</p>
        {enableWebcamMonitoring && (
          <div style={{ margin: '4px 0 18px' }}><ExplanationForm interviewId={interviewId} /></div>
        )}
        <button style={x.doneBtn} onClick={() => navigate('/candidate/interviews')}>Back to Dashboard</button>
      </div>
      <style>{kf}</style>
    </div>
  )

  if (!questions.length) return (
    <div style={x.screen}>
      <p style={{ color: '#666', fontSize: 14 }}>No questions found for this test.</p>
      <button style={x.doneBtn} onClick={() => navigate('/candidate/interviews')}>Back</button>
      <style>{kf}</style>
    </div>
  )

  // ─── Connectivity Check Screen ────────────────────────────────────────────────
  if (!connectivityChecked) {
    const bothReady = checkCamReady && checkMicReady
    const handleStartTest = () => {
      // Stop check stream — proctoring useEffect will restart if webcam monitoring is on
      if (checkAnimRef.current) cancelAnimationFrame(checkAnimRef.current)
      if (checkAudioCtxRef.current) { checkAudioCtxRef.current.close(); checkAudioCtxRef.current = null }
      if (checkStreamRef.current) { checkStreamRef.current.getTracks().forEach(t => t.stop()); checkStreamRef.current = null }
      setConnectivityChecked(true)
    }
    const handleRetry = () => {
      setCheckCamReady(false); setCheckMicReady(false); setCheckMicLevel(0); setCheckError(null)
      if (checkStreamRef.current) { checkStreamRef.current.getTracks().forEach(t => t.stop()); checkStreamRef.current = null }
      // Re-trigger the effect by toggling a dummy — just re-invoke startCheck via key trick;
      // Instead, manually call getUserMedia here:
      ;(async () => {
        try {
          const stream = await navigator.mediaDevices.getUserMedia({ video: true, audio: true })
          checkStreamRef.current = stream
          const vt = stream.getVideoTracks()[0]
          if (vt?.readyState === 'live') setCheckCamReady(true)
          if (checkVideoRef.current) { checkVideoRef.current.srcObject = stream; checkVideoRef.current.play().catch(() => {}) }
          const at = stream.getAudioTracks()[0]
          if (at?.readyState === 'live') {
            setCheckMicReady(true)
            const ctx = new (window.AudioContext || window.webkitAudioContext)()
            checkAudioCtxRef.current = ctx
            const src = ctx.createMediaStreamSource(stream)
            const analyser = ctx.createAnalyser(); analyser.fftSize = 256; src.connect(analyser)
            const data = new Uint8Array(analyser.frequencyBinCount)
            const tick = () => { analyser.getByteFrequencyData(data); const avg = data.reduce((a,b)=>a+b,0)/data.length; setCheckMicLevel(Math.min(100,Math.round((avg/128)*100))); checkAnimRef.current = requestAnimationFrame(tick) }
            tick()
          }
          setCheckError(null)
        } catch (err) {
          setCheckError(err.name === 'NotAllowedError'
            ? 'Camera/microphone access was denied. Please allow access in your browser settings and try again.'
            : 'Could not access camera or microphone. Please ensure they are connected and not in use by another app.')
        }
      })()
    }

    return (
      <div style={{ ...x.screen, background: '#f0f4f8' }}>
        <div style={{ background: '#fff', borderRadius: 16, padding: '36px 40px', maxWidth: 560, width: '90%', boxShadow: '0 8px 32px rgba(0,0,0,0.12)', border: '1px solid #e5e7eb' }}>
          {/* Header */}
          <div style={{ textAlign: 'center', marginBottom: 28 }}>
            <div style={{ width: 56, height: 56, borderRadius: '50%', background: '#eff6ff', display: 'grid', placeItems: 'center', margin: '0 auto 14px' }}>
              <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="#1a73e8" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M23 7l-7 5 7 5V7z"/><rect x="1" y="5" width="15" height="14" rx="2" ry="2"/>
              </svg>
            </div>
            <h2 style={{ margin: '0 0 6px', fontSize: '1.25rem', fontWeight: 800, color: '#111' }}>Device Check</h2>
            <p style={{ margin: 0, fontSize: '0.88rem', color: '#6b7280', lineHeight: 1.6 }}>
              Both camera and microphone must be working before you can start the test.
            </p>
          </div>

          {/* Camera preview */}
          <div style={{ marginBottom: 20 }}>
            <div style={{ fontSize: '0.78rem', fontWeight: 700, textTransform: 'uppercase', color: '#9ca3af', marginBottom: 8, display: 'flex', alignItems: 'center', gap: 6 }}>
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke={checkCamReady ? '#22c55e' : '#9ca3af'} strokeWidth="2.5"><path d="M23 7l-7 5 7 5V7z"/><rect x="1" y="5" width="15" height="14" rx="2" ry="2"/></svg>
              Camera
              <span style={{ marginLeft: 'auto', fontSize: '0.75rem', fontWeight: 700, padding: '2px 8px', borderRadius: 10, background: checkCamReady ? '#dcfce7' : '#f3f4f6', color: checkCamReady ? '#15803d' : '#6b7280' }}>
                {checkCamReady ? 'Connected' : 'Waiting…'}
              </span>
            </div>
            <div style={{ width: '100%', aspectRatio: '16/9', borderRadius: 10, overflow: 'hidden', background: '#111', border: `2px solid ${checkCamReady ? '#22c55e' : '#e5e7eb'}`, position: 'relative' }}>
              <video ref={checkVideoRef} autoPlay playsInline muted style={{ width: '100%', height: '100%', objectFit: 'cover', transform: 'scaleX(-1)', display: 'block' }} />
              {!checkCamReady && !checkError && (
                <div style={{ position: 'absolute', inset: 0, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', color: '#9ca3af' }}>
                  <div style={{ width: 22, height: 22, borderRadius: '50%', border: '3px solid #555', borderTopColor: '#9ca3af', animation: 'spin .7s linear infinite', marginBottom: 10 }} />
                  <span style={{ fontSize: '0.82rem' }}>Requesting camera access…</span>
                </div>
              )}
            </div>
          </div>

          {/* Mic level */}
          <div style={{ marginBottom: 24 }}>
            <div style={{ fontSize: '0.78rem', fontWeight: 700, textTransform: 'uppercase', color: '#9ca3af', marginBottom: 8, display: 'flex', alignItems: 'center', gap: 6 }}>
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke={checkMicReady ? '#22c55e' : '#9ca3af'} strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2"/></svg>
              Microphone
              <span style={{ marginLeft: 'auto', fontSize: '0.75rem', fontWeight: 700, padding: '2px 8px', borderRadius: 10, background: checkMicReady ? '#dcfce7' : '#f3f4f6', color: checkMicReady ? '#15803d' : '#6b7280' }}>
                {checkMicReady ? 'Connected' : 'Waiting…'}
              </span>
            </div>
            <div style={{ background: '#f3f4f6', borderRadius: 8, padding: '12px 14px', border: `1px solid ${checkMicReady ? '#bbf7d0' : '#e5e7eb'}` }}>
              <div style={{ fontSize: '0.8rem', color: '#6b7280', marginBottom: 8 }}>Speak into your microphone — you should see the bar move:</div>
              <div style={{ height: 12, borderRadius: 6, background: '#e5e7eb', overflow: 'hidden' }}>
                <div style={{
                  height: '100%', borderRadius: 6, transition: 'width 0.1s ease',
                  width: `${checkMicLevel}%`,
                  background: checkMicLevel > 60 ? '#22c55e' : checkMicLevel > 20 ? '#f59e0b' : '#94a3b8'
                }} />
              </div>
              {checkMicReady && (
                <div style={{ fontSize: '0.75rem', color: checkMicLevel > 5 ? '#15803d' : '#9ca3af', marginTop: 6, fontWeight: 500 }}>
                  {checkMicLevel > 5 ? '✓ Microphone is picking up audio' : 'Microphone connected — speak to test it'}
                </div>
              )}
            </div>
          </div>

          {/* Error */}
          {checkError && (
            <div style={{ marginBottom: 20, padding: '12px 14px', background: '#fef2f2', borderRadius: 8, border: '1px solid #fca5a5', fontSize: '0.84rem', color: '#dc2626', lineHeight: 1.6 }}>
              {checkError}
            </div>
          )}

          {/* Actions */}
          <div style={{ display: 'flex', gap: 10 }}>
            {checkError && (
              <button style={{ ...x.doneBtn, background: '#6b7280', flex: 1 }} onClick={handleRetry}>Retry</button>
            )}
            <button
              style={{ ...x.doneBtn, flex: 1, opacity: bothReady ? 1 : 0.45, cursor: bothReady ? 'pointer' : 'not-allowed', background: '#1a73e8' }}
              disabled={!bothReady}
              onClick={handleStartTest}
            >
              {bothReady ? 'Start Test →' : 'Waiting for devices…'}
            </button>
          </div>
          {bothReady && (
            <p style={{ textAlign: 'center', fontSize: '0.78rem', color: '#9ca3af', margin: '14px 0 0' }}>
              Camera and microphone are ready. Click "Start Test" to begin.
            </p>
          )}
        </div>
        <style>{kf}</style>
      </div>
    )
  }
  // ─────────────────────────────────────────────────────────────────────────────

  return (
    <div style={x.root}>
      {/* ═══ PROCTORING WARNING TOAST ═══ */}
      {proctorWarning && !terminated && (
        <div style={{
          position: 'fixed', top: 14, left: '50%', transform: 'translateX(-50%)', zIndex: 10000,
          maxWidth: 560, padding: '10px 16px', borderRadius: 10,
          background: proctorWarning.severity === 'serious' ? '#fef2f2' : '#fffbeb',
          border: `1.5px solid ${proctorWarning.severity === 'serious' ? '#dc2626' : '#f59e0b'}`,
          boxShadow: '0 6px 20px rgba(0,0,0,0.15)', display: 'flex', alignItems: 'center', gap: 10,
        }}>
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke={proctorWarning.severity === 'serious' ? '#dc2626' : '#b45309'} strokeWidth="2.5">
            <path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/>
          </svg>
          <div style={{ fontSize: 12.5, color: proctorWarning.severity === 'serious' ? '#991b1b' : '#92400e', fontWeight: 600 }}>
            {/* Only a second person, a phone or a face mismatch can end the test, so only
                those get the numbered "will end" warning. Everything else is recorded for
                the reviewer, and the wording says exactly that. */}
            {proctorWarning.severity === 'serious'
              ? `Warning ${proctorWarning.strike}/${proctorWarning.max} — ${PROCTOR_FLAG_LABELS[proctorWarning.flag] || proctorWarning.flag}. The test will end if this continues.`
              : proctorWarning.severity === 'recorded'
                ? `${PROCTOR_FLAG_LABELS[proctorWarning.flag] || proctorWarning.flag} — recorded for the reviewer. Please stay on the test.`
                : `${(proctorWarning.flags || []).map(f => PROCTOR_FLAG_LABELS[f] || f).join(', ')} — recorded for the reviewer. Please face the screen.`}
          </div>
        </div>
      )}

      {/* ═══ FULLSCREEN LOST — questions hidden until they return ═══ */}
      {/* Below the terminated overlay in z-order on purpose: once the test is over,
          "return to fullscreen" is the wrong thing to be asking for. */}
      {requireFullscreen && fullscreenLost && !terminated && !done && (
        <div style={{
          position: 'fixed', inset: 0, zIndex: 10000, background: 'rgba(15,23,42,0.92)',
          display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 20,
        }}>
          <div style={{ maxWidth: 460, background: '#fff', borderRadius: 14, padding: '28px 30px', textAlign: 'center', boxShadow: '0 20px 60px rgba(0,0,0,0.4)' }}>
            <div style={{ width: 52, height: 52, borderRadius: '50%', background: '#fef3c7', display: 'flex', alignItems: 'center', justifyContent: 'center', margin: '0 auto 14px' }}>
              <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="#b45309" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                <path d="M8 3H5a2 2 0 0 0-2 2v3m18 0V5a2 2 0 0 0-2-2h-3m0 18h3a2 2 0 0 0 2-2v-3M3 16v3a2 2 0 0 0 2 2h3"/>
              </svg>
            </div>
            <h2 style={{ margin: '0 0 8px', fontSize: 19, color: '#0f172a' }}>Return to fullscreen to continue</h2>
            <p style={{ margin: 0, fontSize: 13.5, color: '#475569', lineHeight: 1.5 }}>
              This assessment runs in fullscreen. Your questions are hidden until you go
              back.
            </p>
            {/* The timer does not stop for this, and saying so is fairer than letting
                them discover it. */}
            <p style={{ margin: '10px 0 0', fontSize: 12.5, color: '#b45309', fontWeight: 600 }}>
              The timer is still running.
            </p>
            {fullscreenExits > 0 && (
              <p style={{ margin: '10px 0 0', fontSize: 12.5, color: '#991b1b', fontWeight: 700 }}>
                Leaving fullscreen is recorded for the reviewer. This is exit {fullscreenExits}.
              </p>
            )}
            <button
              onClick={() => requestFullscreen()}
              style={{ marginTop: 20, padding: '11px 28px', borderRadius: 999, border: 'none', background: '#0f766e', color: '#fff', fontSize: 14, fontWeight: 700, cursor: 'pointer' }}
            >
              Return to fullscreen
            </button>
          </div>
        </div>
      )}

      {/* ═══ FULLSCREEN UNAVAILABLE — this browser cannot do it ═══ */}
      {/* Not a blocker. iOS Safari has no element fullscreen, and a candidate should not
          lose an application over their phone. The proctor has been told. */}
      {requireFullscreen && fullscreenUnavailable && !terminated && !done && (
        <div style={{
          position: 'fixed', top: 14, left: '50%', transform: 'translateX(-50%)', zIndex: 9999,
          maxWidth: 560, padding: '10px 16px', borderRadius: 10, background: '#fffbeb',
          border: '1.5px solid #f59e0b', boxShadow: '0 6px 20px rgba(0,0,0,0.15)',
          fontSize: 12.5, color: '#92400e', fontWeight: 600,
        }}>
          This browser cannot run the test in fullscreen. You may continue — please stay
          on this tab, which is still monitored.
        </div>
      )}

      {/* ═══ TEST-TERMINATED OVERLAY (non-dismissible) ═══ */}
      {terminated && (
        <div style={{
          position: 'fixed', inset: 0, zIndex: 10001, background: 'rgba(15,23,42,0.85)',
          display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 20,
        }}>
          <div style={{ maxWidth: 460, background: '#fff', borderRadius: 14, padding: '28px 30px', textAlign: 'center', boxShadow: '0 20px 60px rgba(0,0,0,0.4)' }}>
            <div style={{ width: 52, height: 52, borderRadius: '50%', background: '#fee2e2', display: 'flex', alignItems: 'center', justifyContent: 'center', margin: '0 auto 14px' }}>
              <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="#dc2626" strokeWidth="2.5"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg>
            </div>
            <h2 style={{ margin: '0 0 8px', fontSize: 19, color: '#0f172a' }}>Test Ended</h2>
            <p style={{ margin: 0, fontSize: 13.5, color: '#475569', lineHeight: 1.5 }}>
              {terminated.message}
            </p>
            <p style={{ margin: '12px 0 0', fontSize: 13, color: '#334155', lineHeight: 1.5 }}>
              Your answers so far have been submitted. A person on the hiring team reviews
              what happened before any decision is made.
            </p>
            <p style={{ margin: '10px 0 0', fontSize: 12, color: '#94a3b8' }}>
              You may close this window.
            </p>
          </div>
        </div>
      )}

      {/* ═══ HEADER ═══ */}
      <div style={x.header}>
        <span style={x.headerTxt}>Online Test{title ? ` - ${title}` : ''}</span>
      </div>

      {isCoding ? (
        /* ═══════════ CODING QUESTION LAYOUT ═══════════ */
        <div style={x.body}>
          {/* Left: Problem statement */}
          <div style={c.problemPane}>
            <div style={c.probTabs}>
              <span className="ot-tab" style={probTab === 'problem' ? c.probTabActive : c.probTab} onClick={() => setProbTab('problem')}>Problem</span>
              <span className="ot-tab" style={probTab === 'submissions' ? c.probTabActive : c.probTab} onClick={() => setProbTab('submissions')}>
                Submissions{(submissions[q.id]?.length || 0) > 0 ? ` (${submissions[q.id].length})` : ''}
              </span>
              <span style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 6 }}>
                <button className="ot-btn" style={{ ...x.btnPrev, padding: '6px 13px', opacity: idx === 0 ? .4 : 1 }} disabled={idx === 0} onClick={() => goTo(idx - 1)}>‹ Prev</button>
                <button className="ot-btn" style={{ ...x.btnNext, padding: '6px 13px', opacity: idx === questions.length - 1 ? .4 : 1 }} disabled={idx === questions.length - 1} onClick={() => goTo(idx + 1)}>Next ›</button>
              </span>
            </div>
            <div style={c.probBody}>
              {probTab === 'problem' ? (
                <motion.div key={idx} {...qReveal}>
                  <h2 style={c.probTitle}>Problem {idx + 1}</h2>
                  <div style={c.probMeta}>
                    <span style={{ ...c.tag, background: q.difficulty_level === 'Easy' ? T.okSoft : q.difficulty_level === 'Hard' ? T.dangerSoft : T.warnSoft, color: q.difficulty_level === 'Easy' ? T.ok : q.difficulty_level === 'Hard' ? T.danger : T.tan }}>{q.difficulty_level || 'Medium'}</span>
                    <span style={c.tag}>{q.category || 'coding'}</span>
                    <span style={{ ...c.pts, marginLeft: 'auto' }}>◆ {q.points || 10} pts</span>
                  </div>

                  <div style={c.section}>
                    <div style={c.secTitle}>Problem Statement:</div>
                    <div style={c.secText}>{isSqlQuestion(q) ? sanitizeStatement(q.question_text) : stripWorkedExample(q.question_text)}</div>
                  </div>

                  {/* Sample I/O, CodeKata-style: each sample case shows its Sample Input
                      and Sample Output as labelled blocks. For SQL questions the "input"
                      is a seed script (answer leak) — those show Output only; the table
                      preview already lives in the statement above. The reference solution
                      (sample_answer) and hidden cases are never sent to the candidate. */}
                  {q.sample_test_cases?.map((tc, ti) => {
                    const n = q.sample_test_cases.length > 1 ? ` ${ti + 1}` : ''
                    const showInput = !isSqlQuestion(q) && (tc.input ?? '') !== ''
                    return (
                      <div key={ti}>
                        {showInput && (
                          <div style={c.section}>
                            <div style={c.secTitle}>Sample Input{n}:</div>
                            <div style={c.sampleBox}>{tc.input}</div>
                          </div>
                        )}
                        {(tc.expected_output ?? '') !== '' && (
                          <div style={c.section}>
                            <div style={c.secTitle}>Sample Output{n}:</div>
                            <div style={c.sampleBox}>{tc.expected_output}</div>
                          </div>
                        )}
                      </div>
                    )
                  })}
                </motion.div>
              ) : (
                <SubmissionsList items={submissions[q.id] || []} />
              )}
            </div>
          </div>

          {/* Right: Code editor + test panel */}
          <div style={c.editorPane}>
            {/* Editor header */}
            <div style={c.editorHeader}>
              <span style={c.editorTitle}>Code Editor</span>
              <select
                style={c.langSelect}
                value={curLang}
                disabled={!!forcedLang}
                title={forcedLang ? 'This coding question must be answered in this language' : undefined}
                onChange={e => { const v = e.target.value; setLangByQ(p => ({ ...p, [q.id]: v })); setRunOutput(null); setGradeData(null); setCodeMsg('') }}
              >
                {forcedLang ? (
                  <option value={curLang}>
                    {((langCatalog.find(l => l.key === curLang) || FALLBACK_LANGS.find(l => l.key === curLang))?.label || curLang).toUpperCase()} (required)
                  </option>
                ) : (langCatalog.length ? langCatalog : FALLBACK_LANGS).map(l => (
                  <option key={l.key} value={l.key}>{(l.label || l.key).toUpperCase()}</option>
                ))}
              </select>
              <span style={{ flex: 1 }}/>
              <span style={c.timerBadge}>{pad(hrs)}:{pad(mins)}:{pad(sec)}</span>
            </div>

            {/* Code editor: lazy Monaco, isolated so a failure falls back to a
                plain textarea instead of blanking the exam page. */}
            <div style={c.editorWrap}>
              <ErrorBoundary
                fallback={
                  <textarea
                    style={c.codeInput}
                    value={codeVal}
                    spellCheck={false}
                    onChange={e => setAnswers(p => ({ ...p, [idx]: e.target.value }))}
                    placeholder="# Write your solution here"
                  />
                }
              >
                <Suspense fallback={<div style={{ color: T.edDim, fontFamily: T.sans, fontSize: 13, padding: 16 }}>Loading editor…</div>}>
                  <MonacoCodeEditor
                    language={monacoLang}
                    value={codeVal}
                    onChange={(v) => setAnswers(p => ({ ...p, [idx]: v }))}
                  />
                </Suspense>
              </ErrorBoundary>
            </div>

            {/* Custom input / result panel */}
            <div style={c.testPanel}>
              <div style={c.testTabs}>
                <span style={codeTab === 'testcase' ? c.testTabActive : c.testTab} onClick={() => setCodeTab('testcase')}>Test Case</span>
                <span style={codeTab === 'result' ? c.testTabActive : c.testTab} onClick={() => setCodeTab('result')}>Test Result</span>
                <span style={{ flex: 1 }}/>
                {gradeData && !codeMsg && <span style={c.consoleLbl}>{gradeData.passed}/{gradeData.total} passed</span>}
              </div>

              {codeTab === 'testcase' && (
                <div style={c.testBody}>
                  <div style={c.ioLabel}>{curLang === 'sql' ? 'Setup SQL (seed data — used by Run Code)' : 'Custom Input (stdin — used by Run Code)'}</div>
                  <textarea
                    style={c.customInput}
                    value={customInput}
                    onChange={e => setCustomInput(e.target.value)}
                    spellCheck={false}
                    placeholder={curLang === 'sql' ? 'CREATE TABLE ...; INSERT INTO ...;' : 'Input passed to your program on stdin'}
                  />
                  {(q.sample_test_cases?.[0]?.expected_output ?? '') !== '' && (
                    <>
                      <div style={{ ...c.ioLabel, marginTop: 10 }}>Expected Output (sample 1)</div>
                      <div style={c.ioBox}>{q.sample_test_cases[0].expected_output}</div>
                    </>
                  )}
                </div>
              )}

              {codeTab === 'result' && (
                <div style={c.testBody}>
                  {codeMsg
                    ? <div style={c.resultMsg}>{codeMsg}</div>
                    : runOutput
                      ? <RunOutputView data={runOutput} />
                      : gradeData
                        ? <CaseTable data={gradeData} samples={q.sample_test_cases || []} />
                        : <div style={{ color: T.ink3, fontFamily: T.sans, fontSize: 13, padding: 12 }}>Run your code to see results here.</div>}
                </div>
              )}
            </div>

            {/* Bottom actions */}
            {judgeUnavailable && (
              <div style={c.judgeNotice}>
                Auto-grading is offline — your code is saved automatically. Offline tries don't use up your runs. Click "Submit Test" to finish.
              </div>
            )}
            <div style={c.editorFooter}>
              <span style={c.runsLeftLbl}>
                {runsLeft > 0
                  ? `${runsLeft} of ${MAX_RUNS} graded runs left`
                  : `No graded runs left (${MAX_RUNS}/${MAX_RUNS} used) — finish with Submit Test`}
              </span>
              {/* Free scratch run against custom input — never consumes a graded run. */}
              <button
                className="ot-btn"
                style={{ ...c.runBtn, ...(codeRunning ? c.btnDisabled : {}) }}
                disabled={codeRunning}
                onClick={async () => {
                  setAnswers(p => ({ ...p, [idx]: codeVal }))
                  setCodeRunning(true); setRunOutput(null); setGradeData(null); setCodeMsg(''); setCodeTab('result')
                  try {
                    const { data } = await api.post('/judge/run', { language: curLang, source_code: codeVal, stdin: customInput, question_id: q.id, interview_id: Number(interviewId) })
                    setRunOutput(data)
                  } catch (e) {
                    setCodeMsg('Run failed: ' + (e.response?.data?.detail || e.message))
                  } finally {
                    setCodeRunning(false)
                  }
                }}>
                {codeRunning ? 'Running…' : 'Run Code'}
              </button>
              {/* Graded run against all test cases — capped at MAX_RUNS. */}
              <button
                className="ot-btn"
                style={{ ...c.runTestsBtn, ...((codeRunning || runsLeft <= 0) ? c.btnDisabled : {}) }}
                disabled={codeRunning || runsLeft <= 0}
                onClick={async () => {
                  setAnswers(p => ({ ...p, [idx]: codeVal }))
                  setCodeRunning(true); setRunOutput(null); setGradeData(null); setCodeMsg(''); setCodeTab('result')
                  try {
                    const { data } = await api.post('/judge/grade-question', { question_id: q.id, language: curLang, source_code: codeVal, interview_id: Number(interviewId) })
                    if (data.unavailable) {
                      setJudgeUnavailable(true)
                      setCodeMsg(JUDGE_UNAVAILABLE_MSG)
                      recordSubmission(q.id, { language: curLang, unavailable: true, max_points: data.max_points ?? (q.points || 0) })
                    } else if (data.message) {
                      setCodeMsg(data.message)
                      recordSubmission(q.id, { language: curLang, note: data.message, max_points: data.max_points ?? (q.points || 0) })
                    } else {
                      setGradeData(data)
                      recordSubmission(q.id, {
                        language: curLang, verdict: data.verdict,
                        points_awarded: data.points_awarded ?? 0, max_points: data.max_points ?? (q.points || 0),
                        passed: data.passed ?? 0, total: data.total ?? 0,
                      })
                    }
                    setProbTab('submissions')
                  } catch (e) {
                    setCodeMsg(e.response?.status === 429
                      ? (e.response?.data?.detail || `Run limit reached (${MAX_RUNS}/${MAX_RUNS}). Use Submit Test to finish.`)
                      : 'Grading failed: ' + (e.response?.data?.detail || e.message))
                  } finally {
                    setCodeRunning(false)
                  }
                }}>
                {codeRunning ? '…' : (runsLeft <= 0 ? 'No runs left' : `Run Tests (${runsLeft}/${MAX_RUNS})`)}
              </button>
              <button className="ot-btn" style={c.submitCodeBtn} onClick={() => { setAnswers(p => ({ ...p, [idx]: codeVal })); setConfirmOpen(true) }}>
                Submit Test
              </button>
            </div>
          </div>
        </div>
      ) : (
        /* ═══════════ MCQ / TEXT QUESTION LAYOUT ═══════════ */
        <div style={x.body}>
          <div style={x.left}>
            <div style={x.qCard}>
              <AnimatePresence mode="wait">
              <motion.div key={idx} {...qReveal}>
              <div style={x.qTop}>
                <span style={x.qKicker}>{q.category || q.question_type || 'General'}</span>
              </div>
              <h2 style={x.qSec}>Question {idx + 1}</h2>
              <div style={x.qDivider}/>
              <p style={x.qText}>{q.question_text}</p>

              {q.options?.length > 0 && (
                <div style={x.opts} role="radiogroup" aria-label={`Answer options for question ${idx + 1}`}>
                  {q.options.map((opt, j) => {
                    const sel = answers[idx] === j
                    return (
                      // A real <input type="radio"> inside a <label>, visually hidden.
                      //
                      // This was a <div onClick>, which meant the only way to answer a question
                      // was with a mouse — a candidate navigating by keyboard could open the
                      // test and not sit it. Going native rather than adding role/tabIndex by
                      // hand also brings arrow-key movement between options, the roving tab
                      // stop, and the "option 2 of 4" announcement, none of which we would
                      // otherwise get right.
                      <label key={j} className="ot-opt" style={{ ...x.optRow, ...(sel ? x.optRowSel : {}) }}>
                        <input type="radio" className="ot-opt__input" name={`ot-q-${idx}`}
                               checked={sel} onChange={() => selectAnswer(j)} />
                        <div style={{ ...x.optBox, ...(sel ? x.optBoxSel : {}) }} aria-hidden="true">
                          {sel && <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#fff" strokeWidth="3"><polyline points="20 6 9 17 4 12"/></svg>}
                        </div>
                        <span style={{ ...x.optLetter, ...(sel ? x.optLetterSel : {}) }} aria-hidden="true">{String.fromCharCode(65 + j)}</span>
                        <span style={x.optText}>{opt}</span>
                      </label>
                    )
                  })}
                </div>
              )}

              {isCommunication ? (
                <VoiceAnswer
                  interviewId={interviewId}
                  questionId={q.id}
                  savedResult={answers[idx] && typeof answers[idx] === 'object' ? answers[idx] : null}
                  onRecorded={(r) => {
                    setAnswers(p => ({ ...p, [idx]: r }))
                    setStatuses(p => ({ ...p, [idx]: S.ANSWERED }))
                  }}
                />
              ) : !(q.options?.length > 0) && (
                <textarea style={x.textArea} rows={5} placeholder="Type your answer..." value={typeof answers[idx] === 'string' ? answers[idx] : ''} onChange={e => setAnswers(p => ({ ...p, [idx]: e.target.value }))}/>
              )}
              </motion.div>
              </AnimatePresence>
            </div>

            <div style={x.bottomBar}>
              <div style={x.navBtns}>
                <button className="ot-btn" style={x.btnReview} onClick={toggleReview}>{statuses[idx] === S.REVIEW ? 'Unmark Review' : 'Mark for review'}</button>
                <button className="ot-btn" style={{ ...x.btnPrev, opacity: idx === 0 ? .4 : 1 }} disabled={idx === 0} onClick={() => goTo(idx - 1)}>Previous</button>
                <button className="ot-btn" style={{ ...x.btnNext, opacity: idx === questions.length - 1 ? .4 : 1 }} disabled={idx === questions.length - 1} onClick={() => goTo(idx + 1)}>Next</button>
              </div>
              <button className="ot-btn" style={x.btnSubmit} onClick={() => setConfirmOpen(true)}>Submit Test</button>
            </div>

            <div style={x.legend}>
              {[[S.CURRENT,'Current'],[S.NOT_ATTEMPTED,'Not Attempted'],[S.ANSWERED,'Answered'],[S.NOT_ANSWERED,'Not Answered'],[S.REVIEW,'Review']].map(([s,l])=>(
                <div key={s} style={x.legItem}>
                  <span style={{ ...x.legDot, background: S_BG[s], border: s === S.NOT_ATTEMPTED ? `2px solid ${T.line2}` : `2px solid ${S_BG[s]}` }}/>
                  <span style={x.legLabel}>{l}</span>
                </div>
              ))}
            </div>
          </div>

          <div style={x.right}>
            <div style={x.timerCard}>
              <div style={x.timerHead}>Time Remaining</div>
              {(() => {
                const tColor = secs < 60 ? T.danger : secs < 300 ? T.tan : T.ink
                return (
                  <div style={x.timerRow}>
                    <div style={x.timerUnit}><span style={{ ...x.timerNum, color: tColor }}>{pad(hrs)}</span><span style={x.timerLabel}>hrs</span></div>
                    <span style={x.timerColon}>:</span>
                    <div style={x.timerUnit}><span style={{ ...x.timerNum, color: tColor }}>{pad(mins)}</span><span style={x.timerLabel}>min</span></div>
                    <span style={x.timerColon}>:</span>
                    <div style={x.timerUnit}><span style={{ ...x.timerNum, color: tColor, ...(secs < 60 ? { animation: 'blink 1s infinite' } : {}) }}>{pad(sec)}</span><span style={x.timerLabel}>sec</span></div>
                  </div>
                )
              })()}
              {/* `secs > 0` guard: at 0 the timer has either not been populated yet or the
                  attempt is over, and in both cases "Final minute!" is a lie. Without it a
                  test whose duration had not loaded rendered 00:00:00 + "Final minute!". */}
              {secs > 0 && secs < 300 && <div style={x.timerWarn}>{secs < 60 ? 'Final minute!' : 'Less than 5 minutes left'}</div>}
            </div>
            {/* Proctoring: Camera + Mic + Face Match + Assessment Integrity Flags (only if interviewer enabled webcam monitoring) */}
            {enableWebcamMonitoring && (
            <div style={x.proctorCard}>
              <div style={x.proctorHead}>
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke={camReady ? T.ok : T.danger} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M23 7l-7 5 7 5V7z"/><rect x="1" y="5" width="15" height="14" rx="2" ry="2"/></svg>
                <span style={{ fontFamily: T.sans, fontSize: 11, fontWeight: 700, color: T.ink3, textTransform: 'uppercase', letterSpacing: '0.1em' }}>Proctoring</span>
                {camReady && (
                  <span style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 5, fontFamily: T.mono, fontSize: 10, fontWeight: 700, color: T.danger, background: T.dangerSoft, padding: '3px 9px', borderRadius: 999, letterSpacing: '0.08em' }}>
                    <span style={{ width: 6, height: 6, borderRadius: '50%', background: T.danger, animation: 'blink 1.4s infinite', display: 'inline-block' }} />
                    REC
                  </span>
                )}
                {!camReady && (
                  <span style={{ marginLeft: 'auto', fontFamily: T.mono, fontSize: 10, fontWeight: 700, color: T.danger, background: T.dangerSoft, padding: '3px 9px', borderRadius: 999, letterSpacing: '0.08em' }}>
                    OFF
                  </span>
                )}
              </div>
              {/* The camera feed is deliberately NOT shown to the candidate — only that
                  proctoring is running. The <video> still has to be mounted and decoding:
                  it is the frame source the pipeline draws to a canvas and ships over the
                  WS, and onLoadedData gates enrollment. Hence 1x1/opacity-0 rather than
                  `display:none` or `visibility:hidden`, either of which lets a browser stop
                  producing frames and would silently kill proctoring. */}
              {camReady && (
                <div style={{ position: 'absolute', width: 1, height: 1, opacity: 0, overflow: 'hidden', pointerEvents: 'none' }} aria-hidden="true">
                  <video ref={el => {
                    videoRef.current = el
                    if (el && streamRef.current && el.srcObject !== streamRef.current) {
                      el.srcObject = streamRef.current
                      el.play().catch(() => {})
                    }
                  }}
                  // Real frames now exist. Enrollment waits for this so its first
                  // capture is never a black pre-decode frame.
                  onLoadedData={() => { videoReadyRef.current = true }}
                  autoPlay playsInline muted style={{ width: '100%', height: '100%', display: 'block' }} />
                </div>
              )}
              {!camReady && (
                <div style={{
                  display: 'flex', alignItems: 'center', gap: 6, padding: '8px 10px', marginBottom: 8,
                  background: T.bg2 || '#f3f4f6', borderRadius: 8, border: `1px solid ${T.line}`,
                }}>
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke={camError ? '#dc2626' : '#999'} strokeWidth="2"><path d="M23 7l-7 5 7 5V7z"/><rect x="1" y="5" width="15" height="14" rx="2" ry="2"/></svg>
                  <span style={{ fontSize: 11, color: camError ? '#dc2626' : '#999', fontWeight: camError ? 700 : 500 }}>
                    {camError ? 'Camera blocked' : 'Camera unavailable'}
                  </span>
                </div>
              )}

              {/* Camera-required warning — this test needs webcam monitoring */}
              {camError && (
                <div style={{ marginTop: 8, padding: '8px 10px', background: T.dangerSoft, border: `1px solid ${T.danger}55`, borderRadius: 8 }}>
                  <div style={{ fontSize: 11, fontWeight: 700, color: T.danger, marginBottom: 2 }}>Camera access is required</div>
                  <div style={{ fontSize: 10, color: T.ink2 || T.ink3 }}>
                    This test is proctored. Please allow camera & microphone access in your browser, then reload the page to continue.
                  </div>
                </div>
              )}

              {/* Face Match Status — Checking / Verified / Unverified (no ref photo) / Mismatch.
                  A stale feed also renders neutral: never show a confident verdict from
                  data that stopped updating. */}
              {(() => {
                const { stale, pending, verified, unverified, noFace, mismatch } = faceState
                const C = pending    ? { fg: T.ink3,    bg: T.bg2 || '#f3f4f6', bd: (T.ink3 || '#9ca3af') + '40' }
                        : verified   ? { fg: T.ok,      bg: T.okSoft,     bd: T.ok + '40' }
                        : unverified ? { fg: '#b45309', bg: '#fef3c7',    bd: '#f59e0b66' }
                        : mismatch   ? { fg: T.danger,  bg: T.dangerSoft, bd: T.danger + '40' }
                        :              { fg: '#b45309', bg: '#fef3c7',    bd: '#f59e0b66' }
                const label = pending ? (stale ? `Reconnecting… (${Math.round(analysisAge / 1000)}s)` : 'Checking…')
                            : verified ? 'Matches your photo'
                            : unverified ? 'No photo to compare'
                            : noFace ? 'No Face'
                            : mismatch ? 'Face Mismatch!' : 'Re-checking…'
                return (
                  <div style={{
                    display: 'flex', alignItems: 'center', gap: 6, padding: '9px 10px',
                    background: C.bg, borderRadius: 8, border: `1px solid ${C.bd}`
                  }}>
                    {/* Camera glyph — with no preview, this is what tells the candidate
                        the webcam is what is being checked. */}
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke={C.fg} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <path d="M23 7l-7 5 7 5V7z"/><rect x="1" y="5" width="15" height="14" rx="2" ry="2"/>
                    </svg>
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke={C.fg} strokeWidth="2.5">
                      {verified
                        ? <polyline points="20 6 9 17 4 12"/>
                        : mismatch
                          ? <><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></>
                          : <><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></>}
                    </svg>
                    <span style={{ fontFamily: T.sans, fontSize: 12, fontWeight: 700, color: T.ink, flex: 1 }}>
                      {label}
                    </span>
                  </div>
                )
              })()}

              {/* Gaze Direction + Lip Movement Indicators — hidden */}
              {/* <div style={{ display: 'flex', gap: 4, marginTop: 4 }}>
                <div style={{
                  flex: 1, display: 'flex', alignItems: 'center', gap: 4, padding: '4px 6px',
                  background: faceMatch.gaze === 'LEFT' || faceMatch.gaze === 'RIGHT' ? '#fff7ed' : '#f0fdf4',
                  borderRadius: 5, border: `1px solid ${faceMatch.gaze === 'CENTER' ? '#bbf7d0' : '#fed7aa'}`,
                }}>
                  <span style={{ fontSize: 10, fontWeight: 600, color: faceMatch.gaze === 'CENTER' ? '#15803d' : '#ea580c' }}>
                    {faceMatch.gaze === 'LEFT' ? '◄ L' : faceMatch.gaze === 'RIGHT' ? 'R ►' : '● C'}
                  </span>
                  <span style={{ fontSize: 8, color: '#888' }}>Gaze</span>
                </div>
                {faceMatch.lipMoving && (
                  <div style={{
                    display: 'flex', alignItems: 'center', gap: 3, padding: '4px 6px',
                    background: '#fef2f2', borderRadius: 5, border: '1px solid #fca5a5',
                  }}>
                    <span style={{ fontSize: 10, fontWeight: 700, color: '#dc2626' }}>SPEAKING</span>
                  </div>
                )}
              </div> */}

              {/* Head Turn Alert (sidebar) — hidden */}
              {/* {(faceMatch.headTurned || headTurnCount > 0) && (
                <div style={{
                  display: 'flex', alignItems: 'center', gap: 6, padding: '5px 8px', marginTop: 4,
                  background: faceMatch.headTurned ? '#dc2626' : '#fff7ed', borderRadius: 6,
                  border: `1px solid ${faceMatch.headTurned ? '#dc2626' : '#fed7aa'}`,
                  animation: faceMatch.headTurned ? 'blink 0.8s infinite' : 'none',
                }}>
                  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke={faceMatch.headTurned ? '#fff' : '#ea580c'} strokeWidth="2.5">
                    <path d="M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20z"/><path d="M8 12h8"/><path d="M12 8l4 4-4 4"/>
                  </svg>
                  <span style={{ fontSize: 10, fontWeight: 700, color: faceMatch.headTurned ? '#fff' : '#ea580c', flex: 1 }}>
                    {faceMatch.headTurned ? `HEAD TURNED (${Math.abs(Math.round(faceMatch.yaw || 0))}°)` : 'Head turns detected'}
                  </span>
                  <span style={{
                    fontSize: 10, fontWeight: 800, fontFamily: "'JetBrains Mono',monospace",
                    color: '#fff', background: headTurnCount >= 5 ? '#dc2626' : '#ea580c',
                    borderRadius: 4, padding: '0 5px', minWidth: 16, textAlign: 'center',
                  }}>{headTurnCount}</span>
                </div>
              )} */}

              {/* Multiple Faces Alert (sidebar) — hidden */}
              {/* {(faceMatch.count > 1 || multiFaceCount > 0) && (
                <div style={{
                  display: 'flex', alignItems: 'center', gap: 6, padding: '5px 8px', marginTop: 4,
                  background: faceMatch.count > 1 ? '#dc2626' : '#fef2f2', borderRadius: 6,
                  border: '1px solid #fca5a5',
                  animation: faceMatch.count > 1 ? 'blink 0.8s infinite' : 'none',
                }}>
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke={faceMatch.count > 1 ? '#fff' : '#dc2626'} strokeWidth="2.5">
                    <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/>
                    <path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/>
                  </svg>
                  <span style={{ fontSize: 10, fontWeight: 700, color: faceMatch.count > 1 ? '#fff' : '#dc2626', flex: 1 }}>
                    {faceMatch.count > 1 ? `${faceMatch.count} FACES NOW!` : 'Extra faces detected'}
                  </span>
                  <span style={{
                    fontSize: 10, fontWeight: 800, fontFamily: "'JetBrains Mono',monospace",
                    color: '#fff', background: '#dc2626', borderRadius: 4,
                    padding: '0 5px', minWidth: 16, textAlign: 'center',
                  }}>
                    {multiFaceCount}
                  </span>
                </div>
              )} */}

              {/* Assessment Integrity Flags Bar — hidden */}
              {/* <div style={{ marginTop: 6 }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 3 }}>
                  <span style={{ fontSize: 10, fontWeight: 600, color: '#555' }}>Trust Score</span>
                  <span style={{
                    fontSize: 10, fontWeight: 700, fontFamily: "'JetBrains Mono',monospace",
                    padding: '1px 6px', borderRadius: 3,
                    color: cheatingDecision === 'NORMAL' ? '#15803d' : cheatingDecision === 'WARNING' ? '#92400e' : '#dc2626',
                    background: cheatingDecision === 'NORMAL' ? '#dcfce7' : cheatingDecision === 'WARNING' ? '#fef3c7' : '#fee2e2',
                  }}>
                    {cheatingDecision}
                  </span>
                </div>
                <div style={{ height: 6, borderRadius: 3, background: '#e5e7eb', overflow: 'hidden' }}>
                  <div style={{
                    height: '100%', borderRadius: 3, transition: 'width 0.3s, background 0.3s',
                    width: `${Math.min(cheatingScore, 100)}%`,
                    background: cheatingScore <= 30 ? '#22c55e' : cheatingScore <= 60 ? '#f59e0b' : cheatingScore <= 80 ? '#f97316' : '#ef4444',
                  }} />
                </div>
              </div> */}

              {/* Active Flags — hidden from candidate view */}
              {/* {proctorFlags.length > 0 && (
                <div style={{ marginTop: 5, display: 'flex', flexWrap: 'wrap', gap: 3 }}>
                  {proctorFlags.slice(0, 4).map((flag, i) => (
                    <span key={i} style={{
                      fontSize: 8, fontWeight: 700, fontFamily: "'JetBrains Mono',monospace",
                      padding: '1px 5px', borderRadius: 3, whiteSpace: 'nowrap',
                      color: '#fff',
                      background: ['IDENTITY_MISMATCH','PHONE_DETECTED','CANDIDATE_LEFT','NO_FACE'].includes(flag) ? '#dc2626'
                        : ['MULTIPLE_FACES','HEAD_TURNED','MULTIPLE_VOICES'].includes(flag) ? '#ea580c' : '#6b7280',
                    }}>{flag.replace(/_/g, ' ')}</span>
                  ))}
                </div>
              )} */}

              {/* Tab Switch Counter — visible deterrent (the score itself stays HR-only) */}
              {tabSwitchCount > 0 && (
                <div style={{
                  display: 'flex', alignItems: 'center', gap: 6, padding: '5px 8px', marginTop: 4,
                  background: tabSwitchWarning ? '#fef2f2' : '#fff7ed', borderRadius: 6,
                  border: `1px solid ${tabSwitchWarning ? '#fca5a5' : '#fed7aa'}`,
                  transition: 'all 0.3s',
                }}>
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke={tabSwitchCount >= 3 ? '#dc2626' : '#ea580c'} strokeWidth="2.5">
                    <rect x="2" y="3" width="20" height="14" rx="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/>
                  </svg>
                  <span style={{ fontSize: 10, fontWeight: 700, color: tabSwitchCount >= 3 ? '#dc2626' : '#ea580c', flex: 1 }}>
                    Tab Switches
                  </span>
                  <span style={{
                    fontSize: 11, fontWeight: 800, fontFamily: "'JetBrains Mono',monospace",
                    color: '#fff', background: tabSwitchCount >= 3 ? '#dc2626' : tabSwitchCount >= 2 ? '#ea580c' : '#f59e0b',
                    borderRadius: 4, padding: '0 6px', minWidth: 18, textAlign: 'center',
                  }}>
                    {tabSwitchCount}
                  </span>
                </div>
              )}

              {/* Tab Switch Warning Flash */}
              {tabSwitchWarning && (
                <div style={{
                  marginTop: 4, fontSize: 9, fontWeight: 700, color: '#dc2626', textAlign: 'center',
                  padding: '4px 6px', background: '#fef2f2', borderRadius: 4, border: '1px solid #fca5a5',
                  animation: 'blink 0.5s 3',
                }}>
                  Tab switch detected! This is recorded.
                </div>
              )}

              {/* Mic level */}
              <div style={{ ...x.micRow, marginTop: 8 }}>
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke={micLevel > 5 ? T.ok : T.ink3} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2"/></svg>
                <div style={x.micTrack}>
                  <div style={{ ...x.micFill, width: `${micLevel}%`, background: micLevel > 60 ? T.ok : micLevel > 25 ? T.tan : T.ink3 }} />
                </div>
              </div>

              {/* Face Registration Status — shown only when live analysis confirms there is
                  genuinely no reference photo (mirrors the badge's `unverified` state), so
                  once a reference exists / the face is verified (faceMatch.hasReference) it
                  stays hidden. While enrollment is still retrying we show the server's actual
                  reason via enrollHint ("too dark", "move closer") — actionable, unlike the
                  old blanket "no application photo" message. */}
              {!faceRegistered && camReady && faceMatch.detected && !faceMatch.hasReference && (
                <div style={{ marginTop: 8, fontFamily: T.sans, fontSize: 9.5, color: T.danger, fontWeight: 600, textAlign: 'center', padding: '4px 6px', background: T.dangerSoft, borderRadius: 6 }}>
                  {enrollHint || 'No application photo on file, so your face can’t be compared with one'}
                </div>
              )}
            </div>
            )}

            {Object.entries(sections).map(([name, qs]) => (
              <div key={name} style={x.palCard}>
                <div style={x.palHead}>{name}</div>
                <div style={x.palGrid}>
                  {qs.map(({ gi }) => {
                    const st = paletteStatus(gi)
                    return (
                      <button key={gi} className="ot-pal" onClick={() => goTo(gi)} style={{
                        ...x.palBtn,
                        background: S_BG[st], color: S_FG[st], border: `1.5px solid ${S_BD[st]}`,
                        boxShadow: gi === idx ? `0 0 0 2px ${T.paper}, 0 0 0 4px ${T.teal}` : 'none',
                      }}>{gi + 1}</button>
                    )
                  })}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* ═══ HEAD TURN ALERT ═══ */}
      {headTurnAlert && (
        <div style={{
          position: 'fixed', inset: 0, zIndex: 10001, pointerEvents: 'none',
          background: 'rgba(234,88,12,0.08)', borderTop: '3px solid #ea580c', borderBottom: '3px solid #ea580c',
        }}>
          <div style={{
            position: 'absolute', top: 16, left: '50%', transform: 'translateX(-50%)',
            background: '#ea580c', color: '#fff', padding: '8px 28px', borderRadius: 10,
            boxShadow: '0 4px 20px rgba(234,88,12,0.4)', display: 'flex', alignItems: 'center', gap: 10,
            animation: 'slideDown 0.3s ease-out', pointerEvents: 'auto',
          }}>
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#fff" strokeWidth="2.5">
              <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/>
              <line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/>
            </svg>
            <div>
              <div style={{ fontSize: 14, fontWeight: 800 }}>HEAD TURNED — Look at the screen!</div>
              <div style={{ fontSize: 10, opacity: 0.9, marginTop: 1 }}>Face angle violation recorded (#{headTurnCount})</div>
            </div>
          </div>
        </div>
      )}

      {/* ═══ MULTIPLE FACES RED ALERT ═══ */}
      {multiFaceAlert && (
        <div style={{
          position: 'fixed', inset: 0, zIndex: 10001,
          background: 'rgba(220, 38, 38, 0.12)',
          border: '4px solid #dc2626',
          pointerEvents: 'none',
          animation: 'redFlash 0.6s ease-in-out',
        }}>
          <div style={{
            position: 'absolute', top: 18, left: '50%', transform: 'translateX(-50%)',
            background: '#dc2626', color: '#fff', padding: '10px 32px',
            borderRadius: 10, boxShadow: '0 4px 24px rgba(220,38,38,0.5)',
            display: 'flex', alignItems: 'center', gap: 10, pointerEvents: 'auto',
            animation: 'slideDown 0.3s ease-out',
          }}>
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="#fff" strokeWidth="2.5">
              <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/>
              <line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/>
            </svg>
            <div>
              <div style={{ fontSize: 15, fontWeight: 800, letterSpacing: '0.02em' }}>
                MULTIPLE FACES DETECTED — {faceMatch.count} faces!
              </div>
              <div style={{ fontSize: 11, fontWeight: 500, opacity: 0.9, marginTop: 2 }}>
                Only the candidate should be visible. This violation is recorded. (#{multiFaceCount})
              </div>
            </div>
          </div>
        </div>
      )}

      {/* ═══ CONFIRM MODAL ═══ */}
      {confirmOpen && (
        <div style={x.overlay}>
          <div style={x.modal}>
            <h3 style={x.modalTitle}>Submit Test?</h3>
            <p style={x.modalBody}>
              Answered <b>{Object.keys(answers).length}</b> of <b>{questions.length}</b>.
              {Object.keys(answers).length < questions.length && <span style={{ color: T.danger, fontWeight: 600 }}> {questions.length - Object.keys(answers).length} unanswered.</span>}
            </p>
            <div style={x.modalBtns}>
              <button style={x.modalCancel} onClick={() => setConfirmOpen(false)}>Continue Test</button>
              <button style={x.modalOk} onClick={() => { setConfirmOpen(false); submitTest() }}>Submit Now</button>
            </div>
          </div>
        </div>
      )}

      <style>{kf}</style>
    </div>
  )
}

const kf = `
@keyframes blink{0%,100%{opacity:1}50%{opacity:.4}}
@keyframes spin{to{transform:rotate(360deg)}}
@keyframes redFlash{0%{background:rgba(196,69,54,0.28)}50%{background:rgba(196,69,54,0.05)}100%{background:rgba(196,69,54,0.12)}}
@keyframes slideDown{from{opacity:0;transform:translateX(-50%) translateY(-20px)}to{opacity:1;transform:translateX(-50%) translateY(0)}}
@keyframes ot-fadeup{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:translateY(0)}}
@keyframes ot-pulse{0%,100%{opacity:1}50%{opacity:.45}}
textarea:focus{outline:none;}
.ot-opt{cursor:pointer}
.ot-opt:hover{border-color:#cfcabf!important;background:#faf9f7!important;transform:translateY(-1px)}
/* Clipped, not display:none — a hidden-by-display input is unfocusable and drops out of
   the accessibility tree, which would undo the whole point of using a real radio. */
.ot-opt__input{position:absolute;width:1px;height:1px;margin:-1px;padding:0;overflow:hidden;clip:rect(0 0 0 0);clip-path:inset(50%);white-space:nowrap;border:0}
/* The focus ring has to live on the row, since the input itself is 1px. */
.ot-opt:focus-within{outline:2px solid #2b7cba;outline-offset:2px}
@media (prefers-reduced-motion: reduce){.ot-opt:hover{transform:none}}
.ot-pal:hover{transform:translateY(-1px) scale(1.04)}
.ot-btn{transition:transform .12s cubic-bezier(0.16,1,0.3,1),box-shadow .2s,filter .2s}
.ot-btn:hover:not(:disabled){transform:translateY(-1px);filter:brightness(1.04)}
.ot-btn:active:not(:disabled){transform:translateY(0)}
.ot-tab{transition:color .18s}
`

/* ── Coding question styles — "Editorial Exam Hall" ── */
const c = {
  /* Problem pane (left) — ivory paper */
  problemPane: { width: '40%', minWidth: 360, display: 'flex', flexDirection: 'column', borderRight: `1px solid ${T.line}`, background: T.paper },
  probTabs: { display: 'flex', alignItems: 'center', gap: 4, padding: '0 18px', height: 46, borderBottom: `1px solid ${T.line}`, flexShrink: 0, background: T.paperAlt },
  probTabActive: { fontFamily: T.sans, fontSize: 13, fontWeight: 700, color: T.teal, padding: '13px 6px', borderBottom: `2px solid ${T.teal}`, marginBottom: -1, cursor: 'pointer' },
  probTab: { fontFamily: T.sans, fontSize: 13, fontWeight: 600, color: T.ink3, padding: '13px 6px', cursor: 'pointer' },
  probBody: { flex: 1, overflowY: 'auto', padding: '26px 30px' },
  probTitle: { fontFamily: T.serif, fontSize: 25, fontWeight: 600, color: T.ink, margin: '0 0 14px', lineHeight: 1.18, letterSpacing: '-0.01em' },
  probMeta: { display: 'flex', alignItems: 'center', gap: 8, marginBottom: 22 },
  tag: { fontFamily: T.sans, fontSize: 11, fontWeight: 700, padding: '3px 11px', borderRadius: 999, background: T.tealSoft, color: T.tealDeep, letterSpacing: '0.02em', textTransform: 'capitalize' },
  pts: { fontFamily: T.mono, fontSize: 12, fontWeight: 700, color: T.tan },

  section: { marginBottom: 20 },
  secTitle: { fontFamily: T.sans, fontSize: 11, fontWeight: 700, color: T.ink3, marginBottom: 7, textTransform: 'uppercase', letterSpacing: '0.08em' },
  secText: { fontFamily: T.sans, fontSize: 14.5, lineHeight: 1.75, color: T.ink2, whiteSpace: 'pre-wrap' },
  sampleBox: { fontFamily: T.mono, fontSize: 13, background: T.paperAlt, borderLeft: `3px solid ${T.teal}`, border: `1px solid ${T.line}`, borderRadius: '0 8px 8px 0', padding: '10px 14px', color: T.ink, marginTop: 4, whiteSpace: 'pre-wrap' },

  /* Editor pane (right) — warm charcoal */
  editorPane: { flex: 1, display: 'flex', flexDirection: 'column', background: T.edBg, minWidth: 0, borderTop: `2px solid ${T.teal}` },
  editorHeader: { display: 'flex', alignItems: 'center', gap: 14, padding: '0 16px', height: 44, background: T.edHead, borderBottom: `1px solid ${T.edLine}`, flexShrink: 0 },
  editorTitle: { fontFamily: T.sans, fontSize: 12, fontWeight: 700, color: T.edText, letterSpacing: '0.04em', textTransform: 'uppercase' },
  langSelect: { fontFamily: T.mono, fontSize: 11, fontWeight: 700, color: T.edText, background: '#332a24', border: `1px solid ${T.edLine}`, borderRadius: 6, padding: '5px 9px', cursor: 'pointer', outline: 'none' },
  timerBadge: { fontFamily: T.mono, fontSize: 12.5, fontWeight: 700, color: T.edAmber, background: 'rgba(224,160,90,0.10)', border: `1px solid rgba(224,160,90,0.30)`, borderRadius: 999, padding: '4px 13px', letterSpacing: '0.06em' },

  /* Code editor area */
  editorWrap: { flex: 1, display: 'flex', overflow: 'hidden', minHeight: 0 },
  lineNums: { width: 40, background: T.edBg, borderRight: `1px solid ${T.edLine}`, overflowY: 'hidden', paddingTop: 10, flexShrink: 0 },
  lineNum: { fontFamily: T.mono, fontSize: 12, color: T.edDim, textAlign: 'right', paddingRight: 8, lineHeight: '20px', height: 20 },
  codeInput: {
    flex: 1, padding: '12px 16px', background: T.edBg, color: T.edText,
    fontFamily: T.mono, fontSize: 13.5, lineHeight: '21px',
    border: 'none', resize: 'none', outline: 'none', boxSizing: 'border-box',
    tabSize: 4, whiteSpace: 'pre', overflowX: 'auto',
  },

  /* Test panel */
  testPanel: { height: 250, flexShrink: 0, borderTop: `1px solid ${T.edLine}`, background: T.paper, display: 'flex', flexDirection: 'column' },
  testTabs: { display: 'flex', alignItems: 'center', gap: 4, padding: '0 16px', height: 38, borderBottom: `1px solid ${T.line}`, flexShrink: 0, background: T.paperAlt },
  testTabActive: { fontFamily: T.sans, fontSize: 12, fontWeight: 700, color: T.teal, padding: '10px 6px', borderBottom: `2px solid ${T.teal}`, marginBottom: -1, cursor: 'pointer' },
  testTab: { fontFamily: T.sans, fontSize: 12, fontWeight: 600, color: T.ink3, padding: '10px 6px', cursor: 'pointer' },
  consoleLbl: { fontFamily: T.mono, fontSize: 11, fontWeight: 600, color: T.ink2, cursor: 'pointer' },
  testBody: { flex: 1, overflowY: 'auto', padding: '12px 16px' },
  caseRow: { display: 'flex', gap: 6, marginBottom: 10 },
  caseBadgeActive: { fontSize: 11, fontWeight: 700, padding: '3px 12px', borderRadius: 999, background: T.tealSoft, color: T.tealDeep, cursor: 'pointer' },
  caseBadge: { fontSize: 11, fontWeight: 500, padding: '3px 12px', borderRadius: 999, background: T.paper, color: T.ink3, border: `1px solid ${T.line}`, cursor: 'pointer' },
  ioBlock: { marginBottom: 10 },
  ioLabel: { fontFamily: T.sans, fontSize: 11, fontWeight: 700, color: T.ink3, marginBottom: 5, textTransform: 'uppercase', letterSpacing: '0.06em' },
  ioBox: { fontFamily: T.mono, fontSize: 13, background: T.paperAlt, border: `1px solid ${T.line}`, borderRadius: 8, padding: '9px 13px', color: T.ink, whiteSpace: 'pre-wrap' },
  resultPre: { fontFamily: T.mono, fontSize: 13, color: T.ink, margin: 0, whiteSpace: 'pre-wrap' },

  /* Custom input + rich result view */
  customInput: { width: '100%', minHeight: 56, boxSizing: 'border-box', fontFamily: T.mono, fontSize: 12.5, lineHeight: 1.5, padding: '9px 11px', border: `1px solid ${T.line}`, borderRadius: 8, resize: 'vertical', outline: 'none', color: T.ink, background: T.paperAlt },
  resultMsg: { fontFamily: T.sans, fontSize: 13, color: T.ink2, lineHeight: 1.6, whiteSpace: 'pre-wrap', padding: '6px 2px' },
  resultHeader: { display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12 },
  resultHeadTxt: { fontFamily: T.sans, fontSize: 12.5, fontWeight: 600, color: T.ink2 },
  resultScore: { fontFamily: T.mono, fontSize: 12, fontWeight: 700, color: T.ok },
  compileBox: { fontFamily: T.mono, fontSize: 11.5, background: T.dangerSoft, border: `1px solid ${T.danger}40`, borderRadius: 8, padding: '9px 11px', color: T.danger, whiteSpace: 'pre-wrap', margin: '0 0 10px', maxHeight: 120, overflow: 'auto' },
  caseTable: { display: 'flex', flexDirection: 'column' },
  caseTr: { display: 'grid', gridTemplateColumns: '1fr 0.9fr 0.9fr 0.8fr 0.8fr', alignItems: 'center', gap: 6, padding: '7px 4px', borderBottom: `1px solid ${T.line}`, fontFamily: T.sans, fontSize: 12, color: T.ink2 },
  caseTh: { fontFamily: T.sans, fontSize: 10.5, fontWeight: 700, color: T.ink3, textTransform: 'uppercase', letterSpacing: '0.05em', borderBottom: `1px solid ${T.line2}` },
  caseDetail: { padding: '8px 4px 12px', borderBottom: `1px solid ${T.line}`, background: T.paperAlt },
  ioLabelSm: { fontFamily: T.sans, fontSize: 11, fontWeight: 700, color: T.ink3, margin: '7px 0 3px', textTransform: 'uppercase', letterSpacing: '0.05em' },
  ioBoxSm: { fontFamily: T.mono, fontSize: 12, background: T.paper, border: `1px solid ${T.line}`, borderRadius: 6, padding: '7px 10px', color: T.ink, whiteSpace: 'pre-wrap', maxHeight: 110, overflow: 'auto' },

  /* Footer */
  editorFooter: { display: 'flex', alignItems: 'center', justifyContent: 'flex-end', gap: 10, padding: '10px 16px', background: T.paperAlt, borderTop: `1px solid ${T.line}`, flexShrink: 0 },
  runBtn: { padding: '9px 20px', borderRadius: 999, border: `1px solid ${T.line2}`, background: T.paper, color: T.ink, fontFamily: T.sans, fontSize: 13, fontWeight: 600, cursor: 'pointer' },
  runTestsBtn: { padding: '9px 18px', borderRadius: 999, border: `1px solid ${T.teal}`, background: T.tealSoft, color: T.tealDeep, fontFamily: T.sans, fontSize: 13, fontWeight: 700, cursor: 'pointer' },
  submitCodeBtn: { padding: '9px 24px', borderRadius: 999, border: 'none', background: T.teal, color: '#fff', fontFamily: T.sans, fontSize: 13, fontWeight: 700, cursor: 'pointer', boxShadow: T.shSm },
  btnDisabled: { opacity: 0.45, cursor: 'not-allowed' },
  runsLeftLbl: { fontFamily: T.mono, fontSize: 11.5, fontWeight: 600, color: T.ink2, marginRight: 'auto' },
  judgeNotice: { display: 'flex', alignItems: 'center', gap: 8, padding: '9px 16px', background: T.warnSoft, borderTop: `1px solid ${T.tan}55`, color: '#8a5a22', fontFamily: T.sans, fontSize: 12.5, fontWeight: 600, flexShrink: 0 },

  /* Submissions tab */
  subEmpty: { fontFamily: T.sans, fontSize: 13, color: T.ink3, padding: '12px 2px', lineHeight: 1.6 },
  subList: { display: 'flex', flexDirection: 'column' },
  subRow: { display: 'grid', gridTemplateColumns: '1.6fr 0.9fr 0.9fr 0.9fr 1fr', alignItems: 'start', gap: 8, padding: '11px 4px', borderBottom: `1px solid ${T.line}` },
  subHead: { borderBottom: `1px solid ${T.line2}`, fontSize: 11, fontWeight: 700, color: T.ink3, textTransform: 'uppercase', letterSpacing: '0.05em' },
  subCell: { fontFamily: T.sans, fontSize: 12.5, color: T.ink2, minWidth: 0, wordBreak: 'break-word' },
  subBadge: { display: 'inline-block', fontFamily: T.mono, fontSize: 11, fontWeight: 700, padding: '2px 9px', borderRadius: 999 },
  subLatestPill: { display: 'inline-block', marginLeft: 8, fontFamily: T.sans, fontSize: 10, fontWeight: 700, padding: '2px 8px', borderRadius: 999, background: T.tealSoft, color: T.tealDeep },
  subNote: { fontFamily: T.sans, fontSize: 11, color: T.ink3, marginTop: 4 },
}

const V_COLORS = {
  AC: { bg: T.okSoft, fg: T.ok }, WA: { bg: T.dangerSoft, fg: T.danger },
  TLE: { bg: T.warnSoft, fg: T.tan }, MLE: { bg: T.warnSoft, fg: T.tan },
  RE: { bg: T.purpleSoft, fg: T.purple }, CE: { bg: T.purpleSoft, fg: T.purple },
}

function SubmissionsList({ items }) {
  if (!items.length) {
    return <div style={c.subEmpty}>No submissions yet — click <b>Submit</b> to evaluate your code. Each attempt is recorded here.</div>
  }
  const rows = [...items].reverse() // newest first
  return (
    <div style={c.subList}>
      <div style={{ ...c.subRow, ...c.subHead }}>
        <span style={c.subCell}>Status</span>
        <span style={c.subCell}>Score</span>
        <span style={c.subCell}>Cases</span>
        <span style={c.subCell}>Lang</span>
        <span style={c.subCell}>Time</span>
      </div>
      {rows.map((s, i) => {
        const vc = s.verdict ? (V_COLORS[s.verdict] || { bg: T.hover, fg: T.ink3 }) : null
        return (
          <div key={s.ts} style={c.subRow}>
            <span style={c.subCell}>
              {s.unavailable
                ? <span style={{ ...c.subBadge, background: T.warnSoft, color: '#8a5a22' }}>Pending</span>
                : vc
                  ? <span style={{ ...c.subBadge, background: vc.bg, color: vc.fg }}>{s.verdict}</span>
                  : <span style={{ ...c.subBadge, background: T.hover, color: T.ink3 }}>—</span>}
              {i === 0 && <span style={c.subLatestPill}>{vc ? 'Latest · counts' : 'Latest'}</span>}
              {s.unavailable && <div style={c.subNote}>Auto-grade offline — graded on final submit</div>}
              {s.note && !s.unavailable && <div style={c.subNote}>{s.note}</div>}
            </span>
            <span style={c.subCell}>{vc ? `${s.points_awarded}/${s.max_points}` : '—'}</span>
            <span style={c.subCell}>{vc ? `${s.passed}/${s.total}` : '—'}</span>
            <span style={c.subCell}>{(s.language || '').toUpperCase()}</span>
            <span style={c.subCell}>{new Date(s.ts).toLocaleTimeString()}</span>
          </div>
        )
      })}
    </div>
  )
}


// Per-test-case verdict table for a graded "Run Tests". Sample cases expand to
// show Input / Expected / Your Output (+ stderr); hidden cases show only the
// verdict + time/memory (the backend withholds hidden I/O).
function CaseTable({ data, samples }) {
  const cases = data.cases || []
  const allPassed = data.verdict === 'AC'
  let sampleSeen = -1
  return (
    <div>
      <div style={c.resultHeader}>
        <span style={{ ...c.subBadge, background: allPassed ? T.okSoft : T.dangerSoft, color: allPassed ? T.ok : T.danger, fontSize: 12, fontWeight: 700 }}>{allPassed ? 'PASSED' : 'FAILED'}</span>
        <span style={c.resultHeadTxt}>{data.passed}/{data.total} test cases passed</span>
        <span style={{ flex: 1 }} />
        <span style={c.resultScore}>Score {data.points_awarded}/{data.max_points}</span>
      </div>
      {data.compile_output && <pre style={c.compileBox}>{data.compile_output}</pre>}
      {cases.length === 0 && !data.compile_output && (
        data.error_message
          ? <pre style={c.compileBox}>{data.error_message}</pre>
          : <div style={{ color: T.ink3, fontFamily: T.sans, fontSize: 13, padding: '6px 2px' }}>This question has no test cases.</div>
      )}
      <div style={c.caseTable}>
        {cases.length > 0 && (
          <div style={{ ...c.caseTr, ...c.caseTh }}>
            <span>#</span><span>Type</span><span>Result</span><span>Time</span><span>Memory</span>
          </div>
        )}
        {cases.map((cc, i) => {
          const passed = cc.verdict === 'AC'
          const isSample = !!cc.is_sample
          if (isSample) sampleSeen += 1
          const sample = isSample ? samples[sampleSeen] : null
          return (
            <div key={i}>
              <div style={c.caseTr}>
                <span>Case {cc.ordinal + 1}</span>
                <span style={{ color: isSample ? T.teal : T.ink3, fontWeight: 600 }}>{isSample ? 'Sample' : 'Hidden'}</span>
                <span>
                  <span style={{ ...c.subBadge, background: passed ? T.okSoft : T.dangerSoft, color: passed ? T.ok : T.danger, fontSize: 10.5, fontWeight: 700 }}>{passed ? 'PASS' : 'FAIL'}</span>
                  {!passed && cc.verdict && <span style={{ marginLeft: 6, color: T.ink3, fontSize: 10 }}>{cc.verdict}</span>}
                </span>
                <span>{cc.time_ms != null ? `${cc.time_ms} ms` : '—'}</span>
                <span>{cc.memory_kb != null ? `${Math.max(1, Math.round(cc.memory_kb / 1024))} MB` : '—'}</span>
              </div>
              {isSample && (
                <div style={c.caseDetail}>
                  {sample?.input ? (<><div style={c.ioLabelSm}>Input</div><div style={c.ioBoxSm}>{sample.input}</div></>) : null}
                  {sample?.expected_output != null ? (<><div style={c.ioLabelSm}>Expected</div><div style={c.ioBoxSm}>{sample.expected_output}</div></>) : null}
                  <div style={c.ioLabelSm}>Your Output</div>
                  <div style={{ ...c.ioBoxSm, background: cc.verdict === 'AC' ? T.okSoft : T.dangerSoft }}>{cc.stdout ?? '(no output)'}</div>
                  {cc.stderr ? (<><div style={c.ioLabelSm}>Error</div><div style={{ ...c.ioBoxSm, color: T.danger }}>{cc.stderr}</div></>) : null}
                </div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}

// Raw output of a free "Run Code" against custom input (no scoring).
function RunOutputView({ data }) {
  const isErr = !!data.compile_error || (data.status === 'unavailable') || (data.exit_code !== 0 && !data.timed_out)
  const label = data.compile_error ? 'Compile Error'
    : data.status === 'unavailable' ? 'Unavailable'
    : data.timed_out ? 'Timed Out'
    : data.exit_code === 0 ? 'Finished' : `Exit ${data.exit_code}`
  return (
    <div>
      <div style={c.resultHeader}>
        <span style={{ ...c.subBadge, background: isErr ? T.dangerSoft : T.okSoft, color: isErr ? T.danger : T.ok, fontSize: 12 }}>{label}</span>
        <span style={c.resultHeadTxt}>Run Code (not graded)</span>
        <span style={{ flex: 1 }} />
        {data.time_ms != null && <span style={c.resultScore}>{data.time_ms} ms</span>}
      </div>
      {data.compile_error && (<><div style={c.ioLabelSm}>Compiler</div><pre style={c.compileBox}>{data.compile_error}</pre></>)}
      {data.status === 'unavailable' && <div style={c.resultMsg}>{data.error_message || 'Judge unavailable.'}</div>}
      {data.seeded_from_sample && <div style={{ ...c.ioLabelSm, color: T.teal }}>Ran against the sample dataset shown in the problem (add Custom Input to use your own data).</div>}
      <div style={c.ioLabelSm}>Output (stdout)</div>
      <div style={c.ioBoxSm}>{data.stdout || '(no output)'}</div>
      {data.stderr && (<><div style={c.ioLabelSm}>Errors (stderr)</div><div style={{ ...c.ioBoxSm, color: T.danger }}>{data.stderr}</div></>)}
    </div>
  )
}

/* ── MCQ / shell styles — "Editorial Exam Hall" ── */
const x = {
  root: { position: 'fixed', inset: 0, zIndex: 9999, display: 'flex', flexDirection: 'column', background: T.canvas, fontFamily: T.sans, color: T.ink, overflow: 'hidden' },

  /* Header — slim editorial bar */
  header: { height: 56, background: T.paper, borderBottom: `1px solid ${T.line}`, display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0, boxShadow: T.shSm, position: 'relative' },
  headerTxt: { fontFamily: T.serif, fontSize: 19, fontWeight: 600, color: T.ink, letterSpacing: '-0.01em' },

  /* Body */
  body: { display: 'flex', flex: 1, overflow: 'hidden' },

  /* Left */
  left: { flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' },
  qCard: { flex: 1, margin: '18px 14px 0 20px', padding: '34px 42px', background: T.paper, borderRadius: 16, border: `1px solid ${T.line}`, boxShadow: T.shMd, overflowY: 'auto' },
  qTop: { display: 'flex', alignItems: 'center', gap: 10, marginBottom: 0 },
  qKicker: { fontFamily: T.sans, fontSize: 11, fontWeight: 700, color: T.teal, textTransform: 'uppercase', letterSpacing: '0.12em' },
  qSec: { fontFamily: T.serif, fontSize: 27, fontWeight: 600, color: T.ink, lineHeight: 1.15, letterSpacing: '-0.015em', margin: '6px 0 0' },
  qDivider: { height: 1, background: T.line, margin: '18px 0 22px' },
  qText: { fontFamily: T.sans, fontSize: 16, lineHeight: 1.75, color: T.ink2, margin: '0 0 26px', whiteSpace: 'pre-wrap' },

  /* Options — selectable paper cards */
  opts: { display: 'flex', flexDirection: 'column', gap: 11 },
  optRow: { display: 'flex', alignItems: 'center', gap: 14, cursor: 'pointer', padding: '14px 18px', border: `1px solid ${T.line}`, borderRadius: 12, background: T.paper, transition: `all .18s ${T.ease}` },
  optRowSel: { borderColor: T.teal, background: T.tealSoft, boxShadow: `inset 0 0 0 1px ${T.teal}` },
  optBox: { width: 24, height: 24, borderRadius: 7, border: `2px solid ${T.line2}`, background: T.paper, display: 'grid', placeItems: 'center', flexShrink: 0, transition: `all .18s ${T.ease}` },
  optBoxSel: { background: T.teal, borderColor: T.teal },
  optLetter: { fontFamily: T.mono, fontSize: 13, fontWeight: 700, color: T.ink3, minWidth: 18 },
  optLetterSel: { color: T.teal },
  optText: { fontFamily: T.sans, fontSize: 15, color: T.ink, lineHeight: 1.5 },

  /* Text areas */
  codeArea: { width: '100%', padding: 14, borderRadius: 10, border: `1px solid ${T.edLine}`, background: T.edBg, color: T.edText, fontFamily: T.mono, fontSize: 13, lineHeight: 1.5, resize: 'vertical', outline: 'none', boxSizing: 'border-box' },
  textArea: { width: '100%', padding: 16, borderRadius: 12, border: `1px solid ${T.line2}`, background: T.paperAlt, color: T.ink, fontFamily: T.sans, fontSize: 15, lineHeight: 1.6, resize: 'vertical', outline: 'none', boxSizing: 'border-box' },

  /* Bottom bar */
  bottomBar: { display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '14px 20px', flexShrink: 0 },
  navBtns: { display: 'flex', gap: 8 },
  btnReview: { padding: '9px 18px', borderRadius: 999, border: `1px solid ${T.tan}`, background: T.tanSoft, color: '#8a5a22', fontFamily: T.sans, fontSize: 13, fontWeight: 700, cursor: 'pointer' },
  btnPrev: { padding: '9px 18px', borderRadius: 999, border: `1px solid ${T.line2}`, background: T.paper, color: T.ink, fontFamily: T.sans, fontSize: 13, fontWeight: 600, cursor: 'pointer' },
  btnNext: { padding: '9px 18px', borderRadius: 999, border: `1px solid ${T.teal}`, background: T.tealSoft, color: T.tealDeep, fontFamily: T.sans, fontSize: 13, fontWeight: 700, cursor: 'pointer' },
  btnSubmit: { padding: '10px 26px', borderRadius: 999, border: 'none', background: T.teal, color: '#fff', fontFamily: T.sans, fontSize: 13, fontWeight: 700, cursor: 'pointer', boxShadow: T.shSm },

  /* Legend */
  legend: { display: 'flex', gap: 20, flexWrap: 'wrap', justifyContent: 'center', padding: '12px 20px 14px', flexShrink: 0, borderTop: `1px solid ${T.line}`, background: T.paper },
  legItem: { display: 'flex', alignItems: 'center', gap: 6 },
  legDot: { width: 11, height: 11, borderRadius: '50%', flexShrink: 0 },
  legLabel: { fontFamily: T.sans, fontSize: 11.5, color: T.ink2, fontWeight: 600 },

  /* Right sidebar */
  right: { width: 280, flexShrink: 0, display: 'flex', flexDirection: 'column', gap: 0, overflowY: 'auto', background: T.paper, borderLeft: `1px solid ${T.line}` },

  /* Timer — focal editorial element */
  timerCard: { padding: '20px 18px 18px', borderBottom: `1px solid ${T.line}`, textAlign: 'center', background: T.paperAlt },
  timerHead: { fontFamily: T.sans, fontSize: 11, fontWeight: 700, color: T.ink3, marginBottom: 14, textTransform: 'uppercase', letterSpacing: '0.12em' },
  timerRow: { display: 'flex', justifyContent: 'center', alignItems: 'flex-start', gap: 6 },
  timerUnit: { display: 'flex', flexDirection: 'column', alignItems: 'center', minWidth: 52 },
  timerNum: { fontFamily: T.mono, fontSize: 34, fontWeight: 700, lineHeight: 1, color: T.ink, fontVariantNumeric: 'tabular-nums' },
  timerColon: { fontFamily: T.mono, fontSize: 30, fontWeight: 700, color: T.ink3, lineHeight: 1, paddingTop: 2 },
  timerLabel: { fontFamily: T.sans, fontSize: 9.5, color: T.ink3, marginTop: 7, textTransform: 'uppercase', letterSpacing: '0.1em' },
  timerWarn: { marginTop: 14, fontFamily: T.sans, fontSize: 11, fontWeight: 700, color: T.danger, background: T.dangerSoft, padding: '5px 12px', borderRadius: 999, display: 'inline-block' },

  /* Palette */
  palCard: { padding: '16px 16px', borderBottom: `1px solid ${T.line}` },
  palHead: { fontFamily: T.sans, fontSize: 11, fontWeight: 700, color: T.ink3, textAlign: 'left', marginBottom: 12, textTransform: 'uppercase', letterSpacing: '0.12em' },
  palGrid: { display: 'grid', gridTemplateColumns: 'repeat(6, 1fr)', gap: 7 },
  palBtn: { aspectRatio: '1', borderRadius: 9, display: 'grid', placeItems: 'center', fontFamily: T.mono, fontSize: 12, cursor: 'pointer', fontWeight: 700, transition: `transform .12s ${T.ease}` },

  /* Proctoring */
  proctorCard: { padding: '16px 16px', borderBottom: `1px solid ${T.line}` },
  proctorHead: { display: 'flex', alignItems: 'center', gap: 7, marginBottom: 10 },
  micRow: { display: 'flex', alignItems: 'center', gap: 6 },
  micTrack: { flex: 1, height: 6, borderRadius: 999, background: T.line, overflow: 'hidden' },
  micFill: { height: '100%', borderRadius: 999, transition: 'width .1s ease' },

  /* Screen states */
  screen: { position: 'fixed', inset: 0, zIndex: 9999, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', background: T.canvas },
  spin: { width: 32, height: 32, borderRadius: '50%', border: `3px solid ${T.line}`, borderTopColor: T.teal, animation: 'spin .7s linear infinite' },
  doneCard: { textAlign: 'center', maxWidth: 560, padding: '46px 54px', background: T.paper, borderRadius: 18, border: `1px solid ${T.line}`, boxShadow: T.shLg },
  doneCheck: { width: 68, height: 68, borderRadius: '50%', background: T.okSoft, display: 'grid', placeItems: 'center', margin: '0 auto 18px' },
  doneTitle: { fontFamily: T.serif, fontSize: 24, fontWeight: 600, color: T.ink, margin: '0 0 8px' },
  doneSub: { fontFamily: T.sans, fontSize: 14, color: T.ink2, margin: '0 0 26px' },
  doneBtn: { padding: '11px 28px', borderRadius: 999, border: 'none', background: T.teal, color: '#fff', fontFamily: T.sans, fontSize: 14, fontWeight: 700, cursor: 'pointer' },

  /* Modal */
  overlay: { position: 'fixed', inset: 0, zIndex: 10000, background: 'rgba(28,25,23,.45)', backdropFilter: 'blur(3px)', display: 'grid', placeItems: 'center' },
  modal: { background: T.paper, borderRadius: 16, padding: '30px 34px', maxWidth: 400, width: '90%', boxShadow: T.shLg, border: `1px solid ${T.line}` },
  modalTitle: { fontFamily: T.serif, fontSize: 20, fontWeight: 600, color: T.ink, margin: '0 0 10px' },
  modalBody: { fontFamily: T.sans, fontSize: 14, color: T.ink2, lineHeight: 1.6, margin: '0 0 22px' },
  modalBtns: { display: 'flex', gap: 10, justifyContent: 'flex-end' },
  modalCancel: { padding: '9px 20px', borderRadius: 999, border: `1px solid ${T.line2}`, background: T.paper, color: T.ink2, fontFamily: T.sans, fontSize: 13, fontWeight: 600, cursor: 'pointer' },
  modalOk: { padding: '9px 20px', borderRadius: 999, border: 'none', background: T.teal, color: '#fff', fontFamily: T.sans, fontSize: 13, fontWeight: 700, cursor: 'pointer' },
}
