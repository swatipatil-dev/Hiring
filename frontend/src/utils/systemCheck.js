/**
 * Pre-test system check: browser, connectivity, and — for proctored tests — the
 * proctoring WebSocket, camera and microphone.
 *
 * Every check yields a row: { id, label, status, detail }.
 *   pass     works
 *   warn     works, but the candidate should know (old browser, slow link, dark room)
 *   fail     the test cannot run properly — blocks Start until fixed
 *   pending  waiting on the candidate (grant the camera, say something)
 *   running  in progress
 *
 * The point is to surface problems while the candidate can still fix them. Several of
 * these used to fail silently mid-test: a blocked WebSocket left proctoring analysing
 * zero frames and the session looking clean; a dark room made face enrollment reject
 * frame after frame; a system-muted mic raised MIC_MUTED for the whole test.
 */

export const STATUS = {
  PASS: 'pass', WARN: 'warn', FAIL: 'fail', PENDING: 'pending', RUNNING: 'running',
}

export const isSatisfied = (row) => row.status === STATUS.PASS || row.status === STATUS.WARN

// ── Browser ─────────────────────────────────────────────────────────────────

// Oldest majors we are comfortable with. Older ones are allowed (warn, not fail):
// what actually matters — camera, mic, WebSocket — is checked directly below.
const MIN_MAJOR = { Chrome: 100, Edge: 100, Firefox: 100, Safari: 15, Opera: 90 }

