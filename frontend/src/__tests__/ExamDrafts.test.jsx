/**
 * The Exam Drafts table, shared by the Interviewer and HR dashboards.
 *
 * Written before the interviewer was switched over to this component, because the block it
 * replaces (InterviewerDashboard's inline drafts table) had no test at all — so the refactor
 * had no safety net until this file existed.
 *
 * The load-failure case is pinned deliberately: the original code carries a comment
 * explaining that a failed request and an empty result must never render the same, and that
 * is exactly the kind of thing a later "simplification" quietly undoes.
 */
// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

vi.mock('../api/client', () => ({
  default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}))

import api from '../api/client'
import ExamDrafts from '../components/ExamDrafts'

const DRAFTS = [
  {
    id: 7,
    title: 'React Backend Test',
    job_position: 'Backend Dev',
    job_id: 'job-1',
    question_count: 12,
    updated_at: '2026-09-01T10:00:00Z',
    interview_configuration: { interview_type: 'TECHNICAL' },
  },
  {
    id: 8,
    title: '',
    job_position: 'QA Engineer',
    job_id: 'job-2',
    question_count: 0,
    updated_at: null,
    interview_configuration: {},
  },
]

beforeEach(() => {
  vi.clearAllMocks()
  api.get.mockResolvedValue({ data: { drafts: DRAFTS } })
  api.delete.mockResolvedValue({ data: { message: 'Draft deleted successfully' } })
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('ExamDrafts', () => {
  it('lists drafts from /interviewer/tests/drafts', async () => {
    render(<ExamDrafts onContinue={() => {}} />)

    expect(await screen.findByText('React Backend Test')).toBeInTheDocument()
    expect(api.get).toHaveBeenCalledWith('/interviewer/tests/drafts')
    // Falls back to job_position when the draft was never titled.
    expect(screen.getByText('QA Engineer')).toBeInTheDocument()
    expect(screen.getByText('TECHNICAL')).toBeInTheDocument()
  })

  it('distinguishes a draft with work in it from an abandoned empty one', async () => {
    render(<ExamDrafts onContinue={() => {}} />)

    expect(await screen.findByText('12')).toBeInTheDocument()
    // The empty badge is the signal that nothing was ever generated — it is also what
    // publish_interview uses when it garbage-collects sibling drafts.
    expect(screen.getByText('empty')).toBeInTheDocument()
  })

  it('hands the whole draft row to onContinue', async () => {
    const user = userEvent.setup()
    const onContinue = vi.fn()
    render(<ExamDrafts onContinue={onContinue} />)

    await screen.findByText('React Backend Test')
    await user.click(screen.getAllByRole('button', { name: /Continue/i })[0])

    expect(onContinue).toHaveBeenCalledWith(DRAFTS[0])
  })

  it('deletes after confirmation and refetches', async () => {
    const user = userEvent.setup()
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    render(<ExamDrafts onContinue={() => {}} />)

    await screen.findByText('React Backend Test')
    await user.click(screen.getAllByRole('button', { name: /Delete/i })[0])

    await waitFor(() => expect(api.delete).toHaveBeenCalledWith('/interviewer/interviews/7'))
    // Refetched, so the freed job shows up in Create Test straight away.
    await waitFor(() => expect(api.get).toHaveBeenCalledTimes(2))
  })

  it('does not delete when the confirmation is dismissed', async () => {
    const user = userEvent.setup()
    vi.spyOn(window, 'confirm').mockReturnValue(false)
    render(<ExamDrafts onContinue={() => {}} />)

    await screen.findByText('React Backend Test')
    await user.click(screen.getAllByRole('button', { name: /Delete/i })[0])

    expect(api.delete).not.toHaveBeenCalled()
  })

  it('names the draft and its question count in the confirmation', async () => {
    const user = userEvent.setup()
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)
    render(<ExamDrafts onContinue={() => {}} />)

    await screen.findByText('React Backend Test')
    await user.click(screen.getAllByRole('button', { name: /Delete/i })[0])

    // The row has no job column — Type is the interview type — so the confirmation has to
    // carry enough identity on its own.
    const msg = confirm.mock.calls[0][0]
    expect(msg).toContain('React Backend Test')
    expect(msg).toContain('12 questions')
  })

  it('shows an error instead of the empty state when the load fails', async () => {
    api.get.mockRejectedValue({ response: { data: { detail: 'boom' } } })
    render(<ExamDrafts onContinue={() => {}} />)

    expect(await screen.findByText(/Couldn't load drafts — boom/)).toBeInTheDocument()
    // "No unfinished drafts" here would be a lie that sends someone hunting for a draft
    // that does exist.
    expect(screen.queryByText('No unfinished drafts.')).not.toBeInTheDocument()
  })

  it('surfaces a failed delete rather than silently leaving the row', async () => {
    const user = userEvent.setup()
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    api.delete.mockRejectedValue({ response: { data: { detail: 'Only DRAFT tests can be deleted (this one is READY).' } } })
    render(<ExamDrafts onContinue={() => {}} />)

    await screen.findByText('React Backend Test')
    await user.click(screen.getAllByRole('button', { name: /Delete/i })[0])

    expect(await screen.findByText(/Only DRAFT tests can be deleted/)).toBeInTheDocument()
  })

  it('renders the empty state when there genuinely are none', async () => {
    api.get.mockResolvedValue({ data: { drafts: [] } })
    render(<ExamDrafts onContinue={() => {}} />)

    expect(await screen.findByText('No unfinished drafts.')).toBeInTheDocument()
  })
})
