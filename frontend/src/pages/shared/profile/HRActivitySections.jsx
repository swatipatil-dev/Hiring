import { useEffect, useState } from 'react'
import { Activity, ArrowRight } from 'lucide-react'
import api from '../../../api/client'
import { Card, CardHeader } from './ProfileFields'

const fmtWhen = (iso) => {
  if (!iso) return ''
  const then = new Date(iso)
  if (isNaN(then)) return ''
  const mins = Math.round((Date.now() - then.getTime()) / 60000)
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins}m ago`
  if (mins < 1440) return `${Math.round(mins / 60)}h ago`
  const days = Math.round(mins / 1440)
  if (days < 30) return `${days}d ago`
  return then.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
}

const prettyStage = (s) => (s ? s.replace(/_/g, ' ') : null)

function Tiles({ items }) {
  return (
    <div className="stats-grid" style={{ marginBottom: 18 }}>
      {items.map(([label, value]) => (
        <div className="stat-card" key={label}>
          <div className="stat-label">{label}</div>
          <div className="stat-value">{value}</div>
        </div>
      ))}
    </div>
  )
}

/**
 * The three HR-only sections.
 *
 * Owns its own fetch so the page's existing effect stays untouched, and so a
 * slow or failing /hr/my-activity never delays the rest of the profile.
 *
 * On any failure the sections disappear rather than rendering an error card: a
 * 404 here is exactly what a backend-behind-frontend deploy looks like from the
 * browser, and that must not put a red box on every HR's profile page.
 */
export default function HRActivitySections({ enabled }) {
  const [data, setData] = useState(null)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    if (!enabled) return
    let alive = true
    api.get('/hr/my-activity')
      .then(r => { if (alive) { setData(r.data); setFailed(false) } })
      .catch(() => { if (alive) setFailed(true) })
    return () => { alive = false }
  }, [enabled])

  if (!enabled || failed || !data) return null

  const g = data.at_a_glance || {}
  const w = data.last_30_days || {}
  const feed = data.recent_activity || []

  return (
    <>
      <Tiles items={[
        ['Your job postings', g.jobs ?? 0],
        ['Active jobs', g.active_jobs ?? 0],
        ['Interviews', g.interviews ?? 0],
        ['Offers', g.offers ?? 0],
      ]} />

      <div style={{ fontSize: '0.72rem', fontWeight: 700, letterSpacing: '0.05em', textTransform: 'uppercase', color: 'var(--text-tertiary)', margin: '0 2px 10px' }}>
        Last {w.days ?? 30} days
      </div>
      <Tiles items={[
        ['Jobs created', w.jobs_created ?? 0],
        ['New candidates', w.candidates ?? 0],
        ['Interviews scheduled', w.interviews_scheduled ?? 0],
        ['Offers sent', w.offers_sent ?? 0],
      ]} />

      <Card>
        <CardHeader icon={Activity} title="Recent activity" />

        {feed.length === 0 ? (
          <p className="text-muted" style={{ fontSize: '0.85rem' }}>
            No candidate stage changes yet.
          </p>
        ) : (
          <div>
            {feed.map((row, i) => (
              <div key={row.id} style={{
                display: 'flex', alignItems: 'baseline', gap: 10, padding: '9px 0', flexWrap: 'wrap',
                borderBottom: i === feed.length - 1 ? 'none' : '1px solid var(--border-subtle)',
              }}>
                <span style={{ fontSize: '0.86rem', color: 'var(--text-primary)', fontWeight: 500 }}>
                  {row.candidate_name || 'A candidate'}
                </span>
                <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontSize: '0.78rem', color: 'var(--text-secondary)' }}>
                  {prettyStage(row.from_stage) && <>{prettyStage(row.from_stage)}<ArrowRight size={11} /></>}
                  <span style={{ color: 'var(--text-primary)' }}>{prettyStage(row.to_stage)}</span>
                </span>
                {row.job_title && (
                  <span style={{ fontSize: '0.75rem', color: 'var(--text-tertiary)' }}>{row.job_title}</span>
                )}
                <span style={{ marginLeft: 'auto', fontSize: '0.72rem', color: 'var(--text-tertiary)', whiteSpace: 'nowrap' }}>
                  {fmtWhen(row.created_at)}
                </span>
              </div>
            ))}
          </div>
        )}

        <p style={{ fontSize: '0.72rem', color: 'var(--text-tertiary)', lineHeight: 1.5, margin: '12px 0 0' }}>
          Bulk stage moves made by automated screening aren&rsquo;t attributed to a
          person, so they don&rsquo;t appear here.
        </p>
      </Card>
    </>
  )
}
