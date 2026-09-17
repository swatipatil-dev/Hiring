/**
 * The profile page's account actions and its HR-only sections.
 *
 * Two of these guard behaviour that fails silently in the browser:
 *
 *  - The change-password form must not send `confirm_password`. It is a
 *    client-side check only, and the endpoint rejects unknown fields, so
 *    leaking it turns every password change into a 422.
 *  - A wrong current password comes back as 400, and the page must stay put.
 *    api/client.js treats any 401 as a dead session and hard-redirects to
 *    /login, so if that endpoint ever regressed to 401 the user would be
 *    signed out for a typo. Asserting the URL never moves is the UI half of
 *    that guarantee.
 */
// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'

vi.mock('../api/client', () => ({
  default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), patch: vi.fn(), delete: vi.fn() },
}))
vi.mock('../components/Header', () => ({ default: () => <div /> }))

import api from '../api/client'
import MyProfile from '../pages/shared/MyProfile'
import { AuthProvider } from '../context/AuthContext'

const ME = {
  id: 'u1', email: 'hr@test.com', role: 'hr', first_name: 'Ada', last_name: 'Lovelace',
  phone: '123', gender: null, date_of_birth: null, is_active: true, email_verified: true,
  created_at: '2026-01-01T00:00:00', updated_at: '2026-01-01T00:00:00',
  company_id: 'c1', company_name: 'Test Co', profile: null,
}

const PERMS = {
  role: 'hr', known_role: true, total: 1, granted_count: 1,
  group_count: 1, granted_group_count: 1,
  groups: [{ group: 'Jobs', total: 1, granted_count: 1, permissions: [
    { slug: 'jobs.create', label: 'Create jobs', granted: true, endpoints: [] },
  ] }],
}

const ACTIVITY = {
  at_a_glance: { jobs: 4, active_jobs: 2, interviews: 9, offers: 1 },
  last_30_days: { days: 30, since: '2026-08-18T00:00:00', jobs_created: 1, candidates: 5, interviews_scheduled: 2, offers_sent: 0 },
  recent_activity: [],
}

/** localStorage as a just-logged-in user, including the JWT branding keys. */
function seedSession(role = 'hr') {
  localStorage.setItem('hireflow_token', 'tok')
  localStorage.setItem('hireflow_user', JSON.stringify({
    id: 'u1', email: 'hr@test.com', role, first_name: 'Ada', last_name: 'Lovelace',
    company_logo: '/logo.png', company_color: '#123456', company_slug: 'testco',
  }))
}

function mountProfile() {
  return render(
    <MemoryRouter><AuthProvider><MyProfile /></AuthProvider></MemoryRouter>
  )
}

function routeGet(overrides = {}) {
  const table = { '/auth/me': ME, '/auth/my-permissions': PERMS, '/hr/my-activity': ACTIVITY, ...overrides }
  api.get.mockImplementation((url) => {
    const hit = table[url]
    if (hit instanceof Error) return Promise.reject(hit)
    if (hit === undefined) return Promise.reject(new Error(`unexpected GET ${url}`))
    return Promise.resolve({ data: hit })
  })
}

beforeEach(() => {
  vi.clearAllMocks()
  localStorage.clear()
})

