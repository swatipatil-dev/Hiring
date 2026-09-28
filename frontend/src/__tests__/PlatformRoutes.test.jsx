/**
 * The platform's own front door must survive client company portals.
 *
 * `career.innomaxskills.com/` and `/login` belong to Innomax. They used to redirect a
 * visitor carrying a company slug to that company's job board — which meant one visit to
 * any client's careers page permanently took those two URLs away from that browser, staff
 * included. Now that every company has `/<slug>`, the redirect is gone.
 *
 * These tests set a stored slug first, because that is exactly the browser that used to
 * get captured. They also pin the other half of the guarantee: a company slug must not
 * shadow a platform route, which React Router gives us by ranking static segments above
 * dynamic ones (`RESERVED_SLUGS` in the backend is what stops a company being named
 * `login` in the first place).
 */
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

import { SLUG_KEY } from '../utils/tenant'

// Nobody is signed in — these are the logged-out entry points.
vi.mock('../context/AuthContext', () => ({
  useAuth: () => ({ isAuthenticated: false, user: null }),
}))

// Stub every page App imports. The assertions are about which route wins, not about what
// any page renders, and the real modules drag in Monaco and the whole dashboard tree.
const stub = (label) => ({ default: () => <div>{label}</div> })
vi.mock('../pages/LandingPage', () => stub('LANDING'))
vi.mock('../pages/Login', () => stub('PLATFORM_LOGIN'))
vi.mock('../pages/Register', () => stub('REGISTER'))
vi.mock('../pages/ForgotPassword', () => stub('FORGOT'))
vi.mock('../pages/public/JobBoard', () => stub('JOB_BOARD'))
vi.mock('../pages/public/CompanyPortal', () => stub('COMPANY_PORTAL'))
vi.mock('../pages/public/CompanyLogin', () => stub('COMPANY_LOGIN'))
vi.mock('../pages/admin/AdminLogin', () => stub('ADMIN_LOGIN'))
vi.mock('../pages/admin/AdminDashboard', () => stub('ADMIN_DASH'))
vi.mock('../pages/superadmin/SuperAdminDashboard', () => stub('SUPERADMIN'))
vi.mock('../pages/superadmin/DepartmentPage', () => stub('DEPARTMENTS'))
vi.mock('../pages/superadmin/SupportTicketsPage', () => stub('SUPPORT'))
vi.mock('../pages/hr/HRDashboard', () => stub('HR'))
vi.mock('../pages/interviewer/InterviewerDashboard', () => stub('INTERVIEWER'))
vi.mock('../pages/candidate/CandidateDashboard', () => stub('CANDIDATE'))
vi.mock('../pages/candidate/OnlineTest', () => stub('ONLINE_TEST'))
vi.mock('../pages/candidate/TestInstructions', () => stub('TEST_INSTRUCTIONS'))
vi.mock('../pages/shared/MyProfile', () => stub('PROFILE'))
vi.mock('../components/Sidebar', () => stub('SIDEBAR'))

const { default: App } = await import('../App')

const renderAt = (path) =>
  render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>
  )

describe('the platform front door survives a stored company slug', () => {
  beforeEach(() => {
    localStorage.clear()
    // The browser has visited a client portal. This is the state that used to capture it.
    localStorage.setItem(SLUG_KEY, 'acme-corp')
  })

  it('serves the Innomax landing page at /', () => {
    renderAt('/')
    expect(screen.getByText('LANDING')).toBeInTheDocument()
    expect(screen.queryByText('JOB_BOARD')).not.toBeInTheDocument()
  })

  it('serves the platform login at /login', () => {
    renderAt('/login')
    expect(screen.getByText('PLATFORM_LOGIN')).toBeInTheDocument()
    expect(screen.queryByText('JOB_BOARD')).not.toBeInTheDocument()
  })

  it('still serves the company portal at /<slug>', () => {
    renderAt('/acme-corp')
    expect(screen.getByText('COMPANY_LOGIN')).toBeInTheDocument()
  })

  it('still serves the company job board at /<slug>/jobs', () => {
    renderAt('/acme-corp/jobs')
    expect(screen.getByText('JOB_BOARD')).toBeInTheDocument()
  })
})

describe('a company slug cannot shadow a platform route', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  // Static segments outrank the dynamic /:companySlug. If this ever regresses, a company
  // named after a route would take that page away from everyone.
  it.each([
    ['/login', 'PLATFORM_LOGIN'],
    ['/register', 'REGISTER'],
    ['/forgot-password', 'FORGOT'],
    ['/jobs', 'JOB_BOARD'],
    ['/portal', 'COMPANY_PORTAL'],
    ['/app', 'COMPANY_LOGIN'],
    ['/admin/login', 'ADMIN_LOGIN'],
  ])('%s reaches its own page, not a company lookup', (path, marker) => {
    renderAt(path)
    expect(screen.getByText(marker)).toBeInTheDocument()
  })
})
