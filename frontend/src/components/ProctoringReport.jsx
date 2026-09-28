import { useEffect, useState } from 'react'
import api from '../api/client'

// api baseURL is already '/api/v1' — strip that prefix from server-provided URLs.
const toApiPath = (url) => (url || '').replace(/^\/api\/v1/, '')

const SEV_COLORS = {
  CRITICAL: { fg: '#b91c1c', bg: '#fee2e2' },
  HIGH:     { fg: '#c2410c', bg: '#ffedd5' },
  MEDIUM:   { fg: '#b45309', bg: '#fef3c7' },
  LOW:      { fg: '#3f6212', bg: '#ecfccb' },
}

const decisionColor = (d) =>
  d === 'NORMAL' ? { fg: '#15803d', bg: '#dcfce7' }
  : d === 'WARNING' ? { fg: '#92400e', bg: '#fef3c7' }
  : d === 'SUSPICIOUS' ? { fg: '#c2410c', bg: '#ffedd5' }
  : { fg: '#b91c1c', bg: '#fee2e2' }  // CHEATING (shown as HIGH RISK)

const decisionLabel = (d) => (d === 'CHEATING' ? 'HIGH RISK' : d)

// Panel palettes: "detected" is neutral (a record of what happened); "ai" and "rule"
// are tinted so a reviewer never mistakes a model's conclusion for an observation.
const PANEL = {
  detected: { fg: '#374151', bg: '#f9fafb', border: '#e5e7eb' },
  ai:       { fg: '#6d28d9', bg: '#f5f3ff', border: '#ddd6fe' },
  rule:     { fg: '#1d4ed8', bg: '#eff6ff', border: '#bfdbfe' },
}

// Server timestamps are naive UTC (datetime.utcnow().isoformat()). Without a zone
// suffix the browser would read them as local time and shift every event.
const parseServerTime = (s) => {
  if (!s) return null
  const d = new Date(/(Z|[+-]\d\d:?\d\d)$/i.test(s) ? s : `${s}Z`)
  return Number.isNaN(d.getTime()) ? null : d
}

const pad2 = (n) => String(n).padStart(2, '0')
const fmtClock = (d) => (d ? d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }) : '—')
const fmtOffset = (ms) => {
  if (ms == null || ms < 0) return null
  const t = Math.round(ms / 1000)
  const h = Math.floor(t / 3600), m = Math.floor((t % 3600) / 60), s = t % 60
  return h ? `+${h}:${pad2(m)}:${pad2(s)}` : `+${pad2(m)}:${pad2(s)}`
}
const fmtDuration = (ms) => {
  const t = Math.max(0, Math.round(ms / 1000))
  const h = Math.floor(t / 3600), m = Math.floor((t % 3600) / 60), s = t % 60
  return h ? `${h}h ${m}m` : m ? `${m}m ${s}s` : `${s}s`
}
const pct = (x) => (x == null ? null : `${Math.round(x * 100)}%`)
const fixed = (x, d) => (x == null ? null : Number(x).toFixed(d))
const human = (t) => (t || '').toLowerCase().replaceAll('_', ' ').replace(/^\w/, (c) => c.toUpperCase())

// Events the browser reports directly. No model is involved, so the "interpretation"
// is a fixed rule (strike counting), not an AI judgement.
const BROWSER_EVENTS = new Set(['TAB_SWITCH', 'FULLSCREEN_EXIT', 'FULLSCREEN_UNAVAILABLE'])

const MODULE_SOURCE = {
  face: 'Camera · face model',
  object: 'Camera · object model',
  pose: 'Camera · pose model',
  audio: 'Microphone · audio model',
  behavioral: 'Typing & mouse · behaviour model',
  proctor: 'Proctoring system',
}
const eventSource = (v) => (BROWSER_EVENTS.has(v.type) ? 'Browser' : MODULE_SOURCE[v.module] || v.module)
const isAiEvent = (v) => !BROWSER_EVENTS.has(v.type) && v.module !== 'proctor'

