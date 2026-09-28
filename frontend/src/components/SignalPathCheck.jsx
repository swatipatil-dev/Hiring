import { STATUS } from '../utils/systemCheck'

/**
 * The pre-test system check, drawn as the path the candidate's video and audio travel:
 * camera → microphone → this browser → internet → HireFlow's proctoring server.
 *
 * One line runs through every step. It is lit where the step works and visibly broken
 * where it doesn't, so "what's wrong" and "where" are the same glance. Unproctored tests
 * get the short path (browser → internet → HireFlow) and no camera lens.
 *
 * Presentation only — the checks themselves live in utils/systemCheck.js and the stream
 * and device state in TestInstructions.
 */

// How to refer to a step in the headline sentence.
const SUBJECT = {
  camera: 'Your camera', microphone: 'Your microphone', browser: 'This browser',
  server: 'Your internet connection', 'proctor-socket': 'The proctoring connection',
}

function headline(steps, proctored, camStatus) {
  const failed = steps.filter(s => s.status === STATUS.FAIL)
  if (failed.length === 1) return { tone: 'break', text: `${SUBJECT[failed[0].id]} needs fixing before you can start.` }
  if (failed.length > 1) return { tone: 'break', text: `${failed.length} steps need fixing before you can start.` }
  if (proctored && camStatus === 'idle') return { tone: 'wait', text: 'Turn on your camera and microphone to finish the check.' }
  if (steps.some(s => s.status === STATUS.RUNNING)) return { tone: 'wait', text: 'Checking your setup…' }
  const mic = steps.find(s => s.id === 'microphone')
  if (mic?.status === STATUS.PENDING) return { tone: 'wait', text: 'Say a few words so we know we can hear you.' }
  if (steps.some(s => s.status === STATUS.PENDING)) return { tone: 'wait', text: 'Finish the steps below to continue.' }
  if (steps.some(s => s.status === STATUS.WARN)) return { tone: 'attn', text: 'You can start. Read the notes below first.' }
  return {
    tone: 'signal',
    text: proctored ? 'Everything connects. You’re ready to start.' : 'Your browser and connection are ready.',
  }
}

// Line segment below a step: carries on if the step works, stops if it doesn't.
const segmentFor = (status) =>
  status === STATUS.PASS || status === STATUS.WARN ? 'lit'
  : status === STATUS.FAIL ? 'break'
  : 'wait'

function Marker({ status }) {
  if (status === STATUS.PASS) return (
    <svg className="sc-mark sc-mark--pass" viewBox="0 0 20 20" aria-hidden="true">
      <circle cx="10" cy="10" r="9" /><path d="M6 10.4l2.6 2.6L14 7.6" />
    </svg>
  )
  if (status === STATUS.WARN) return (
    <svg className="sc-mark sc-mark--warn" viewBox="0 0 20 20" aria-hidden="true">
      <circle cx="10" cy="10" r="8" /><path d="M10 5.8v5.2M10 13.6v.6" />
    </svg>
  )
  if (status === STATUS.FAIL) return (
    <svg className="sc-mark sc-mark--fail" viewBox="0 0 20 20" aria-hidden="true">
      <circle cx="10" cy="10" r="9" /><path d="M7 7l6 6M13 7l-6 6" />
    </svg>
  )
  if (status === STATUS.RUNNING) return (
    <svg className="sc-mark sc-mark--run" viewBox="0 0 20 20" aria-hidden="true">
      <circle cx="10" cy="10" r="8" /><path d="M10 2a8 8 0 0 1 8 8" />
    </svg>
  )
  return (
    <svg className="sc-mark sc-mark--wait" viewBox="0 0 20 20" aria-hidden="true">
      <circle cx="10" cy="10" r="8" />
    </svg>
  )
}

const STATUS_TEXT = {
  [STATUS.PASS]: 'Working', [STATUS.WARN]: 'Working, with a note', [STATUS.FAIL]: 'Not working',
  [STATUS.RUNNING]: 'Checking', [STATUS.PENDING]: 'Waiting for you',
}

function MicLevel({ level }) {
  // Five steps is enough to show "it moves when I talk"; a precise meter invites fiddling.
  const lit = Math.round((Math.min(level, 60) / 60) * 5)
  return (
    <span className="sc-level" aria-hidden="true">
      {[0, 1, 2, 3, 4].map(i => <span key={i} className={i < lit ? 'on' : ''} />)}
    </span>
  )
}

