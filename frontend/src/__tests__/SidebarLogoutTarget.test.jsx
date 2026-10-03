/**
 * Where signing out takes you.
 *
 * Platform staff (Innomax Skills) go to the platform login; a client company's people go
 * back to their company's job board. The platform used to be recognised by a company id
 * hardcoded from one database — ids are random per database, so in production every
 * platform user failed the check and was logged out to /innomax/jobs, the platform's own
 * careers page, as though they worked for a client.
 */
// @vitest-environment jsdom
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { MemoryRouter, Routes, Route, useLocation } from 'react-router-dom'

import { isPlatformUser } from '../utils/tenant'

let mockUser = null
const logout = vi.fn()
vi.mock('../context/AuthContext', () => ({ useAuth: () => ({ user: mockUser, logout }) }))

import Sidebar from '../components/Sidebar'

function Where() {
  return <div data-testid="where">{useLocation().pathname}</div>
}

const signOut = () => {
  render(
    <MemoryRouter initialEntries={['/hr']}>
      <Routes>
        <Route path="/hr" element={<Sidebar />} />
        <Route path="*" element={<Where />} />
      </Routes>
    </MemoryRouter>,
  )
  fireEvent.click(screen.getByRole('button', { name: /log ?out|sign ?out/i }))
  return screen.getByTestId('where').textContent
}

const base = { role: 'hr', first_name: 'P', email: 'p@x.com' }

beforeEach(() => {
  vi.clearAllMocks()
  localStorage.clear()
  // jsdom has no matchMedia; the sidebar reads it for its mobile drawer.
  window.matchMedia = window.matchMedia || (() => ({
    matches: false, addEventListener: () => {}, removeEventListener: () => {},
  }))
})

describe('sign out target', () => {
  it('sends platform staff to the login page, whatever their company id is', () => {
    // A production id: random, nothing like the dev id that used to be hardcoded.
    mockUser = { ...base, company_id: 'f3c1d2e4-0000-4000-8000-123456789abc', company_slug: 'innomax', is_platform: true }
    expect(signOut()).toBe('/login')
    expect(logout).toHaveBeenCalled()
  })

  it("sends a client company's staff back to their company's job board", () => {
    mockUser = { ...base, company_id: 'c-1', company_slug: 'client-co', is_platform: false }
    expect(signOut()).toBe('/client-co/jobs')
  })

  it('treats a session from before the is_platform claim by its slug', () => {
    mockUser = { ...base, company_id: 'f3c1d2e4-0000-4000-8000-123456789abc', company_slug: 'innomax' }
    expect(signOut()).toBe('/login')
  })
})

describe('isPlatformUser', () => {
  it('trusts the backend claim over the slug', () => {
    expect(isPlatformUser({ company_id: 'x', company_slug: 'innomax', is_platform: false })).toBe(false)
    expect(isPlatformUser({ company_id: 'x', company_slug: 'renamed', is_platform: true })).toBe(true)
  })

  it('treats a company-less account as platform-level, and nobody as nothing', () => {
    expect(isPlatformUser({ company_id: null })).toBe(true)
    expect(isPlatformUser(null)).toBe(false)
  })
})
