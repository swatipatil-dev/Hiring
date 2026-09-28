/**
 * Pre-test system check rules (utils/systemCheck.js).
 *
 * The line that matters is fail vs warn: a fail blocks Start, a warn does not. Fails
 * are reserved for things that make the test unworkable (no camera API, blocked
 * proctoring socket, no picture, muted mic); anything the candidate can live with — an
 * old browser, a phone, a slow link, a dark room, no fullscreen — only warns, so nobody
 * is locked out of an application by a check that was being cautious.
 */
import { describe, it, expect, vi, afterEach } from 'vitest'
import {
  STATUS, detectBrowser, checkBrowser, checkServer, checkProctorSocket,
  classifyCamera, classifyMicrophone, buildSignalPath, worstStatus,
} from '../utils/systemCheck'

const UA = {
  chrome: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36',
  edge: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36 Edg/128.0.0.0',
  firefox: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:130.0) Gecko/20100101 Firefox/130.0',
  safari: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15',
  oldChrome: 'Mozilla/5.0 (Windows NT 10.0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/80.0.0.0 Safari/537.36',
  iphone: 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1',
}
const env = (extra = {}) => ({ ua: UA.chrome, secure: true, getUserMedia: true, webSocket: true, ...extra })
const byId = (rows, id) => rows.find(r => r.id === id)

describe('detectBrowser', () => {
  it.each([
    ['chrome', 'Chrome', 128], ['edge', 'Edge', 128], ['firefox', 'Firefox', 130], ['safari', 'Safari', 17],
  ])('%s', (k, name, major) => expect(detectBrowser(UA[k])).toEqual({ name, major }))

  it('does not mistake Edge for Chrome', () => expect(detectBrowser(UA.edge).name).toBe('Edge'))
})

describe('checkBrowser', () => {
  it('passes a current desktop browser with everything available', () => {
    const rows = checkBrowser({ needsMedia: true, needsFullscreen: true, fullscreenSupported: true, env: env() })
    expect(rows.every(r => r.status === STATUS.PASS)).toBe(true)
  })

  it('only warns for an old browser, a phone, or no fullscreen', () => {
    const rows = checkBrowser({
      needsMedia: true, needsFullscreen: true, fullscreenSupported: false,
      env: env({ ua: UA.iphone }),
    })
    expect(byId(rows, 'device').status).toBe(STATUS.WARN)
    expect(byId(rows, 'fullscreen').status).toBe(STATUS.WARN)
    expect(rows.some(r => r.status === STATUS.FAIL)).toBe(false)
    expect(byId(checkBrowser({ needsMedia: false, env: env({ ua: UA.oldChrome }) }), 'browser').status).toBe(STATUS.WARN)
  })

  it('fails a proctored test when the camera API or a secure connection is missing', () => {
    const rows = checkBrowser({ needsMedia: true, env: env({ secure: false, getUserMedia: false }) })
    const row = byId(rows, 'media-support')
    expect(row.status).toBe(STATUS.FAIL)
    expect(row.detail).toMatch(/https/)
    expect(row.detail).toMatch(/camera and microphone/)
  })

  it('does not require media support for an unproctored test', () => {
    const rows = checkBrowser({ needsMedia: false, env: env({ getUserMedia: false }) })
    expect(byId(rows, 'media-support')).toBeUndefined()
  })
})

describe('checkServer', () => {
  const clock = (...ticks) => { let i = 0; return () => ticks[i++] }

  it('passes a responsive server and reports the median round trip', async () => {
    const r = await checkServer(async () => {}, { onLine: true, now: clock(0, 100, 100, 150, 150, 1000) })
    expect(r.status).toBe(STATUS.PASS)
    expect(r.detail).toMatch(/100 ms/)
  })

  it('warns, not fails, on a slow server', async () => {
    const r = await checkServer(async () => {}, { samples: 1, onLine: true, now: clock(0, 2000) })
    expect(r.status).toBe(STATUS.WARN)
  })

  it('fails when offline or unreachable', async () => {
    expect((await checkServer(async () => {}, { onLine: false })).status).toBe(STATUS.FAIL)
    const r = await checkServer(async () => { throw new Error('net') }, { onLine: true })
    expect(r.status).toBe(STATUS.FAIL)
  })
})

