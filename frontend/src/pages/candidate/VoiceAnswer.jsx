import { useState, useRef, useEffect, useCallback } from 'react'
import api from '../../api/client'

/**
 * Voice answer interface for `communication` questions.
 *
 * The candidate speaks into the mic; we capture a live transcript via the browser
 * Web Speech API AND the raw audio (encoded to a 16 kHz mono WAV client-side, so the
 * server can read it with soundfile — no ffmpeg). On stop, the recording + transcript
 * are uploaded; the server measures delivery (pace, fillers, acoustics) and returns a
 * score breakdown shown inline.
 *
 * Props:
 *   interviewId, questionId  — identify the answer
 *   savedResult              — previously returned score payload (restored on revisit)
 *   onRecorded(result)       — called after a successful upload (parent marks answered)
 */
const TARGET_SR = 16000

function encodeWav(samples, sampleRate) {
  // samples: Float32Array in [-1,1]; returns a 16-bit PCM mono WAV Blob.
  const buffer = new ArrayBuffer(44 + samples.length * 2)
  const view = new DataView(buffer)
  const writeStr = (offset, str) => { for (let i = 0; i < str.length; i++) view.setUint8(offset + i, str.charCodeAt(i)) }

  writeStr(0, 'RIFF')
  view.setUint32(4, 36 + samples.length * 2, true)
  writeStr(8, 'WAVE')
  writeStr(12, 'fmt ')
  view.setUint32(16, 16, true)          // PCM chunk size
  view.setUint16(20, 1, true)           // audio format = PCM
  view.setUint16(22, 1, true)           // mono
  view.setUint32(24, sampleRate, true)
  view.setUint32(28, sampleRate * 2, true) // byte rate
  view.setUint16(32, 2, true)           // block align
  view.setUint16(34, 16, true)          // bits per sample
  writeStr(36, 'data')
  view.setUint32(40, samples.length * 2, true)

  let off = 44
  for (let i = 0; i < samples.length; i++, off += 2) {
    const s = Math.max(-1, Math.min(1, samples[i]))
    view.setInt16(off, s < 0 ? s * 0x8000 : s * 0x7fff, true)
  }
  return new Blob([view], { type: 'audio/wav' })
}

function downsample(chunks, inRate, outRate) {
  const length = chunks.reduce((n, c) => n + c.length, 0)
  const flat = new Float32Array(length)
  let o = 0
  for (const c of chunks) { flat.set(c, o); o += c.length }
  if (outRate >= inRate) return flat
  const ratio = inRate / outRate
  const outLen = Math.floor(flat.length / ratio)
  const out = new Float32Array(outLen)
  for (let i = 0; i < outLen; i++) {
    const start = Math.floor(i * ratio)
    const end = Math.min(flat.length, Math.floor((i + 1) * ratio))
    let sum = 0
    for (let j = start; j < end; j++) sum += flat[j]
    out[i] = sum / Math.max(1, end - start)
  }
  return out
}

