/**
 * Stat cards have to be operable, not just clickable.
 *
 * Every dashboard drew its cards as divs that lifted on hover and did nothing — the
 * affordance the app's own CSS calls out at index.css:408 ("if it moves, it must
 * navigate"). Admin was fixed and the other dashboards were not, so these lock in the
 * three properties that made Admin's version correct, for the two that were converted:
 *
 *   1. the card is a button, so it is reachable and activates from the keyboard;
 *   2. its accessible name carries the value, not just the label — the numeral renders
 *      first, so the raw text content reads "5Tests Ready";
 *   3. clicking it actually moves the view.
 */
// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

vi.mock('../api/client', () => ({
  default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), patch: vi.fn(), delete: vi.fn() },
}))
vi.mock('../components/Header', () => ({ default: () => <div /> }))
vi.mock('recharts', async () => {
  const actual = await vi.importActual('recharts')
  const { cloneElement } = await vi.importActual('react')
  return { ...actual, ResponsiveContainer: ({ children }) => cloneElement(children, { width: 600, height: 240 }) }
})

import api from '../api/client'
import InterviewerDashboard from '../pages/interviewer/InterviewerDashboard'
import SuperAdminDashboard from '../pages/superadmin/SuperAdminDashboard'
import HRDashboard from '../pages/hr/HRDashboard'

const userEvent = async () => (await import('@testing-library/user-event')).default

// Eight meetings, so a card reporting "5" would be reporting the slice, not the total.
const MEETINGS = Array.from({ length: 8 }, (_, i) => ({
  id: `m${i}`, scheduled_datetime: '2030-01-01T10:00:00Z', candidate_name: `C${i}`,
}))

const INTERVIEWS = [
  { id: 'i1', status: 'READY', title: 'T1' },
  { id: 'i2', status: 'READY', title: 'T2' },
  { id: 'i3', status: 'DRAFT', title: 'T3' },
]

const COMPANIES = [
  { id: 'c1', name: 'Acme', slug: 'acme', is_active: true, total_users: 10, total_jobs: 4, total_departments: 2, total_candidates: 30 },
  { id: 'c2', name: 'Globex', slug: 'globex', is_active: false, total_users: 5, total_jobs: 1, total_departments: 1, total_candidates: 7 },
]

beforeEach(() => {
  vi.clearAllMocks()
  window.matchMedia = window.matchMedia || (() => ({ matches: false, addListener() {}, removeListener() {} }))
  Element.prototype.scrollIntoView = vi.fn()
})

describe('Interviewer dashboard stat cards', () => {
  beforeEach(() => {
    api.get.mockImplementation((url) => {
      const u = String(url)
      if (u === '/interviewer/my-meetings') return Promise.resolve({ data: MEETINGS })
      if (u === '/interviewer/interviews/counts')
        return Promise.resolve({ data: { total: 62, draft: 1, ready: 60, scheduled: 1, completed: 0, by_status: { DRAFT: 1, READY: 60, SCHEDULED: 1 } } })
      if (u === '/interviewer/interviews') return Promise.resolve({ data: INTERVIEWS })
      if (u === '/interviewer/my-assignments') return Promise.resolve({ data: [] })
      if (u === '/interviewer/candidate-requests') return Promise.resolve({ data: [] })
      return Promise.resolve({ data: {} })
    })
  })

  const renderIv = () =>
    render(<MemoryRouter initialEntries={['/interviewer']}><InterviewerDashboard /></MemoryRouter>)

  it('renders each stat as a button rather than a div', async () => {
    renderIv()
    const card = await screen.findByRole('button', { name: /^Tests Ready:/ })
    expect(card.tagName).toBe('BUTTON')
  })

  it('puts the value in the accessible name, not just the label', async () => {
    renderIv()
    // "60Tests Ready" is what the raw text content reads; the label has to spell it out.
    const card = await screen.findByRole('button', { name: /^Tests Ready: 60\./ })
    expect(card).toBeTruthy()
  })

  it('reports every test, not the page of 50 the list endpoint returns', async () => {
    renderIv()
    // The mocked list holds 3 rows; the count endpoint says 60 are READY. The card has
    // to believe the count — deriving it from the list is what capped it at 50.
    expect(await screen.findByRole('button', { name: /^Tests Ready: 60\./ })).toBeTruthy()
  })

  it('counts every meeting assigned, not the five it lists below', async () => {
    renderIv()
    // The card used to read upcomingMeetings.length — myMeetings.slice(0, 5) — so it
    // reported 5 whether there were 5 or 50.
    expect(await screen.findByRole('button', { name: /^Upcoming Meetings: 8\./ })).toBeTruthy()
  })

  it('moves the view when a card is activated from the keyboard', async () => {
    const user = await userEvent()
    renderIv()
    const card = await screen.findByRole('button', { name: /^Candidate Requests:/ })
    card.focus()
    expect(document.activeElement).toBe(card)
    await user.keyboard('{Enter}')
    await waitFor(() => expect(screen.getByText(/My Requests/i)).toBeTruthy())
  })
})

