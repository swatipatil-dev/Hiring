/**
 * Tests for AuthContext — login, logout, state persistence, verifyOtp.
 * API calls are mocked so no backend is needed.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, act } from '@testing-library/react'
import { BrowserRouter } from 'react-router-dom'
import { AuthProvider, useAuth } from '../context/AuthContext'

// ── helpers ─────────────────────────────────────────────────────────────────

// Minimal valid JWT payload encoded as base64 (not cryptographically signed — for tests only)
function makeToken(payload) {
  const header = btoa(JSON.stringify({ alg: 'HS256', typ: 'JWT' }))
  const body   = btoa(JSON.stringify({ exp: Math.floor(Date.now() / 1000) + 3600, ...payload }))
  return `${header}.${body}.fakesig`
}

const MOCK_TOKEN = makeToken({ sub: 'user-123', role: 'candidate' })
const MOCK_USER  = { id: 'user-123', email: 'test@example.com', role: 'candidate', first_name: 'Test', last_name: 'User' }

// Mock the api client so no real HTTP goes out
vi.mock('../api/client', () => ({
  default: {
    post: vi.fn(),
    get:  vi.fn(),
    interceptors: {
      request:  { use: vi.fn() },
      response: { use: vi.fn() },
    },
  },
}))

import api from '../api/client'

function Wrapper({ children }) {
  return (
    <BrowserRouter>
      <AuthProvider>{children}</AuthProvider>
    </BrowserRouter>
  )
}

// Consumer component that exposes context values for assertion
function AuthConsumer({ onRender }) {
  const ctx = useAuth()
  onRender(ctx)
  return null
}

// ── tests ────────────────────────────────────────────────────────────────────

describe('AuthContext', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.clearAllMocks()
  })

  it('starts unauthenticated when localStorage is empty', () => {
    let ctx
    render(
      <Wrapper>
        <AuthConsumer onRender={(c) => { ctx = c }} />
      </Wrapper>
    )
    expect(ctx.isAuthenticated).toBe(false)
    expect(ctx.user).toBeNull()
    expect(ctx.token).toBeNull()
  })

  it('login() sets token and user in state and localStorage', async () => {
    api.post.mockResolvedValueOnce({
      data: { access_token: MOCK_TOKEN, user: MOCK_USER },
    })

    let ctx
    render(
      <Wrapper>
        <AuthConsumer onRender={(c) => { ctx = c }} />
      </Wrapper>
    )

    await act(async () => {
      await ctx.login('test@example.com', 'password123')
    })

    expect(ctx.isAuthenticated).toBe(true)
    expect(ctx.token).toBe(MOCK_TOKEN)
    expect(ctx.user?.email).toBe('test@example.com')
    expect(localStorage.getItem('hireflow_token')).toBe(MOCK_TOKEN)
  })

  it('logout() clears token and user', async () => {
    api.post.mockResolvedValueOnce({
      data: { access_token: MOCK_TOKEN, user: MOCK_USER },
    })

    let ctx
    render(
      <Wrapper>
        <AuthConsumer onRender={(c) => { ctx = c }} />
      </Wrapper>
    )

    await act(async () => {
      await ctx.login('test@example.com', 'password123')
    })
    expect(ctx.isAuthenticated).toBe(true)

    act(() => {
      ctx.logout()
    })

    expect(ctx.isAuthenticated).toBe(false)
    expect(ctx.token).toBeNull()
    expect(ctx.user).toBeNull()
    expect(localStorage.getItem('hireflow_token')).toBeNull()
  })

  it('rehydrates user from localStorage on mount', () => {
    localStorage.setItem('hireflow_token', MOCK_TOKEN)
    localStorage.setItem('hireflow_user', JSON.stringify(MOCK_USER))

    let ctx
    render(
      <Wrapper>
        <AuthConsumer onRender={(c) => { ctx = c }} />
      </Wrapper>
    )

    expect(ctx.isAuthenticated).toBe(true)
    expect(ctx.user?.email).toBe(MOCK_USER.email)
  })

  it('verifyOtp() sets token and user after successful OTP', async () => {
    api.post.mockResolvedValueOnce({
      data: { access_token: MOCK_TOKEN, user: MOCK_USER },
    })

    let ctx
    render(
      <Wrapper>
        <AuthConsumer onRender={(c) => { ctx = c }} />
      </Wrapper>
    )

    await act(async () => {
      await ctx.verifyOtp('test@example.com', '123456')
    })

    expect(ctx.isAuthenticated).toBe(true)
    expect(ctx.token).toBe(MOCK_TOKEN)
    expect(api.post).toHaveBeenCalledWith('/auth/verify-otp', {
      email: 'test@example.com',
      otp: '123456',
    })
  })

  it('register() calls the correct API endpoint', async () => {
    api.post.mockResolvedValueOnce({
      data: { message: 'Account created.', email: 'new@example.com', requires_verification: true },
    })

    let ctx
    render(
      <Wrapper>
        <AuthConsumer onRender={(c) => { ctx = c }} />
      </Wrapper>
    )

    let result
    await act(async () => {
      result = await ctx.register({
        email: 'new@example.com',
        password: 'Pass@1234',
        role: 'candidate',
        first_name: 'New',
        last_name: 'User',
      })
    })

    expect(api.post).toHaveBeenCalledWith('/auth/register', expect.objectContaining({
      email: 'new@example.com',
    }))
    expect(result.requires_verification).toBe(true)
    // Registration alone should not set auth state
    expect(ctx.isAuthenticated).toBe(false)
  })

  it('useAuth throws when used outside AuthProvider', () => {
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {})
    expect(() => {
      render(
        <BrowserRouter>
          <AuthConsumer onRender={() => {}} />
        </BrowserRouter>
      )
    }).toThrow('useAuth must be inside AuthProvider')
    spy.mockRestore()
  })
})