describe('change password', () => {
  it('does not send confirm_password', async () => {
    seedSession()
    routeGet()
    api.post.mockResolvedValue({ data: { message: 'ok' } })
    const user = userEvent.setup()
    mountProfile()

    await user.click(await screen.findByRole('button', { name: /change password/i }))
    await user.type(screen.getByLabelText(/current password/i), 'OldPass@123')
    await user.type(screen.getByLabelText(/^new password/i), 'NewPass@123')
    await user.type(screen.getByLabelText(/confirm new password/i), 'NewPass@123')
    await user.click(screen.getByRole('button', { name: /^change password$/i }))

    await waitFor(() => expect(api.post).toHaveBeenCalled())
    const [url, body] = api.post.mock.calls[0]
    expect(url).toBe('/auth/change-password')
    expect(body).toEqual({ current_password: 'OldPass@123', new_password: 'NewPass@123' })
    expect(body).not.toHaveProperty('confirm_password')
  })

  it('shows a 400 in place and never redirects to /login', async () => {
    seedSession()
    routeGet()
    api.post.mockRejectedValue({
      response: { status: 400, data: { detail: 'Current password is incorrect.' } },
    })
    const before = window.location.href
    const user = userEvent.setup()
    mountProfile()

    await user.click(await screen.findByRole('button', { name: /change password/i }))
    await user.type(screen.getByLabelText(/current password/i), 'WrongPass1')
    await user.type(screen.getByLabelText(/^new password/i), 'NewPass@123')
    await user.type(screen.getByLabelText(/confirm new password/i), 'NewPass@123')
    await user.click(screen.getByRole('button', { name: /^change password$/i }))

    expect(await screen.findByText(/current password is incorrect/i)).toBeInTheDocument()
    expect(window.location.href).toBe(before)
    // Form still open so the user can retry without retyping the new password.
    expect(screen.getByLabelText(/^new password/i)).toHaveValue('NewPass@123')
  })

  it('catches a mismatch before hitting the network', async () => {
    seedSession()
    routeGet()
    const user = userEvent.setup()
    mountProfile()

    await user.click(await screen.findByRole('button', { name: /change password/i }))
    await user.type(screen.getByLabelText(/current password/i), 'OldPass@123')
    await user.type(screen.getByLabelText(/^new password/i), 'NewPass@123')
    await user.type(screen.getByLabelText(/confirm new password/i), 'Different@1')
    await user.click(screen.getByRole('button', { name: /^change password$/i }))

    expect(await screen.findByText(/do not match/i)).toBeInTheDocument()
    expect(api.post).not.toHaveBeenCalled()
  })
})

describe('edit account details', () => {
  it('sends only the editable fields and preserves cached branding', async () => {
    seedSession()
    routeGet()
    api.put.mockResolvedValue({ data: { ...ME, first_name: 'Grace' } })
    const user = userEvent.setup()
    mountProfile()

    await user.click(await screen.findByRole('button', { name: /^edit$/i }))
    const first = screen.getByLabelText(/first name/i)
    await user.clear(first)
    await user.type(first, 'Grace')
    await user.click(screen.getByRole('button', { name: /^save$/i }))

    await waitFor(() => expect(api.put).toHaveBeenCalled())
    const [url, body] = api.put.mock.calls[0]
    expect(url).toBe('/auth/me')
    expect(Object.keys(body).sort()).toEqual(['first_name', 'last_name', 'phone'])
    expect(body).not.toHaveProperty('role')
    expect(body).not.toHaveProperty('email')

    // setUserFromServer merges, so the JWT branding keys must survive.
    await waitFor(() => {
      const cached = JSON.parse(localStorage.getItem('hireflow_user'))
      expect(cached.first_name).toBe('Grace')
      expect(cached.company_logo).toBe('/logo.png')
      expect(cached.company_color).toBe('#123456')
    })
  })
})

describe('HR-only sections', () => {
  it('renders for an HR and requests the activity endpoint', async () => {
    seedSession('hr')
    routeGet()
    mountProfile()

    expect(await screen.findByText(/your job postings/i)).toBeInTheDocument()
    expect(await screen.findByText(/recent activity/i)).toBeInTheDocument()
    expect(api.get).toHaveBeenCalledWith('/hr/my-activity')
  })

  it('never requests the activity endpoint for a non-HR role', async () => {
    seedSession('candidate')
    routeGet({ '/auth/me': { ...ME, role: 'candidate' }, '/auth/my-permissions': { ...PERMS, role: 'candidate' } })
    mountProfile()

    await screen.findByText(/account details/i)
    expect(api.get).not.toHaveBeenCalledWith('/hr/my-activity')
    expect(screen.queryByText(/your job postings/i)).not.toBeInTheDocument()
  })

  it('hides the sections rather than showing an error when the fetch fails', async () => {
    seedSession('hr')
    routeGet({ '/hr/my-activity': new Error('boom') })
    mountProfile()

    await screen.findByText(/account details/i)
    await waitFor(() => {
      expect(screen.queryByText(/your job postings/i)).not.toBeInTheDocument()
    })
    // A half-finished deploy 404s here; that must not put an error card on the page.
    expect(screen.queryByText(/couldn.t load your activity/i)).not.toBeInTheDocument()
  })
})
