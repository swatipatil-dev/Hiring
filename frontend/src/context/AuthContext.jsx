import { createContext, useContext, useState, useCallback } from 'react'
import axios from 'axios'
import api from '../api/client'
import MfaDialog from '../components/MfaDialog'

const AuthContext = createContext(null)

function parseJWT(token) {
  try {
    return JSON.parse(atob(token.split('.')[1]))
  } catch {
    return {}
  }
}

function setFavicon(url) {
  if (!url) return
  let link = document.querySelector("link[rel~='icon']")
  if (!link) {
    link = document.createElement('link')
    link.rel = 'icon'
    document.head.appendChild(link)
  }
  link.href = url
}

function applyFont(fontFamily) {
  if (!fontFamily) return
  const id = 'company-font-link'
  if (!document.getElementById(id)) {
    const link = document.createElement('link')
    link.id = id
    link.rel = 'stylesheet'
    document.head.appendChild(link)
  }
  const encoded = encodeURIComponent(fontFamily)
  document.getElementById(id).href = `https://fonts.googleapis.com/css2?family=${encoded}:wght@400;500;600;700;800&display=swap`
  document.documentElement.style.setProperty('--font-body', `'${fontFamily}', system-ui, sans-serif`)
}

function applyCompanyBranding(token) {
  const payload = parseJWT(token)
  // The platform org (Innomax) keeps the default teal theme — only real client
  // companies override the accent with their brand color.
  const isInnomax = payload.company_slug === 'innomax'
  if (payload.company_color && !isInnomax) {
    document.documentElement.style.setProperty('--accent-primary', payload.company_color)
    document.documentElement.style.setProperty('--accent-secondary', payload.company_secondary_color || payload.company_color)
    document.documentElement.style.setProperty('--accent-tertiary', payload.company_accent_color || payload.company_color)
  }
  if (payload.company_favicon && !isInnomax) setFavicon(payload.company_favicon)
  if (payload.company_font && !isInnomax) applyFont(payload.company_font)
  return {
    company_id: payload.company_id || null,
    company_logo: payload.company_logo || null,
    company_favicon: payload.company_favicon || null,
    company_color: payload.company_color || null,
    company_secondary_color: payload.company_secondary_color || null,
    company_accent_color: payload.company_accent_color || null,
    company_font: payload.company_font || null,
    company_name: payload.company_name || null,
    company_slug: payload.company_slug || null,
  }
}

export function AuthProvider({ children }) {
  const [user, setUser] = useState(() => {
    try { return JSON.parse(localStorage.getItem('hireflow_user')) } catch { return null }
  })
  const [token, setToken] = useState(() => {
    const t = localStorage.getItem('hireflow_token')
    if (t) applyCompanyBranding(t)
    return t
  })

  // Pending two-factor step: { kind: 'verify' | 'enroll', mfaToken, resolve, reject }
  const [mfaChallenge, setMfaChallenge] = useState(null)

  // Used by CompanyLogin to update React state after a raw fetch login
  const loginDirect = useCallback((access_token, u) => {
    const branding = applyCompanyBranding(access_token)
    const enrichedUser = { ...u, ...branding }
    localStorage.setItem('hireflow_token', access_token)
    localStorage.setItem('hireflow_user', JSON.stringify(enrichedUser))
    setToken(access_token)
    setUser(enrichedUser)
    return enrichedUser
  }, [])

  // Every sign-in response goes through here. A password alone is not always a
  // session: an account with two-factor gets { mfa_required, mfa_token }, and a role
  // that requires it but hasn't set it up gets { mfa_enrollment_required, mfa_token }.
  // Either way the MfaDialog below finishes the job, and this promise resolves with
  // the signed-in user (or rejects if the user cancels).
  const completeLogin = useCallback((data) => {
    if (data?.mfa_required || data?.mfa_enrollment_required) {
      return new Promise((resolve, reject) => setMfaChallenge({
        kind: data.mfa_required ? 'verify' : 'enroll',
        mfaToken: data.mfa_token,
        resolve,
        reject,
      }))
    }
    return Promise.resolve(loginDirect(data.access_token, data.user))
  }, [loginDirect])

  const login = useCallback(async (email, password, opts = {}) => {
    const body = { email, password }
    if (opts.slug) body.company_slug = opts.slug
    if (opts.platformLogin) body.platform_login = true
    const res = await api.post('/auth/login', body)
    return completeLogin(res.data)
  }, [completeLogin])

  const register = useCallback(async (data) => {
    return (await api.post('/auth/register', data)).data
  }, [])

  const verifyOtp = useCallback(async (email, otp) => {
    const res = await api.post('/auth/verify-otp', { email, otp })
    return completeLogin(res.data)
  }, [completeLogin])

  const resendOtp = useCallback(async (email) => {
    return (await api.post('/auth/resend-otp', { email })).data
  }, [])

  const refreshToken = useCallback(async () => {
    try {
      const res = await api.post('/auth/refresh')
      const { access_token, user: u } = res.data
      const branding = applyCompanyBranding(access_token)
      const enrichedUser = { ...u, ...branding }
      localStorage.setItem('hireflow_token', access_token)
      localStorage.setItem('hireflow_user', JSON.stringify(enrichedUser))
      setToken(access_token)
      setUser(enrichedUser)
      return enrichedUser
    } catch {
      return null
    }
  }, [])

  // The cached user is written at login and never again — the axios interceptor's
  // proactive refresh rewrites hireflow_token only. So a name or email changed
  // after sign-in stays stale in the sidebar until the next login. Any page that
  // fetches fresh user data from the server can hand it back here.
  //
  // MERGE, never replace: the cached object carries the JWT-derived branding keys
  // (company_logo, company_color, company_slug, …) that /auth/me does not return,
  // and a blind overwrite would blank a tenant's sidebar colour and logo.
  const setUserFromServer = useCallback((fresh) => {
    if (!fresh) return null
    let merged = null
    setUser(prev => {
      merged = { ...(prev || {}), ...fresh }
      try { localStorage.setItem('hireflow_user', JSON.stringify(merged)) } catch { /* private mode */ }
      return merged
    })
    return merged
  }, [])

  const logout = useCallback(() => {
    // Revoke on the server too — otherwise the token stays valid until it expires.
    // Plain axios with the token captured now: the shared client would read
    // localStorage after it is cleared below, and redirect on a 401.
    const current = localStorage.getItem('hireflow_token')
    if (current) {
      axios.post('/api/v1/auth/logout', {}, { headers: { Authorization: `Bearer ${current}` } })
        .catch(() => { /* already revoked or offline — the local sign-out still happens */ })
    }
    localStorage.removeItem('hireflow_token')
    localStorage.removeItem('hireflow_user')
    setToken(null)
    setUser(null)
  }, [])

  return (
    <AuthContext.Provider value={{ user, token, login, loginDirect, completeLogin, register, verifyOtp, resendOtp, logout, refreshToken, setUserFromServer, isAuthenticated: !!token }}>
      {children}
      {mfaChallenge && (
        <MfaDialog
          kind={mfaChallenge.kind}
          mfaToken={mfaChallenge.mfaToken}
          onDone={(data) => {
            const u = loginDirect(data.access_token, data.user)
            mfaChallenge.resolve(u)
            setMfaChallenge(null)
          }}
          onCancel={() => {
            const err = new Error('Sign-in cancelled.')
            err.response = { data: { detail: 'Sign-in cancelled.' } }
            mfaChallenge.reject(err)
            setMfaChallenge(null)
          }}
        />
      )}
    </AuthContext.Provider>
  )
}

export const useAuth = () => {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be inside AuthProvider')
  return ctx
}
