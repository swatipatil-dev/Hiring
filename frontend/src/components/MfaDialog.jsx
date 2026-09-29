import { useEffect, useState } from 'react'
import axios from 'axios'
import { ShieldCheck, Copy, Check } from 'lucide-react'

/*
 * The second sign-in step, rendered by AuthProvider whenever /auth/login answers
 * with an MFA challenge instead of a session:
 *
 *   kind="verify" — the account has two-factor on: ask for the 6-digit code from the
 *                   authenticator app (or a one-time recovery code).
 *   kind="enroll" — the role requires two-factor and it isn't set up yet: show the key,
 *                   confirm one code, then show the recovery codes once.
 *
 * Plain axios, not the shared api client: that client treats any 401 as a dead
 * session and redirects to /login, and these calls run before there is a session.
 */

const API = '/api/v1/auth'

const overlay = {
  position: 'fixed', inset: 0, background: 'rgba(15,23,42,0.55)', zIndex: 2000,
  display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 16,
}
const panel = {
  background: 'var(--surface-card, #fff)', color: 'var(--text-primary, #0f172a)',
  borderRadius: 14, width: '100%', maxWidth: 440, padding: 24,
  boxShadow: '0 20px 50px rgba(0,0,0,0.25)', fontFamily: 'inherit',
}
const input = {
  width: '100%', padding: '10px 12px', fontSize: '1.1rem', letterSpacing: 4,
  borderRadius: 8, border: '1px solid var(--border-subtle, #cbd5e1)', boxSizing: 'border-box',
  fontFamily: 'var(--font-mono, monospace)', textAlign: 'center',
}
const primary = {
  padding: '10px 16px', borderRadius: 8, border: 'none', cursor: 'pointer', fontWeight: 600,
  background: 'var(--accent-primary, #0f766e)', color: '#fff', width: '100%', marginTop: 14,
}
const link = {
  background: 'none', border: 'none', color: 'var(--accent-primary, #0f766e)', cursor: 'pointer',
  fontSize: '0.82rem', padding: 0, marginTop: 12,
}

function errorText(err, fallback) {
  const d = err?.response?.data?.detail
  if (typeof d === 'string') return d
  if (err?.response?.status === 429) return 'Too many attempts. Wait a few minutes and try again.'
  return fallback
}

