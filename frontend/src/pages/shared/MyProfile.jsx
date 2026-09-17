import { useEffect, useMemo, useState, useCallback } from 'react'
import { Link } from 'react-router-dom'
import {
  AlertTriangle, BadgeCheck, Building2, Check, Minus, RefreshCw, Search,
} from 'lucide-react'
import api from '../../api/client'
import { useAuth } from '../../context/AuthContext'
import Header from '../../components/Header'
import { Card } from './profile/ProfileFields'
import AccountDetailsCard from './profile/AccountDetailsCard'
import ChangePasswordCard from './profile/ChangePasswordCard'
import HRActivitySections from './profile/HRActivitySections'

const ROLE_LABELS = {
  superadmin: 'Super Admin', admin: 'Admin', hr: 'HR',
  interviewer: 'Interviewer', candidate: 'Candidate',
}

const ROLE_BADGE = {
  superadmin: 'badge-amber', admin: 'badge-red', hr: 'badge-green',
  interviewer: 'badge-blue', candidate: 'badge-purple',
}

// Why a role's permission list looks the way it does. Two of the five roles
// hold permissions in only 4 of the 18 areas, which reads as a broken page
// unless the page says out loud that the gap is deliberate.
const ROLE_NOTES = {
  superadmin: 'Super Admin is a platform-operations role. It manages companies, support and system health across the whole platform, and deliberately does not carry day-to-day recruiting permissions such as posting jobs or managing users inside a company — those belong to each company’s own Admin and HR accounts.',
  admin: 'Admin is the widest role inside a company. It covers recruiting, users, departments, email and analytics for your company only — platform-level operations such as managing other companies are reserved for Super Admin.',
  hr: 'HR covers the day-to-day recruiting pipeline: jobs, candidates, interviews, offers and the talent pool for your company. Company-wide administration such as creating users or changing email settings is reserved for Admin.',
  interviewer: 'Interviewer is a focused role. It covers the candidates and interviews assigned to you, the question bank and test setup — it intentionally carries no job-posting, offer or user-management permissions.',
  candidate: 'Candidate covers your own profile, applications, interviews and offers. Everything here is scoped to you — you cannot see other candidates or any company-side data.',
}

const slugifyGroup = (g) => 'mp-g-' + g.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '')

