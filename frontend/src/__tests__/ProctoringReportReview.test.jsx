/**
 * Reviewer outcomes in the proctoring report.
 *
 * A reviewer records Confirmed / Dismissed / Inconclusive per timeline entry. Confirming
 * needs remarks, and one review covers every reading folded into an entry.
 */
// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

vi.mock('../api/client', () => ({ default: { get: vi.fn(), post: vi.fn() } }))
import api from '../api/client'
import ProctoringReport from '../components/ProctoringReport'

const t = (sec) => new Date(Date.UTC(2026, 8, 28, 10, 0, sec)).toISOString().replace('Z', '')
const violation = (id, type, sec, extra = {}) => ({
  id, type, severity: 'MEDIUM', module: 'face', score_impact: 10, timestamp: t(sec),
  details: {}, evidence_url: null, review: null, reviewable: true, ...extra,
})
const report = (violations, extra = {}) => ({
  interview_id: 7, session_count: 1,
  sessions: [{
    session_id: 'sess-1', status: 'completed', started_at: t(0), ended_at: t(50),
    cheating_score: 12, max_cheating_score: 20, decision: 'NORMAL', module_scores: {},
    reference: 'application_photo', monitoring_consent: null, total_violations: violations.length,
    unreviewed_count: violations.length, review: null, review_history: [], violations, ...extra,
  }],
})

beforeEach(() => {
  vi.clearAllMocks()
  api.post.mockResolvedValue({ data: { reviews: [] } })
})

describe('reviewer outcomes', () => {
  it('requires remarks to confirm, then saves for the entry', async () => {
    api.get.mockResolvedValue({ data: report([violation('v1', 'PHONE_DETECTED', 5, { module: 'object' })]) })
    const user = userEvent.setup()
    render(<ProctoringReport interviewId={7} />)

    const entry = (await screen.findByText('Phone detected')).closest('li')
    await user.click(within(entry).getByRole('button', { name: 'Confirmed' }))
    const save = within(entry).getByRole('button', { name: 'Save review' })
    expect(save.disabled).toBe(true)

    await user.type(within(entry).getByRole('textbox'), 'Phone visible in frame')
    expect(save.disabled).toBe(false)
    await user.click(save)

    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/proctor/reviews', {
      session_id: 'sess-1', violation_ids: ['v1'], outcome: 'confirmed', remarks: 'Phone visible in frame',
    }))
  })

  it('reviews every reading folded into one entry together', async () => {
    api.get.mockResolvedValue({ data: report([
      violation('a', 'HEAD_TURNED', 10), violation('b', 'HEAD_TURNED', 20), violation('c', 'HEAD_TURNED', 30),
    ]) })
    const user = userEvent.setup()
    render(<ProctoringReport interviewId={7} />)

    expect(await screen.findByText(/covers all 3 readings/)).toBeTruthy()
    const entry = screen.getByText('Head turned').closest('li')
    await user.click(within(entry).getByRole('button', { name: 'Dismissed' }))
    await user.click(within(entry).getByRole('button', { name: 'Save review' }))

    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/proctor/reviews', expect.objectContaining({
      violation_ids: ['a', 'b', 'c'], outcome: 'dismissed', remarks: null,
    })))
  })

  it('shows an existing outcome with who made it and counts what is left', async () => {
    api.get.mockResolvedValue({ data: report([
      violation('v1', 'NO_FACE', 5, { review: { id: 'r1', outcome: 'dismissed', remarks: 'Stood up to adjust the lamp', reviewer_name: 'Priya S', created_at: t(40) } }),
      violation('v2', 'MULTIPLE_VOICES', 25, { module: 'audio' }),
    ]) })
    render(<ProctoringReport interviewId={7} />)

    expect(await screen.findByText('Priya S', { exact: false })).toBeTruthy()
    expect(screen.getByText('Stood up to adjust the lamp')).toBeTruthy()
    expect(screen.getByText('1 entry still to review.')).toBeTruthy()
  })

  it('does not offer a review for a recorded fact', async () => {
    api.get.mockResolvedValue({ data: report([
      violation('f1', 'FULLSCREEN_UNAVAILABLE', 1, { module: 'behavioral', reviewable: false }),
    ]) })
    render(<ProctoringReport interviewId={7} />)
    const entry = (await screen.findByText('Fullscreen unavailable')).closest('li')
    expect(within(entry).queryByRole('button', { name: 'Confirmed' })).toBeNull()
  })
})