export function detectBrowser(ua) {
  let m
  if ((m = ua.match(/Edg(?:A|iOS)?\/(\d+)/))) return { name: 'Edge', major: +m[1] }
  if ((m = ua.match(/OPR\/(\d+)/))) return { name: 'Opera', major: +m[1] }
  if ((m = ua.match(/(?:Firefox|FxiOS)\/(\d+)/))) return { name: 'Firefox', major: +m[1] }
  if ((m = ua.match(/(?:CriOS|Chrome)\/(\d+)/))) return { name: 'Chrome', major: +m[1] }
  if (/Safari\//.test(ua) && (m = ua.match(/Version\/(\d+)/))) return { name: 'Safari', major: +m[1] }
  return { name: null, major: null }
}

export const isMobileDevice = (ua) => /Mobi|Android|iPhone|iPad|iPod/i.test(ua)

/** What the running browser offers. Separate from checkBrowser so tests can pass their own. */
export function browserEnv() {
  return {
    ua: navigator.userAgent || '',
    // Standard in every current browser; absent only in very old ones (and jsdom).
    secure: window.isSecureContext !== false,
    getUserMedia: !!navigator.mediaDevices?.getUserMedia,
    webSocket: typeof window.WebSocket === 'function',
  }
}

export function checkBrowser({ needsMedia, needsFullscreen, fullscreenSupported, env = browserEnv() }) {
  const rows = []
  const { name, major } = detectBrowser(env.ua)
  const summary = name ? `${name} ${major}` : 'Unknown browser'

  if (!name) {
    rows.push({ id: 'browser', label: 'Browser', status: STATUS.WARN, summary,
      detail: 'Unrecognised browser. The latest Chrome, Edge, Firefox or Safari is recommended.' })
  } else if (major < MIN_MAJOR[name]) {
    rows.push({ id: 'browser', label: 'Browser', status: STATUS.WARN, summary,
      detail: `${name} ${major} is out of date. Update your browser before starting if you can.` })
  } else {
    rows.push({ id: 'browser', label: 'Browser', status: STATUS.PASS, summary, detail: summary })
  }

  rows.push(isMobileDevice(env.ua)
    ? { id: 'device', label: 'Device', status: STATUS.WARN,
        detail: 'Phone or tablet detected. A laptop or desktop computer is recommended.' }
    : { id: 'device', label: 'Device', status: STATUS.PASS, detail: 'Laptop or desktop' })

  if (needsMedia) {
    const missing = []
    if (!env.secure) missing.push('a secure (https) connection — open the test link exactly as it was sent to you')
    if (!env.getUserMedia) missing.push('camera and microphone access')
    if (!env.webSocket) missing.push('live streaming (WebSockets)')
    rows.push(missing.length
      ? { id: 'media-support', label: 'Proctoring support', status: STATUS.FAIL,
          detail: `This browser is missing ${missing.join(', ')}. Use the latest Chrome, Edge, Firefox or Safari.` }
      : { id: 'media-support', label: 'Proctoring support', status: STATUS.PASS,
          detail: 'Camera, microphone and streaming are available' })
  }

  if (needsFullscreen) {
    rows.push(fullscreenSupported
      ? { id: 'fullscreen', label: 'Fullscreen', status: STATUS.PASS, detail: 'Supported' }
      : { id: 'fullscreen', label: 'Fullscreen', status: STATUS.WARN,
          detail: "This browser can't go fullscreen. You can still take the test; your recruiter will see that fullscreen wasn't available." })
  }
  return rows
}

// ── Connectivity ────────────────────────────────────────────────────────────

const median = (xs) => [...xs].sort((a, b) => a - b)[Math.floor(xs.length / 2)]

/** Round-trip time to the API. `get` performs one lightweight request. */
export async function checkServer(get, { samples = 3, onLine = navigator.onLine, now = () => performance.now() } = {}) {
  const row = { id: 'server', label: 'Internet connection' }
  if (onLine === false) {
    return { ...row, status: STATUS.FAIL, detail: 'You appear to be offline. Reconnect to the internet and re-run the check.' }
  }
  const times = []
  try {
    for (let i = 0; i < samples; i++) {
      const t0 = now()
      await get()
      times.push(now() - t0)
    }
  } catch {
    return { ...row, status: STATUS.FAIL,
      detail: "Couldn't reach the HireFlow server. Check your internet connection and re-run the check." }
  }
  const ms = Math.round(median(times))
  const summary = `${ms} ms`
  if (ms <= 600) return { ...row, status: STATUS.PASS, summary, detail: `Server responds in ${ms} ms` }
  return { ...row, status: STATUS.WARN, summary,
    detail: `Slow connection: the server takes ${ms} ms to respond. Pages and answers may save slowly; move closer to your router or switch networks if you can.` }
}

export const proctorPingUrl = (loc = window.location) =>
  `${loc.protocol === 'https:' ? 'wss' : 'ws'}://${loc.host}/api/v1/proctor/ws/ping`

const fmtRate = (kbps) => (kbps >= 1000 ? `${(kbps / 1000).toFixed(1)} Mbps` : `${Math.round(kbps)} kbps`)

const SOCKET_BLOCKED =
  "Couldn't open the proctoring connection. A VPN, proxy, firewall or restricted network " +
  '(common on office and school Wi-Fi) may be blocking it. Try another network, or turn off the VPN, and re-run the check.'

/**
 * Open the proctoring WebSocket probe, time a round trip, then time the upload of a
 * frame-sized payload. Travels the same proxy route as the real proctoring sockets.
 */
export function checkProctorSocket({
  url = proctorPingUrl(), payloadBytes = 64 * 1024, timeoutMs = 10000,
  WS = globalThis.WebSocket, now = () => performance.now(),
} = {}) {
  const row = { id: 'proctor-socket', label: 'Proctoring connection' }
  return new Promise((resolve) => {
    let ws = null
    let done = false
    let phase = 'ping'
    let t0 = 0
    let rtt = null

    const finish = (result) => {
      if (done) return
      done = true
      clearTimeout(timer)
      try { ws?.close() } catch { /* already closed */ }
      resolve({ ...row, ...result })
    }
    const timer = setTimeout(() => finish({
      status: STATUS.FAIL,
      detail: rtt == null
        ? SOCKET_BLOCKED
        : 'The proctoring connection opened but stalled while sending data. Your upload speed may be too low; switch networks and re-run the check.',
    }), timeoutMs)

    try {
      ws = new WS(url)
    } catch {
      finish({ status: STATUS.FAIL, detail: SOCKET_BLOCKED })
      return
    }
    ws.onopen = () => { t0 = now(); ws.send('ping') }
    ws.onmessage = () => {
      const t = now()
      if (phase === 'ping') {
        rtt = t - t0
        phase = 'upload'
        t0 = now()
        ws.send('x'.repeat(payloadBytes))
        return
      }
      // Upload time ≈ total minus one round trip of latency.
      const secs = Math.max((t - t0 - rtt) / 1000, 0.001)
      const kbps = (payloadBytes * 8) / 1000 / secs
      const latency = `${Math.round(rtt)} ms latency`
      const summary = `${Math.round(rtt)} ms, ${fmtRate(kbps)} up`
      if (kbps < 500) {
        finish({ status: STATUS.WARN, summary,
          detail: `Slow upload (about ${fmtRate(kbps)}, ${latency}). Proctoring will work but check your camera less often. Move closer to your router or switch networks if you can.` })
      } else if (rtt > 800) {
        finish({ status: STATUS.WARN, summary,
          detail: `High latency (${latency}, upload about ${fmtRate(kbps)}). Proctoring will work but may lag.` })
      } else {
        finish({ status: STATUS.PASS, summary,
          detail: `Connected with ${latency} and an upload speed of about ${fmtRate(kbps)}` })
      }
    }
    ws.onerror = () => finish({ status: STATUS.FAIL, detail: SOCKET_BLOCKED })
    ws.onclose = () => finish({ status: STATUS.FAIL, detail: SOCKET_BLOCKED })
  })
}

// ── Camera & microphone ─────────────────────────────────────────────────────

// Mean luma below this makes face enrollment reject the frame (face_analyzer.py
// _ENROLL_MIN_BRIGHTNESS is 45 on the face crop; the whole frame runs darker).
const DARK_FRAME_LUMA = 40

/** Mean brightness (0-255) of the current video frame, or null if no frame has arrived. */
export function sampleVideoBrightness(video) {
  if (!video || !video.videoWidth || !video.videoHeight) return null
  const canvas = document.createElement('canvas')
  canvas.width = 64
  canvas.height = 48
  const ctx = canvas.getContext('2d', { willReadFrequently: true })
  if (!ctx) return null
  ctx.drawImage(video, 0, 0, canvas.width, canvas.height)
  const { data } = ctx.getImageData(0, 0, canvas.width, canvas.height)
  let sum = 0
  for (let i = 0; i < data.length; i += 4) sum += 0.299 * data[i] + 0.587 * data[i + 1] + 0.114 * data[i + 2]
  return sum / (data.length / 4)
}

export function classifyCamera({ permission, measured, width, height, brightness }) {
  const row = { id: 'camera', label: 'Camera' }
  if (permission === 'idle') return { ...row, status: STATUS.PENDING, summary: 'Not turned on', detail: null }
  if (permission === 'checking') return { ...row, status: STATUS.RUNNING, detail: 'Waiting for camera access…' }
  if (permission === 'denied') return { ...row, status: STATUS.FAIL,
    detail: 'Camera access was blocked. Allow the camera using the icon in your address bar, then re-check.' }
  if (permission === 'ended') return { ...row, status: STATUS.FAIL,
    detail: 'The camera disconnected. Reconnect it and re-check.' }
  if (permission !== 'granted') return { ...row, status: STATUS.FAIL,
    detail: 'No camera found. Connect a camera and re-check.' }
  if (!measured) return { ...row, status: STATUS.RUNNING, detail: 'Checking the picture…' }
  if (brightness == null) return { ...row, status: STATUS.FAIL,
    detail: 'The camera is connected but not sending a picture. Close other apps using it (Zoom, Teams, Meet) and re-check.' }
  if (brightness < DARK_FRAME_LUMA) return { ...row, status: STATUS.WARN,
    detail: 'The picture is very dark. Turn on a light facing you so your face can be verified.' }
  const size = width && height ? `${width}×${height}` : null
  return { ...row, status: STATUS.PASS, summary: size || 'Clear picture',
    detail: size ? `Clear picture at ${size}` : 'Clear picture' }
}

// Level on the page's 0-100 meter that counts as "heard something".
export const MIC_HEARD_LEVEL = 15

export function classifyMicrophone({ permission, muted, heard }) {
  const row = { id: 'microphone', label: 'Microphone' }
  if (permission === 'idle') return { ...row, status: STATUS.PENDING, summary: 'Not turned on', detail: null }
  if (permission === 'checking') return { ...row, status: STATUS.RUNNING, detail: 'Waiting for microphone access…' }
  if (permission === 'denied') return { ...row, status: STATUS.FAIL,
    detail: 'Microphone access was blocked. Allow it using the icon in your address bar, then re-check.' }
  if (permission === 'ended') return { ...row, status: STATUS.FAIL,
    detail: 'The microphone disconnected. Reconnect it and re-check.' }
  if (permission !== 'granted') return { ...row, status: STATUS.FAIL,
    detail: 'No microphone found. Connect one and re-check.' }
  if (muted) return { ...row, status: STATUS.FAIL,
    detail: 'The microphone is muted by your system or another app. Unmute it and re-check.' }
  if (!heard) return { ...row, status: STATUS.PENDING, summary: 'Listening', detail: 'Say a few words so we know we can hear you.' }
  return { ...row, status: STATUS.PASS, summary: 'Hearing you', detail: 'We can hear you' }
}

// ── The signal path ─────────────────────────────────────────────────────────

// How bad a status is when several checks share one step. Anything unresolved outranks
// a warning: a step is not "fine, with a note" while part of it is still running.
const SEVERITY = { fail: 4, running: 3, pending: 2, warn: 1, pass: 0 }

export const worstStatus = (rows) =>
  rows.reduce((worst, r) => (SEVERITY[r.status] > SEVERITY[worst] ? r.status : worst), STATUS.PASS)

const BROWSER_ROWS = ['browser', 'device', 'media-support', 'fullscreen']

const STATUS_WORD = {
  [STATUS.RUNNING]: 'Checking', [STATUS.PENDING]: 'Waiting for you',
  [STATUS.WARN]: 'Works, see note', [STATUS.FAIL]: 'Not working',
}

/**
 * Fold the individual checks into the steps a proctored test's video and audio pass
 * through, in order: camera, microphone, this browser, the internet, the proctoring
 * server. A candidate cares whether "my browser" is fine, not which of four browser
 * rules tripped, so those collapse into one step that lists only what needs attention.
 *
 * Each step: { id, label, status, reading, notes: [{ status, text }] }.
 * `reading` is the short value shown on the line; `notes` explain anything not passing.
 */
export function buildSignalPath(rows) {
  const byId = Object.fromEntries(rows.map(r => [r.id, r]))
  const single = (id, label) => {
    const r = byId[id]
    if (!r) return null
    const ok = r.status === STATUS.PASS
    return {
      id, label, status: r.status,
      reading: r.summary || (ok ? r.detail : STATUS_WORD[r.status]),
      notes: ok || !r.detail ? [] : [{ status: r.status, text: r.detail }],
    }
  }
  const browserRows = BROWSER_ROWS.map(id => byId[id]).filter(Boolean)
  const browser = browserRows.length && {
    id: 'browser', label: 'This browser', status: worstStatus(browserRows),
    reading: byId.browser?.summary || STATUS_WORD[worstStatus(browserRows)],
    notes: browserRows.filter(r => r.status !== STATUS.PASS).map(r => ({ status: r.status, text: r.detail })),
  }
  return [
    single('camera', 'Camera'),
    single('microphone', 'Microphone'),
    browser,
    single('server', 'Internet connection'),
    single('proctor-socket', 'Proctoring connection'),
  ].filter(Boolean)
}