describe('checkProctorSocket', () => {
  afterEach(() => vi.useRealTimers())

  /** A WebSocket stand-in that answers each send with a pong after `delays[i]` ms. */
  const fakeSocket = ({ open = true, delays = [0, 0] } = {}) => {
    const sent = []
    class FakeWS {
      constructor() {
        setTimeout(() => (open ? this.onopen?.() : this.onerror?.()), 0)
      }
      send(data) {
        const i = sent.push(data) - 1
        setTimeout(() => this.onmessage?.({ data: '{"event":"pong"}' }), delays[i] ?? 0)
      }
      close() {}
    }
    return { FakeWS, sent }
  }

  it('passes a fast link and uploads a frame-sized payload', async () => {
    vi.useFakeTimers()
    const { FakeWS, sent } = fakeSocket({ delays: [20, 60] })
    const p = checkProctorSocket({ url: 'ws://x', WS: FakeWS, now: () => Date.now() })
    await vi.runAllTimersAsync()
    const r = await p
    expect(r.status).toBe(STATUS.PASS)
    expect(sent[1].length).toBe(64 * 1024)
  })

  it('warns on a slow upload', async () => {
    vi.useFakeTimers()
    const { FakeWS } = fakeSocket({ delays: [20, 3000] })   // 64 KB in ~3s ≈ 175 kbps
    const p = checkProctorSocket({ url: 'ws://x', WS: FakeWS, now: () => Date.now() })
    await vi.runAllTimersAsync()
    expect((await p).status).toBe(STATUS.WARN)
  })

  it('fails when the socket cannot open (blocked by a proxy or firewall)', async () => {
    vi.useFakeTimers()
    const { FakeWS } = fakeSocket({ open: false })
    const p = checkProctorSocket({ url: 'ws://x', WS: FakeWS, now: () => Date.now() })
    await vi.runAllTimersAsync()
    const r = await p
    expect(r.status).toBe(STATUS.FAIL)
    expect(r.detail).toMatch(/VPN|firewall/)
  })

  it('fails when the socket never answers', async () => {
    vi.useFakeTimers()
    const { FakeWS } = fakeSocket({ delays: [60000] })
    const p = checkProctorSocket({ url: 'ws://x', WS: FakeWS, timeoutMs: 5000, now: () => Date.now() })
    await vi.advanceTimersByTimeAsync(6000)
    expect((await p).status).toBe(STATUS.FAIL)
  })
})

describe('camera and microphone', () => {
  it('camera: waits, then passes a picture, warns when dark, fails with no picture', () => {
    expect(classifyCamera({ permission: 'idle' }).status).toBe(STATUS.PENDING)
    expect(classifyCamera({ permission: 'granted', measured: false }).status).toBe(STATUS.RUNNING)
    expect(classifyCamera({ permission: 'granted', measured: true, brightness: 120, width: 320, height: 240 }).status).toBe(STATUS.PASS)
    expect(classifyCamera({ permission: 'granted', measured: true, brightness: 12 }).status).toBe(STATUS.WARN)
    expect(classifyCamera({ permission: 'granted', measured: true, brightness: null }).status).toBe(STATUS.FAIL)
    expect(classifyCamera({ permission: 'denied' }).status).toBe(STATUS.FAIL)
  })

  it('microphone: must actually hear the candidate, and fails when muted at the source', () => {
    expect(classifyMicrophone({ permission: 'granted', heard: false }).status).toBe(STATUS.PENDING)
    expect(classifyMicrophone({ permission: 'granted', heard: true }).status).toBe(STATUS.PASS)
    expect(classifyMicrophone({ permission: 'granted', muted: true, heard: true }).status).toBe(STATUS.FAIL)
    expect(classifyMicrophone({ permission: 'denied' }).status).toBe(STATUS.FAIL)
  })
})

describe('buildSignalPath', () => {
  const browserRows = checkBrowser({
    needsMedia: true, needsFullscreen: true, fullscreenSupported: false, env: env({ ua: UA.iphone }),
  })
  const rows = [
    ...browserRows,
    { id: 'server', label: 'Internet connection', status: STATUS.PASS, summary: '40 ms', detail: 'Server responds in 40 ms' },
    { id: 'proctor-socket', label: 'Proctoring connection', status: STATUS.FAIL, detail: 'Blocked by a firewall.' },
    classifyCamera({ permission: 'granted', measured: true, brightness: 120, width: 640, height: 480 }),
    classifyMicrophone({ permission: 'granted', heard: false }),
  ]
  const steps = buildSignalPath(rows)

  it('orders steps along the path the video and audio travel', () => {
    expect(steps.map(s => s.id)).toEqual(['camera', 'microphone', 'browser', 'server', 'proctor-socket'])
  })

  it('folds the browser rules into one step that lists only what needs attention', () => {
    const b = steps.find(s => s.id === 'browser')
    expect(b.status).toBe(STATUS.WARN)            // phone + no fullscreen: warnings, not failures
    expect(b.reading).toBe('Safari 17')
    expect(b.notes).toHaveLength(2)
  })

  it('shows the short reading on a passing step and the fix on a failing one', () => {
    expect(steps.find(s => s.id === 'server')).toMatchObject({ reading: '40 ms', notes: [] })
    const socket = steps.find(s => s.id === 'proctor-socket')
    expect(socket.reading).toBe('Not working')
    expect(socket.notes[0].text).toMatch(/firewall/)
  })

  it('treats an unfinished check as worse than a warning', () => {
    expect(worstStatus([{ status: STATUS.WARN }, { status: STATUS.PENDING }])).toBe(STATUS.PENDING)
    expect(worstStatus([{ status: STATUS.PENDING }, { status: STATUS.FAIL }])).toBe(STATUS.FAIL)
  })

  it('leaves out camera and microphone for an unproctored test', () => {
    const short = buildSignalPath(checkBrowser({ needsMedia: false, env: env() }))
    expect(short.map(s => s.id)).toEqual(['browser'])
  })
})
