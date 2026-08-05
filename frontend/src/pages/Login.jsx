import { useState, useRef } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'
import { Eye, EyeOff } from 'lucide-react'

const EMAIL_NOT_VERIFIED = 'EMAIL_NOT_VERIFIED'
const ROLE_ROUTES = { superadmin: '/superadmin', admin: '/admin', hr: '/hr', interviewer: '/interviewer', candidate: '/candidate' }

export default function Login() {
  const { login, verifyOtp, resendOtp } = useAuth()
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()

  const clientEmail = searchParams.get('email') || ''
  const clientLogo = searchParams.get('logo') || '/logo.png?v=3'
  const clientColor = searchParams.get('color') || '#0e4fa0'
  const clientName = searchParams.get('name') || ''

  const [email, setEmail] = useState(clientEmail)
  const [password, setPassword] = useState('')
  const [showPw, setShowPw] = useState(false)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  // OTP verification step (shown when login returns EMAIL_NOT_VERIFIED)
  const [otpStep, setOtpStep] = useState(false)
  const [pendingEmail, setPendingEmail] = useState('')
  const [otp, setOtp] = useState(['', '', '', '', '', ''])
  const [otpError, setOtpError] = useState('')
  const [otpLoading, setOtpLoading] = useState(false)
  const [resendLoading, setResendLoading] = useState(false)
  const [resendMsg, setResendMsg] = useState('')
  const otpRefs = useRef([])

  const extractErrorDetail = (err, fallback = 'An error occurred') => {
    const detail = err?.response?.data?.detail
    if (typeof detail === 'string') return detail
    if (Array.isArray(detail)) {
      return detail.map(item => (typeof item === 'string' ? item : item.msg || JSON.stringify(item))).join('; ')
    }
    if (detail && typeof detail === 'object') {
      return detail.msg || JSON.stringify(detail)
    }
    return err?.message || fallback
  }

  const handleSubmit = async (e) => {
    e.preventDefault()
    setError('')
    setLoading(true)
    try {
      const user = await login(email, password, { platformLogin: true })
      navigate(ROLE_ROUTES[user.role?.toLowerCase()] || '/candidate')
    } catch (err) {
      const rawDetail = err.response?.data?.detail
      if (rawDetail === EMAIL_NOT_VERIFIED) {
        setPendingEmail(email)
        setOtp(['', '', '', '', '', ''])
        setOtpError('')
        setResendMsg('')
        setOtpStep(true)
      } else {
        setError(extractErrorDetail(err, 'Invalid credentials'))
      }
    } finally {
      setLoading(false)
    }
  }

  const handleOtpChange = (idx, val) => {
    const digit = val.replace(/\D/g, '').slice(-1)
    const next = [...otp]; next[idx] = digit; setOtp(next)
    if (digit && idx < 5) otpRefs.current[idx + 1]?.focus()
  }

  const handleOtpKeyDown = (idx, e) => {
    if (e.key === 'Backspace' && !otp[idx] && idx > 0) otpRefs.current[idx - 1]?.focus()
  }

  const handleOtpPaste = (e) => {
    const pasted = e.clipboardData.getData('text').replace(/\D/g, '').slice(0, 6)
    if (pasted.length === 6) { setOtp(pasted.split('')); otpRefs.current[5]?.focus() }
  }

  const handleVerifyOtp = async (e) => {
    e.preventDefault()
    const code = otp.join('')
    if (code.length < 6) { setOtpError('Enter the 6-digit OTP'); return }
    setOtpError('')
    setOtpLoading(true)
    try {
      const user = await verifyOtp(pendingEmail, code)
      navigate(ROLE_ROUTES[user.role?.toLowerCase()] || '/candidate')
    } catch (err) {
      setOtpError(err.response?.data?.detail || 'Invalid OTP')
      setOtp(['', '', '', '', '', ''])
      otpRefs.current[0]?.focus()
    } finally {
      setOtpLoading(false)
    }
  }

  const handleResend = async () => {
    setResendMsg(''); setOtpError('')
    setResendLoading(true)
    try {
      await resendOtp(pendingEmail)
      setResendMsg('New OTP sent to your email.')
      setOtp(['', '', '', '', '', ''])
      otpRefs.current[0]?.focus()
    } catch (err) {
      setResendMsg(err.response?.data?.detail || 'Failed to resend OTP')
    } finally {
      setResendLoading(false)
    }
  }

  const backToLogin = () => {
    setOtpStep(false)
    setOtpError('')
    setResendMsg('')
    setOtp(['', '', '', '', '', ''])
  }

  return (
    <div className="login-page" style={{ '--brand': clientColor }}>
      {/* Back to the Innomax hero / landing page */}
      <Link to="/" className="login-home-link">← Back to home</Link>

      {/* Logo (also links home) */}
      <div className="login-logo-wrap">
        <Link to="/" aria-label="Back to home">
          <img
            src={clientLogo}
            alt={clientName || 'HireFlow'}
            className="login-logo"
            onError={e => { e.target.src = '/logo.png?v=3' }}
          />
        </Link>
      </div>

      <h1 className="login-title">
        {clientName ? `Welcome to ${clientName}` : 'Welcome to HireFlow'}
      </h1>
      <p className="login-tagline">Where talent meets opportunity</p>

      <div className="login-card">
        {otpStep ? (
          /* ── OTP verification step ── */
          <>
            <button onClick={backToLogin} className="login-back-btn">← Back to login</button>
            <h2 className="login-card-title" style={{ marginTop: 12 }}>Verify your email</h2>
            <p style={{ fontSize: '0.88rem', color: '#78716c', marginBottom: 24, fontFamily: "'DM Sans', sans-serif", lineHeight: 1.5 }}>
              We sent a 6-digit code to <b style={{ color: '#1c1917' }}>{pendingEmail}</b>. Valid for 10 minutes.
            </p>

            {otpError && <div className="login-error">{otpError}</div>}
            {resendMsg && (
              <div className="login-error" style={{ background: '#f0fdf4', border: '1px solid #bbf7d0', color: '#16a34a' }}>
                {resendMsg}
              </div>
            )}

            <form onSubmit={handleVerifyOtp}>
              <div style={{ display: 'flex', gap: 10, justifyContent: 'center', marginBottom: 28 }} onPaste={handleOtpPaste}>
                {otp.map((d, i) => (
                  <input
                    key={i}
                    ref={el => otpRefs.current[i] = el}
                    type="text"
                    inputMode="numeric"
                    maxLength={1}
                    value={d}
                    onChange={e => handleOtpChange(i, e.target.value)}
                    onKeyDown={e => handleOtpKeyDown(i, e)}
                    autoFocus={i === 0}
                    className="login-otp-box"
                  />
                ))}
              </div>
              <button className="login-btn" type="submit" disabled={otpLoading} style={{ background: clientColor }}>
                {otpLoading ? 'Verifying…' : 'Verify & Login'}
              </button>
            </form>

            <div style={{ marginTop: 20, textAlign: 'center', fontSize: '0.85rem', color: '#78716c', fontFamily: "'DM Sans', sans-serif" }}>
              Didn't receive it?{' '}
              <button
                onClick={handleResend}
                disabled={resendLoading}
                style={{ background: 'none', border: 'none', color: clientColor, cursor: 'pointer', fontWeight: 600, fontSize: '0.85rem', fontFamily: 'inherit' }}
              >
                {resendLoading ? 'Sending…' : 'Resend OTP'}
              </button>
            </div>
          </>
        ) : (
          /* ── Login form ── */
          <>
            <h2 className="login-card-title">Log in</h2>

            {error && <div className="login-error">{error}</div>}

            <form onSubmit={handleSubmit}>
              <div className="login-field">
                <label>Email</label>
                <input
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  required
                  autoComplete="email"
                />
              </div>

              <div className="login-field">
                <label>Password</label>
                <div className="login-input-wrap">
                  <input
                    type={showPw ? 'text' : 'password'}
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    required
                    autoComplete="current-password"
                  />
                  <button type="button" className="login-eye" onClick={() => setShowPw(!showPw)} tabIndex={-1}>
                    {showPw ? <EyeOff size={18} /> : <Eye size={18} />}
                  </button>
                </div>
              </div>

              <div style={{ textAlign: 'right', marginTop: -8 }}>
                <Link to="/forgot-password" style={{ fontSize: '0.82rem', color: clientColor, textDecoration: 'none', fontWeight: 500 }}>
                  Forgot password?
                </Link>
              </div>

              <button className="login-btn" type="submit" disabled={loading} style={{ background: clientColor }}>
                {loading ? 'Logging in...' : 'Login'}
              </button>
            </form>

            <div className="login-footer">
              <span>Don't have an account?</span>
              <Link to="/register" style={{ color: clientColor }}>Register here</Link>
            </div>
          </>
        )}
      </div>

      <style>{`
        .login-page {
          position: relative;
          min-height: 100vh;
          display: flex;
          flex-direction: column;
          align-items: center;
          justify-content: center;
          padding: 40px 20px;
          background: #1a1f2e;
          background-image:
            radial-gradient(ellipse at 30% 0%, rgba(78, 205, 181, 0.06) 0%, transparent 60%),
            radial-gradient(ellipse at 70% 100%, rgba(193, 127, 62, 0.04) 0%, transparent 60%);
        }

        .login-home-link {
          position: absolute;
          top: 24px;
          left: 28px;
          color: #6b7589;
          text-decoration: none;
          font-size: 0.85rem;
          font-weight: 600;
          font-family: 'DM Sans', sans-serif;
          transition: color 0.15s;
        }
        .login-home-link:hover { color: #e8e4de; }

        .login-logo-wrap { margin-bottom: 28px; }
        .login-logo-wrap a { display: inline-block; cursor: pointer; }

        .login-logo {
          height: 90px;
          width: auto;
          max-width: 240px;
          object-fit: contain;
          filter: brightness(1.1);
        }

        .login-title {
          font-size: 1.8rem;
          font-weight: 700;
          color: #e8e4de;
          text-align: center;
          margin: 0 0 8px 0;
          font-family: 'Fraunces', Georgia, serif;
          letter-spacing: -0.01em;
        }

        .login-tagline {
          font-size: 0.95rem;
          color: #6b7589;
          text-align: center;
          margin: 0 0 32px 0;
          font-family: 'DM Sans', sans-serif;
          font-style: italic;
        }

        .login-card {
          width: 100%;
          max-width: 440px;
          background: #fff;
          border-radius: 20px;
          padding: 36px 40px;
          box-shadow: 0 24px 80px rgba(0,0,0,0.25);
        }

        .login-card-title {
          font-size: 1.4rem;
          font-weight: 700;
          color: #1c1917;
          margin: 0 0 24px 0;
          font-family: 'Fraunces', Georgia, serif;
        }

        .login-error {
          padding: 10px 14px;
          background: #fef2f0;
          border: 1px solid #f5c6c0;
          border-radius: 10px;
          color: #c44536;
          font-size: 0.85rem;
          font-weight: 500;
          margin-bottom: 20px;
        }

        .login-field { margin-bottom: 20px; }

        .login-field label {
          display: block;
          font-size: 0.78rem;
          font-weight: 600;
          color: #57534e;
          margin-bottom: 6px;
          font-family: 'DM Sans', sans-serif;
          text-transform: uppercase;
          letter-spacing: 0.06em;
        }

        .login-field input {
          width: 100%;
          padding: 12px 14px;
          background: #faf9f7;
          border: 1.5px solid #e7e5e0;
          border-radius: 10px;
          color: #1c1917;
          font-size: 0.95rem;
          font-family: 'DM Sans', sans-serif;
          outline: none;
          transition: border-color 0.2s, box-shadow 0.2s, background 0.2s;
          box-sizing: border-box;
        }

        .login-field input:focus {
          border-color: var(--brand);
          box-shadow: 0 0 0 3px rgba(0,0,0,0.06);
          background: #fff;
        }

        .login-input-wrap { position: relative; }
        .login-input-wrap input { padding-right: 44px; }

        .login-eye {
          position: absolute;
          right: 8px;
          top: 50%;
          transform: translateY(-50%);
          background: none;
          border: none;
          color: #a8a29e;
          cursor: pointer;
          padding: 4px;
          display: grid;
          place-items: center;
          border-radius: 8px;
          transition: color 0.15s;
        }
        .login-eye:hover { color: var(--brand); }

        .login-btn {
          width: 100%;
          padding: 12px 24px;
          color: #fff;
          border: none;
          border-radius: 10px;
          font-size: 0.95rem;
          font-weight: 600;
          font-family: 'DM Sans', sans-serif;
          cursor: pointer;
          transition: all 0.25s cubic-bezier(0.16, 1, 0.3, 1);
          margin-top: 8px;
          letter-spacing: 0.01em;
        }

        .login-btn:hover:not(:disabled) {
          filter: brightness(0.9);
          box-shadow: 0 6px 20px rgba(0,0,0,0.2);
          transform: translateY(-1px);
        }

        .login-btn:disabled { opacity: 0.5; cursor: not-allowed; }

        .login-footer {
          display: flex;
          align-items: center;
          gap: 6px;
          margin-top: 24px;
          font-size: 0.88rem;
          color: #78716c;
          font-family: 'DM Sans', sans-serif;
        }

        .login-footer a {
          text-decoration: none;
          font-weight: 600;
        }
        .login-footer a:hover { text-decoration: underline; }

        .login-back-btn {
          background: none;
          border: none;
          color: #a8a29e;
          cursor: pointer;
          font-size: 0.82rem;
          font-family: 'DM Sans', sans-serif;
          padding: 0;
          transition: color 0.15s;
        }
        .login-back-btn:hover { color: #57534e; }

        .login-otp-box {
          width: 48px;
          height: 56px;
          text-align: center;
          font-size: 1.4rem;
          font-weight: 700;
          border: 1.5px solid #e7e5e0;
          border-radius: 10px;
          background: #faf9f7;
          color: #1c1917;
          outline: none;
          font-family: 'DM Mono', monospace;
          transition: border-color 0.2s, box-shadow 0.2s;
        }
        .login-otp-box:focus {
          border-color: var(--brand);
          box-shadow: 0 0 0 3px rgba(0,0,0,0.06);
          background: #fff;
        }

        @media (max-width: 600px) {
          .login-title { font-size: 1.4rem; }
          .login-card { padding: 28px 24px; }
          .login-otp-box { width: 40px; height: 48px; font-size: 1.2rem; }
        }
      `}</style>
    </div>
  )
}
