/**
 * HR Compose Mail — the Send button must actually send.
 *
 * This button previously validated the form, called `alert('Mail sent to …!')`,
 * and made no request at all. These tests pin down that it now posts to
 * /hr/send-mail, and that it reports queued-vs-sent honestly rather than
 * replacing one overclaim with another.
 */
// @vitest-environment jsdom
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'

const post = vi.fn()
const get = vi.fn()

// HRDashboard reads the signed-in user (to tell owner / co-HR apart); in the app it
// always sits inside AuthProvider, which these isolated renders don't mount.
vi.mock('../context/AuthContext', () => ({ useAuth: () => ({ user: { role: 'hr' } }) }))
vi.mock('../api/client', () => ({
  default: {
    get: (...a) => get(...a),
    post: (...a) => post(...a),
    put: vi.fn(() => Promise.resolve({ data: {} })),
    delete: vi.fn(() => Promise.resolve({ data: {} })),
  },
}))

// Heavy children are irrelevant to the send path and drag in charts/monaco/websockets.
vi.mock('../components/Header', () => ({ default: () => <div /> }))
vi.mock('../components/StageTimeline', () => ({ default: () => <div /> }))
vi.mock('../components/ProctoringReport', () => ({ default: () => <div /> }))
vi.mock('../pages/hr/PipelineNotifications', () => ({ default: () => <div /> }))
vi.mock('../pages/hr/MailFromPicker', () => ({ default: () => <div data-testid="from-picker" /> }))
vi.mock('../pages/shared/CreateTestWizard', () => ({ default: () => <div /> }))
vi.mock('../pages/hr/ResumeMatcher', () => ({ default: () => <div /> }))

import HRDashboard from '../pages/hr/HRDashboard'

function renderOffersTab() {
  return render(
    <MemoryRouter initialEntries={['/hr/offers']}>
      <HRDashboard />
    </MemoryRouter>
  )
}

async function openComposeAndFill(user) {
  const openers = await screen.findAllByRole('button', { name: /send mail/i })
  await user.click(openers[0])

  await user.type(await screen.findByPlaceholderText(/candidate@email\.com/i), 'x@y.com')
  const [subject] = screen.getAllByPlaceholderText(/subject/i)
  await user.type(subject, 'Interview')
  await user.type(screen.getByPlaceholderText(/write your message here/i), 'Hello there')

  // The modal's own Send button is the last one matching.
  const buttons = screen.getAllByRole('button', { name: /send mail/i })
  return buttons[buttons.length - 1]
}

describe('HR Compose Mail', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    get.mockResolvedValue({ data: [] })
    post.mockResolvedValue({ data: { queued: true, mode: 'outbox', status: 'queued', message_id: 'm1' } })
    localStorage.clear()
    vi.spyOn(window, 'alert').mockImplementation(() => {})
  })

  it('posts to /hr/send-mail with the composed fields', async () => {
    const user = userEvent.setup()
    renderOffersTab()
    const send = await openComposeAndFill(user)

    await user.click(send)

    await waitFor(() => {
      expect(post).toHaveBeenCalledWith('/hr/send-mail', expect.objectContaining({
        to: 'x@y.com',
        subject: 'Interview',
        body: 'Hello there',
      }))
    })
  })

  it('never claims the mail was sent without a request', async () => {
    const user = userEvent.setup()
    renderOffersTab()
    const send = await openComposeAndFill(user)

    await user.click(send)

    await waitFor(() => expect(post).toHaveBeenCalled())
    // The old implementation's exact tell.
    const alerts = window.alert.mock.calls.flat().join(' ')
    expect(alerts).not.toMatch(/^Mail sent to/)
  })

  it('says "Queued" — not "Sent" — when the worker has not run yet', async () => {
    const user = userEvent.setup()
    renderOffersTab()
    const send = await openComposeAndFill(user)

    await user.click(send)

    await waitFor(() => expect(window.alert).toHaveBeenCalled())
    const message = window.alert.mock.calls.flat().join(' ')
    expect(message).toMatch(/queued/i)
  })

  it('keeps the draft and says why when the email is blocked', async () => {
    post.mockResolvedValueOnce({ data: { queued: false, status: 'blocked',
                                         error: 'No sender is configured for this company.' } })
    const user = userEvent.setup()
    renderOffersTab()
    const send = await openComposeAndFill(user)

    await user.click(send)

    expect(await screen.findByText(/Not sent: No sender is configured/i)).toBeInTheDocument()
    expect(window.alert).not.toHaveBeenCalled()
    expect(screen.getByPlaceholderText(/write your message here/i)).toHaveValue('Hello there')
  })

  it('surfaces a server error instead of closing the dialog silently', async () => {
    post.mockRejectedValueOnce({ response: { data: { detail: 'SMTP not configured' } } })
    const user = userEvent.setup()
    renderOffersTab()
    const send = await openComposeAndFill(user)

    await user.click(send)

    expect(await screen.findByText(/SMTP not configured/i)).toBeInTheDocument()
  })

  it('does not send when the recipient is missing', async () => {
    const user = userEvent.setup()
    renderOffersTab()
    const openers = await screen.findAllByRole('button', { name: /send mail/i })
    await user.click(openers[0])

    const buttons = screen.getAllByRole('button', { name: /send mail/i })
    await user.click(buttons[buttons.length - 1])

    expect(post).not.toHaveBeenCalledWith('/hr/send-mail', expect.anything())
    expect(await screen.findByText(/recipient email/i)).toBeInTheDocument()
  })
})