describe('Super-admin stat cards', () => {
  beforeEach(() => {
    api.get.mockImplementation((url) => {
      const u = String(url)
      if (u === '/superadmin/companies') return Promise.resolve({ data: COMPANIES })
      return Promise.resolve({ data: [] })
    })
  })

  const renderSa = () =>
    render(<MemoryRouter initialEntries={['/superadmin']}><SuperAdminDashboard /></MemoryRouter>)

  it('renders the KPI tiles as buttons', async () => {
    renderSa()
    const card = await screen.findByRole('button', { name: /^Total Clients:/ })
    expect(card.tagName).toBe('BUTTON')
  })

  it('filters the client list to the active ones, and says so', async () => {
    const user = await userEvent()
    renderSa()
    expect(await screen.findByText(/Client Companies \(2\)/)).toBeTruthy()

    await user.click(screen.getByRole('button', { name: /^Active:/ }))

    await waitFor(() => expect(screen.getByText(/Active Clients \(1\)/)).toBeTruthy())
    // The filter is a toggle, so its state has to be exposed, not just tinted.
    expect(screen.getByRole('button', { name: /^Active:/ })).toHaveAttribute('aria-pressed', 'true')
  })

  it('marks the roll-up tiles as plain actions, not toggles', async () => {
    renderSa()
    // aria-pressed on something that does not hold a state announces a lie.
    const rollup = await screen.findByRole('button', { name: /^Total Users:/ })
    expect(rollup.hasAttribute('aria-pressed')).toBe(false)
  })

  it('lets the filter be cleared again', async () => {
    const user = await userEvent()
    renderSa()
    await user.click(await screen.findByRole('button', { name: /^Active:/ }))
    await waitFor(() => expect(screen.getByText(/Active Clients \(1\)/)).toBeTruthy())
    await user.click(screen.getByRole('button', { name: /Clear filter/i }))
    await waitFor(() => expect(screen.getByText(/Client Companies \(2\)/)).toBeTruthy())
  })
})

describe('Counts come from the server, not from the fetched page', () => {
  const HR_COUNTS = { jobs: 80, active_jobs: 61, interviews: 55, offers: 12 }

  const renderHr = () =>
    render(<MemoryRouter initialEntries={['/hr']}><HRDashboard /></MemoryRouter>)

  it('HR cards report the company total, not the 50 rows the list returns', async () => {
    api.get.mockImplementation((url) => {
      const u = String(url)
      if (u === '/hr/dashboard/counts') return Promise.resolve({ data: HR_COUNTS })
      // One job in the page. Counting this array is what produced the capped number.
      if (u === '/hr/jobs') return Promise.resolve({ data: [{ id: 'j1', title: 'A', status: 'active' }] })
      return Promise.resolve({ data: [] })
    })
    renderHr()
    expect(await screen.findByRole('button', { name: /^Job Postings: 80\./ })).toBeTruthy()
    expect(screen.getByRole('button', { name: /^Active Jobs: 61\./ })).toBeTruthy()
    expect(screen.getByRole('button', { name: /^Interviews: 55\./ })).toBeTruthy()
  })

  it('shows a dash rather than zero while the counts are still loading', async () => {
    api.get.mockImplementation((url) => {
      const u = String(url)
      // Never resolves — the state a slow network leaves the card in.
      if (u === '/hr/dashboard/counts') return new Promise(() => {})
      return Promise.resolve({ data: [] })
    })
    renderHr()
    // A flash of 0 reads as "you have no jobs", not as "not loaded yet".
    const card = await screen.findByRole('button', { name: /^Job Postings, loading count/ })
    expect(card.textContent).toContain('—')
    expect(card.textContent).not.toContain('0')
  })

  // A failed load used to render the same em dash as a slow one, so a half-finished
  // deploy — new frontend, old API, 404 on the endpoint — looked exactly like an empty
  // pipeline. These pin the three states apart.
  const failWith = (err) => {
    api.get.mockImplementation((url) => {
      if (String(url) === '/hr/dashboard/counts') return Promise.reject(err)
      return Promise.resolve({ data: [] })
    })
  }

  it('reports a failed counts request as unavailable, not as loading or zero', async () => {
    failWith({ response: { status: 500 } })
    renderHr()

    const card = await screen.findByRole('button', { name: /^Offers, count unavailable/ })
    // Never a confident zero, and no longer the same dash a slow load shows.
    expect(card.textContent).not.toContain('0')
    expect(card.textContent).toContain('!')
  })

  it('names a 404 as an out-of-date backend, which is what a half-deploy looks like', async () => {
    failWith({ response: { status: 404 } })
    renderHr()

    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toContain('Totals unavailable')
    expect(alert.textContent).toMatch(/backend may be out of date/i)
  })

  it('says it could not reach the server when there is no response at all', async () => {
    failWith(new Error('Network Error'))
    renderHr()

    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toMatch(/could not reach the server/i)
  })

  it('retry re-requests and fills the cards in', async () => {
    const user = await userEvent()
    failWith({ response: { status: 404 } })
    renderHr()

    await screen.findByRole('alert')

    // The deploy finishes; the same button must recover without a page reload.
    api.get.mockImplementation((url) => {
      if (String(url) === '/hr/dashboard/counts') return Promise.resolve({ data: HR_COUNTS })
      return Promise.resolve({ data: [] })
    })
    await user.click(screen.getByRole('button', { name: /^Retry$/ }))

    expect(await screen.findByRole('button', { name: /^Job Postings: 80\./ })).toBeTruthy()
    expect(screen.queryByRole('alert')).toBeNull()
  })
})
