/**
 * Company-branded login page.
 * URL: /<company-slug>   (e.g. /innomax-it-solutions)
 *
 * The slug is the company's public identifier. It replaced /app?key=<sk_live_…>, which
 * put a live secret in a URL that gets printed on job ads, forwarded, bookmarked and
 * logged. Old links still work: the key is exchanged for a slug on mount and scrubbed
 * from the address bar.
 *
 * Loads company branding via the public API, then shows a full login/register
 * experience styled with the company's logo and primary color.
 * All roles (candidate, hr, interviewer, admin) can authenticate here.
 * After login, users are routed to their role's dashboard.
 */
import { useEffect, useState, useRef } from 'react'
import { useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { Eye, EyeOff, Briefcase, Users, ChevronRight } from 'lucide-react'
import { useAuth } from '../../context/AuthContext'
import { clearSlug, exchangeLegacyKey, getStoredSlug, storeSlug } from '../../utils/tenant'

const API = '/api/v1'
const ALLOWED_ROLES = ['admin', 'hr', 'interviewer', 'candidate']
const ROLE_ROUTES = {
  admin: '/admin', hr: '/hr', interviewer: '/interviewer', candidate: '/candidate',
}

export default function CompanyLogin() {
  const [searchParams] = useSearchParams()
  const params   = useParams()
  const navigate = useNavigate()
  const { completeLogin, logout } = useAuth()

  const [slug,     setSlug]     = useState(params.companySlug || searchParams.get('c') || '')
  const [company,  setCompany]  = useState(null)
  const [loading,  setLoading]  = useState(true)
  const [notFound, setNotFound] = useState(false)

  // view: 'login' | 'register'
  const [view,     setView]     = useState('login')
  // register step: 'form' | 'otp'
  const [regStep,  setRegStep]  = useState('form')

  // Login state
  const [email,    setEmail]    = useState('')
  const [password, setPassword] = useState('')
  const [showPw,   setShowPw]   = useState(false)
  const [loginErr, setLoginErr] = useState('')
  const [loginBusy,setLoginBusy]= useState(false)

  // Register state
  const [reg, setReg] = useState({
    first_name: '', last_name: '', email: '',
    password: '', confirm: '', phone: '',
  })
  const [showRegPw, setShowRegPw] = useState(false)
  const [regErr,    setRegErr]    = useState('')
  const [regBusy,   setRegBusy]  = useState(false)

  // OTP boxes (shared between register-verify and login-unverified flows)
  const [otp,   setOtp]   = useState(['','','','','',''])
  const otpRefs = [useRef(),useRef(),useRef(),useRef(),useRef(),useRef()]
  const [otpErr, setOtpErr] = useState('')
  const [otpBusy,setOtpBusy]= useState(false)
  // verifyMode: '' (idle) | 'register' (just signed up) | 'login' (existing user, unverified)
  const [verifyMode,  setVerifyMode]  = useState('')
  const [pendingEmail,setPendingEmail]= useState('')
  const [resendBusy,  setResendBusy]  = useState(false)
  const [resendMsg,   setResendMsg]   = useState('')

  const accent = company?.primary_color || '#2b7cba'

  // ── Resolve the company, then load its branding ───────────────────────────
  useEffect(() => {
    let cancelled = false
    // Whether the visitor actually named a company. A bare /app with nothing at all is
    // still a valid entry point and falls back to the unbranded form, as it always has;
    // naming a company that does not resolve is an error worth showing.
    const named = Boolean(params.companySlug || searchParams.get('c') || searchParams.get('key'))

    ;(async () => {
      let active = params.companySlug || searchParams.get('c') || ''
      if (!active && searchParams.get('key')) {
        // A legacy link. Trade the secret for the slug once, then rewrite the address
        // bar so it stops travelling in Referer headers and bookmarks.
        active = (await exchangeLegacyKey(searchParams, sl => `/${sl}`)) || ''
      }
      if (!active) active = getStoredSlug() || ''
      if (cancelled) return
      setSlug(active)

      if (!active) {
        setNotFound(named)
        setLoading(false)
        return
      }
      try {
        const r = await fetch(`${API}/public/company-info?c=${encodeURIComponent(active)}`)
        if (!r.ok) throw new Error('unresolved')
        const d = await r.json()
        if (!cancelled) setCompany(d)
      } catch {
        if (!cancelled) setNotFound(true)
      } finally {
        if (!cancelled) setLoading(false)
      }
    })()

    return () => { cancelled = true }
  }, [params.companySlug, searchParams])

  // ── Helpers ────────────────────────────────────────────────────────────────
  // Throws (with a message for the caller's error line) if the user cancels the
  // two-factor step or the account is not a company role.
  const saveAndGo = async (data) => {
    // The role is only known up front when no two-factor step is pending.
    const early = (data.user?.role || '').toLowerCase()
    if (data.user && !ALLOWED_ROLES.includes(early)) {
      throw new Error('Access denied. This portal is for company users only.')
    }
    const u = await completeLogin(data)
    const role = (u?.role || '').toLowerCase()
    if (!ALLOWED_ROLES.includes(role)) {
      logout()
      throw new Error('Access denied. This portal is for company users only.')
    }
    storeSlug(slug)
    navigate(ROLE_ROUTES[role] || '/candidate')
  }

  const otpInput = (i, val) => {
    const next = [...otp]; next[i] = val.slice(-1)
    setOtp(next)
    if (val && i < 5) otpRefs[i+1].current?.focus()
  }
  const otpKeyDown = (i, e) => {
    if (e.key === 'Backspace' && !otp[i] && i > 0) otpRefs[i-1].current?.focus()
  }
  const otpValue = otp.join('')

  // ── Login ──────────────────────────────────────────────────────────────────
  const handleLogin = async (e) => {
    e.preventDefault(); setLoginErr(''); setLoginBusy(true)
    try {
      const r = await fetch(`${API}/auth/login`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, password, company_slug: slug || undefined }),
      })
      const data = await r.json()
      if (!r.ok) {
        // Backend signals an unverified email — switch to the OTP step and
        // auto-resend so the user gets a fresh code right away.
        if (r.status === 403 && data.detail === 'EMAIL_NOT_VERIFIED') {
          setPendingEmail(email)
          setVerifyMode('login')
          setOtp(['','','','','',''])
          setOtpErr(''); setResendMsg('')
          sendOtp(email, { silent: false })
          return
        }
        throw new Error(data.detail || 'Login failed')
      }
      await saveAndGo(data)
    } catch (err) { setLoginErr(err.message) }
    setLoginBusy(false)
  }

  // ── OTP resend (works for both register-verify and login-unverified) ──────
  const sendOtp = async (toEmail, { silent } = { silent: false }) => {
    if (!toEmail) return
    setResendBusy(true); setResendMsg('')
    try {
      const r = await fetch(`${API}/auth/resend-otp`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: toEmail, company_slug: slug || undefined }),
      })
      const data = await r.json().catch(() => ({}))
      if (!r.ok) throw new Error(data.detail || 'Failed to send OTP')
      setResendMsg(silent ? '' : 'A fresh 6-digit code has been sent to your email.')
    } catch (err) {
      setResendMsg(err.message)
    } finally {
      setResendBusy(false)
    }
  }

  // ── Register ───────────────────────────────────────────────────────────────
  const handleRegister = async (e) => {
    e.preventDefault(); setRegErr('')
    if (reg.password !== reg.confirm) { setRegErr('Passwords do not match'); return }
    setRegBusy(true)
    try {
      const r = await fetch(`${API}/auth/register`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          email: reg.email, password: reg.password,
          first_name: reg.first_name, last_name: reg.last_name,
          phone: reg.phone || undefined, role: 'candidate',
          company_slug: slug || undefined,
        }),
      })
      const data = await r.json()
      if (!r.ok) throw new Error(data.detail || 'Registration failed')
      setPendingEmail(reg.email)
      setVerifyMode('register')
      setOtp(['','','','','',''])
      setOtpErr(''); setResendMsg('')
      setRegStep('otp')
    } catch (err) { setRegErr(err.message) }
    setRegBusy(false)
  }

  const handleOtp = async (e) => {
    e.preventDefault(); setOtpErr(''); setOtpBusy(true)
    try {
      const r = await fetch(`${API}/auth/verify-otp`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: pendingEmail, otp: otpValue, company_slug: slug || undefined }),
      })
      const data = await r.json()
      if (!r.ok) throw new Error(data.detail || 'Invalid OTP')
      await saveAndGo(data)
    } catch (err) { setOtpErr(err.message) }
    setOtpBusy(false)
  }

  // ── Render ─────────────────────────────────────────────────────────────────
  if (loading) return (
    <div style={pg(accent)}>
      <div style={{ margin: 'auto', color: '#9ca3af', fontSize: '0.9rem' }}>Loading…</div>
    </div>
  )

  // The visitor named a company that does not resolve — a renamed or deactivated slug, a
  // typo, or a rotated ?key= link. Say so, rather than showing an unbranded form that
  // would silently sign them in against no company at all.
  //
  // The button is not decoration. A slug in localStorage outlives the company it names,
  // and ProtectedRoute and the catch-all route both redirect by it — so without a way to
  // clear it, a visitor whose company was renamed can bounce back here from anything that
  // needs auth, with no way out but wiping site data.
  if (notFound) return (
    <div style={pg(accent)}>
      <div style={{ margin: 'auto', maxWidth: 420, padding: 32, textAlign: 'center' }}>
        <div style={{ fontSize: '1.15rem', fontWeight: 700, color: '#111827', marginBottom: 10 }}>
          Company not found
        </div>
        <p style={{ fontSize: '0.88rem', color: '#6b7280', lineHeight: 1.6, margin: 0 }}>
          We couldn&rsquo;t find a company at this address. The link may have changed, or the
          company may no longer be active. Please check with whoever shared it with you.
        </p>
        <button
          onClick={() => { clearSlug(); navigate('/login') }}
          style={{
            marginTop: 22, padding: '10px 20px', border: 'none', borderRadius: 10,
            background: accent, color: '#fff', fontSize: '0.85rem', fontWeight: 700,
            cursor: 'pointer',
          }}>
          Go to sign in
        </button>
      </div>
    </div>
  )

  return (
    <div style={pg(accent)}>
      {/* Left panel — branding */}
      <div style={leftPanel(accent)}>
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', justifyContent: 'center' }}>
          {company?.logo_url
            ? <img src={company.logo_url} alt={company.name}
                style={{ height: 64, objectFit: 'contain', marginBottom: 28 }}
                onError={e => e.target.style.display='none'} />
            : <div style={logoFB(accent)}>{(company?.name || 'C')[0]}</div>
          }
          <h1 style={{ fontSize: '2rem', fontWeight: 800, color: '#fff', lineHeight: 1.2, marginBottom: 16 }}>
            {company?.name || 'HireFlow'}
          </h1>
          <p style={{ color: 'rgba(255,255,255,0.75)', fontSize: '1rem', lineHeight: 1.7, maxWidth: 320 }}>
            {company?.tagline || 'Sign in to access your recruitment portal — manage jobs, interviews, and candidates in one place.'}
          </p>
          <div style={{ marginTop: 40, display: 'flex', flexDirection: 'column', gap: 16 }}>
            {[
              { icon: <Briefcase size={16} />, text: 'Job Postings & ATS Scoring' },
              { icon: <Users size={16} />,     text: 'Interview & Feedback Management' },
              { icon: <ChevronRight size={16}/>,text: 'Offer Letters & Onboarding' },
            ].map((f, i) => (
              <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 12, color: 'rgba(255,255,255,0.85)', fontSize: '0.88rem' }}>
                <div style={{ width: 30, height: 30, borderRadius: 8, background: 'rgba(255,255,255,0.15)', display: 'grid', placeItems: 'center' }}>
                  {f.icon}
                </div>
                {f.text}
              </div>
            ))}
          </div>
        </div>
        <div style={{ fontSize: '0.7rem', color: 'rgba(255,255,255,0.4)', paddingTop: 24, borderTop: '1px solid rgba(255,255,255,0.1)' }}>
          Powered by <span style={{ fontWeight: 700, color: 'rgba(255,255,255,0.7)' }}>HireFlow</span>
        </div>
      </div>

      {/* Right panel — form */}
      <div style={rightPanel}>
        <div style={card}>

          {/* Tab switcher — hidden while we're verifying an OTP */}
          {!verifyMode && (
            <div style={tabBar}>
              {['login','register'].map(t => (
                <button key={t}
                  onClick={() => { setView(t); setLoginErr(''); setRegErr(''); setRegStep('form'); setOtp(['','','','','','']) }}
                  style={tabBtn(t === view, accent)}>
                  {t === 'login' ? 'Sign In' : 'Register'}
                </button>
              ))}
            </div>
          )}

          {/* ── LOGIN ── */}
          {view === 'login' && !verifyMode && (
            <form onSubmit={handleLogin} style={formStyle}>
              <div style={formTitle}>Welcome back</div>
              <div style={formSub}>Sign in to {company?.name || 'your portal'}</div>

              {loginErr && <div style={errBox}>{loginErr}</div>}

              <Field label="Email address">
                <input style={inp(accent)} type="email" required autoFocus
                  placeholder="you@company.com"
                  value={email} onChange={e => setEmail(e.target.value)} />
              </Field>

              <Field label="Password">
                <div style={{ position: 'relative' }}>
                  <input style={{ ...inp(accent), paddingRight: 42 }}
                    type={showPw ? 'text' : 'password'} required
                    placeholder="••••••••"
                    value={password} onChange={e => setPassword(e.target.value)} />
                  <button type="button" onClick={() => setShowPw(p => !p)}
                    style={eyeBtn}>{showPw ? <EyeOff size={16}/> : <Eye size={16}/>}</button>
                </div>
              </Field>

              <button type="submit" disabled={loginBusy} style={submitBtn(accent, loginBusy)}>
                {loginBusy ? 'Signing in…' : 'Sign In →'}
              </button>

              <div style={switchTxt}>
                Don't have an account?{' '}
                <Anchor onClick={() => setView('register')} color={accent}>Create one</Anchor>
              </div>
            </form>
          )}

          {/* ── REGISTER form ── */}
          {view === 'register' && regStep === 'form' && (
            <form onSubmit={handleRegister} style={formStyle}>
              <div style={formTitle}>Create account</div>
              <div style={formSub}>Join {company?.name || 'the'} recruitment portal</div>

              {regErr && <div style={errBox}>{regErr}</div>}

              <div style={grid2}>
                <Field label="First Name">
                  <input style={inp(accent)} required placeholder="John"
                    value={reg.first_name} onChange={e => setReg({...reg, first_name: e.target.value})} />
                </Field>
                <Field label="Last Name">
                  <input style={inp(accent)} required placeholder="Doe"
                    value={reg.last_name} onChange={e => setReg({...reg, last_name: e.target.value})} />
                </Field>
              </div>

              <Field label="Email address">
                <input style={inp(accent)} type="email" required placeholder="you@example.com"
                  value={reg.email} onChange={e => setReg({...reg, email: e.target.value})} />
              </Field>

              <Field label="Phone (optional)">
                <input style={inp(accent)} type="tel" placeholder="+91 9000000000"
                  value={reg.phone} onChange={e => setReg({...reg, phone: e.target.value})} />
              </Field>

              <div style={grid2}>
                <Field label="Password">
                  <div style={{ position: 'relative' }}>
                    <input style={{ ...inp(accent), paddingRight: 42 }}
                      type={showRegPw ? 'text' : 'password'} required placeholder="••••••••"
                      value={reg.password} onChange={e => setReg({...reg, password: e.target.value})} />
                    <button type="button" onClick={() => setShowRegPw(p => !p)} style={eyeBtn}>
                      {showRegPw ? <EyeOff size={16}/> : <Eye size={16}/>}
                    </button>
                  </div>
                </Field>
                <Field label="Confirm Password">
                  <input style={inp(accent)} type="password" required placeholder="••••••••"
                    value={reg.confirm} onChange={e => setReg({...reg, confirm: e.target.value})} />
                </Field>
              </div>

              <button type="submit" disabled={regBusy} style={submitBtn(accent, regBusy)}>
                {regBusy ? 'Creating…' : 'Create Account →'}
              </button>

              <div style={switchTxt}>
                Already registered?{' '}
                <Anchor onClick={() => setView('login')} color={accent}>Sign in</Anchor>
              </div>
            </form>
          )}

          {/* ── OTP step (shared by register-verify and login-unverified) ── */}
          {verifyMode && (
            <form onSubmit={handleOtp} style={formStyle}>
              <div style={{ textAlign: 'center', marginBottom: 8 }}>
                <div style={{ fontSize: '3rem', marginBottom: 12 }}>📬</div>
                <div style={formTitle}>
                  {verifyMode === 'login' ? 'Verify your email' : 'Check your email'}
                </div>
                <div style={{ ...formSub, marginBottom: 0 }}>
                  {verifyMode === 'login'
                    ? <>Your email isn't verified yet. We just sent a 6-digit code to<br /><strong>{pendingEmail}</strong></>
                    : <>We sent a 6-digit code to<br /><strong>{pendingEmail}</strong></>
                  }
                </div>
              </div>

              {otpErr && <div style={errBox}>{otpErr}</div>}
              {resendMsg && (
                <div style={{ padding: '8px 12px', borderRadius: 8, background: '#ecfdf5', border: '1px solid #a7f3d0', color: '#047857', fontSize: '0.78rem' }}>
                  {resendMsg}
                </div>
              )}

              <div style={{ display: 'flex', gap: 8, justifyContent: 'center', margin: '8px 0' }}>
                {otp.map((d, i) => (
                  <input key={i} ref={otpRefs[i]}
                    style={{ width: 48, height: 56, borderRadius: 12, border: `2px solid ${d ? accent : '#e5e7eb'}`,
                      textAlign: 'center', fontSize: '1.4rem', fontWeight: 700, outline: 'none',
                      transition: 'border-color 0.15s', fontFamily: 'monospace' }}
                    maxLength={1} value={d}
                    onChange={e => otpInput(i, e.target.value)}
                    onKeyDown={e => otpKeyDown(i, e)} />
                ))}
              </div>

              <button type="submit" disabled={otpBusy || otpValue.length !== 6}
                style={submitBtn(accent, otpBusy || otpValue.length !== 6)}>
                {otpBusy ? 'Verifying…' : 'Verify & Continue →'}
              </button>

              <button
                type="button"
                onClick={() => sendOtp(pendingEmail)}
                disabled={resendBusy || !pendingEmail}
                style={{
                  marginTop: 4, padding: '10px', borderRadius: 10, border: `1px solid ${accent}`,
                  background: 'transparent', color: accent, fontSize: '0.85rem', fontWeight: 700,
                  cursor: resendBusy ? 'not-allowed' : 'pointer', opacity: resendBusy ? 0.6 : 1,
                  fontFamily: 'inherit',
                }}
              >
                {resendBusy ? 'Sending…' : 'Resend OTP'}
              </button>

              <div style={switchTxt}>
                <Anchor
                  onClick={() => {
                    if (verifyMode === 'register') setRegStep('form')
                    setVerifyMode('')
                    setPendingEmail('')
                    setOtp(['','','','','',''])
                    setOtpErr(''); setResendMsg('')
                  }}
                  color={accent}
                >
                  ← Back
                </Anchor>
              </div>
            </form>
          )}
        </div>

        {/* Jobs board link */}
        <div style={{ marginTop: 20, textAlign: 'center', fontSize: '0.78rem', color: '#9ca3af' }}>
          Looking for open positions?{' '}
          <a href={`/${encodeURIComponent(slug)}/jobs`}
            style={{ color: accent, fontWeight: 600, textDecoration: 'none' }}>
            View job board →
          </a>
        </div>
      </div>
    </div>
  )
}