function Lens({ camStatus, streamRef, videoRef, onEnable }) {
  if (camStatus === 'granted') return (
    <div className="sc-lens">
      <video
        ref={el => {
          videoRef.current = el
          if (el && streamRef.current && el.srcObject !== streamRef.current) {
            el.srcObject = streamRef.current
            el.play().catch(() => {})
          }
        }}
        autoPlay playsInline muted
        aria-label="Your camera preview"
      />
    </div>
  )
  const message = {
    idle: 'Your browser will ask to use your camera and microphone.',
    checking: 'Waiting for your permission…',
    denied: 'Access was blocked. Allow the camera and microphone from the icon in the address bar, then try again.',
    error: 'No camera or microphone found. Connect them and try again.',
    ended: 'Your camera or microphone disconnected. Reconnect it and try again.',
  }[camStatus] || ''
  return (
    <div className="sc-lens sc-lens--off">
      <p>{message}</p>
      {camStatus !== 'checking' && (
        <button type="button" className="sc-btn sc-btn--primary" onClick={onEnable}>
          {camStatus === 'idle' ? 'Turn on camera and microphone' : 'Try again'}
        </button>
      )}
    </div>
  )
}

export default function SignalPathCheck({
  steps, proctored, camStatus, micLevel, streamRef, videoRef, onEnableDevices, onRerun,
}) {
  const head = headline(steps, proctored, camStatus)
  return (
    <div className="sc">
      <p className={`sc-head sc-tone--${head.tone}`} role="status" aria-live="polite">{head.text}</p>
      <p className="sc-intro">
        {proctored
          ? 'Your camera and microphone reach HireFlow through this browser and your internet connection. Every step has to work for the test to be monitored.'
          : 'The test runs in this browser over your internet connection. Both need to work before you start.'}
      </p>

      <div className={proctored ? 'sc-body' : 'sc-body sc-body--short'}>
        {proctored && (
          <Lens camStatus={camStatus} streamRef={streamRef} videoRef={videoRef} onEnable={onEnableDevices} />
        )}

        <ol className="sc-path" aria-label="Setup check, from your device to HireFlow">
          {steps.map(step => (
            <li key={step.id} className={`sc-step sc-step--${step.status}`}>
              <span className="sc-rail">
                <Marker status={step.status} />
                <span className={`sc-seg sc-seg--${segmentFor(step.status)}`} />
              </span>
              <div className="sc-content">
                <div className="sc-line">
                  <span className="sc-label">{step.label}</span>
                  <span className="sc-sr">{STATUS_TEXT[step.status]}.</span>
                  {step.id === 'microphone' && camStatus === 'granted' && step.status !== STATUS.FAIL && (
                    <MicLevel level={micLevel} />
                  )}
                  <span className="sc-reading">{step.reading}</span>
                </div>
                {step.notes.map((n, i) => (
                  <p key={i} className={`sc-note sc-note--${n.status}`}>{n.text}</p>
                ))}
              </div>
            </li>
          ))}
          <li className="sc-step sc-step--end">
            <span className="sc-rail"><span className="sc-terminus" aria-hidden="true" /></span>
            <div className="sc-content">
              <div className="sc-line">
                <span className="sc-label">HireFlow</span>
                <span className="sc-reading">{proctored ? 'Proctoring and test server' : 'Test server'}</span>
              </div>
            </div>
          </li>
        </ol>
      </div>

      <div className="sc-actions">
        <button type="button" className="sc-btn" onClick={onRerun}>Run the check again</button>
      </div>
      <style>{css}</style>
    </div>
  )
}

