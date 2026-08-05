import { useState, useCallback } from 'react'
import { Search, Briefcase, Clock, Building2, MapPin, Eye, Bookmark, ArrowUpDown } from 'lucide-react'
import { computeVisibleSkills } from '../utils'
import useSavedJobs from '../hooks/useSavedJobs'

export default function JobsTab({ jobs, onViewDetails, onQuickApply }) {
  const [search, setSearch] = useState('')
  const [activeFilter, setActiveFilter] = useState('all')
  const [sortBy, setSortBy] = useState('newest')
  const { isSaved, toggle, savedCount } = useSavedJobs()

  const hasBestMatch = jobs.some(j => typeof j.matchPct === 'number')
  const effectiveSort = hasBestMatch ? sortBy : 'newest'

  // Stage 1: text search
  const q = search.toLowerCase()
  const textFiltered = jobs.filter(j =>
    j.title.toLowerCase().includes(q) ||
    (j.department || '').toLowerCase().includes(q) ||
    (j.skills || []).some(s => s.toLowerCase().includes(q))
  )

  // Stage 2: chip filter
  const chipFiltered =
    activeFilter === 'all'    ? textFiltered
    : activeFilter === 'saved'  ? textFiltered.filter(j => isSaved(j.id))
    : activeFilter === 'Remote' ? textFiltered.filter(j => (j.location || '').toLowerCase() === 'remote')
    : textFiltered.filter(j => (j.type || '').toLowerCase() === activeFilter.toLowerCase())

  // Stage 3: sort
  const displayedJobs = [...chipFiltered].sort((a, b) =>
    effectiveSort === 'bestMatch'
      ? (b.matchPct ?? -1) - (a.matchPct ?? -1)
      : new Date(b.posted) - new Date(a.posted)
  )

  const filterChips = [
    { key: 'all',       label: 'All' },
    { key: 'Full-time', label: 'Full-time' },
    { key: 'Part-time', label: 'Part-time' },
    { key: 'Contract',  label: 'Contract' },
    { key: 'Remote',    label: 'Remote' },
    { key: 'saved',     label: savedCount > 0 ? `Saved (${savedCount})` : 'Saved' },
  ]

  return (
    <>
      {/* Search bar */}
      <div className="flex-center gap-12 mb-24" style={{
        background: 'var(--surface-input)', border: '1px solid var(--border-subtle)',
        borderRadius: 12, padding: '4px 16px', maxWidth: 480
      }}>
        <Search size={18} style={{ color: 'var(--text-tertiary)' }} />
        <input className="input" style={{ border: 'none', background: 'none', padding: '12px 0' }}
          placeholder="Search jobs, skills, departments..." value={search} onChange={e => setSearch(e.target.value)} />
      </div>

      {/* Filter chips */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', marginBottom: 12 }}>
        {filterChips.map(({ key, label }) => (
          <button
            key={key}
            onClick={() => setActiveFilter(key)}
            className={activeFilter === key ? 'filter-chip filter-chip-active' : 'filter-chip'}
          >
            {key === 'saved' && <Bookmark size={11} />}
            {label}
          </button>
        ))}
      </div>

      {/* Sort row */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 20 }}>
        <ArrowUpDown size={13} style={{ color: 'var(--text-tertiary)' }} />
        <span style={{ fontSize: '0.72rem', fontFamily: 'var(--font-mono)', color: 'var(--text-tertiary)', textTransform: 'uppercase', letterSpacing: '0.08em' }}>
          Sort:
        </span>
        <button
          className={effectiveSort === 'newest' ? 'sort-btn sort-btn-active' : 'sort-btn'}
          onClick={() => setSortBy('newest')}
        >
          Newest
        </button>
        {hasBestMatch && (
          <button
            className={effectiveSort === 'bestMatch' ? 'sort-btn sort-btn-active' : 'sort-btn'}
            onClick={() => setSortBy('bestMatch')}
          >
            Best Match
          </button>
        )}
      </div>

      {/* Job grid */}
      <div className="jobs-grid">
        {displayedJobs.length === 0 && activeFilter === 'saved' && (
          <div className="card" style={{ padding: 40, textAlign: 'center', color: 'var(--text-tertiary)', gridColumn: '1 / -1' }}>
            <Bookmark size={48} style={{ marginBottom: 12, opacity: 0.3 }} />
            <div style={{ fontWeight: 600, marginBottom: 6, color: 'var(--text-secondary)' }}>No saved jobs yet</div>
            <div style={{ fontSize: '0.85rem' }}>Click the bookmark icon on any job card to save it here.</div>
          </div>
        )}
        {displayedJobs.length === 0 && activeFilter !== 'saved' && (
          <div className="card" style={{ padding: 40, textAlign: 'center', color: 'var(--text-tertiary)', gridColumn: '1 / -1' }}>
            <Briefcase size={48} style={{ marginBottom: 12, opacity: 0.3 }} />
            <div>No jobs match your current filters.</div>
          </div>
        )}
        {displayedJobs.map(job => {
          const allSkills = job.skills || []
          const visibleCount = computeVisibleSkills(allSkills)
          const visibleSkills = allSkills.slice(0, visibleCount)
          const extraCount = allSkills.length - visibleCount
          const saved = isSaved(job.id)
          return (
            <div key={job.id} className="card card-glow job-card">
              <div className="job-card-top-bar" />
              <div className="job-card-header">
                <span className="badge badge-amber">{job.type}</span>
                <button
                  className={saved ? 'bookmark-btn bookmark-btn-active' : 'bookmark-btn'}
                  onClick={(e) => { e.stopPropagation(); toggle(job.id) }}
                  aria-label={saved ? 'Remove from saved' : 'Save job'}
                  title={saved ? 'Remove from saved' : 'Save job'}
                >
                  <Bookmark
                    size={14}
                    fill={saved ? 'var(--accent-primary)' : 'none'}
                    stroke="var(--accent-primary)"
                  />
                </button>
                <span className="job-card-date">
                  <Clock size={11} />
                  {job.posted}
                </span>
              </div>
              <h3 className="job-card-title">{job.title}</h3>
              <div className="job-card-meta">
                <span className="job-meta-item">
                  <Building2 size={12} />
                  {job.department}
                </span>
                <span className="job-meta-sep">·</span>
                <span className="job-meta-item">
                  <MapPin size={12} />
                  {job.location}
                </span>
              </div>
              <div className="job-card-divider" />
              <div className="job-card-skills">
                {visibleSkills.map(s => (
                  <span key={s} className="skill-tag">{s}</span>
                ))}
                {extraCount > 0 && (
                  <span className="skill-tag skill-tag-more">+{extraCount}</span>
                )}
                {(job.skills || []).length === 0 && (
                  <span className="skill-tag-empty">No skills listed</span>
                )}
              </div>
              <div style={{ display: 'flex', gap: 8, marginTop: 'auto', paddingTop: 8 }}>
                <button
                  className="btn btn-secondary btn-sm"
                  style={{ flex: 1, justifyContent: 'center' }}
                  onClick={() => onViewDetails(job)}
                >
                  <Eye size={14} /> View Details
                </button>
                <button
                  className="btn btn-primary btn-sm"
                  style={{ flex: 1, justifyContent: 'center' }}
                  onClick={() => onQuickApply(job)}
                >
                  <Briefcase size={14} /> Quick Apply
                </button>
              </div>
            </div>
          )
        })}
      </div>

      <style>{`
        .jobs-grid {
          display: grid;
          grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
          gap: 20px;
        }

        .job-card {
          position: relative;
          display: flex;
          flex-direction: column;
          min-height: 304px;
          overflow: hidden;
          border-left: 2px solid var(--accent-primary);
          transition: transform 0.2s ease, box-shadow 0.2s ease;
        }

        .job-card:hover {
          transform: translateY(-4px);
          box-shadow: 0 16px 40px rgba(0, 0, 0, 0.22);
        }

        .job-card-top-bar {
          position: absolute;
          top: 0;
          left: 0;
          right: 0;
          height: 2px;
          background: linear-gradient(90deg, var(--accent-primary), transparent 70%);
          opacity: 0;
          transition: opacity 0.2s ease;
        }

        .job-card:hover .job-card-top-bar {
          opacity: 1;
        }

        .job-card-header {
          display: flex;
          align-items: center;
          justify-content: space-between;
          margin-bottom: 14px;
          flex-shrink: 0;
        }

        .job-card-date {
          display: flex;
          align-items: center;
          gap: 5px;
          font-size: 0.7rem;
          font-family: var(--font-mono);
          color: var(--text-tertiary);
          letter-spacing: 0.02em;
        }

        .job-card-title {
          font-size: 1rem;
          font-weight: 700;
          line-height: 1.4;
          margin: 0 0 10px 0;
          display: -webkit-box;
          -webkit-line-clamp: 2;
          -webkit-box-orient: vertical;
          overflow: hidden;
          flex-shrink: 0;
        }

        .job-card-meta {
          display: flex;
          align-items: center;
          gap: 6px;
          font-size: 0.75rem;
          color: var(--text-tertiary);
          margin-bottom: 14px;
          flex-shrink: 0;
        }

        .job-meta-item {
          display: flex;
          align-items: center;
          gap: 4px;
        }

        .job-meta-sep {
          opacity: 0.35;
        }

        .job-card-divider {
          height: 1px;
          background: var(--border-subtle);
          margin-bottom: 12px;
          flex-shrink: 0;
        }

        .job-card-skills {
          display: flex;
          align-items: center;
          gap: 6px;
          flex: 1;
          flex-wrap: nowrap;
          overflow: visible;
        }

        .skill-tag {
          display: inline-flex;
          align-items: center;
          font-size: 0.7rem;
          font-family: var(--font-mono);
          padding: 4px 10px;
          background: var(--bg-hover);
          border: 1px solid var(--border-subtle);
          border-radius: 4px;
          color: var(--text-secondary);
          white-space: nowrap;
          flex-shrink: 0;
          letter-spacing: 0.01em;
          transition: border-color 0.15s ease;
        }

        .job-card:hover .skill-tag {
          border-color: var(--border-medium, rgba(255,255,255,0.12));
        }

        .skill-tag-more {
          background: var(--accent-glow);
          border-color: transparent;
          color: var(--accent-primary);
          font-weight: 600;
        }

        .skill-tag-empty {
          font-size: 0.72rem;
          color: var(--text-tertiary);
          font-style: italic;
        }

        .job-card-btn {
          width: 100%;
          justify-content: center;
          margin-top: 14px;
          flex-shrink: 0;
        }

        /* Filter chips */
        .filter-chip {
          display: inline-flex;
          align-items: center;
          gap: 5px;
          padding: 5px 14px;
          border-radius: 20px;
          border: 1px solid var(--border-medium);
          background: var(--surface-input);
          color: var(--text-secondary);
          font-size: 0.78rem;
          font-weight: 600;
          cursor: pointer;
          transition: all 0.18s ease;
          font-family: var(--font-body);
          letter-spacing: 0.01em;
          white-space: nowrap;
        }

        .filter-chip:hover {
          border-color: var(--accent-primary);
          color: var(--accent-primary);
          background: var(--accent-glow);
        }

        .filter-chip-active {
          background: var(--accent-primary);
          border-color: var(--accent-primary);
          color: #fff;
        }

        .filter-chip-active:hover {
          opacity: 0.88;
        }

        /* Sort buttons */
        .sort-btn {
          padding: 4px 12px;
          border-radius: 6px;
          border: 1px solid var(--border-medium);
          background: var(--surface-input);
          color: var(--text-secondary);
          font-size: 0.75rem;
          font-weight: 600;
          cursor: pointer;
          transition: all 0.15s ease;
          font-family: var(--font-mono);
          letter-spacing: 0.02em;
        }

        .sort-btn:hover {
          border-color: var(--accent-primary);
          color: var(--accent-primary);
        }

        .sort-btn-active {
          background: var(--accent-glow);
          border-color: var(--accent-primary);
          color: var(--accent-primary);
        }

        /* Bookmark button */
        .bookmark-btn {
          background: none;
          border: none;
          cursor: pointer;
          padding: 3px 5px;
          border-radius: 5px;
          display: flex;
          align-items: center;
          justify-content: center;
          transition: background 0.15s ease, transform 0.15s ease, opacity 0.15s ease;
          opacity: 0.4;
        }

        .bookmark-btn:hover {
          background: var(--accent-glow);
          opacity: 1;
          transform: scale(1.15);
        }

        .bookmark-btn-active {
          opacity: 1;
        }

        .bookmark-btn-active:hover {
          transform: scale(1.1);
        }
      `}</style>
    </>
  )
}
