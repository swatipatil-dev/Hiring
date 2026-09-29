import { useState } from 'react'
import { KeyRound } from 'lucide-react'
import api from '../../../api/client'
import { Card, CardHeader, Notice } from './ProfileFields'

const MIN_LENGTH = 8
const EMPTY = { current_password: '', new_password: '', confirm_password: '' }

export default function ChangePasswordCard() {
  const [open, setOpen] = useState(false)
  const [form, setForm] = useState(EMPTY)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [done, setDone] = useState('')

  const set = (k) => (e) => setForm(f => ({ ...f, [k]: e.target.value }))

  const submit = async (e) => {
    e.preventDefault()
    setError('')

    // Mirrors the server's rules so the common mistakes cost no round trip.
    // The server re-checks all three regardless — this is convenience, not
    // validation.
    if (form.new_password !== form.confirm_password) {
      return setError('The new passwords do not match.')
    }
    if (form.new_password.length < MIN_LENGTH) {
      return setError(`New password must be at least ${MIN_LENGTH} characters.`)
    }
    if (form.new_password === form.current_password) {
      return setError('New password must be different from your current password.')
    }

    setSaving(true)
    try {
      // confirm_password is deliberately NOT sent — it is a client-side check,
      // and the endpoint rejects unknown fields.
      const res = await api.post('/auth/change-password', {
        current_password: form.current_password,
        new_password: form.new_password,
      })
      // The change revoked every existing token, this one included; the response
      // carries a fresh one so this tab stays signed in.
      if (res.data?.access_token) localStorage.setItem('hireflow_token', res.data.access_token)
      setForm(EMPTY)
      setOpen(false)
      setDone('Password changed. You have been signed out on every other device.')
    } catch (err) {
      const status = err.response?.status
      if (status === 429) {
        setError('Too many attempts — wait a minute and try again.')
      } else {
        setError(err.response?.data?.detail || 'Could not change your password.')
      }
      // Keep the new-password values so a typo in the CURRENT field doesn't
      // cost the user the whole form.
      setForm(f => ({ ...f, current_password: '' }))
    }
    setSaving(false)
  }

  return (
    <Card>
      <CardHeader
        icon={KeyRound}
        title="Security"
        action={!open && (
          <button className="btn btn-secondary btn-sm" onClick={() => { setOpen(true); setDone('') }}>
            Change password
          </button>
        )}
      />

      {!open && (
        <div style={{ fontSize: '0.85rem', color: 'var(--text-secondary)' }}>
          Password <span style={{ fontFamily: 'var(--font-mono)', letterSpacing: 2 }}>••••••••</span>
        </div>
      )}

      {open && (
        <form onSubmit={submit} style={{ display: 'grid', gap: 14, maxWidth: 420 }}>
          <label className="mp-edit-field">
            <span>Current password</span>
            <input className="input" type="password" autoComplete="current-password"
                   value={form.current_password} onChange={set('current_password')} autoFocus required />
          </label>
          <label className="mp-edit-field">
            <span>New password</span>
            <input className="input" type="password" autoComplete="new-password"
                   value={form.new_password} onChange={set('new_password')}
                   minLength={MIN_LENGTH} required />
          </label>
          <label className="mp-edit-field">
            <span>Confirm new password</span>
            <input className="input" type="password" autoComplete="new-password"
                   value={form.confirm_password} onChange={set('confirm_password')} required />
          </label>

          <div style={{ display: 'flex', gap: 8 }}>
            <button className="btn btn-primary btn-sm" type="submit" disabled={saving}>
              {saving ? 'Changing…' : 'Change password'}
            </button>
            <button className="btn btn-secondary btn-sm" type="button" disabled={saving}
                    onClick={() => { setOpen(false); setForm(EMPTY); setError('') }}>
              Cancel
            </button>
          </div>

          <p style={{ fontSize: '0.75rem', color: 'var(--text-tertiary)', lineHeight: 1.5, margin: 0 }}>
            At least {MIN_LENGTH} characters, mixing at least two of lowercase,
            uppercase, digits and symbols. Changing your password signs you out on
            every other device.
          </p>
        </form>
      )}

      <Notice kind="error">{error}</Notice>
      <Notice kind="success">{done}</Notice>
    </Card>
  )
}