export default function MfaDialog({ kind, mfaToken, onDone, onCancel }) {
  const [code, setCode] = useState('')
  const [useRecovery, setUseRecovery] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [setup, setSetup] = useState(null)          // { secret, otpauth_uri }
  const [finished, setFinished] = useState(null)    // enrol response, while codes are shown
  const [copied, setCopied] = useState(false)

  useEffect(() => {
    if (kind !== 'enroll') return
    let cancelled = false
    axios.post(`${API}/mfa/enroll/start`, { mfa_token: mfaToken })
      .then(r => { if (!cancelled) setSetup(r.data) })
      .catch(err => { if (!cancelled) setError(errorText(err, 'Could not start set-up. Sign in again.')) })
    return () => { cancelled = true }
  }, [kind, mfaToken])

  const submit = async (e) => {
    e.preventDefault()
    setError('')
    setBusy(true)
    try {
      if (kind === 'verify') {
        const r = await axios.post(`${API}/login/mfa`, { mfa_token: mfaToken, code: code.trim() })
        onDone(r.data)
      } else {
        const r = await axios.post(`${API}/mfa/enroll/confirm`, { mfa_token: mfaToken, code: code.trim() })
        setFinished(r.data)
      }
    } catch (err) {
      setError(errorText(err, 'That code is not valid. Try again.'))
      setCode('')
    }
    setBusy(false)
  }

  const copyKey = async () => {
    try { await navigator.clipboard.writeText(setup.secret); setCopied(true) } catch { /* no clipboard */ }
  }

  return (
    <div style={overlay} role="dialog" aria-modal="true" aria-labelledby="mfa-title">
      <div style={panel}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12 }}>
          <ShieldCheck size={20} style={{ color: 'var(--accent-primary, #0f766e)' }} />
          <h2 id="mfa-title" style={{ fontSize: '1.05rem', margin: 0 }}>
            {kind === 'verify' ? 'Two-factor verification' : 'Set up two-factor authentication'}
          </h2>
        </div>

        {finished ? (
          <>
            <p style={{ fontSize: '0.88rem', lineHeight: 1.5 }}>
              Two-factor authentication is on. Save these recovery codes somewhere safe — each
              works once if you lose your phone. They will not be shown again.
            </p>
            <div style={{
              display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 6, padding: 12, borderRadius: 8,
              background: 'var(--surface-subtle, #f1f5f9)', fontFamily: 'var(--font-mono, monospace)',
              fontSize: '0.9rem',
            }}>
              {finished.recovery_codes.map(c => <span key={c}>{c}</span>)}
            </div>
            <button style={primary} onClick={() => onDone(finished)}>I have saved these codes</button>
          </>
        ) : (
          <form onSubmit={submit}>
            {kind === 'enroll' && (
              <div style={{ fontSize: '0.86rem', lineHeight: 1.5, marginBottom: 12 }}>
                <p style={{ marginTop: 0 }}>
                  Your role requires a second sign-in step. In an authenticator app (Google
                  Authenticator, Microsoft Authenticator, 1Password…), add an account with this key:
                </p>
                {setup ? (
                  <>
                    <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                      <code style={{
                        flex: 1, padding: '8px 10px', borderRadius: 6, wordBreak: 'break-all',
                        background: 'var(--surface-subtle, #f1f5f9)', fontSize: '0.85rem',
                      }}>{setup.secret}</code>
                      <button type="button" onClick={copyKey} aria-label="Copy key"
                              style={{ ...link, marginTop: 0 }}>
                        {copied ? <Check size={16} /> : <Copy size={16} />}
                      </button>
                    </div>
                    <p style={{ marginBottom: 0 }}>
                      On a phone you can <a href={setup.otpauth_uri}>open it in your authenticator app</a> directly.
                      Then enter the 6-digit code it shows.
                    </p>
                  </>
                ) : !error && <p>Preparing your key…</p>}
              </div>
            )}

            {kind === 'verify' && (
              <p style={{ fontSize: '0.86rem', marginTop: 0 }}>
                {useRecovery
                  ? 'Enter one of your recovery codes.'
                  : 'Enter the 6-digit code from your authenticator app.'}
              </p>
            )}

            <input
              style={input}
              value={code}
              onChange={e => setCode(e.target.value)}
              inputMode={useRecovery ? 'text' : 'numeric'}
              autoComplete="one-time-code"
              placeholder={useRecovery ? 'xxxxx-xxxxx' : '123456'}
              maxLength={useRecovery ? 16 : 6}
              autoFocus
              required
              aria-label={useRecovery ? 'Recovery code' : 'Authentication code'}
            />

            {error && (
              <div role="alert" style={{ color: 'var(--status-error, #c73e3e)', fontSize: '0.82rem', marginTop: 10 }}>
                {error}
              </div>
            )}

            <button style={primary} type="submit" disabled={busy || (kind === 'enroll' && !setup)}>
              {busy ? 'Checking…' : kind === 'verify' ? 'Verify' : 'Turn on two-factor'}
            </button>

            <div style={{ display: 'flex', justifyContent: 'space-between' }}>
              {kind === 'verify' ? (
                <button type="button" style={link} onClick={() => { setUseRecovery(v => !v); setCode(''); setError('') }}>
                  {useRecovery ? 'Use authenticator code' : 'Use a recovery code'}
                </button>
              ) : <span />}
              <button type="button" style={link} onClick={onCancel}>Cancel</button>
            </div>
          </form>
        )}
      </div>
    </div>
  )
}