// What the model concluded from the measurements. Worded as possibilities on purpose:
// these are the pipeline's reading of the signal, not established facts.
const AI_INTERPRETATION = {
  NO_FACE: 'Candidate may have left the seat or covered the camera.',
  MULTIPLE_FACES: 'Another person may be in the room.',
  IDENTITY_MISMATCH: 'The person on camera may not be the registered candidate.',
  HEAD_TURNED: 'Candidate may be looking away from the screen.',
  SUSPICIOUS_GAZE: 'Candidate may be reading something off-screen.',
  PHONE_DETECTED: 'A phone may be in use.',
  PHONE_GESTURE: 'Posture resembles holding a phone.',
  PERSON_DETECTED: 'Possibly another person. Unconfirmed: based on body shape only, with no second face seen.',
  CANDIDATE_LEFT: 'Candidate may have left the seat.',
  LEANING_FORWARD: 'Minor posture change. Rarely meaningful on its own.',
  LEANING_SIDEWAYS: 'Minor posture change. Rarely meaningful on its own.',
  HEAD_DOWN: 'Candidate may be looking at notes, a keyboard or a device below the screen.',
  MULTIPLE_VOICES: 'Someone else may be speaking. The voice-separation model is new, so confirm before relying on it.',
  FLAGGED_SPEECH: 'Speech may relate to the test content.',
  BACKGROUND_NOISE: 'Noisy surroundings. Not evidence of misconduct by itself.',
  MIC_MUTED: 'Audio could not be monitored during this period.',
  BEHAVIORAL_ANOMALY: "Typing or mouse pattern differs from this candidate's earlier behaviour.",
}
const OBJECT_INTERPRETATION = 'An item not allowed during the test may be present.'

function ruleText(v) {
  const d = v.details || {}
  switch (v.type) {
    case 'TAB_SWITCH':
    case 'FULLSCREEN_EXIT':
      return d.strike != null ? `Counted as strike ${d.strike}.` : 'Counted as a strike.'
    case 'FULLSCREEN_UNAVAILABLE':
      return 'Not a violation. Fullscreen could not be monitored for this session.'
    case 'TEST_TERMINATED':
      return d.reason ? `Strike limit reached for "${human(d.reason)}".` : 'Strike limit reached.'
    default:
      return 'Recorded by the proctoring rules.'
  }
}

/** Plain description of what was recorded, plus the raw measurements behind it. */
function describeDetected(v) {
  const d = v.details || {}
  const o = d.observed || {} // absent on events recorded before measurements were stored
  const facts = []
  const add = (label, value) => { if (value != null && value !== '') facts.push([label, value]) }
  let text

  switch (v.type) {
    case 'NO_FACE':
      text = 'No face found in the camera frame.'
      break
    case 'MULTIPLE_FACES':
      text = 'More than one face found in the camera frame.'
      add('Faces', o.face_count)
      break
    case 'IDENTITY_MISMATCH':
      text = 'Face on camera compared with the registered photo.'
      add('Similarity', fixed(d.similarity, 2))
      break
    case 'HEAD_TURNED':
      text = 'Head angle measured outside the forward-facing range.'
      add('Yaw', o.yaw_deg != null ? `${o.yaw_deg}°` : null)
      add('Pitch', o.pitch_deg != null ? `${o.pitch_deg}°` : null)
      break
    case 'SUSPICIOUS_GAZE':
      text = o.gaze_direction
        ? `Eye direction measured as ${o.gaze_direction.toLowerCase()}.`
        : 'Eye direction measured away from centre.'
      break
    case 'PHONE_GESTURE':
      text = 'Hand and arm position tracked by the pose model.'
      add('Gesture confidence', pct(o.phone_gesture_confidence))
      break
    case 'CANDIDATE_LEFT':
      text = 'Body mostly not visible to the camera.'
      add('Body visibility', pct(o.visibility_score))
      break
    case 'LEANING_FORWARD':
    case 'LEANING_SIDEWAYS':
    case 'HEAD_DOWN':
      text = `Posture change measured (${human(v.type).toLowerCase()}).`
      add('Posture deviation', fixed(o.posture_deviation, 2))
      break
    case 'MULTIPLE_VOICES':
      text = d.voice_count ? `${d.voice_count} distinct voices estimated.` : 'More than one voice estimated.'
      break
    case 'BACKGROUND_NOISE':
      text = 'Sound level above the room baseline.'
      break
    case 'FLAGGED_SPEECH':
      text = 'Speech transcript matched a flagged keyword.'
      break
    case 'MIC_MUTED':
      text = 'No microphone input received.'
      break
    case 'BEHAVIORAL_ANOMALY':
      text = 'Typing and mouse measurements recorded.'
      add('Anomaly score', fixed(o.anomaly_score, 2))
      add('Main factors', o.top_contributing_features?.length ? o.top_contributing_features.join(', ') : null)
      break
    case 'TAB_SWITCH':
      text = 'Browser reported that the test tab lost focus.'
      add('Switch count', d.tab_switch_count)
      break
    case 'FULLSCREEN_EXIT':
      text = 'Browser left fullscreen mode.'
      add('Exit count', d.exit_count)
      break
    case 'FULLSCREEN_UNAVAILABLE':
      text = 'Browser has no fullscreen support.'
      add('Reason', d.reason)
      break
    case 'TEST_TERMINATED':
      text = 'Test was ended automatically.'
      break
    default:
      if (v.type.endsWith('_DETECTED')) {
        const objs = o.objects || []
        text = objs.length
          ? `Object model found: ${objs.map((x) => `${x.label} (${pct(x.confidence)} confidence)`).join(', ')}.`
          : `${human(v.type.replace(/_DETECTED$/, ''))} found by the object model.`
      } else {
        text = human(v.type)
      }
  }

  if (v.module === 'audio') {
    add('Level', d.spl_dbfs != null ? `${d.spl_dbfs} dBFS` : null)
    add('Peak', d.peak_dbfs != null ? `${d.peak_dbfs} dBFS` : null)
    add('Noise floor', d.noise_floor_dbfs != null ? `${d.noise_floor_dbfs} dBFS` : null)
    add('Voices', d.voice_count)
  }
  add('Frame', d.frame)
  return { text, facts }
}

