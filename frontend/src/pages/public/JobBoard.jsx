import { useEffect, useState } from 'react'
import { useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { MapPin, Briefcase, Clock, DollarSign, ChevronRight, Search } from 'lucide-react'
import { exchangeLegacyKey, getStoredSlug, storeSlug } from '../../utils/tenant'

const API_BASE = '/api/v1'

export default function JobBoard() {
  const navigate = useNavigate()
  const params   = useParams()
  const [searchParams] = useSearchParams()

  const [slug, setSlug]         = useState(params.companySlug || searchParams.get('c') || '')
  const [company, setCompany]   = useState(null)
  const [jobs, setJobs]         = useState([])
  const [loading, setLoading]   = useState(true)
  const [error, setError]       = useState('')
  const [search, setSearch]     = useState('')
  const [selected, setSelected] = useState(null)   // job detail modal

  useEffect(() => {
    let cancelled = false

    ;(async () => {
      let active = params.companySlug || searchParams.get('c') || ''
      if (!active && searchParams.get('key')) {
        // Legacy link: swap the secret for the slug and scrub it from the address bar.
        active = (await exchangeLegacyKey(searchParams, sl => `/${sl}/jobs`)) || ''
      }
      if (!active) active = getStoredSlug() || ''
      if (cancelled) return
      setSlug(active)

      if (!active) {
        setError('This job board link is missing its company name.')
        setLoading(false)
        return
      }

      try {
        const r = await fetch(`${API_BASE}/public/jobs?c=${encodeURIComponent(active)}`)
        if (!r.ok) {
          // Only a lookup failure means the company is actually wrong. A 5xx or proxy
          // error means the server is unreachable — don't blame the link.
          if (r.status === 401 || r.status === 403 || r.status === 404) {
            throw new Error('This company page is no longer available.')
          }
          throw new Error("Couldn't reach the server. Please refresh and try again.")
        }
        const data = await r.json()
        if (cancelled) return
        const c = data.company
        setCompany(c)
        // Stored only once the company actually resolved. The old code wrote the
        // identifier to localStorage before validating it, so a bad value stuck and
        // poisoned every later redirect. Never for the platform company: it has no
        // slug portal, so remembering it would send later redirects to a dead end.
        if (!c?.is_platform) storeSlug(c?.slug || active)
        setJobs(data.jobs || [])
        // Apply company branding to the public page
        if (c?.name) document.title = `${c.name} — Careers`
        if (c?.favicon_url) {
          let link = document.querySelector("link[rel~='icon']")
          if (!link) { link = document.createElement('link'); link.rel = 'icon'; document.head.appendChild(link) }
          link.href = c.favicon_url
        }
        if (c?.font_family) {
          const id = 'jb-font-link'
          if (!document.getElementById(id)) {
            const l = document.createElement('link'); l.id = id; l.rel = 'stylesheet'
            document.head.appendChild(l)
          }
          document.getElementById('jb-font-link').href =
            `https://fonts.googleapis.com/css2?family=${encodeURIComponent(c.font_family)}:wght@400;500;600;700&display=swap`
          document.documentElement.style.setProperty('--jb-font', `'${c.font_family}', system-ui, sans-serif`)
        }
      } catch (e) {
        if (!cancelled) setError(
          e.message === 'Failed to fetch'
            ? "Couldn't reach the server. Please refresh and try again."
            : e.message
        )
      } finally {
        if (!cancelled) setLoading(false)
      }
    })()

    return () => { cancelled = true }
  }, [params.companySlug, searchParams])

  const accent  = company?.primary_color || '#2b7cba'
  const filtered = jobs.filter(j =>
    !search || j.title.toLowerCase().includes(search.toLowerCase()) ||
    (j.location || '').toLowerCase().includes(search.toLowerCase())
  )

  const fmt = (min, max) => {
    if (!min && !max) return null
    const f = n => n >= 100000 ? `₹${(n/100000).toFixed(1)}L` : `₹${(n/1000).toFixed(0)}K`
    if (min && max) return `${f(min)} – ${f(max)}`
    return min ? `From ${f(min)}` : `Up to ${f(max)}`
  }

  const daysAgo = d => {
    const diff = Math.floor((Date.now() - new Date(d)) / 86400000)
    if (diff === 0) return 'Today'
    if (diff === 1) return 'Yesterday'
    if (diff < 30) return `${diff}d ago`
    return `${Math.floor(diff / 30)}mo ago`
  }

  // Same-tab navigation so the browser back-stack stays coherent for the
  // candidate:  career page → portal (login) → dashboard.  Pressing Back then
  // walks back the same way, and the career page is the static floor.
  // The platform company (Innomax) has no slug portal — its candidates use the
  // platform's own sign-in, which links to registration.
  const portalPath = (jobId) => company?.is_platform
    ? '/login'
    : `/${encodeURIComponent(slug)}/portal${jobId ? `?job_id=${jobId}` : ''}`
  const goApply = (jobId) => navigate(portalPath(jobId))
  const signInPath = company?.is_platform ? '/login' : `/${encodeURIComponent(slug)}`

  if (loading) return (
    <div className="jb-page">
      <div className="jb-skeleton-header" />
      <div className="jb-container">
        {[1,2,3].map(i => <div key={i} className="jb-skeleton-card" style={{ animationDelay: `${i*0.1}s` }} />)}
      </div>
      <Styles accent="#2b7cba" />
    </div>
  )

  if (error) return (
    <div className="jb-page jb-error-page">
      <div className="jb-error-box">
        <div className="jb-error-icon">⚠</div>
        <h2>Unable to load job board</h2>
        <p>{error}</p>
      </div>
      <Styles accent="#2b7cba" />
    </div>
  )

  return (
    <div className="jb-page">
      {/* Header */}
      <header className="jb-header" style={{ borderBottomColor: accent + '30' }}>
        <div className="jb-header-inner">
          <div className="jb-brand">
            {company?.logo_url
              ? <img src={company.logo_url} alt={company.name} className="jb-logo" onError={e => { e.target.style.display='none' }} />
              : <div className="jb-logo-fallback" style={{ background: accent }}>{(company?.name||'?')[0]}</div>
            }
            <div>
              <div className="jb-company-name">{company?.name}</div>
              <div className="jb-company-sub">Open Positions</div>
            </div>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <div className="jb-count" style={{ background: accent + '18', color: accent }}>
              {filtered.length} {filtered.length === 1 ? 'role' : 'roles'}
            </div>
            <a href={signInPath}
              style={{ background: accent, color: '#fff', padding: '8px 18px', borderRadius: 10, fontSize: '0.85rem', fontWeight: 700, textDecoration: 'none', whiteSpace: 'nowrap' }}>
              Sign In
            </a>
          </div>
        </div>
      </header>

      {/* Search */}
      {jobs.length > 3 && (
        <div className="jb-search-wrap">
          <div className="jb-search-inner">
            <Search size={15} className="jb-search-icon" />
            <input
              className="jb-search"
              placeholder="Search by title or location…"
              value={search}
              onChange={e => setSearch(e.target.value)}
              style={{ '--accent': accent }}
            />
          </div>
        </div>
      )}

      {/* Job list */}
      <main className="jb-container">
        {filtered.length === 0 ? (
          <div className="jb-empty">
            <Briefcase size={32} style={{ color: accent + '60', marginBottom: 12 }} />
            <div className="jb-empty-title">
              {search ? 'No matching positions' : 'No open positions right now'}
            </div>
            <div className="jb-empty-sub">Check back soon for new opportunities.</div>
          </div>
        ) : (
          <div className="jb-grid">
            {filtered.map((job, i) => (
              <div
                key={job.id}
                className="jb-card"
                style={{ animationDelay: `${i * 0.05}s`, '--accent': accent }}
                onClick={() => setSelected(job)}
              >
                <div className="jb-card-top">
                  <div className="jb-job-title">{job.title}</div>
                  {job.employment_type && (
                    <span className="jb-badge" style={{ background: accent + '15', color: accent }}>
                      {job.employment_type}
                    </span>
                  )}
                </div>

                <div className="jb-meta">
                  {job.location && (
                    <span className="jb-meta-item"><MapPin size={12} />{job.location}</span>
                  )}
                  {job.experience_years > 0 && (
                    <span className="jb-meta-item"><Clock size={12} />{job.experience_years}+ yrs exp</span>
                  )}
                  {fmt(job.salary_min, job.salary_max) && (
                    <span className="jb-meta-item"><DollarSign size={12} />{fmt(job.salary_min, job.salary_max)}</span>
                  )}
                </div>

                {job.description && (
                  <p className="jb-desc">{job.description}{job.description.length >= 200 ? '…' : ''}</p>
                )}

                {Array.isArray(job.required_skills) && job.required_skills.length > 0 && (
                  <div className="jb-skills">
                    {job.required_skills.slice(0, 5).map((s, idx) => (
                      <span key={idx} className="jb-skill">{s}</span>
                    ))}
                    {job.required_skills.length > 5 && (
                      <span className="jb-skill jb-skill-more">+{job.required_skills.length - 5}</span>
                    )}
                  </div>
                )}

                <div className="jb-card-footer">
                  <span className="jb-posted">{daysAgo(job.created_at)}</span>
                  <button
                    className="jb-apply-btn"
                    style={{ background: accent }}
                    onClick={e => { e.stopPropagation(); goApply(job.id) }}
                  >
                    Apply Now <ChevronRight size={13} />
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </main>

      {/* Job detail modal */}
      {selected && (
        <div className="jb-overlay" onClick={() => setSelected(null)}>
          <div className="jb-modal" onClick={e => e.stopPropagation()} style={{ '--accent': accent }}>
            <button className="jb-modal-close" onClick={() => setSelected(null)}>✕</button>
            <div className="jb-modal-header" style={{ borderBottomColor: accent + '25' }}>
              <div className="jb-modal-title">{selected.title}</div>
              {selected.employment_type && (
                <span className="jb-badge" style={{ background: accent + '15', color: accent }}>
                  {selected.employment_type}
                </span>
              )}
            </div>
            <div className="jb-modal-meta">
              {selected.location    && <span className="jb-meta-item"><MapPin size={13} />{selected.location}</span>}
              {selected.experience_years > 0 && <span className="jb-meta-item"><Clock size={13} />{selected.experience_years}+ yrs experience</span>}
              {fmt(selected.salary_min, selected.salary_max) && <span className="jb-meta-item"><DollarSign size={13} />{fmt(selected.salary_min, selected.salary_max)}</span>}
            </div>
            {selected.description && <p className="jb-modal-desc">{selected.description}</p>}
            {Array.isArray(selected.required_skills) && selected.required_skills.length > 0 && (
              <>
                <div className="jb-modal-section">Required Skills</div>
                <div className="jb-skills">
                  {selected.required_skills.map((s, i) => <span key={i} className="jb-skill">{s}</span>)}
                </div>
              </>
            )}
            <button
              className="jb-modal-apply"
              style={{ background: accent }}
              onClick={() => goApply(selected.id)}
            >
              Apply for this position <ChevronRight size={15} />
            </button>
          </div>
        </div>
      )}

      <footer className="jb-footer">
        <span>Powered by </span>
        <span style={{ color: accent, fontWeight: 700 }}>HireFlow</span>
      </footer>

      <Styles accent={accent} />
    </div>
  )
}

function Styles({ accent }) {
  return (
    <style>{`
      * { box-sizing: border-box; margin: 0; padding: 0; }

      .jb-page {
        min-height: 100vh;
        background: #f8fafc;
        font-family: 'Segoe UI', system-ui, -apple-system, sans-serif;
        color: #1a1d23;
      }

      /* Header */
      .jb-header {
        background: #fff;
        border-bottom: 2px solid #f0f0f0;
        position: sticky; top: 0; z-index: 10;
        box-shadow: 0 2px 8px rgba(0,0,0,0.04);
      }
      .jb-header-inner {
        max-width: 900px;
        margin: 0 auto;
        padding: 16px 24px;
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 16px;
      }
      .jb-brand { display: flex; align-items: center; gap: 14px; }
      .jb-logo  { height: 44px; width: auto; object-fit: contain; }
      .jb-logo-fallback {
        width: 44px; height: 44px; border-radius: 10px;
        display: grid; place-items: center;
        font-size: 1.2rem; font-weight: 800; color: #fff;
      }
      .jb-company-name { font-size: 1.05rem; font-weight: 700; color: #1a1d23; }
      .jb-company-sub  { font-size: 0.72rem; color: #9ca3af; margin-top: 1px; }
      .jb-count {
        padding: 5px 14px; border-radius: 20px;
        font-size: 0.78rem; font-weight: 700;
        white-space: nowrap;
      }

      /* Search */
      .jb-search-wrap {
        background: #fff;
        border-bottom: 1px solid #f0f0f0;
        padding: 12px 24px;
      }
      .jb-search-inner {
        max-width: 900px; margin: 0 auto;
        position: relative;
      }
      .jb-search-icon {
        position: absolute; left: 12px; top: 50%;
        transform: translateY(-50%); color: #9ca3af;
      }
      .jb-search {
        width: 100%; padding: 10px 14px 10px 36px;
        border: 1.5px solid #e5e7eb; border-radius: 10px;
        font-size: 0.88rem; outline: none; background: #f9fafb;
        transition: border-color 0.2s, box-shadow 0.2s;
      }
      .jb-search:focus {
        border-color: ${accent};
        box-shadow: 0 0 0 3px ${accent}20;
        background: #fff;
      }

      /* Container */
      .jb-container {
        max-width: 900px;
        margin: 0 auto;
        padding: 24px;
      }

      /* Grid */
      .jb-grid {
        display: grid;
        grid-template-columns: repeat(auto-fill, minmax(360px, 1fr));
        gap: 16px;
      }

      /* Card */
      .jb-card {
        background: #fff;
        border: 1.5px solid #e5e7eb;
        border-radius: 14px;
        padding: 20px;
        cursor: pointer;
        transition: all 0.2s cubic-bezier(0.16,1,0.3,1);
        animation: jb-card-in 0.4s cubic-bezier(0.16,1,0.3,1) both;
        display: flex; flex-direction: column; gap: 10px;
      }
      @keyframes jb-card-in {
        from { opacity: 0; transform: translateY(10px); }
        to   { opacity: 1; transform: translateY(0); }
      }
      .jb-card:hover {
        border-color: ${accent}60;
        box-shadow: 0 4px 20px ${accent}15;
        transform: translateY(-2px);
      }

      .jb-card-top {
        display: flex; align-items: flex-start;
        justify-content: space-between; gap: 10px;
      }
      .jb-job-title {
        font-size: 1rem; font-weight: 700; color: #1a1d23; line-height: 1.3;
      }
      .jb-badge {
        padding: 3px 10px; border-radius: 20px;
        font-size: 0.68rem; font-weight: 700;
        white-space: nowrap; flex-shrink: 0;
        text-transform: uppercase; letter-spacing: 0.04em;
      }

      .jb-meta {
        display: flex; flex-wrap: wrap; gap: 10px;
      }
      .jb-meta-item {
        display: flex; align-items: center; gap: 4px;
        font-size: 0.78rem; color: #6b7280;
      }

      .jb-desc {
        font-size: 0.82rem; color: #6b7280; line-height: 1.55;
      }

      .jb-skills {
        display: flex; flex-wrap: wrap; gap: 6px;
      }
      .jb-skill {
        padding: 3px 10px; border-radius: 20px;
        font-size: 0.7rem; font-weight: 600;
        background: #f3f4f6; color: #374151;
      }
      .jb-skill-more { background: #e5e7eb; color: #9ca3af; }

      .jb-card-footer {
        display: flex; align-items: center;
        justify-content: space-between;
        margin-top: auto; padding-top: 6px;
      }
      .jb-posted { font-size: 0.72rem; color: #9ca3af; }
      .jb-apply-btn {
        display: inline-flex; align-items: center; gap: 5px;
        padding: 8px 16px; border-radius: 8px; border: none;
        color: #fff; font-size: 0.82rem; font-weight: 700;
        cursor: pointer; transition: all 0.2s; flex-shrink: 0;
      }
      .jb-apply-btn:hover { filter: brightness(1.1); transform: translateX(2px); }

      /* Empty */
      .jb-empty {
        text-align: center; padding: 60px 20px;
        display: flex; flex-direction: column; align-items: center;
      }
      .jb-empty-title { font-size: 1rem; font-weight: 600; color: #374151; }
      .jb-empty-sub   { font-size: 0.82rem; color: #9ca3af; margin-top: 6px; }

      /* Skeleton */
      .jb-skeleton-header {
        height: 76px; background: #fff;
        border-bottom: 1px solid #f0f0f0;
      }
      .jb-skeleton-card {
        max-width: 900px; margin: 16px auto;
        height: 160px; background: #f3f4f6; border-radius: 14px;
        animation: jb-pulse 1.2s ease-in-out infinite;
      }
      @keyframes jb-pulse {
        0%,100% { opacity: 1; } 50% { opacity: 0.5; }
      }

      /* Error */
      .jb-error-page {
        display: flex; align-items: center; justify-content: center;
      }
      .jb-error-box {
        text-align: center; padding: 40px;
        background: #fff; border-radius: 16px;
        max-width: 380px; margin: 40px;
        box-shadow: 0 4px 24px rgba(0,0,0,0.08);
      }
      .jb-error-icon { font-size: 2.5rem; margin-bottom: 16px; }
      .jb-error-box h2 { font-size: 1.1rem; color: #1a1d23; margin-bottom: 8px; }
      .jb-error-box p  { font-size: 0.85rem; color: #6b7280; }

      /* Modal overlay */
      .jb-overlay {
        position: fixed; inset: 0;
        background: rgba(0,0,0,0.45);
        display: flex; align-items: center; justify-content: center;
        z-index: 100; padding: 20px;
        animation: jb-fade-in 0.2s ease;
      }
      @keyframes jb-fade-in { from { opacity: 0; } to { opacity: 1; } }

      .jb-modal {
        background: #fff; border-radius: 18px;
        padding: 28px; max-width: 540px; width: 100%;
        max-height: 85vh; overflow-y: auto;
        position: relative;
        animation: jb-modal-in 0.25s cubic-bezier(0.16,1,0.3,1) both;
        box-shadow: 0 20px 60px rgba(0,0,0,0.2);
      }
      @keyframes jb-modal-in {
        from { opacity: 0; transform: scale(0.95) translateY(10px); }
        to   { opacity: 1; transform: scale(1) translateY(0); }
      }
      .jb-modal-close {
        position: absolute; top: 16px; right: 16px;
        width: 28px; height: 28px; border-radius: 8px;
        border: 1px solid #e5e7eb; background: #f9fafb;
        color: #6b7280; cursor: pointer; font-size: 0.75rem;
        display: grid; place-items: center;
        transition: all 0.15s;
      }
      .jb-modal-close:hover { background: #f3f4f6; color: #1a1d23; }
      .jb-modal-header {
        padding-bottom: 16px; margin-bottom: 16px;
        border-bottom: 1.5px solid #f0f0f0;
        display: flex; align-items: flex-start;
        justify-content: space-between; gap: 12px;
      }
      .jb-modal-title  { font-size: 1.15rem; font-weight: 700; color: #1a1d23; }
      .jb-modal-meta   { display: flex; flex-wrap: wrap; gap: 12px; margin-bottom: 16px; }
      .jb-modal-desc   { font-size: 0.88rem; color: #6b7280; line-height: 1.65; margin-bottom: 16px; }
      .jb-modal-section {
        font-size: 0.7rem; font-weight: 700; text-transform: uppercase;
        letter-spacing: 0.1em; color: #9ca3af; margin-bottom: 8px;
      }
      .jb-modal-apply {
        display: flex; align-items: center; justify-content: center; gap: 8px;
        width: 100%; padding: 13px; border: none; border-radius: 12px;
        color: #fff; font-size: 0.95rem; font-weight: 700;
        cursor: pointer; margin-top: 20px;
        transition: all 0.2s;
      }
      .jb-modal-apply:hover { filter: brightness(1.08); transform: translateY(-1px); }

      /* Footer */
      .jb-footer {
        text-align: center; padding: 20px;
        font-size: 0.72rem; color: #c4c9d4;
        border-top: 1px solid #f0f0f0; margin-top: 20px;
      }

      @media (max-width: 600px) {
        .jb-grid { grid-template-columns: 1fr; }
        .jb-header-inner { padding: 12px 16px; }
        .jb-container { padding: 16px; }
      }
    `}</style>
  )
}