export default function VoiceAnswer({ interviewId, questionId, savedResult, onRecorded }) {
  const [status, setStatus] = useState(savedResult ? 'done' : 'idle') // idle|recording|processing|done|error
  const [transcript, setTranscript] = useState(savedResult?.transcript || '')
  const [interim, setInterim] = useState('')
  const [level, setLevel] = useState(0)
  const [elapsed, setElapsed] = useState(0)
  const [result, setResult] = useState(savedResult || null)
  const [error, setError] = useState('')
  const [sttSupported, setSttSupported] = useState(true)
  const [sttWarn, setSttWarn] = useState('')

  const streamRef = useRef(null)
  const ctxRef = useRef(null)
  const procRef = useRef(null)
  const analyserRef = useRef(null)
  const animRef = useRef(null)
  const chunksRef = useRef([])
  const finalTextRef = useRef('')
  const recogRef = useRef(null)
  const recordingRef = useRef(false)
  const startedAtRef = useRef(0)
  const timerRef = useRef(null)

  const cleanup = useCallback(() => {
    recordingRef.current = false
    if (timerRef.current) { clearInterval(timerRef.current); timerRef.current = null }
    if (animRef.current) { cancelAnimationFrame(animRef.current); animRef.current = null }
    if (procRef.current) { try { procRef.current.disconnect() } catch {} procRef.current = null }
    if (analyserRef.current) { try { analyserRef.current.disconnect() } catch {} analyserRef.current = null }
    if (ctxRef.current) { try { ctxRef.current.close() } catch {} ctxRef.current = null }
    if (recogRef.current) { try { recogRef.current.stop() } catch {} recogRef.current = null }
    if (streamRef.current) { streamRef.current.getTracks().forEach(t => t.stop()); streamRef.current = null }
  }, [])

  useEffect(() => () => cleanup(), [cleanup])

  const startRecording = async () => {
    setError(''); setResult(null); setTranscript(''); setInterim(''); setElapsed(0)
    finalTextRef.current = ''
    chunksRef.current = []
    setSttSupported(true); setSttWarn('')

    // Mic + Web Speech API require a secure context (https or localhost). Over a
    // plain-HTTP LAN IP both are unavailable, so surface that instead of a vague error.
    if (!window.isSecureContext) {
      setError('Recording needs a secure connection. Open this page over https:// or localhost.')
      setStatus('error')
      return
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      streamRef.current = stream

      const Ctx = window.AudioContext || window.webkitAudioContext
      const ctx = new Ctx()
      ctxRef.current = ctx
      const source = ctx.createMediaStreamSource(stream)

      // Level meter
      const analyser = ctx.createAnalyser()
      analyser.fftSize = 256
      source.connect(analyser)
      analyserRef.current = analyser
      const data = new Uint8Array(analyser.frequencyBinCount)
      const tick = () => {
        analyser.getByteFrequencyData(data)
        const avg = data.reduce((a, b) => a + b, 0) / data.length
        setLevel(Math.min(100, Math.round((avg / 255) * 140)))
        animRef.current = requestAnimationFrame(tick)
      }
      tick()

      // PCM capture for upload
      const proc = ctx.createScriptProcessor(4096, 1, 1)
      proc.onaudioprocess = (e) => {
        chunksRef.current.push(new Float32Array(e.inputBuffer.getChannelData(0)))
      }
      source.connect(proc)
      proc.connect(ctx.destination)
      procRef.current = proc

      // Live transcript via Web Speech API
      const SR = window.SpeechRecognition || window.webkitSpeechRecognition
      if (SR) {
        const recog = new SR()
        recog.continuous = true
        recog.interimResults = true
        recog.lang = 'en-US'
        recog.onresult = (ev) => {
          let intr = ''
          for (let i = ev.resultIndex; i < ev.results.length; i++) {
            const txt = ev.results[i][0].transcript
            if (ev.results[i].isFinal) finalTextRef.current += txt + ' '
            else intr += txt
          }
          setTranscript(finalTextRef.current.trim())
          setInterim(intr)
        }
        // Chrome ends recognition on silence even with continuous=true; restart while
        // we're still recording so long answers with pauses aren't truncated.
        recog.onend = () => {
          if (recordingRef.current) { try { recog.start() } catch {} }
        }
        recog.onerror = (ev) => {
          const err = ev?.error
          if (err === 'not-allowed' || err === 'service-not-allowed') {
            setSttSupported(false)
            setSttWarn('Live transcript was blocked. Allow microphone access; your audio is still recorded and scored.')
          } else if (err === 'network') {
            setSttSupported(false)
            setSttWarn('Live transcript needs an internet connection. Your audio is still recorded and scored.')
          }
          // 'no-speech' / 'aborted' are transient — onend will restart recognition.
        }
        try { recog.start() } catch {}
        recogRef.current = recog
        setSttSupported(true)
      } else {
        setSttSupported(false)
      }

      recordingRef.current = true
      startedAtRef.current = Date.now()
      timerRef.current = setInterval(() => setElapsed(Math.floor((Date.now() - startedAtRef.current) / 1000)), 250)
      setStatus('recording')
    } catch (err) {
      setError('Microphone access was blocked. Allow mic access and try again.')
      setStatus('error')
      cleanup()
    }
  }

  const stopRecording = async () => {
    const duration = (Date.now() - startedAtRef.current) / 1000
    const inRate = ctxRef.current?.sampleRate || 44100
    const chunks = chunksRef.current
    recordingRef.current = false
    if (recogRef.current) { try { recogRef.current.stop() } catch {} }
    setStatus('processing')
    setInterim('')

    // Build WAV from captured PCM, then tear down the mic.
    const samples = downsample(chunks, inRate, TARGET_SR)
    cleanup()
    const wav = encodeWav(samples, TARGET_SR)
    const finalTranscript = (finalTextRef.current || '').trim()
    setTranscript(finalTranscript)

    try {
      const fd = new FormData()
      fd.append('question_id', questionId)
      fd.append('transcript', finalTranscript)
      fd.append('duration_seconds', String(duration.toFixed(2)))
      fd.append('audio', wav, 'answer.wav')
      const res = await api.post(`/candidate/interviews/${interviewId}/voice-answer`, fd)
      setResult(res.data)
      setStatus('done')
      onRecorded && onRecorded(res.data)
    } catch (err) {
      setError('Could not upload your answer. Please try recording again.')
      setStatus('error')
    }
  }

  const mm = String(Math.floor(elapsed / 60)).padStart(2, '0')
  const ss = String(elapsed % 60).padStart(2, '0')
  const b = result?.breakdown || {}

  return (
    <div style={s.wrap}>
      <div style={s.banner}>
        🎤 Speak your answer aloud. We capture a live transcript and measure how clearly you communicate
        (pace, clarity, and voice confidence).
      </div>

      {/* Controls */}
      <div style={s.controls}>
        {status === 'idle' && (
          <button style={s.recBtn} onClick={startRecording}>● Start Recording</button>
        )}
        {status === 'recording' && (
          <>
            <button style={s.stopBtn} onClick={stopRecording}>■ Stop &amp; Submit</button>
            <span style={s.timer}>{mm}:{ss}</span>
            <div style={s.meterWrap}>
              <div style={{ ...s.meterFill, width: `${level}%`, background: level > 60 ? '#22c55e' : level > 15 ? '#f59e0b' : '#94a3b8' }} />
            </div>
            <span style={s.recDot}>REC</span>
          </>
        )}
        {status === 'processing' && <span style={s.processing}>Analyzing your answer…</span>}
        {status === 'done' && (
          <button style={s.reRecBtn} onClick={startRecording}>↻ Re-record</button>
        )}
        {status === 'error' && (
          <button style={s.recBtn} onClick={startRecording}>Try Again</button>
        )}
      </div>

      {!sttSupported && status !== 'idle' && (
        <div style={s.warn}>
          {sttWarn || "Live transcript isn't supported in this browser (use Chrome or Edge). Your audio is still recorded and scored."}
        </div>
      )}
      {error && <div style={s.errBox}>{error}</div>}

      {/* Live / final transcript */}
      {(transcript || interim) && (
        <div style={s.transcriptBox}>
          <div style={s.transcriptLabel}>Transcript</div>
          <div style={s.transcriptText}>
            {transcript} <span style={{ color: '#9ca3af' }}>{interim}</span>
          </div>
        </div>
      )}

      {/* Score breakdown */}
      {status === 'done' && result && (
        <div style={s.scoreCard}>
          <div style={s.scoreHead}>
            <span style={s.scoreLabel}>Communication score</span>
            <span style={s.scoreVal}>{Math.round(result.communication_score)}<span style={s.scoreOf}>/100</span></span>
          </div>
          <div style={s.metrics}>
            {[
              ['Pace', `${Math.round(b.words_per_minute || 0)} wpm`, b.pace_score],
              ['Clarity', `${b.filler_count || 0} fillers`, b.filler_score],
              ['Voice', '', b.acoustic_score],
              ['Fluency', `${b.pause_count || 0} pauses`, b.fluency_score],
            ].map(([label, sub, val]) => (
              <div key={label} style={s.metric}>
                <div style={s.metricTop}>
                  <span style={s.metricLabel}>{label}</span>
                  <span style={s.metricScore}>{Math.round(val || 0)}</span>
                </div>
                <div style={s.metricBarBg}>
                  <div style={{ ...s.metricBarFill, width: `${Math.round(val || 0)}%` }} />
                </div>
                {sub && <span style={s.metricSub}>{sub}</span>}
              </div>
            ))}
          </div>
          {Array.isArray(b.feedback) && b.feedback.length > 0 && (
            <ul style={s.feedback}>
              {b.feedback.map((f, i) => <li key={i}>{f}</li>)}
            </ul>
          )}
          <div style={s.savedNote}>✓ Answer saved. You can re-record to replace it.</div>
        </div>
      )}
    </div>
  )
}