// MEDIUM flags are re-recorded every ~10s while they last. Fold those continuation
// rows (same type, no new evidence frame, close together) into one timeline entry.
// Matched against the latest entry of the same type, not just the previous row:
// flags raised on the same frame (NO_FACE + CANDIDATE_LEFT) interleave.
const CONTINUATION_GAP_MS = 15000

function buildTimeline(violations) {
  const sorted = violations
    .map((v) => ({ ...v, at: parseServerTime(v.timestamp) }))
    .sort((a, b) => (a.at?.getTime() ?? 0) - (b.at?.getTime() ?? 0))
  const events = []
  const latestByType = new Map()
  for (const v of sorted) {
    const open = latestByType.get(v.type)
    if (open && !v.evidence_url && v.at && open.endAt && v.at - open.endAt <= CONTINUATION_GAP_MS) {
      open.endAt = v.at
      open.readings += 1
      open.ids.push(v.id)
      continue
    }
    const entry = { ...v, endAt: v.at, readings: 1, ids: [v.id] }
    events.push(entry)
    latestByType.set(v.type, entry)
  }
  return events
}

// Fetches a protected evidence image as an authorized blob and shows a thumbnail.
function EvidenceThumb({ url, caption, onOpen }) {
  const [src, setSrc] = useState(null)
  const [err, setErr] = useState(false)
  useEffect(() => {
    let revoked = false, objUrl = null
    ;(async () => {
      try {
        const r = await api.get(toApiPath(url), { responseType: 'blob' })
        if (revoked) return
        objUrl = URL.createObjectURL(r.data)
        setSrc(objUrl)
      } catch { setErr(true) }
    })()
    return () => { revoked = true; if (objUrl) URL.revokeObjectURL(objUrl) }
  }, [url])
  if (err) return <span style={{ fontSize: 10, color: '#9ca3af' }}>Evidence image unavailable</span>
  if (!src) return <span style={{ fontSize: 10, color: '#9ca3af' }}>Loading evidence…</span>
  return (
    <button
      type="button"
      onClick={() => onOpen({ src, caption })}
      title="View full size"
      style={{ padding: 0, border: 'none', background: 'none', cursor: 'zoom-in', display: 'inline-flex', flexDirection: 'column', alignItems: 'flex-start', gap: 2 }}
    >
      <img src={src} alt={caption} style={{ width: 120, height: 90, objectFit: 'cover', borderRadius: 4, border: '1px solid #e5e7eb' }} />
      <span style={{ fontSize: 10, color: '#6b7280' }}>Evidence frame · click to enlarge</span>
    </button>
  )
}

function Lightbox({ image, onClose }) {
  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])
  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={image.caption}
      onClick={onClose}
      style={{ position: 'fixed', inset: 0, background: 'rgba(17,24,39,0.85)', zIndex: 10000, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', padding: 16, gap: 10 }}
    >
      <img src={image.src} alt={image.caption} onClick={(e) => e.stopPropagation()} style={{ maxWidth: '100%', maxHeight: '85vh', borderRadius: 6 }} />
      <div style={{ color: '#f9fafb', fontSize: 12, textAlign: 'center' }}>
        {image.caption} · click outside the image or press Esc to close
      </div>
    </div>
  )
}

