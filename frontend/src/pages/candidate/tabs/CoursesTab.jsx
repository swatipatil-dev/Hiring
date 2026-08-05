import { useState, useEffect, useRef } from 'react'
import { Search, Clock, ExternalLink, Award, BookOpen, Zap, ChevronRight, AlertTriangle, GraduationCap, CheckCircle2, PlusCircle, Plus, X, Sparkles, Tag } from 'lucide-react'
import api from '../../../api/client'
import useCourseRecommendations from '../hooks/useCourseRecommendations'
import useMyLearning from '../hooks/useMyLearning'
import { TEAL, TEAL_LIGHT, TEAL_MID } from '../utils'

const prettifySkill = (s) => (s || '').split(' ').map(w => (w ? w[0].toUpperCase() + w.slice(1) : w)).join(' ')

const LEVEL_STYLES = {
  Beginner:     { bg: '#f0fdf4', color: '#15803d', border: '#bbf7d0' },
  Intermediate: { bg: '#eff6ff', color: '#1d4ed8', border: '#bfdbfe' },
  Advanced:     { bg: '#faf5ff', color: '#7e22ce', border: '#e9d5ff' },
}

const CATEGORIES = ['All', 'Web Development', 'Data & AI', 'Cloud & DevOps', 'Cybersecurity', 'Business & Management', 'Digital Marketing']

function buildAtsMissingSkillsMap(appDetailsList) {
  const map = new Map()
  appDetailsList.forEach(detail => {
    let missing = detail?.ats_result?.missing_skills
    if (!missing) return
    // API returns a JSON string stored from json.dumps()
    if (typeof missing === 'string') {
      try { missing = JSON.parse(missing) } catch { return }
    }
    if (!Array.isArray(missing)) return
    missing.forEach(skill => {
      const s = (skill || '').toLowerCase().trim()
      if (s) map.set(s, (map.get(s) || 0) + 1)
    })
  })
  return map
}

