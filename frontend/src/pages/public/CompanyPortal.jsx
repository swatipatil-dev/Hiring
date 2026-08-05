import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'

const API = '/api/v1'

export default function CompanyPortal() {
  const params  = new URLSearchParams(window.location.search)
  const apiKey  = params.get('key') || ''
  const jobId   = params.get('job_id') || ''
  const navigate = useNavigate()

  const [company, setCompany] = useState(null)
  const [tab, setTab]         = useState('login')   // 'login' | 'register'
  const [step, setStep]       = useState('form')    // 'form' | 'otp'
  const [loading, setLoading] = useState(true)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError]     = useState('')
  const [success, setSuccess] = useState('')

  // Login fields
  const [email, setEmail]     = useState('')
  const [password, setPassword] = useState('')

  // Register fields
  const [reg, setReg] = useState({
    first_name: '', last_name: '', email: '', password: '',
    confirm: '', phone: '', role: 'candidate'
  })
  const [otp, setOtp] = useState('')

  const accent = company?.primary_color || '#2b7cba'

  useEffect(() => {
    if (!apiKey) { setLoading(false); return }
    fetch(`${API}/public/company-info?key=${encodeURIComponent(apiKey)}`)
      .then(r => r.ok ? r.json() : Promise.reject())
      .then(d => setCompany(d))
      .catch(() => setError('Invalid company link.'))
      .finally(() => setLoading(false))
  }, [apiKey])

  const saveSession = (data) => {
    localStorage.setItem('hireflow_token', data.access_token)
    localStorage.setItem('hireflow_user', JSON.stringify(data.user))
    if (apiKey) localStorage.setItem('hireflow_company_key', apiKey)
    navigate('/candidate')
  }

  const handleLogin = async (e) => {
    e.preventDefault()
    setError(''); setSubmitting(true)
    try {
      const r = await fetch(`${API}/auth/login`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, password, api_key: apiKey || undefined })
      })
      const data = await r.json()
      if (!r.ok) throw new Error(data.detail || 'Login failed')
      saveSession(data)
    } catch (err) {
      setError(err.message)
    }
    setSubmitting(false)
  }

  const handleRegister = async (e) => {
    e.preventDefault()
    setError('')
    if (reg.password !== reg.confirm) { setError('Passwords do not match'); return }
    setSubmitting(true)
    try {
      const r = await fetch(`${API}/auth/register`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          email: reg.email, password: reg.password,
          first_name: reg.first_name, last_name: reg.last_name,
          phone: reg.phone, role: 'candidate',
          api_key: apiKey || undefined,
        })
      })
      const data = await r.json()
      if (!r.ok) throw new Error(data.detail || 'Registration failed')
      setSuccess('OTP sent to your email. Please verify.')
      setStep('otp')
    } catch (err) {
      setError(err.message)
    }
    setSubmitting(false)
  }

  const handleOtp = async (e) => {
    e.preventDefault()
    setError(''); setSubmitting(true)
    try {
      const r = await fetch(`${API}/auth/verify-otp`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: reg.email, otp, api_key: apiKey || undefined })
      })
      const data = await r.json()
      if (!r.ok) throw new Error(data.detail || 'OTP verification failed')
      saveSession(data)
    } catch (err) {
      setError(err.message)
    }
    setSubmitting(false)
  }

  if (loading) return (
    <div style={S.page}>
      <div style={{ textAlign: 'center', color: '#9ca3af', marginTop: 120 }}>Loading…</div>
    </div>
  )

  return (
    <div style={S.page}>
      {/* Header */}
      <header style={{ ...S.header, borderBottom: `2px solid ${accent}25` }}>
        <div style={S.brand}>
          {company?.logo_url
            ? <img src={company.logo_url} alt={company.name} style={S.logo} onError={e => e.target.style.display='none'} />
            : <div style={{ ...S.logoFallback, background: accent }}>{(company?.name || 'C')[0]}</div>
          }
          <div>
            <div style={S.companyName}>{company?.name || 'Company Portal'}</div>
            <div style={S.companySub}>Candidate Portal</div>
          </div>
        </div>
        <a href={`/jobs?key=${apiKey}`} style={{ ...S.backLink, color: accent }}>← View Jobs</a>
      </header>

      {/* Card */}
      <div style={S.cardWrap}>
        <div style={S.card}>
          <div style={S.cardTitle}>
            {tab === 'login' ? 'Sign in to apply' : 'Create your account'}
          </div>
          {jobId && (
            <div style={{ ...S.jobBanner, background: accent + '12', border: `1px solid ${accent}30`, color: accent }}>
              You're applying for a position at <strong>{company?.name}</strong>
            </div>
          )}

          {/* Tabs */}
          <div style={S.tabs}>
            {['login', 'register'].map(t => (
              <button key={t} onClick={() => { setTab(t); setError(''); setSuccess(''); setStep('form') }}
                style={{ ...S.tabBtn, ...(tab === t ? { ...S.tabActive, borderBottomColor: accent, color: accent } : {}) }}>
                {t === 'login' ? 'Sign In' : 'Register'}
              </button>
            ))}
          </div>

          {error   && <div style={S.errorBox}>{error}</div>}
          {success && <div style={S.successBox}>{success}</div>}

          {/* ── LOGIN FORM ── */}
          {tab === 'login' && (
            <form onSubmit={handleLogin} style={S.form}>
              <label style={S.label}>Email</label>
              <input style={S.input} type="email" required placeholder="you@example.com"
                value={email} onChange={e => setEmail(e.target.value)}
                onFocus={el => el.target.style.borderColor = accent}
                onBlur={el => el.target.style.borderColor = '#e5e7eb'} />

              <label style={S.label}>Password</label>
              <input style={S.input} type="password" required placeholder="••••••••"
                value={password} onChange={e => setPassword(e.target.value)}
                onFocus={el => el.target.style.borderColor = accent}
                onBlur={el => el.target.style.borderColor = '#e5e7eb'} />

              <button type="submit" disabled={submitting}
                style={{ ...S.submit, background: accent, opacity: submitting ? 0.7 : 1 }}>
                {submitting ? 'Signing in…' : 'Sign In'}
              </button>

              <div style={S.switchRow}>
                Don't have an account?{' '}
                <button type="button" onClick={() => { setTab('register'); setError('') }}
                  style={{ ...S.switchLink, color: accent }}>Register</button>
              </div>
            </form>
          )}

          {/* ── REGISTER FORM ── */}
          {tab === 'register' && step === 'form' && (
            <form onSubmit={handleRegister} style={S.form}>
              <div style={S.row2}>
                <div>
                  <label style={S.label}>First Name</label>
                  <input style={S.input} required placeholder="John"
                    value={reg.first_name} onChange={e => setReg({ ...reg, first_name: e.target.value })}
                    onFocus={el => el.target.style.borderColor = accent}
                    onBlur={el => el.target.style.borderColor = '#e5e7eb'} />
                </div>
                <div>
                  <label style={S.label}>Last Name</label>
                  <input style={S.input} required placeholder="Doe"
                    value={reg.last_name} onChange={e => setReg({ ...reg, last_name: e.target.value })}
                    onFocus={el => el.target.style.borderColor = accent}
                    onBlur={el => el.target.style.borderColor = '#e5e7eb'} />
                </div>
              </div>

              <label style={S.label}>Email</label>
              <input style={S.input} type="email" required placeholder="you@example.com"
                value={reg.email} onChange={e => setReg({ ...reg, email: e.target.value })}
                onFocus={el => el.target.style.borderColor = accent}
                onBlur={el => el.target.style.borderColor = '#e5e7eb'} />

              <label style={S.label}>Phone (optional)</label>
              <input style={S.input} type="tel" placeholder="+91 9999999999"
                value={reg.phone} onChange={e => setReg({ ...reg, phone: e.target.value })}
                onFocus={el => el.target.style.borderColor = accent}
                onBlur={el => el.target.style.borderColor = '#e5e7eb'} />

              <div style={S.row2}>
                <div>
                  <label style={S.label}>Password</label>
                  <input style={S.input} type="password" required placeholder="••••••••"
                    value={reg.password} onChange={e => setReg({ ...reg, password: e.target.value })}
                    onFocus={el => el.target.style.borderColor = accent}
                    onBlur={el => el.target.style.borderColor = '#e5e7eb'} />
                </div>
                <div>
                  <label style={S.label}>Confirm Password</label>
                  <input style={S.input} type="password" required placeholder="••••••••"
                    value={reg.confirm} onChange={e => setReg({ ...reg, confirm: e.target.value })}
                    onFocus={el => el.target.style.borderColor = accent}
                    onBlur={el => el.target.style.borderColor = '#e5e7eb'} />
                </div>
              </div>

              <button type="submit" disabled={submitting}
                style={{ ...S.submit, background: accent, opacity: submitting ? 0.7 : 1 }}>
                {submitting ? 'Creating account…' : 'Create Account'}
              </button>

              <div style={S.switchRow}>
                Already have an account?{' '}
                <button type="button" onClick={() => { setTab('login'); setError('') }}
                  style={{ ...S.switchLink, color: accent }}>Sign In</button>
              </div>
            </form>
          )}

          {/* ── OTP STEP ── */}
          {tab === 'register' && step === 'otp' && (
            <form onSubmit={handleOtp} style={S.form}>
              <div style={{ textAlign: 'center', marginBottom: 20 }}>
                <div style={{ fontSize: '2.5rem', marginBottom: 8 }}>📧</div>
                <div style={{ fontSize: '0.9rem', color: '#6b7280' }}>
                  Enter the 6-digit code sent to <strong>{reg.email}</strong>
                </div>
              </div>
              <input style={{ ...S.input, textAlign: 'center', fontSize: '1.5rem', letterSpacing: '0.3em', fontWeight: 700 }}
                required maxLength={6} placeholder="000000"
                value={otp} onChange={e => setOtp(e.target.value.replace(/\D/g, '').slice(0, 6))}
                onFocus={el => el.target.style.borderColor = accent}
                onBlur={el => el.target.style.borderColor = '#e5e7eb'} />
              <button type="submit" disabled={submitting || otp.length !== 6}
                style={{ ...S.submit, background: accent, opacity: (submitting || otp.length !== 6) ? 0.7 : 1 }}>
                {submitting ? 'Verifying…' : 'Verify & Continue'}
              </button>
            </form>
          )}
        </div>
      </div>

      <footer style={S.footer}>
        Powered by <span style={{ color: accent, fontWeight: 700 }}>HireFlow</span>
      </footer>
    </div>
  )
}