// What face matching compared the candidate against. Never an identity-document check:
// the reference is the photo the candidate uploaded with the application, or a snapshot
// they took at the start of the test when there was none.
const REFERENCE = {
  application_photo: { t: 'Compared with application photo', fg: '#15803d', bg: '#dcfce7' },
  live_snapshot: { t: 'Compared with a start-of-test snapshot', fg: '#b45309', bg: '#fef3c7',
    note: 'No application photo was on file, so the reference was taken by the candidate at the start. It shows the same person stayed for the test, not who they are.' },
  none: { t: 'Face not compared', fg: '#6b7280', bg: '#f3f4f6',
    note: 'No reference photo was available, so identity was not compared.' },
}

function referenceOf(s) {
  if (s.reference) return s.reference
  // Sessions reported before `reference` existed.
  return s.live_enrolled ? 'live_snapshot' : s.face_registered ? 'application_photo' : 'none'
}

function IdentityBadge({ s }) {
  const state = REFERENCE[referenceOf(s)] || REFERENCE.none
  return (
    <span
      title="Face matching compares the person on camera with this reference. It is not an identity-document check."
      style={{ fontSize: 11, fontWeight: 700, padding: '2px 8px', borderRadius: 6, color: state.fg, background: state.bg }}
    >{state.t}</span>
  )
}

function PanelHeading({ kind, children }) {
  return (
    <div style={{ fontSize: 10, fontWeight: 700, letterSpacing: 0.4, textTransform: 'uppercase', color: PANEL[kind].fg, marginBottom: 4 }}>
      {children}
    </div>
  )
}

const panelStyle = (kind) => ({
  border: `1px solid ${PANEL[kind].border}`, background: PANEL[kind].bg, borderRadius: 8, padding: '8px 10px', minWidth: 0,
})

function Legend() {
  const chip = (kind, title, text) => (
    <div style={{ ...panelStyle(kind), flex: '1 1 200px' }}>
      <PanelHeading kind={kind}>{title}</PanelHeading>
      <div style={{ fontSize: 12, color: '#4b5563' }}>{text}</div>
    </div>
  )
  return (
    <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
      {chip('detected', 'Detected', 'What the camera, microphone or browser recorded, with the measurements and evidence frame.')}
      {chip('ai', 'AI interpretation', "The model's conclusion from that recording. It can be wrong, so review the evidence before acting.")}
      {chip('rule', 'Rule applied', 'Fixed proctoring rules such as strike counting. No AI judgement is involved.')}
      <div style={{ ...panelStyle('detected'), background: '#fff', flex: '1 1 200px' }}>
        <div style={{ fontSize: 10, fontWeight: 700, letterSpacing: 0.4, textTransform: 'uppercase', color: '#374151', marginBottom: 4 }}>Reviewer outcome</div>
        <div style={{ fontSize: 12, color: '#4b5563' }}>What a person concluded after checking the evidence: confirmed, dismissed or inconclusive.</div>
      </div>
    </div>
  )
}

// A person's conclusion after checking the evidence. Distinct from the AI panels above
// it: those describe what the software recorded and inferred; this is the decision.
const OUTCOMES = [
  { key: 'confirmed', label: 'Confirmed', fg: '#991b1b', bg: '#fee2e2',
    hint: 'The evidence shows what was flagged.' },
  { key: 'dismissed', label: 'Dismissed', fg: '#166534', bg: '#dcfce7',
    hint: 'Not a concern, for example a false detection or something allowed.' },
  { key: 'inconclusive', label: 'Inconclusive', fg: '#92400e', bg: '#fef3c7',
    hint: 'The evidence is not enough to say either way.' },
]
const outcomeOf = (key) => OUTCOMES.find((o) => o.key === key)

function OutcomeChip({ outcome }) {
  const o = outcomeOf(outcome)
  if (!o) return null
  return <span style={{ fontSize: 11, fontWeight: 700, padding: '2px 8px', borderRadius: 6, color: o.fg, background: o.bg }}>{o.label}</span>
}

/**
 * Record or change a reviewer outcome. `violationIds` null = a verdict on the whole
 * session. Remarks are required to confirm: that outcome can count against a candidate.
 */