export default function CoursesTab({ profile, jobs, apps }) {
  const [atsMissingSkills, setAtsMissingSkills] = useState(new Map())
  const [atsLoading, setAtsLoading] = useState(false)
  const fetchedRef = useRef(false)

  // Fetch per-application ATS details once on mount (only apps that have an ATS score)
  useEffect(() => {
    if (fetchedRef.current) return
    const atsApps = (apps || []).filter(a => a.atsScore != null)
    if (atsApps.length === 0) return

    fetchedRef.current = true
    setAtsLoading(true)

    Promise.allSettled(
      atsApps.map(a => api.get(`/candidate/applications/${a.id}`).then(r => r.data))
    ).then(results => {
      const details = results
        .filter(r => r.status === 'fulfilled')
        .map(r => r.value)
      setAtsMissingSkills(buildAtsMissingSkillsMap(details))
    }).finally(() => setAtsLoading(false))
  }, [apps])

  const { recommended, gapSkills } = useCourseRecommendations({ profile, jobs, atsMissingSkills })

  const [search, setSearch] = useState('')
  const [activeCategory, setActiveCategory] = useState('All')
  const [sortBy, setSortBy] = useState('recommended')
  const [costFilter, setCostFilter] = useState('all')          // all | free | paid
  const [learningFilter, setLearningFilter] = useState('all')  // all | enrolled | completed
  const [detailCourse, setDetailCourse] = useState(null)

  const learning = useMyLearning()

  // Course → Profile skill bridge: skills the candidate already has (lowercased)
  const profileSkillNames = new Set(
    (profile?.skills || []).map(s => (s?.skill_name || s || '').toLowerCase()).filter(Boolean)
  )
  const [addedSkillsByCourse, setAddedSkillsByCourse] = useState({})
  const [addingSkills, setAddingSkills] = useState(false)
  const [bridgeError, setBridgeError] = useState('')

  const handleAddSkills = async (course, skillNames) => {
    if (!skillNames.length) return
    setAddingSkills(true); setBridgeError('')
    const added = []
    for (const name of skillNames) {
      try {
        await api.post('/candidate/profile/skills', {
          skill_name: prettifySkill(name),
          proficiency_level: 'Beginner',
          years_of_experience: null,
          category: 'Technical',
        })
        added.push(name)
      } catch (e) {
        setBridgeError(e.response?.data?.detail || 'Some skills could not be added')
      }
    }
    setAddedSkillsByCourse(prev => ({ ...prev, [course.id]: [...(prev[course.id] || []), ...added] }))
    setAddingSkills(false)
  }

  const q = search.toLowerCase()

  const filtered = recommended.filter(c => {
    const matchesSearch = !q
      || c.title.toLowerCase().includes(q)
      || c.description.toLowerCase().includes(q)
      || c.skills.some(s => s.toLowerCase().includes(q))
    const matchesCategory = activeCategory === 'All' || c.category === activeCategory
    const matchesCost = costFilter === 'all' || (costFilter === 'free' ? c.isFree : !c.isFree)
    const matchesLearning = learningFilter === 'all' || learning.statusOf(c.id) === learningFilter
    return matchesSearch && matchesCategory && matchesCost && matchesLearning
  })

  const displayed = [...filtered].sort((a, b) => {
    if (sortBy === 'recommended') return b.score - a.score || a.title.localeCompare(b.title)
    if (sortBy === 'level') {
      const order = { Beginner: 0, Intermediate: 1, Advanced: 2 }
      return (order[a.level] ?? 1) - (order[b.level] ?? 1)
    }
    return a.title.localeCompare(b.title)
  })

  const topGaps = [...gapSkills].slice(0, 6)
  const hasProfile = (profile?.skills || []).length > 0
  const totalAtsSkills = atsMissingSkills.size

  // Per-category course counts for the filter chips (full catalog, ignores search)
  const categoryCounts = recommended.reduce((m, c) => {
    m[c.category] = (m[c.category] || 0) + 1
    return m
  }, {})
  const countFor = cat => (cat === 'All' ? recommended.length : categoryCounts[cat] || 0)

  const hasActiveFilters = q !== '' || activeCategory !== 'All' || costFilter !== 'all' || learningFilter !== 'all'
  const clearFilters = () => { setSearch(''); setActiveCategory('All'); setCostFilter('all'); setLearningFilter('all') }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>

      {/* ── NASSCOM Partnership Banner ── */}
      <div style={{
        background: 'linear-gradient(135deg, #0f172a 0%, #1e293b 50%, #0f2027 100%)',
        borderRadius: 16, padding: '24px 28px',
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        gap: 20, flexWrap: 'wrap', position: 'relative', overflow: 'hidden',
      }}>
        <div style={{ position: 'absolute', right: -40, top: -40, width: 200, height: 200, borderRadius: '50%', border: `1px solid rgba(78,205,181,0.1)`, pointerEvents: 'none' }} />
        <div style={{ position: 'absolute', right: 20, top: 20, width: 120, height: 120, borderRadius: '50%', border: `1px solid rgba(78,205,181,0.08)`, pointerEvents: 'none' }} />

        <div style={{ position: 'relative', zIndex: 1 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 8 }}>
            <div style={{ background: TEAL, color: '#fff', borderRadius: 6, padding: '3px 10px', fontSize: 11, fontWeight: 700, letterSpacing: '0.05em' }}>
              NASSCOM
            </div>
            <div style={{ background: 'rgba(78,205,181,0.1)', color: TEAL, border: `1px solid rgba(78,205,181,0.2)`, borderRadius: 20, padding: '2px 10px', fontSize: 11, fontWeight: 600 }}>
              FutureSkills Prime
            </div>
          </div>
          <div style={{ fontSize: 20, fontWeight: 700, color: '#f1f5f9', lineHeight: 1.3 }}>
            Upskill with NASSCOM-certified courses
          </div>
          <div style={{ fontSize: 13, color: '#94a3b8', marginTop: 6, maxWidth: 480 }}>
            Courses are matched to your profile, the jobs you're browsing, and your ATS screening results.
          </div>
        </div>

        <div style={{ position: 'relative', zIndex: 1, display: 'flex', alignItems: 'center', gap: 24, flexShrink: 0 }}>
          {[
            { label: 'Courses', value: recommended.length },
            { label: 'Categories', value: CATEGORIES.length - 1 },
            { label: 'Certified', value: recommended.filter(c => c.certified).length },
          ].map(({ label, value }) => (
            <div key={label} style={{ textAlign: 'center' }}>
              <div style={{ fontSize: 24, fontWeight: 700, color: TEAL }}>{value}</div>
              <div style={{ fontSize: 11, color: '#94a3b8', marginTop: 2 }}>{label}</div>
            </div>
          ))}
        </div>
      </div>

      {/* ── ATS Rejection Callout (highest priority signal) ── */}
      {!atsLoading && totalAtsSkills > 0 && (
        <div style={{
          background: '#fff7ed', border: '1px solid #fed7aa',
          borderRadius: 12, padding: '14px 18px',
          display: 'flex', alignItems: 'flex-start', gap: 12,
        }}>
          <AlertTriangle size={18} style={{ color: '#c2410c', flexShrink: 0, marginTop: 2 }} />
          <div>
            <div style={{ fontSize: 13, fontWeight: 700, color: '#9a3412', marginBottom: 8 }}>
              Skills flagged in your ATS screenings
            </div>
            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
              {[...atsMissingSkills.entries()]
                .sort((a, b) => b[1] - a[1])
                .slice(0, 8)
                .map(([skill, count]) => (
                  <span key={skill} style={{
                    background: '#fff', border: '1px solid #fca5a5',
                    color: '#b91c1c', borderRadius: 20, padding: '3px 10px',
                    fontSize: 12, fontWeight: 600, display: 'inline-flex', alignItems: 'center', gap: 4,
                  }}>
                    {skill}
                    {count > 1 && (
                      <span style={{ background: '#fee2e2', borderRadius: 10, padding: '0 5px', fontSize: 10, fontWeight: 700 }}>
                        ×{count}
                      </span>
                    )}
                  </span>
                ))}
              {atsMissingSkills.size > 8 && (
                <span style={{ background: '#dc2626', color: '#fff', borderRadius: 20, padding: '3px 10px', fontSize: 12, fontWeight: 600 }}>
                  +{atsMissingSkills.size - 8} more
                </span>
              )}
            </div>
            <div style={{ fontSize: 12, color: '#9a3412', marginTop: 8, opacity: 0.85 }}>
              These skills caused your applications to score lower. Courses covering these are ranked highest.
            </div>
          </div>
        </div>
      )}

      {/* ── General Gap Skills Callout ── */}
      {topGaps.length > 0 && (
        <div style={{
          background: TEAL_LIGHT, border: `1px solid ${TEAL_MID}`,
          borderRadius: 12, padding: '14px 18px',
          display: 'flex', alignItems: 'flex-start', gap: 12,
        }}>
          <Zap size={18} style={{ color: TEAL, flexShrink: 0, marginTop: 2 }} />
          <div>
            <div style={{ fontSize: 13, fontWeight: 700, color: '#0f766e', marginBottom: 8 }}>
              Skill gaps detected from jobs you're browsing
            </div>
            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
              {topGaps.map(skill => (
                <span key={skill} style={{
                  background: '#fff', border: `1px solid ${TEAL_MID}`,
                  color: '#0f766e', borderRadius: 20, padding: '3px 10px', fontSize: 12, fontWeight: 600,
                }}>
                  {skill}
                </span>
              ))}
              {gapSkills.size > 6 && (
                <span style={{ background: TEAL, color: '#fff', borderRadius: 20, padding: '3px 10px', fontSize: 12, fontWeight: 600 }}>
                  +{gapSkills.size - 6} more
                </span>
              )}
            </div>
          </div>
        </div>
      )}

      {/* ── No profile skills nudge ── */}
      {!hasProfile && (
        <div style={{
          background: '#fffbeb', border: '1px solid #fde68a', borderRadius: 12,
          padding: '12px 16px', fontSize: 13, color: '#92400e',
          display: 'flex', alignItems: 'center', gap: 10,
        }}>
          <BookOpen size={16} style={{ flexShrink: 0 }} />
          Add skills to your profile to get personalised course recommendations. Showing all courses for now.
        </div>
      )}

      {/* ── Search ── */}
      <div style={{
        display: 'flex', alignItems: 'center', gap: 12,
        background: 'var(--surface-input)', border: '1px solid var(--border-subtle)',
        borderRadius: 12, padding: '4px 16px', maxWidth: 480,
      }}>
        <Search size={18} style={{ color: 'var(--text-tertiary)', flexShrink: 0 }} />
        <input
          className="input"
          style={{ border: 'none', background: 'none', padding: '12px 0' }}
          placeholder="Search courses by title, skill, or topic…"
          aria-label="Search courses by title, skill, or topic"
          value={search}
          onChange={e => setSearch(e.target.value)}
        />
      </div>

      {/* ── Category filter chips ── */}
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
        {CATEGORIES.map(cat => {
          const isActive = activeCategory === cat
          return (
            <button
              key={cat}
              onClick={() => setActiveCategory(cat)}
              aria-pressed={isActive}
              className={isActive ? 'filter-chip filter-chip-active' : 'filter-chip'}
            >
              {cat}
              <span className="filter-chip-count">{countFor(cat)}</span>
            </button>
          )
        })}
      </div>

      {/* ── Cost + Learning status filters ── */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 18, flexWrap: 'wrap' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <Tag size={13} style={{ color: 'var(--text-tertiary)' }} />
          {[
            { key: 'all', label: 'All' },
            { key: 'free', label: 'Free' },
            { key: 'paid', label: 'Paid' },
          ].map(({ key, label }) => (
            <button
              key={key}
              aria-pressed={costFilter === key}
              className={costFilter === key ? 'sort-btn sort-btn-active' : 'sort-btn'}
              onClick={() => setCostFilter(key)}
            >
              {label}
            </button>
          ))}
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <GraduationCap size={14} style={{ color: 'var(--text-tertiary)' }} />
          {[
            { key: 'all', label: 'All' },
            { key: 'enrolled', label: `Enrolled${learning.enrolledCount ? ` (${learning.enrolledCount})` : ''}` },
            { key: 'completed', label: `Completed${learning.completedCount ? ` (${learning.completedCount})` : ''}` },
          ].map(({ key, label }) => (
            <button
              key={key}
              aria-pressed={learningFilter === key}
              className={learningFilter === key ? 'sort-btn sort-btn-active' : 'sort-btn'}
              onClick={() => setLearningFilter(key)}
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      {/* ── Sort ── */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <span style={{ fontSize: '0.72rem', fontFamily: 'var(--font-mono)', color: 'var(--text-tertiary)', textTransform: 'uppercase', letterSpacing: '0.08em' }}>
          Sort:
        </span>
        {[
          { key: 'recommended', label: 'Recommended' },
          { key: 'level',       label: 'Level' },
          { key: 'title',       label: 'A–Z' },
        ].map(({ key, label }) => (
          <button
            key={key}
            aria-pressed={sortBy === key}
            className={sortBy === key ? 'sort-btn sort-btn-active' : 'sort-btn'}
            onClick={() => setSortBy(key)}
          >
            {label}
          </button>
        ))}
        <span style={{ marginLeft: 'auto', fontSize: 12, color: 'var(--text-tertiary)' }}>
          {atsLoading ? 'Loading ATS data…' : `${displayed.length} course${displayed.length !== 1 ? 's' : ''}`}
        </span>
      </div>

      {/* ── Course Grid ── */}
      {displayed.length === 0 ? (
        <div style={{
          padding: 60, textAlign: 'center', color: 'var(--text-tertiary)',
          background: 'var(--bg-card)', border: '1px solid var(--border-subtle)', borderRadius: 14,
        }}>
          <BookOpen size={48} style={{ marginBottom: 12, opacity: 0.3 }} />
          <div style={{ fontWeight: 600, marginBottom: 6, color: 'var(--text-secondary)' }}>No courses match your search</div>
          <div style={{ fontSize: '0.85rem' }}>Try a different keyword or category.</div>
          {hasActiveFilters && (
            <button className="btn btn-sm" style={{ marginTop: 16 }} onClick={clearFilters}>
              Clear filters
            </button>
          )}
        </div>
      ) : (
        <div className="courses-grid">
          {displayed.map(course => {
            const lvl = LEVEL_STYLES[course.level] || LEVEL_STYLES.Intermediate
            const hasAtsMatch = (course.matchedAtsSkills || []).length > 0
            const hasGapMatch = (course.matchedGapSkills || []).length > 0

            // Build the "why recommended" lines — ATS takes priority
            const atsWhy = hasAtsMatch
              ? `Covers ${course.matchedAtsSkills.length} ATS-flagged skill${course.matchedAtsSkills.length > 1 ? 's' : ''}: ${course.matchedAtsSkills.slice(0, 3).map(x => x.skill).join(', ')}${course.matchedAtsSkills.length > 3 ? '…' : ''}`
              : null
            const gapWhy = hasGapMatch
              ? `Covers ${course.matchedGapSkills.length} gap skill${course.matchedGapSkills.length > 1 ? 's' : ''}: ${course.matchedGapSkills.slice(0, 3).join(', ')}${course.matchedGapSkills.length > 3 ? '…' : ''}`
              : null

            // ATS-flagged skill names (lowercase) for tag highlighting
            const atsSkillSet = new Set((course.matchedAtsSkills || []).map(x => x.skill.toLowerCase()))
            const status = learning.statusOf(course.id)

            return (
              <div
                key={course.id}
                className={`course-card${hasAtsMatch ? ' course-card-ats' : ''}${status === 'completed' ? ' course-card-completed' : ''}`}
                role="button"
                tabIndex={0}
                style={{ cursor: 'pointer' }}
                onClick={() => setDetailCourse(course)}
                onKeyDown={e => { if (e.key === 'Enter') setDetailCourse(course) }}
              >
                <div className="course-card-accent-bar" />

                {/* Badges row */}
                <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 12, flexWrap: 'wrap' }}>
                  <span style={{ background: TEAL, color: '#fff', borderRadius: 5, padding: '3px 8px', fontSize: 10.5, fontWeight: 700, letterSpacing: '0.04em' }}>
                    NASSCOM
                  </span>
                  <span style={{ display: 'inline-flex', alignItems: 'center', gap: 3, background: course.isFree ? '#ecfdf5' : '#eef2ff', color: course.isFree ? '#047857' : '#4338ca', border: `1px solid ${course.isFree ? '#a7f3d0' : '#c7d2fe'}`, borderRadius: 5, padding: '3px 8px', fontSize: 10.5, fontWeight: 700 }}>
                    {course.isFree ? 'Free' : `₹${course.cost.toLocaleString('en-IN')}`}
                  </span>
                  {status !== 'none' && (
                    <span className={`course-status-pill course-status-${status}`}>
                      {status === 'completed' ? <CheckCircle2 size={10} /> : <GraduationCap size={10} />}
                      {status === 'completed' ? 'Completed' : 'Enrolled'}
                    </span>
                  )}
                  {course.certified && (
                    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4, background: '#fffbeb', color: '#b45309', border: '1px solid #fde68a', borderRadius: 5, padding: '3px 8px', fontSize: 10.5, fontWeight: 600 }}>
                      <Award size={10} /> Certified
                    </span>
                  )}
                  {hasAtsMatch && (
                    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4, background: '#fff1f2', color: '#b91c1c', border: '1px solid #fecaca', borderRadius: 5, padding: '3px 8px', fontSize: 10.5, fontWeight: 600 }}>
                      <AlertTriangle size={10} /> ATS Match
                    </span>
                  )}
                  <span style={{ background: lvl.bg, color: lvl.color, border: `1px solid ${lvl.border}`, borderRadius: 5, padding: '3px 8px', fontSize: 10.5, fontWeight: 600, marginLeft: 'auto' }}>
                    {course.level}
                  </span>
                </div>

                {/* Title */}
                <h3 className="course-title">{course.title}</h3>

                {/* Description */}
                <p className="course-description">{course.description}</p>

                {/* Skill tags */}
                <div className="course-skills">
                  {course.skills.slice(0, 5).map(skill => {
                    const sl = skill.toLowerCase()
                    const isAts = atsSkillSet.has(sl)
                    const isGap = (course.matchedGapSkills || []).map(s => s.toLowerCase()).includes(sl)
                    const cls = isAts
                      ? 'course-skill-tag course-skill-tag-ats'
                      : isGap
                        ? 'course-skill-tag course-skill-tag-gap'
                        : 'course-skill-tag'
                    return <span key={skill} className={cls}>{skill}</span>
                  })}
                  {course.skills.length > 5 && (
                    <span className="course-skill-tag course-skill-tag-more">+{course.skills.length - 5}</span>
                  )}
                </div>

                {/* Footer */}
                <div style={{ marginTop: 'auto', paddingTop: 14 }}>
                  {/* ATS why */}
                  {atsWhy && (
                    <div style={{
                      display: 'flex', alignItems: 'center', gap: 6, fontSize: 11.5,
                      color: '#9a3412', marginBottom: 6,
                      background: '#fff7ed', border: '1px solid #fed7aa',
                      borderRadius: 6, padding: '5px 10px',
                    }}>
                      <AlertTriangle size={11} style={{ flexShrink: 0 }} />
                      {atsWhy}
                    </div>
                  )}
                  {/* Gap why */}
                  {gapWhy && (
                    <div style={{
                      display: 'flex', alignItems: 'center', gap: 6, fontSize: 11.5,
                      color: '#0f766e', marginBottom: 6,
                      background: TEAL_LIGHT, borderRadius: 6, padding: '5px 10px',
                    }}>
                      <Zap size={11} style={{ flexShrink: 0 }} />
                      {gapWhy}
                    </div>
                  )}

                  {/* Duration + learning toggle + CTA */}
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontSize: 12, color: 'var(--text-tertiary)', fontFamily: 'var(--font-mono)' }}>
                      <Clock size={12} /> {course.duration}
                    </span>
                    <button
                      type="button"
                      className={`course-learn-btn course-learn-${status}`}
                      style={{ marginLeft: 'auto' }}
                      onClick={e => {
                        e.stopPropagation()
                        if (status === 'none') learning.enroll(course.id)
                        else if (status === 'enrolled') learning.complete(course.id)
                        else learning.reset(course.id)
                      }}
                    >
                      {status === 'none' && (<><PlusCircle size={13} /> Enroll</>)}
                      {status === 'enrolled' && (<><CheckCircle2 size={13} /> Mark done</>)}
                      {status === 'completed' && (<><CheckCircle2 size={13} /> Done</>)}
                    </button>
                    <a
                      href={course.url}
                      target="_blank"
                      rel="noreferrer"
                      className="btn btn-primary btn-sm"
                      style={{ display: 'inline-flex', alignItems: 'center', gap: 6, textDecoration: 'none' }}
                      onClick={e => e.stopPropagation()}
                    >
                      Start <ExternalLink size={13} />
                    </a>
                  </div>
                </div>
              </div>
            )
          })}
        </div>
      )}

      {/* ── Course detail modal ── */}
      {detailCourse && (
        <CourseDetailModal
          course={detailCourse}
          status={learning.statusOf(detailCourse.id)}
          onClose={() => setDetailCourse(null)}
          onEnroll={() => learning.enroll(detailCourse.id)}
          onComplete={() => learning.complete(detailCourse.id)}
          onReset={() => learning.reset(detailCourse.id)}
          profileSkillNames={profileSkillNames}
          addedSkills={addedSkillsByCourse[detailCourse.id] || []}
          addingSkills={addingSkills}
          bridgeError={bridgeError}
          onAddSkills={handleAddSkills}
        />
      )}

      {/* ── See all on NASSCOM link ── */}
      <div style={{ textAlign: 'center', paddingTop: 8 }}>
        <a
          href="https://futureskillsprime.in"
          target="_blank"
          rel="noreferrer"
          style={{ display: 'inline-flex', alignItems: 'center', gap: 6, color: TEAL, fontSize: 13, fontWeight: 600, textDecoration: 'none' }}
        >
          Browse all courses on FutureSkills Prime <ChevronRight size={14} />
        </a>
      </div>

      <style>{`
        .courses-grid {
          display: grid;
          grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
          gap: 20px;
        }

        .course-card {
          position: relative;
          display: flex;
          flex-direction: column;
          background: var(--bg-card, #fff);
          border: 1px solid var(--border-subtle);
          border-radius: 14px;
          padding: 20px;
          overflow: hidden;
          transition: transform 0.2s ease, box-shadow 0.2s ease, border-color 0.2s ease;
        }
        .course-card-ats {
          border-left: 3px solid #ef4444;
        }
        .course-card:hover {
          transform: translateY(-3px);
          box-shadow: 0 12px 32px rgba(0,0,0,0.18);
          border-color: ${TEAL_MID};
        }
        .course-card-ats:hover {
          border-left-color: #ef4444;
        }

        .course-card-accent-bar {
          position: absolute;
          top: 0; left: 0; right: 0;
          height: 3px;
          background: linear-gradient(90deg, ${TEAL}, transparent 70%);
          opacity: 0;
          transition: opacity 0.2s ease;
        }
        .course-card:hover .course-card-accent-bar { opacity: 1; }

        .course-title {
          font-size: 1rem;
          font-weight: 700;
          line-height: 1.4;
          margin: 0 0 10px;
          display: -webkit-box;
          -webkit-line-clamp: 2;
          -webkit-box-orient: vertical;
          overflow: hidden;
          color: var(--text-primary);
        }

        .course-description {
          font-size: 0.83rem;
          color: var(--text-secondary);
          line-height: 1.65;
          margin: 0 0 14px;
          display: -webkit-box;
          -webkit-line-clamp: 3;
          -webkit-box-orient: vertical;
          overflow: hidden;
        }

        .course-skills {
          display: flex;
          flex-wrap: wrap;
          gap: 6px;
          margin-bottom: 4px;
        }

        .course-skill-tag {
          display: inline-flex;
          align-items: center;
          font-size: 0.7rem;
          font-family: var(--font-mono);
          padding: 3px 9px;
          background: var(--bg-hover);
          border: 1px solid var(--border-subtle);
          border-radius: 4px;
          color: var(--text-secondary);
          white-space: nowrap;
          letter-spacing: 0.01em;
        }
        .course-skill-tag-ats {
          background: #fff1f2;
          border-color: #fca5a5;
          color: #b91c1c;
          font-weight: 700;
        }
        .course-skill-tag-gap {
          background: ${TEAL_LIGHT};
          border-color: ${TEAL_MID};
          color: #0f766e;
          font-weight: 600;
        }
        .course-skill-tag-more {
          background: var(--accent-glow);
          border-color: transparent;
          color: var(--accent-primary);
          font-weight: 600;
        }

        .filter-chip {
          display: inline-flex; align-items: center; gap: 5px;
          padding: 5px 14px; border-radius: 20px;
          border: 1px solid var(--border-medium);
          background: var(--surface-input); color: var(--text-secondary);
          font-size: 0.78rem; font-weight: 600; cursor: pointer;
          transition: all 0.18s ease; font-family: var(--font-body);
          letter-spacing: 0.01em; white-space: nowrap;
        }
        .filter-chip:hover { border-color: var(--accent-primary); color: var(--accent-primary); background: var(--accent-glow); }
        .filter-chip-active { background: var(--accent-primary); border-color: var(--accent-primary); color: #fff; }
        .filter-chip-active:hover { opacity: 0.88; }
        .filter-chip-count {
          font-size: 0.68rem; font-weight: 700; font-family: var(--font-mono);
          line-height: 1; padding: 2px 6px; border-radius: 10px;
          background: var(--bg-hover); color: var(--text-tertiary);
        }
        .filter-chip-active .filter-chip-count { background: rgba(255,255,255,0.22); color: #fff; }

        .sort-btn {
          padding: 4px 12px; border-radius: 6px;
          border: 1px solid var(--border-medium);
          background: var(--surface-input); color: var(--text-secondary);
          font-size: 0.75rem; font-weight: 600; cursor: pointer;
          transition: all 0.15s ease; font-family: var(--font-mono); letter-spacing: 0.02em;
        }
        .sort-btn:hover { border-color: var(--accent-primary); color: var(--accent-primary); }
        .sort-btn-active { background: var(--accent-glow); border-color: var(--accent-primary); color: var(--accent-primary); }

        /* Learning status */
        .course-card-completed { border-color: #a7f3d0; }
        .course-status-pill {
          display: inline-flex; align-items: center; gap: 4px;
          border-radius: 5px; padding: 3px 8px; font-size: 0.66rem; font-weight: 700;
          letter-spacing: 0.02em;
        }
        .course-status-enrolled { background: #eff6ff; color: #1d4ed8; border: 1px solid #bfdbfe; }
        .course-status-completed { background: #ecfdf5; color: #047857; border: 1px solid #a7f3d0; }

        .course-learn-btn {
          display: inline-flex; align-items: center; gap: 5px;
          padding: 6px 11px; border-radius: 8px; cursor: pointer;
          font-family: var(--font-body); font-size: 0.74rem; font-weight: 600;
          border: 1px solid var(--border-medium); background: var(--surface-input);
          color: var(--text-secondary); transition: all 0.15s ease; white-space: nowrap;
        }
        .course-learn-btn:hover { border-color: var(--accent-primary); color: var(--accent-primary); }
        .course-learn-enrolled { border-color: #93c5fd; color: #1d4ed8; background: #eff6ff; }
        .course-learn-enrolled:hover { background: #dbeafe; color: #1d4ed8; border-color: #60a5fa; }
        .course-learn-completed { border-color: #6ee7b7; color: #047857; background: #ecfdf5; }
        .course-learn-completed:hover { background: #d1fae5; color: #047857; border-color: #34d399; }

        /* Course detail modal */
        .course-modal-overlay {
          position: fixed; inset: 0; z-index: 1000;
          background: rgba(15,23,42,0.45); backdrop-filter: blur(5px);
          display: grid; place-items: center; padding: 20px;
          animation: courseFade 0.2s ease;
        }
        @keyframes courseFade { from { opacity: 0; } to { opacity: 1; } }
        .course-modal {
          background: var(--bg-card, #fff); border: 1px solid var(--border-subtle);
          border-radius: 18px; width: 100%; max-width: 560px;
          max-height: 88vh; overflow-y: auto; position: relative;
          box-shadow: 0 24px 60px rgba(0,0,0,0.22);
          animation: coursePop 0.25s cubic-bezier(.2,.8,.2,1);
        }
        @keyframes coursePop { from { opacity: 0; transform: translateY(12px) scale(0.98); } to { opacity: 1; transform: none; } }
        .course-modal-close {
          position: absolute; top: 16px; right: 16px;
          width: 32px; height: 32px; border-radius: 8px;
          border: 1px solid var(--border-subtle); background: var(--surface-input);
          color: var(--text-tertiary); cursor: pointer; display: grid; place-items: center;
          transition: all 0.15s ease;
        }
        .course-modal-close:hover { color: var(--text-primary); background: var(--bg-hover); }
        .course-bridge {
          margin-top: 18px; padding: 16px; border-radius: 12px;
          background: ${TEAL_LIGHT}; border: 1px solid ${TEAL_MID};
        }
      `}</style>
    </div>
  )
}

