import { Send, Calendar, CheckCircle2, ChevronRight, Briefcase, Clock, FileText, Building2, MapPin, GraduationCap } from 'lucide-react'
import {
  STAGE_LABELS,
  STATUS_BADGES,
  TEAL,
  TEAL_LIGHT,
  TEAL_MID,
  rankJobsBySkillMatch,
} from '../utils'

export default function OverviewTab({ profile, apps, myInterviews, myOffers, jobs, navigate, onViewJob }) {
  const firstName = profile?.first_name || 'there'
  const score = profile?.completion?.score ?? 0
  const missing = profile?.completion?.missing || []

  const isOffer = s => /offer/i.test(s || '')
  const isInterviewing = s => /interview|shortlist/i.test(s || '') && !isOffer(s)
  const statApplied = apps.length
  const statInterviewing = apps.filter(a => isInterviewing(a.stage)).length

  // Offers come from the OFFERS endpoint, not from the application's stage string.
  //
  // This used to be `apps.filter(a => isOffer(a.stage)).length`, which is wrong in the
  // one case that matters: an offer is a row in its own table with its own lifecycle,
  // and sending one does not necessarily move the application's stage. A candidate with
  // a live, sent offer therefore saw "Offers 0" — and, worse, the "You have an offer"
  // prompt below never fired, so nothing on the dashboard told them an offer existed.
  // `myOffers` was already being fetched and handed to InterviewsTab; it just was not
  // used here.
  const offers = myOffers || []
  const respondedTo = o => ['accepted', 'rejected', 'declined'].includes(String(o.status || '').toLowerCase())
  // The card counts offers still standing — a declined offer is not one you "have".
  const statOffers = offers.filter(o => String(o.status || '').toLowerCase() !== 'rejected'
                                     && String(o.status || '').toLowerCase() !== 'declined').length
  // The prompt fires only for an offer still awaiting a decision.
  const pendingOffers = offers.filter(o => !respondedTo(o) && !o.responded_at)

  const now = Date.now()
  const upcoming = [...(myInterviews || [])]
    .filter(i => i.scheduled_datetime && new Date(i.scheduled_datetime).getTime() >= now)
    .sort((a, b) => new Date(a.scheduled_datetime) - new Date(b.scheduled_datetime))[0]

  const recent = [...apps]
    .sort((a, b) => new Date(b.applied) - new Date(a.applied))
    .slice(0, 3)

  const ranked = rankJobsBySkillMatch(jobs, profile?.skills)
  const hasSkills = (profile?.skills || []).length > 0
  const recs = ranked.length > 0
    ? ranked
    : [...jobs].sort((a, b) => new Date(b.posted) - new Date(a.posted)).slice(0, 6)

  const card = {
    background: '#fff', border: '1px solid var(--border-subtle)',
    borderRadius: 14, padding: '18px 20px',
  }
  const headerRow = {
    display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12,
  }
  const sectionTitle = { fontWeight: 700, fontSize: 14, color: 'var(--text-primary)' }
  const linkBtn = {
    background: 'none', border: 'none', color: TEAL, fontWeight: 600,
    fontSize: 12.5, cursor: 'pointer', display: 'inline-flex', alignItems: 'center', gap: 4,
  }

  const fmtDateTime = (iso) => {
    if (!iso) return ''
    const d = new Date(iso)
    return d.toLocaleString(undefined, { weekday: 'short', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })
  }

  // ── Next best action: pick the single highest-value step from loaded data ──
  const lowAtsApp = [...apps].filter(a => a.atsScore != null).sort((a, b) => a.atsScore - b.atsScore)[0]
  const topRec = recs[0]
  const nba = (() => {
    // Pending, not merely present: an offer already accepted should not keep nagging
    // the candidate to "respond before it expires".
    if (pendingOffers.length > 0) {
      return { tone: 'green', icon: CheckCircle2, title: 'You have an offer 🎉', sub: 'Review the details and respond before it expires.', cta: 'View offer', onClick: () => navigate('/candidate/interviews') }
    }
    if (upcoming) {
      return { tone: 'teal', icon: Calendar, title: `Upcoming: ${upcoming.title || upcoming.job_title || 'interview'}`, sub: `Scheduled for ${fmtDateTime(upcoming.scheduled_datetime)} — prepare now.`, cta: 'Get ready', onClick: () => navigate('/candidate/interviews') }
    }
    if (score < 60 && missing.length > 0) {
      return { tone: 'amber', icon: FileText, title: `Complete your profile — ${score}%`, sub: `${missing.length} section${missing.length > 1 ? 's' : ''} left. A complete profile ranks higher with recruiters.`, cta: 'Complete profile', onClick: () => navigate('/candidate/profile') }
    }
    if (lowAtsApp && lowAtsApp.atsScore < 60) {
      return { tone: 'teal', icon: GraduationCap, title: 'Boost your ATS score', sub: 'Some applications scored low on skills — close the gaps with a recommended course.', cta: 'Explore courses', onClick: () => navigate('/candidate/courses') }
    }
    if (statApplied === 0 && topRec) {
      return { tone: 'teal', icon: Send, title: `Apply to your top match: ${topRec.title}`, sub: topRec.matchPct != null ? `${topRec.matchPct}% skills match — a strong fit.` : 'A strong fit based on your profile.', cta: 'View & apply', onClick: () => onViewJob(topRec) }
    }
    if (missing.length > 0) {
      return { tone: 'amber', icon: FileText, title: `Finish your profile — ${score}%`, sub: `${missing.length} section${missing.length > 1 ? 's' : ''} left to round out your profile.`, cta: 'Complete profile', onClick: () => navigate('/candidate/profile') }
    }
    if (topRec) {
      return { tone: 'teal', icon: Briefcase, title: 'New matches for you', sub: 'Fresh roles that fit your skills are waiting.', cta: 'Browse jobs', onClick: () => navigate('/candidate/jobs') }
    }
    return null
  })()
  const toneBg = {
    teal: `linear-gradient(135deg, ${TEAL}, #0f9b86)`,
    green: 'linear-gradient(135deg, #10b981, #059669)',
    amber: 'linear-gradient(135deg, #f59e0b, #d97706)',
  }
  const toneText = { teal: '#0f766e', green: '#047857', amber: '#b45309' }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
      {/* Greeting + profile strength */}
      <div style={{
        ...card, display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 16,
        flexWrap: 'wrap',
      }}>
        <div style={{ flex: '1 1 200px', minWidth: 0 }}>
          <div style={{ fontSize: 20, fontWeight: 700, color: 'var(--text-primary)' }}>
            Hi {firstName} 👋
          </div>
          <div style={{ fontSize: 13, color: 'var(--text-secondary)', marginTop: 4 }}>
            Here's what's happening with your job search.
          </div>
        </div>
        <div
          onClick={() => navigate('/candidate/profile')}
          style={{
            display: 'flex', alignItems: 'center', gap: 10, padding: '8px 14px',
            background: TEAL_LIGHT, border: `1px solid ${TEAL_MID}`, borderRadius: 999,
            cursor: 'pointer', flexShrink: 0,
          }}>
          <div style={{
            width: 32, height: 32, borderRadius: '50%', position: 'relative',
            background: `conic-gradient(${TEAL} ${score * 3.6}deg, #e5e7eb 0deg)`,
            display: 'grid', placeItems: 'center',
          }}>
            <div style={{
              width: 24, height: 24, borderRadius: '50%', background: '#fff',
              display: 'grid', placeItems: 'center', fontSize: 10, fontWeight: 700, color: TEAL,
            }}>{score}%</div>
          </div>
          <div>
            <div style={{ fontSize: 11, color: 'var(--text-tertiary)', textTransform: 'uppercase', letterSpacing: 0.4, fontWeight: 600 }}>
              Profile
            </div>
            <div style={{ fontSize: 13, fontWeight: 700, color: TEAL }}>View & complete</div>
          </div>
        </div>
      </div>

      {/* Next best action */}
      {nba && (
        <div
          onClick={nba.onClick}
          style={{
            cursor: 'pointer', borderRadius: 14, padding: '16px 20px',
            display: 'flex', alignItems: 'center', gap: 16, flexWrap: 'wrap',
            background: toneBg[nba.tone], boxShadow: '0 6px 20px rgba(15,118,110,0.18)',
          }}
        >
          <div style={{
            width: 44, height: 44, borderRadius: 12, flexShrink: 0,
            background: 'rgba(255,255,255,0.2)', display: 'grid', placeItems: 'center', color: '#fff',
          }}>
            <nba.icon size={22} strokeWidth={1.9} />
          </div>
          <div style={{ flex: '1 1 180px', minWidth: 0 }}>
            <div style={{ fontSize: 10.5, textTransform: 'uppercase', letterSpacing: 0.6, fontWeight: 700, color: 'rgba(255,255,255,0.82)', marginBottom: 3 }}>
              Next best step
            </div>
            <div style={{ fontSize: 15.5, fontWeight: 700, color: '#fff', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
              {nba.title}
            </div>
            <div style={{ fontSize: 12.5, color: 'rgba(255,255,255,0.88)', marginTop: 2 }}>{nba.sub}</div>
          </div>
          <button
            onClick={e => { e.stopPropagation(); nba.onClick() }}
            style={{
              flexShrink: 0, background: '#fff', color: toneText[nba.tone], border: 'none',
              borderRadius: 9, padding: '9px 16px', fontSize: 13, fontWeight: 700, cursor: 'pointer',
              fontFamily: 'var(--font-body)', display: 'inline-flex', alignItems: 'center', gap: 6,
            }}
          >
            {nba.cta} <ChevronRight size={15} />
          </button>
        </div>
      )}

      {/* Quick stat tiles */}
      {/* auto-fit: three across on desktop, two per row on a phone. */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(150px, 100%), 1fr))', gap: 14 }}>
        {[
          { label: 'Applications',  value: statApplied,      icon: Send,     route: '/candidate/applications' },
          { label: 'Interviewing',  value: statInterviewing, icon: Calendar, route: '/candidate/interviews' },
          { label: 'Offers',        value: statOffers,       icon: CheckCircle2, route: '/candidate/status' },
        ].map(({ label, value, icon: Icon, route }) => (
          <div key={label}
            onClick={() => navigate(route)}
            style={{
              ...card, cursor: 'pointer',
              display: 'flex', alignItems: 'center', justifyContent: 'space-between',
              transition: 'border-color 0.15s ease, background 0.15s ease',
            }}
            onMouseEnter={e => { e.currentTarget.style.borderColor = TEAL_MID; e.currentTarget.style.background = TEAL_LIGHT }}
            onMouseLeave={e => { e.currentTarget.style.borderColor = 'var(--border-subtle)'; e.currentTarget.style.background = '#fff' }}
          >
            <div>
              <div style={{ fontSize: 11, color: 'var(--text-tertiary)', textTransform: 'uppercase', letterSpacing: 0.4, fontWeight: 600 }}>{label}</div>
              <div style={{ fontSize: 26, fontWeight: 700, color: 'var(--text-primary)', marginTop: 2 }}>{value}</div>
            </div>
            <div style={{
              width: 40, height: 40, borderRadius: 10, background: TEAL_LIGHT,
              display: 'grid', placeItems: 'center', color: TEAL,
            }}>
              <Icon size={18} strokeWidth={1.8} />
            </div>
          </div>
        ))}
      </div>

      {/* Two-column: upcoming interview + recent applications */}
      <div className="stack-mobile" style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14 }}>
        <div style={card}>
          <div style={headerRow}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <Calendar size={16} strokeWidth={1.8} style={{ color: TEAL }} />
              <span style={sectionTitle}>Upcoming Interview</span>
            </div>
            <button style={linkBtn} onClick={() => navigate('/candidate/interviews')}>
              View all <ChevronRight size={14} />
            </button>
          </div>
          {upcoming ? (
            <div>
              <div style={{ fontSize: 15, fontWeight: 700, color: 'var(--text-primary)' }}>
                {upcoming.title || upcoming.job_title || 'Interview'}
              </div>
              {upcoming.job_title && upcoming.title && (
                <div style={{ fontSize: 13, color: 'var(--text-secondary)', marginTop: 2 }}>
                  {upcoming.job_title}
                </div>
              )}
              <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginTop: 10, fontSize: 12.5, color: 'var(--text-secondary)' }}>
                <span style={{
                  background: TEAL_LIGHT, color: TEAL, padding: '3px 10px',
                  borderRadius: 20, fontWeight: 600, fontSize: 11.5,
                }}>
                  {upcoming.interview_type || 'Interview'}
                </span>
                <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                  <Clock size={12} /> {fmtDateTime(upcoming.scheduled_datetime)}
                </span>
                {upcoming.duration_minutes && (
                  <span>· {upcoming.duration_minutes} min</span>
                )}
              </div>
            </div>
          ) : (
            <div style={{ fontSize: 13, color: 'var(--text-tertiary)', padding: '12px 0' }}>
              No upcoming interviews. Keep applying!
            </div>
          )}
        </div>

        <div style={card}>
          <div style={headerRow}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <FileText size={16} strokeWidth={1.8} style={{ color: TEAL }} />
              <span style={sectionTitle}>Recent Applications</span>
            </div>
            <button style={linkBtn} onClick={() => navigate('/candidate/applications')}>
              See all <ChevronRight size={14} />
            </button>
          </div>
          {recent.length === 0 ? (
            <div style={{ fontSize: 13, color: 'var(--text-tertiary)', padding: '12px 0' }}>
              You haven't applied to any jobs yet.
            </div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              {recent.map(a => {
                const label = STAGE_LABELS[a.stage] || STAGE_LABELS[(a.stage || '').toLowerCase()] || a.stage || '—'
                return (
                  <div key={a.id} style={{
                    display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                    padding: '10px 12px', background: 'var(--bg-primary)',
                    border: '1px solid var(--border-subtle)', borderRadius: 10,
                  }}>
                    <div style={{ minWidth: 0, flex: 1 }}>
                      <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-primary)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                        {a.job}
                      </div>
                      <div style={{ fontSize: 11.5, color: 'var(--text-tertiary)', marginTop: 2 }}>{a.applied}</div>
                    </div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexShrink: 0 }}>
                      {a.atsScore != null && (
                        <span style={{
                          background: TEAL_LIGHT, color: TEAL, border: `1px solid ${TEAL_MID}`,
                          padding: '2px 8px', borderRadius: 20, fontSize: 11, fontWeight: 700,
                        }}>ATS {a.atsScore}</span>
                      )}
                      <span className={`badge ${STATUS_BADGES[a.stage] || 'badge-blue'}`} style={{ fontSize: 11 }}>
                        {label}
                      </span>
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </div>
      </div>

      {/* Profile nudges */}
      {missing.length > 0 && (
        <div style={{
          ...card,
          background: '#fff7ed', borderColor: '#fed7aa',
        }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <span style={{ fontWeight: 700, fontSize: 14, color: '#9a3412' }}>Finish your profile</span>
              <span style={{ fontSize: 12, color: '#b45309' }}>
                {missing.length} section{missing.length > 1 ? 's' : ''} left
              </span>
            </div>
            <button
              onClick={() => navigate('/candidate/profile')}
              style={{
                background: '#d97706', color: '#fff', border: 'none',
                borderRadius: 8, padding: '7px 14px', fontSize: 12.5, fontWeight: 600,
                cursor: 'pointer', fontFamily: 'var(--font-body)',
              }}>
              Complete profile
            </button>
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))', gap: 6 }}>
            {missing.slice(0, 4).map((m, i) => (
              <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12.5, color: '#7c2d12' }}>
                <span style={{ width: 14, height: 14, borderRadius: '50%', border: '1.5px solid #d97706', flexShrink: 0 }} />
                {m}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Recommended for you */}
      <div style={card}>
        <div style={headerRow}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <Briefcase size={16} strokeWidth={1.8} style={{ color: TEAL }} />
            <span style={sectionTitle}>
              {ranked.length > 0 ? 'Recommended for you' : 'Recently posted jobs'}
            </span>
            {ranked.length > 0 && (
              <span style={{ fontSize: 11.5, color: 'var(--text-tertiary)' }}>
                matched on your skills
              </span>
            )}
          </div>
          <button style={linkBtn} onClick={() => navigate('/candidate/jobs')}>
            Browse all <ChevronRight size={14} />
          </button>
        </div>

        {!hasSkills && (
          <div style={{
            padding: '10px 12px', marginBottom: 12, borderRadius: 10,
            background: TEAL_LIGHT, border: `1px solid ${TEAL_MID}`,
            fontSize: 12.5, color: 'var(--text-secondary)',
            display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10,
          }}>
            <span>Add skills to your profile for personalized job matches.</span>
            <button onClick={() => navigate('/candidate/profile')} style={{
              background: TEAL, color: '#fff', border: 'none', borderRadius: 6,
              padding: '4px 10px', fontSize: 12, fontWeight: 600, cursor: 'pointer',
              fontFamily: 'var(--font-body)', flexShrink: 0,
            }}>Add skills</button>
          </div>
        )}

        {recs.length === 0 ? (
          <div style={{ fontSize: 13, color: 'var(--text-tertiary)', padding: '12px 0' }}>
            No jobs available right now. Check back later.
          </div>
        ) : (
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(240px, 1fr))', gap: 12 }}>
            {recs.map(job => (
              <div key={job.id}
                onClick={() => onViewJob(job)}
                style={{
                  border: '1px solid var(--border-subtle)', borderRadius: 12,
                  padding: 14, cursor: 'pointer', background: '#fff',
                  transition: 'all 0.15s ease',
                }}
                onMouseEnter={e => { e.currentTarget.style.borderColor = TEAL_MID; e.currentTarget.style.background = TEAL_LIGHT }}
                onMouseLeave={e => { e.currentTarget.style.borderColor = 'var(--border-subtle)'; e.currentTarget.style.background = '#fff' }}
              >
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 6 }}>
                  <span className="badge badge-amber" style={{ fontSize: 10.5 }}>{job.type || 'Full-time'}</span>
                  {job.matchPct != null && (
                    <span style={{
                      background: TEAL, color: '#fff', borderRadius: 20,
                      padding: '2px 8px', fontSize: 11, fontWeight: 700,
                    }}>{job.matchPct}% match</span>
                  )}
                </div>
                <div style={{ fontSize: 14, fontWeight: 700, color: 'var(--text-primary)', lineHeight: 1.25, marginBottom: 4 }}>
                  {job.title}
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 11.5, color: 'var(--text-secondary)' }}>
                  <span style={{ display: 'inline-flex', alignItems: 'center', gap: 3 }}>
                    <Building2 size={11} /> {job.department}
                  </span>
                  <span style={{ display: 'inline-flex', alignItems: 'center', gap: 3 }}>
                    <MapPin size={11} /> {job.location}
                  </span>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