function ReviewControl({ sessionId, violationIds, review, subject, onSaved }) {
  const [editing, setEditing] = useState(false)
  const [outcome, setOutcome] = useState(review?.outcome || null)
  const [remarks, setRemarks] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState(null)

  const start = (key) => { setOutcome(key); setRemarks(''); setError(null); setEditing(true) }
  const needsRemarks = outcome === 'confirmed' && !remarks.trim()

  const save = async () => {
    if (!outcome || needsRemarks) return
    setSaving(true); setError(null)
    try {
      await api.post('/proctor/reviews', {
        session_id: sessionId,
        violation_ids: violationIds,
        outcome,
        remarks: remarks.trim() || null,
      })
      setEditing(false)
      onSaved?.()
    } catch (e) {
      setError(e?.response?.data?.detail || 'Could not save the review. Try again.')
    } finally {
      setSaving(false)
    }
  }

  const btn = (o, active) => ({
    fontSize: 12, fontWeight: 700, padding: '5px 12px', borderRadius: 6, cursor: 'pointer',
    border: `1px solid ${active ? o.fg : '#d1d5db'}`, color: active ? o.fg : '#374151',
    background: active ? o.bg : '#fff',
  })

  if (!editing) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        {review ? (
          <>
            <OutcomeChip outcome={review.outcome} />
            <span style={{ fontSize: 11, color: '#6b7280' }}>
              {review.reviewer_name || 'A reviewer'}, {fmtClock(parseServerTime(review.created_at))}
            </span>
            {review.remarks && <q style={{ fontSize: 12, color: '#374151' }}>{review.remarks}</q>}
            <button type="button" onClick={() => start(review.outcome)}
              style={{ marginLeft: 'auto', fontSize: 12, fontWeight: 600, padding: '4px 10px', borderRadius: 6, border: '1px solid #d1d5db', background: '#fff', cursor: 'pointer' }}>
              Change
            </button>
          </>
        ) : (
          <>
            <span style={{ fontSize: 12, color: '#6b7280' }}>Not reviewed yet.</span>
            {OUTCOMES.map((o) => (
              <button key={o.key} type="button" onClick={() => start(o.key)} title={o.hint} style={btn(o, false)}>
                {o.label}
              </button>
            ))}
          </>
        )}
      </div>
    )
  }

  const selected = outcomeOf(outcome)
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      <div role="radiogroup" aria-label={`Outcome for ${subject}`} style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
        {OUTCOMES.map((o) => (
          <button key={o.key} type="button" role="radio" aria-checked={outcome === o.key}
            onClick={() => setOutcome(o.key)} style={btn(o, outcome === o.key)}>
            {o.label}
          </button>
        ))}
      </div>
      {selected && <div style={{ fontSize: 11, color: '#6b7280' }}>{selected.hint}</div>}
      <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 12, color: '#374151' }}>
        Remarks{outcome === 'confirmed' ? ' (required to confirm)' : ' (optional)'}
        <textarea value={remarks} onChange={(e) => setRemarks(e.target.value)} rows={2} maxLength={2000}
          placeholder="What did you see in the evidence?"
          style={{ fontFamily: 'inherit', fontSize: 12, padding: '6px 8px', borderRadius: 6, border: '1px solid #d1d5db', resize: 'vertical' }} />
      </label>
      {error && <div role="alert" style={{ fontSize: 12, color: '#b91c1c' }}>{error}</div>}
      <div style={{ display: 'flex', gap: 8 }}>
        <button type="button" onClick={save} disabled={saving || !outcome || needsRemarks}
          style={{ fontSize: 12, fontWeight: 700, padding: '6px 14px', borderRadius: 6, border: 'none', color: '#fff', background: '#1f2937', cursor: saving || !outcome || needsRemarks ? 'not-allowed' : 'pointer', opacity: saving || !outcome || needsRemarks ? 0.5 : 1 }}>
          {saving ? 'Saving…' : 'Save review'}
        </button>
        <button type="button" onClick={() => setEditing(false)}
          style={{ fontSize: 12, fontWeight: 600, padding: '6px 12px', borderRadius: 6, border: '1px solid #d1d5db', background: '#fff', cursor: 'pointer' }}>
          Cancel
        </button>
      </div>
    </div>
  )
}

