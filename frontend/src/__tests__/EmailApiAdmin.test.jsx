/**
 * Admin email screens: an API key is shown exactly once, and the routing panel
 * says plainly which emails are blocked instead of implying a fallback sender.
 */
// @vitest-environment jsdom
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

const get = vi.fn()
const post = vi.fn()
vi.mock('../api/client', () => ({
  default: { get: (...a) => get(...a), post: (...a) => post(...a) },
}))

import ApiKeysPanel from '../pages/admin/ApiKeysPanel'
import MailRoutingPanel from '../pages/admin/MailRoutingPanel'

const SCOPES = ['email:send', 'email:read', 'connections:read', 'connections:write',
                'senders:read', 'senders:write']

describe('ApiKeysPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    get.mockResolvedValue({ data: { api_keys: [], scopes: SCOPES } })
  })

  it('creates a key and reveals the token once, then lists it without the token', async () => {
    post.mockResolvedValueOnce({ data: { id: 'k1', name: 'ATS', token: 'hf_live_secretvalue' } })
    get.mockResolvedValueOnce({ data: { api_keys: [], scopes: SCOPES } })
       .mockResolvedValueOnce({ data: { scopes: SCOPES, api_keys: [{
         id: 'k1', name: 'ATS', mode: 'live', key_prefix: 'hf_live_abcd1234', state: 'active',
         scopes: ['email:send', 'email:read'], expires_at: null, last_used_at: null }] } })
    const user = userEvent.setup()
    render(<ApiKeysPanel />)

    await user.click(await screen.findByRole('button', { name: /new key/i }))
    await user.type(screen.getByLabelText(/name/i), 'ATS')
    await user.click(screen.getByRole('button', { name: /create key/i }))

    await waitFor(() => expect(post).toHaveBeenCalledWith('/admin/api-keys', expect.objectContaining({
      name: 'ATS', mode: 'live', scopes: ['email:send', 'email:read'],
    })))
    expect(await screen.findByTestId('revealed-token')).toHaveTextContent('hf_live_secretvalue')
    expect(await screen.findByText('hf_live_abcd1234…')).toBeInTheDocument()

    await user.click(screen.getByTitle(/i have stored it/i))
    expect(screen.queryByText('hf_live_secretvalue')).not.toBeInTheDocument()
  })
})

describe('MailRoutingPanel', () => {
  beforeEach(() => vi.clearAllMocks())

  it('shows a blocked step as not sent, with the reason', async () => {
    get.mockResolvedValue({ data: {
      chain: [],
      events: [],
      steps: [{
        step_key: 'offer_letter', label: 'Offer Letter', enabled: true, cc: [], mailbox: null,
        fallback_reason: 'No sender is configured for this company.',
        effective: { status: 'blocked', reason_code: 'no_sender_configured',
                     reason: 'No sender is configured for this company.', from_email: null },
      }],
      delivery: { email_enabled: true, redis_available: true, queue_depth: 0,
                  dispatcher_heartbeat_age_s: 10, outbox: { blocked_24h: 2 } },
    } })
    render(<MailRoutingPanel />)

    expect(await screen.findByText('Blocked')).toBeInTheDocument()
    expect(screen.getByText(/will not be sent until that is fixed/i)).toBeInTheDocument()
    // The old panel's claim that another address would be used.
    expect(screen.queryByText(/Sending from the address above instead/i)).not.toBeInTheDocument()
  })
})
