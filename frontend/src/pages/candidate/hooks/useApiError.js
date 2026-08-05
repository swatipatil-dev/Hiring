import { useCallback, useState } from 'react'

// Lightweight replacement for the `.catch(() => {})` pattern that was
// hiding network failures across CandidateDashboard. Errors are logged to the
// console (with context so they're greppable) and kept in a small ring so the
// UI can show a banner instead of a silent blank section.
//
// Usage:
//   const { errors, report, clear } = useApiError()
//   api.get('/candidate/jobs').then(...).catch(report('Load jobs'))
//   {errors.length > 0 && <ApiErrorBanner errors={errors} onDismiss={clear} />}

const MAX_ERRORS = 5

function describe(err) {
  if (!err) return 'Unknown error'
  if (err.response) {
    const status = err.response.status
    const detail = err.response.data?.detail || err.response.data?.message
    return detail ? `${status} — ${detail}` : `Request failed (${status})`
  }
  if (err.request) return 'Network error — server unreachable'
  return err.message || String(err)
}

export default function useApiError() {
  const [errors, setErrors] = useState([])

  // Returns a .catch-friendly handler that captures context for the log.
  const report = useCallback((context) => (err) => {
    const message = describe(err)
    // Surface in devtools so engineers can investigate.
    // eslint-disable-next-line no-console
    console.error(`[candidate dashboard] ${context}:`, err)
    setErrors(prev => {
      const next = [...prev, { context, message, at: Date.now() }]
      return next.slice(-MAX_ERRORS)
    })
  }, [])

  const clear = useCallback(() => setErrors([]), [])

  return { errors, report, clear }
}