function ReviewHistory({ history, typeById }) {
  const [open, setOpen] = useState(false)
  if (!history?.length) return null
  return (
    <div style={{ marginTop: 10 }}>
      <button type="button" onClick={() => setOpen(!open)} aria-expanded={open}
        style={{ fontSize: 12, fontWeight: 600, color: '#374151', background: 'none', border: 'none', padding: 0, cursor: 'pointer', textDecoration: 'underline' }}>
        {open ? 'Hide' : 'Show'} review history ({history.length})
      </button>
      {open && (
        <ol style={{ listStyle: 'none', margin: '6px 0 0', padding: 0, display: 'flex', flexDirection: 'column', gap: 6 }}>
          {history.map((r) => (
            <li key={r.id} style={{ fontSize: 12, color: '#374151', display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'baseline' }}>
              <span style={{ color: '#6b7280' }}>{parseServerTime(r.created_at)?.toLocaleString() || ''}</span>
              <span>{r.reviewer_name || 'A reviewer'}</span>
              <OutcomeChip outcome={r.outcome} />
              <span>{r.violation_id ? human(typeById[r.violation_id] || 'event') : 'Whole session'}</span>
              {r.remarks && <q>{r.remarks}</q>}
            </li>
          ))}
        </ol>
      )}
    </div>
  )
}

function TimelineEvent({ v, sessionId, sessionStart, onOpenEvidence, onReviewed }) {
  const detected = describeDetected(v)
  const sev = SEV_COLORS[v.severity] || SEV_COLORS.LOW
  const d = v.details || {}
  const source = eventSource(v)
  const ai = isAiEvent(v)
  const interpretation = ai
    ? AI_INTERPRETATION[v.type] || (v.type.endsWith('_DETECTED') ? OBJECT_INTERPRETATION : 'Flagged by the model for review.')
    : ruleText(v)
  const offset = sessionStart && v.at ? fmtOffset(v.at - sessionStart) : null

  return (
    <li style={{ display: 'grid', gridTemplateColumns: '84px minmax(0, 1fr)', gap: 12, padding: '12px 0', borderTop: '1px solid #f3f4f6' }}>
      <div style={{ fontSize: 12 }}>
        <div style={{ fontWeight: 700, fontFamily: "'JetBrains Mono', monospace", color: '#111827' }}>{fmtClock(v.at)}</div>
        {offset && <div style={{ color: '#6b7280', fontFamily: "'JetBrains Mono', monospace" }} title="Time since the session started">{offset}</div>}
        {v.readings > 1 && (
          <div style={{ color: '#6b7280', fontSize: 11, marginTop: 4 }}>
            {v.endAt && v.at && v.endAt - v.at >= 1000
              ? `Continued for ${fmtDuration(v.endAt - v.at)} (${v.readings} readings)`
              : `${v.readings} readings`}
          </div>
        )}
      </div>

      <div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', marginBottom: 6 }}>
          <span style={{ fontSize: 13, fontWeight: 700, color: '#111827' }}>{human(v.type)}</span>
          <span style={{ fontSize: 11, color: '#6b7280' }}>{source}</span>
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 8 }}>
          <div style={panelStyle('detected')}>
            <PanelHeading kind="detected">Detected</PanelHeading>
            <div style={{ fontSize: 12, color: '#111827' }}>{detected.text}</div>
            {detected.facts.length > 0 && (
              <dl style={{ display: 'flex', flexWrap: 'wrap', gap: '2px 12px', margin: '6px 0 0', fontSize: 11 }}>
                {detected.facts.map(([label, value]) => (
                  <div key={label} style={{ display: 'flex', gap: 4 }}>
                    <dt style={{ color: '#6b7280' }}>{label}</dt>
                    <dd style={{ margin: 0, color: '#111827', fontWeight: 600 }}>{value}</dd>
                  </div>
                ))}
              </dl>
            )}
            {v.evidence_url && (
              <div style={{ marginTop: 8 }}>
                <EvidenceThumb
                  url={v.evidence_url}
                  caption={`${human(v.type)} · ${source} · ${fmtClock(v.at)}`}
                  onOpen={onOpenEvidence}
                />
              </div>
            )}
          </div>

          <div style={panelStyle(ai ? 'ai' : 'rule')}>
            <PanelHeading kind={ai ? 'ai' : 'rule'}>{ai ? 'AI interpretation' : 'Rule applied'}</PanelHeading>
            <div style={{ fontSize: 12, color: '#111827' }}>{interpretation}</div>
            <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 8, marginTop: 6, fontSize: 11, color: '#4b5563' }}>
              <span style={{ fontSize: 10, fontWeight: 700, padding: '1px 7px', borderRadius: 5, color: sev.fg, background: sev.bg }}>{v.severity}</span>
              {v.score_impact > 0 && <span>Weight +{Math.round(v.score_impact)}</span>}
              {ai && d.cheating_score != null && <span>Integrity flags at this moment: {Math.round(d.cheating_score)}</span>}
            </div>
          </div>
        </div>

        {v.reviewable !== false && (
          <div style={{ marginTop: 8, padding: '8px 10px', borderRadius: 8, border: '1px solid #e5e7eb', background: '#fff' }}>
            <div style={{ fontSize: 10, fontWeight: 700, letterSpacing: 0.4, textTransform: 'uppercase', color: '#374151', marginBottom: 6 }}>
              Reviewer outcome{v.readings > 1 ? ` (covers all ${v.readings} readings)` : ''}
            </div>
            <ReviewControl sessionId={sessionId} violationIds={v.ids} review={v.review}
              subject={human(v.type)} onSaved={onReviewed} />
          </div>
        )}
      </div>
    </li>
  )
}

