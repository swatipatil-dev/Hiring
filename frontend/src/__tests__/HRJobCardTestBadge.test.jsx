/**
 * An HR job card must say whether a test already exists for the job.
 *
 * The card's only badge is the JOB's own lifecycle — "Open" / "Closed" / draft — which says
 * nothing about its assessment. A job holding an unfinished test draft therefore looked
 * identical to one with no test at all, and the button read "Create Test" while the click
 * actually resumed a half-finished one.
 */
// @vitest-environment jsdom
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

const get = vi.fn()

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

const baseJob = {
  department: 'Technology & AI', department_id: 'd1', location: 'Hyderabad',
  employment_type: 'full-time', required_skills: [], status: 'active',
  created_at: '2026-09-08T10:00:00Z', description: '', requirements: '',
}

const withJobs = (jobs) => {
  get.mockImplementation((url) => {
    if (url === '/hr/jobs') return Promise.resolve({ data: jobs })
    return Promise.resolve({ data: [] })
  })
}

beforeEach(() => {
  vi.clearAllMocks()
  localStorage.clear()
  get.mockResolvedValue({ data: [] })
})

const renderJobs = () => render(
  <MemoryRouter initialEntries={['/hr/jobs']}><HRDashboard /></MemoryRouter>
)

// The Job Postings toolbar has its own "Create Test" button, so every card assertion has to
// be scoped to the card itself.
const cardFor = async (title) => (await screen.findByText(title)).closest('.card')

describe('HR job card — test state', () => {
  it('flags a job that is holding an unfinished draft', async () => {
    withJobs([{ ...baseJob, id: 'job-1', title: 'Ai Developer', has_test: true, test_id: 42, test_status: 'DRAFT' }])
    renderJobs()

    const card = within(await cardFor('Ai Developer'))
    expect(card.getByText('Test: draft')).toBeInTheDocument()
    // Same action, honest label — "Create Test" promised a new test and resumed an old one.
    expect(card.getByRole('button', { name: /Resume Draft/i })).toBeInTheDocument()
    expect(card.queryByRole('button', { name: /Create Test/i })).not.toBeInTheDocument()
  })

  it('flags a job whose test is finished', async () => {
    withJobs([{ ...baseJob, id: 'job-2', title: 'QA Engineer', has_test: true, test_id: 43, test_status: 'READY' }])
    renderJobs()

    const card = within(await cardFor('QA Engineer'))
    expect(card.getByText('Test: ready')).toBeInTheDocument()
    expect(card.getByRole('button', { name: /Create Test/i })).toBeInTheDocument()
  })

  it('says nothing about a test when there is none', async () => {
    withJobs([{ ...baseJob, id: 'job-3', title: 'Data Engineer', has_test: false, test_id: null, test_status: null }])
    renderJobs()

    const card = within(await cardFor('Data Engineer'))
    expect(card.queryByText(/^Test:/)).not.toBeInTheDocument()
    expect(card.getByRole('button', { name: /Create Test/i })).toBeInTheDocument()
  })

  it('still shows the job\'s own status independently of its test', async () => {
    withJobs([{ ...baseJob, id: 'job-4', title: 'Ai Developer', has_test: true, test_id: 42, test_status: 'DRAFT' }])
    renderJobs()

    // Both badges coexist: "Open" is the posting, "Test: draft" is the assessment. Conflating
    // them is what made the original report read as "it's showing OPEN not DRAFT".
    const card = within(await cardFor('Ai Developer'))
    expect(card.getByText('Test: draft')).toBeInTheDocument()
    expect(card.getByText('Open')).toBeInTheDocument()
  })
})
