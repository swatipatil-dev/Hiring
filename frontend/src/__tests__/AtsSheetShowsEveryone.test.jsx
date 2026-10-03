/**
 * The ATS sheet shows everyone connected to the job, not only scored applicants.
 *
 * An applicant whose scoring failed has no ATS result, and a Talent Pool resume matched
 * to the job is not a candidate until moved. Both used to be invisible from the sheet,
 * so HR saw "2 candidates" with no way to find the rest.
 */
// @vitest-environment jsdom
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

const get = vi.fn()
const post = vi.fn(() => Promise.resolve({ data: {} }))

vi.mock('../context/AuthContext', () => ({ useAuth: () => ({ user: { role: 'hr' } }) }))
vi.mock('../api/client', () => ({
  default: {
    get: (...a) => get(...a),
    post: (...a) => post(...a),
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

const candidates = [
  { candidate_id: 'c1', name: 'Katarukonda Ashok', email: 'k@example.com', ats_score: 60, scored: true, ats_level: 'Below Average', current_stage: 'ats_screening' },
  { candidate_id: 'c2', name: 'Yashwant Patil', email: 'y@example.com', ats_score: 58, scored: true, ats_level: 'Poor', current_stage: 'ats_screening' },
  { candidate_id: 'c3', name: 'Unscored Applicant', email: 'u@example.com', ats_score: null, scored: false, ats_level: 'Not scored', current_stage: 'applied' },
]

const poolMatches = [
  { entry_id: 'p1', full_name: 'Pool Person', email: 'pool@example.com', phone: null, experience_years: 3, match_score: 72, is_best_match: true, parse_status: 'ok' },
]

beforeEach(() => {
  vi.clearAllMocks()
  localStorage.clear()
  get.mockImplementation((url) => {
    if (url === '/hr/jobs') return Promise.resolve({ data: [job] })
    if (url === '/hr/jobs/job-1/candidates') {
      return Promise.resolve({ data: { candidates, total_candidates: candidates.length, rules_versions_present: [] } })
    }
    if (url === '/hr/jobs/job-1/pool-matches') return Promise.resolve({ data: poolMatches })
    return Promise.resolve({ data: [] })
  })
})

const openSheet = async () => {
  render(<MemoryRouter initialEntries={['/hr/ats']}><HRDashboard /></MemoryRouter>)
  fireEvent.click(await screen.findByRole('button', { name: /Open ATS Scores/i }))
  await screen.findByText(/3 candidates/)
}

describe('ATS sheet shows everyone connected to the job', () => {
  it('lists an unscored applicant in its own group, with a way to score them', async () => {
    await openSheet()
    expect(screen.getByText(/1 not scored/)).toBeInTheDocument()
    expect(screen.getByText(/Not scored yet \(1\)/)).toBeInTheDocument()
    expect(screen.getByText('Unscored Applicant')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Score now' }))
    await waitFor(() => expect(post).toHaveBeenCalledWith('/hr/jobs/job-1/candidates/c3/rescore'))
  })

  it('counts only ranked candidates for Top N', async () => {
    await openSheet()
    expect(screen.getByText(/of 2 ranked/)).toBeInTheDocument()
  })

  it('shows Talent Pool matches that are not in the ATS yet, with Move to ATS', async () => {
    await openSheet()
    expect(await screen.findByText(/In Talent Pool — not yet in ATS \(1\)/)).toBeInTheDocument()
    expect(screen.getByText('Pool Person')).toBeInTheDocument()
    expect(screen.getByText(/1 more in Talent Pool/)).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Move to ATS' }))
    // The dialog opens on this job, preselected.
    expect(await screen.findByRole('option', { name: /AI Engineer/ })).toBeInTheDocument()
  })
})