/** Which attempt this is, the adjustments it ran with, and the candidate's own words. */
function AttemptContext({ data }) {
  const attempt = data.attempt_number || 1
  const adjustments = data.accommodations_summary || []
  const notes = data.candidate_explanations || []
  if (attempt === 1 && !adjustments.length && !notes.length) return null
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8, padding: '12px 14px', borderRadius: 10, border: '1px solid #e5e7eb', background: '#fff' }}>
      {(attempt > 1 || adjustments.length > 0) && (
        <div style={{ fontSize: 13, color: '#111827' }}>
          {attempt > 1 && <b>Attempt {attempt}. </b>}
          {adjustments.length > 0
            ? `Taken with approved adjustments: ${adjustments.join(', ')}.`
            : 'Taken without adjustments.'}
        </div>
      )}
      {notes.length > 0 && (
        <div>
          <div style={{ fontSize: 12, fontWeight: 800, color: '#111827', marginBottom: 4 }}>In the candidate's words</div>
          {notes.map((n) => (
            <blockquote key={n.id} style={{ margin: '4px 0 0', padding: '8px 12px', borderLeft: '3px solid #0f766e', background: '#f0f7f5', fontSize: 13, color: '#1f2937', lineHeight: 1.5 }}>
              {n.text}
              <div style={{ marginTop: 4, fontSize: 11, color: '#6b7280' }}>
                {parseServerTime(n.created_at)?.toLocaleString() || ''}
              </div>
            </blockquote>
          ))}
        </div>
      )}
    </div>
  )
}

/**
 * Proctoring report for a given interview/test. Reads persisted sessions +
 * violations via GET /proctor/results/{interviewId} (works after the test ends).
 */