// ── Small helpers ──────────────────────────────────────────────────────────────

function Field({ label, children }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
      <label style={{ fontSize: '0.75rem', fontWeight: 700, color: '#4b5563', textTransform: 'uppercase', letterSpacing: '0.04em' }}>{label}</label>
      {children}
    </div>
  )
}

function Anchor({ onClick, color, children }) {
  return (
    <button type="button" onClick={onClick}
      style={{ background: 'none', border: 'none', color, fontWeight: 700, cursor: 'pointer', fontSize: 'inherit', padding: 0 }}>
      {children}
    </button>
  )
}

// ── Style functions ────────────────────────────────────────────────────────────

const pg = (accent) => ({
  display: 'flex', minHeight: '100vh',
  fontFamily: "'Segoe UI', system-ui, -apple-system, sans-serif",
  background: '#f1f5f9',
})

const leftPanel = (accent) => ({
  width: 380, flexShrink: 0,
  background: `linear-gradient(160deg, ${accent} 0%, ${accent}cc 100%)`,
  padding: '48px 40px', display: 'flex', flexDirection: 'column',
  '@media (max-width: 768px)': { display: 'none' },
})

const rightPanel = {
  flex: 1, display: 'flex', flexDirection: 'column',
  alignItems: 'center', justifyContent: 'center', padding: '40px 24px',
}

