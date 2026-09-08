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
    // "2Tests Ready" is what the raw text content reads; the label has to spell it out.
    const card = await screen.findByRole('button', { name: /^Tests Ready: 2\./ })
    expect(card).toBeTruthy()
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
