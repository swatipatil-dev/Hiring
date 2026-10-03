import { useEffect, useState } from 'react'
import { ShieldCheck } from 'lucide-react'
import api from '../../../api/client'
import { Card, CardHeader, Notice } from './ProfileFields'

/*
 * Two-factor authentication (TOTP) for the signed-in user. Admins and superadmins
 * are enrolled at sign-in and cannot turn it off; everyone else can opt in here.
 * The server re-checks every step — this card only drives the calls.
 */

const detail = (err, fallback) => {
  const d = err?.response?.data?.detail
  return typeof d === 'string' ? d : fallback
}

function RecoveryCodes({ codes, onDismiss }) {
  return (
    <div style={{ marginTop: 12 }}>
      <p style={{ fontSize: '0.85rem', margin: '0 0 8px' }}>
        Save these recovery codes somewhere safe. Each works once, and they will not be shown again.
      </p>
      <div style={{
        display: 'grid', gridTemplateColumns: 'repeat(2, max-content)', gap: '6px 24px',
        fontFamily: 'var(--font-mono)', fontSize: '0.88rem',
      }}>
        {codes.map(c => <span key={c}>{c}</span>)}
      </div>
      <button className="btn btn-secondary btn-sm" style={{ marginTop: 10 }} onClick={onDismiss}>
        I have saved them
      </button>
    </div>
  )
}

export default function MfaSettingsCard() {
  const [status, setStatus] = useState(null)
  const [mode, setMode] = useState(null)          // 'enroll' | 'disable' | 'regenerate'
  const [setup, setSetup] = useState(null)
  const [code, setCode] = useState('')
  const [password, setPassword] = useState('')
  const [codes, setCodes] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [done, setDone] = useState('')

  const load = () => api.get('/auth/mfa/status').then(r => setStatus(r.data)).catch(() => setStatus(null))
  useEffect(() => { load() }, [])

  const reset = () => { setMode(null); setSetup(null); setCode(''); setPassword(''); setError('') }

  const startEnroll = async () => {
    setError(''); setDone(''); setBusy(true)
    try {
      const r = await api.post('/auth/mfa/enroll/start', {})
      setSetup(r.data)
      setMode('enroll')
    } catch (err) { setError(detail(err, 'Could not start set-up.')) }
    setBusy(false)
  }

  const submit = async (e) => {
    e.preventDefault()
    setError(''); setBusy(true)
    try {
      if (mode === 'enroll') {
        const r = await api.post('/auth/mfa/enroll/confirm', { code: code.trim() })
        // Enrolling signs out every other session; keep this one with the new token.
        if (r.data?.access_token) localStorage.setItem('hireflow_token', r.data.access_token)
        setCodes(r.data.recovery_codes)
        setDone('Two-factor authentication is on.')
      } else if (mode === 'regenerate') {
        const r = await api.post('/auth/mfa/recovery-codes', { password, code: code.trim() })
        setCodes(r.data.recovery_codes)
        setDone('New recovery codes issued; the old ones no longer work.')
      } else if (mode === 'disable') {
        const r = await api.post('/auth/mfa/disable', { password, code: code.trim() })
        if (r.data?.access_token) localStorage.setItem('hireflow_token', r.data.access_token)
        setDone('Two-factor authentication is off.')
      }
      reset()
      load()
    } catch (err) {
      setError(detail(err, 'That did not work. Check the code and try again.'))
      setCode('')
    }
    setBusy(false)
  }

  if (!status) return null

  return (
    <Card>
      <CardHeader
        icon={ShieldCheck}
        title="Two-factor authentication"
        action={!mode && !codes && (
          status.enabled ? (
            <div style={{ display: 'flex', gap: 8 }}>
              <button className="btn btn-secondary btn-sm" onClick={() => { setMode('regenerate'); setDone('') }}>
                New recovery codes
              </button>
              {!status.required && (
                <button className="btn btn-secondary btn-sm" onClick={() => { setMode('disable'); setDone('') }}>
                  Turn off
                </button>
              )}
            </div>
          ) : (
            <button className="btn btn-primary btn-sm" onClick={startEnroll} disabled={busy}>Turn on</button>
          )
        )}
      />

      <div style={{ fontSize: '0.85rem', color: 'var(--text-secondary)' }}>
        {status.enabled
          ? `On — ${status.recovery_codes_remaining} recovery code${status.recovery_codes_remaining === 1 ? '' : 's'} left.`
          : 'Off. Add a code from an authenticator app to every sign-in.'}
        {status.required && ' Required for your role.'}
      </div>

      {codes && <RecoveryCodes codes={codes} onDismiss={() => setCodes(null)} />}

      {mode && (
        <form onSubmit={submit} style={{ display: 'grid', gap: 12, maxWidth: 420, marginTop: 14 }}>
          {mode === 'enroll' && setup && (
            <div style={{ fontSize: '0.85rem', lineHeight: 1.5 }}>
              Add this key in your authenticator app
              (or <a href={setup.otpauth_uri}>open it on your phone</a>), then enter the code it shows:
              <code style={{ display: 'block', marginTop: 6, padding: '6px 8px', wordBreak: 'break-all',
                             background: 'var(--surface-subtle)', borderRadius: 6 }}>{setup.secret}</code>
            </div>
          )}
          {(mode === 'disable' || mode === 'regenerate') && (
            <label className="mp-edit-field">
              <span>Password</span>
              <input className="input" type="password" autoComplete="current-password"
                     value={password} onChange={e => setPassword(e.target.value)} required />
            </label>
          )}
          <label className="mp-edit-field">
            <span>{mode === 'enroll' ? 'Code from the app' : 'Current code (or a recovery code)'}</span>
            <input className="input" value={code} onChange={e => setCode(e.target.value)}
                   autoComplete="one-time-code" inputMode="numeric" required autoFocus />
          </label>
          <div style={{ display: 'flex', gap: 8 }}>
            <button className="btn btn-primary btn-sm" type="submit" disabled={busy}>
              {busy ? 'Checking…' : 'Confirm'}
            </button>
            <button className="btn btn-secondary btn-sm" type="button" onClick={reset} disabled={busy}>
              Cancel
            </button>
          </div>
        </form>
      )}

      <Notice kind="error">{error}</Notice>
      <Notice kind="success">{done}</Notice>
    </Card>
  )
}