const s = {
  wrap: { marginTop: 18 },
  banner: { background: '#eff6ff', border: '1px solid #bfdbfe', color: '#1e40af', borderRadius: 8, padding: '10px 14px', fontSize: '0.85rem', lineHeight: 1.5, marginBottom: 14 },
  controls: { display: 'flex', alignItems: 'center', gap: 14, flexWrap: 'wrap', minHeight: 44 },
  recBtn: { background: '#dc2626', color: '#fff', border: 'none', borderRadius: 8, padding: '10px 18px', fontSize: '0.9rem', fontWeight: 600, cursor: 'pointer' },
  stopBtn: { background: '#1f2937', color: '#fff', border: 'none', borderRadius: 8, padding: '10px 18px', fontSize: '0.9rem', fontWeight: 600, cursor: 'pointer' },
  reRecBtn: { background: '#fff', color: '#1a73e8', border: '1.5px solid #1a73e8', borderRadius: 8, padding: '9px 16px', fontSize: '0.88rem', fontWeight: 600, cursor: 'pointer' },
  timer: { fontFamily: 'monospace', fontSize: '1.05rem', fontWeight: 600, color: '#111' },
  meterWrap: { flex: 1, maxWidth: 220, height: 8, background: '#e5e7eb', borderRadius: 6, overflow: 'hidden' },
  meterFill: { height: '100%', borderRadius: 6, transition: 'width 0.1s ease' },
  recDot: { color: '#dc2626', fontWeight: 700, fontSize: '0.75rem', letterSpacing: 1, animation: 'blink 1s infinite' },
  processing: { color: '#6b7280', fontSize: '0.9rem' },
  warn: { marginTop: 10, background: '#fffbeb', border: '1px solid #fde68a', color: '#92400e', borderRadius: 8, padding: '8px 12px', fontSize: '0.82rem' },
  errBox: { marginTop: 10, background: '#fef2f2', border: '1px solid #fca5a5', color: '#dc2626', borderRadius: 8, padding: '8px 12px', fontSize: '0.82rem' },
  transcriptBox: { marginTop: 14, background: '#f9fafb', border: '1px solid #e5e7eb', borderRadius: 8, padding: '12px 14px' },
  transcriptLabel: { fontSize: '0.72rem', textTransform: 'uppercase', letterSpacing: 0.5, color: '#6b7280', marginBottom: 6, fontWeight: 600 },
  transcriptText: { fontSize: '0.92rem', color: '#111', lineHeight: 1.6, minHeight: 24 },
  scoreCard: { marginTop: 16, border: '1px solid #e5e7eb', borderRadius: 10, padding: 16, background: '#fff' },
  scoreHead: { display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', marginBottom: 14 },
  scoreLabel: { fontSize: '0.9rem', fontWeight: 600, color: '#374151' },
  scoreVal: { fontSize: '1.6rem', fontWeight: 700, color: '#1a73e8' },
  scoreOf: { fontSize: '0.9rem', color: '#9ca3af', fontWeight: 500 },
  metrics: { display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(120px, 1fr))', gap: 14 },
  metric: {},
  metricTop: { display: 'flex', justifyContent: 'space-between', marginBottom: 4 },
  metricLabel: { fontSize: '0.8rem', color: '#4b5563', fontWeight: 500 },
  metricScore: { fontSize: '0.8rem', color: '#111', fontWeight: 700 },
  metricBarBg: { height: 6, background: '#eef2f7', borderRadius: 4, overflow: 'hidden' },
  metricBarFill: { height: '100%', background: '#1a73e8', borderRadius: 4 },
  metricSub: { fontSize: '0.72rem', color: '#9ca3af', marginTop: 3, display: 'block' },
  feedback: { margin: '14px 0 0', paddingLeft: 18, color: '#4b5563', fontSize: '0.82rem', lineHeight: 1.7 },
  savedNote: { marginTop: 12, fontSize: '0.8rem', color: '#15803d', fontWeight: 500 },
}