const css = `
.sc {
  --sc-ink: #1d2733;
  --sc-muted: #5f6b78;
  --sc-rule: #d9dee4;
  --sc-signal: #0f7b6c;
  --sc-signal-soft: #e4f2ef;
  --sc-attn: #a86a12;
  --sc-break: #b93a26;
  color: var(--sc-ink);
  font-variant-numeric: tabular-nums;
}
.sc-head { margin: 4px 0 4px; font-size: 1.02rem; font-weight: 800; line-height: 1.4; }
.sc-tone--signal { color: var(--sc-signal); }
.sc-tone--attn { color: var(--sc-attn); }
.sc-tone--break { color: var(--sc-break); }
.sc-tone--wait { color: var(--sc-ink); }
.sc-intro { margin: 0 0 18px; max-width: 62ch; font-size: .84rem; line-height: 1.6; color: var(--sc-muted); }

.sc-body { display: grid; grid-template-columns: minmax(0, 15rem) minmax(0, 1fr); gap: 28px; align-items: start; }
.sc-body--short { grid-template-columns: minmax(0, 1fr); }
@media (max-width: 760px) { .sc-body { grid-template-columns: minmax(0, 1fr); gap: 20px; } }

/* The lens: the camera is the first thing on the path, so it gets the most room. */
.sc-lens { position: relative; aspect-ratio: 4 / 3; border-radius: 12px; overflow: hidden; background: var(--sc-ink); }
.sc-lens video { width: 100%; height: 100%; object-fit: cover; transform: scaleX(-1); display: block; }
.sc-lens--off { display: flex; flex-direction: column; justify-content: flex-end; gap: 12px; padding: 16px; }
.sc-lens--off p { margin: 0; font-size: .8rem; line-height: 1.5; color: #c9d1da; }

/* The path. Each step owns the rail segment beneath its marker. */
.sc-path { list-style: none; margin: 0; padding: 0; }
.sc-step { display: grid; grid-template-columns: 20px minmax(0, 1fr); column-gap: 14px; }
.sc-rail { position: relative; display: flex; flex-direction: column; align-items: center; }
.sc-mark { width: 20px; height: 20px; flex-shrink: 0; fill: none; stroke-width: 1.8; stroke-linecap: round; stroke-linejoin: round; }
.sc-mark--pass circle { fill: var(--sc-signal); stroke: var(--sc-signal); }
.sc-mark--pass path { stroke: #fff; stroke-width: 2.2; }
.sc-mark--warn circle { fill: #fff; stroke: var(--sc-attn); stroke-width: 2.2; }
.sc-mark--warn path { stroke: var(--sc-attn); stroke-width: 2.2; }
.sc-mark--fail circle { fill: var(--sc-break); stroke: var(--sc-break); }
.sc-mark--fail path { stroke: #fff; stroke-width: 2.2; }
.sc-mark--wait circle { fill: #fff; stroke: var(--sc-rule); stroke-width: 2; }
.sc-mark--run circle { stroke: var(--sc-rule); stroke-width: 2; fill: #fff; }
.sc-mark--run path { stroke: var(--sc-signal); stroke-width: 2.2; transform-origin: 10px 10px; animation: sc-turn .9s linear infinite; }

.sc-seg { flex: 1; width: 2px; min-height: 22px; margin: 3px 0; border-radius: 1px; transition: background-color .4s ease; }
.sc-seg--lit { background: var(--sc-signal); }
.sc-seg--wait { background: repeating-linear-gradient(to bottom, var(--sc-rule) 0 4px, transparent 4px 8px); }
/* A broken step leaves a gap in the line: two stubs of brick with nothing between. */
.sc-seg--break { background: linear-gradient(to bottom, var(--sc-break) 0 6px, transparent 6px calc(100% - 6px), var(--sc-break) calc(100% - 6px)); opacity: .55; }

.sc-content { padding-bottom: 18px; min-width: 0; }
.sc-line { display: flex; flex-wrap: wrap; align-items: baseline; column-gap: 10px; row-gap: 2px; min-height: 20px; }
.sc-label { font-size: .9rem; font-weight: 700; }
.sc-reading { margin-left: auto; font-size: .82rem; color: var(--sc-muted); text-align: right; }
.sc-step--pass .sc-reading { color: var(--sc-signal); }
.sc-step--fail .sc-reading { color: var(--sc-break); font-weight: 700; }
.sc-note { margin: 5px 0 0; max-width: 60ch; font-size: .8rem; line-height: 1.55; color: var(--sc-muted); }
.sc-note--fail { color: var(--sc-break); }
.sc-note--warn { color: var(--sc-attn); }

.sc-step--end .sc-content { padding-bottom: 0; }
.sc-step--end .sc-label { font-weight: 800; }
.sc-terminus { width: 14px; height: 14px; margin: 3px; border-radius: 3px; background: var(--sc-ink); }

.sc-level { display: inline-flex; align-items: flex-end; gap: 2px; height: 12px; align-self: center; }
.sc-level span { width: 3px; border-radius: 1px; background: var(--sc-rule); transition: background-color .08s linear; }
.sc-level span:nth-child(1) { height: 4px; } .sc-level span:nth-child(2) { height: 6px; }
.sc-level span:nth-child(3) { height: 8px; } .sc-level span:nth-child(4) { height: 10px; }
.sc-level span:nth-child(5) { height: 12px; }
.sc-level span.on { background: var(--sc-signal); }

.sc-actions { margin-top: 18px; }
.sc-btn {
  font: inherit; font-size: .84rem; font-weight: 700; cursor: pointer;
  padding: 9px 16px; border-radius: 8px; border: 1px solid var(--sc-rule); background: #fff; color: var(--sc-ink);
}
.sc-btn:hover { border-color: var(--sc-muted); }
.sc-btn--primary { align-self: flex-start; border-color: #fff; background: #fff; color: var(--sc-ink); }
.sc-btn--primary:hover { background: var(--sc-signal-soft); }
.sc-btn:focus-visible { outline: 2px solid var(--sc-signal); outline-offset: 2px; }

.sc-sr { position: absolute; width: 1px; height: 1px; margin: -1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; }

@keyframes sc-turn { to { transform: rotate(360deg); } }
@media (prefers-reduced-motion: reduce) {
  .sc-mark--run path { animation: none; }
  .sc-seg, .sc-level span { transition: none; }
}
`
