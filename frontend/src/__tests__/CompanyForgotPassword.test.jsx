/**
 * Password reset reached from a company login page (/<slug>/forgot-password).
 *
 * One email can have an account at several companies. Without `company_slug` the
 * backend only finds the user when the email is unique across all companies, and
 * otherwise answers "OTP sent" without sending anything, so a missing slug fails
 * silently. The user must also land back on their company's login, not the
 * platform /login.
 */
// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'

vi.mock('../api/client', () => ({
  default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), patch: vi.fn(), delete: vi.fn() },
}))

import api from '../api/client'
import ForgotPassword from '../pages/ForgotPassword'

const renderAt = (path) =>
  render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/forgot-password" element={<ForgotPassword />} />
        <Route path="/:companySlug/forgot-password" element={<ForgotPassword />} />
      </Routes>
    </MemoryRouter>
  )

const resetThrough = async (user) => {
  await user.type(screen.getByPlaceholderText('you@email.com'), 'ann@acme.com')
  await user.click(screen.getByRole('button', { name: 'Send OTP' }))
  const boxes = await screen.findAllByRole('textbox')
  for (const [i, d] of [...'123456'].entries()) await user.type(boxes[i], d)
  await user.click(screen.getByRole('button', { name: 'Verify OTP' }))
  await user.type(screen.getByPlaceholderText('Min 8 characters'), 'N3w-Passw0rd!')
  await user.type(screen.getByPlaceholderText('Re-enter password'), 'N3w-Passw0rd!')
  await user.click(screen.getByRole('button', { name: 'Reset Password' }))
  await screen.findByText('Password reset!')
}

describe('company password reset', () => {
  beforeEach(() => {
    vi.mocked(api.post).mockReset()
    vi.mocked(api.post).mockResolvedValue({ data: {} })
  })

  it('scopes both calls to the company and links back to its login', async () => {
    const user = userEvent.setup()
    renderAt('/acme-corp/forgot-password')

    expect(screen.getByRole('link', { name: /back to login/i })).toHaveAttribute('href', '/acme-corp')
    await resetThrough(user)

    expect(api.post).toHaveBeenCalledWith('/auth/forgot-password',
      { email: 'ann@acme.com', company_slug: 'acme-corp' })
    expect(api.post).toHaveBeenCalledWith('/auth/reset-password',
      { email: 'ann@acme.com', otp: '123456', new_password: 'N3w-Passw0rd!', company_slug: 'acme-corp' })
  })

  it('leaves the platform reset unscoped', async () => {
    const user = userEvent.setup()
    renderAt('/forgot-password')

    expect(screen.getByRole('link', { name: /back to login/i })).toHaveAttribute('href', '/login')
    await resetThrough(user)

    expect(api.post).toHaveBeenCalledWith('/auth/forgot-password', { email: 'ann@acme.com' })
  })
})
