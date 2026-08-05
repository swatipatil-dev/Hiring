import { useState, useRef } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { Mail, ShieldCheck, Lock, Eye, EyeOff, RotateCcw, ArrowLeft } from 'lucide-react'
import api from '../api/client'

const STEPS = { EMAIL: 1, OTP: 2, PASSWORD: 3, DONE: 4 }

export default function ForgotPassword() {
  const navigate = useNavigate()
  const [step, setStep]           = useState(STEPS.EMAIL)
  const [email, setEmail]         = useState('')
  const [otp, setOtp]             = useState(['', '', '', '', '', ''])
  const [newPw, setNewPw]         = useState('')
  const [confirmPw, setConfirmPw] = useState('')
  const [showPw, setShowPw]       = useState(false)
  const [showCpw, setShowCpw]     = useState(false)
  const [loading, setLoading]     = useState(false)
  const [error, setError]         = useState('')
  const [resendMsg, setResendMsg] = useState('')
  const otpRefs = useRef([])

  // ── Step 1: send OTP ──────────────────────────────────────
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

  const handleEmailSubmit = async (e) => {
    e.preventDefault()
    setError('')
    setLoading(true)
    try {
      await api.post('/auth/forgot-password', { email })
      setStep(STEPS.OTP)
    } catch (err) {
      setError(extractErrorDetail(err, 'Something went wrong'))
    } finally {
      setLoading(false)
    }
  }

  // ── OTP helpers ───────────────────────────────────────────
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

  // ── Step 2: verify OTP ────────────────────────────────────
  const handleOtpSubmit = async (e) => {
    e.preventDefault()
    const code = otp.join('')
    if (code.length < 6) { setError('Enter the 6-digit OTP'); return }
    // We just move to the password step; OTP is verified on final submit
    setError('')
    setStep(STEPS.PASSWORD)
  }

  const handleResend = async () => {
    setResendMsg(''); setError('')
    try {
      await api.post('/auth/forgot-password', { email })
      setResendMsg('New OTP sent.')
      setOtp(['', '', '', '', '', ''])
      otpRefs.current[0]?.focus()
    } catch {
      setResendMsg('Failed to resend.')
    }
  }

  // ── Step 3: reset password ────────────────────────────────
  const pwStrength = (() => {
    if (!newPw) return 0
    let s = 0
    if (newPw.length >= 8) s++
    if (/[A-Z]/.test(newPw)) s++
    if (/[0-9]/.test(newPw)) s++
    if (/[^A-Za-z0-9]/.test(newPw)) s++
    return s
  })()
  const pwLabel = ['', 'Weak', 'Fair', 'Good', 'Strong'][pwStrength]
  const pwColor = ['', '#ef4444', '#f59e0b', '#22c55e', '#16a34a'][pwStrength]

  const handlePasswordSubmit = async (e) => {
    e.preventDefault()
    setError('')
    if (newPw !== confirmPw) { setError('Passwords do not match'); return }
    if (newPw.length < 8)    { setError('Password must be at least 8 characters'); return }
    setLoading(true)
    try {
      await api.post('/auth/reset-password', { email, otp: otp.join(''), new_password: newPw })
      setStep(STEPS.DONE)
    } catch (err) {
      const detail = extractErrorDetail(err, 'Reset failed')
      if (detail.toLowerCase().includes('otp')) {
        setError(detail + ' — go back and re-enter the OTP.')
        setStep(STEPS.OTP)
        setOtp(['', '', '', '', '', ''])
      } else {
        setError(detail)
      }
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="fp-page">
      <div className="fp-card">

        {/* Back to login */}
        <Link to="/login" className="fp-back">
          <ArrowLeft size={15} /> Back to login
        </Link>

        {/* ── Step 1: Email ── */}
        {step === STEPS.EMAIL && (
          <>
            <div className="fp-icon-wrap" style={{ background: 'rgba(43,124,186,0.1)' }}>
              <Mail size={26} color="#2b7cba" />
            </div>
            <h2 className="fp-title">Forgot password?</h2>
            <p className="fp-sub">Enter your registered email and we'll send a reset OTP.</p>

            {error && <div className="fp-alert">{error}</div>}

            <form onSubmit={handleEmailSubmit} className="fp-form">
              <div className="fp-field">
                <label>Email address</label>
                <input
                  type="email" value={email} required autoFocus
                  onChange={e => setEmail(e.target.value)}
                  placeholder="you@email.com"
                />
              </div>
              <button className="fp-btn" type="submit" disabled={loading}>
                {loading ? 'Sending OTP…' : 'Send OTP'}
              </button>
            </form>
          </>
        )}

        {/* ── Step 2: OTP ── */}
        {step === STEPS.OTP && (
          <>
            <div className="fp-icon-wrap" style={{ background: 'rgba(43,124,186,0.1)' }}>
              <ShieldCheck size={26} color="#2b7cba" />
            </div>
            <h2 className="fp-title">Enter OTP</h2>
            <p className="fp-sub">We sent a 6-digit code to <b>{email}</b>. Valid for 10 minutes.</p>

            {error    && <div className="fp-alert">{error}</div>}
            {resendMsg && <div className="fp-success">{resendMsg}</div>}

            <form onSubmit={handleOtpSubmit} className="fp-form">
              <div className="otp-boxes" onPaste={handleOtpPaste}>
                {otp.map((d, i) => (
                  <input
                    key={i}
                    ref={el => otpRefs.current[i] = el}
                    className="otp-box"
                    type="text" inputMode="numeric" maxLength={1}
                    value={d} autoFocus={i === 0}
                    onChange={e => handleOtpChange(i, e.target.value)}
                    onKeyDown={e => handleOtpKeyDown(i, e)}
                  />
                ))}
              </div>
              <button className="fp-btn" type="submit" disabled={otp.join('').length < 6}>
                Verify OTP
              </button>
            </form>

            <div className="fp-resend">
              <span>Didn't receive it?</span>
              <button onClick={handleResend} className="fp-link-btn">
                <RotateCcw size={13} /> Resend OTP
              </button>
            </div>
          </>
        )}

        {/* ── Step 3: New password ── */}
        {step === STEPS.PASSWORD && (
          <>
            <div className="fp-icon-wrap" style={{ background: 'rgba(34,197,94,0.1)' }}>
              <Lock size={26} color="#16a34a" />
            </div>
            <h2 className="fp-title">New password</h2>
            <p className="fp-sub">Choose a strong password for your account.</p>

            {error && <div className="fp-alert">{error}</div>}

            <form onSubmit={handlePasswordSubmit} className="fp-form">
              <div className="fp-field">
                <label>New password</label>
                <div className="fp-input-wrap">
                  <input
                    type={showPw ? 'text' : 'password'}
                    value={newPw} required minLength={8} autoFocus
                    onChange={e => setNewPw(e.target.value)}
                    placeholder="Min 8 characters"
                  />
                  <button type="button" className="fp-eye" onClick={() => setShowPw(!showPw)} tabIndex={-1}>
                    {showPw ? <EyeOff size={16}/> : <Eye size={16}/>}
                  </button>
                </div>
                {newPw && (
                  <div className="pw-strength">
                    <div className="pw-bars">
                      {[1,2,3,4].map(i => (
                        <div key={i} className="pw-bar" style={{ background: i <= pwStrength ? pwColor : '#e5e7eb' }} />
                      ))}
                    </div>
                    <span className="pw-label" style={{ color: pwColor }}>{pwLabel}</span>
                  </div>
                )}
              </div>

              <div className="fp-field">
                <label>Confirm password</label>
                <div className="fp-input-wrap">
                  <input
                    type={showCpw ? 'text' : 'password'}
                    value={confirmPw} required
                    onChange={e => setConfirmPw(e.target.value)}
                    placeholder="Re-enter password"
                    style={{ borderColor: confirmPw && confirmPw !== newPw ? '#ef4444' : '' }}
                  />
                  <button type="button" className="fp-eye" onClick={() => setShowCpw(!showCpw)} tabIndex={-1}>
                    {showCpw ? <EyeOff size={16}/> : <Eye size={16}/>}
                  </button>
                </div>
              </div>

              <button className="fp-btn" type="submit" disabled={loading}>
                {loading ? 'Resetting…' : 'Reset Password'}
              </button>
            </form>
          </>
        )}

        {/* ── Step 4: Done ── */}
        {step === STEPS.DONE && (
          <div className="fp-done">
            <div className="fp-icon-wrap" style={{ background: 'rgba(34,197,94,0.12)' }}>
              <ShieldCheck size={30} color="#16a34a" />
            </div>
            <h2 className="fp-title" style={{ color: '#16a34a' }}>Password reset!</h2>
            <p className="fp-sub">Your password has been updated successfully.</p>
            <button className="fp-btn" style={{ background: '#16a34a' }} onClick={() => navigate('/login')}>
              Go to Login
            </button>
          </div>
        )}

        {/* Step indicator */}
        {step !== STEPS.DONE && (
          <div className="fp-steps">
            {[STEPS.EMAIL, STEPS.OTP, STEPS.PASSWORD].map(s => (
              <div key={s} className={`fp-step-dot ${step === s ? 'active' : ''} ${step > s ? 'done' : ''}`} />
            ))}
          </div>
        )}
      </div>

      <style>{`
        .fp-page {
          min-height: 100vh;
          display: flex;
          align-items: center;
          justify-content: center;
          background: #1a1f2e;
          background-image:
            radial-gradient(ellipse at 30% 20%, rgba(43,124,186,0.08) 0%, transparent 60%),
            radial-gradient(ellipse at 70% 80%, rgba(43,124,186,0.05) 0%, transparent 50%);
          padding: 24px;
        }

        .fp-card {
          background: #fff;
          border-radius: 20px;
          padding: 44px 40px 36px;
          width: 100%;
          max-width: 420px;
          position: relative;
          box-shadow: 0 20px 60px rgba(0,0,0,0.3);
          animation: fp-in 0.4s cubic-bezier(0.16,1,0.3,1) both;
        }

        @keyframes fp-in {
          from { opacity: 0; transform: translateY(20px); }
          to   { opacity: 1; transform: translateY(0); }
        }

        .fp-back {
          display: inline-flex;
          align-items: center;
          gap: 6px;
          color: #9ca3af;
          text-decoration: none;
          font-size: 0.82rem;
          font-weight: 500;
          margin-bottom: 28px;
          transition: color 0.15s;
        }
        .fp-back:hover { color: #2b7cba; }

        .fp-icon-wrap {
          width: 56px; height: 56px;
          border-radius: 16px;
          display: grid; place-items: center;
          margin-bottom: 20px;
        }

        .fp-title {
          font-family: 'Outfit', sans-serif;
          font-size: 1.75rem;
          font-weight: 700;
          color: #1a1d23;
          margin: 0 0 8px;
          letter-spacing: -0.03em;
        }

        .fp-sub {
          color: #6b7280;
          font-size: 0.92rem;
          margin: 0 0 28px;
          line-height: 1.5;
        }

        .fp-alert {
          background: #fef2f2;
          border: 1px solid #fecaca;
          color: #dc2626;
          border-radius: 10px;
          padding: 11px 14px;
          font-size: 0.84rem;
          font-weight: 500;
          margin-bottom: 20px;
        }

        .fp-success {
          background: #f0fdf4;
          border: 1px solid #bbf7d0;
          color: #16a34a;
          border-radius: 10px;
          padding: 11px 14px;
          font-size: 0.84rem;
          font-weight: 500;
          margin-bottom: 20px;
        }

        .fp-form {
          display: flex;
          flex-direction: column;
          gap: 18px;
        }

        .fp-field label {
          display: block;
          font-size: 0.75rem;
          font-weight: 600;
          color: #5a6170;
          text-transform: uppercase;
          letter-spacing: 0.08em;
          margin-bottom: 8px;
          font-family: 'Outfit', sans-serif;
        }

        .fp-field input {
          width: 100%;
          padding: 13px 16px;
          border: 1.5px solid #e5e7eb;
          border-radius: 10px;
          font-size: 0.95rem;
          color: #1a1d23;
          outline: none;
          transition: all 0.2s;
          font-family: 'Source Sans 3', sans-serif;
          box-sizing: border-box;
        }
        .fp-field input:focus {
          border-color: #2b7cba;
          box-shadow: 0 0 0 3px rgba(43,124,186,0.1);
        }

        .fp-input-wrap { position: relative; }
        .fp-input-wrap input { padding-right: 44px; }
        .fp-eye {
          position: absolute; right: 4px; top: 50%; transform: translateY(-50%);
          width: 34px; height: 34px; background: none; border: none;
          color: #9ca3af; cursor: pointer; border-radius: 8px;
          display: grid; place-items: center; transition: all 0.15s;
        }
        .fp-eye:hover { color: #2b7cba; background: rgba(43,124,186,0.06); }

        .fp-btn {
          width: 100%;
          padding: 14px;
          background: linear-gradient(135deg, #2b7cba, #1a5f96);
          color: #fff;
          border: none;
          border-radius: 12px;
          font-size: 1rem;
          font-weight: 600;
          font-family: 'Outfit', sans-serif;
          cursor: pointer;
          transition: all 0.25s;
        }
        .fp-btn:hover:not(:disabled) {
          transform: translateY(-2px);
          box-shadow: 0 8px 24px rgba(43,124,186,0.35);
        }
        .fp-btn:disabled { opacity: 0.6; cursor: not-allowed; }

        /* OTP boxes */
        .otp-boxes {
          display: flex;
          gap: 10px;
          justify-content: center;
          margin-bottom: 4px;
        }
        .otp-box {
          width: 52px; height: 60px;
          text-align: center;
          font-size: 1.6rem;
          font-weight: 700;
          font-family: 'JetBrains Mono', monospace;
          color: #1a5f96;
          border: 2px solid #e5e7eb;
          border-radius: 12px;
          background: #fff;
          outline: none;
          transition: all 0.2s;
          caret-color: transparent;
        }
        .otp-box:focus {
          border-color: #2b7cba;
          box-shadow: 0 0 0 3px rgba(43,124,186,0.12);
        }

        .fp-resend {
          display: flex;
          align-items: center;
          justify-content: center;
          gap: 8px;
          margin-top: 16px;
          font-size: 0.83rem;
          color: #9ca3af;
        }
        .fp-link-btn {
          display: inline-flex;
          align-items: center;
          gap: 5px;
          background: none;
          border: none;
          color: #2b7cba;
          font-size: 0.83rem;
          font-weight: 600;
          cursor: pointer;
          padding: 0;
          transition: color 0.15s;
        }
        .fp-link-btn:hover { color: #1a5f96; }

        /* Password strength */
        .pw-strength { display: flex; align-items: center; gap: 10px; margin-top: 8px; }
        .pw-bars { display: flex; gap: 4px; flex: 1; }
        .pw-bar { height: 3px; flex: 1; border-radius: 3px; transition: background 0.3s; }
        .pw-label { font-family: 'JetBrains Mono', monospace; font-size: 0.65rem; font-weight: 600; letter-spacing: 0.04em; min-width: 40px; text-align: right; }

        /* Done state */
        .fp-done {
          display: flex;
          flex-direction: column;
          align-items: center;
          text-align: center;
          padding: 12px 0;
          gap: 12px;
        }
        .fp-done .fp-sub { margin-bottom: 12px; }

        /* Step dots */
        .fp-steps {
          display: flex;
          justify-content: center;
          gap: 8px;
          margin-top: 28px;
        }
        .fp-step-dot {
          width: 8px; height: 8px;
          border-radius: 50%;
          background: #e5e7eb;
          transition: all 0.3s;
        }
        .fp-step-dot.active { background: #2b7cba; transform: scale(1.3); }
        .fp-step-dot.done   { background: #22c55e; }

        @media (max-width: 480px) {
          .fp-card { padding: 32px 24px 28px; }
          .otp-box { width: 44px; height: 52px; font-size: 1.4rem; }
        }
      `}</style>
    </div>
  )
}
