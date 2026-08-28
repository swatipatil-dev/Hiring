/**
 * The exam must enter fullscreen from the Start button.
 *
 * This is the whole reason the request lives on the instructions page rather than on the
 * exam screen: browsers only grant fullscreen from a user gesture, so an effect running
 * when the exam route mounts would be refused every time. That constraint is invisible
 * in the code — it looks like the request could go anywhere — so it is pinned here.
 *
 * Also pinned: a browser that cannot go fullscreen still gets to take the test. iOS
 * Safari has no element fullscreen, and nobody should lose a job application over the
 * phone they own.
 */
// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

const navigate = vi.fn()
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom')
  return { ...actual, useNavigate: () => navigate, useParams: () => ({ interviewId: '7' }) }
})

vi.mock('../api/client', () => ({
  default: { get: vi.fn(), post: vi.fn() },
}))

const requestFullscreen = vi.fn(async () => true)
const isSupported = vi.fn(() => true)
vi.mock('../utils/fullscreen', () => ({
  isSupported: (...a) => isSupported(...a),
  requestFullscreen: (...a) => requestFullscreen(...a),
  isFullscreen: () => false,
  exitFullscreen: vi.fn(),
  onFullscreenChange: () => () => {},
}))

import api from '../api/client'
import TestInstructions from '../pages/candidate/TestInstructions'

/** An interview with no webcam requirement, so the Start button only needs the tickbox. */
const interview = (extra = {}) => ({
  id: 7, title: 'Backend Assessment', job_title: 'Backend Engineer',
  duration_minutes: 30, enable_webcam_monitoring: false, ...extra,
})

const route = (data) => (url) => {
  if (String(url).includes('/candidate/interviews')) return Promise.resolve({ data })
  return Promise.resolve({ data: { questions: [] } })
}

beforeEach(() => {
  vi.clearAllMocks()
  requestFullscreen.mockResolvedValue(true)
  isSupported.mockReturnValue(true)
  api.get.mockImplementation(route([interview()]))
})

const startTest = async () => {
  const user = (await import('@testing-library/user-event')).default
  render(<MemoryRouter><TestInstructions /></MemoryRouter>)
  const agree = await screen.findByRole('checkbox')
  await user.click(agree)
  await user.click(screen.getByRole('button', { name: /Start Test/i }))
  return user
}

describe('Start Test enters fullscreen', () => {
  it('requests fullscreen before navigating to the exam', async () => {
    await startTest()
    await waitFor(() => expect(requestFullscreen).toHaveBeenCalled())
    expect(navigate).toHaveBeenCalledWith('/test/7/exam')
  })

  it('still starts the test when the browser has no fullscreen', async () => {
    // iOS Safari. The exam reports the gap to the proctor and runs anyway.
    isSupported.mockReturnValue(false)
    await startTest()
    await waitFor(() => expect(navigate).toHaveBeenCalledWith('/test/7/exam'))
    expect(requestFullscreen).not.toHaveBeenCalled()
  })

  it('still starts the test when the request is refused', async () => {
    requestFullscreen.mockResolvedValue(false)
    await startTest()
    await waitFor(() => expect(navigate).toHaveBeenCalledWith('/test/7/exam'))
  })

  it('skips fullscreen when the test does not require it', async () => {
    api.get.mockImplementation(route([interview({ require_fullscreen: false })]))
    await startTest()
    await waitFor(() => expect(navigate).toHaveBeenCalledWith('/test/7/exam'))
    expect(requestFullscreen).not.toHaveBeenCalled()
  })

  it('defaults to requiring fullscreen when the flag is absent', async () => {
    // Tests created before this flag existed must not silently lose the protection.
    api.get.mockImplementation(route([interview()]))   // no require_fullscreen key
    await startTest()
    await waitFor(() => expect(requestFullscreen).toHaveBeenCalled())
  })
})
