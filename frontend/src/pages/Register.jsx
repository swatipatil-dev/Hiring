import { useState, useRef, useEffect } from 'react'
import { Link, useNavigate, useLocation } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'
import { UserPlus, Eye, EyeOff, CheckCircle, ShieldCheck, RotateCcw } from 'lucide-react'

export default function Register() {
  const { register, verifyOtp, resendOtp } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const [form, setForm] = useState({ first_name: '', last_name: '', email: '', password: '', role: 'candidate', gender: '', date_of_birth: '' })
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const [showPw, setShowPw] = useState(false)

  // OTP step state — can also be triggered from Login's "Verify now" link
  const [otpStep, setOtpStep] = useState(false)
  const [pendingEmail, setPendingEmail] = useState('')

  useEffect(() => {
    const verifyEmail = location.state?.verifyEmail
    if (verifyEmail) {
      setPendingEmail(verifyEmail)
      setOtpStep(true)
    }
  }, [location.state])
  const [otp, setOtp] = useState(['', '', '', '', '', ''])
  const [otpError, setOtpError] = useState('')
  const [otpLoading, setOtpLoading] = useState(false)
  const [resendLoading, setResendLoading] = useState(false)
  const [resendMsg, setResendMsg] = useState('')
  const otpRefs = useRef([])

  const set = (k) => (e) => setForm({ ...form, [k]: e.target.value })

  const pwStrength = (() => {
    const p = form.password
    if (!p) return 0
    let s = 0
    if (p.length >= 8) s++
    if (/[A-Z]/.test(p)) s++
    if (/[0-9]/.test(p)) s++
    if (/[^A-Za-z0-9]/.test(p)) s++
    return s
  })()

  const pwLabel = ['', 'Weak', 'Fair', 'Good', 'Strong'][pwStrength]
  const pwColor = ['', '#ef4444', '#f59e0b', '#22c55e', '#16a34a'][pwStrength]

  const handleSubmit = async (e) => {
    e.preventDefault()
    setError('')
    setLoading(true)
    try {
      const res = await register(form)
      if (res.requires_verification) {
        setPendingEmail(res.email)
        setOtpStep(true)
      } else {
        navigate('/login')
      }
    } catch (err) {
      setError(err.response?.data?.detail || 'Registration failed')
    } finally {
      setLoading(false)
    }
  }

  const handleOtpChange = (idx, val) => {
    const digit = val.replace(/\D/g, '').slice(-1)
    const next = [...otp]
    next[idx] = digit
    setOtp(next)
    if (digit && idx < 5) otpRefs.current[idx + 1]?.focus()
  }

  const handleOtpKeyDown = (idx, e) => {
    if (e.key === 'Backspace' && !otp[idx] && idx > 0) otpRefs.current[idx - 1]?.focus()
  }

  const handleOtpPaste = (e) => {
    const pasted = e.clipboardData.getData('text').replace(/\D/g, '').slice(0, 6)
    if (pasted.length === 6) {
      setOtp(pasted.split(''))
      otpRefs.current[5]?.focus()
    }
  }

  const handleVerifyOtp = async (e) => {
    e.preventDefault()
    const code = otp.join('')
    if (code.length < 6) { setOtpError('Enter the 6-digit OTP'); return }
    setOtpError('')
    setOtpLoading(true)
    try {
      const user = await verifyOtp(pendingEmail, code)
      const ROLE_ROUTES = { superadmin: '/superadmin', admin: '/admin', hr: '/hr', interviewer: '/interviewer', candidate: '/candidate' }
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
    setResendMsg('')
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

  return (
    <div className="auth-split">
      {/* Left — Brand panel */}
      <div className="auth-brand">
        <div className="brand-noise" />
        <div className="brand-gradient" />

        <div className="geo-ring geo-ring-1" />
        <div className="geo-ring geo-ring-2" />
        <div className="geo-ring geo-ring-3" />
        <div className="geo-dot geo-dot-1" />
        <div className="geo-dot geo-dot-2" />
        <div className="geo-dot geo-dot-3" />
        <div className="geo-line geo-line-1" />
        <div className="geo-line geo-line-2" />

        <div className="brand-content">
          <div className="brand-logo-wrap">
            <img src="/logo.png?v=3" alt="Innomax Skills" className="brand-logo" />
          </div>
          <p className="brand-tagline">
            Your next chapter<br />
            <em>starts here</em>
          </p>

          <div className="brand-perks">
            <div className="brand-perk">
              <CheckCircle size={16} />
              <span>Browse open positions instantly</span>
            </div>
            <div className="brand-perk">
              <CheckCircle size={16} />
              <span>AI-powered resume scoring</span>
            </div>
            <div className="brand-perk">
              <CheckCircle size={16} />
              <span>Track your application status</span>
            </div>
            <div className="brand-perk">
              <CheckCircle size={16} />
              <span>Seamless interview scheduling</span>
            </div>
          </div>
        </div>

        <p className="brand-footer">&copy; 2026 Innomax Skills</p>
      </div>

      {/* Right — Form panel */}
      <div className="auth-form-panel">
        <div className="auth-form-inner">

          {/* ── OTP Verification Step ── */}
          {otpStep ? (
            <>
              <div className="form-header">
                <div className="form-header-badge">EMAIL VERIFICATION</div>
                <h2>Check your<br />inbox</h2>
                <p>We sent a 6-digit OTP to <b>{pendingEmail}</b></p>
              </div>

              {otpError && (
                <div className="auth-alert">
                  <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
                    <circle cx="8" cy="8" r="7" stroke="currentColor" strokeWidth="1.5"/>
                    <path d="M8 5v3.5M8 10.5v.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
                  </svg>
                  {otpError}
                </div>
              )}
              {resendMsg && (
                <div className="auth-alert" style={{ background: '#f0fdf4', border: '1px solid #bbf7d0', color: '#16a34a' }}>
                  {resendMsg}
                </div>
              )}

              <form onSubmit={handleVerifyOtp} className="auth-fields">
                <div className="otp-boxes" onPaste={handleOtpPaste}>
                  {otp.map((d, i) => (
                    <input
                      key={i}
                      ref={el => otpRefs.current[i] = el}
                      className="otp-box"
                      type="text"
                      inputMode="numeric"
                      maxLength={1}
                      value={d}
                      onChange={e => handleOtpChange(i, e.target.value)}
                      onKeyDown={e => handleOtpKeyDown(i, e)}
                      autoFocus={i === 0}
                    />
                  ))}
                </div>

                <button className="auth-submit" type="submit" disabled={otpLoading}>
                  <span>{otpLoading ? 'Verifying…' : 'Verify & Continue'}</span>
                  {!otpLoading && <ShieldCheck size={18} />}
                  <div className="submit-shine" />
                </button>
              </form>

              <div className="auth-footer" style={{ flexDirection: 'column', gap: 10 }}>
                <span style={{ fontSize: '0.82rem', color: '#9ca3af' }}>Didn't receive the email?</span>
                <button
                  onClick={handleResend}
                  disabled={resendLoading}
                  style={{ background: 'none', border: 'none', color: '#2b7cba', cursor: 'pointer', fontWeight: 600, fontSize: '0.88rem', display: 'flex', alignItems: 'center', gap: 6 }}
                >
                  <RotateCcw size={14} /> {resendLoading ? 'Sending…' : 'Resend OTP'}
                </button>
              </div>
            </>
          ) : (
            <>
              <div className="form-header">
                <div className="form-header-badge">CANDIDATE REGISTRATION</div>
                <h2>Create your<br />account</h2>
                <p>Join as a candidate and discover opportunities</p>
              </div>

              {error && (
                <div className="auth-alert">
                  <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
                    <circle cx="8" cy="8" r="7" stroke="currentColor" strokeWidth="1.5" />
                    <path d="M8 5v3.5M8 10.5v.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
                  </svg>
                  {error}
                </div>
              )}

              <form onSubmit={handleSubmit} className="auth-fields">
                <div className="field-row" style={{ animationDelay: '0.1s' }}>
                  <div className="field">
                    <label>First Name</label>
                    <div className="field-input-wrap">
                      <input value={form.first_name} onChange={set('first_name')} placeholder="Jane" required />
                    </div>
                  </div>
                  <div className="field">
                    <label>Last Name</label>
                    <div className="field-input-wrap">
                      <input value={form.last_name} onChange={set('last_name')} placeholder="Doe" required />
                    </div>
                  </div>
                </div>

                <div className="field" style={{ animationDelay: '0.12s' }}>
                  <label>Gender</label>
                  <div className="field-input-wrap">
                    <select
                      value={form.gender}
                      onChange={set('gender')}
                      required
                      style={{
                        width: '100%', padding: '13px 16px', background: '#fff',
                        border: '1.5px solid #e5e7eb', borderRadius: 10, color: form.gender ? '#1a1d23' : '#c8ccd4',
                        fontFamily: "'Source Sans 3', sans-serif", fontSize: '0.95rem', outline: 'none',
                        cursor: 'pointer', transition: 'all 0.2s ease'
                      }}
                    >
                      <option value="" disabled>Select gender</option>
                      <option value="male">Male</option>
                      <option value="female">Female</option>
                      <option value="other">Other</option>
                    </select>
                  </div>
                </div>

                <div className="field" style={{ animationDelay: '0.13s' }}>
                  <label>Date of Birth</label>
                  <div className="field-input-wrap">
                    <input type="date" value={form.date_of_birth} onChange={set('date_of_birth')} required
                      max={new Date(new Date().setFullYear(new Date().getFullYear() - 18)).toISOString().split('T')[0]}
                      style={{ colorScheme: 'light' }}
                    />
                  </div>
                </div>

                <div className="field" style={{ animationDelay: '0.15s' }}>
                  <label>Email Address</label>
                  <div className="field-input-wrap">
                    <input type="email" value={form.email} onChange={set('email')} placeholder="you@email.com" required autoComplete="email" />
                  </div>
                </div>

                <div className="field" style={{ animationDelay: '0.2s' }}>
                  <label>Password</label>
                  <div className="field-input-wrap has-action">
                    <input
                      type={showPw ? 'text' : 'password'}
                      value={form.password}
                      onChange={set('password')}
                      placeholder="Min 8 characters"
                      required
                      minLength={8}
                      autoComplete="new-password"
                    />
                    <button type="button" className="field-action" onClick={() => setShowPw(!showPw)} tabIndex={-1}>
                      {showPw ? <EyeOff size={16} /> : <Eye size={16} />}
                    </button>
                  </div>
                  {form.password && (
                    <div className="pw-strength">
                      <div className="pw-bars">
                        {[1, 2, 3, 4].map(i => (
                          <div key={i} className="pw-bar" style={{ background: i <= pwStrength ? pwColor : '#e5e7eb' }} />
                        ))}
                      </div>
                      <span className="pw-label" style={{ color: pwColor }}>{pwLabel}</span>
                    </div>
                  )}
                </div>

                <button className="auth-submit" type="submit" disabled={loading} style={{ animationDelay: '0.3s' }}>
                  <span>{loading ? 'Creating account...' : 'Create Account'}</span>
                  {!loading && <UserPlus size={18} />}
                  <div className="submit-shine" />
                </button>
              </form>

              <div className="auth-footer">
                <span>Already have an account?</span>
                <Link to="/login">Sign in</Link>
              </div>
            </>
          )}
        </div>
      </div>

      <style>{`
        /* ═══ Split Layout ═══ */
        .auth-split {
          display: flex;
          min-height: 100vh;
          background: #fafafa;
        }

        /* ═══ Brand Panel ═══ */
        .auth-brand {
          position: relative;
          width: 46%;
          min-height: 100vh;
          background: #0f1b2d;
          overflow: hidden;
          display: flex;
          flex-direction: column;
          justify-content: center;
          padding: 60px;
        }

        .brand-gradient {
          position: absolute;
          inset: 0;
          background:
            radial-gradient(ellipse at 30% 20%, rgba(43,124,186,0.25) 0%, transparent 60%),
            radial-gradient(ellipse at 70% 80%, rgba(26,95,150,0.15) 0%, transparent 50%),
            radial-gradient(ellipse at 50% 50%, rgba(30,25,20,0.5) 0%, transparent 100%);
        }

        .brand-noise {
          position: absolute;
          inset: 0;
          opacity: 0.035;
          background-image: url("data:image/svg+xml,%3Csvg viewBox='0 0 256 256' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='noise'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.85' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23noise)'/%3E%3C/svg%3E");
          background-size: 128px 128px;
        }

        .geo-ring {
          position: absolute;
          border-radius: 50%;
          border: 1.5px solid rgba(43,124,186,0.2);
        }
        .geo-ring-1 {
          width: 320px; height: 320px;
          top: -60px; right: -80px;
          animation: float-slow 20s ease-in-out infinite;
        }
        .geo-ring-2 {
          width: 200px; height: 200px;
          bottom: 15%; left: -40px;
          border-style: dashed;
          border-color: rgba(43,124,186,0.12);
          animation: float-slow 16s ease-in-out infinite reverse;
        }
        .geo-ring-3 {
          width: 120px; height: 120px;
          top: 35%; right: 20%;
          border-color: rgba(255,255,255,0.06);
          animation: float-slow 12s ease-in-out infinite;
        }

        .geo-dot {
          position: absolute;
          width: 6px; height: 6px;
          border-radius: 50%;
          background: rgba(43,124,186,0.5);
        }
        .geo-dot-1 { top: 20%; left: 25%; animation: pulse-dot 3s ease-in-out infinite; }
        .geo-dot-2 { top: 60%; right: 30%; animation: pulse-dot 3s ease-in-out infinite 1s; }
        .geo-dot-3 { bottom: 25%; left: 40%; animation: pulse-dot 3s ease-in-out infinite 2s; }

        .geo-line {
          position: absolute;
          height: 1px;
          background: linear-gradient(90deg, transparent, rgba(43,124,186,0.15), transparent);
        }
        .geo-line-1 { width: 200px; top: 30%; left: 10%; transform: rotate(-15deg); }
        .geo-line-2 { width: 150px; bottom: 30%; right: 5%; transform: rotate(20deg); }

        @keyframes float-slow {
          0%, 100% { transform: translate(0, 0) rotate(0deg); }
          50% { transform: translate(10px, -15px) rotate(3deg); }
        }
        @keyframes pulse-dot {
          0%, 100% { opacity: 0.3; transform: scale(1); }
          50% { opacity: 0.8; transform: scale(1.8); }
        }

        .brand-content {
          position: relative;
          z-index: 2;
          animation: brand-reveal 0.9s cubic-bezier(0.16, 1, 0.3, 1) both;
          display: flex;
          flex-direction: column;
          align-items: center;
          text-align: center;
        }

        @keyframes brand-reveal {
          from { opacity: 0; transform: translateY(30px); }
          to { opacity: 1; transform: translateY(0); }
        }

        .brand-logo-wrap {
          margin-bottom: 32px;
        }
        .brand-logo {
          height: 100px;
          width: auto;
          filter: drop-shadow(0 4px 20px rgba(43,124,186,0.3)) brightness(1.15);
          transition: transform 0.3s ease;
        }
        .brand-logo:hover {
          transform: scale(1.03);
        }

        .brand-tagline {
          font-family: 'Source Sans 3', sans-serif;
          font-size: 1.6rem;
          color: rgba(255,255,255,0.5);
          font-weight: 300;
          line-height: 1.5;
          margin-bottom: 40px;
        }
        .brand-tagline em {
          color: rgba(43,124,186,0.8);
          font-style: italic;
          font-family: 'Playfair Display', serif;
          font-size: 1.8rem;
        }

        .brand-perks {
          display: flex;
          flex-direction: column;
          gap: 16px;
          padding-top: 32px;
          border-top: 1px solid rgba(255,255,255,0.08);
          align-items: center;
        }
        .brand-perk {
          display: flex;
          align-items: center;
          gap: 12px;
          color: rgba(255,255,255,0.45);
          font-size: 1rem;
          font-family: 'Source Sans 3', sans-serif;
        }
        .brand-perk svg {
          color: rgba(43,124,186,0.6);
          flex-shrink: 0;
        }

        .brand-footer {
          position: absolute;
          bottom: 32px;
          left: 60px;
          font-size: 0.7rem;
          color: rgba(255,255,255,0.15);
          font-family: 'JetBrains Mono', monospace;
          letter-spacing: 0.05em;
        }

        /* ═══ Form Panel ═══ */
        .auth-form-panel {
          flex: 1;
          display: flex;
          align-items: center;
          justify-content: center;
          padding: 48px;
          position: relative;
          background: #fafafa;
        }

        .auth-form-panel::before {
          content: '';
          position: absolute;
          top: 0;
          left: 0;
          bottom: 0;
          width: 1px;
          background: linear-gradient(180deg, transparent 10%, #e5e7eb 50%, transparent 90%);
        }

        .auth-form-inner {
          width: 100%;
          max-width: 420px;
        }

        .form-header {
          margin-bottom: 32px;
          animation: field-up 0.6s cubic-bezier(0.16, 1, 0.3, 1) both;
        }

        .form-header-badge {
          font-family: 'JetBrains Mono', monospace;
          font-size: 0.65rem;
          font-weight: 600;
          letter-spacing: 0.2em;
          color: #2b7cba;
          margin-bottom: 12px;
          padding: 5px 12px;
          background: rgba(43,124,186,0.08);
          border-radius: 4px;
          display: inline-block;
        }

        .form-header h2 {
          font-family: 'Outfit', sans-serif;
          font-size: 2.2rem;
          font-weight: 700;
          color: #1a1d23;
          line-height: 1.15;
          letter-spacing: -0.03em;
          margin-bottom: 8px;
        }

        .form-header p {
          font-size: 0.95rem;
          color: #9ca3af;
        }

        /* Alert */
        .auth-alert {
          display: flex;
          align-items: center;
          gap: 10px;
          padding: 12px 16px;
          background: #fef2f2;
          border: 1px solid #fecaca;
          border-radius: 10px;
          color: #dc2626;
          font-size: 0.85rem;
          font-weight: 500;
          margin-bottom: 24px;
          animation: shake 0.4s ease-in-out;
        }

        @keyframes shake {
          0%, 100% { transform: translateX(0); }
          25% { transform: translateX(-6px); }
          75% { transform: translateX(6px); }
        }

        /* Fields */
        .auth-fields {
          display: flex;
          flex-direction: column;
          gap: 18px;
        }

        .field-row {
          display: grid;
          grid-template-columns: 1fr 1fr;
          gap: 14px;
          animation: field-up 0.6s cubic-bezier(0.16, 1, 0.3, 1) both;
        }

        .field {
          animation: field-up 0.6s cubic-bezier(0.16, 1, 0.3, 1) both;
        }

        .field-row .field {
          animation: none;
        }

        @keyframes field-up {
          from { opacity: 0; transform: translateY(16px); }
          to { opacity: 1; transform: translateY(0); }
        }

        .field label {
          display: block;
          font-family: 'Outfit', sans-serif;
          font-size: 0.75rem;
          font-weight: 600;
          color: #5a6170;
          text-transform: uppercase;
          letter-spacing: 0.08em;
          margin-bottom: 8px;
        }

        .field-input-wrap {
          position: relative;
        }

        .field-input-wrap input {
          width: 100%;
          padding: 13px 16px;
          background: #fff;
          border: 1.5px solid #e5e7eb;
          border-radius: 10px;
          color: #1a1d23;
          font-family: 'Source Sans 3', sans-serif;
          font-size: 0.95rem;
          outline: none;
          transition: all 0.2s ease;
        }

        .field-input-wrap input::placeholder {
          color: #c8ccd4;
        }

        .field-input-wrap input:focus {
          border-color: #2b7cba;
          box-shadow: 0 0 0 3px rgba(43,124,186,0.1), 0 2px 8px rgba(43,124,186,0.08);
          background: #fff;
        }

        .field-input-wrap.has-action input {
          padding-right: 44px;
        }

        .field-action {
          position: absolute;
          right: 4px;
          top: 50%;
          transform: translateY(-50%);
          width: 34px;
          height: 34px;
          display: grid;
          place-items: center;
          background: none;
          border: none;
          color: #9ca3af;
          cursor: pointer;
          border-radius: 8px;
          transition: all 0.15s;
        }
        .field-action:hover {
          color: #2b7cba;
          background: rgba(43,124,186,0.06);
        }

        /* Password strength */
        .pw-strength {
          display: flex;
          align-items: center;
          gap: 10px;
          margin-top: 8px;
        }
        .pw-bars {
          display: flex;
          gap: 4px;
          flex: 1;
        }
        .pw-bar {
          height: 3px;
          flex: 1;
          border-radius: 3px;
          transition: background 0.3s;
        }
        .pw-label {
          font-family: 'JetBrains Mono', monospace;
          font-size: 0.65rem;
          font-weight: 600;
          letter-spacing: 0.04em;
          min-width: 40px;
          text-align: right;
        }

        /* Submit */
        .auth-submit {
          position: relative;
          display: flex;
          align-items: center;
          justify-content: center;
          gap: 10px;
          width: 100%;
          padding: 14px 24px;
          margin-top: 6px;
          background: linear-gradient(135deg, #2b7cba 0%, #1a5f96 100%);
          color: #fff;
          border: none;
          border-radius: 12px;
          font-family: 'Outfit', sans-serif;
          font-size: 1rem;
          font-weight: 600;
          cursor: pointer;
          overflow: hidden;
          transition: all 0.3s cubic-bezier(0.16, 1, 0.3, 1);
          animation: field-up 0.6s cubic-bezier(0.16, 1, 0.3, 1) both;
        }

        .auth-submit:hover:not(:disabled) {
          transform: translateY(-2px);
          box-shadow: 0 8px 30px rgba(43,124,186,0.35), 0 2px 8px rgba(43,124,186,0.2);
        }

        .auth-submit:active:not(:disabled) {
          transform: translateY(0);
        }

        .auth-submit:disabled {
          opacity: 0.6;
          cursor: not-allowed;
        }

        .submit-shine {
          position: absolute;
          top: 0;
          left: -100%;
          width: 100%;
          height: 100%;
          background: linear-gradient(90deg, transparent, rgba(255,255,255,0.15), transparent);
          transition: left 0.5s;
        }
        .auth-submit:hover .submit-shine {
          left: 100%;
        }

        /* Footer */
        .auth-footer {
          display: flex;
          align-items: center;
          justify-content: center;
          gap: 6px;
          margin-top: 28px;
          font-size: 0.88rem;
          color: #9ca3af;
          animation: field-up 0.6s cubic-bezier(0.16, 1, 0.3, 1) 0.4s both;
        }

        .auth-footer a {
          color: #2b7cba;
          text-decoration: none;
          font-weight: 600;
          transition: color 0.15s;
        }
        .auth-footer a:hover {
          color: #1a5f96;
          text-decoration: underline;
        }

        /* ═══ OTP Boxes ═══ */
        .otp-boxes {
          display: flex;
          gap: 10px;
          justify-content: center;
          margin-bottom: 8px;
        }
        .otp-box {
          width: 52px;
          height: 60px;
          text-align: center;
          font-size: 1.6rem;
          font-weight: 700;
          font-family: 'JetBrains Mono', monospace;
          color: #1a5f96;
          border: 2px solid #e5e7eb;
          border-radius: 12px;
          background: #fff;
          outline: none;
          transition: all 0.2s ease;
          caret-color: transparent;
        }
        .otp-box:focus {
          border-color: #2b7cba;
          box-shadow: 0 0 0 3px rgba(43,124,186,0.12);
        }

        /* ═══ Responsive ═══ */
        @media (max-width: 900px) {
          .auth-split { flex-direction: column; }
          .auth-brand {
            width: 100%;
            min-height: auto;
            padding: 40px 32px;
          }
          .brand-title { font-size: 2.8rem; }
          .brand-tagline { margin-bottom: 24px; font-size: 1rem; }
          .brand-tagline em { font-size: 1.15rem; }
          .brand-footer { display: none; }
          .auth-form-panel { padding: 32px 24px; }
          .auth-form-panel::before { display: none; }
          .field-row { grid-template-columns: 1fr; }
        }
      `}</style>
    </div>
  )
}
