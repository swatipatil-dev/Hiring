/**
 * The Users tab filters by role through a row of cards.
 *
 * These replaced a text tab bar. The card is a button carrying `aria-pressed`, because
 * the tinted background and heavier border that show which one is chosen are decoration
 * a screen reader never sees — and because it filters the table below it, so it has to
 * work from the keyboard.
 *
 * The counts come from /admin/users/counts, not from the loaded page: `users` holds one
 * filtered page of 25, so counting it would make every card report the wrong number the
 * moment anybody clicked one.
 */
// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

vi.mock('../api/client', () => ({
  default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), patch: vi.fn(), delete: vi.fn() },
}))

// Header pulls in AuthContext, which this test has no business standing up — the same
// shortcut HRComposeMail.test.jsx takes.
vi.mock('../components/Header', () => ({ default: () => <div /> }))

vi.mock('recharts', async () => {
  const actual = await vi.importActual('recharts')
  const { cloneElement } = await vi.importActual('react')
  return { ...actual, ResponsiveContainer: ({ children }) => cloneElement(children, { width: 600, height: 240 }) }
})

import api from '../api/client'
import AdminDashboard from '../pages/admin/AdminDashboard'

const COUNTS = { all: 120, admin: 3, hr: 8, interviewer: 5, candidate: 104 }

const USER = {
  id: 'u1', name: 'Anjali Rao', email: 'anjali@example.com',
  role: 'hr', is_active: true,
}

/** The params of the most recent /admin/users request. */
const lastUsersParams = () => {
  const calls = api.get.mock.calls.filter(
    c => String(c[0]) === '/admin/users')
  return calls.length ? (calls[calls.length - 1][1]?.params ?? {}) : null
}

beforeEach(() => {
  vi.clearAllMocks()
  api.get.mockImplementation((url) => {
    const u = String(url)
    if (u === '/admin/users/counts') return Promise.resolve({ data: COUNTS })
    if (u === '/admin/users') return Promise.resolve({ data: { users: [USER], total: 1 } })
    return Promise.resolve({ data: [] })
  })
})

const renderAdmin = async () => {
  const user = (await import('@testing-library/user-event')).default
  render(<MemoryRouter initialEntries={['/admin/users']}><AdminDashboard /></MemoryRouter>)
  return user
}

const card = (label) => screen.getByRole('button', { name: new RegExp(`^${label}, `) })

describe('Admin Users role cards', () => {
  it('shows a card per role with its own count', async () => {
    await renderAdmin()
    const group = await screen.findByRole('group', { name: /Filter users by role/i })

    for (const [label, n] of [['All Users', 120], ['Admin', 3], ['HR', 8],
                              ['Interviewer', 5], ['Candidate', 104]]) {
      // The visible numeral and the announced one have to agree.
      const btn = within(group).getByRole('button', { name: `${label}, ${n} users` })
      expect(btn).toHaveTextContent(String(n))
    }
  })

  it('opens on All, with that card marked selected', async () => {
    await renderAdmin()
    await screen.findByRole('group', { name: /Filter users by role/i })
    expect(card('All Users')).toHaveAttribute('aria-pressed', 'true')
    expect(card('Candidate')).toHaveAttribute('aria-pressed', 'false')
  })

  it('filters the list to the role you click', async () => {
    const user = await renderAdmin()
    await screen.findByRole('group', { name: /Filter users by role/i })

    await user.click(card('HR'))
    await waitFor(() => expect(lastUsersParams()).toMatchObject({ role: 'hr' }))
    expect(card('HR')).toHaveAttribute('aria-pressed', 'true')

    await user.click(card('Candidate'))
    await waitFor(() => expect(lastUsersParams()).toMatchObject({ role: 'candidate' }))
  })

  it('sends no role at all for All, rather than the string "all"', async () => {
    // 'all' is a UI-only key; the API has no such role and would return nothing.
    const user = await renderAdmin()
    await screen.findByRole('group', { name: /Filter users by role/i })

    await user.click(card('HR'))
    await waitFor(() => expect(lastUsersParams()).toMatchObject({ role: 'hr' }))

    await user.click(card('All Users'))
    await waitFor(() => expect(lastUsersParams().role).toBeUndefined())
  })

  it('counts describe the whole company, not the page in view', async () => {
    // The page holds one user; the cards must still report 120.
    const user = await renderAdmin()
    await screen.findByRole('group', { name: /Filter users by role/i })
    await user.click(card('HR'))
    await waitFor(() => expect(lastUsersParams()).toMatchObject({ role: 'hr' }))
    expect(card('All Users')).toHaveTextContent('120')
  })

  it('shows a dash rather than zero while the counts are still loading', async () => {
    // A flash of 0 reads as an empty system instead of as "not loaded yet".
    api.get.mockImplementation((url) => {
      const u = String(url)
      if (u === '/admin/users/counts') return new Promise(() => {})   // never resolves
      if (u === '/admin/users') return Promise.resolve({ data: { users: [], total: 0 } })
      return Promise.resolve({ data: [] })
    })
    await renderAdmin()
    const group = await screen.findByRole('group', { name: /Filter users by role/i })
    expect(within(group).getByRole('button', { name: 'All Users, loading count' }))
      .toHaveTextContent('—')
  })
})
