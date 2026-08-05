import { useState, useEffect, useRef, useCallback } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import api from '../../api/client'

export default function TestInstructions() {
  const { interviewId } = useParams()
  const navigate = useNavigate()
  const [interview, setInterview] = useState(null)
  const [alreadySubmitted, setAlreadySubmitted] = useState(false)
  const [qCount, setQCount] = useState(0)
  const [totalTime, setTotalTime] = useState(0)
  const [loading, setLoading] = useState(true)
  const [agreed, setAgreed] = useState(false)

  // Camera & Mic state
  const [camStatus, setCamStatus] = useState('idle')   // idle | checking | granted | denied | error
  const [micStatus, setMicStatus] = useState('idle')
  const [micLevel, setMicLevel] = useState(0)
  const videoRef = useRef(null)
  const streamRef = useRef(null)
  const audioCtxRef = useRef(null)
  const animRef = useRef(null)

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
    }
  }, [])

  const checkDevices = useCallback(async () => {
    setCamStatus('checking')
    setMicStatus('checking')

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
      } else {
        setCamStatus('error')
      }

      // Microphone - set up audio level meter
      const audioTrack = stream.getAudioTracks()[0]
      if (audioTrack && audioTrack.readyState === 'live') {
        setMicStatus('granted')
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
            setMicLevel(Math.min(100, Math.round((avg / 128) * 100)))
            animRef.current = requestAnimationFrame(updateLevel)
          }
          updateLevel()
        } catch {
          // Audio analysis failed but mic still works
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
  }, [interviewId])

  const webcamEnabled = !!interview?.enable_webcam_monitoring
  const devicesReady = camStatus === 'granted' && micStatus === 'granted'
  const canStart = agreed && (!webcamEnabled || devicesReady)

  const handleStart = () => {
    if (!canStart) return
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
            You have already submitted <strong>{jobName}</strong>. Each assessment can only
            be taken once, so it cannot be reopened. Your recruiter will be in touch with
            the outcome.
          </p>
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

      <div style={p.wrap}>
        {/* ── HERO ── */}
        <header style={p.hero}>
          <div style={p.heroBg}/>
          <div style={p.heroContent}>
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
        <div style={p.body}>
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
            <Section n="04" title="Camera & Microphone Check">
              <Rule>This test is <b>proctored</b>. Your camera and microphone must remain on throughout the assessment.</Rule>
              <Rule>Ensure your <b>face is clearly visible</b> and you are in a <b>well-lit environment</b>.</Rule>
              <Rule>Do <b>not wear headphones or earbuds</b> unless explicitly permitted.</Rule>
              <Rule>Any <b>suspicious activity</b> (looking away, multiple faces, unusual sounds) will be flagged.</Rule>

              <div style={p.deviceCheckWrap}>
                {/* Camera Preview */}
                <div style={p.deviceCard}>
                  <div style={p.deviceHeader}>
                    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke={camStatus==='granted'?'#16a34a':'#6b7280'} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M23 7l-7 5 7 5V7z"/><rect x="1" y="5" width="15" height="14" rx="2" ry="2"/></svg>
                    <span style={p.deviceLabel}>Camera</span>
                    <DeviceStatusBadge status={camStatus} />
                  </div>
                  <div style={p.videoBox}>
                    {camStatus === 'granted' ? (
                      <video ref={el => {
                        videoRef.current = el
                        if (el && streamRef.current && el.srcObject !== streamRef.current) {
                          el.srcObject = streamRef.current
                          el.play().catch(() => {})
                        }
                      }} autoPlay playsInline muted style={p.videoEl} />
                    ) : (
                      <div style={p.videoPlaceholder}>
                        <svg width="36" height="36" viewBox="0 0 24 24" fill="none" stroke="#9ca3af" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><path d="M23 7l-7 5 7 5V7z"/><rect x="1" y="5" width="15" height="14" rx="2" ry="2"/></svg>
                        <span style={p.placeholderTxt}>
                          {camStatus === 'denied' ? 'Camera access denied — check browser permissions' :
                           camStatus === 'error' ? 'Camera not detected' :
                           camStatus === 'checking' ? 'Requesting access...' :
                           'Click "Check Devices" to begin'}
                        </span>
                      </div>
                    )}
                  </div>
                </div>

                {/* Microphone Level */}
                <div style={p.deviceCard}>
                  <div style={p.deviceHeader}>
                    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke={micStatus==='granted'?'#16a34a':'#6b7280'} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2"/><line x1="12" y1="19" x2="12" y2="23"/><line x1="8" y1="23" x2="16" y2="23"/></svg>
                    <span style={p.deviceLabel}>Microphone</span>
                    <DeviceStatusBadge status={micStatus} />
                  </div>
                  <div style={p.micBox}>
                    {micStatus === 'granted' ? (
                      <div style={p.micLevelWrap}>
                        <span style={p.micHint}>Speak to test your microphone</span>
                        <div style={p.micBarTrack}>
                          <div style={{...p.micBarFill, width: `${micLevel}%`, background: micLevel > 60 ? '#16a34a' : micLevel > 25 ? '#d97706' : '#94a3b8'}} />
                        </div>
                        <div style={p.micLevelLabels}>
                          <span>Silent</span><span>Good</span><span>Loud</span>
                        </div>
                      </div>
                    ) : (
                      <div style={p.videoPlaceholder}>
                        <svg width="36" height="36" viewBox="0 0 24 24" fill="none" stroke="#9ca3af" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2"/></svg>
                        <span style={p.placeholderTxt}>
                          {micStatus === 'denied' ? 'Microphone access denied — check browser permissions' :
                           micStatus === 'error' ? 'Microphone not detected' :
                           micStatus === 'checking' ? 'Requesting access...' :
                           'Click "Check Devices" to begin'}
                        </span>
                      </div>
                    )}
                  </div>
                </div>
              </div>

              <div style={p.checkBtnWrap}>
                <button style={p.checkBtn} onClick={checkDevices}>
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#fff" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg>
                  {devicesReady ? 'Re-check Devices' : 'Check Devices'}
                </button>
                {!devicesReady && camStatus !== 'idle' && (
                  <span style={p.checkHint}>Both camera and microphone must be working to start the test.</span>
                )}
              </div>
            </Section>
            )}
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
                <li>Camera and microphone must stay <b>on</b> throughout.</li>
                <li>Sit in a quiet, well-lit room with no one else visible.</li>
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

            <div style={p.helpCard}>
              Having trouble? Contact your HR representative before starting the test.
            </div>
          </aside>
        </div>

        {/* ── FOOTER ── */}
        <footer style={p.footer}>
          <label style={p.chkWrap}>
            <input type="checkbox" checked={agreed} onChange={()=>setAgreed(!agreed)} style={p.chk}/>
            <span style={p.chkTxt}>I have read all instructions and agree to the assessment rules.</span>
          </label>
          <div style={p.fActions}>
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
    <div style={p.sec}>
      <h2 style={p.secH}><span style={p.secN}>{n}</span>{title}</h2>
      <div style={p.secBody}>{children}</div>
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

