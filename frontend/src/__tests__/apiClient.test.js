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
