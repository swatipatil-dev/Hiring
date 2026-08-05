import { useEffect, useState } from 'react'
import api from '../api/client'

// api baseURL is already '/api/v1' — strip that prefix from server-provided URLs.
const toApiPath = (url) => (url || '').replace(/^\/api\/v1/, '')

const SEV_COLORS = {
  CRITICAL: { fg: '#b91c1c', bg: '#fee2e2' },
  HIGH:     { fg: '#c2410c', bg: '#ffedd5' },
  MEDIUM:   { fg: '#b45309', bg: '#fef3c7' },
  LOW:      { fg: '#3f6212', bg: '#ecfccb' },
}

const decisionColor = (d) =>
  d === 'NORMAL' ? { fg: '#15803d', bg: '#dcfce7' }
  : d === 'WARNING' ? { fg: '#92400e', bg: '#fef3c7' }
  : d === 'SUSPICIOUS' ? { fg: '#c2410c', bg: '#ffedd5' }
  : { fg: '#b91c1c', bg: '#fee2e2' }  // CHEATING

// Fetches a protected evidence image as an authorized blob and shows a thumbnail.
function EvidenceThumb({ url }) {
  const [src, setSrc] = useState(null)
  const [err, setErr] = useState(false)
  useEffect(() => {
    let revoked = false, objUrl = null
    ;(async () => {
      try {
        const r = await api.get(toApiPath(url), { responseType: 'blob' })
        if (revoked) return
        objUrl = URL.createObjectURL(r.data)
        setSrc(objUrl)
      } catch { setErr(true) }
    })()
    return () => { revoked = true; if (objUrl) URL.revokeObjectURL(objUrl) }
  }, [url])
  if (err) return <span style={{ fontSize: 10, color: '#9ca3af' }}>no image</span>
  if (!src) return <span style={{ fontSize: 10, color: '#9ca3af' }}>…</span>
  return <img src={src} alt="evidence" style={{ width: 64, height: 48, objectFit: 'cover', borderRadius: 4, border: '1px solid #e5e7eb' }} />
}

function IdentityBadge({ s }) {
  const state = s.identity_verified ? { t: 'Identity Verified', fg: '#15803d', bg: '#dcfce7' }
    : s.live_enrolled ? { t: 'Live-enrolled (unvetted)', fg: '#b45309', bg: '#fef3c7' }
    : { t: 'Identity Unverified', fg: '#6b7280', bg: '#f3f4f6' }
  return <span style={{ fontSize: 11, fontWeight: 700, padding: '2px 8px', borderRadius: 6, color: state.fg, background: state.bg }}>{state.t}</span>
}

/**
 * Proctoring report for a given interview/test. Reads persisted sessions +
 * violations via GET /proctor/results/{interviewId} (works after the test ends).
 */
export default function ProctoringReport({ interviewId }) {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  useEffect(() => {
    if (!interviewId) return
    let cancelled = false
    setLoading(true); setError(null)
    ;(async () => {
      try {
        const r = await api.get(`/proctor/results/${interviewId}`)
        if (!cancelled) setData(r.data)
      } catch (e) {
        if (!cancelled) setError(e?.response?.data?.detail || 'Failed to load proctoring report')
      } finally {
        if (!cancelled) setLoading(false)
      }
    })()
    return () => { cancelled = true }
  }, [interviewId])

  if (loading) return <div style={{ padding: 12, color: '#6b7280', fontSize: 13 }}>Loading proctoring report…</div>
  if (error) return <div style={{ padding: 12, color: '#b91c1c', fontSize: 13 }}>{error}</div>
  if (!data || data.session_count === 0)
    return <div style={{ padding: 12, color: '#6b7280', fontSize: 13 }}>No proctoring sessions recorded for this test.</div>

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      {data.sessions.map((s) => {
        const dc = decisionColor(s.decision)
        return (
          <div key={s.session_id} style={{ border: '1px solid #e5e7eb', borderRadius: 10, padding: 14 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap', marginBottom: 10 }}>
              <span style={{ fontSize: 12, fontWeight: 700, padding: '3px 10px', borderRadius: 6, color: dc.fg, background: dc.bg }}>
                {s.decision}
              </span>
              <span style={{ fontSize: 13, fontWeight: 700 }}>
                Score {Math.round(s.cheating_score || 0)}{s.max_cheating_score != null ? ` (peak ${Math.round(s.max_cheating_score)})` : ''}
              </span>
              <IdentityBadge s={s} />
              <span style={{ fontSize: 12, color: '#6b7280' }}>
                {s.total_violations} violation{s.total_violations === 1 ? '' : 's'}
              </span>
              <span style={{ marginLeft: 'auto', fontSize: 11, color: '#9ca3af' }}>
                {s.started_at ? new Date(s.started_at).toLocaleString() : ''}
              </span>
            </div>

            {/* Module scores */}
            <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap', marginBottom: 10 }}>
              {Object.entries(s.module_scores).map(([k, v]) => (
                <span key={k} style={{ fontSize: 11, color: '#374151' }}>
                  <b style={{ textTransform: 'capitalize' }}>{k}</b>: {Math.round(v || 0)}
                </span>
              ))}
            </div>

            {/* Violations table */}
            {s.violations.length > 0 ? (
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
                <thead>
                  <tr style={{ textAlign: 'left', color: '#6b7280' }}>
                    <th style={{ padding: '4px 6px' }}>Type</th>
                    <th style={{ padding: '4px 6px' }}>Severity</th>
                    <th style={{ padding: '4px 6px' }}>Module</th>
                    <th style={{ padding: '4px 6px' }}>Time</th>
                    <th style={{ padding: '4px 6px' }}>Evidence</th>
                  </tr>
                </thead>
                <tbody>
                  {s.violations.map((v) => {
                    const sc = SEV_COLORS[v.severity] || SEV_COLORS.LOW
                    return (
                      <tr key={v.id} style={{ borderTop: '1px solid #f3f4f6' }}>
                        <td style={{ padding: '6px 6px', fontWeight: 600 }}>{v.type.replaceAll('_', ' ')}</td>
                        <td style={{ padding: '6px 6px' }}>
                          <span style={{ fontSize: 10, fontWeight: 700, padding: '1px 7px', borderRadius: 5, color: sc.fg, background: sc.bg }}>{v.severity}</span>
                        </td>
                        <td style={{ padding: '6px 6px', color: '#6b7280' }}>{v.module}</td>
                        <td style={{ padding: '6px 6px', color: '#6b7280' }}>{v.timestamp ? new Date(v.timestamp).toLocaleTimeString() : '—'}</td>
                        <td style={{ padding: '6px 6px' }}>{v.evidence_url ? <EvidenceThumb url={v.evidence_url} /> : <span style={{ color: '#d1d5db' }}>—</span>}</td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            ) : (
              <div style={{ fontSize: 12, color: '#9ca3af' }}>No flagged violations.</div>
            )}
          </div>
        )
      })}
    </div>
  )
}