export default function MyProfile() {
  const { user, setUserFromServer } = useAuth()
  const [me, setMe] = useState(null)
  const [perms, setPerms] = useState(null)
  const [loading, setLoading] = useState(true)
  const [meError, setMeError] = useState(false)
  const [permError, setPermError] = useState(false)
  const [reloadKey, setReloadKey] = useState(0)

  const [query, setQuery] = useState('')
  const [grantedOnly, setGrantedOnly] = useState(true)

  useEffect(() => {
    // The 401 interceptor in api/client.js hard-redirects to /login, so guard
    // every setState against landing after this component is gone.
    let alive = true
    setLoading(true)
    Promise.allSettled([api.get('/auth/me'), api.get('/auth/my-permissions')])
      .then(([meRes, permRes]) => {
        if (!alive) return
        if (meRes.status === 'fulfilled') {
          setMe(meRes.value.data)
          setMeError(false)
          // The sidebar reads a login-time snapshot from localStorage that is
          // never refreshed. Hand it the live values so the two don't disagree.
          setUserFromServer?.({
            first_name: meRes.value.data.first_name,
            last_name: meRes.value.data.last_name,
            email: meRes.value.data.email,
            role: meRes.value.data.role,
            phone: meRes.value.data.phone,
            is_active: meRes.value.data.is_active,
          })
        } else setMeError(true)

        if (permRes.status === 'fulfilled') { setPerms(permRes.value.data); setPermError(false) }
        else setPermError(true)
      })
      .finally(() => { if (alive) setLoading(false) })
    return () => { alive = false }
  }, [reloadKey, setUserFromServer])

  const retry = useCallback(() => setReloadKey(k => k + 1), [])

  // Fall back to the cached user so the identity card always renders something.
  const identity = me || user || {}
  const role = (identity.role || '').toLowerCase()
  const displayName = [identity.first_name, identity.last_name].filter(Boolean).join(' ')
    || identity.email?.split('@')[0] || 'User'
  const initial = (identity.first_name?.[0] || identity.email?.[0] || '?').toUpperCase()
  // Server role decides. A stale cached role would otherwise keep showing HR
  // sections to someone who was just demoted.
  const isHR = (me?.role || user?.role || '').toLowerCase() === 'hr'

  const visibleGroups = useMemo(() => {
    if (!perms) return []
    const q = query.trim().toLowerCase()
    return perms.groups
      .map(g => ({
        ...g,
        permissions: g.permissions.filter(p => {
          if (grantedOnly && !p.granted) return false
          if (!q) return true
          // Label and slug only. Endpoints are no longer shown, so matching on
          // them would return rows with no visible reason for matching.
          return p.label.toLowerCase().includes(q)
            || p.slug.toLowerCase().includes(q)
        }),
      }))
      .filter(g => g.permissions.length > 0)
  }, [perms, query, grantedOnly])

  const jumpTo = (group) => {
    document.getElementById(slugifyGroup(group))?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }

  if (loading) {
    return (
      <>
        <Header title="My Profile" subtitle="Your account and what your role can do" />
        <Card><p className="text-muted">Loading your profile&hellip;</p></Card>
      </>
    )
  }

  return (
    <>
      <Header title="My Profile" subtitle="Your account and what your role can do" />

      {/* A — identity */}
      <Card>
        <div style={{ display: 'flex', alignItems: 'center', gap: 18, flexWrap: 'wrap' }}>
          <div style={{
            width: 56, height: 56, flexShrink: 0, borderRadius: 14,
            background: 'var(--accent-light)', color: 'var(--accent-primary)',
            display: 'grid', placeItems: 'center', fontWeight: 700,
            fontSize: '1.3rem', fontFamily: 'var(--font-display)',
          }}>{initial}</div>

          <div style={{ minWidth: 0, flex: 1 }}>
            <div style={{ fontFamily: 'var(--font-display)', fontSize: '1.4rem', fontWeight: 600, color: 'var(--text-primary)', lineHeight: 1.2 }}>
              {displayName}
            </div>
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: '0.78rem', color: 'var(--text-tertiary)', marginTop: 3, wordBreak: 'break-all' }}>
              {identity.email || '—'}
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 10, flexWrap: 'wrap' }}>
              <span className={`badge ${ROLE_BADGE[role] || 'badge-neutral'}`}>
                {ROLE_LABELS[role] || identity.role || 'Unknown role'}
              </span>
              {me && (me.email_verified
                ? <span className="badge badge-green"><BadgeCheck size={12} style={{ marginRight: 4, verticalAlign: '-2px' }} />Verified</span>
                : <span className="badge badge-amber">Unverified</span>)}
              {me && !me.is_active && <span className="badge badge-red">Inactive</span>}
              {me?.company_name && (
                <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontSize: '0.78rem', color: 'var(--text-secondary)' }}>
                  <Building2 size={13} /> {me.company_name}
                </span>
              )}
            </div>
          </div>

          {role === 'candidate' && (
            <Link to="/candidate/profile" className="btn btn-secondary btn-sm" style={{ textDecoration: 'none' }}>
              Edit full profile &rarr;
            </Link>
          )}
        </div>

        {meError && (
          <div style={{ marginTop: 14, padding: '10px 12px', borderRadius: 8, background: 'var(--bg-tertiary)', fontSize: '0.8rem', color: 'var(--text-secondary)', display: 'flex', alignItems: 'center', gap: 8 }}>
            <AlertTriangle size={14} />
            Showing cached details &mdash; we couldn&rsquo;t reach the server.
            <button className="btn btn-ghost btn-sm" onClick={retry} style={{ marginLeft: 'auto' }}>
              <RefreshCw size={13} style={{ marginRight: 5, verticalAlign: '-2px' }} />Retry
            </button>
          </div>
        )}
      </Card>

      {/* HR-only: their own work. Requested on the cached role (available
          synchronously, so this stays one batch), rendered on the server role. */}
      <HRActivitySections enabled={isHR} />

      {/* B — account details, editable */}
      {me && <AccountDetailsCard me={me} onUpdated={setMe} />}

      {/* B3 — security */}
      {me && <ChangePasswordCard />}

      {/* C/D/E — permissions */}
      {permError && (
        <Card>
          <div className="empty-state">
            <AlertTriangle size={28} style={{ color: 'var(--text-tertiary)' }} />
            <p>We couldn&rsquo;t load your permission details.</p>
            <button className="btn btn-secondary btn-sm" onClick={retry}>
              <RefreshCw size={13} style={{ marginRight: 5, verticalAlign: '-2px' }} />Retry
            </button>
          </div>
        </Card>
      )}

      {perms && perms.total === 0 && (
        <Card>
          <div className="empty-state"><p>Permission details are unavailable right now.</p></div>
        </Card>
      )}

      {perms && perms.total > 0 && (
        <>
          <div className="stats-grid" style={{ marginBottom: 18 }}>
            <div className="stat-card">
              <div className="stat-label">Your role</div>
              <div className="stat-value">{ROLE_LABELS[role] || perms.role || '—'}</div>
            </div>
            <div className="stat-card">
              <div className="stat-label">Permissions granted</div>
              <div className="stat-value">{perms.granted_count}</div>
            </div>
            <div className="stat-card">
              <div className="stat-label">Total in catalog</div>
              <div className="stat-value">{perms.total}</div>
            </div>
            <div className="stat-card">
              <div className="stat-label">Areas with access</div>
              <div className="stat-value">{perms.granted_group_count}<span style={{ fontSize: '0.5em', color: 'var(--text-tertiary)' }}> / {perms.group_count}</span></div>
            </div>
          </div>

          <div style={{
            background: perms.known_role ? 'var(--accent-light)' : '#fdf4e3',
            borderLeft: `3px solid ${perms.known_role ? 'var(--accent-primary)' : '#d99323'}`,
            borderRadius: 8, padding: '14px 16px', marginBottom: 18,
            fontSize: '0.85rem', lineHeight: 1.55, color: 'var(--text-secondary)',
          }}>
            {perms.known_role
              ? ROLE_NOTES[role]
              : `Your account role “${perms.role || 'unknown'}” is not in the permission catalog, so nothing can be listed below. What you can actually do is controlled by the server, not by this page — contact your administrator if something looks wrong.`}
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: '220px 1fr', gap: 24, alignItems: 'start' }} className="mp-layout">
            {/* Sticky group rail — a table of contents, so 100+ rows stay navigable */}
            <div className="mp-rail" style={{ position: 'sticky', top: 'calc(var(--header-height) + 24px)' }}>
              <div style={{ fontSize: '0.7rem', fontWeight: 700, letterSpacing: '0.05em', textTransform: 'uppercase', color: 'var(--text-tertiary)', padding: '0 10px 8px' }}>
                Areas
              </div>
              {perms.groups.map(g => (
                <button
                  key={g.group}
                  className="mp-rail-item"
                  onClick={() => jumpTo(g.group)}
                  style={{ opacity: g.granted_count === 0 ? 0.5 : 1 }}
                >
                  <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{g.group}</span>
                  <span style={{ fontFamily: 'var(--font-mono)', fontSize: '0.68rem', color: 'var(--text-tertiary)', flexShrink: 0 }}>
                    {g.granted_count}/{g.total}
                  </span>
                </button>
              ))}
            </div>

            <div style={{ minWidth: 0 }}>
              <div className="flex-between" style={{ gap: 12, flexWrap: 'wrap', marginBottom: 16 }}>
                <div style={{ position: 'relative', flex: '1 1 240px', minWidth: 0 }}>
                  <Search size={15} style={{ position: 'absolute', left: 12, top: '50%', transform: 'translateY(-50%)', color: 'var(--text-tertiary)' }} />
                  <input
                    className="input"
                    style={{ paddingLeft: 34 }}
                    placeholder="Search permissions..."
                    value={query}
                    onChange={e => setQuery(e.target.value)}
                    aria-label="Search permissions"
                  />
                </div>
                <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                  <button className={`btn btn-sm ${grantedOnly ? 'btn-primary' : 'btn-secondary'}`} onClick={() => setGrantedOnly(true)}>
                    Granted ({perms.granted_count})
                  </button>
                  <button className={`btn btn-sm ${grantedOnly ? 'btn-secondary' : 'btn-primary'}`} onClick={() => setGrantedOnly(false)}>
                    All ({perms.total})
                  </button>
                </div>
              </div>

              {visibleGroups.length === 0 ? (
                <Card>
                  <div className="empty-state">
                    <p>{query ? `No permissions match “${query}”.` : 'Nothing to show with the current filter.'}</p>
                  </div>
                </Card>
              ) : visibleGroups.map(g => (
                <Card key={g.group} style={{ padding: 0 }}>
                  <div id={slugifyGroup(g.group)} style={{
                    display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                    padding: '14px 20px', borderBottom: '1px solid var(--border-subtle)',
                    scrollMarginTop: 'calc(var(--header-height) + 16px)',
                  }}>
                    <span style={{ fontFamily: 'var(--font-display)', fontSize: '1.02rem', fontWeight: 600, color: 'var(--text-primary)' }}>
                      {g.group}
                    </span>
                    <span className={`badge ${g.granted_count === g.total ? 'badge-green' : g.granted_count === 0 ? 'badge-neutral' : 'badge-blue'}`}>
                      {g.granted_count} / {g.total}
                    </span>
                  </div>

                  <div style={{ padding: '4px 20px 12px' }}>
                    {g.permissions.map((p, i) => (
                      <div
                        key={p.slug}
                        style={{
                          display: 'flex', alignItems: 'flex-start', gap: 10, padding: '9px 0',
                          borderBottom: i === g.permissions.length - 1 ? 'none' : '1px solid var(--border-subtle)',
                          opacity: p.granted ? 1 : 0.5,
                        }}
                      >
                        <span style={{ flexShrink: 0, marginTop: 2 }}>
                          {p.granted
                            ? <Check size={15} style={{ color: 'var(--status-success)' }} />
                            : <Minus size={15} style={{ color: 'var(--text-tertiary)' }} />}
                        </span>
                        <div style={{ minWidth: 0, flex: 1 }}>
                          <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                            <span style={{ fontSize: '0.86rem', color: 'var(--text-primary)' }}>{p.label}</span>
                            {p.conflict && (
                              <span className="badge badge-amber" title={`Currently gated as: ${p.current_gate}`}>
                                Inconsistent
                              </span>
                            )}
                          </div>
                        </div>
                      </div>
                    ))}
                  </div>
                </Card>
              ))}

              <p style={{ fontSize: '0.75rem', color: 'var(--text-tertiary)', lineHeight: 1.6, margin: '4px 2px 32px' }}>
                This reflects the platform&rsquo;s permission matrix. Individual actions are always re-checked by the server.
                A few permissions are marked <em>inconsistent</em> &mdash; their endpoints are currently gated differently from one another.
              </p>
            </div>
          </div>
        </>
      )}

      <style>{`
        /* Used by the account + password cards; kept here because this page
           deliberately has exactly one <style> block. */
        .mp-edit-field {
          display: flex;
          flex-direction: column;
          gap: 5px;
        }
        .mp-edit-field > span {
          font-size: 0.7rem;
          font-weight: 600;
          letter-spacing: 0.04em;
          text-transform: uppercase;
          color: var(--text-tertiary);
        }
        .mp-rail-item {
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 8px;
          width: 100%;
          padding: 7px 10px;
          border: none;
          background: none;
          border-radius: 8px;
          cursor: pointer;
          text-align: left;
          font-family: var(--font-body);
          font-size: 0.8rem;
          color: var(--text-secondary);
          transition: background 0.15s ease, color 0.15s ease;
        }
        .mp-rail-item:hover {
          background: var(--bg-tertiary);
          color: var(--text-primary);
        }
        .mp-rail-item:focus-visible {
          outline: 2px solid var(--accent-primary);
          outline-offset: 2px;
        }
        @media (max-width: 900px) {
          .mp-layout { grid-template-columns: 1fr !important; }
          .mp-rail { display: none; }
        }
      `}</style>
    </>
  )
}
