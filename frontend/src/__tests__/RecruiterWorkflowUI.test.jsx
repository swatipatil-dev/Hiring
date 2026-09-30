/**
 * Recruiter-workflow UI: the bulk action confirms the previewed count, scorecards say
 * why colleagues' scores are hidden, and a candidate can withdraw only after confirming.
 */
// @vitest-environment jsdom
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

const get = vi.fn()
const post = vi.fn()
const put = vi.fn()
vi.mock('../api/client', () => ({
  default: {
    get: (...a) => get(...a),
    post: (...a) => post(...a),
    put: (...a) => put(...a),
    patch: vi.fn(() => Promise.resolve({ data: {} })),
  },
}))

import BulkActionBar from '../components/workflow/BulkActionBar'
import ScorecardPanel from '../components/workflow/ScorecardPanel'
import ApplicationsTab from '../pages/candidate/tabs/ApplicationsTab'
import { initialSource, sourcePayload } from '../pages/candidate/SourceFields'

beforeEach(() => { get.mockReset(); post.mockReset(); put.mockReset() })

describe('BulkActionBar', () => {
  it('previews, then executes with the previewed count', async () => {
    const user = userEvent.setup()
    get.mockResolvedValue({ data: { stages: [] } })
    post.mockImplementation((url) => {
      if (url === '/hr/workflow/bulk/preview') {
        return Promise.resolve({ data: {
          id: 'op1', eligible_count: 2, requested_count: 3, action_label: 'Reject',
          preview: { target_stage: 'rejected', warnings: ['1 candidate(s) have an outstanding offer.'],
            eligible: [{ candidate_id: 'a', name: 'A' }, { candidate_id: 'b', name: 'B' }],
            skipped: [{ candidate_id: 'c', name: 'C', reason: 'Already hired' }] },
        } })
      }
      return Promise.resolve({ data: { action_label: 'Reject', affected_count: 2, rejection_emails: { failed: 0 } } })
    })
    const onDone = vi.fn()
    render(<BulkActionBar selectedIds={['a', 'b', 'c']} onClear={() => {}} onDone={onDone} />)

    await user.selectOptions(screen.getByLabelText('Bulk action'), 'reject')
    await user.click(screen.getByRole('button', { name: 'Preview' }))

    expect(await screen.findByText(/outstanding offer/)).toBeInTheDocument()
    expect(screen.getByText(/1 will be skipped/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Reject 2 candidates' }))

    await waitFor(() => expect(onDone).toHaveBeenCalled())
    expect(post).toHaveBeenCalledWith('/hr/workflow/bulk/op1/execute', { confirm_count: 2 })
  })

  it('needs a target stage before previewing a move', async () => {
    const user = userEvent.setup()
    get.mockResolvedValue({ data: { stages: [{ key: 'shortlisted', label: 'Shortlisted', is_active: true }] } })
    render(<BulkActionBar selectedIds={['a']} onClear={() => {}} />)
    await user.selectOptions(screen.getByLabelText('Bulk action'), 'move_stage')
    expect(screen.getByRole('button', { name: 'Preview' })).toBeDisabled()
    await user.selectOptions(screen.getByLabelText('Target stage'), 'shortlisted')
    expect(screen.getByRole('button', { name: 'Preview' })).toBeEnabled()
  })
})

describe('ScorecardPanel', () => {
  it('explains that colleagues are hidden until you submit', async () => {
    get.mockResolvedValue({ data: {
      interview_id: 1, candidate_name: 'Asha Rao', job_title: 'Backend', is_panelist: true, can_edit: true,
      my_scorecard: null, others_visible: false, others: null, summary: null,
      hidden_reason: "Other panellists' scorecards appear once you submit your own.",
      panel: [{ name: 'Alice', email: 'a@x', status: 'submitted' }, { name: 'Bob', email: 'b@x', status: 'not_started' }],
    } })
    render(<ScorecardPanel interviewId={1} onClose={() => {}} />)
    expect(await screen.findByText(/appear once you submit your own/)).toBeInTheDocument()
    expect(screen.getByText('Alice: Submitted')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Submit scorecard' })).toBeInTheDocument()
  })
})

describe('ApplicationsTab withdrawal', () => {
  const apps = [{ id: 'app1', job: 'Backend Dev', applied: '1/1/2026', status: 'hr_review',
    statusLabel: 'Under review', statusMessage: 'A recruiter is reviewing your application.', canWithdraw: true }]

  it('shows the plain-language status and withdraws after confirming', async () => {
    const user = userEvent.setup()
    post.mockResolvedValue({ data: {} })
    const onChanged = vi.fn()
    render(<ApplicationsTab apps={apps} onOpenDetails={() => {}} reportApiError={() => () => {}} onChanged={onChanged} />)

    expect(screen.getByText('Under review')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Withdraw' }))
    expect(post).not.toHaveBeenCalled()
    await user.type(screen.getByRole('textbox'), 'Took another offer')
    await user.click(screen.getByRole('button', { name: 'Withdraw application' }))

    await waitFor(() => expect(onChanged).toHaveBeenCalled())
    expect(post).toHaveBeenCalledWith('/candidate/applications/app1/withdraw',
      { confirm: true, reason: 'Took another offer' })
  })

  it('hides Withdraw when the application is final', () => {
    render(<ApplicationsTab apps={[{ ...apps[0], canWithdraw: false }]} onOpenDetails={() => {}} reportApiError={() => () => {}} />)
    expect(screen.queryByRole('button', { name: 'Withdraw' })).toBeNull()
  })
})

describe('source from a shared link', () => {
  it('reads ?source= and utm_source, and drops referrer fields for non-referrals', () => {
    expect(initialSource('?source=referral&ref=Ravi').referrer_name).toBe('Ravi')
    expect(initialSource('?utm_source=linkedin')).toMatchObject({ source: 'job_portal', source_detail: 'linkedin' })
    expect(initialSource('').source).toBe('direct')
    expect(sourcePayload({ source: 'agency', source_detail: ' ABC ', referrer_name: 'x' }))
      .toEqual({ source: 'agency', source_detail: 'ABC' })
  })
})