function CourseDetailModal({
  course, status, onClose, onEnroll, onComplete, onReset,
  profileSkillNames, addedSkills, addingSkills, bridgeError, onAddSkills,
}) {
  const lvl = LEVEL_STYLES[course.level] || LEVEL_STYLES.Intermediate
  const atsSkills = (course.matchedAtsSkills || []).map(x => x.skill.toLowerCase())
  const gapSkills = (course.matchedGapSkills || []).map(s => s.toLowerCase())
  const addedLower = new Set((addedSkills || []).map(s => s.toLowerCase()))

  // Skills this course teaches that the candidate doesn't already have / hasn't added
  const bridgeSkills = course.skills.filter(
    s => !profileSkillNames.has(s.toLowerCase()) && !addedLower.has(s.toLowerCase())
  )

  return (
    <div className="course-modal-overlay" onClick={onClose}>
      <div className="course-modal" onClick={e => e.stopPropagation()}>
        <button className="course-modal-close" onClick={onClose} aria-label="Close"><X size={16} /></button>
        <div style={{ padding: 24 }}>
          {/* Badges */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap', marginBottom: 14, paddingRight: 32 }}>
            <span style={{ background: TEAL, color: '#fff', borderRadius: 5, padding: '3px 8px', fontSize: 10.5, fontWeight: 700 }}>NASSCOM</span>
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 3, background: course.isFree ? '#ecfdf5' : '#eef2ff', color: course.isFree ? '#047857' : '#4338ca', border: `1px solid ${course.isFree ? '#a7f3d0' : '#c7d2fe'}`, borderRadius: 5, padding: '3px 8px', fontSize: 10.5, fontWeight: 700 }}>
              {course.isFree ? 'Free' : `₹${course.cost.toLocaleString('en-IN')}`}
            </span>
            {course.certified && (
              <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4, background: '#fffbeb', color: '#b45309', border: '1px solid #fde68a', borderRadius: 5, padding: '3px 8px', fontSize: 10.5, fontWeight: 600 }}><Award size={10} /> Certified</span>
            )}
            {status !== 'none' && (
              <span className={`course-status-pill course-status-${status}`}>
                {status === 'completed' ? <CheckCircle2 size={10} /> : <GraduationCap size={10} />}
                {status === 'completed' ? 'Completed' : 'Enrolled'}
              </span>
            )}
            <span style={{ background: lvl.bg, color: lvl.color, border: `1px solid ${lvl.border}`, borderRadius: 5, padding: '3px 8px', fontSize: 10.5, fontWeight: 600, marginLeft: 'auto' }}>{course.level}</span>
          </div>

          <h2 style={{ fontSize: '1.2rem', fontWeight: 800, color: 'var(--text-primary)', lineHeight: 1.3, margin: '0 0 6px' }}>{course.title}</h2>
          <div style={{ fontSize: 11.5, color: 'var(--text-tertiary)', fontFamily: 'var(--font-mono)', marginBottom: 14, display: 'flex', alignItems: 'center', gap: 8 }}>
            <span>{course.category}</span>
            <span>·</span>
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}><Clock size={11} /> {course.duration}</span>
          </div>
          <p style={{ fontSize: '0.88rem', color: 'var(--text-secondary)', lineHeight: 1.65, margin: '0 0 18px' }}>{course.description}</p>

          {/* Why recommended */}
          {atsSkills.length > 0 && (
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, color: '#9a3412', marginBottom: 8, background: '#fff7ed', border: '1px solid #fed7aa', borderRadius: 8, padding: '8px 12px' }}>
              <AlertTriangle size={13} style={{ flexShrink: 0 }} /> Covers {atsSkills.length} skill{atsSkills.length > 1 ? 's' : ''} flagged in your ATS screenings.
            </div>
          )}
          {gapSkills.length > 0 && (
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, color: '#0f766e', marginBottom: 8, background: TEAL_LIGHT, borderRadius: 8, padding: '8px 12px' }}>
              <Zap size={13} style={{ flexShrink: 0 }} /> Covers {gapSkills.length} skill{gapSkills.length > 1 ? 's' : ''} required by jobs you're browsing.
            </div>
          )}

          {/* Skills covered */}
          <div style={{ fontSize: '0.7rem', fontFamily: 'var(--font-mono)', color: 'var(--text-tertiary)', textTransform: 'uppercase', letterSpacing: '0.08em', margin: '14px 0 8px' }}>Skills covered</div>
          <div className="course-skills">
            {course.skills.map(s => {
              const sl = s.toLowerCase()
              const cls = atsSkills.includes(sl)
                ? 'course-skill-tag course-skill-tag-ats'
                : gapSkills.includes(sl)
                  ? 'course-skill-tag course-skill-tag-gap'
                  : 'course-skill-tag'
              return <span key={s} className={cls}>{s}</span>
            })}
          </div>

          {/* Actions */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 18 }}>
            <button
              type="button"
              className={`course-learn-btn course-learn-${status}`}
              onClick={() => { if (status === 'none') onEnroll(); else if (status === 'enrolled') onComplete(); else onReset() }}
            >
              {status === 'none' && (<><PlusCircle size={14} /> Enroll</>)}
              {status === 'enrolled' && (<><CheckCircle2 size={14} /> Mark complete</>)}
              {status === 'completed' && (<><CheckCircle2 size={14} /> Completed — undo</>)}
            </button>
            <a href={course.url} target="_blank" rel="noreferrer" className="btn btn-primary btn-sm" style={{ marginLeft: 'auto', display: 'inline-flex', alignItems: 'center', gap: 6, textDecoration: 'none' }}>
              Start course <ExternalLink size={13} />
            </a>
          </div>

          {/* Skill bridge — only after completion */}
          {status === 'completed' && (
            <div className="course-bridge">
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10 }}>
                <Sparkles size={16} style={{ color: '#0f766e' }} />
                <span style={{ fontSize: 13, fontWeight: 700, color: '#0f766e' }}>Add what you learned to your profile</span>
              </div>
              {bridgeSkills.length === 0 ? (
                <div style={{ fontSize: 12.5, color: '#0f766e' }}>
                  {(addedSkills || []).length > 0
                    ? `${addedSkills.length} skill${addedSkills.length > 1 ? 's' : ''} added to your profile ✓`
                    : 'All of this course’s skills are already on your profile.'}
                </div>
              ) : (
                <>
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginBottom: 12 }}>
                    {bridgeSkills.map(s => (
                      <span key={s} style={{ background: '#fff', border: `1px solid ${TEAL_MID}`, color: '#0f766e', borderRadius: 20, padding: '3px 10px', fontSize: 12, fontWeight: 600 }}>{prettifySkill(s)}</span>
                    ))}
                  </div>
                  <button
                    type="button"
                    className="btn btn-primary btn-sm"
                    disabled={addingSkills}
                    onClick={() => onAddSkills(course, bridgeSkills)}
                    style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}
                  >
                    {addingSkills ? 'Adding…' : <><Plus size={13} /> Add {bridgeSkills.length} skill{bridgeSkills.length > 1 ? 's' : ''} to profile</>}
                  </button>
                  {(addedSkills || []).length > 0 && (
                    <div style={{ fontSize: 11.5, color: '#0f766e', marginTop: 8 }}>{addedSkills.length} already added ✓</div>
                  )}
                </>
              )}
              {bridgeError && <div style={{ fontSize: 11.5, color: '#dc2626', marginTop: 8 }}>{bridgeError}</div>}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
