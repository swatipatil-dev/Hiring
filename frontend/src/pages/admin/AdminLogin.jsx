import { useState, useEffect } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { Eye, EyeOff, Shield } from 'lucide-react'
import { useAuth } from '../../context/AuthContext'

const API = '/api/v1'

export default function AdminLogin() {
  const { login, logout } = useAuth()
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  // Branding only — this page does not scope the login, it just themes itself.
  const companySlug = searchParams.get('c') || ''

  const [company,  setCompany]  = useState(null)
  const [email,    setEmail]    = useState('')
  const [password, setPassword] = useState('')
  const [showPw,   setShowPw]   = useState(false)
  const [error,    setError]    = useState('')
  const [loading,  setLoading]  = useState(false)

  // Load company branding when a company slug is supplied
  useEffect(() => {
    if (!companySlug) return
    fetch(`${API}/public/company-info?c=${encodeURIComponent(companySlug)}`)
      .then(r => r.ok ? r.json() : null)
      .then(d  => { if (d) setCompany(d) })
      .catch(() => {})
  }, [companySlug])

  const accent = company?.primary_color || '#2b7cba'

  const handleSubmit = async (e) => {
    e.preventDefault()
    setError('')
    setLoading(true)
    try {
      const user = await login(email, password)
      const role = user?.role?.toLowerCase()
      if (role !== 'admin') {
        logout()
        setError('Access denied. This portal is for administrators only.')
        setLoading(false)
        return
      }
      navigate('/admin')
    } catch (err) {
      setError(err.response?.data?.detail || 'Invalid email or password')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div style={{ display: 'flex', minHeight: '100vh', fontFamily: "'Segoe UI', system-ui, sans-serif", background: '#f1f5f9' }}>

      {/* Left branding panel — decorative, so it steps aside on narrow screens
          rather than pushing the form off the edge (see <style> below). */}
      <div className="al-brand" style={{
        width: 400, flexShrink: 0, display: 'flex', flexDirection: 'column',
        background: `linear-gradient(160deg, ${accent} 0%, ${accent}cc 100%)`,
        padding: '48px 44px',
      }}>
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', justifyContent: 'center' }}>
          {/* Company logo or Shield fallback */}
          {company?.logo_url ? (
            <img
              src={company.logo_url}
              alt={company.name}
              style={{ height: 70, objectFit: 'contain', marginBottom: 28, alignSelf: 'flex-start' }}
              onError={e => e.target.style.display = 'none'}
            />
          ) : (
            <div style={{
              width: 64, height: 64, borderRadius: 18,
              background: 'rgba(255,255,255,0.15)',
              display: 'grid', placeItems: 'center', marginBottom: 28,
            }}>
              <Shield size={32} color="#fff" />
            </div>
          )}

          <h1 style={{ fontSize: '2rem', fontWeight: 800, color: '#fff', lineHeight: 1.2, margin: '0 0 14px', fontFamily: 'Georgia, serif' }}>
            {company?.name || 'Admin Portal'}
          </h1>
          <p style={{ color: 'rgba(255,255,255,0.75)', fontSize: '0.95rem', lineHeight: 1.7, maxWidth: 300, margin: '0 0 32px' }}>
            Sign in to manage your hiring pipeline — jobs, interviews, and candidates in one place.
          </p>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
            {['User & department management', 'Job postings and pipelines', 'Analytics and reporting', 'Offer letter tracking'].map((f, i) => (
              <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 12, color: 'rgba(255,255,255,0.85)', fontSize: '0.88rem' }}>
                <span style={{ width: 7, height: 7, borderRadius: '50%', background: 'rgba(255,255,255,0.6)', flexShrink: 0 }} />
                {f}
              </div>
            ))}
          </div>
        </div>
        <div style={{ fontSize: '0.72rem', color: 'rgba(255,255,255,0.4)', paddingTop: 24, borderTop: '1px solid rgba(255,255,255,0.1)' }}>
          Powered by <strong style={{ color: 'rgba(255,255,255,0.65)' }}>HireFlow</strong>
        </div>
      </div>

      {/* Right form panel */}
      <div className="al-form-panel" style={{ flex: 1, minWidth: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '40px 24px' }}>
        <div className="al-card" style={{
          background: '#fff', borderRadius: 20, padding: '44px 48px',
          width: '100%', maxWidth: 460,
          boxShadow: '0 8px 48px rgba(0,0,0,0.1)', border: '1px solid #e8edf4',
        }}>
          {/* Header */}
          <div style={{ display: 'flex', alignItems: 'flex-start', gap: 14, marginBottom: 28 }}>
            <div style={{
              width: 44, height: 44, borderRadius: 12, flexShrink: 0,
              background: `${accent}18`, display: 'grid', placeItems: 'center',
            }}>
              <Shield size={20} color={accent} />
            </div>
            <div>
              <h2 style={{ fontSize: '1.3rem', fontWeight: 800, color: '#111827', margin: '0 0 4px', fontFamily: 'Georgia, serif' }}>
                Administrator Sign In
              </h2>
              <p style={{ fontSize: '0.82rem', color: '#6b7280', margin: 0 }}>
                {company?.name ? `Sign in to ${company.name}` : 'Sign in to access your admin dashboard'}
              </p>
            </div>
          </div>

          {error && (
            <div style={{ padding: '11px 14px', borderRadius: 10, background: '#fef2f2', border: '1px solid #fecaca', color: '#dc2626', fontSize: '0.83rem', marginBottom: 20 }}>
              {error}
            </div>
          )}

          <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              <label style={{ fontSize: '0.74rem', fontWeight: 700, color: '#4b5563', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                Email Address
              </label>
              <input
                style={{ width: '100%', padding: '12px 14px', borderRadius: 10, border: '1.5px solid #e5e7eb', fontSize: '0.92rem', outline: 'none', boxSizing: 'border-box' }}
                type="email" required autoFocus placeholder="admin@company.com"
                value={email} onChange={e => setEmail(e.target.value)}
                onFocus={e => e.target.style.borderColor = accent}
                onBlur={e => e.target.style.borderColor = '#e5e7eb'}
              />
            </div>

            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              <label style={{ fontSize: '0.74rem', fontWeight: 700, color: '#4b5563', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                Password
              </label>
              <div style={{ position: 'relative' }}>
                <input
                  style={{ width: '100%', padding: '12px 44px 12px 14px', borderRadius: 10, border: '1.5px solid #e5e7eb', fontSize: '0.92rem', outline: 'none', boxSizing: 'border-box' }}
                  type={showPw ? 'text' : 'password'} required placeholder="••••••••"
                  value={password} onChange={e => setPassword(e.target.value)}
                  onFocus={e => e.target.style.borderColor = accent}
                  onBlur={e => e.target.style.borderColor = '#e5e7eb'}
                />
                <button type="button" onClick={() => setShowPw(p => !p)} tabIndex={-1}
                  style={{ position: 'absolute', right: 10, top: '50%', transform: 'translateY(-50%)', background: 'none', border: 'none', cursor: 'pointer', color: '#9ca3af', padding: 4 }}>
                  {showPw ? <EyeOff size={16} /> : <Eye size={16} />}
                </button>
              </div>
            </div>

            <button
              type="submit"
              disabled={loading}
              style={{
                padding: '13px', borderRadius: 12, border: 'none',
                background: loading ? '#e5e7eb' : accent,
                color: loading ? '#9ca3af' : '#fff',
                fontSize: '0.95rem', fontWeight: 700,
                cursor: loading ? 'not-allowed' : 'pointer',
                transition: 'all 0.2s', marginTop: 4,
              }}
            >
              {loading ? 'Signing in…' : 'Sign In →'}
            </button>
          </form>

          <div style={{ marginTop: 22, textAlign: 'center' }}>
            <a href="/forgot-password" style={{ fontSize: '0.82rem', color: accent, textDecoration: 'none', fontWeight: 600 }}>
              Forgot password?
            </a>
          </div>
        </div>
      </div>

      <style>{`
        @media (max-width: 820px) {
          .al-brand { display: none !important; }
        }
        @media (max-width: 480px) {
          .al-form-panel { padding: 24px 12px !important; }
          .al-card { padding: 28px 20px !important; }
        }
      `}</style>
    </div>
  )
}
