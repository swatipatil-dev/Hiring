/**
 * HR must be able to reach its own unfinished test drafts.
 *
 * The Exam Drafts view existed only in InterviewerDashboard, so an HR user who abandoned the
 * Create Test wizard had no route back to the draft — and no way to free the job the draft was
 * silently holding out of the Step-1 dropdown. /hr/jobs/{id}/tests cannot fill the gap: it
 * excludes DRAFTs on purpose, because they are not assignable.
 *
 * These tests render the real HRDashboard so the tab wiring is covered end to end — the
 * TAB_MAP key, the sidebar path segment, and the hand-off into the wizard. A missing TAB_MAP
 * key does not throw; it silently renders the overview, which is exactly the kind of failure
 * a test has to catch.
 */
// @vitest-environment jsdom
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'

const get = vi.fn()
const del = vi.fn()

// HRDashboard reads the signed-in user (to tell owner / co-HR apart); in the app it
// always sits inside AuthProvider, which these isolated renders don't mount.
vi.mock('../context/AuthContext', () => ({ useAuth: () => ({ user: { role: 'hr' } }) }))
vi.mock('../api/client', () => ({
  default: {
    get: (...a) => get(...a),
    post: vi.fn(() => Promise.resolve({ data: {} })),
    put: vi.fn(() => Promise.resolve({ data: {} })),
    delete: (...a) => del(...a),
  },
}))

// Heavy children are irrelevant here and drag in charts / monaco / websockets.
vi.mock('../components/Header', () => ({ default: () => <div /> }))
vi.mock('../components/StageTimeline', () => ({ default: () => <div /> }))
vi.mock('../components/ProctoringReport', () => ({ default: () => <div /> }))
vi.mock('../pages/hr/PipelineNotifications', () => ({ default: () => <div /> }))
vi.mock('../pages/hr/MailFromPicker', () => ({ default: () => <div /> }))
vi.mock('../pages/hr/ResumeMatcher', () => ({ default: () => <div /> }))
// Stand-in for the wizard that reports the props the drafts hand-off depends on.
vi.mock('../pages/shared/CreateTestWizard', () => ({
  default: ({ openDraft, preselectedJobId }) => (
    <div data-testid="wizard" data-draft={openDraft?.id ?? ''} data-job={preselectedJobId ?? ''} />
  ),
}))

import HRDashboard from '../pages/hr/HRDashboard'

const DRAFT = {
  id: 7,
  title: 'React Backend Test',
  job_position: 'Backend Dev',
  job_id: 'job-1',
  question_count: 12,
  updated_at: '2026-09-01T10:00:00Z',
  interview_configuration: { interview_type: 'TECHNICAL' },
}

beforeEach(() => {
  vi.clearAllMocks()
  localStorage.clear()
  get.mockImplementation((url) => {
    if (url === '/interviewer/tests/drafts') return Promise.resolve({ data: { drafts: [DRAFT] } })
    return Promise.resolve({ data: [] })
  })
  del.mockResolvedValue({ data: { message: 'Draft deleted successfully' } })
})

const renderAt = (path) => render(
  <MemoryRouter initialEntries={[path]}><HRDashboard /></MemoryRouter>
)

describe('HR — Exam Drafts tab', () => {
  it('renders the drafts tab at /hr/exam-drafts', async () => {
    renderAt('/hr/exam-drafts')

    expect(await screen.findByText('Unfinished Drafts')).toBeInTheDocument()
    expect(await screen.findByText('React Backend Test')).toBeInTheDocument()
    expect(get).toHaveBeenCalledWith('/interviewer/tests/drafts')
  })

  it('opens the draft in the Create Test wizard on Continue', async () => {
    const user = userEvent.setup()
    renderAt('/hr/exam-drafts')

    await screen.findByText('React Backend Test')
    await user.click(screen.getByRole('button', { name: /Continue/i }))

    // The wizard lives inside the Job Postings screen, so Continue has to switch tabs too.
    const wizard = await screen.findByTestId('wizard')
    expect(wizard).toHaveAttribute('data-draft', '7')
    expect(wizard).toHaveAttribute('data-job', 'job-1')
  })

  it('deletes a draft so its job is free again', async () => {
    const user = userEvent.setup()
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    renderAt('/hr/exam-drafts')

    await screen.findByText('React Backend Test')
    await user.click(screen.getByRole('button', { name: /Delete/i }))

    await waitFor(() => expect(del).toHaveBeenCalledWith('/interviewer/interviews/7'))
  })
})