export default function ProctoringReport({ interviewId }) {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [lightbox, setLightbox] = useState(null)
  const [version, setVersion] = useState(0)   // bump to reload after a review is saved
  const reload = () => setVersion((n) => n + 1)

  useEffect(() => {
    if (!interviewId) return
    let cancelled = false
    if (version === 0) setLoading(true)
    setError(null)
    ;(async () => {
      try {
        const r = await api.get(`/proctor/results/${interviewId}`)
        if (!cancelled) setData(r.data)
      } catch (e) {
        if (!cancelled) setError(e?.response?.data?.detail || 'Failed to load proctoring report')
      } finally {
        if (!cancelled) setLoading(false)
      }
    })()
    return () => { cancelled = true }
  }, [interviewId, version])

  if (loading) return <div style={{ padding: 12, color: '#6b7280', fontSize: 13 }}>Loading proctoring report…</div>
  if (error) return <div style={{ padding: 12, color: '#b91c1c', fontSize: 13 }}>{error}</div>
  if (!data) return null
  const context = <AttemptContext data={data} />
  if (data.session_count === 0)
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        {context}
        <div style={{ padding: 12, color: '#6b7280', fontSize: 13 }}>No proctoring sessions recorded for this test.</div>
      </div>
    )

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      {context}
      <Legend />

      {data.sessions.map((s) => {
        const dc = decisionColor(s.decision)
        const started = parseServerTime(s.started_at)
        const ended = parseServerTime(s.ended_at)
        const timeline = buildTimeline(s.violations)
        const toReview = timeline.filter((v) => v.reviewable !== false && !v.review).length
        const typeById = Object.fromEntries(s.violations.map((v) => [v.id, v.type]))
        return (
          <div key={s.session_id} style={{ border: '1px solid #e5e7eb', borderRadius: 10, padding: 14 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
              <span style={{ fontSize: 12, fontWeight: 700, padding: '3px 10px', borderRadius: 6, color: dc.fg, background: dc.bg }}>
                {decisionLabel(s.decision)}
              </span>
              <span style={{ fontSize: 13, fontWeight: 700 }}>
                Assessment Integrity Flags: {Math.round(s.cheating_score || 0)}{s.max_cheating_score != null ? ` (peak ${Math.round(s.max_cheating_score)})` : ''}
              </span>
              <IdentityBadge s={s} />
              <span style={{ fontSize: 12, color: '#6b7280' }}>
                {s.total_violations} event{s.total_violations === 1 ? '' : 's'}
              </span>
            </div>
            <div style={{ fontSize: 11, color: PANEL.ai.fg, marginTop: 4 }}>
              AI summary, built from the events below. Check the evidence before drawing conclusions.
            </div>
            <div style={{ fontSize: 11, color: '#6b7280', marginTop: 4 }}>
              Face matching compares the person on camera with the reference shown above. It is
              not an identity-document check.
              {REFERENCE[referenceOf(s)]?.note ? ` ${REFERENCE[referenceOf(s)].note}` : ''}
            </div>
            <div style={{ fontSize: 12, color: '#6b7280', marginTop: 6 }}>
              {started ? `Started ${started.toLocaleString()}` : 'Start time unknown'}
              {ended ? ` · Ended ${fmtClock(ended)}` : s.status === 'active' ? ' · Still active' : ''}
              {started && ended ? ` (${fmtDuration(ended - started)})` : ''}
              {s.status === 'terminated' ? ' · Ended early by proctoring, needs review' : ''}
            </div>

            <div style={{ fontSize: 12, color: '#6b7280', marginTop: 4 }}>
              {s.monitoring_consent
                ? `Candidate agreed to the monitoring disclosure (version ${s.monitoring_consent.disclosure_version}) at ${fmtClock(parseServerTime(s.monitoring_consent.recorded_at))}.`
                : 'No monitoring consent was recorded for this session (sessions before consent was collected show this too).'}
            </div>
            <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap', margin: '8px 0 4px', fontSize: 11, color: '#374151' }}>
              <span style={{ color: PANEL.ai.fg, fontWeight: 700 }}>AI module scores:</span>
              {Object.entries(s.module_scores).map(([k, v]) => (
                <span key={k}><b style={{ textTransform: 'capitalize' }}>{k}</b>: {Math.round(v || 0)}</span>
              ))}
            </div>

            <div style={{ margin: '10px 0 4px', padding: '10px 12px', borderRadius: 8, border: '1px solid #d1d5db', background: '#f9fafb' }}>
              <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, flexWrap: 'wrap', marginBottom: 6 }}>
                <span style={{ fontSize: 12, fontWeight: 800, color: '#111827' }}>Overall outcome for this session</span>
                <span style={{ fontSize: 12, color: toReview ? '#b45309' : '#6b7280' }}>
                  {timeline.length === 0 ? 'No events to review.'
                    : toReview ? `${toReview} ${toReview === 1 ? 'entry' : 'entries'} still to review.`
                    : 'Every entry has been reviewed.'}
                </span>
              </div>
              <ReviewControl sessionId={s.session_id} violationIds={null} review={s.review}
                subject="this session" onSaved={reload} />
              <ReviewHistory history={s.review_history} typeById={typeById} />
            </div>

            {timeline.length > 0 ? (
              <ol style={{ listStyle: 'none', margin: '8px 0 0', padding: 0 }}>
                {timeline.map((v) => (
                  <TimelineEvent key={v.id} v={v} sessionId={s.session_id} sessionStart={started}
                    onOpenEvidence={setLightbox} onReviewed={reload} />
                ))}
              </ol>
            ) : (
              <div style={{ fontSize: 12, color: '#9ca3af', marginTop: 8 }}>No events recorded.</div>
            )}
          </div>
        )
      })}

      {lightbox && <Lightbox image={lightbox} onClose={() => setLightbox(null)} />}
    </div>
  )
}
