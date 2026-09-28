import { useState, useEffect, useRef, useCallback } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import api from '../../api/client'
import { isSupported as fullscreenSupported, requestFullscreen } from '../../utils/fullscreen'
import {
  STATUS, isSatisfied, checkBrowser, checkServer, checkProctorSocket,
  sampleVideoBrightness, classifyCamera, classifyMicrophone, MIC_HEARD_LEVEL, buildSignalPath,
} from '../../utils/systemCheck'
import SignalPathCheck from '../../components/SignalPathCheck'
import ExplanationForm from '../../components/ExplanationForm'

export default function TestInstructions() {
  const { interviewId } = useParams()
  const navigate = useNavigate()
  const [interview, setInterview] = useState(null)
  const [alreadySubmitted, setAlreadySubmitted] = useState(false)
  const [qCount, setQCount] = useState(0)
  const [totalTime, setTotalTime] = useState(0)
  const [loading, setLoading] = useState(true)
  const [agreed, setAgreed] = useState(false)
  // Separate from `agreed` on purpose: agreeing to the test rules is not agreeing to be
  // recorded. A proctored test needs both.
  const [consented, setConsented] = useState(false)

  // Camera & Mic state
  const [camStatus, setCamStatus] = useState('idle')   // idle | checking | granted | denied | error | ended
  const [micStatus, setMicStatus] = useState('idle')
  const [micLevel, setMicLevel] = useState(0)
  const [camMeta, setCamMeta] = useState({ measured: false })  // picture actually arriving, and how bright
  const [micMuted, setMicMuted] = useState(false)
  const [micHeard, setMicHeard] = useState(false)
  const micHeardRef = useRef(false)
  const camTimerRef = useRef(null)
  const videoRef = useRef(null)
  const streamRef = useRef(null)
  const audioCtxRef = useRef(null)
  const animRef = useRef(null)

  // Browser + connectivity rows (camera/mic rows are derived from the state above).
  const [envRows, setEnvRows] = useState([])

  // Cleanup streams on unmount
  useEffect(() => {
    return () => {
      if (streamRef.current) {
        streamRef.current.getTracks().forEach(t => t.stop())
      }
      if (audioCtxRef.current) {
        audioCtxRef.current.close()
      }
      if (animRef.current) {
        cancelAnimationFrame(animRef.current)
      }
      clearTimeout(camTimerRef.current)
    }
  }, [])

  // Sample the live picture: first after the camera has had ~1s to settle exposure, then
  // every 1.5s so turning a light on clears the "too dark" warning without a re-check.
  const watchCamera = useCallback(() => {
    clearTimeout(camTimerRef.current)
    let tries = 0
    const tick = () => {
      const v = videoRef.current
      const brightness = sampleVideoBrightness(v)
      tries += 1
      if (brightness != null && tries >= 3) {
        setCamMeta({ measured: true, brightness, width: v.videoWidth, height: v.videoHeight })
        camTimerRef.current = setTimeout(tick, 1500)
        return
      }
      if (tries >= 12) {   // ~3.6s and still no frame: the camera is not delivering a picture
        setCamMeta({ measured: true, brightness: null })
        return
      }
      camTimerRef.current = setTimeout(tick, 300)
    }
    camTimerRef.current = setTimeout(tick, 300)
  }, [])

  const checkDevices = useCallback(async () => {
    setCamStatus('checking')
    setMicStatus('checking')
    setCamMeta({ measured: false })
    setMicMuted(false)
    setMicHeard(false)
    micHeardRef.current = false
    clearTimeout(camTimerRef.current)

    // Stop any previous stream
    if (streamRef.current) {
      streamRef.current.getTracks().forEach(t => t.stop())
    }
    if (audioCtxRef.current) {
      audioCtxRef.current.close()
      audioCtxRef.current = null
    }
    if (animRef.current) {
      cancelAnimationFrame(animRef.current)
    }

    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        video: { width: 320, height: 240, frameRate: 15 },
        audio: true,
      })
      streamRef.current = stream

      // Camera
      const videoTrack = stream.getVideoTracks()[0]
      if (videoTrack && videoTrack.readyState === 'live') {
        setCamStatus('granted')
        // srcObject is set via the ref callback on the <video> element
        videoTrack.addEventListener('ended', () => { clearTimeout(camTimerRef.current); setCamStatus('ended') })
        watchCamera()
      } else {
        setCamStatus('error')
      }

      // Microphone - set up audio level meter
      const audioTrack = stream.getAudioTracks()[0]
      if (audioTrack && audioTrack.readyState === 'live') {
        setMicStatus('granted')
        // `muted` here is the SOURCE being muted (OS-level mute switch, another app
        // holding the device) — the page cannot unmute it, so the candidate must.
        setMicMuted(audioTrack.muted)
        audioTrack.addEventListener('mute', () => setMicMuted(true))
        audioTrack.addEventListener('unmute', () => setMicMuted(false))
        audioTrack.addEventListener('ended', () => setMicStatus('ended'))
        try {
          const audioCtx = new (window.AudioContext || window.webkitAudioContext)()
          audioCtxRef.current = audioCtx
          const source = audioCtx.createMediaStreamSource(stream)
          const analyser = audioCtx.createAnalyser()
          analyser.fftSize = 256
          source.connect(analyser)
          const dataArray = new Uint8Array(analyser.frequencyBinCount)

          const updateLevel = () => {
            analyser.getByteFrequencyData(dataArray)
            const avg = dataArray.reduce((a, b) => a + b, 0) / dataArray.length
            const level = Math.min(100, Math.round((avg / 128) * 100))
            setMicLevel(level)
            if (level >= MIC_HEARD_LEVEL && !micHeardRef.current) {
              micHeardRef.current = true
              setMicHeard(true)
            }
            animRef.current = requestAnimationFrame(updateLevel)
          }
          updateLevel()
        } catch {
          // No level meter means we can't confirm the mic hears anything; don't strand
          // the candidate over a missing Web Audio API when the track itself is live.
          micHeardRef.current = true
          setMicHeard(true)
        }
      } else {
        setMicStatus('error')
      }
    } catch (err) {
      if (err.name === 'NotAllowedError' || err.name === 'PermissionDeniedError') {
        setCamStatus('denied')
        setMicStatus('denied')
      } else {
        setCamStatus('error')
        setMicStatus('error')
      }
    }
  }, [watchCamera])

  // Browser and connection checks. Run on load (no permission prompt involved) and again
  // from "Re-run checks". The proctoring socket is only probed for proctored tests.
  const runEnvChecks = useCallback(async (iv) => {
    const proctored = !!iv?.enable_webcam_monitoring
    const browser = checkBrowser({
      needsMedia: proctored,
      needsFullscreen: iv?.require_fullscreen !== false,
      fullscreenSupported: fullscreenSupported(),
    })
    const running = (id, label) => ({ id, label, status: STATUS.RUNNING, detail: 'Checking…' })
    const pending = [running('server', 'Internet connection')]
    if (proctored) pending.push(running('proctor-socket', 'Proctoring connection'))
    setEnvRows([...browser, ...pending])

    const settle = (row) => setEnvRows(rows => rows.map(r => (r.id === row.id ? row : r)))
    const jobs = [checkServer(() => api.get('/version')).then(settle)]
    // Only worth probing when the browser can open a socket at all; otherwise the
    // "Proctoring support" row already explains the failure.
    if (proctored) {
      jobs.push(browser.some(r => r.id === 'media-support' && r.status === STATUS.FAIL)
        ? Promise.resolve(settle({ id: 'proctor-socket', label: 'Proctoring connection', status: STATUS.FAIL,
            detail: 'Not available in this browser. See "Proctoring support" above.' }))
        : checkProctorSocket().then(settle))
    }
    await Promise.all(jobs)
  }, [])

  useEffect(() => {
    async function load() {
      try {
        const ivRes = await api.get('/candidate/interviews')
        const match = (ivRes.data || []).find(i => String(i.id) === String(interviewId))
        if (match) setInterview(match)

        // Already submitted: stop here. The exam page enforces this too, but landing there
        // first meant the candidate granted camera access and burned a proctoring session
        // before being told the test was over.
        if (match && String(match.status).toUpperCase() === 'COMPLETED') {
          setAlreadySubmitted(true)
          setLoading(false)
          return
        }

        // Build the proctoring pipeline while the candidate reads the instructions.
        // Cold, this costs ~107s server-side (mediapipe/ultralytics imports); doing it
        // here means clicking "Start test" hits a ready pool instead of waiting.
        // Fire-and-forget: prewarm failing must never block entering the test.
        if (match?.enable_webcam_monitoring) {
          api.post('/proctor/warmup').catch(() => {})
        }
        runEnvChecks(match)
        let qs = []
        try {
          const r = await api.get(`/mcq/interviews/${interviewId}/questions`)
          qs = r.data?.questions || r.data?.data?.questions || r.data || []
        } catch {
          const r = await api.get(`/candidate/interviews/${interviewId}/questions`)
          qs = r.data || []
        }
        setQCount(qs.length)
        setTotalTime(qs.reduce((s, q) => s + (q.time_limit_minutes || 5), 0))
      } catch {} finally { setLoading(false) }
    }
    load()
  }, [interviewId, runEnvChecks])

  const webcamEnabled = !!interview?.enable_webcam_monitoring
  // Defaults ON, matching the server: a test predating this flag still runs fullscreen.
  const requireFullscreen = interview?.require_fullscreen !== false

  const checkRows = webcamEnabled
    ? [
        ...envRows,
        classifyCamera({ permission: camStatus, ...camMeta }),
        classifyMicrophone({ permission: micStatus, muted: micMuted, heard: micHeard }),
      ]
    : envRows
  const failedChecks = checkRows.filter(r => r.status === STATUS.FAIL)
  const openChecks = checkRows.filter(r => !isSatisfied(r) && r.status !== STATUS.FAIL)
  // A failure always blocks. A proctored test also waits for every check to finish —
  // starting before the proctoring socket or camera is confirmed is exactly the silent
  // failure this exists to prevent. An unproctored test has nothing to wait for.
  const checksOk = failedChecks.length === 0 && (!webcamEnabled || openChecks.length === 0)
  const canStart = agreed && checksOk && (!webcamEnabled || consented)
  const checkSection = webcamEnabled ? '05' : '04'

  const rerunChecks = () => {
    runEnvChecks(interview)
    if (webcamEnabled) checkDevices()
  }

  const handleStart = async () => {
    if (!canStart) return
    // Enter fullscreen HERE, not on the exam screen. The browser only grants it from a
    // user gesture, and this click is the last one before the test begins — an effect
    // in OnlineTest would run with no gesture in hand and be refused every time.
    //
    // Best effort on purpose. If it is refused or unsupported (iOS Safari has no
    // element fullscreen), the exam still starts: OnlineTest re-checks on mount, prompts
    // if it can, and reports the gap to the proctor so HR sees an unmonitored run for
    // what it is. Nobody loses a job application because of their phone.
    if (webcamEnabled) {
      // Handed to the exam page, which sends it with /proctor/sessions/start so the
      // consent is stored with the monitoring record it covers.
      try {
        sessionStorage.setItem(`hf_monitoring_consent_${interviewId}`, JSON.stringify({
          accepted_at: new Date().toISOString(),
          disclosure_version: MONITORING_DISCLOSURE_VERSION,
        }))
      } catch { /* storage blocked: the test still starts; the record just lacks the marker */ }
    }
    if (requireFullscreen && fullscreenSupported()) {
      await requestFullscreen()
    }
    navigate(`/test/${interviewId}/exam`)
  }

  if (loading) return (
    <div style={p.root}><div style={p.center}>
      <div style={p.spinner}/><p style={p.loadTxt}>Preparing assessment...</p>
    </div><style>{css}</style></div>
  )

  const jobName = interview?.job_title || interview?.title || interview?.job_position || `Assessment #${interviewId}`

  // Terminal state: this assessment was already submitted and cannot be retaken. Shown
  // before any camera prompt or proctoring session is created.
  if (alreadySubmitted) return (
    <div style={p.root}>
      <div style={p.topBar}/>
      <div style={p.center}>
        <div style={{ maxWidth: 460, textAlign: 'center', padding: '32px 24px' }}>
          <div style={{ fontSize: 40, marginBottom: 12 }}>&#10003;</div>
          <h2 style={{ fontSize: 20, fontWeight: 700, marginBottom: 8, color: '#111827' }}>
            Assessment already completed
          </h2>
          <p style={{ fontSize: 14, lineHeight: 1.6, color: '#6b7280', marginBottom: 20 }}>
            You have already submitted <strong>{jobName}</strong>. Each attempt can only be
            submitted once. Your recruiter will be in touch with the outcome, and can offer
            another attempt if something went wrong.
          </p>
          <div style={{ margin: '0 0 20px' }}><ExplanationForm interviewId={interviewId} /></div>
          <button onClick={() => navigate('/candidate/dashboard')} style={p.startBtn}>
            Back to dashboard
          </button>
        </div>
      </div>
      <style>{css}</style>
    </div>
  )

  return (
    <div style={p.root}>
      <div style={p.topBar}/>

      <div style={p.wrap} className="ti-wrap">
        {/* ── HERO ── */}
        <header style={p.hero}>
          <div style={p.heroBg}/>
          <div style={p.heroContent} className="ti-hero">
            <div style={p.heroLeft}>
              <div style={p.badge}>ONLINE ASSESSMENT</div>
              <h1 style={p.heroTitle}>{jobName}</h1>
              <p style={p.heroSub}>Please read all instructions carefully before starting</p>
            </div>
            <div style={p.stats}>
              <div style={p.stat}><span style={p.statV}>{qCount}</span><span style={p.statL}>Questions</span></div>
              <div style={p.statDiv}/>
              <div style={p.stat}><span style={p.statV}>{totalTime}</span><span style={p.statL}>Minutes</span></div>
              <div style={p.statDiv}/>
              <div style={p.stat}><span style={{...p.statV, color:'#d97706'}}>{interview?.difficulty_level||'Mixed'}</span><span style={p.statL}>Level</span></div>
            </div>
          </div>
        </header>

        {/* ── BODY ── */}
        <div style={p.body} className="ti-body">
          <div style={p.main}>
            <Section n="01" title="General Instructions">
              <Rule>This is a <b>timed assessment</b>. The timer begins when you click "Start Test" and cannot be paused.</Rule>
              <Rule>The test contains <b>{qCount} questions</b> with a total time of <b>{totalTime} minutes</b>.</Rule>
              <Rule>All questions are compulsory. There is <b>no negative marking</b>.</Rule>
              <Rule>Do <b>not</b> refresh, close, or navigate away from the test window.</Rule>
              <Rule>The test will <b>auto-submit</b> when the timer reaches zero.</Rule>
            </Section>

            <Section n="02" title="Navigation & Controls">
              <Rule>Use <b>Previous</b> and <b>Next</b> buttons to move between questions.</Rule>
              <Rule>Click any question number in the sidebar to jump directly to it.</Rule>
              <Rule>Use <b>"Mark for Review"</b> to flag questions for revisiting later.</Rule>
              <Rule>You may change your answer at any time before final submission.</Rule>
            </Section>

            <Section n="03" title="Question Status Legend">
              <div style={p.legendWrap}>
                {[
                  ['#2563eb','transparent','Current','Question you are viewing now'],
                  ['#e5e7eb','#9ca3af','Not Attempted','Not yet visited'],
                  ['#16a34a','transparent','Answered','Answer selected'],
                  ['#ea580c','transparent','Not Answered','Visited, no answer given'],
                  ['#57534e','transparent','Review','Flagged for revisiting'],
                ].map(([bg,bd,label,desc],i)=>(
                  <div key={i} style={p.legRow}>
                    <span style={{...p.legDot,background:bg,border:bd!=='transparent'?`2px solid ${bd}`:'2px solid transparent'}}/>
                    <div><span style={p.legLabel}>{label}</span><span style={p.legDesc}> — {desc}</span></div>
                  </div>
                ))}
              </div>
            </Section>

            {webcamEnabled && (
              <Section n="04" title="How This Test Is Monitored">
                <MonitoringDisclosure />
              </Section>
            )}

            <Section n={checkSection} title="System Check">
              {webcamEnabled && (<>
              <Rule>Keep your camera and microphone <b>on</b> for the whole test.</Rule>
              <Rule>Make sure your <b>face is clearly visible</b> and the room is <b>well lit</b>.</Rule>
              <Rule>Do <b>not wear headphones or earbuds</b> unless your recruiter has said you may.</Rule>
              </>)}

              <SignalPathCheck
                steps={buildSignalPath(checkRows)}
                proctored={webcamEnabled}
                camStatus={camStatus}
                micLevel={micLevel}
                streamRef={streamRef}
                videoRef={videoRef}
                onEnableDevices={checkDevices}
                onRerun={rerunChecks}
              />
            </Section>
          </div>

          <aside style={p.side}>
            <div style={p.warnCard}>
              <div style={p.warnHead}>
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#92400e" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>
                <span style={p.warnTitle}>Important Notices</span>
              </div>
              <ul style={p.warnList}>
                <li>Ensure a stable internet connection.</li>
                <li>Use a laptop or desktop browser.</li>
                <li>Close all other tabs and apps.</li>
                <li>Do not switch windows during the test.</li>
                {webcamEnabled && <>
                  <li>Camera and microphone must stay <b>on</b> throughout.</li>
                  <li>Sit in a quiet, well-lit room with no one else visible.</li>
                </>}
              </ul>
            </div>

            <div style={p.infoCard}>
              <h4 style={p.infoTitle}>Test Details</h4>
              {[
                ['Type', interview?.interview_type || 'MCQ'],
                ['Duration', `${totalTime} min`],
                ['Questions', qCount],
                ['Negative Marking', 'No'],
                ['Auto-submit', 'On timeout'],
              ].map(([k,v],i)=>(
                <div key={i} style={p.infoRow}>
                  <span style={p.infoK}>{k}</span>
                  <span style={p.infoV}>{v}</span>
                </div>
              ))}
            </div>

            <AdjustmentCard interviewId={interviewId} interview={interview}
              onChange={(patch) => setInterview((iv) => ({ ...iv, ...patch }))} />

            <div style={p.helpCard}>
              Having trouble? Contact your HR representative before starting the test.
            </div>
          </aside>
        </div>

        {/* ── FOOTER ── */}
        <footer style={p.footer} className="ti-footer">
          <label style={p.chkWrap}>
            <input type="checkbox" checked={agreed} onChange={()=>setAgreed(!agreed)} style={p.chk}/>
            <span style={p.chkTxt}>I have read all instructions and agree to the assessment rules.</span>
          </label>
          {webcamEnabled && (
            <label style={p.chkWrap}>
              <input type="checkbox" checked={consented} onChange={() => setConsented(!consented)} style={p.chk}/>
              <span style={p.chkTxt}>
                I understand how this test is monitored (section 04) and agree to it.
              </span>
            </label>
          )}
          {!checksOk && (
            <p style={p.startHint} role="status">
              {failedChecks.length > 0
                ? `Fix the ${failedChecks.length === 1 ? 'problem' : 'problems'} in the System Check (section ${checkSection}) before starting.`
                : `Finish the System Check (section ${checkSection}) before starting.`}
            </p>
          )}
          <div style={p.fActions} className="ti-actions">
            <button style={p.backBtn} onClick={()=>navigate('/candidate/interviews')}>
              Back to Dashboard
            </button>
            <button
              style={{...p.startBtn, opacity: canStart?1:0.35, cursor: canStart?'pointer':'not-allowed'}}
              disabled={!canStart}
              onClick={handleStart}
            >
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#fff" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><polygon points="5 3 19 12 5 21 5 3"/></svg>
              Start Test
            </button>
          </div>
        </footer>
      </div>

      <div style={p.wmk}>HIREFLOW</div>
      <style>{css}</style>
    </div>
  )
}