const card = {
  background: '#fff', borderRadius: 20, padding: '40px 44px',
  width: '100%', maxWidth: 440,
  boxShadow: '0 8px 48px rgba(0,0,0,0.1)', border: '1px solid #f0f4f8',
}

const tabBar = {
  display: 'flex', borderRadius: 12, background: '#f1f5f9',
  padding: 4, marginBottom: 32, gap: 4,
}

const tabBtn = (active, accent) => ({
  flex: 1, padding: '9px', borderRadius: 9, border: 'none', cursor: 'pointer',
  fontSize: '0.88rem', fontWeight: 700, transition: 'all 0.2s',
  background: active ? '#fff' : 'transparent',
  color: active ? accent : '#9ca3af',
  boxShadow: active ? '0 2px 8px rgba(0,0,0,0.08)' : 'none',
})

const formStyle = { display: 'flex', flexDirection: 'column', gap: 18 }
const formTitle = { fontSize: '1.3rem', fontWeight: 800, color: '#111827' }
const formSub   = { fontSize: '0.84rem', color: '#6b7280', marginTop: -10 }
const grid2     = { display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }

const inp = (accent) => ({
  width: '100%', padding: '11px 14px', borderRadius: 10,
  border: '1.5px solid #e5e7eb', fontSize: '0.9rem', outline: 'none',
  transition: 'border-color 0.2s, box-shadow 0.2s', boxSizing: 'border-box',
  fontFamily: 'inherit',
  // Focus styles are applied via onFocus/onBlur since inline styles can't do :focus
})

const eyeBtn = {
  position: 'absolute', right: 12, top: '50%', transform: 'translateY(-50%)',
  background: 'none', border: 'none', cursor: 'pointer', color: '#9ca3af', padding: 2,
}

const submitBtn = (accent, disabled) => ({
  padding: '13px', borderRadius: 12, border: 'none',
  background: disabled ? '#e5e7eb' : accent,
  color: disabled ? '#9ca3af' : '#fff',
  fontSize: '0.95rem', fontWeight: 700, cursor: disabled ? 'not-allowed' : 'pointer',
  transition: 'all 0.2s', marginTop: 4,
})

const switchTxt = { textAlign: 'center', fontSize: '0.82rem', color: '#6b7280' }
const errBox    = { padding: '10px 14px', borderRadius: 10, background: '#fef2f2', border: '1px solid #fecaca', color: '#dc2626', fontSize: '0.82rem' }
const logoFB    = (accent) => ({
  width: 64, height: 64, borderRadius: 16, background: 'rgba(255,255,255,0.2)',
  display: 'grid', placeItems: 'center', fontSize: '1.8rem', fontWeight: 800,
  color: '#fff', marginBottom: 28,
})
