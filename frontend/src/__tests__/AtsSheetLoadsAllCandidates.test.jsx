/**
 * The ATS sheet must hold every applicant for a job, not only the first page.
 *
 * The endpoint caps a page at 100 and the sheet used to stop there, so an applicant
 * ranked below 100 never appeared — yet a Top-N / Top-% send auto-rejects every unsent
 * applicant in the job server-side, including the ones HR was never shown.
 */
// @vitest-environment jsdom
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

const get = vi.fn()

vi.mock('../context/AuthContext', () => ({ useAuth: () => ({ user: { role: 'hr' } }) }))
vi.mock('../api/client', () => ({
  default: {
    get: (...a) => get(...a),
    post: vi.fn(() => Promise.resolve({ data: {} })),
    put: vi.fn(() => Promise.resolve({ data: {} })),
    delete: vi.fn(() => Promise.resolve({ data: {} })),
  },
}))

vi.mock('../components/Header', () => ({ default: () => <div /> }))
vi.mock('../components/StageTimeline', () => ({ default: () => <div /> }))
vi.mock('../components/ProctoringReport', () => ({ default: () => <div /> }))
vi.mock('../components/ExamDrafts', () => ({ default: () => <div /> }))
vi.mock('../pages/hr/PipelineNotifications', () => ({ default: () => <div /> }))
vi.mock('../pages/hr/MailFromPicker', () => ({ default: () => <div /> }))
vi.mock('../pages/hr/ResumeMatcher', () => ({ default: () => <div /> }))
vi.mock('../pages/shared/CreateTestWizard', () => ({ default: () => <div /> }))

import HRDashboard from '../pages/hr/HRDashboard'

const job = {
  id: 'job-1', title: 'AI Engineer', department: 'Technology & AI', department_id: 'd1',
  location: 'Hyderabad', employment_type: 'full-time', required_skills: [], status: 'active',
  created_at: '2026-09-08T10:00:00Z', description: '', requirements: '',
}

const TOTAL = 150
const cand = (i) => ({
  candidate_id: `c${i}`, name: `Applicant ${i}`, email: `a${i}@example.com`,
  ats_score: 90 - i * 0.1, current_stage: 'ats_screening',
})

beforeEach(() => {
  vi.clearAllMocks()
  localStorage.clear()
  get.mockImplementation((url, config) => {
    if (url === '/hr/jobs') return Promise.resolve({ data: [job] })
    if (url === '/hr/jobs/job-1/candidates') {
      const page = config?.params?.page || 1
      const per = config?.params?.per_page || 100
      const rows = Array.from({ length: TOTAL }, (_, i) => cand(i)).slice((page - 1) * per, page * per)
      return Promise.resolve({ data: { candidates: rows, total_candidates: TOTAL, rules_versions_present: [] } })
    }
    return Promise.resolve({ data: [] })
  })
})

describe('ATS sheet', () => {
  it('loads every applicant across pages, not just the first 100', async () => {
    render(<MemoryRouter initialEntries={['/hr/ats']}><HRDashboard /></MemoryRouter>)

    fireEvent.click(await screen.findByRole('button', { name: /Open ATS Scores/i }))

    expect(await screen.findByText(/150 candidates/)).toBeInTheDocument()
    const pages = get.mock.calls
      .filter(([url]) => url === '/hr/jobs/job-1/candidates')
      .map(([, config]) => config.params.page)
    expect(pages).toEqual([1, 2])
  })
})