function Section({ n, title, children }) {
  return (
    <div style={p.sec} className="ti-sec">
      <h2 style={p.secH}><span style={p.secN}>{n}</span>{title}</h2>
      <div style={p.secBody}>{children}</div>
    </div>
  )
}

/**
 * Ask for an adjustment before starting (extra time, microphone monitoring off...), and
 * see what was decided. Nothing changes until HR approves specific settings; approved
 * extra time is already reflected in the minutes shown on this page.
 */
function AdjustmentCard({ interviewId, interview, onChange }) {
  const [open, setOpen] = useState(false)
  const [text, setText] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState(null)
  const status = interview?.accommodation_status
  const approved = interview?.accommodations_summary || []

  const send = async () => {
    setSaving(true); setError(null)
    try {
      const r = await api.post(`/candidate/interviews/${interviewId}/accommodation-request`, { text: text.trim() })
      onChange(r.data)
      setOpen(false)
    } catch (e) {
      setError(e?.response?.data?.detail || 'Your request could not be sent. Try again.')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div style={p.adjCard}>
      <h4 style={p.infoTitle}>Need an adjustment?</h4>
      {status === 'approved' ? (
        <>
          <p style={p.adjText}>Approved for this test:</p>
          <ul style={p.adjList}>{approved.map((a) => <li key={a}>{a}</li>)}</ul>
          {!approved.length && <p style={p.adjText}>No changes were needed.</p>}
        </>
      ) : status === 'requested' ? (
        <>
          <p style={p.adjText}>Your request was sent to the hiring team:</p>
          <q style={p.adjQuote}>{interview.accommodation_request}</q>
          <p style={p.adjText}>
            They will reply before you start. If you start now, the test runs without the
            adjustment.
          </p>
        </>
      ) : (
        <>
          {status === 'declined' && (
            <p style={p.adjText}>Your last request was declined. You can ask again or contact your recruiter.</p>
          )}
          <p style={p.adjText}>
            For example extra time, or microphone monitoring turned off. Ask before you start;
            the hiring team decides what to approve.
          </p>
          {open ? (
            <>
              <label htmlFor="adj-text" style={p.adjLabel}>What do you need, and why?</label>
              <textarea id="adj-text" value={text} onChange={(e) => setText(e.target.value)}
                rows={4} maxLength={2000} style={p.adjInput} />
              {error && <p role="alert" style={{ ...p.adjText, color: '#b91c1c' }}>{error}</p>}
              <div style={{ display: 'flex', gap: 8 }}>
                <button type="button" onClick={send} disabled={saving || text.trim().length < 10}
                  style={{ ...p.adjBtn, opacity: saving || text.trim().length < 10 ? 0.5 : 1 }}>
                  {saving ? 'Sending…' : 'Send request'}
                </button>
                <button type="button" onClick={() => setOpen(false)} style={p.adjBtnGhost}>Cancel</button>
              </div>
            </>
          ) : (
            <button type="button" onClick={() => setOpen(true)} style={p.adjBtnGhost}>Ask for an adjustment</button>
          )}
        </>
      )}
    </div>
  )
}

// Bump when the wording below changes materially: the version is stored with each
// candidate's consent, so a reviewer can see exactly what that candidate agreed to.
const MONITORING_DISCLOSURE_VERSION = '2026-09-28'

/**
 * What proctoring collects, who sees it, and what it can and cannot do to the candidate.
 * Every statement here is checked against the code: see backend/proctoring/routes.py
 * (_TERMINATING_FLAGS, MARKER_VIOLATION_TYPES, evidence saving) and the face analyzer.
 * If the behaviour changes, this text must change with it.
 */
function MonitoringDisclosure() {
  const items = [
    ['Your camera', 'The picture from your camera is sent to HireFlow\u2019s servers and analysed automatically while you take the test, to check that you are present, that no one else is with you, and that no phone is in use.'],
    ['Your face', 'Your face is compared with the photo you uploaded with your application, to check it is the same person. This is not an identity-document check. If there is no photo on file, a snapshot taken at the start of the test is used instead, and the hiring team is told that.'],
    ['Your microphone', 'Sound is analysed as you speak for other voices and background noise. Audio is not recorded or transcribed; only measurements such as volume are kept when something is flagged.'],
    ['Your browser', 'Switching away from the test tab and leaving fullscreen are recorded.'],
    ['What is saved', 'When something is flagged, a still image from your camera is saved with a note of what was detected and when.'],
    ['Who can see it', 'Only the hiring team for this job: the recruiters and interviewers working on it.'],
    ['How long it is kept', 'Until the employer deletes the job.'],
  ]
  return (
    <div style={p.discWrap}>
      <p style={p.discLead}>
        This test is proctored. Here is exactly what that means, so you can decide before you start.
      </p>
      <dl style={p.discList}>
        {items.map(([term, text]) => (
          <div key={term} style={p.discRow} className="ti-disc-row">
            <dt style={p.discTerm}>{term}</dt>
            <dd style={p.discText}>{text}</dd>
          </div>
        ))}
      </dl>
      <div style={p.discNote}>
        <p style={p.discNoteHead}>A person makes every decision</p>
        <p style={p.discNoteText}>
          Flags are reviewed by someone on the hiring team. Nothing detected during the test
          rejects you automatically. Looking away, background noise, switching tabs or leaving
          fullscreen are recorded for that review and never end the test.
        </p>
        <p style={p.discNoteText}>
          The test ends early only if a second person or a phone is seen repeatedly (you are
          warned three times first), or if the person on camera does not match your photo for
          about five minutes. Your answers up to that point are submitted and a person reviews
          what happened.
        </p>
        <p style={{ ...p.discNoteText, marginBottom: 0 }}>
          If you need an adjustment, such as extra time or microphone monitoring turned off,
          use “Need an adjustment?” on this page before you start.
        </p>
      </div>
    </div>
  )
}

function Rule({ children }) {
  return (
    <div style={p.rule}>
      <span style={p.ruleDot}>&#9654;</span>
      <span style={p.ruleTxt}>{children}</span>
    </div>
  )
}

const css = `
  @keyframes spin{to{transform:rotate(360deg)}}
  @keyframes rise{from{opacity:0;transform:translateY(20px)}to{opacity:1;transform:translateY(0)}}
  @keyframes slideR{from{opacity:0;transform:translateX(24px)}to{opacity:1;transform:translateX(0)}}
  /* The layout is inline-styled; these override it on narrow screens (hence !important).
     The two-column grid used to squeeze the instructions into ~170px on a phone. */
  @media (max-width: 860px) {
    .ti-body { grid-template-columns: minmax(0, 1fr) !important; }
    .ti-wrap { padding: 16px 16px 40px !important; }
    .ti-hero { padding: 22px 20px !important; }
    .ti-sec { padding: 18px 16px !important; }
    .ti-footer { padding: 18px 16px !important; }
    .ti-actions { flex-direction: column-reverse; align-items: stretch !important; gap: 10px; }
    .ti-actions button { justify-content: center; }
    .ti-disc-row { grid-template-columns: minmax(0, 1fr) !important; }
  }
  @media (prefers-reduced-motion: reduce) {
    .ti-wrap, .ti-footer, aside { animation: none !important; }
  }
`

const p = {
  root: {
    position:'fixed',inset:0,zIndex:9999,
    background:'#f5f2ed',
    overflowY:'auto',
    fontFamily:"'Manrope', system-ui, sans-serif",
  },
  center:{position:'fixed',inset:0,display:'flex',flexDirection:'column',alignItems:'center',justifyContent:'center'},
  spinner:{width:30,height:30,borderRadius:'50%',border:'3px solid #ddd',borderTopColor:'#1e3a5f',animation:'spin .7s linear infinite'},
  loadTxt:{marginTop:14,fontSize:'.88rem',color:'#6b7280'},
  topBar:{height:5,background:'linear-gradient(90deg,#1e3a5f 0%,#2563eb 50%,#d97706 100%)',flexShrink:0},
  wrap:{maxWidth:1080,margin:'0 auto',padding:'28px 24px 52px',animation:'rise .5s ease'},

  /* Hero */
  hero:{position:'relative',borderRadius:14,overflow:'hidden',marginBottom:24,border:'1px solid #c9c2b6'},
  heroBg:{position:'absolute',inset:0,background:'linear-gradient(135deg,#1e3a5f 0%,#234e78 60%,#2b6cb0 100%)'},
  heroContent:{position:'relative',display:'flex',alignItems:'center',justifyContent:'space-between',padding:'28px 36px',flexWrap:'wrap',gap:20},
  heroLeft:{},
  badge:{
    display:'inline-block',fontSize:'.62rem',fontWeight:800,
    letterSpacing:'.14em',color:'#fbbf24',
    background:'rgba(251,191,36,.12)',padding:'4px 12px',borderRadius:4,marginBottom:10,
  },
  heroTitle:{fontFamily:"'DM Serif Display',serif",fontSize:'1.65rem',fontWeight:400,color:'#fff',margin:'0 0 4px',lineHeight:1.2},
  heroSub:{fontSize:'.85rem',color:'rgba(255,255,255,.6)',margin:0,fontWeight:500},
  stats:{display:'flex',alignItems:'center',gap:18},
  stat:{display:'flex',flexDirection:'column',alignItems:'center',gap:2},
  statV:{fontFamily:"'JetBrains Mono',monospace",fontSize:'1.5rem',fontWeight:700,color:'#fff',lineHeight:1},
  statL:{fontSize:'.6rem',fontWeight:700,color:'rgba(255,255,255,.45)',textTransform:'uppercase',letterSpacing:'.1em'},
  statDiv:{width:1,height:32,background:'rgba(255,255,255,.15)'},

  /* Body */
  body:{display:'grid',gridTemplateColumns:'1fr 290px',gap:20,alignItems:'start'},
  main:{display:'flex',flexDirection:'column',gap:0},
  side:{display:'flex',flexDirection:'column',gap:14,animation:'slideR .55s ease .15s both'},

  /* Section */
  sec:{background:'#fff',borderRadius:10,border:'1px solid #d6cfc4',padding:'20px 26px',marginBottom:14,boxShadow:'0 1px 4px rgba(0,0,0,.03)'},
  secH:{fontFamily:"'DM Serif Display',serif",fontSize:'1.02rem',fontWeight:400,color:'#1e3a5f',margin:'0 0 14px',display:'flex',alignItems:'center',gap:10,borderBottom:'1px solid #eee',paddingBottom:10},
  secN:{fontFamily:"'JetBrains Mono',monospace",fontSize:'.65rem',fontWeight:700,color:'#d97706',background:'#fef9ee',padding:'3px 8px',borderRadius:4},
  secBody:{display:'flex',flexDirection:'column',gap:8},

  /* Rule */
  rule:{display:'flex',gap:10,alignItems:'flex-start'},
  ruleDot:{color:'#1e3a5f',fontSize:'.5rem',marginTop:6,flexShrink:0,opacity:.5},
  ruleTxt:{fontSize:'.87rem',lineHeight:1.65,color:'#374151'},

  /* Legend */
  legendWrap:{display:'flex',flexDirection:'column',gap:6},
  legRow:{display:'flex',alignItems:'center',gap:12,padding:'6px 10px',borderRadius:5,background:'#fafaf7'},
  legDot:{width:20,height:20,borderRadius:4,flexShrink:0},
  legLabel:{fontSize:'.82rem',fontWeight:700,color:'#1e293b'},
  legDesc:{fontSize:'.82rem',color:'#6b7280'},

  /* Side cards */
  warnCard:{background:'#fffbeb',border:'1px solid #fcd34d',borderRadius:10,padding:'16px 18px'},
  warnHead:{display:'flex',alignItems:'center',gap:7,marginBottom:10},
  warnTitle:{fontSize:'.82rem',fontWeight:800,color:'#92400e'},
  warnList:{margin:0,padding:'0 0 0 16px',fontSize:'.8rem',color:'#78350f',lineHeight:1.55,display:'flex',flexDirection:'column',gap:4},

  infoCard:{background:'#fff',border:'1px solid #d6cfc4',borderRadius:10,padding:'16px 18px'},
  infoTitle:{fontFamily:"'DM Serif Display',serif",fontSize:'.88rem',color:'#1e3a5f',margin:'0 0 12px',paddingBottom:8,borderBottom:'1px solid #eee'},
  infoRow:{display:'flex',justifyContent:'space-between',padding:'4px 0'},
  infoK:{fontSize:'.8rem',color:'#6b7280'},
  infoV:{fontSize:'.8rem',fontWeight:700,color:'#1e293b',fontFamily:"'JetBrains Mono',monospace"},

  helpCard:{background:'#f0f4f8',border:'1px solid #d0d8e4',borderRadius:10,padding:'14px 16px',fontSize:'.78rem',color:'#4b5563',lineHeight:1.5},

  startHint:{margin:'-6px 0 14px 28px',fontSize:'.8rem',fontWeight:600,color:'#b45309'},

  /* Adjustment request */
  adjCard:{background:'#fff',border:'1px solid #d6cfc4',borderRadius:10,padding:'16px 18px',display:'flex',flexDirection:'column',gap:8},
  adjText:{margin:0,fontSize:'.8rem',lineHeight:1.55,color:'#4b5563'},
  adjList:{margin:0,padding:'0 0 0 18px',fontSize:'.82rem',lineHeight:1.6,color:'#1e293b',fontWeight:600},
  adjQuote:{display:'block',fontSize:'.8rem',lineHeight:1.5,color:'#1e293b',background:'#f5f2ed',borderRadius:6,padding:'8px 10px'},
  adjLabel:{fontSize:'.8rem',fontWeight:700,color:'#1e293b'},
  adjInput:{fontFamily:'inherit',fontSize:'.82rem',padding:'8px 10px',borderRadius:6,border:'1px solid #c9c2b6',resize:'vertical'},
  adjBtn:{padding:'8px 14px',borderRadius:7,border:'none',background:'#1e3a5f',color:'#fff',fontSize:'.8rem',fontWeight:700,cursor:'pointer',fontFamily:"'Manrope',sans-serif"},
  adjBtnGhost:{alignSelf:'flex-start',padding:'8px 14px',borderRadius:7,border:'1px solid #c9c2b6',background:'#fff',color:'#374151',fontSize:'.8rem',fontWeight:700,cursor:'pointer',fontFamily:"'Manrope',sans-serif"},

  /* Monitoring disclosure */
  discWrap:{display:'flex',flexDirection:'column',gap:12},
  discLead:{margin:0,fontSize:'.87rem',lineHeight:1.6,color:'#374151'},
  discList:{margin:0,display:'flex',flexDirection:'column',gap:10},
  discRow:{display:'grid',gridTemplateColumns:'minmax(0,9.5rem) minmax(0,1fr)',gap:'4px 16px'},
  discTerm:{fontSize:'.84rem',fontWeight:700,color:'#1e293b'},
  discText:{margin:0,fontSize:'.84rem',lineHeight:1.6,color:'#374151',maxWidth:'62ch'},
  discNote:{marginTop:4,padding:'14px 16px',borderRadius:8,background:'#f0f7f5',border:'1px solid #cfe6e0'},
  discNoteHead:{margin:'0 0 6px',fontSize:'.86rem',fontWeight:800,color:'#0f5f53'},
  discNoteText:{margin:'0 0 8px',fontSize:'.84rem',lineHeight:1.6,color:'#274b45',maxWidth:'66ch'},

  /* Footer */
  footer:{background:'#fff',borderRadius:12,border:'1px solid #d6cfc4',padding:'22px 30px',marginTop:20,boxShadow:'0 2px 10px rgba(0,0,0,.04)',animation:'rise .5s ease .25s both'},
  chkWrap:{display:'flex',alignItems:'flex-start',gap:10,cursor:'pointer',marginBottom:18},
  chk:{width:18,height:18,marginTop:2,accentColor:'#1e3a5f',cursor:'pointer',flexShrink:0},
  chkTxt:{fontSize:'.86rem',lineHeight:1.5,color:'#374151',fontWeight:600},
  fActions:{display:'flex',justifyContent:'space-between',alignItems:'center'},
  backBtn:{padding:'10px 22px',borderRadius:7,border:'1px solid #c9c2b6',background:'#fff',color:'#4b5563',fontSize:'.86rem',fontWeight:600,cursor:'pointer',fontFamily:"'Manrope',sans-serif"},
  startBtn:{
    display:'flex',alignItems:'center',gap:9,
    padding:'12px 34px',borderRadius:8,border:'none',
    background:'#1e3a5f',color:'#fff',fontSize:'.95rem',fontWeight:800,
    fontFamily:"'Manrope',sans-serif",letterSpacing:'.02em',
    boxShadow:'0 4px 18px rgba(30,58,95,.3)',transition:'all .2s',
  },

  wmk:{position:'fixed',bottom:14,right:22,fontSize:'.6rem',fontWeight:800,letterSpacing:'.18em',color:'rgba(30,58,95,.06)',fontFamily:"'Manrope',sans-serif",pointerEvents:'none',userSelect:'none'},
}
