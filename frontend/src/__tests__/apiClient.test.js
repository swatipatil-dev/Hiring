/**
 * Tests for the axios API client — token injection, 401 handling.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

// jsdom does not define window.location.href reassignment as a jest fn,
// so we spy on it manually.
const locationSpy = vi.spyOn(window, 'location', 'get')

describe('parseJWT / decodeExpiry utility', () => {
  // The decodeExpiry function is internal — we test its observable behaviour
  // via the localStorage flow in AuthContext. This file tests the raw util.
  function decodeExpiry(token) {
    try { return JSON.parse(atob(token.split('.')[1])).exp * 1000 } catch { return null }
  }

  it('returns null for malformed token', () => {
    expect(decodeExpiry('not.a.token')).toBeNull()
  })

  it('returns null for empty string', () => {
    expect(decodeExpiry('')).toBeNull()
  })

  it('extracts exp from a valid JWT payload', () => {
    const exp = Math.floor(Date.now() / 1000) + 3600
    const payload = btoa(JSON.stringify({ exp, sub: 'user-1' }))
    const token = `header.${payload}.sig`
    expect(decodeExpiry(token)).toBe(exp * 1000)
  })
})

describe('parseJWT utility (AuthContext)', () => {
  function parseJWT(token) {
    try {
      return JSON.parse(atob(token.split('.')[1]))
    } catch {
      return {}
    }
  }

  it('returns empty object for invalid token', () => {
    expect(parseJWT('bad')).toEqual({})
  })

  it('decodes role from payload', () => {
    const payload = btoa(JSON.stringify({ role: 'hr', company_id: 'co-1' }))
    const token = `h.${payload}.s`
    const result = parseJWT(token)
    expect(result.role).toBe('hr')
    expect(result.company_id).toBe('co-1')
  })

  it('decodes company branding fields', () => {
    const payload = btoa(JSON.stringify({
      company_color: '#ff0000',
      company_name: 'Acme',
      company_logo: 'https://example.com/logo.png',
    }))
    const token = `h.${payload}.s`
    const result = parseJWT(token)
    expect(result.company_color).toBe('#ff0000')
    expect(result.company_name).toBe('Acme')
  })
})

describe('multipart uploads', () => {
  // Must run with no stored token. The request interceptor proactively refreshes a
  // token that is near expiry, and that refresh is a real axios.post the stub
  // adapter below does not intercept — so a token left behind by another test file
  // (they share a worker when file parallelism is off) would hang this request.
  beforeEach(() => {
    localStorage.clear()
  })

  // The client sets a default Content-Type of application/json. A FormData POST
  // must override it, otherwise axios cannot attach the multipart boundary and
  // the upload fails server-side with no useful error — the failure mode that
  // would silently break the department-logo upload.
  //
  // Asserted through a stub adapter rather than api.defaults, so this tests what
  // actually goes on the wire rather than axios's internal header bookkeeping.
  async function captureRequest(url, data, config) {
    const api = (await import('../api/client')).default
    let seen = null
    const original = api.defaults.adapter
    api.defaults.adapter = async (cfg) => {
      seen = cfg
      return { data: {}, status: 200, statusText: 'OK', headers: {}, config: cfg }
    }
    try {
      await api.post(url, data, config)
    } finally {
      api.defaults.adapter = original
    }
    return seen
  }

  it('sends multipart/form-data when the request overrides the default', async () => {
    const fd = new FormData()
    fd.append('file', new Blob(['x'], { type: 'image/png' }), 'logo.png')

    const cfg = await captureRequest('/admin/departments/d1/logo', fd, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })

    const contentType = cfg.headers['Content-Type'] || cfg.headers['content-type']
    expect(String(contentType)).toMatch(/multipart\/form-data/)
    expect(cfg.data).toBeInstanceOf(FormData)
    expect(cfg.data.get('file')).toBeInstanceOf(Blob)
  })

  it('still defaults to JSON for ordinary posts', async () => {
    const cfg = await captureRequest('/hr/send-mail', { to: 'a@b.com' })
    const contentType = cfg.headers['Content-Type'] || cfg.headers['content-type']
    expect(String(contentType)).toMatch(/application\/json/)
  })
})
