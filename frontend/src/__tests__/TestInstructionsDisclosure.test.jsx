/**
 * Monitoring disclosure and consent on the pre-test page.
 *
 * A proctored test must say what is collected, who sees it and what can end the test,
 * and must ask for consent to that separately from agreeing to the test rules. An
 * unproctored test must not tell the candidate their camera has to stay on.
 */
// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom')
  return { ...actual, useNavigate: () => vi.fn(), useParams: () => ({ interviewId: '7' }) }
})
vi.mock('../api/client', () => ({ default: { get: vi.fn(), post: vi.fn(() => Promise.resolve({ data: {} })) } }))

import api from '../api/client'
import TestInstructions from '../pages/candidate/TestInstructions'

const interview = (extra = {}) => ({
  id: 7, title: 'Backend Assessment', job_title: 'Backend Engineer', status: 'SCHEDULED',
  enable_webcam_monitoring: false, ...extra,
})
const route = (iv) => (url) =>
  String(url).includes('/candidate/interviews')
    ? Promise.resolve({ data: [iv] })
    : Promise.resolve({ data: { questions: [] } })

beforeEach(() => { vi.clearAllMocks() })

describe('proctored test', () => {
  beforeEach(() => { api.get.mockImplementation(route(interview({ enable_webcam_monitoring: true }))) })

  it('explains what is monitored before the test', async () => {
    render(<MemoryRouter><TestInstructions /></MemoryRouter>)
    expect(await screen.findByText('How This Test Is Monitored')).toBeTruthy()
    expect(screen.getByText(/not an identity-document check/i)).toBeTruthy()
    expect(screen.getByText(/Audio is not recorded or transcribed/i)).toBeTruthy()
    expect(screen.getByText(/Nothing detected during the test\s+rejects you automatically/i)).toBeTruthy()
    expect(screen.getByText(/Only the hiring team for this job/i)).toBeTruthy()
  })

  it('asks for monitoring consent separately from the test rules', async () => {
    render(<MemoryRouter><TestInstructions /></MemoryRouter>)
    await screen.findByText('How This Test Is Monitored')
    const boxes = screen.getAllByRole('checkbox')
    expect(boxes).toHaveLength(2)
    expect(screen.getByText(/I understand how this test is monitored/i)).toBeTruthy()
  })
})

describe('unproctored test', () => {
  beforeEach(() => { api.get.mockImplementation(route(interview())) })

  it('has no monitoring disclosure, consent box or camera requirement', async () => {
    render(<MemoryRouter><TestInstructions /></MemoryRouter>)
    await screen.findByText('System Check')
    expect(screen.queryByText('How This Test Is Monitored')).toBeNull()
    expect(screen.getAllByRole('checkbox')).toHaveLength(1)
    expect(screen.queryByText(/Camera and microphone must stay/i)).toBeNull()
  })
})
