import axios from 'axios'

const api = axios.create({
  baseURL: '/api/v1',
  headers: { 'Content-Type': 'application/json' },
})

const decodeExpiry = (token) => {
  try { return JSON.parse(atob(token.split('.')[1])).exp * 1000 } catch { return null }
}

// Shared promise so concurrent requests don't each trigger their own refresh
let refreshPromise = null

api.interceptors.request.use(async (config) => {
  const token = localStorage.getItem('hireflow_token')
  if (!token) return config

  const expiry = decodeExpiry(token)
  const TEN_MIN = 10 * 60 * 1000

  if (expiry && Date.now() > expiry - TEN_MIN) {
    // Token expires within 10 minutes — refresh proactively
    if (!refreshPromise) {
      refreshPromise = axios
        .post('/api/v1/auth/refresh', {}, {
          headers: { Authorization: `Bearer ${token}` },
        })
        .then((r) => {
          const t = r.data.access_token
          localStorage.setItem('hireflow_token', t)
          return t
        })
        .catch(() => null)
        .finally(() => { refreshPromise = null })
    }
    const newToken = await refreshPromise
    config.headers.Authorization = `Bearer ${newToken || token}`
    return config
  }

  config.headers.Authorization = `Bearer ${token}`
  return config
})

api.interceptors.response.use(
  (res) => res,
  async (err) => {
    if (err.response?.status === 401) {
      localStorage.removeItem('hireflow_token')
      localStorage.removeItem('hireflow_user')
      window.location.href = '/login'
      return Promise.reject(err)
    }

    // Auto-retry transient failures ONCE on idempotent reads.
    // Covers the "backend mid-reload" / "Vite proxy momentary hiccup" case that
    // otherwise leaves dashboards permanently empty (silent .catch + no re-fetch).
    const cfg = err.config || {}
    const method = (cfg.method || 'get').toLowerCase()
    const isRead = method === 'get'
    const isTransient = !err.response || err.response.status >= 500
    if (isRead && isTransient && !cfg._retried) {
      cfg._retried = true
      await new Promise(r => setTimeout(r, 600))
      return api(cfg)
    }

    return Promise.reject(err)
  }
)

export default api