function DeviceStatusBadge({ status }) {
  const cfg = {
    idle:     { bg: '#f3f4f6', color: '#6b7280', label: 'Not checked' },
    checking: { bg: '#eff6ff', color: '#2563eb', label: 'Checking...' },
    granted:  { bg: '#dcfce7', color: '#16a34a', label: 'Working' },
    denied:   { bg: '#fef2f2', color: '#dc2626', label: 'Denied' },
    error:    { bg: '#fef2f2', color: '#dc2626', label: 'Not found' },
  }[status] || { bg: '#f3f4f6', color: '#6b7280', label: 'Unknown' }

  return (
    <span style={{
      marginLeft: 'auto', fontSize: '.7rem', fontWeight: 700,
      padding: '3px 10px', borderRadius: 20,
      background: cfg.bg, color: cfg.color,
    }}>
      {status === 'granted' && <span style={{ marginRight: 4 }}>&#10003;</span>}
      {(status === 'denied' || status === 'error') && <span style={{ marginRight: 4 }}>&#10007;</span>}
      {cfg.label}
    </span>
  )
}

const css = `
  @keyframes spin{to{transform:rotate(360deg)}}
  @keyframes rise{from{opacity:0;transform:translateY(20px)}to{opacity:1;transform:translateY(0)}}
  @keyframes slideR{from{opacity:0;transform:translateX(24px)}to{opacity:1;transform:translateX(0)}}
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

  /* Device Check */
  deviceCheckWrap:{display:'grid',gridTemplateColumns:'1fr 1fr',gap:14,marginTop:14},
  deviceCard:{background:'#fafaf7',border:'1px solid #e5e2db',borderRadius:10,padding:14,display:'flex',flexDirection:'column',gap:10},
  deviceHeader:{display:'flex',alignItems:'center',gap:8},
  deviceLabel:{fontSize:'.85rem',fontWeight:700,color:'#1e293b'},
  videoBox:{borderRadius:8,overflow:'hidden',background:'#1e293b',aspectRatio:'4/3',display:'flex',alignItems:'center',justifyContent:'center'},
  videoEl:{width:'100%',height:'100%',objectFit:'cover',transform:'scaleX(-1)'},
  videoPlaceholder:{display:'flex',flexDirection:'column',alignItems:'center',justifyContent:'center',gap:8,padding:20,textAlign:'center',height:'100%'},
  placeholderTxt:{fontSize:'.75rem',color:'#9ca3af',lineHeight:1.4,maxWidth:200},
  micBox:{borderRadius:8,background:'#1e293b',padding:20,display:'flex',alignItems:'center',justifyContent:'center',minHeight:100},
  micLevelWrap:{width:'100%',display:'flex',flexDirection:'column',gap:8},
  micHint:{fontSize:'.75rem',color:'#94a3b8',textAlign:'center'},
  micBarTrack:{height:10,borderRadius:5,background:'#334155',overflow:'hidden'},
  micBarFill:{height:'100%',borderRadius:5,transition:'width .1s ease'},
  micLevelLabels:{display:'flex',justifyContent:'space-between',fontSize:'.6rem',color:'#64748b'},
  checkBtnWrap:{display:'flex',alignItems:'center',gap:14,marginTop:14},
  checkBtn:{
    display:'flex',alignItems:'center',gap:8,
    padding:'10px 22px',borderRadius:7,border:'none',
    background:'#2563eb',color:'#fff',fontSize:'.84rem',fontWeight:700,
    cursor:'pointer',fontFamily:"'Manrope',sans-serif",
    boxShadow:'0 2px 8px rgba(37,99,235,.25)',transition:'all .2s',
  },
  checkHint:{fontSize:'.78rem',color:'#dc2626',fontWeight:600},

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