const S = {
  page:        { minHeight: '100vh', background: '#f8fafc', fontFamily: "'Segoe UI', system-ui, sans-serif", color: '#1a1d23', display: 'flex', flexDirection: 'column' },
  header:      { background: '#fff', padding: '16px 32px', display: 'flex', alignItems: 'center', justifyContent: 'space-between', boxShadow: '0 2px 8px rgba(0,0,0,0.04)' },
  brand:       { display: 'flex', alignItems: 'center', gap: 14 },
  logo:        { height: 44, width: 'auto', objectFit: 'contain' },
  logoFallback:{ width: 44, height: 44, borderRadius: 10, display: 'grid', placeItems: 'center', fontSize: '1.2rem', fontWeight: 800, color: '#fff' },
  companyName: { fontSize: '1.05rem', fontWeight: 700 },
  companySub:  { fontSize: '0.72rem', color: '#9ca3af', marginTop: 1 },
  backLink:    { fontSize: '0.82rem', fontWeight: 600, textDecoration: 'none' },
  cardWrap:    { flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '32px 16px' },
  card:        { background: '#fff', borderRadius: 18, padding: '36px 40px', width: '100%', maxWidth: 480, boxShadow: '0 4px 32px rgba(0,0,0,0.08)', border: '1px solid #f0f0f0' },
  cardTitle:   { fontSize: '1.25rem', fontWeight: 800, marginBottom: 16, color: '#111827' },
  jobBanner:   { padding: '10px 16px', borderRadius: 10, fontSize: '0.82rem', marginBottom: 18 },
  tabs:        { display: 'flex', borderBottom: '2px solid #f0f0f0', marginBottom: 24 },
  tabBtn:      { flex: 1, padding: '10px', background: 'none', border: 'none', borderBottom: '2px solid transparent', marginBottom: -2, fontSize: '0.88rem', fontWeight: 600, color: '#9ca3af', cursor: 'pointer', transition: 'all 0.15s' },
  tabActive:   { color: '#111', borderBottom: '2px solid' },
  form:        { display: 'flex', flexDirection: 'column', gap: 14 },
  label:       { fontSize: '0.78rem', fontWeight: 700, color: '#374151', marginBottom: -8 },
  input:       { padding: '10px 14px', borderRadius: 10, border: '1.5px solid #e5e7eb', fontSize: '0.9rem', outline: 'none', transition: 'border-color 0.2s', width: '100%', boxSizing: 'border-box' },
  row2:        { display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 },
  submit:      { padding: '12px', borderRadius: 12, border: 'none', color: '#fff', fontSize: '0.95rem', fontWeight: 700, cursor: 'pointer', marginTop: 4, transition: 'all 0.2s' },
  switchRow:   { textAlign: 'center', fontSize: '0.82rem', color: '#6b7280' },
  switchLink:  { background: 'none', border: 'none', fontWeight: 700, cursor: 'pointer', fontSize: '0.82rem', padding: 0 },
  errorBox:    { padding: '10px 14px', borderRadius: 10, background: '#fef2f2', border: '1px solid #fecaca', color: '#dc2626', fontSize: '0.82rem' },
  successBox:  { padding: '10px 14px', borderRadius: 10, background: '#f0fdf4', border: '1px solid #bbf7d0', color: '#15803d', fontSize: '0.82rem' },
  footer:      { textAlign: 'center', padding: '16px', fontSize: '0.72rem', color: '#c4c9d4', borderTop: '1px solid #f0f0f0' },
}
