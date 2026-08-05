export const STATUS_BADGES = {
  'applied': 'badge-blue',
  'ats_screening': 'badge-purple', 'ATS Screening': 'badge-purple',
  'hr_review': 'badge-blue',       'HR Review': 'badge-blue',
  'shortlisted': 'badge-amber',
  'test_sent': 'badge-amber',      'test_in_progress': 'badge-amber',
  'test_submitted': 'badge-blue',  'test_completed': 'badge-blue',
  'interview': 'badge-amber',      'Technical Interview': 'badge-amber', 'technical_interview': 'badge-amber',
  'interview_completed': 'badge-blue',
  'hired': 'badge-green',          'Offer Extended': 'badge-green', 'offer_extended': 'badge-green',
  'offer_sent': 'badge-green',
  'rejected': 'badge-red',         'offer_rejected': 'badge-red',
  'registered': 'badge-neutral',
}

export const STAGE_LABELS = {
  'applied': 'Applied',
  'ats_screening': 'ATS Screening',
  'hr_review': 'HR Review',
  'shortlisted': 'Shortlisted',
  'test_sent': 'Test Sent',
  'test_in_progress': 'Test In Progress',
  'test_submitted': 'Test Submitted',
  'test_completed': 'Test Completed',
  'interview': 'Interview',
  'interview_completed': 'Interview Completed',
  'hired': 'Hired',
  'offer_sent': 'Offer Sent',
  'offer_extended': 'Offer Extended',
  'rejected': 'Not Selected',
  'offer_rejected': 'Offer Declined',
}

export const TAB_MAP = {
  overview: 'overview',
  jobs: 'jobs',
  applications: 'applications',
  interviews: 'interviews',
  status: 'tracker',
  courses: 'courses',
  profile: 'profile',
}

export const TEAL = '#4ecdb5'
export const TEAL_LIGHT = '#e9f9f4'
export const TEAL_MID = '#c3ede6'

export function parseApps(data) {
  return (data || []).map(a => ({
    id: a.id,
    job: a.job_title,
    status: a.stage || a.status,
    stage: a.stage || a.status,
    applied: new Date(a.applied_at).toLocaleDateString(),
    atsScore: a.ats_score ? Math.round(a.ats_score) : null,
  }))
}

// Rank jobs by skill intersection with candidate's profile skills.
export function rankJobsBySkillMatch(jobs, profileSkills) {
  const mine = new Set((profileSkills || []).map(s => (s?.name || s || '').toString().toLowerCase()).filter(Boolean))
  if (mine.size === 0) return []
  return jobs
    .map(j => {
      const req = (j.skills || []).map(s => s.toLowerCase())
      const hit = req.filter(s => mine.has(s)).length
      const pct = req.length ? Math.round((hit / req.length) * 100) : 0
      return { ...j, matchPct: pct, matchHits: hit }
    })
    .filter(j => j.matchPct > 0)
    .sort((a, b) => b.matchPct - a.matchPct || b.matchHits - a.matchHits)
    .slice(0, 6)
}

// Estimates how many skill tags fit in the card's skills row without clipping.
// Monospace char-width approximation (0.7rem ≈ 11.2px → ~6.7px per char).
export function computeVisibleSkills(skills, availableWidth = 228) {
  const CHAR_W = 6.7    // px per char at 0.7rem monospace
  const TAG_PAD = 22    // 10px left + 10px right padding + 2px border
  const GAP = 6         // gap between tags
  const BADGE_W = 40    // reserved width for "+99" badge + its gap

  let used = 0
  let count = 0

  for (let i = 0; i < skills.length; i++) {
    const tagW = Math.round(skills[i].length * CHAR_W) + TAG_PAD
    const spacer = count > 0 ? GAP : 0
    const moreAhead = i < skills.length - 1
    const reserve = moreAhead ? GAP + BADGE_W : 0

    if (used + spacer + tagW + reserve > availableWidth) break

    used += spacer + tagW
    count++
  }

  return Math.max(count, skills.length > 0 ? 1 : 0)
}
